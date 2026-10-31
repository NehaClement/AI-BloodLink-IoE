"""Technology 10 - AI chatbot (NLP intent detection) answering from live database + ML forecast.

It is rule/intent based (keyword + regex entity extraction), so it needs no paid API and works
offline. The same endpoint can later be swapped for an LLM without changing the frontend.
"""
import re
from datetime import date, timedelta

import analytics
from blockchain import verify

GROUP_RE = re.compile(r"\b(ab|a|b|o)\s?(\+|-|positive|negative|pos\b|neg\b)", re.I)

SUGGEST = [
    "How many units of O- are available?",
    "Which blood groups are at risk of shortage?",
    "Where can I donate B+ blood?",
    "Am I eligible to donate?",
    "Which units are expiring soon?",
    "Track active transfers",
    "Is the cold chain healthy?",
    "Is the blockchain ledger valid?",
    "Which blood groups are compatible with B+?",
]


def parse_group(text):
    m = GROUP_RE.search(text)
    if not m:
        return None
    sign = "+" if m.group(2).lower() in ("+", "positive", "pos") else "-"
    return m.group(1).upper() + sign


def parse_groups(text):
    out = []
    for m in GROUP_RE.finditer(text):
        sign = "+" if m.group(2).lower() in ("+", "positive", "pos") else "-"
        g = m.group(1).upper() + sign
        if g not in out:
            out.append(g)
    return out


def has(t, *words):
    return any(w in t for w in words)


def reply(conn, text):
    t = text.lower().strip()
    group = parse_group(text)
    _, cells = analytics.stock_matrix(conn)

    tag = re.search(r"rfid-\d+", t)
    if tag:
        tag = tag.group(0).upper()
        bag = conn.execute("SELECT b.*, k.name bank FROM bags b JOIN banks k ON k.id=b.bank_id WHERE tag=?", (tag,)).fetchone()
        if not bag:
            return f"I couldn't find a blood bag with tag **{tag}**.", SUGGEST[:3]
        n = conn.execute("SELECT COUNT(*) FROM ledger WHERE data LIKE ?", (f'%"{tag}"%',)).fetchone()[0]
        return (f"**{tag}** is a **{bag['blood_group']}** unit at {bag['bank']}, status **{bag['status']}**, "
                f"expires {bag['expiry_date']}. It has {n} blockchain record(s). Open *Traceability* for the full trail."), SUGGEST[:3]

    if has(t, "compatib", "substitut", "universal", "can receive", "can give", "give to", "donate to", "receive", "match"):
        from analytics import COMPAT_DONORS
        pair = parse_groups(text)
        if len(pair) == 2:
            a, b = pair                       # a = donor, b = recipient
            if has(t, "receive") and not has(t, "give to", "donate to"):
                a, b = b, a                    # 'can X receive from Y' names the recipient first
            if a in COMPAT_DONORS[b]:
                return (f"**Yes.** **{a}** red cells can be given to a **{b}** patient. "
                        + ("But **O-** is scarce, so use it as a last resort. " if a == "O-" and b != "O-" else "")
                        + "A clinician must confirm any substitution."), ["Which blood groups are compatible with B+?", "Which blood groups are at risk of shortage?"]
            return (f"**No.** **{a}** red cells should not be given to a **{b}** patient. "
                    f"A **{b}** patient can receive from: {', '.join(COMPAT_DONORS[b])}."), ["Which blood groups are compatible with B+?"]
        if group:
            donors = COMPAT_DONORS[group]
            recips = [r for r, ds in COMPAT_DONORS.items() if group in ds]
            return (f"**{group}** red cells can be **given to**: {', '.join(recips)}.\n"
                    f"A **{group}** patient can **receive from**: {', '.join(donors)}.\n"
                    "O- is the universal donor but is scarce, so I treat it as a last resort. "
                    "A clinician must confirm any substitution."), ["Which blood groups are at risk of shortage?", "Which units are expiring soon?"]
        subs = [x for x in analytics.substitutions(conn) if x["options"]][:4]
        lines = [f"• **{x['group']}** at {x['bank']}: can borrow {', '.join(o['group'] + ' (' + str(o['spare']) + ')' for o in x['options'][:3])}" for x in subs]
        return ("Compatibility rules (red cells): O- gives to everyone, AB+ receives from everyone, and Rh- can only receive Rh-.\n"
                + ("Current in-house substitution options:\n" + "\n".join(lines) if lines else "No shortage currently has a compatible group to spare.")
                + "\nOpen *Smart Redistribution* for the full matrix."), ["Can O- give to B+?", "Which blood groups are at risk of shortage?"]

    asks_where = has(t, "where", "nearest", "near me", "which bank", "who needs")
    if has(t, "eligib", "who can donate", "requirement", "criteria") or \
            (has(t, "can i donate", "am i able") and not asks_where):
        return ("To donate whole blood you should be:\n• aged **18-65**\n• weighing at least **45 kg**\n"
                "• hemoglobin of at least **12.5 g/dL**\n• at least **90 days** since your last donation\n"
                "• free of fever, infection and recent tattoos/surgery.\nA doctor makes the final decision at the bank."), \
               ["Where can I donate O+ blood?", "Which blood groups are at risk of shortage?"]

    if has(t, "expir"):
        lim = (date.today() + timedelta(days=5)).isoformat()
        rows = conn.execute(
            "SELECT k.name, COUNT(*) n FROM bags b JOIN banks k ON k.id=b.bank_id "
            "WHERE b.status='available' AND b.expiry_date BETWEEN ? AND ? GROUP BY k.name ORDER BY n DESC",
            (date.today().isoformat(), lim)).fetchall()
        if not rows:
            return "No units expire within the next 5 days.", SUGGEST[:3]
        lines = [f"• {r['name']}: **{r['n']}** units" for r in rows]
        return f"**{sum(r['n'] for r in rows)}** units expire within 5 days:\n" + "\n".join(lines) + \
               "\nSmart redistribution uses first-expiring-first-out so these move first.", SUGGEST[1:3]

    if has(t, "donate", "donation", "where") and not has(t, "shortage", "forecast"):
        if group:
            sel = sorted([c for c in cells if c["group"] == group], key=lambda c: c["cover"])[:3]
            lines = [f"• **{c['bank']}** - {c['units']} units, {c['cover']} days cover ({c['status']})" for c in sel]
            return f"**{group}** donors are most needed at:\n" + "\n".join(lines) + \
                   "\nThank you - one donation can save up to three lives.", SUGGEST[3:5]
        need = {}
        for c in cells:
            if c["status"] in ("critical", "low"):
                need.setdefault(c["bank"], []).append(c["group"])
        if not need:
            return "All banks are well stocked right now. Regular donors are still always welcome!", SUGGEST[:3]
        lines = [f"• **{b}** needs {', '.join(gs)}" for b, gs in need.items()]
        return "These banks need donors most:\n" + "\n".join(lines), SUGGEST[3:5]

    if has(t, "shortage", "critical", "forecast", "predict", "run out", "risk", "low stock", "running low"):
        al = [c for c in cells if c["status"] in ("critical", "low") and (group is None or c["group"] == group)]
        al.sort(key=lambda c: c["cover"])
        if not al:
            return "No shortage is predicted for the next 7 days" + (f" for **{group}**." if group else "."), SUGGEST[:3]
        lines = [f"• **{c['group']}** at {c['bank']}: {c['units']} units, ~{c['cover']} days cover - **{c['status']}**"
                 for c in al[:8]]
        return "My demand forecast flags:\n" + "\n".join(lines) + \
               "\nOpen *Smart Redistribution* to see recommended transfers.", ["Track active transfers", "Which units are expiring soon?"]

    if has(t, "stock", "available", "units", "how many", "inventory", "have"):
        if group:
            sel = [c for c in cells if c["group"] == group]
            lines = [f"• {c['bank']}: **{c['units']}** units ({c['cover']} days cover)" for c in sel]
            return f"**{group}** stock across the network ({sum(c['units'] for c in sel)} units):\n" + "\n".join(lines), SUGGEST[1:3]
        from db import GROUPS
        lines = [f"• {g}: **{sum(c['units'] for c in cells if c['group'] == g)}** units" for g in GROUPS]
        return "Network stock by blood group:\n" + "\n".join(lines), SUGGEST[1:3]

    if has(t, "track", "transfer", "drone", "delivery", "transit", "van"):
        act = [x for x in analytics.transfers(conn) if x["status"] == "in_transit"]
        if not act:
            return "No transfers are in transit. Approve a recommendation in *Smart Redistribution* to start one.", SUGGEST[1:3]
        lines = [f"• #{x['id']} {x['units']}×{x['group']} {x['from']['name']} → {x['to']['name']} "
                 f"by {x['mode']} - {int(x['progress'] * 100)}% done, ~{x['remaining_min']} min left" for x in act]
        return "Active transfers:\n" + "\n".join(lines), SUGGEST[:2]

    if has(t, "temperature", "cold", "sensor", "fridge", "storage", "iot"):
        rows = conn.execute("SELECT s.*, k.name FROM sensors s JOIN banks k ON k.id=s.bank_id "
                            "JOIN (SELECT bank_id, MAX(id) m FROM sensors GROUP BY bank_id) x ON s.id=x.m").fetchall()
        if not rows:
            return "No sensor readings yet - open the *Cold Chain* page to start live monitoring.", SUGGEST[:2]
        bad = [r for r in rows if r["status"] == "ALARM"]
        lines = [f"• {r['name']}: {r['temp']:.1f} °C ({r['link']}) - {r['status']}" for r in rows]
        head = f"⚠ **{len(bad)} storage unit(s) outside 2-6 °C!**" if bad else "All storage units are within the safe 2-6 °C range."
        return head + "\n" + "\n".join(lines), SUGGEST[:2]

    if has(t, "blockchain", "ledger", "verify", "tamper", "trace", "chain"):
        v = verify(conn)
        if v["valid"]:
            return f"The ledger is **valid** - all {v['length']} blocks verify, nothing has been tampered with.", SUGGEST[:2]
        return f"⚠ **Tampering detected** at block #{v['broken_at']} of {v['length']}.", SUGGEST[:2]

    if has(t, "hello", "hi", "hey", "help", "what can you"):
        return ("Hi! I'm the **BloodLink assistant**. I can tell you about stock levels, predicted shortages, "
                "where to donate, expiring units, live transfers, cold-chain health and ledger integrity."), SUGGEST

    return "I'm not sure about that yet. Try one of these:", SUGGEST
