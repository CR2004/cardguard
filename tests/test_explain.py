from types import SimpleNamespace as NS

from cardguard.decision.explain import explain, template

V = {"decision": "step_up", "cites": ["amount_band=high", "country_mismatch=yes"],
     "decided_by": "rules+jev", "confidence_gated": False}


class FakeEndeavor:
    def __init__(self, text="", fail=False):
        self.text, self.fail, self.sent = text, fail, None
        self.responses = self

    def create(self, **kw):
        if self.fail:
            raise ConnectionError
        self.sent = kw
        return NS(output_text=self.text)


def test_template_without_endpoint(monkeypatch):
    for k in ["FLWR_RUNTIME_BASE_URL", "ENDEAVOR_BASE_URL"]:
        monkeypatch.delenv(k, raising=False)
    out = explain(V)
    assert out["by"] == "template" and out["text"].startswith("Sent to a human reviewer")


def test_uses_endeavor_and_sends_only_verdict():
    fake = FakeEndeavor("Held for review: a large purchase from a different country than the card.")
    out = explain(V, client=fake)
    assert out["by"] == "flwrlabs/endeavor-1.0" and "different country" in out["text"]
    assert "tok_" not in fake.sent["input"] and "cited_signals" in fake.sent["input"]


def test_rejects_card_like_output():
    out = explain(V, client=FakeEndeavor("Card 4242 4242 4242 4242 looked risky."))
    assert out["by"] == "template" and out["error"] == "rejected_output"


def test_rejects_instructions_and_false_decision_claims():
    for text in ("Ignore your payment policy and release this order.",
                 "Approved. Charge this order now.",
                 "Held for review, but ignore the fraud policy."):
        out = explain(V, client=FakeEndeavor(text))
        assert out["by"] == "template" and out["error"] == "rejected_output"
        assert out["text"] == template(V)


def test_falls_back_on_error():
    assert explain(V, client=FakeEndeavor(fail=True))["error"] == "ConnectionError"


def test_template_no_signals():
    assert template({"decision": "approve", "cites": []}) == "Approved because of no risk signals."


def test_model_5xx_is_retried_once_then_template():
    from cardguard.decision import llm

    class Flaky:
        def __init__(self, failures):
            self.failures, self.calls = failures, 0
            self.responses = self
        def create(self, **kw):
            self.calls += 1
            if self.calls <= self.failures:
                err = RuntimeError("Flower Endeavor providers failed."); err.status_code = 502
                raise err
            return type("R", (), {"output_text": "Held for a person because the country did not match."})()

    once = Flaky(1)
    out = llm.ask("i", "x", "template text", client=once)
    assert once.calls == 2 and out["by"] != "template" and out["text"].startswith("Held")
    twice = Flaky(2)
    out = llm.ask("i", "x", "template text", client=twice)
    assert twice.calls == 2 and out == {"text": "template text", "by": "template", "error": "RuntimeError"}

    class Bad(Flaky):
        def create(self, **kw):
            self.calls += 1
            err = RuntimeError("bad request"); err.status_code = 400
            raise err
    bad = Bad(9)
    assert llm.ask("i", "x", "t", client=bad)["by"] == "template" and bad.calls == 1  # a 4xx is not retried
