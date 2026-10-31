"""Technologies 1, 3 and 4 - IoT sensors, 5G / LoRaWAN connectivity and edge computing.

Each blood bank has a temperature/humidity sensor. The "edge node" at the bank checks every
reading locally (safe range 2-6 C) so an alarm fires even when the network is down. Readings
made while offline are buffered at the edge and synchronised to the cloud when the link returns.
The hardware is simulated here; the logic (edge decision, buffering, sync) is the real design.
"""
import random
from datetime import datetime

from blockchain import add_block

SAFE_MIN, SAFE_MAX = 2.0, 6.0
STATE = {"online": True, "buffer": [], "fault": {}, "temp": {}, "alarm": {}, "synced": 0}


def _link_profile(link):
    if link == "5G":
        return random.gauss(14, 3), random.gauss(-72, 4)      # latency ms, RSSI dBm
    return random.gauss(380, 60), random.gauss(-104, 4)        # LoRaWAN: slow but long range


def _store(conn, rec):
    conn.execute(
        "INSERT INTO sensors (bank_id, ts, temp, humidity, link, latency, rssi, status) VALUES (?,?,?,?,?,?,?,?)",
        (rec["bank_id"], rec["ts"], rec["temp"], rec["humidity"], rec["link"], rec["latency"], rec["rssi"], rec["status"]),
    )
    if rec["alarm_start"]:
        add_block(conn, "COLD_CHAIN_ALARM", {"bank": rec["bank_id"], "temp_c": rec["temp"], "time": rec["ts"]})


def _flush(conn):
    if STATE["buffer"]:
        for rec in STATE["buffer"]:
            _store(conn, rec)
        STATE["synced"] += len(STATE["buffer"])
        STATE["buffer"].clear()


def tick(conn):
    banks = conn.execute("SELECT * FROM banks ORDER BY id").fetchall()
    for b in banks:
        bid = b["id"]
        temp = STATE["temp"].get(bid, 4.0 + random.uniform(-0.4, 0.4))
        remaining = STATE["fault"].get(bid, 0)
        target = 8.6 if remaining > 0 else 4.0
        if remaining > 0:
            STATE["fault"][bid] = remaining - 1
        temp = temp + (target - temp) * 0.4 + random.gauss(0, 0.1)
        STATE["temp"][bid] = temp
        latency, rssi = _link_profile(b["link"])
        status = "ALARM" if (temp < SAFE_MIN or temp > SAFE_MAX) else "OK"   # decided at the EDGE
        alarm_start = status == "ALARM" and not STATE["alarm"].get(bid, False)
        STATE["alarm"][bid] = status == "ALARM"
        rec = {
            "bank_id": bid, "ts": datetime.now().isoformat(timespec="seconds"),
            "temp": round(temp, 2), "humidity": round(random.gauss(46, 2), 1), "link": b["link"],
            "latency": int(max(latency, 3)), "rssi": int(rssi), "status": status, "alarm_start": alarm_start,
        }
        if STATE["online"]:
            _flush(conn)
            _store(conn, rec)
        else:
            STATE["buffer"].append(rec)
            del STATE["buffer"][:-500]
    conn.execute("DELETE FROM sensors WHERE id < (SELECT MAX(id) - 2000 FROM sensors)")
    conn.commit()


def live(conn):
    tick(conn)
    banks = conn.execute("SELECT * FROM banks ORDER BY id").fetchall()
    out, history = [], {}
    for b in banks:
        rows = conn.execute(
            "SELECT * FROM sensors WHERE bank_id=? ORDER BY id DESC LIMIT 30", (b["id"],)).fetchall()
        series = [r["temp"] for r in reversed(rows)] + [r["temp"] for r in STATE["buffer"] if r["bank_id"] == b["id"]]
        series = series[-30:]
        history[b["id"]] = series
        latest = series[-1] if series else None
        buffered = [r for r in STATE["buffer"] if r["bank_id"] == b["id"]]
        last_row = buffered[-1] if buffered else (dict(rows[0]) if rows else None)
        if last_row:
            out.append({
                "bank_id": b["id"], "name": b["name"], "city": b["city"], "link": b["link"],
                "temp": latest, "humidity": last_row["humidity"], "latency": last_row["latency"],
                "rssi": last_row["rssi"], "status": last_row["status"],
            })
    return {
        "network": {"online": STATE["online"], "buffered": len(STATE["buffer"]), "synced": STATE["synced"]},
        "safe_range": [SAFE_MIN, SAFE_MAX], "banks": out, "history": history,
    }


def set_network(online):
    STATE["online"] = bool(online)


def inject_fault(bank_id, ticks=8):
    STATE["fault"][int(bank_id)] = ticks
