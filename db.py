"""Database layer + realistic seed data (SQLite)."""
import os
import random
import sqlite3
from datetime import date, timedelta

import numpy as np

from blockchain import add_block

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bloodlink.db")
GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
# Approximate Indian blood-group distribution (used to shape demand)
SHARE = {"A+": 0.21, "A-": 0.015, "B+": 0.30, "B-": 0.02, "AB+": 0.07, "AB-": 0.005, "O+": 0.36, "O-": 0.02}
SHELF_LIFE_DAYS = 35  # red blood cells

# id, name, city, lat, lng, link technology, demand scale
BANKS = [
    (1, "Chennai Central Blood Bank", "Chennai", 13.0827, 80.2707, "5G", 1.3),
    (2, "Tambaram Blood Centre", "Tambaram", 12.9249, 80.1000, "5G", 1.0),
    (3, "Ambattur North Blood Bank", "Ambattur", 13.1143, 80.1548, "5G", 0.9),
    (4, "Chengalpattu District Blood Bank", "Chengalpattu", 12.6819, 79.9888, "LoRaWAN", 0.6),
    (5, "Kancheepuram Blood Bank", "Kancheepuram", 12.8342, 79.7036, "LoRaWAN", 0.5),
]

# (bank_id, group) -> stock factor. <0.5 creates a shortage, >2 creates a surplus.
STOCK_OVERRIDES = {
    (1, "B+"): 0.20, (1, "O-"): 0.25, (1, "A+"): 0.40,
    (3, "A+"): 0.30, (3, "B+"): 0.45,
    (2, "O+"): 2.20, (2, "B+"): 2.30,
    (4, "B+"): 2.40, (4, "O+"): 2.10,
    (5, "A+"): 2.20,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS banks (
    id INTEGER PRIMARY KEY, name TEXT, city TEXT, lat REAL, lng REAL, link TEXT, scale REAL);
CREATE TABLE IF NOT EXISTS bags (
    id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT UNIQUE, bank_id INTEGER, blood_group TEXT,
    collected_date TEXT, expiry_date TEXT, status TEXT);
CREATE INDEX IF NOT EXISTS ix_bags ON bags (bank_id, blood_group, status);
CREATE TABLE IF NOT EXISTS demand (
    bank_id INTEGER, blood_group TEXT, day TEXT, units INTEGER);
CREATE TABLE IF NOT EXISTS sensors (
    id INTEGER PRIMARY KEY AUTOINCREMENT, bank_id INTEGER, ts TEXT, temp REAL, humidity REAL,
    link TEXT, latency INTEGER, rssi INTEGER, status TEXT);
CREATE TABLE IF NOT EXISTS ledger (
    idx INTEGER PRIMARY KEY, ts TEXT, event TEXT, data TEXT, prev_hash TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, from_bank INTEGER, to_bank INTEGER, blood_group TEXT,
    units INTEGER, mode TEXT, distance REAL, eta_min REAL, created REAL, status TEXT, tags TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS donors (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, blood_group TEXT, phone TEXT,
    last_donation TEXT, bank_id INTEGER);
"""

FIRST = ["Aarav", "Priya", "Karthik", "Divya", "Suresh", "Meena", "Arjun", "Lakshmi", "Vikram", "Nithya",
         "Rahul", "Sneha", "Manoj", "Kavya", "Harish", "Anitha", "Naveen", "Pooja", "Gokul", "Revathi"]
LAST = ["Kumar", "Raj", "Iyer", "Nair", "Reddy", "Krishnan", "Menon", "Pillai", "Sharma", "Devi"]


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(force=False):
    if force and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = connect()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM banks").fetchone()[0] == 0:
        seed(conn)
    conn.commit()
    conn.close()


def seed_demand(conn):
    rng = np.random.default_rng(42)
    today = date.today()
    # --- 180 days of demand history (weekly pattern + trend + random event spikes) ---
    weekday = [1.10, 1.05, 1.00, 1.00, 1.08, 0.88, 0.72]
    rows = []
    for b in BANKS:
        bid, scale = b[0], b[6]
        spikes = set(rng.choice(180, 6, replace=False).tolist())
        for g in GROUPS:
            base = 85 * scale * SHARE[g]
            for i in range(180):
                d = today - timedelta(days=180 - i)
                lam = base * weekday[d.weekday()] * (1 + 0.0006 * i) * (1.6 if i in spikes else 1.0)
                rows.append((bid, g, d.isoformat(), int(rng.poisson(lam))))
    conn.executemany("INSERT INTO demand VALUES (?,?,?,?)", rows)

    conn.execute("INSERT OR REPLACE INTO meta VALUES ('data_source', 'synthetic')")


def seed(conn):
    rng = np.random.default_rng(42)
    random.seed(42)
    today = date.today()
    conn.executemany("INSERT INTO banks VALUES (?,?,?,?,?,?,?)", BANKS)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('data_source', 'synthetic')")

    seed_demand(conn)

    # --- current stock: individual RFID-tagged bags ---
    bags, seq = [], 0
    for b in BANKS:
        bid, scale = b[0], b[6]
        for g in GROUPS:
            factor = STOCK_OVERRIDES.get((bid, g), float(rng.uniform(0.8, 1.5)))
            n = max(1, round(85 * scale * SHARE[g] * 8 * factor))
            for _ in range(n):
                seq += 1
                remaining = int(rng.integers(3, 36))
                if factor > 1.9 and rng.random() < 0.20:
                    remaining = int(rng.integers(3, 7))  # surplus stock that is close to expiry
                collected = today - timedelta(days=SHELF_LIFE_DAYS - remaining)
                expiry = today + timedelta(days=remaining)
                bags.append((f"RFID-{seq:05d}", bid, g, collected.isoformat(), expiry.isoformat(), "available"))
    conn.executemany(
        "INSERT INTO bags (tag, bank_id, blood_group, collected_date, expiry_date, status) VALUES (?,?,?,?,?,?)", bags)

    # --- ledger: genesis + lifecycle of the first 12 bags ---
    add_block(conn, "GENESIS", {"note": "AI-BloodLink ledger initialised"})
    for tag, bid, g, *_ in bags[:12]:
        add_block(conn, "COLLECTED", {"tag": tag, "bank": bid, "group": g})
        add_block(conn, "SCREENED", {"tag": tag, "tests": "HIV, HBV, HCV, Syphilis, Malaria", "result": "negative"})
        add_block(conn, "STORED", {"tag": tag, "bank": bid, "temp_c": 4.0})

    # --- donors (names + masked phone numbers only) ---
    donors = []
    probs = np.array([SHARE[g] for g in GROUPS])
    probs = probs / probs.sum()
    for _ in range(40):
        name = f"{random.choice(FIRST)} {random.choice(LAST)}"
        g = GROUPS[int(rng.choice(len(GROUPS), p=probs))]
        phone = f"98{int(rng.integers(10, 99))}XXXX{int(rng.integers(10, 99))}"
        last = today - timedelta(days=int(rng.integers(20, 300)))
        donors.append((name, g, phone, last.isoformat(), int(rng.integers(1, 6))))
    conn.executemany("INSERT INTO donors (name, blood_group, phone, last_donation, bank_id) VALUES (?,?,?,?,?)", donors)
