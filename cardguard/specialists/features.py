"""Seven signal families from a raw dict (see data.py). Every feature is in [0, 1].

  transaction  amount rank within its vertical, round amounts, C1-C14 counts, 24h velocity
  identity     payer/recipient email, M1-M9 match flags, card age, new customer, credit
  device       identity-file flags. covered=False (no identity row) means the specialist ABSTAINS
  geo          country mismatch, address rarity, dist1/dist2, address change since the card's last seen
  behavior     deviation from this card's own history: amount, hour, new product, gap since previous
  merchant     ProductCD and recipient-email rarity (thin: IEEE-CIS has no MCC)
  network      distinct cards sharing an address+email / email / recipient email; emails per card

History features are computed in time order and only look at PRIOR rows (no lookahead). Statistics
that need a scale (percentiles, rarity counts, caps) come from the training period only.
"""
from __future__ import annotations

import collections
import math

import numpy as np

from cardguard.data.ieee_cis import days_feature
from cardguard.specialists.data import D_EXTRA, IDENT_NAMES

FAMILIES = ["transaction", "identity", "device", "geo", "behavior", "merchant", "network"]
TEST_SHARE = 0.20  # same time holdout as cardguard.data.ieee_cis
HOME_COUNTRY = 87.0
D_EXTRA_NUMS = [c[1:] for c in D_EXTRA]
HIST = ["velocity", "has_hist", "amt_dev", "hour_unusual", "new_product", "prior_count",
        "loc_known", "loc_shift", "emails_per_card", "cards_per_pair", "cards_per_email",
        "cards_per_remail"]


def _intern(keys) -> np.ndarray:
    seen: dict = {}
    return np.array([seen.setdefault(k, len(seen)) for k in keys], dtype=np.int64)


def _cap(x: np.ndarray, train: np.ndarray) -> float:
    v = x[train]
    v = v[np.isfinite(v)]
    return max(float(np.percentile(v, 99)), 1.0) if len(v) else 1.0


def _log_scale(x: np.ndarray, cap: float) -> np.ndarray:
    return np.minimum(np.log1p(np.maximum(np.nan_to_num(x, nan=0.0), 0.0)) / np.log1p(cap), 1.0)


def _rarity(codes: np.ndarray, train: np.ndarray) -> np.ndarray:
    """0 for the most common value, towards 1 for rare ones; missing -> 0 (see the *_missing flag)."""
    valid = codes >= 0
    if not (valid & train).any():
        return np.zeros(len(codes))
    cnt = np.bincount(codes[valid & train], minlength=int(codes.max()) + 1)
    c = np.where(valid, cnt[np.maximum(codes, 0)], 0)
    return np.where(valid, 1 - np.log1p(c) / np.log1p(max(int(cnt.max()), 1)), 0.0)


def history_features(raw: dict) -> dict:
    """Unscaled per-row history, computed in time order over PRIOR rows only."""
    dt, n = raw["dt"], len(raw["dt"])
    card = np.nan_to_num(raw["card"], nan=-1).astype(np.int64)
    a1 = np.nan_to_num(raw["addr1"], nan=-1).astype(np.int64).tolist()
    pe, re_, prod = raw["pemail"].tolist(), raw["remail"].tolist(), raw["prod"].tolist()
    ck = _intern(map(tuple, card.tolist())).tolist()  # card key: card1,2,3,5
    pk = _intern(zip(ck, a1, pe)).tolist()            # card proxy: + addr1 + payer email
    hr = ((dt // 3600) % 24).astype(np.int64).tolist()
    la = np.log1p(np.maximum(raw["amt"], 0)).tolist()
    dtl = dt.tolist()
    out = {k: np.zeros(n) for k in HIST}

    recent = collections.defaultdict(collections.deque)
    amt_stats, hours, prods, last_a1 = {}, {}, {}, {}
    emails = collections.defaultdict(set)
    pairs, by_email, by_remail = (collections.defaultdict(set) for _ in range(3))

    for i in np.argsort(dt, kind="stable").tolist():
        p, c, t = pk[i], ck[i], dtl[i]
        q = recent[p]
        while q and t - q[0] > 86400:
            q.popleft()
        out["velocity"][i] = min(len(q), 10) / 10
        q.append(t)

        st = amt_stats.get(p)
        if st is None:
            amt_stats[p], hours[p], prods[p] = [1, la[i], 0.0], [0] * 24, {prod[i]}
        else:
            k, mean, m2 = st
            out["prior_count"][i] = k
            if k >= 2:
                out["has_hist"][i] = 1
                out["amt_dev"][i] = min(abs(la[i] - mean) / max(math.sqrt(m2 / k), 0.25) / 4, 1.0)
            if k >= 3:
                out["hour_unusual"][i] = float(sum(hours[p][(hr[i] + d) % 24] for d in range(-3, 4)) == 0)
            out["new_product"][i] = float(prod[i] not in prods[p])
            k += 1
            d = la[i] - mean
            mean += d / k
            st[:] = [k, mean, m2 + d * (la[i] - mean)]
            prods[p].add(prod[i])
        hours[p][hr[i]] += 1

        prev = last_a1.get(c)
        if prev is not None and a1[i] >= 0:
            out["loc_known"][i], out["loc_shift"][i] = 1.0, float(prev != a1[i])
        if a1[i] >= 0:
            last_a1[c] = a1[i]

        if pe[i] >= 0:
            s = emails[c]
            out["emails_per_card"][i] = len(s) - (pe[i] in s)
            s.add(pe[i])
            s = by_email[pe[i]]
            out["cards_per_email"][i] = len(s) - (c in s)
            s.add(c)
            if a1[i] >= 0:
                s = pairs[(a1[i], pe[i])]
                out["cards_per_pair"][i] = len(s) - (c in s)
                s.add(c)
        if re_[i] >= 0:
            s = by_remail[re_[i]]
            out["cards_per_remail"][i] = len(s) - (c in s)
            s.add(c)
    return out


def build_families(raw: dict) -> dict:
    """{'families': {name: {'X','names','covered'}}, 'y','vert','is_test','dt'}."""
    dt, amt, n = raw["dt"], raw["amt"], len(raw["dt"])
    is_test = dt > np.percentile(dt, 100 * (1 - TEST_SHARE))
    train = ~is_test
    h = history_features(raw)
    voc = raw["vocab"]

    # amount relative to the row's own vertical, cuts from that vertical's training rows only
    pct, high, micro = np.zeros(n), np.zeros(n), np.zeros(n)
    for code in range(len(voc["prod"])):
        m = raw["prod"] == code
        tr = np.sort(amt[m & train])
        if len(tr):
            pct[m] = np.searchsorted(tr, amt[m], side="right") / len(tr)
            p10, p90 = np.percentile(tr, [10, 90])
            high[m], micro[m] = amt[m] > p90, amt[m] < p10

    def cnt(name):  # distinct-entity counts, log-scaled by their own training 99th percentile
        return _log_scale(h[name], _cap(h[name], train))

    d1, d3 = raw["D1"], raw["D3"]
    a1 = np.nan_to_num(raw["addr1"], nan=-1).astype(np.int64)
    pe, re_ = raw["pemail"], raw["remail"]
    p_index = {s: i for i, s in enumerate(voc["pemail"])}
    re_as_p = np.array([p_index.get(s, -2) for s in voc["remail"]] + [-3])
    match = (pe >= 0) & (np.where(re_ >= 0, re_as_p[re_], -3) == pe)
    credit = voc["card6"].index("credit") if "credit" in voc["card6"] else -9

    fams: dict = {}

    def add(name, cols, covered=None):
        names = [c for c, _ in cols]
        X = np.column_stack([np.asarray(v, dtype=float) for _, v in cols])
        assert X.min() >= 0 and X.max() <= 1, f"{name} feature out of [0, 1]"
        fams[name] = {"X": X, "names": names,
                      "covered": np.ones(n, dtype=bool) if covered is None else covered}

    add("transaction", [("amt_pct", pct), ("high_amount", high), ("micro_amount", micro),
                        ("round_1", amt == np.floor(amt)), ("round_10", amt % 10 == 0),
                        ("subcent", np.abs(amt * 100 - np.round(amt * 100)) > 1e-6),  # currency-converted
                        *[(f"C{j + 1}", _log_scale(raw["C"][:, j], _cap(raw["C"][:, j], train)))
                          for j in range(raw["C"].shape[1])],
                        ("velocity", h["velocity"])])

    m_cols = []
    for j, words in enumerate(voc["M"]):
        for v in range(min(len(words), 3)):
            m_cols.append((f"M{j + 1}={words[v]}", raw["M"][:, j] == v))
    add("identity", [("p_missing", pe < 0), ("r_missing", re_ < 0), ("email_match", match),
                     ("p_rarity", _rarity(pe, train)), ("credit", raw["card6"] == credit),
                     ("card_age", days_feature(d1)), ("new_customer", d1 == 0),
                     ("d1_missing", np.isnan(d1)), *m_cols,
                     *[(f"card4={w}", raw["card4"] == j) for j, w in enumerate(voc["card4"][:4])]])

    has = raw["has_identity"]
    add("device", [("has_identity", has), *[(nm, raw["ident"][:, j]) for j, nm in enumerate(IDENT_NAMES)]],
        covered=has)

    a2 = raw["addr2"]
    add("geo", [("country_mismatch", np.isnan(a2) | (a2 != HOME_COUNTRY)), ("addr2_missing", np.isnan(a2)),
                ("addr1_missing", a1 < 0), ("addr1_rarity", _rarity(a1, train)),
                ("dist1", _log_scale(raw["dist1"], _cap(raw["dist1"], train))),
                ("dist1_missing", np.isnan(raw["dist1"])),
                ("dist2", _log_scale(raw["dist2"], _cap(raw["dist2"], train))),
                ("dist2_missing", np.isnan(raw["dist2"])),
                ("loc_shift", h["loc_shift"]), ("loc_known", h["loc_known"])])

    add("behavior", [("night", ((dt // 3600) % 24) < 6), ("hour_unusual", h["hour_unusual"]),
                     ("has_hist", h["has_hist"]), ("amt_dev", h["amt_dev"]),
                     ("new_product", h["new_product"]), ("days_since_prev", days_feature(d3)),
                     ("d3_missing", np.isnan(d3)), ("prior_count", cnt("prior_count")),
                     *[(f"D{k}", days_feature(raw["D"][:, j])) for j, k in enumerate(D_EXTRA_NUMS)],
                     *[(f"D{k}_missing", np.isnan(raw["D"][:, j])) for j, k in enumerate(D_EXTRA_NUMS)]])

    add("merchant", [*[(f"product={w}", raw["prod"] == j) for j, w in enumerate(voc["prod"])],
                     ("r_missing", re_ < 0), ("r_rarity", _rarity(re_, train))])

    add("network", [(k, cnt(k)) for k in ("emails_per_card", "cards_per_pair",
                                          "cards_per_email", "cards_per_remail")])

    return {"families": fams, "y": raw["y"], "vert": raw["prod"], "vert_names": voc["prod"],
            "is_test": is_test, "dt": dt}


# Features a live merchant node could compute at checkout from what it already holds (its own
# per-card history, the amount, the buyer country, the clock, its own vertical). Everything else in
# the full set depends on columns Vesta engineered for the dataset (C, D, M, email, addr/dist, device
# recognition) that this checkout does not collect. An entry ending in "=" matches by prefix.
DEPLOYABLE = {
    "transaction": ["high_amount", "micro_amount", "round_1", "round_10", "subcent", "velocity"],
    "identity": ["credit", "card_age", "new_customer"],
    "geo": ["country_mismatch"],
    "behavior": ["night", "hour_unusual", "has_hist", "amt_dev", "new_product", "days_since_prev",
                 "d3_missing", "prior_count"],
    "merchant": ["product="],
}


def _allowed(name: str, allow: list[str]) -> bool:
    return any(name == a or (a.endswith("=") and name.startswith(a)) for a in allow)


def restrict(fam: dict, allow: dict = DEPLOYABLE) -> dict:
    """The same data with only the allowed columns; a family left with none is dropped."""
    kept = {}
    for name, f in fam["families"].items():
        idx = [j for j, nm in enumerate(f["names"]) if _allowed(nm, allow.get(name, []))]
        if idx:
            kept[name] = {"X": f["X"][:, idx], "names": [f["names"][j] for j in idx], "covered": f["covered"]}
    return {**fam, "families": kept}
