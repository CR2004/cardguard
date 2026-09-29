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
    assert out["by"] == "Flwrlabs/endeavor-v1.0" and "different country" in out["text"]
    assert "tok_" not in fake.sent["input"] and "cited_signals" in fake.sent["input"]


def test_rejects_card_like_output():
    out = explain(V, client=FakeEndeavor("Card 4242 4242 4242 4242 looked risky."))
    assert out["by"] == "template" and out["error"] == "rejected_output"


def test_falls_back_on_error():
    assert explain(V, client=FakeEndeavor(fail=True))["error"] == "ConnectionError"


def test_template_no_signals():
    assert template({"decision": "approve", "cites": []}) == "Approved because of no risk signals."
