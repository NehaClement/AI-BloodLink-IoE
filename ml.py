"""Technologies 5-7 - Big data analytics + Machine learning demand forecasting.

A single model is trained across every (blood bank, blood group) demand series using lag and
calendar features. Random Forest and Gradient Boosting are compared with a 7-day moving-average
baseline on the last 30 days (time-based split - no shuffling, no data leakage). The best model
is refit on all data and used to forecast the next 7 days recursively.
"""
from datetime import date, timedelta

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

FEATURES = ["blood bank", "blood group", "weekday", "day of month", "lag 1", "lag 7", "mean 7", "mean 14", "mean 28"]
MODEL = {"ready": False, "metrics": None, "fc": {}, "fc_dates": [], "hist": {}, "importance": [], "best": None}


def _features(h, d, bank, group_idx):
    return [bank, group_idx, d.weekday(), d.day, h[-1], h[-7],
            float(np.mean(h[-7:])), float(np.mean(h[-14:])), float(np.mean(h[-28:]))]


def _load(conn):
    series = {}
    rows = conn.execute("SELECT bank_id, blood_group, day, units FROM demand ORDER BY bank_id, blood_group, day")
    for r in rows:
        s = series.setdefault((r["bank_id"], r["blood_group"]), {"days": [], "vals": []})
        s["days"].append(r["day"])
        s["vals"].append(r["units"])
    return series


def train(conn, groups):
    series = _load(conn)
    X_tr, y_tr, X_te, y_te, base = [], [], [], [], []
    for (bank, group), s in series.items():
        vals = s["vals"]
        days = [date.fromisoformat(x) for x in s["days"]]
        n = len(vals)
        gi = groups.index(group)
        for t in range(28, n):
            x = _features(vals[:t], days[t], bank, gi)
            if t < n - 30:
                X_tr.append(x); y_tr.append(vals[t])
            else:
                X_te.append(x); y_te.append(vals[t]); base.append(float(np.mean(vals[t - 7:t])))
    X_tr, y_tr, X_te, y_te, base = map(np.array, (X_tr, y_tr, X_te, y_te, base))

    def score(name, pred):
        return {"model": name, "mae": round(float(mean_absolute_error(y_te, pred)), 3),
                "rmse": round(float(np.sqrt(mean_squared_error(y_te, pred))), 3),
                "wmape": round(float(np.abs(y_te - pred).sum() / max(y_te.sum(), 1) * 100), 1)}

    metrics = [score("Baseline: 7-day moving average", base)]
    candidates = {
        "Random Forest": RandomForestRegressor(n_estimators=150, min_samples_leaf=2, random_state=42, n_jobs=-1),
        "Gradient Boosting": GradientBoostingRegressor(n_estimators=200, learning_rate=0.05, max_depth=3, random_state=42),
    }
    fitted = {}
    for name, m in candidates.items():
        m.fit(X_tr, y_tr)
        fitted[name] = m
        metrics.append(score(name, m.predict(X_te)))
    best_name = min(candidates, key=lambda k: next(x["mae"] for x in metrics if x["model"] == k))
    best = candidates[best_name]
    best.fit(np.vstack([X_tr, X_te]), np.concatenate([y_tr, y_te]))   # refit on everything

    keys = list(series.keys())
    hist = {k: [float(v) for v in series[k]["vals"]] for k in keys}
    fc = {k: [] for k in keys}
    last = date.fromisoformat(series[keys[0]]["days"][-1])
    dates = []
    for step in range(1, 8):
        d = last + timedelta(days=step)
        dates.append(d.isoformat())
        X = np.array([_features(hist[k], d, k[0], groups.index(k[1])) for k in keys])
        for k, p in zip(keys, best.predict(X)):
            p = max(0.0, float(p))
            hist[k].append(p)
            fc[k].append(round(p, 1))

    base_mae = metrics[0]["mae"]
    best_mae = next(x["mae"] for x in metrics if x["model"] == best_name)
    imp = sorted(zip(FEATURES, best.feature_importances_), key=lambda x: -x[1])
    MODEL.update({
        "ready": True, "best": best_name, "fc": fc, "fc_dates": dates,
        "hist": {k: {"days": series[k]["days"][-60:], "vals": series[k]["vals"][-60:]} for k in keys},
        "metrics": {"table": metrics, "best": best_name, "test_days": 30, "train_rows": int(len(X_tr)),
                    "test_rows": int(len(X_te)), "improvement_pct": round((base_mae - best_mae) / base_mae * 100, 1)},
        "importance": [{"feature": f, "value": round(float(v), 4)} for f, v in imp],
    })


def _keys(bank, group):
    return [k for k in MODEL["hist"] if bank in (0, k[0]) and (group is None or group == k[1])]


def history(bank, group, days=30):
    ks = _keys(bank, group)
    dates = MODEL["hist"][ks[0]]["days"][-days:]
    vals = np.sum([MODEL["hist"][k]["vals"][-days:] for k in ks], axis=0)
    return {"dates": dates, "values": [float(v) for v in vals]}


def forecast(bank, group):
    ks = _keys(bank, group)
    vals = np.sum([MODEL["fc"][k] for k in ks], axis=0)
    return {"dates": MODEL["fc_dates"], "values": [round(float(v), 1) for v in vals]}


def daily_forecast(bank, group):
    return sum(MODEL["fc"][(bank, group)]) / 7.0
