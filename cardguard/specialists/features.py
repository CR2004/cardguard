"""The four signal families the live merchant node can compute at checkout. Every feature is in [0, 1].

  transaction  amount above / below this merchant's 90th / 10th percentile, whole-dollar and whole-ten-dollar
               amounts, purchases by this card in the previous 24 hours
  identity     credit card, card age, first time this merchant sees the card
  geo          card country differs from the buyer's country
  behavior     night hour, hour unusual for this card, history length, amount deviation from this card's own
               habit, days since the card's previous purchase

The names and order are live.FEATURES: the node computes exactly these. History features look only at PRIOR
rows (no lookahead), and every cut, cap and percentile comes from the training period only.
"""
from __future__ import annotations

import collections
import math

import numpy as np

from cardguard.data.ieee_cis import days_feature
from cardguard.specialists.live import FEATURES

FAMILIES = list(FEATURES)
TEST_SHARE = 0.20  # same time holdout as cardguard.data.ieee_cis
HOME_COUNTRY = 87.0
HIST = ["velocity", "has_hist", "amt_dev", "hour_unusual", "prior_count"]


def _intern(keys) -> np.ndarray:
    seen: dict = {}
    return np.array([seen.setdefault(k, len(seen)) for k in keys], dtype=np.int64)


def _cap(x: np.ndarray, train: np.ndarray) -> float:
    v = x[train]
    v = v[np.isfinite(v)]
    return max(float(np.percentile(v, 99)), 1.0) if len(v) else 1.0


def _log_scale(x: np.ndarray, cap: float) -> np.ndarray:
    return np.minimum(np.log1p(np.maximum(np.nan_to_num(x, nan=0.0), 0.0)) / np.log1p(cap), 1.0)


def history_features(raw: dict) -> dict:
    """Unscaled per-row history, computed in time order over PRIOR rows only."""
    dt, n = raw["dt"], len(raw["dt"])
    card = np.nan_to_num(raw["card"], nan=-1).astype(np.int64)
    a1 = np.nan_to_num(raw["addr1"], nan=-1).astype(np.int64).tolist()
    pe = raw["pemail"].tolist()
    ck = _intern(map(tuple, card.tolist())).tolist()  # card key: card1,2,3,5
    pk = _intern(zip(ck, a1, pe)).tolist()            # card proxy: + addr1 + payer email
    hr = ((dt // 3600) % 24).astype(np.int64).tolist()
    la = np.log1p(np.maximum(raw["amt"], 0)).tolist()
    dtl = dt.tolist()
    out = {k: np.zeros(n) for k in HIST}

    recent = collections.defaultdict(collections.deque)
    amt_stats, hours = {}, {}
    for i in np.argsort(dt, kind="stable").tolist():
        p, t = pk[i], dtl[i]
        q = recent[p]
        while q and t - q[0] > 86400:
            q.popleft()
        out["velocity"][i] = min(len(q), 10) / 10
        q.append(t)

        st = amt_stats.get(p)
        if st is None:
            amt_stats[p], hours[p] = [1, la[i], 0.0], [0] * 24
        else:
            k, mean, m2 = st
            out["prior_count"][i] = k
            if k >= 2:
                out["has_hist"][i] = 1
                out["amt_dev"][i] = min(abs(la[i] - mean) / max(math.sqrt(m2 / k), 0.25) / 4, 1.0)
            if k >= 3:
                out["hour_unusual"][i] = float(sum(hours[p][(hr[i] + d) % 24] for d in range(-3, 4)) == 0)
            k += 1
            d = la[i] - mean
            mean += d / k
            st[:] = [k, mean, m2 + d * (la[i] - mean)]
        hours[p][hr[i]] += 1
    return out


def build_families(raw: dict) -> dict:
    """{'families': {name: {'X','names'}}, 'y','vert','vert_names','is_test','dt','caps'}."""
    dt, amt, n = raw["dt"], raw["amt"], len(raw["dt"])
    is_test = dt > np.percentile(dt, 100 * (1 - TEST_SHARE))
    train = ~is_test
    h = history_features(raw)
    voc = raw["vocab"]

    # amount cutoffs of the row's own vertical (merchant), from that vertical's training rows only
    high, micro = np.zeros(n), np.zeros(n)
    for code in range(len(voc["prod"])):
        m = raw["prod"] == code
        tr = amt[m & train]
        if len(tr):
            p10, p90 = np.percentile(tr, [10, 90])
            high[m], micro[m] = amt[m] > p90, amt[m] < p10

    d1, d3, a2 = raw["D1"], raw["D3"], raw["addr2"]
    credit = voc["card6"].index("credit") if "credit" in voc["card6"] else -9
    prior_cap = _cap(h["prior_count"], train)  # the live node scales prior_count the same way

    cols = {
        "transaction": [("high_amount", high), ("micro_amount", micro), ("round_1", amt == np.floor(amt)),
                        ("round_10", amt % 10 == 0), ("velocity", h["velocity"])],
        "identity": [("credit", raw["card6"] == credit), ("card_age", days_feature(d1)), ("new_customer", d1 == 0)],
        "geo": [("country_mismatch", np.isnan(a2) | (a2 != HOME_COUNTRY))],
        "behavior": [("night", ((dt // 3600) % 24) < 6), ("hour_unusual", h["hour_unusual"]),
                     ("has_hist", h["has_hist"]), ("amt_dev", h["amt_dev"]),
                     ("days_since_prev", days_feature(d3)), ("d3_missing", np.isnan(d3)),
                     ("prior_count", _log_scale(h["prior_count"], prior_cap))],
    }
    fams = {}
    for name, c in cols.items():
        X = np.column_stack([np.asarray(v, dtype=float) for _, v in c])
        assert [k for k, _ in c] == FEATURES[name], f"{name}: features differ from what the live node computes"
        assert X.min() >= 0 and X.max() <= 1, f"{name} feature out of [0, 1]"
        fams[name] = {"X": X, "names": FEATURES[name]}
    return {"families": fams, "y": raw["y"], "vert": raw["prod"], "vert_names": voc["prod"],
            "is_test": is_test, "dt": dt, "caps": {"prior_count": prior_cap}}
