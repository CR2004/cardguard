"""Raw columns for the specialists: the IEEE-CIS transaction file, or a synthetic stand-in.

Only the columns the live features need are read: time, amount, billing country and region codes, card type, the
two card-history day counts, and the keys that identify a card. A "raw" dict is column-oriented numpy (the file is
650 MB); numeric columns use NaN for missing, categorical columns are small int codes with -1 for missing plus a
vocab list per column.
"""
from __future__ import annotations

import csv
import os
from array import array

import numpy as np

from cardguard import ROOT

TX_CSV = str(ROOT / "datasets" / "train_transaction.csv")
CACHE = str(ROOT / "datasets" / "specialist_features.npz")  # git-ignored with the rest of datasets/

NUM_COLS = ["TransactionDT", "TransactionAmt", "addr1", "addr2", "D1", "D3", "card1", "card2", "card3", "card5"]
CAT_COLS = ["ProductCD", "card6", "P_emaildomain"]


def available() -> bool:
    return os.path.exists(TX_CSV)


def _num(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return np.nan


def read_transactions(path: str = TX_CSV, limit: int | None = None) -> dict:
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        r = csv.reader(fh)
        cols = next(r)
        ix = {c: i for i, c in enumerate(cols)}
        num_ix = [ix[c] for c in NUM_COLS]
        cat_ix = [ix[c] for c in CAT_COLS]
        num, cat, y = array("d"), array("h"), array("b")
        vocab = [dict() for _ in CAT_COLS]
        for k, row in enumerate(r):
            if limit is not None and k >= limit:
                break
            num.extend([_num(row[i]) for i in num_ix])
            for j, i in enumerate(cat_ix):
                v = row[i]
                cat.append(-1 if v == "" else vocab[j].setdefault(v, len(vocab[j])))
            y.append(int(row[ix["isFraud"]]))
    num = np.frombuffer(num, dtype=float).reshape(-1, len(NUM_COLS))
    cat = np.frombuffer(cat, dtype=np.int16).reshape(-1, len(CAT_COLS)).astype(np.int64)
    col = lambda name: num[:, NUM_COLS.index(name)]
    words = [sorted(v, key=v.get) for v in vocab]
    return {
        "y": np.frombuffer(y, dtype=np.int8).astype(float),
        "dt": col("TransactionDT"), "amt": col("TransactionAmt"),
        "addr1": col("addr1"), "addr2": col("addr2"), "D1": col("D1"), "D3": col("D3"),
        "card": num[:, [NUM_COLS.index(c) for c in ("card1", "card2", "card3", "card5")]],
        "prod": cat[:, 0], "card6": cat[:, 1], "pemail": cat[:, 2],
        "vocab": {"prod": words[0], "card6": words[1], "pemail": words[2]},
    }


def load_raw(limit: int | None = None) -> dict:
    return read_transactions(limit=limit)


# ---------- synthetic ----------

FRAUD_TYPES = ["transaction", "identity", "geo", "behavior"]


def synthetic_raw(n: int = 12000, seed: int = 0) -> dict:
    """Four fraud types, each visible mainly to ONE specialist, so no single family sees them all.

    transaction: round amounts             identity: a first-time card that is a credit card
    geo: the billing country differs       behavior: amount and hour far from this card's own habit
    """
    rng = np.random.default_rng(seed)
    n_cards = n // 4
    cid = rng.integers(0, n_cards, n)
    kind = rng.choice(len(FRAUD_TYPES) + 1, n, p=[0.96] + [0.01] * len(FRAUD_TYPES))  # 0 = legit
    is_ = lambda name: kind == FRAUD_TYPES.index(name) + 1

    home_hour = rng.integers(8, 22, n_cards)
    card_la = rng.normal(3.5, 1.0, n_cards)
    card_age = rng.integers(5, 400, n_cards)
    hour = np.clip(np.rint(home_hour[cid] + rng.normal(0, 1.5, n)), 0, 23)
    hour = np.where(is_("behavior"), (home_hour[cid] + 12) % 24, hour)
    la = card_la[cid] + rng.normal(0, 0.3, n) + np.where(is_("behavior"), 1.5, 0.0)
    day = rng.integers(0, 60, n)
    dt = day * 86400.0 + hour * 3600 + rng.integers(0, 3600, n)
    amt = np.round(np.exp(la) - 1 + 1.37, 2)
    amt = np.where(is_("transaction"), np.maximum(np.round(amt / 50) * 50, 50), amt)

    card = np.c_[cid.astype(float), 100 + cid % 900, np.full(n, 150.0), 200 + cid % 50]
    a2 = np.where(is_("geo"), 60.0, np.where(rng.random(n) < 0.94, 87.0, np.nan))
    d1 = np.where(is_("identity"), 0.0, card_age[cid] + day)
    d3 = np.where(rng.random(n) < 0.3, np.nan, rng.exponential(10, n))
    credit = np.where(is_("identity"), 1, (rng.random(n) < 0.25).astype(int))
    return {
        "y": (kind > 0).astype(float), "dt": dt, "amt": amt,
        "addr1": (100 + cid % 50).astype(float), "addr2": a2, "D1": d1, "D3": d3, "card": card,
        "prod": rng.integers(0, 5, n), "card6": credit, "pemail": (cid % 8).astype(int),
        "vocab": {"prod": ["W", "C", "R", "H", "S"], "card6": ["debit", "credit"],
                  "pemail": [f"mail{i}.com" for i in range(8)]},
    }
