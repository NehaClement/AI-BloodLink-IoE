"""AI-BloodLink - Internet of Everything blood bank network (Flask API + dashboard)."""
import hashlib
import os
from datetime import date, timedelta

from flask import Flask, Response, g, jsonify, request, send_from_directory

import analytics
import blockchain
import backtest
import chatbot
import dataio
import db
import ml
import sensors

app = Flask(__name__, static_folder="static", static_url_path="")


def conn():
    if "conn" not in g:
        g.conn = db.connect()
    return g.conn


@app.teardown_appcontext
def close_conn(_):
    c = g.pop("conn", None)
    if c is not None:
        c.close()


@app.after_request
def security_headers(resp):          # cross-cutting security layer
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    else:                                            # always revalidate pages and scripts so updates show up
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def err(message, code=400):
    return jsonify({"error": message}), code


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------------------------------------------------------------- overview
@app.get("/api/summary")
def summary():
    return jsonify(analytics.summary(conn()))


@app.get("/api/inventory")
def inventory():
    banks, cells = analytics.stock_matrix(conn())
    return jsonify({"banks": [dict(b) for b in banks], "groups": db.GROUPS, "cells": cells})


@app.get("/api/alerts")
def alerts():
    return jsonify(analytics.alerts(conn()))


@app.get("/api/bags")
def bags():
    q, args = "SELECT b.*, k.name bank FROM bags b JOIN banks k ON k.id=b.bank_id WHERE 1=1", []
    if request.args.get("bank_id", type=int):
        q += " AND b.bank_id=?"; args.append(request.args.get("bank_id", type=int))
    if request.args.get("group") in db.GROUPS:
        q += " AND b.blood_group=?"; args.append(request.args.get("group"))
    if request.args.get("status"):
        q += " AND b.status=?"; args.append(request.args.get("status"))
    q += " ORDER BY b.expiry_date LIMIT ?"
    args.append(min(request.args.get("limit", 30, type=int), 200))
    return jsonify([dict(r) for r in conn().execute(q, args)])


# ---------------------------------------------------------------- machine learning
@app.get("/api/model")
def model():
    return jsonify({**ml.MODEL["metrics"], "importance": ml.MODEL["importance"]})


@app.get("/api/forecast")
def forecast():
    bank = request.args.get("bank_id", 0, type=int)
    group = request.args.get("group")
    if group not in db.GROUPS:
        return err("Unknown blood group")
    _, cells = analytics.stock_matrix(conn())
    sel = [c for c in cells if c["group"] == group and bank in (0, c["bank_id"])]
    stock, daily = sum(c["units"] + c["incoming"] for c in sel), sum(c["daily"] for c in sel)
    cover = round(stock / daily, 1) if daily > 0.05 else 99
    status = sel[0]["status"] if len(sel) == 1 else ("critical" if cover < 3 else "low" if cover < 5 else "ok")
    return jsonify({"history": ml.history(bank, group), "forecast": ml.forecast(bank, group),
                    "stock": stock, "daily": round(daily, 2), "cover": cover, "status": status})


@app.get("/api/forecast/network")
def forecast_network():
    _, cells = analytics.stock_matrix(conn())
    groups = [{"group": g, "stock": sum(c["units"] for c in cells if c["group"] == g),
               "incoming": sum(c["incoming"] for c in cells if c["group"] == g),
               "forecast7": round(sum(c["forecast7"] for c in cells if c["group"] == g), 1)} for g in db.GROUPS]
    return jsonify({"groups": groups, "history": ml.history(0, None), "forecast": ml.forecast(0, None)})


# ---------------------------------------------------------------- impact backtest, data import, compatibility
@app.get("/api/backtest")
def get_backtest():
    return jsonify({**backtest.CACHE, "data": dataio.source(conn())})


@app.get("/api/data/source")
def data_source():
    return jsonify(dataio.source(conn()))


@app.get("/api/data/template")
def data_template():
    return Response(dataio.template_csv(conn()), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=bloodlink_demand_template.csv"})


@app.post("/api/data/import")
def data_import():
    f = request.files.get("file")
    if not f:
        return err("Choose a CSV file first")
    try:
        res = dataio.import_csv(conn(), f.read(), f.filename or "upload.csv")
    except ValueError as e:
        return err(str(e))
    return jsonify(res)


@app.post("/api/data/reset")
def data_reset():
    dataio.reset_synthetic(conn())
    return jsonify(dataio.source(conn()))


@app.get("/api/compatibility")
def compatibility():
    return jsonify(analytics.compat_matrix())


@app.get("/api/substitutions")
def substitutions():
    return jsonify(analytics.substitutions(conn()))


# ---------------------------------------------------------------- redistribution & tracking
@app.get("/api/redistribution")
def redistribution():
    return jsonify(analytics.proposals(conn()))


@app.post("/api/redistribution/approve")
def approve():
    d = request.get_json(silent=True) or {}
    try:
        res = analytics.approve(conn(), int(d.get("from_id", 0)), int(d.get("to_id", 0)),
                                str(d.get("group", "")), int(d.get("units", 0)))
    except (ValueError, TypeError) as e:
        return err(str(e))
    return jsonify(res)


@app.get("/api/transfers")
def transfers():
    return jsonify({"transfers": analytics.transfers(conn())})


# ---------------------------------------------------------------- blockchain & RFID
@app.get("/api/ledger")
def ledger():
    rows = conn().execute("SELECT * FROM ledger ORDER BY idx DESC LIMIT ?",
                          (min(request.args.get("limit", 15, type=int), 100),)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/ledger/verify")
def ledger_verify():
    return jsonify(blockchain.verify(conn()))


@app.post("/api/ledger/tamper")
def ledger_tamper():
    c = conn()
    last = c.execute("SELECT MAX(idx) m FROM ledger").fetchone()["m"]
    idx = (request.get_json(silent=True) or {}).get("index", max(1, last // 2))
    blockchain.tamper(c, int(idx))
    c.commit()
    return jsonify({"tampered": int(idx), **blockchain.verify(c)})


@app.post("/api/ledger/restore")
def ledger_restore():
    c = conn()
    blockchain.restore(c)
    c.commit()
    return jsonify(blockchain.verify(c))


@app.get("/api/rfid/<tag>")
def rfid(tag):
    tag = tag.strip().upper()
    c = conn()
    bag = c.execute("SELECT b.*, k.name bank FROM bags b JOIN banks k ON k.id=b.bank_id WHERE tag=?", (tag,)).fetchone()
    if not bag:
        return err("No bag with that RFID tag", 404)
    trail = c.execute("SELECT * FROM ledger WHERE data LIKE ? ORDER BY idx", (f'%"{tag}"%',)).fetchall()
    return jsonify({"bag": dict(bag), "trail": [dict(r) for r in trail], "chain": blockchain.verify(c)})


@app.post("/api/bags/<tag>/issue")
def issue(tag):
    c = conn()
    bag = c.execute("SELECT * FROM bags WHERE tag=?", (tag,)).fetchone()
    if not bag or bag["status"] != "available":
        return err("Bag is not available to issue")
    ref = (request.get_json(silent=True) or {}).get("patient", "demo-patient")
    c.execute("UPDATE bags SET status='issued' WHERE tag=?", (tag,))
    blockchain.add_block(c, "ISSUED_TO_PATIENT", {"tag": tag, "bank": bag["bank_id"], "group": bag["blood_group"],
                                                  "patient_hash": hashlib.sha256(str(ref).encode()).hexdigest()[:16]})
    c.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- IoT
@app.get("/api/iot/live")
def iot_live():
    return jsonify(sensors.live(conn()))


@app.post("/api/iot/network")
def iot_network():
    sensors.set_network((request.get_json(silent=True) or {}).get("online", True))
    return jsonify({"online": sensors.STATE["online"]})


@app.post("/api/iot/fault")
def iot_fault():
    sensors.inject_fault((request.get_json(silent=True) or {}).get("bank_id", 1))
    return jsonify({"ok": True})


# ---------------------------------------------------------------- donors
@app.get("/api/donors")
def donors():
    limit = (date.today() - timedelta(days=90)).isoformat()
    rows = conn().execute("SELECT d.*, k.name bank FROM donors d JOIN banks k ON k.id=d.bank_id ORDER BY d.last_donation").fetchall()
    return jsonify([{**dict(r), "eligible": r["last_donation"] <= limit} for r in rows])


@app.post("/api/donations")
def donation():
    d = request.get_json(silent=True) or {}
    c = conn()
    donor = c.execute("SELECT * FROM donors WHERE id=?", (d.get("donor_id"),)).fetchone()
    bank = c.execute("SELECT * FROM banks WHERE id=?", (d.get("bank_id"),)).fetchone()
    if not donor or not bank:
        return err("Unknown donor or blood bank")
    if donor["last_donation"] > (date.today() - timedelta(days=90)).isoformat():
        return err("Donor is not yet eligible (90-day gap required)")
    today = date.today()
    nxt = (c.execute("SELECT COALESCE(MAX(id),0)+1 n FROM bags").fetchone()["n"])
    tag = f"RFID-{nxt:05d}"
    c.execute("INSERT INTO bags (tag, bank_id, blood_group, collected_date, expiry_date, status) VALUES (?,?,?,?,?,?)",
              (tag, bank["id"], donor["blood_group"], today.isoformat(),
               (today + timedelta(days=db.SHELF_LIFE_DAYS)).isoformat(), "available"))
    c.execute("UPDATE donors SET last_donation=? WHERE id=?", (today.isoformat(), donor["id"]))
    blockchain.add_block(c, "COLLECTED", {"tag": tag, "bank": bank["id"], "group": donor["blood_group"]})
    blockchain.add_block(c, "SCREENED", {"tag": tag, "tests": "HIV, HBV, HCV, Syphilis, Malaria", "result": "negative"})
    blockchain.add_block(c, "STORED", {"tag": tag, "bank": bank["id"], "temp_c": 4.0})
    c.commit()
    return jsonify({"tag": tag, "group": donor["blood_group"], "bank": bank["name"]})


# ---------------------------------------------------------------- chatbot
@app.post("/api/chat")
def chat():
    msg = str((request.get_json(silent=True) or {}).get("message", ""))[:300]
    if not msg.strip():
        return err("Empty message")
    text, suggestions = chatbot.reply(conn(), msg)
    return jsonify({"reply": text, "suggestions": suggestions})


def bootstrap():
    db.init_db()
    c = db.connect()
    ml.train(c, db.GROUPS)
    backtest.run(c, db.GROUPS)
    c.close()


bootstrap()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=True)
