"""Federated fraud model: FedAvg over merchants that never share rows.

Real data (IEEE-CIS via cardguard.data.ieee_cis, five verticals = five SuperNodes) when
datasets/train_transaction.csv is present; otherwise the synthetic three merchants below,
which the offline tests use.

Synthetic: three merchants, each mostly seeing ONE kind of fraud:
  electronics  - high-ticket fraud, new customers, at night
  travel       - cross-border fraud, prepaid cards
  digital      - card testing: micro-amounts, bursts, CVC failures
A model trained on any single merchant misses the other two fraud types.
FedAvg across all three catches all of them without pooling transactions.

The core is plain numpy so it runs anywhere; flower_app.py wraps the same
local_train / fedavg functions as a Flower ServerApp + ClientApp.
"""
from __future__ import annotations

import numpy as np

from cardguard.data import ieee_cis as fl_data

# Same feature interface for synthetic and real data, and for merchant.py at checkout.
# Amount features are relative to the merchant's own history (see cardguard.data.ieee_cis / merchant.py).
FEATURES = fl_data.FEATURES  # 9 features: see ieee_cis.FEATURES
MERCHANTS = ["electronics", "travel", "digital"]
FRAUD_TYPE = {"electronics": "high_ticket", "travel": "cross_border", "digital": "card_testing"}


def _rows(rng, n, kind):
    """kind: 'legit' or a fraud type. Returns (n, len(FEATURES)) float array."""
    b = lambda p: (rng.random(n) < p).astype(float)
    # columns: high_amount, micro_amount, country_mismatch, credit, velocity, new_customer, night,
    #          card_age, days_since_prev   (last two: fl_data.days_feature of a day count)
    age = lambda lo, hi: fl_data.days_feature(rng.uniform(lo, hi, n))
    if kind == "legit":
        return np.c_[b(.05), b(.05), b(.05), b(.25),
                     np.minimum(rng.poisson(.5, n), 10) / 10, b(.3), b(.1), age(0, 365), age(0, 90)]
    if kind == "high_ticket":
        return np.c_[b(.9), b(0), b(.1), b(.6),
                     np.minimum(rng.poisson(1, n), 10) / 10, b(.9), b(.7), age(0, 120), age(0, 60)]
    if kind == "cross_border":
        return np.c_[b(.3), b(0), b(.95), b(.7),
                     np.minimum(rng.poisson(1, n), 10) / 10, b(.6), b(.2), age(0, 200), age(0, 60)]
    if kind == "card_testing":
        return np.c_[b(0), b(.98), b(.2), b(.3),
                     np.minimum(rng.poisson(9, n), 10) / 10, b(.6), b(.3), age(0, 120), age(0, 30)]
    raise ValueError(kind)


def make_merchant_data(merchant: str, n: int = 4000, fraud_rate: float = .05,
                       own_share: float = .95, seed: int = 0):
    """Synthetic transactions for one merchant. Fraud is mostly its own type."""
    rng = np.random.default_rng(seed)
    n_fraud = int(n * fraud_rate)
    own = FRAUD_TYPE[merchant]
    others = [t for t in FRAUD_TYPE.values() if t != own]
    n_own = int(n_fraud * own_share)
    parts = [_rows(rng, n - n_fraud, "legit"), _rows(rng, n_own, own)]
    rest = n_fraud - n_own
    parts += [_rows(rng, rest // 2, others[0]), _rows(rng, rest - rest // 2, others[1])]
    X = np.vstack(parts)
    y = np.r_[np.zeros(n - n_fraud), np.ones(n_fraud)]
    idx = rng.permutation(len(y))
    return X[idx], y[idx]


def make_global_test(n_per_type: int = 400, n_legit: int = 6000, seed: int = 99):
    rng = np.random.default_rng(seed)
    X = np.vstack([_rows(rng, n_legit, "legit")] +
                  [_rows(rng, n_per_type, t) for t in FRAUD_TYPE.values()])
    y = np.r_[np.zeros(n_legit), np.ones(n_per_type * 3)]
    return X, y


# ---------- model: logistic regression, weights = [bias, w1..wd] ----------

def init_weights() -> np.ndarray:
    return np.zeros(len(FEATURES) + 1)


def predict_proba(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-(X @ w[1:] + w[0])))


def local_train(w: np.ndarray, X: np.ndarray, y: np.ndarray,
                epochs: int = 5, lr: float = 1.0, pos_weight: float = 10.0, sample_weight=None):
    """A few epochs of full-batch gradient descent. Returns (new_weights, n_examples)."""
    w = w.copy()
    sw = np.where(y == 1, pos_weight, 1.0)  # fraud is rare; upweight it
    if sample_weight is not None:
        sw = sw * np.asarray(sample_weight, dtype=float)
    for _ in range(epochs):
        err = (predict_proba(w, X) - y) * sw
        grad = np.r_[err.mean(), X.T @ err / len(y)]
        w -= lr * grad
    return w, len(y)


def fedavg(results: list[tuple[np.ndarray, int]]) -> np.ndarray:
    total = sum(n for _, n in results)
    return sum(w * (n / total) for w, n in results)


def auc(y: np.ndarray, p: np.ndarray) -> float:
    """Mann-Whitney AUC with average ranks for tied scores (banded features tie often)."""
    order = np.argsort(p, kind="mergesort")
    ps = p[order]
    ranks = np.empty(len(p))
    i = 0
    while i < len(p):
        j = i
        while j + 1 < len(p) and ps[j + 1] == ps[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    pos = y == 1
    n_pos, n_neg = pos.sum(), (~pos).sum()
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def train_federated(rounds: int = 30, seed: int = 0):
    data = {m: make_merchant_data(m, seed=seed + i) for i, m in enumerate(MERCHANTS)}
    w = init_weights()
    for _ in range(rounds):
        w = fedavg([local_train(w, *data[m]) for m in MERCHANTS])
    return w, data


def train_local_only(X, y, rounds: int = 30):
    w = init_weights()
    for _ in range(rounds):
        w, _ = local_train(w, X, y)
    return w


# Score cuts from the shipped real-data model (9 features): "high" = top 5% of held-out scores,
# "medium" = the next 15%. Re-derive from the score quantiles whenever the model changes.
BAND_CUTS = (0.28, 0.50)


def risk_band(p: float) -> str:
    return "low" if p < BAND_CUTS[0] else "medium" if p < BAND_CUTS[1] else "high"


def catch_rates(w: np.ndarray, threshold: float = .5, seed: int = 99) -> dict:
    """Share of each fraud type flagged (p >= threshold), plus legit false-positive rate."""
    rng = np.random.default_rng(seed)
    out = {t: float((predict_proba(w, _rows(rng, 1000, t)) >= threshold).mean())
           for t in FRAUD_TYPE.values()}
    out["legit_flagged"] = float((predict_proba(w, _rows(rng, 5000, "legit")) >= threshold).mean())
    return out


def report(seed: int = 0) -> dict:
    """AUC on a test set with all three fraud types, plus per-type catch rates."""
    w_fed, data = train_federated(seed=seed)
    Xt, yt = make_global_test()
    models = {f"local_only_{m}": train_local_only(*data[m]) for m in MERCHANTS}
    models["federated"] = w_fed
    Xall = np.vstack([d[0] for d in data.values()])
    yall = np.concatenate([d[1] for d in data.values()])
    models["centralized_upper_bound"] = train_local_only(Xall, yall)
    return {name: {"auc": auc(yt, predict_proba(w, Xt)), **catch_rates(w)}
            for name, w in models.items()}


# ---------- real data: IEEE-CIS verticals ----------

def train_federated_real(rounds: int = 100, data: dict | None = None):
    """FedAvg across the five ProductCD verticals; each node trains on its own rows only."""
    data = data or fl_data.load()
    parts = {v: fl_data.split(data, v) for v in fl_data.VERTICALS}
    w = init_weights()
    for _ in range(rounds):
        w = fedavg([local_train(w, *parts[v]) for v in fl_data.VERTICALS])
    return w, parts


def report_real(rounds: int = 100) -> dict:
    """AUC on each vertical's held-out later transactions: local-only vs federated vs pooled."""
    data = fl_data.load()
    w_fed, parts = train_federated_real(rounds, data)
    tests = {v: fl_data.split(data, v, test=True) for v in fl_data.VERTICALS}
    Xt = np.vstack([tests[v][0] for v in fl_data.VERTICALS])
    yt = np.concatenate([tests[v][1] for v in fl_data.VERTICALS])
    models = {f"local_only_{v}": train_local_only(*parts[v], rounds=rounds) for v in fl_data.VERTICALS}
    models["federated"] = w_fed
    models["centralized_upper_bound"] = train_local_only(
        np.vstack([parts[v][0] for v in fl_data.VERTICALS]),
        np.concatenate([parts[v][1] for v in fl_data.VERTICALS]), rounds=rounds)
    return {name: {**{v: auc(tests[v][1], predict_proba(w, tests[v][0])) for v in fl_data.VERTICALS},
                   "all": auc(yt, predict_proba(w, Xt))}
            for name, w in models.items()}


if __name__ == "__main__":
    print("synthetic merchants: share of each fraud type caught (p >= 0.5)")
    cols = ["auc", *FRAUD_TYPE.values(), "legit_flagged"]
    print(f"{'model':26s}" + "".join(f"{c:>15s}" for c in cols))
    for name, r in report().items():
        print(f"{name:26s}" + "".join(f"{r[c]:15.3f}" for c in cols))
    if fl_data.available():
        print("\nIEEE-CIS (real): AUC on each vertical's held-out later transactions")
        cols = [*fl_data.VERTICALS, "all"]
        print(f"{'model':26s}" + "".join(f"{c:>8s}" for c in cols))
        for name, r in report_real().items():
            print(f"{name:26s}" + "".join(f"{r[c]:8.3f}" for c in cols))
