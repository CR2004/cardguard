"""Coordinator + federated model tests. Jev is faked, so these run offline."""
from types import SimpleNamespace as NS

import numpy as np
import pytest

import fl
from coordinator import _jev_state, decide, rules

LOW = {"token": "tok_abcdefghijklmnop", "amount_band": "low", "country_mismatch": "no",
       "card_funding": "credit", "cvc_check": "pass", "velocity_band": "low",
       "new_customer": "no", "model_risk_band": "low"}


class FakeJev:
    def __init__(self, action="approve", confidence=0.95, fail=False, answers=None):
        self.action, self.confidence, self.fail, self.seen_state = action, confidence, fail, None
        self.answers = list(answers or [])  # optional per-call (action, confidence) script
        self.calls = 0

    def system_one(self, state, questions, **kw):
        self.calls += 1
        if self.fail:
            raise TimeoutError("jev down")
        if self.answers:
            self.action, self.confidence = self.answers.pop(0)
        self.seen_state = state
        assert set(questions) == {"action", "fraud_risk", "card_testing"}
        return NS(model="jev-fake", answers={
            "action": NS(choice=self.action, confidence=self.confidence,
                         probabilities={"approve": .1, "step_up": .1, "decline": .1}),
            "fraud_risk": NS(score=0.4), "card_testing": NS(noul=0.05)})


def test_demo_calibration():
    """Normal approves, risky preset reaches a human, card testing auto-declines."""
    assert rules({**LOW, "new_customer": "yes"})["decision"] == "approve"
    risky = {**LOW, "amount_band": "high", "country_mismatch": "yes", "new_customer": "yes",
             "model_risk_band": "high"}
    assert rules(risky)["decision"] == "step_up"
    testing = {**LOW, "cvc_check": "fail", "velocity_band": "high", "new_customer": "yes",
               "model_risk_band": "high"}
    assert rules(testing)["decision"] == "decline"


def test_cvc_fail_is_a_hard_decline_even_when_everything_else_is_clean():
    """A failed security-code check declines on its own; Jev is not consulted."""
    jev = FakeJev("approve", .99)
    v = decide({**LOW, "cvc_check": "fail"}, client=jev)
    assert v["decision"] == "decline" and v["cites"] == ["cvc_check=fail"]
    assert v["decided_by"] == "rules" and jev.seen_state is None


def test_cvc_unavailable_is_not_treated_as_a_pass():
    assert rules({**LOW, "cvc_check": "unavailable"})["score"] > rules(LOW)["score"]
    assert "cvc_check=unavailable" in rules({**LOW, "cvc_check": "unavailable"})["cites"]


def test_malformed_jev_answer_is_retried_once_then_used():
    jev = FakeJev(answers=[("maybe", .9), ("approve", .95)])
    v = decide(LOW, client=jev)
    assert jev.calls == 2 and v["decision"] == "approve" and v["decided_by"] == "rules+jev"
    assert v["jev_retries"] == ["MalformedAnswer"]


def test_malformed_jev_twice_falls_back_to_rules_and_never_500s():
    jev = FakeJev(answers=[("approve", 1.5), ("nonsense", .9)])
    v = decide(LOW, client=jev)
    assert jev.calls == 2 and v["decided_by"] == "rules" and v["decision"] == "approve"
    assert v["jev_error"] == "MalformedAnswer"
    assert v["jev_attempts"] == ["MalformedAnswer", "MalformedAnswer"]


def test_rules_only_without_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert decide(LOW)["decided_by"] == "rules"


def test_both_approve_confidently():
    v = decide(LOW, client=FakeJev("approve", .95))
    assert v["decision"] == "approve" and v["decided_by"] == "rules+jev"


def test_more_cautious_vote_wins():
    assert decide(LOW, client=FakeJev("decline"))["decision"] == "decline"
    risky = {**LOW, "amount_band": "high", "country_mismatch": "yes"}
    assert rules(risky)["decision"] == "step_up"
    assert decide(risky, client=FakeJev("approve"))["decision"] == "step_up"


def test_low_confidence_approve_goes_to_human():
    v = decide(LOW, client=FakeJev("approve", .55))
    assert v["decision"] == "step_up" and v["confidence_gated"]


def test_jev_failure_falls_back_to_rules():
    v = decide(LOW, client=FakeJev(fail=True))
    assert v["decided_by"] == "rules" and v["jev_error"] == "TimeoutError"


def test_jev_never_sees_token():
    jev = FakeJev()
    decide(LOW, client=jev)
    assert "tok_" not in str(jev.seen_state)
    assert "token" not in jev.seen_state["transaction_facts"]
    assert _jev_state(LOW)["fact_meanings"].keys() == jev.seen_state["transaction_facts"].keys()


def test_federated_beats_every_single_merchant():
    w_fed, data = fl.train_federated(rounds=30)
    fed = fl.catch_rates(w_fed)
    for m in fl.MERCHANTS:
        local = fl.catch_rates(fl.train_local_only(*data[m]))
        worst_local = min(local[t] for t in fl.FRAUD_TYPE.values())
        assert min(fed[t] for t in fl.FRAUD_TYPE.values()) > worst_local + 0.2
    assert all(fed[t] > .85 for t in fl.FRAUD_TYPE.values())
    assert fed["legit_flagged"] < .05


def test_fedavg_weights_by_examples():
    a, b = np.ones(3), np.zeros(3)
    assert np.allclose(fl.fedavg([(a, 3), (b, 1)]), .75)


@pytest.mark.parametrize("p,band", [(.1, "low"), (.3, "medium"), (.9, "high")])
def test_risk_band(p, band):
    assert fl.risk_band(p) == band
