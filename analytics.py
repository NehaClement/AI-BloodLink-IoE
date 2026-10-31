"""Technologies 8 and the Process pillar - stock status, shortage alerts, smart redistribution
and GPS-tracked transfers (drone for short hops, refrigerated van for long ones)."""
import json
import math
import time
from datetime import date, timedelta

import ml
from blockchain import add_block, verify
from db import GROUPS

SIM_SPEED = 60          # 1 real second = 60 simulated seconds, so a 2-hour van trip takes ~2 minutes
DRONE_MAX_KM = 40


def haversine(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def update_transfers(conn):
    """Deliver any transfer whose (simulated) journey has finished."""
    now = time.time()
    for r in conn.execute("SELECT * FROM transfers WHERE status='in_transit'").fetchall():
        if (now - r["created"]) * SIM_SPEED >= r["eta_min"] * 60:
            tags = json.loads(r["tags"])
            conn.executemany("UPDATE bags SET bank_id=?, status='available' WHERE tag=?",
                             [(r["to_bank"], t) for t in tags])
            conn.execute("UPDATE transfers SET status='delivered' WHERE id=?", (r["id"],))
            add_block(conn, "TRANSFER_DELIVERED", {
                "transfer": r["id"], "from": r["from_bank"], "to": r["to_bank"],
                "group": r["blood_group"], "units": r["units"], "tags": tags})
    conn.commit()


def stock_matrix(conn):
    update_transfers(conn)
    today = date.today()
    lim = (today + timedelta(days=5)).isoformat()
    banks = conn.execute("SELECT * FROM banks ORDER BY id").fetchall()
    cnt = {}
    rows = conn.execute(
        "SELECT bank_id, blood_group, COUNT(*) n, SUM(CASE WHEN expiry_date<=? THEN 1 ELSE 0 END) e "
        "FROM bags WHERE status='available' AND expiry_date>=? GROUP BY bank_id, blood_group",
        (lim, today.isoformat()))
    for r in rows:
        cnt[(r["bank_id"], r["blood_group"])] = (r["n"], r["e"] or 0)
    inc = {}
    for r in conn.execute("SELECT to_bank, blood_group, SUM(units) u FROM transfers "
                          "WHERE status='in_transit' GROUP BY to_bank, blood_group"):
        inc[(r["to_bank"], r["blood_group"])] = r["u"]
    cells = []
    for b in banks:
        for g in GROUPS:
            n, e = cnt.get((b["id"], g), (0, 0))
            i = inc.get((b["id"], g), 0)
            daily = ml.daily_forecast(b["id"], g)
            cover = min((n + i) / daily, 99.0) if daily > 0.05 else 99.0
            status = "critical" if cover < 3 else "low" if cover < 5 else "surplus" if cover > 14 else "ok"
            cells.append({"bank_id": b["id"], "bank": b["name"], "group": g, "units": n, "incoming": i,
                          "expiring": e, "daily": round(daily, 2), "forecast7": round(daily * 7, 1),
                          "cover": round(cover, 1), "status": status})
    return banks, cells


def alerts(conn):
    _, cells = stock_matrix(conn)
    return sorted([c for c in cells if c["status"] in ("critical", "low")], key=lambda c: c["cover"])


def proposals(conn):
    banks, cells = stock_matrix(conn)
    by_id = {b["id"]: b for b in banks}
    today = date.today()
    out = []
    for g in GROUPS:
        gc = [c for c in cells if c["group"] == g]
        recipients = sorted([c for c in gc if c["status"] in ("critical", "low")], key=lambda c: c["cover"])
        # a donor keeps 10 days of cover; everything above that is shareable
        donors = {c["bank_id"]: max(0, c["units"] - math.ceil(10 * c["daily"])) for c in gc if c["cover"] > 10}
        cover_of = {c["bank_id"]: c["cover"] for c in gc}
        for r in recipients:
            need = math.ceil(8 * r["daily"]) - (r["units"] + r["incoming"])
            rb = by_id[r["bank_id"]]
            order = sorted(donors, key=lambda d: haversine(by_id[d]["lat"], by_id[d]["lng"], rb["lat"], rb["lng"]))
            for did in order:
                if need <= 0:
                    break
                avail = donors[did]
                if avail <= 0:
                    continue
                db_ = by_id[did]
                near = haversine(db_["lat"], db_["lng"], rb["lat"], rb["lng"]) <= DRONE_MAX_KM
                qty = min(need, avail, 30 if near else 80)      # payload cap per trip (drone / van)
                bags = conn.execute(
                    "SELECT expiry_date FROM bags WHERE bank_id=? AND blood_group=? AND status='available' "
                    "AND expiry_date>=? ORDER BY expiry_date LIMIT ?",
                    (did, g, (today + timedelta(days=3)).isoformat(), qty)).fetchall()
                qty = len(bags)
                if qty == 0:
                    continue
                donors[did] -= qty
                need -= qty
                dist = haversine(db_["lat"], db_["lng"], rb["lat"], rb["lng"])
                if dist <= DRONE_MAX_KM:
                    mode, eta = "Drone", dist / 60 * 60 + 5
                else:
                    mode, eta = "Refrigerated van", dist * 1.3 / 45 * 60 + 10
                rescued = sum(1 for x in bags if date.fromisoformat(x["expiry_date"]) <= today + timedelta(days=10))
                out.append({
                    "from_id": did, "from": db_["name"], "to_id": r["bank_id"], "to": rb["name"], "group": g,
                    "units": qty, "distance_km": round(dist, 1), "mode": mode, "eta_min": round(eta),
                    "rescued": rescued, "urgency": r["status"],
                    "recipient_cover": r["cover"], "donor_cover": cover_of[did],
                    "reason": f"{rb['name']} has {r['cover']} days of {g} cover; "
                              f"{db_['name']} holds {cover_of[did]} days."})
    out.sort(key=lambda p: (p["urgency"] != "critical", p["recipient_cover"]))
    return out


def approve(conn, from_id, to_id, group, units):
    update_transfers(conn)
    if from_id == to_id or group not in GROUPS or units < 1:
        raise ValueError("Invalid transfer request")
    banks = {b["id"]: b for b in conn.execute("SELECT * FROM banks")}
    if from_id not in banks or to_id not in banks:
        raise ValueError("Unknown blood bank")
    today = date.today()
    bags = conn.execute(
        "SELECT tag FROM bags WHERE bank_id=? AND blood_group=? AND status='available' AND expiry_date>=? "
        "ORDER BY expiry_date LIMIT ?",     # FEFO: first-expiring, first-out
        (from_id, group, (today + timedelta(days=3)).isoformat(), units)).fetchall()
    if not bags:
        raise ValueError("No suitable units available at the source bank")
    tags = [b["tag"] for b in bags]
    conn.executemany("UPDATE bags SET status='in_transit' WHERE tag=?", [(t,) for t in tags])
    a, b = banks[from_id], banks[to_id]
    dist = haversine(a["lat"], a["lng"], b["lat"], b["lng"])
    if dist <= DRONE_MAX_KM:
        mode, eta = "Drone", dist / 60 * 60 + 5
    else:
        mode, eta = "Refrigerated van", dist * 1.3 / 45 * 60 + 10
    cur = conn.execute(
        "INSERT INTO transfers (from_bank, to_bank, blood_group, units, mode, distance, eta_min, created, status, tags) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (from_id, to_id, group, len(tags), mode, round(dist, 1), round(eta), time.time(), "in_transit", json.dumps(tags)))
    add_block(conn, "TRANSFER_DISPATCHED", {
        "transfer": cur.lastrowid, "from": from_id, "to": to_id, "group": group, "units": len(tags),
        "mode": mode, "tags": tags})
    conn.commit()
    return {"transfer_id": cur.lastrowid, "units": len(tags), "mode": mode, "eta_min": round(eta)}


def transfers(conn):
    update_transfers(conn)
    banks = {b["id"]: b for b in conn.execute("SELECT * FROM banks")}
    now = time.time()
    out = []
    rows = conn.execute("SELECT * FROM transfers ORDER BY id DESC LIMIT 12").fetchall()
    for r in rows:
        a, b = banks[r["from_bank"]], banks[r["to_bank"]]
        prog = 1.0 if r["status"] == "delivered" else min(1.0, (now - r["created"]) * SIM_SPEED / (r["eta_min"] * 60))
        out.append({
            "id": r["id"], "group": r["blood_group"], "units": r["units"], "mode": r["mode"],
            "distance": r["distance"], "eta_min": r["eta_min"], "status": r["status"],
            "progress": round(prog, 3), "remaining_min": round(r["eta_min"] * (1 - prog)),
            "from": {"id": a["id"], "name": a["name"], "lat": a["lat"], "lng": a["lng"]},
            "to": {"id": b["id"], "name": b["name"], "lat": b["lat"], "lng": b["lng"]},
            "lat": a["lat"] + (b["lat"] - a["lat"]) * prog, "lng": a["lng"] + (b["lng"] - a["lng"]) * prog,
        })
    return out


def summary(conn):
    _, cells = stock_matrix(conn)
    today = date.today().isoformat()
    donors_ok = conn.execute(
        "SELECT COUNT(*) FROM donors WHERE last_donation <= ?", ((date.today() - timedelta(days=90)).isoformat(),)
    ).fetchone()[0]
    return {
        "units": sum(c["units"] for c in cells),
        "banks": conn.execute("SELECT COUNT(*) FROM banks").fetchone()[0],
        "critical": sum(1 for c in cells if c["status"] == "critical"),
        "low": sum(1 for c in cells if c["status"] == "low"),
        "expiring": sum(c["expiring"] for c in cells),
        "active_transfers": conn.execute("SELECT COUNT(*) FROM transfers WHERE status='in_transit'").fetchone()[0],
        "proposals": len(proposals(conn)),
        "ledger": verify(conn),
        "model": {"best": ml.MODEL["metrics"]["best"], "improvement_pct": ml.MODEL["metrics"]["improvement_pct"]},
        "eligible_donors": donors_ok,
        "date": today,
    }


# ------------------------------------------------------------------ blood-group compatibility (red cells)
# recipient group -> donor groups whose red cells it can safely receive (ABO + Rh)
COMPAT_DONORS = {
    "O-": ["O-"],
    "O+": ["O+", "O-"],
    "A-": ["A-", "O-"],
    "A+": ["A+", "A-", "O+", "O-"],
    "B-": ["B-", "O-"],
    "B+": ["B+", "B-", "O+", "O-"],
    "AB-": ["AB-", "A-", "B-", "O-"],
    "AB+": ["AB+", "AB-", "A+", "A-", "B+", "B-", "O+", "O-"],
}


def compat_matrix():
    return {"groups": GROUPS, "donors": COMPAT_DONORS}


def substitutions(conn):
    """For every low/critical bank-group, list compatible groups the SAME bank could spare.
    Advisory only: a clinician must confirm any substitution (cross-match, patient Rh status, etc.)."""
    banks, cells = stock_matrix(conn)
    idx = {(c["bank_id"], c["group"]): c for c in cells}
    out = []
    for c in cells:
        if c["status"] not in ("critical", "low"):
            continue
        need = math.ceil(8 * c["daily"]) - (c["units"] + c["incoming"])
        if need <= 0:
            continue
        options = []
        for d in COMPAT_DONORS[c["group"]]:
            if d == c["group"]:
                continue
            o = idx[(c["bank_id"], d)]
            spare = max(0, o["units"] - math.ceil(10 * o["daily"]))
            if spare > 0:
                options.append({"group": d, "spare": spare, "last_resort": d == "O-"})
        options.sort(key=lambda x: (x["last_resort"], -x["spare"]))
        covered = min(need, sum(x["spare"] for x in options))
        out.append({
            "bank_id": c["bank_id"], "bank": c["bank"], "group": c["group"], "status": c["status"],
            "need": need, "options": options, "covered": covered,
            "cover_now": c["cover"],
            "cover_with_sub": round(min((c["units"] + c["incoming"] + covered) / max(c["daily"], 0.05), 99.0), 1),
        })
    out.sort(key=lambda x: x["cover_now"])
    return out
