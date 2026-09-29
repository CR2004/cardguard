"""Every human decision updates the fraud model at once, safely: right direction, no compounding, bounded drift,
never blocks the decision, survives a restart."""
import numpy as np
import pytest

from cardguard.payment_processing import merchant
from cardguard.training import fl
from cardguard.training import retrain as rt
from tests.test_human_review import REVIEWER, client, soft  # noqa: F401 - client is a fixture


@pytest.fixture(scope="module")
def world():
    """Shipped-style weights plus a small replay sample from a synthetic merchant."""
    w = fl.train_federated(rounds=10)[0]
    X, y = fl.make_merchant_data("electronics", n=3000, seed=1)
    return w, X, y


def p(w, x):
    return float(fl.predict_proba(w, np.asarray(x, dtype=float).reshape(1, -1))[0])


def test_no_labels_means_no_change(world):
    w, X, y = world
    assert np.array_equal(rt.instant_update(w, X, y, []), w)


def test_a_decline_raises_and_an_approval_lowers_that_payments_risk(world):
    w, X, y = world
    calm, risky = X[y == 0][0], X[y == 1][0]
    assert p(rt.instant_update(w, X, y, [(list(calm), 1)]), calm) > p(w, calm)     # human says: this was fraud
    assert p(rt.instant_update(w, X, y, [(list(risky), 0)]), risky) < p(w, risky)  # human says: this was fine


def test_one_label_does_not_swing_unrelated_payments(world):
    w, X, y = world
    x = X[y == 0][0]
    w2 = rt.instant_update(w, X, y, [(list(x), 1)])
    assert abs(float(fl.predict_proba(w2, X).mean() - fl.predict_proba(w, X).mean())) < 0.15   # the old fine-tune moved it by ~0.6


def test_update_is_a_pure_function_of_the_labels(world):
    """No compounding, and the order the labels arrived in does not matter."""
    w, X, y = world
    labs = [(list(X[i]), int(y[i])) for i in range(0, 60, 6)]
    a = rt.instant_update(w, X, y, labs)
    assert np.allclose(a, rt.instant_update(w, X, y, labs))
    assert np.allclose(a, rt.instant_update(w, X, y, labs[::-1]))


def test_weights_can_never_drift_past_the_cap(world):
    w, X, y = world
    hostile = [(list(X[y == 0][0]), 1)] * 400          # 400 identical, one-sided labels
    w2 = rt.instant_update(w, X, y, hostile)
    assert np.isfinite(w2).all() and np.abs(w2 - w).max() <= rt.MAX_DRIFT + 1e-9


def test_update_is_fast_enough_to_run_inside_the_request(world):
    import time
    w, X, y = world
    t = time.time()
    rt.instant_update(w, X, y, [(list(X[0]), 1)] * 50)
    assert time.time() - t < 2.0


# ---------------- through the merchant node ----------------

@pytest.fixture
def learner(client, monkeypatch, world):  # noqa: F811
    w, X, y = world
    monkeypatch.setattr(merchant, "_replay", lambda: (X, y))
    monkeypatch.setattr(merchant, "INSTANT_LEARNING", True)
    base = w.copy()  # trained on the same world as the replay, so replay alone does not move the payment under test
    monkeypatch.setattr(merchant, "BASE_WEIGHTS", base.copy())
    monkeypatch.setattr(merchant, "GLOBAL_WEIGHTS", base.copy())
    monkeypatch.setattr(merchant, "FL_WEIGHTS", base.copy())
    return client, base


def test_a_human_decline_updates_the_model_immediately(learner, monkeypatch):
    c, base = learner
    rid = soft(monkeypatch, c)["review_id"]
    x = merchant.pending[rid]["features"]
    out = c.post(f"/reviews/{rid}/decline", headers=REVIEWER, json={"reason": "card_testing"}).get_json()
    assert out["learned"]["learning"] == "instant" and out["learned"]["labels"] == 1
    assert out["learned"]["model_band_before"] in {"low", "medium", "high"}
    assert p(merchant.FL_WEIGHTS, x) > p(base, x)          # the very next checkout already sees it


def test_a_human_approval_updates_the_model_immediately(learner, monkeypatch):
    c, base = learner
    rid = soft(monkeypatch, c)["review_id"]
    x = merchant.pending[rid]["features"]
    out = c.post(f"/reviews/{rid}/approve", headers=REVIEWER, json={"reason": "known_customer"}).get_json()
    assert out["learned"]["learning"] == "instant"
    assert p(merchant.FL_WEIGHTS, x) < p(base, x)


def test_a_chargeback_teaches_the_model_too(learner, monkeypatch):
    c, base = learner
    rid = soft(monkeypatch, c)["review_id"]
    code = c.post(f"/reviews/{rid}/approve", headers=REVIEWER).get_json()["payment"]["auth_code"]   # a human let it through
    x, before = merchant.payments[code]["features"], merchant.FL_WEIGHTS.copy()
    out = c.post(f"/payments/{code}/dispute", headers=REVIEWER).get_json()                       # ... and it was fraud
    assert out["learned"]["learning"] == "instant" and out["labels"] == 2
    assert p(merchant.FL_WEIGHTS, x) > p(before, x)


def test_learning_can_be_switched_off(learner, monkeypatch):
    c, base = learner
    monkeypatch.setattr(merchant, "INSTANT_LEARNING", False)
    rid = soft(monkeypatch, c)["review_id"]
    out = c.post(f"/reviews/{rid}/decline", headers=REVIEWER).get_json()
    assert out["learned"]["learning"] == "off" and np.array_equal(merchant.FL_WEIGHTS, base) and out["labels"] == 1


def test_a_failure_to_learn_never_blocks_the_human_decision(learner, monkeypatch):
    c, base = learner
    rid = soft(monkeypatch, c)["review_id"]

    def boom(*a, **k):
        raise RuntimeError("no learning today")

    monkeypatch.setattr(merchant.rt, "instant_update", boom)
    res = c.post(f"/reviews/{rid}/decline", headers=REVIEWER)
    out = res.get_json()
    assert res.status_code == 200 and out["outcome"] == "declined_by_human" and out["payment"]["status"] == "voided"
    assert out["learned"]["learning"] == "failed" and np.array_equal(merchant.FL_WEIGHTS, base)   # kept the last good weights
    assert len(merchant.label_store.load()) == 1                                                  # the label itself was still saved


def test_a_restart_relearns_from_the_saved_labels(learner, monkeypatch):
    c, base = learner
    rid = soft(monkeypatch, c)["review_id"]
    c.post(f"/reviews/{rid}/decline", headers=REVIEWER)
    learned = merchant.FL_WEIGHTS.copy()
    monkeypatch.setattr(merchant, "FL_WEIGHTS", base.copy())                     # what a restart starts from
    monkeypatch.setattr(merchant, "labels", merchant.label_store.load())         # ... and the labels it reloads from disk
    merchant.learn_from_labels()
    assert np.allclose(merchant.FL_WEIGHTS, learned)


def test_hard_declines_never_reach_a_human_so_never_teach_the_model(learner):
    c, base = learner
    from tests.test_merchant import CVC_FAIL, buy
    assert buy(c, card=CVC_FAIL)["outcome"] == "declined"
    assert merchant.labels == [] and np.array_equal(merchant.FL_WEIGHTS, base)


def test_a_manual_retrain_becomes_the_new_starting_point(learner, monkeypatch):
    from tests.test_retrain import _registry_small
    c, base = learner
    monkeypatch.setattr(merchant, "registry", _registry_small())
    monkeypatch.setattr(merchant, "VERTICAL", "electronics")
    rid = soft(monkeypatch, c)["review_id"]
    c.post(f"/reviews/{rid}/decline", headers=REVIEWER)
    c.post("/agent/retrain", headers=REVIEWER)
    assert not np.allclose(merchant.GLOBAL_WEIGHTS, base)      # the federated round moved the shared weights
    rid2 = soft(monkeypatch, c)["review_id"]
    c.post(f"/reviews/{rid2}/decline", headers=REVIEWER)       # the next label starts from them, not from the shipped file
    assert np.allclose(merchant.FL_WEIGHTS, rt.instant_update(merchant.GLOBAL_WEIGHTS, *merchant._replay(), merchant.labels,
                                                              label_share=merchant.INSTANT_LABEL_SHARE))
