"""Live scoring of the specialists at checkout. numpy only; needs no dataset.

specialist_weights.json is written offline by `python -m cardguard.specialists.export`. A merchant node loads
the weights for its own vertical (FedAvg across merchants, then a few local epochs) and scores each checkout
with four small models, each seeing only its own signal family. Their bands are stacked into ONE banded fact,
`specialist_stack_band`, which is the only thing that crosses the wire. The per-specialist bands stay in the
node's local audit note. If the file is missing or does not validate, `load` returns None and the node decides
exactly as before (invariant 5: models degrade, they never block).

Every feature below is computed the way features.py computed it for training (a parity test checks the
history features against features.history_features on the same sequence of transactions).
"""
from __future__ import annotations

import collections
import json
import math
import os
import sys

import numpy as np

from cardguard import ROOT
from cardguard.data.ieee_cis import days_feature
from cardguard.specialists.model import BAND_NAMES, sigmoid, to_bands

WEIGHTS_FILE = ROOT / "specialist_weights.json"
STACK_ORDER = ("transaction", "identity", "geo", "behavior")
FEATURES = {
    "transaction": ["high_amount", "micro_amount", "round_1", "round_10", "velocity"],
    "identity": ["credit", "card_age", "new_customer"],
    "geo": ["country_mismatch"],
    "behavior": ["night", "hour_unusual", "has_hist", "amt_dev", "days_since_prev", "d3_missing", "prior_count"],
}


class CardHistory:
    """Per-card amount and hour history, prior rows only: read with features(), then record() the purchase.

    Mirrors features.history_features: log-amount mean and spread (Welford), an hour histogram."""

    def __init__(self, max_cards: int = 20000):
        self.cards: collections.OrderedDict = collections.OrderedDict()
        self.max_cards = max_cards

    def features(self, key: str, dollars: float, hour: int) -> dict:
        out = {"prior": 0, "has_hist": 0.0, "amt_dev": 0.0, "hour_unusual": 0.0}
        st = self.cards.get(key)
        if st is None:
            return out
        k, mean, m2, hours = st
        out["prior"] = k
        if k >= 2:
            out["has_hist"] = 1.0
            out["amt_dev"] = min(abs(math.log1p(dollars) - mean) / max(math.sqrt(m2 / k), 0.25) / 4, 1.0)
        if k >= 3:
            out["hour_unusual"] = float(sum(hours[(hour + d) % 24] for d in range(-3, 4)) == 0)
        return out

    def record(self, key: str, dollars: float, hour: int) -> None:
        x = math.log1p(dollars)
        st = self.cards.get(key)
        if st is None:
            st = self.cards[key] = [1, x, 0.0, [0] * 24]
        else:
            k, mean, m2, _ = st
            k += 1
            d = x - mean
            mean += d / k
            st[0], st[1], st[2] = k, mean, m2 + d * (x - mean)
        st[3][hour] += 1
        self.cards.move_to_end(key)
        while len(self.cards) > self.max_cards:
            self.cards.popitem(last=False)


def build_features(*, amount_cents: int, cuts: tuple, recent_purchases: int, first_time: bool,
                   card_age_days: float, days_since_prev: float, had_prev: bool, hour: int, funding: str,
                   country_mismatch: bool, hist: dict, prior_cap: float) -> dict:
    """The live value of every feature in FEATURES, in the units the model was trained on."""
    p10, _, p90 = cuts
    dollars = amount_cents / 100
    return {
        "high_amount": float(dollars > p90), "micro_amount": float(dollars < p10),
        "round_1": float(amount_cents % 100 == 0), "round_10": float(amount_cents % 1000 == 0),
        "velocity": min(recent_purchases, 10) / 10,
        "credit": float(funding == "credit"), "card_age": float(days_feature(card_age_days)),
        "new_customer": float(first_time),
        "country_mismatch": float(country_mismatch),
        "night": float(hour < 6), "hour_unusual": hist["hour_unusual"], "has_hist": hist["has_hist"],
        "amt_dev": hist["amt_dev"], "days_since_prev": float(days_feature(days_since_prev)),
        "d3_missing": float(not had_prev),
        "prior_count": min(math.log1p(hist["prior"]) / math.log1p(max(prior_cap, 1.0)), 1.0),
    }


class Specialists:
    def __init__(self, spec: dict, vertical: str):
        if spec.get("version") != 1 or tuple(spec["stack"]["order"]) != STACK_ORDER:
            raise ValueError("unsupported specialist file")
        self.vertical, self.mode = vertical, spec.get("mode")
        self.prior_cap = float(spec["caps"]["prior_count"])
        self.models = {}
        for name in STACK_ORDER:
            m = spec["specialists"][name]
            if m["features"] != FEATURES[name]:
                raise ValueError(f"{name}: trained on different features than this node computes")
            v = m["verticals"].get(vertical) or m["global"]
            w = np.array(v["weights"], dtype=float)
            if len(w) != len(FEATURES[name]) + 1 or not np.isfinite(w).all():
                raise ValueError(f"{name}: bad weights")
            self.models[name] = (w, tuple(v["cuts"]))
        st = spec["stack"]
        self.stack_w = np.array(st["weights"], dtype=float)
        if len(self.stack_w) != 2 * len(STACK_ORDER) + 1 or not np.isfinite(self.stack_w).all():
            raise ValueError("bad stack weights")
        self.stack_cuts = tuple((st["verticals"].get(vertical) or st["global"])["cuts"])

    def score(self, feats: dict) -> dict:
        bands, z = {}, []
        for name, (w, cuts) in self.models.items():
            x = np.array([feats[f] for f in FEATURES[name]], dtype=float)
            b = int(to_bands(np.array([float(sigmoid(x @ w[1:] + w[0]))]), cuts)[0])
            bands[name] = BAND_NAMES[b]
            z += [float(b == 1), float(b == 2)]
        q = float(sigmoid(np.array(z) @ self.stack_w[1:] + self.stack_w[0]))
        return {"bands": bands, "stack_score": q,
                "stack_band": BAND_NAMES[int(to_bands(np.array([q]), self.stack_cuts)[0])]}


def load(vertical: str, path=None) -> Specialists | None:
    """Specialists for this vertical, or None (missing file, disabled, or does not validate)."""
    path = path if path is not None else os.environ.get("SPECIALIST_WEIGHTS", str(WEIGHTS_FILE))
    if path in {"", "none"} or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return Specialists(json.load(f), vertical)
    except (OSError, ValueError, KeyError, TypeError) as e:  # noqa: PERF203 - one-time startup
        print(f"specialists disabled: {path} did not validate ({type(e).__name__}: {e})", file=sys.stderr)
        return None
