"""Federated fraud model on synthetic data.

Three merchants, each mostly seeing ONE kind of fraud:
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

FEATURES = ["high_amount", "micro_amount", "country_mismatch", "prepaid",
            "cvc_fail", "velocity", "new_customer", "night"]
MERCHANTS = ["electronics", "travel", "digital"]
FRAUD_TYPE = {"electronics": "high_ticket", "travel": "cross_border", "digital": "card_testing"}


def _rows(rng, n, kind):
    """kind: 'legit' or a fraud type. Returns (n, len(FEATURES)) float array."""
    b = lambda p: (rng.random(n) < p).astype(float)
    if kind == "legit":
        amount = rng.lognormal(4.0, 0.8, n)
        return np.c_[amount > 500, amount < 5, b(.05), b(.05), b(.01),
                     np.minimum(rng.poisson(.5, n), 10) / 10, b(.3), b(.1)]
    if kind == "high_ticket":
        return np.c_[b(.9), b(0), b(.1), b(.1), b(.05),
                     np.minimum(rng.poisson(1, n), 10) / 10, b(.9), b(.7)]
    if kind == "cross_border":
        return np.c_[b(.3), b(0), b(.95), b(.5), b(.05),
                     np.minimum(rng.poisson(1, n), 10) / 10, b(.6), b(.2)]
    if kind == "card_testing":
        return np.c_[b(0), b(.95), b(.2), b(.3), b(.6),
                     np.minimum(rng.poisson(8, n), 10) / 10, b(.5), b(.3)]
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
                epochs: int = 5, lr: float = 1.0, pos_weight: float = 10.0):
    """A few epochs of full-batch gradient descent. Returns (new_weights, n_examples)."""
    w = w.copy()
    sw = np.where(y == 1, pos_weight, 1.0)  # fraud is rare; upweight it
    for _ in range(epochs):
        err = (predict_proba(w, X) - y) * sw
        grad = np.r_[err.mean(), X.T @ err / len(y)]
        w -= lr * grad
    return w, len(y)


def fedavg(results: list[tuple[np.ndarray, int]]) -> np.ndarray:
    total = sum(n for _, n in results)
    return sum(w * (n / total) for w, n in results)


def auc(y: np.ndarray, p: np.ndarray) -> float:
    order = np.argsort(p)
    ranks = np.empty(len(p))
    ranks[order] = np.arange(1, len(p) + 1)
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


def risk_band(p: float) -> str:
    return "low" if p < .2 else "medium" if p < .6 else "high"


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


if __name__ == "__main__":
    cols = ["auc", *FRAUD_TYPE.values(), "legit_flagged"]
    print(f"{'model':26s}" + "".join(f"{c:>15s}" for c in cols))
    for name, r in report().items():
        print(f"{name:26s}" + "".join(f"{r[c]:15.3f}" for c in cols))
