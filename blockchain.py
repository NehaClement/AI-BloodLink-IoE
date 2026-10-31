"""Technology 7 - Blockchain: a hash-chained, tamper-evident ledger.

Every block stores the SHA-256 hash of the previous block. If anybody edits an old
record, its hash no longer matches and verify() reports exactly where the chain broke.
"""
import hashlib
import json
from datetime import datetime

GENESIS_HASH = "0" * 64
TAMPER_MARK = " /*edited*/"


def _hash(idx, ts, event, data, prev):
    return hashlib.sha256(f"{idx}|{ts}|{event}|{data}|{prev}".encode()).hexdigest()


def add_block(conn, event, data):
    last = conn.execute("SELECT idx, hash FROM ledger ORDER BY idx DESC LIMIT 1").fetchone()
    idx = 0 if last is None else last["idx"] + 1
    prev = GENESIS_HASH if last is None else last["hash"]
    ts = datetime.now().isoformat(timespec="seconds")
    payload = json.dumps(data, sort_keys=True)
    block_hash = _hash(idx, ts, event, payload, prev)
    conn.execute(
        "INSERT INTO ledger (idx, ts, event, data, prev_hash, hash) VALUES (?,?,?,?,?,?)",
        (idx, ts, event, payload, prev, block_hash),
    )
    return idx


def verify(conn):
    prev = GENESIS_HASH
    rows = conn.execute("SELECT * FROM ledger ORDER BY idx").fetchall()
    for r in rows:
        expected = _hash(r["idx"], r["ts"], r["event"], r["data"], r["prev_hash"])
        if r["prev_hash"] != prev or expected != r["hash"]:
            return {"valid": False, "broken_at": r["idx"], "length": len(rows)}
        prev = r["hash"]
    return {"valid": True, "broken_at": None, "length": len(rows)}


def tamper(conn, idx):
    """Demo helper: silently edit a block's data WITHOUT recomputing its hash."""
    conn.execute("UPDATE ledger SET data = data || ? WHERE idx = ?", (TAMPER_MARK, idx))


def restore(conn):
    conn.execute("UPDATE ledger SET data = REPLACE(data, ?, '')", (TAMPER_MARK,))
