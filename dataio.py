"""Import real demand data (CSV) and retrain the model + backtest on it."""
import csv
import io
from datetime import date, timedelta

import backtest
import ml
from db import GROUPS

MIN_DAYS = 90
MAX_BYTES = 5 * 1024 * 1024


def source(conn):
    row = conn.execute("SELECT value FROM meta WHERE key='data_source'").fetchone()
    n = conn.execute("SELECT COUNT(*) c, MIN(day) a, MAX(day) b FROM demand").fetchone()
    return {"source": row["value"] if row else "synthetic", "rows": n["c"], "from": n["a"], "to": n["b"]}


def template_csv(conn):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["bank_id", "blood_group", "day", "units"])
    for r in conn.execute("SELECT bank_id, blood_group, day, units FROM demand ORDER BY bank_id, blood_group, day"):
        w.writerow([r["bank_id"], r["blood_group"], r["day"], r["units"]])
    return out.getvalue()


def import_csv(conn, raw, filename="upload.csv"):
    if len(raw) > MAX_BYTES:
        raise ValueError("File is larger than 5 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("File must be UTF-8 text (save as CSV UTF-8)")
    reader = csv.DictReader(io.StringIO(text))
    cols = {(c or "").strip().lower(): c for c in (reader.fieldnames or [])}
    for need in ("bank_id", "blood_group", "day", "units"):
        if need not in cols:
            raise ValueError(f"Missing column '{need}'. Required columns: bank_id, blood_group, day, units")
    bank_ids = {r["id"] for r in conn.execute("SELECT id FROM banks")}
    data, bad = {}, 0
    for row in reader:
        try:
            b = int(str(row[cols["bank_id"]]).strip())
            g = str(row[cols["blood_group"]]).strip().upper().replace("POS", "+").replace("NEG", "-")
            d = date.fromisoformat(str(row[cols["day"]]).strip())
            u = int(float(str(row[cols["units"]]).strip()))
        except (ValueError, TypeError, KeyError):
            bad += 1
            continue
        if b not in bank_ids or g not in GROUPS or u < 0:
            bad += 1
            continue
        data.setdefault((b, g), {})[d] = data.get((b, g), {}).get(d, 0) + u
    if bad > max(5, 0.02 * (sum(len(v) for v in data.values()) + bad)):
        raise ValueError(f"{bad} rows are invalid (bank_id must be 1-{max(bank_ids)}, blood_group one of {', '.join(GROUPS)}, day YYYY-MM-DD, units >= 0)")
    missing = [f"bank {b} / {g}" for b in sorted(bank_ids) for g in GROUPS if (b, g) not in data]
    if missing:
        raise ValueError(f"{len(missing)} bank/blood-group series have no rows, e.g. {', '.join(missing[:4])}. "
                         "Every one of the 40 series (5 banks x 8 groups) needs data. Use the template for the exact format.")
    first = min(min(v) for v in data.values())
    last = max(max(v) for v in data.values())
    span = (last - first).days + 1
    if span < MIN_DAYS:
        raise ValueError(f"Only {span} days of history. At least {MIN_DAYS} days are needed to train and backtest.")
    rows = []
    for (b, g), v in data.items():
        for i in range(span):
            d = first + timedelta(days=i)
            rows.append((b, g, d.isoformat(), v.get(d, 0)))        # days with no record count as zero demand
    conn.execute("DELETE FROM demand")
    conn.executemany("INSERT INTO demand VALUES (?,?,?,?)", rows)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('data_source', ?)", (f"imported: {filename[:60]}",))
    conn.commit()
    ml.train(conn, GROUPS)
    backtest.run(conn, GROUPS)
    return {"rows": len(rows), "days": span, "from": first.isoformat(), "to": last.isoformat(), "skipped_rows": bad,
            "best_model": ml.MODEL["metrics"]["best"], "improvement_pct": ml.MODEL["metrics"]["improvement_pct"]}


def reset_synthetic(conn):
    conn.execute("DELETE FROM demand")
    conn.execute("DELETE FROM meta WHERE key='data_source'")
    conn.commit()
    import db
    db.seed_demand(conn)
    conn.commit()
    ml.train(conn, GROUPS)
    backtest.run(conn, GROUPS)
