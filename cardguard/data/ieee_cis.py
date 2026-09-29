"""Real transactions for the federated fraud model: IEEE-CIS (Vesta) via Kaggle.

datasets/train_transaction.csv is licensed under the Kaggle competition rules, is 650 MB,
and is git-ignored. Each ProductCD (W, C, R, H, S) is one merchant vertical = one SuperNode.

Per row we derive the same kind of features merchant.py computes at checkout, each
relative to the vertical's own history (cuts come from that vertical's training rows only):
  high_amount       amount above the vertical's 90th percentile
  micro_amount      amount below the vertical's 10th percentile
  country_mismatch  billing country missing or not the platform's home country (addr2 != 87)
  credit            card6 == credit (debit is the platform's norm; no prepaid cards in this data)
  velocity          prior transactions by the same card proxy in the previous 24h, min(n,10)/10
  new_customer      D1 == 0: first transaction seen on this card
  night             hour of day < 6 (TransactionDT is seconds from a reference time)
  card_age          D1, days since this card was first seen, scaled log1p(d)/log1p(365), capped at 1
  days_since_prev   D3, days since this card's previous transaction, same scaling (0 if none)
There is no CVC-check column, so the model has no cvc feature on real data; the coordinator's
hard decline on cvc_check=fail covers that signal in the rules.

Card proxy: (card1, card2, card3, card5, addr1, P_emaildomain), the community-standard key,
because card1 alone is shared by thousands of cards. Nothing here is a card number.

Time split: the last 20% of the observation window is the test set for every vertical, so
catch rates are measured on later transactions than the model saw.
"""
from __future__ import annotations

import collections
import csv
import os

import numpy as np

from cardguard import ROOT

CSV = str(ROOT / "datasets" / "train_transaction.csv")
CACHE = str(ROOT / "datasets" / "features.npz")
FEATURES = ["high_amount", "micro_amount", "country_mismatch", "credit",
            "velocity", "new_customer", "night", "card_age", "days_since_prev"]
DAY_SCALE = np.log1p(365.0)


def days_feature(days) -> np.ndarray:
    """Bounded [0, 1] encoding of a day count; missing/negative -> 0."""
    d = np.nan_to_num(np.asarray(days, dtype=float), nan=0.0)
    return np.minimum(np.log1p(np.maximum(d, 0)) / DAY_SCALE, 1.0)
VERTICALS = ["W", "C", "R", "H", "S"]
HOME_COUNTRY = "87.0"  # addr2 code for the platform's home country (88% of rows)
TEST_SHARE = 0.20


def available() -> bool:
    return os.path.exists(CSV) or os.path.exists(CACHE)


def _read_csv():
    """Yields (vertical, dt, amount, addr2, card6, D1, card_proxy, y, D3) per row."""
    with open(CSV, newline="") as fh:
        r = csv.reader(fh)
        cols = next(r)
        ix = {c: i for i, c in enumerate(cols)}
        for row in r:
            proxy = (row[ix["card1"]], row[ix["card2"]], row[ix["card3"]],
                     row[ix["card5"]], row[ix["addr1"]], row[ix["P_emaildomain"]])
            yield (row[ix["ProductCD"]], int(row[ix["TransactionDT"]]),
                   float(row[ix["TransactionAmt"]]), row[ix["addr2"]], row[ix["card6"]],
                   row[ix["D1"]], proxy, int(row[ix["isFraud"]]), row[ix["D3"]])


def build_cache() -> dict:
    rows = list(_read_csv())
    n = len(rows)
    vert = np.array([x[0] for x in rows])
    dt = np.array([x[1] for x in rows])
    amt = np.array([x[2] for x in rows])
    y = np.array([x[7] for x in rows], dtype=float)

    # velocity: prior transactions of the same card proxy in the previous 24h (no lookahead)
    vel = np.zeros(n, dtype=float)
    recent: dict = collections.defaultdict(collections.deque)
    for i in np.argsort(dt, kind="stable"):
        q = recent[rows[i][6]]
        while q and dt[i] - q[0] > 86400:
            q.popleft()
        vel[i] = min(len(q), 10) / 10
        q.append(dt[i])

    cut = np.percentile(dt, 100 * (1 - TEST_SHARE))
    is_test = dt > cut

    X = np.zeros((n, len(FEATURES)))
    cuts = {}
    for v in VERTICALS:
        m = vert == v
        train_amt = amt[m & ~is_test]
        p10, p50, p90 = np.percentile(train_amt, [10, 50, 90])
        cuts[v] = (float(p10), float(p50), float(p90))  # relative amount bands: low < p50 <= medium < p90 <= high
        X[m, 0] = amt[m] > p90
        X[m, 1] = amt[m] < p10
    X[:, 2] = [x[3] == "" or x[3] != HOME_COUNTRY for x in rows]
    X[:, 3] = [x[4] == "credit" for x in rows]
    X[:, 4] = vel
    X[:, 5] = [x[5] == "0.0" for x in rows]
    X[:, 6] = ((dt // 3600) % 24) < 6
    X[:, 7] = days_feature([float(x[5]) if x[5] else np.nan for x in rows])
    X[:, 8] = days_feature([float(x[8]) if x[8] else np.nan for x in rows])

    np.savez_compressed(CACHE, X=X, y=y, vert=vert, is_test=is_test,
                        cut_names=np.array(VERTICALS), cuts=np.array([cuts[v] for v in VERTICALS]))
    global _LOADED
    _LOADED = None
    return load()


_LOADED: dict | None = None


def load() -> dict:
    """{'X','y','vert','is_test','cuts': {vertical: (p10, p50, p90)}}, loaded once per process."""
    global _LOADED
    if _LOADED is not None:
        return _LOADED
    if not os.path.exists(CACHE):
        return build_cache()
    z = np.load(CACHE, allow_pickle=False)
    cuts = {str(k): tuple(map(float, c)) for k, c in zip(z["cut_names"], z["cuts"])}
    _LOADED = {"X": z["X"], "y": z["y"], "vert": z["vert"], "is_test": z["is_test"], "cuts": cuts}
    return _LOADED


def split(data: dict, vertical: str, test: bool = False):
    m = (data["vert"] == vertical) & (data["is_test"] if test else ~data["is_test"])
    return data["X"][m], data["y"][m]


if __name__ == "__main__":
    d = load()
    print(f"rows {len(d['y']):,}  features {FEATURES}")
    for v in VERTICALS:
        _, ytr = split(d, v)
        _, yte = split(d, v, test=True)
        print(f"{v}: train {len(ytr):7,} (fraud {ytr.mean():.2%})  test {len(yte):6,} (fraud {yte.mean():.2%})"
              f"  amount p10/p50/p90 = ${d['cuts'][v][0]:.0f}/${d['cuts'][v][1]:.0f}/${d['cuts'][v][2]:.0f}")
