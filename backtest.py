"""Impact backtest: would predictive redistribution have prevented shortages and expiry waste?

The last WINDOW days of demand history are held out. A fresh model is trained ONLY on the days before
the window, then three policies are replayed day by day over the window on identical demand and supply:

  none        every bank works alone (no transfers)
  reactive    transfers use a naive forecast (trailing 7-day average demand)
  predictive  transfers use the ML forecast (recursive 7-day Gradient Boosting)

Blood units age day by day (35-day shelf life), are issued first-expiring-first-out, and units that reach
zero days are counted as expired. Supply (donations) is simulated, because demand history alone has no
donation records: each bank receives a random share of the network supply, so some banks run surplus while
others run short. With imported real data, demand is real but supply is still a model - state this clearly.
"""
import math
from datetime import date, timedelta

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor

import ml
from analytics import haversine

SHELF = 35
HORIZON = 7
WINDOW = 60
SEEDS = (5, 6, 7, 8, 9)      # five random supply scenarios, results are averaged
IMBALANCE = (0.4, 1.8)       # each bank receives 0.4x to 1.8x its fair share of donations
START_DAYS = 10              # opening stock = 10 days of average demand
SUPPLY_LEVELS = ((1.0, "Balanced supply (donations = demand)"), (1.15, "15% oversupply (donations > demand)"))
LABELS = {"none": "No redistribution", "reactive": "Reactive (trailing average)", "predictive": "Predictive (ML forecast)"}
CACHE = {}


def _take_fefo(stock, qty, start):
    """Remove up to qty units, soonest-expiring first, from index >= start. Returns the removed array."""
    taken = np.zeros_like(stock)
    for i in range(start, len(stock)):
        if qty <= 0:
            break
        q = int(min(qty, stock[i]))
        if q:
            stock[i] -= q
            taken[i] = q
            qty -= q
    return taken


def run(conn, groups):
    series = ml._load(conn)
    keys = sorted(series)
    n = min(len(series[k]["vals"]) for k in keys)
    vals = {k: np.array(series[k]["vals"][-n:], dtype=float) for k in keys}
    d0 = date.fromisoformat(series[keys[0]]["days"][-n])
    window = WINDOW if n >= 120 else max(20, n // 3)
    te = n - window
    banks = {r["id"]: r for r in conn.execute("SELECT * FROM banks")}
    dist = {(a, b): haversine(banks[a]["lat"], banks[a]["lng"], banks[b]["lat"], banks[b]["lng"]) for a in banks for b in banks}
    gi = {g: i for i, g in enumerate(groups)}

    # ---- train on the days BEFORE the window only (no leakage)
    X, y = [], []
    for k in keys:
        for t in range(28, te):
            X.append(ml._features(vals[k][:t], d0 + timedelta(t), k[0], gi[k[1]]))
            y.append(vals[k][t])
    model = GradientBoostingRegressor(n_estimators=150, learning_rate=0.06, max_depth=3, random_state=42)
    model.fit(np.array(X), np.array(y))

    # ---- forecasts available at the end of each day t (history up to t-1)
    fc = {"reactive": {}, "predictive": {}}
    for t in range(te, n + 1):
        hist = {k: list(vals[k][:t]) for k in keys}
        preds = {k: [] for k in keys}
        for step in range(HORIZON):
            d = d0 + timedelta(t + step)
            Xs = np.array([ml._features(hist[k], d, k[0], gi[k[1]]) for k in keys])
            p = np.maximum(model.predict(Xs), 0)
            for k, v in zip(keys, p):
                hist[k].append(float(v))
                preds[k].append(float(v))
        fc["predictive"][t] = {k: float(np.mean(preds[k])) for k in keys}
        fc["reactive"][t] = {k: float(np.mean(vals[k][t - 7:t])) for k in keys}

    base = {k: float(np.mean(vals[k][:te])) for k in keys}

    def scenario(seed, level):
        """Random split of network supply across banks + opening stock; identical for all policies."""
        rng = np.random.default_rng(seed)
        mu = {}
        for g in groups:
            ks = [k for k in keys if k[1] == g]
            raw = rng.uniform(*IMBALANCE, size=len(ks))
            m = np.array([base[k] for k in ks]) * raw
            m *= level * sum(base[k] for k in ks) / max(m.sum(), 1e-9)   # network supply = level x demand
            for k, v in zip(ks, m):
                mu[k] = float(v)
        init = {}
        for k in keys:
            total = int(round(START_DAYS * base[k]))
            init[k] = np.bincount(rng.integers(6, SHELF + 1, size=total), minlength=SHELF + 1).astype(int)
        return mu, init

    def simulate(policy, seed, level):
        mu, init = scenario(seed, level)
        stock = {k: init[k].copy() for k in keys}
        pending, incoming = {}, {k: 0 for k in keys}
        prng = np.random.default_rng(seed + 100)
        unmet = expired = events = transfers = moved_units = 0
        total_demand = 0.0
        cum, cum_unmet = [], 0
        for t in range(te, n):
            for k in keys:                                   # 1) age
                expired += int(stock[k][0])
                stock[k][:-1] = stock[k][1:].copy()
                stock[k][-1] = 0
            for k, arr in pending.pop(t, []):                # transfers dispatched yesterday arrive
                stock[k] += arr
                incoming[k] -= int(arr.sum())
            for k in keys:                                   # 2) donations
                stock[k][SHELF] += int(prng.poisson(mu[k]))
            day_unmet = 0
            for k in keys:                                   # 3) demand, soonest-expiring first
                need = int(vals[k][t])
                total_demand += need
                taken = _take_fefo(stock[k], need, 1)
                short = need - int(taken.sum())
                if short > 0:
                    unmet += short
                    day_unmet += short
                    events += 1
            cum_unmet += day_unmet
            cum.append(cum_unmet)
            if policy == "none":
                continue
            f = fc[policy][t + 1]                            # 4) decide using info up to today
            for g in groups:
                ks = [k for k in keys if k[1] == g]
                info = {}
                for k in ks:
                    fk = max(f[k], 0.05)
                    st = int(stock[k][1:].sum())
                    info[k] = (fk, st, incoming[k], (st + incoming[k]) / fk)
                recips = sorted([k for k in ks if info[k][3] < 4], key=lambda k: info[k][3])
                avail = {k: int(stock[k][3:].sum()) - math.ceil(10 * info[k][0]) for k in ks if info[k][3] > 10}
                for r in recips:
                    fk, st, inc, _ = info[r]
                    need = math.ceil(7 * fk) - (st + inc)
                    if need <= 0:
                        continue
                    for d in sorted([k for k in avail if avail[k] > 0], key=lambda d: dist[(d[0], r[0])]):
                        if need <= 0:
                            break
                        taken = _take_fefo(stock[d], min(need, avail[d]), 3)
                        moved = int(taken.sum())
                        if moved == 0:
                            continue
                        avail[d] -= moved
                        need -= moved
                        arr = np.zeros_like(taken)
                        arr[:-1] = taken[1:]                 # one day spent in transit
                        pending.setdefault(t + 1, []).append((r, arr))
                        incoming[r] += moved
                        transfers += 1
                        moved_units += moved
        return {
            "policy": policy, "label": LABELS[policy],
            "fill_rate": round(100 * (1 - unmet / max(total_demand, 1)), 2),
            "unmet": int(unmet), "shortage_events": int(events), "expired": int(expired),
            "transfers": int(transfers), "units_moved": int(moved_units), "cum_unmet": cum,
        }

    def evaluate(level):
        results = []
        for p in ("none", "reactive", "predictive"):
            runs = [simulate(p, sd, level) for sd in SEEDS]
            agg = {"policy": p, "label": LABELS[p]}
            for key in ("unmet", "shortage_events", "expired", "transfers", "units_moved"):
                agg[key] = round(float(np.mean([r[key] for r in runs])), 1)
            agg["fill_rate"] = round(float(np.mean([r["fill_rate"] for r in runs])), 2)
            agg["fill_min"] = round(min(r["fill_rate"] for r in runs), 2)
            agg["fill_max"] = round(max(r["fill_rate"] for r in runs), 2)
            agg["cum_unmet"] = [round(float(v), 1) for v in np.mean([r["cum_unmet"] for r in runs], axis=0)]
            agg["_runs"] = runs
            results.append(agg)
        base_r = results[0]
        for r in results:
            per = [100 * (b["unmet"] - x["unmet"]) / max(b["unmet"], 1) for b, x in zip(base_r["_runs"], r["_runs"])]
            r["unmet_reduction"] = round(float(np.mean(per)), 1)
            r["unmet_reduction_min"] = round(min(per), 1)
            r["unmet_reduction_max"] = round(max(per), 1)
            r["events_reduction"] = round(100 * (base_r["shortage_events"] - r["shortage_events"]) / max(base_r["shortage_events"], 1), 1)
            r["expired_reduction"] = round(100 * (base_r["expired"] - r["expired"]) / base_r["expired"], 1) if base_r["expired"] > 0 else 0.0
        for r in results:
            r.pop("_runs")
        return results

    out = {
        "window_days": window, "train_days": te, "series": len(keys), "scenarios": len(SEEDS),
        "imbalance": list(IMBALANCE), "start_days": START_DAYS,
        "dates": [(d0 + timedelta(t)).isoformat() for t in range(te, n)],
        "levels": [{"supply": lv, "label": lb, "results": evaluate(lv)} for lv, lb in SUPPLY_LEVELS],
        "total_demand": int(sum(vals[k][te:].sum() for k in keys)),
    }
    CACHE.clear()
    CACHE.update(out)
    return out
