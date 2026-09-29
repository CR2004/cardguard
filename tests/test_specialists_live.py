"""Specialists wired into the live merchant node: training/serving parity, safe loading, the wire, the verdict."""
import json

import numpy as np
import pytest

from cardguard.decision.coordinator import rules
from cardguard.decision.guard import WIRE_SCHEMA, WireViolation, strip_for_wire
from cardguard.payment_processing import merchant
from cardguard.specialists import live
from cardguard.specialists.features import DEPLOYABLE, build_families, history_features
from cardguard.specialists.live import FEATURES, STACK_ORDER, CardHistory, Specialists, build_features
from tests.test_merchant import MASTER_DE, VISA, buy, client  # noqa: F401 - client is a fixture


def tiny_spec(bias=0.0, stack_bias=0.0, cuts=(0.3, 0.6)):
    """A valid specialist file with hand-set weights: every model sees its features with weight +2."""
    specs = {}
    for name in STACK_ORDER:
        w = [bias] + [2.0] * len(FEATURES[name])
        v = {"weights": w, "cuts": list(cuts)}
        specs[name] = {"features": FEATURES[name], "global": v, "verticals": {"W": v}}
    return {"version": 1, "mode": "personalised", "caps": {"prior_count": 20.0}, "specialists": specs,
            "stack": {"order": list(STACK_ORDER), "weights": [stack_bias] + [1.0] * (2 * len(STACK_ORDER)),
                      "global": {"cuts": [0.3, 0.6]}, "verticals": {"W": {"cuts": [0.3, 0.6]}}}}


def test_live_history_matches_training_history_exactly():
    """The same sequence of purchases gives the same behavior features in training and at the checkout."""
    rng = np.random.default_rng(3)
    n, n_cards = 500, 12
    cid = rng.integers(0, n_cards, n)
    dt = np.sort(rng.uniform(0, 30 * 86400, n))
    amt = np.round(np.exp(rng.normal(3.5, 1.0, n)), 2)
    raw = {"dt": dt, "amt": amt, "prod": np.zeros(n, dtype=int), "remail": np.full(n, -1),
           "card": np.c_[cid, np.full(n, 1.0), np.full(n, 2.0), np.full(n, 3.0)].astype(float),
           "addr1": (100 + cid).astype(float), "pemail": (cid % 4).astype(int)}
    train = history_features(raw)
    hist, hour = CardHistory(), ((dt // 3600) % 24).astype(int)
    for i in range(n):
        f = hist.features(str(cid[i]), amt[i], int(hour[i]))
        assert f["prior"] == train["prior_count"][i]
        assert f["has_hist"] == train["has_hist"][i] and f["hour_unusual"] == train["hour_unusual"][i]
        assert f["amt_dev"] == pytest.approx(train["amt_dev"][i])
        hist.record(str(cid[i]), amt[i], int(hour[i]))


def test_card_history_is_bounded_and_forgets_oldest():
    h = CardHistory(max_cards=3)
    for k in "abcd":
        h.record(k, 10.0, 12)
    assert list(h.cards) == ["b", "c", "d"]


def test_build_features_covers_every_model_input_and_matches_definitions():
    hist = {"prior": 0, "has_hist": 0.0, "amt_dev": 0.0, "hour_unusual": 0.0}
    f = build_features(amount_cents=90000, cuts=(10.0, 50.0, 300.0), recent_purchases=25, first_time=True,
                       card_age_days=0.0, days_since_prev=0.0, had_prev=False, hour=3, funding="credit",
                       country_mismatch=True, hist=hist, prior_cap=20.0)
    assert set(f) >= {n for names in FEATURES.values() for n in names}
    assert f["high_amount"] == 1 and f["micro_amount"] == 0 and f["round_1"] == 1 and f["round_10"] == 1
    assert f["velocity"] == 1.0 and f["night"] == 1 and f["d3_missing"] == 1 and f["credit"] == 1
    assert all(0 <= v <= 1 for v in f.values())
    odd = build_features(amount_cents=1234, cuts=(10.0, 50.0, 300.0), recent_purchases=0, first_time=False,
                         card_age_days=30.0, days_since_prev=2.0, had_prev=True, hour=14, funding="debit",
                         country_mismatch=False, hist={**hist, "prior": 5}, prior_cap=20.0)
    assert odd["round_1"] == 0 and odd["round_10"] == 0 and odd["d3_missing"] == 0 and 0 < odd["prior_count"] < 1


def test_live_features_are_exactly_the_training_features():
    """DEPLOYABLE (what training may use) and FEATURES (what the node computes) must never drift apart."""
    assert {k: list(v) for k, v in DEPLOYABLE.items()} == {k: list(v) for k, v in FEATURES.items()}


def test_missing_disabled_or_broken_file_means_no_specialists(tmp_path, monkeypatch, capsys):
    assert live.load("W", path=str(tmp_path / "nope.json")) is None
    assert live.load("W", path="none") is None
    bad = tiny_spec()
    bad["specialists"]["geo"]["features"] = ["something_else"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad))
    assert live.load("W", path=str(p)) is None and "did not validate" in capsys.readouterr().err
    nan = tiny_spec()
    nan["specialists"]["geo"]["global"]["weights"] = [None, 1.0]
    p.write_text(json.dumps(nan))
    assert live.load("W", path=str(p)) is None
    p.write_text("{not json")
    assert live.load("W", path=str(p)) is None


def test_scoring_uses_the_merchants_own_weights_and_returns_closed_bands():
    spec = tiny_spec(bias=-1.0)  # p = 0.27 with no risk feature (low), 0.73 with one (high)
    spec["specialists"]["geo"]["verticals"]["C"] = {"weights": [-5.0, 5.0], "cuts": [0.3, 0.6]}  # p = 0.5 (medium)
    calm = {n: 0.0 for names in FEATURES.values() for n in names}
    risky = {**calm, "high_amount": 1.0, "country_mismatch": 1.0, "amt_dev": 1.0, "new_customer": 1.0}
    w, c = Specialists(spec, "W"), Specialists(spec, "C")
    assert set(w.score(risky)["bands"]) == set(STACK_ORDER)
    assert set(w.score(risky)["bands"].values()) <= {"low", "medium", "high"}
    assert w.score(risky)["bands"]["geo"] == "high" and c.score(risky)["bands"]["geo"] == "medium"  # own weights
    assert w.score(calm)["bands"] == {n: "low" for n in STACK_ORDER}
    assert w.score(risky)["stack_score"] > w.score(calm)["stack_score"]
    assert w.score(risky)["stack_band"] in {"low", "medium", "high"}
    assert Specialists(spec, "Z").score(risky)["bands"]["geo"] == "high"  # unknown vertical: global weights


def test_the_new_wire_fact_is_a_closed_vocabulary_and_moves_the_rules_score():
    assert WIRE_SCHEMA["specialist_stack_band"] == {"low", "medium", "high"}
    strip_for_wire({"token": "tok_abcdefghijklmnop", "specialist_stack_band": "high"})
    with pytest.raises(WireViolation):
        strip_for_wire({"token": "tok_abcdefghijklmnop", "specialist_stack_band": "extreme"})
    base = {"amount_band": "low", "country_mismatch": "no", "card_funding": "debit", "cvc_check": "pass",
            "velocity_band": "low", "new_customer": "no", "model_risk_band": "low"}
    assert rules(base)["score"] == 0
    hi = rules({**base, "specialist_stack_band": "high"})
    assert hi["score"] == 2 and "specialist_stack_band=high" in hi["cites"]


@pytest.fixture
def with_specialists(client, monkeypatch):  # noqa: F811
    merchant.card_history.cards.clear()

    def enable(spec):
        monkeypatch.setattr(merchant, "SPECIALISTS", Specialists(spec, merchant.VERTICAL))
    return enable


def test_checkout_puts_one_band_on_the_wire_and_the_four_in_the_local_note(client, with_specialists):
    with_specialists(tiny_spec(bias=3.0, stack_bias=3.0))  # everything scores high
    buy(client, card=MASTER_DE, amount=90000)
    entry = merchant.ledger.entries[-1]
    assert entry["fields"]["specialist_stack_band"] == "high"
    assert not any(k.startswith("specialist_") and k != "specialist_stack_band" for k in entry["fields"])
    assert {entry["note"][f"specialist_{n}"] for n in STACK_ORDER} <= {"low", "medium", "high"}
    assert set(entry["fields"]) <= set(WIRE_SCHEMA)


def test_without_specialists_the_node_sends_no_specialist_fact(client, monkeypatch):
    monkeypatch.setattr(merchant, "SPECIALISTS", None)
    buy(client)
    assert "specialist_stack_band" not in merchant.ledger.entries[-1]["fields"]


def test_a_high_specialist_band_is_cited_in_the_verdict(client, with_specialists):
    with_specialists(tiny_spec(bias=3.0, stack_bias=3.0))
    res = buy(client, card=MASTER_DE, amount=90000)
    assert "specialist_stack_band=high" in res["verdict"]["cites"]


def test_checkout_records_card_history_after_scoring(client, with_specialists):
    with_specialists(tiny_spec())
    buy(client, card=VISA, amount=2000)
    buy(client, card=VISA, amount=2500)
    (state,) = merchant.card_history.cards.values()
    assert state[0] == 2 and sum(state[3]) == 2


def test_export_trains_a_file_the_node_accepts(tmp_path):
    from cardguard.specialists import data as sdata
    from cardguard.specialists.export import train_live
    fam = build_families(sdata.synthetic_raw(n=6000, seed=2))
    for mode in ("personalised", "federated"):
        spec = train_live(fam, mode=mode, rounds=6, local_epochs=2, epochs=80)
        p = tmp_path / f"{mode}.json"
        p.write_text(json.dumps(spec))
        for v in fam["vert_names"]:
            assert live.load(v, path=str(p)) is not None
        assert spec["specialists"]["behavior"]["features"] == FEATURES["behavior"]
        assert 0.4 < spec["metrics"]["test_auc_stack_score"] <= 1.0
