"""The dispute evidence agent only sees banded facts and events, and its draft is leak-scanned."""
from types import SimpleNamespace as NS

import pytest

from cardguard.agentapp import dispute_agent as da
from cardguard.decision.guard import find_leaks, json_values

FACTS = {"token": "tok_abcdefghijklmnop", "amount_band": "low", "country_mismatch": "no", "cvc_check": "pass",
         "network_velocity_band": "low"}
PAY = {"amount": 2000, "features": [0] * 9, "store": "store-a", "t": 1000.0, "disputed": True, "facts": FACTS}


def _ledger():
    from cardguard.decision import audit
    entries = []
    audit.append(entries, {"t": 999.0, "status": "DISCLOSED", "note": {"store": "store-a"}, "fields": FACTS})
    return entries


LEDGER = _ledger()
AUDIT = [{"t": 998.0, "event": "verify", "merchant": "m", "status": "ok", "cvc_check": "pass"},
         {"t": 1000.5, "event": "authorize", "merchant": "m", "status": "succeeded"}]


class FakeLLM:
    def __init__(self, text): self.text, self.sent, self.responses = text, None, self
    def create(self, **kw): self.sent = kw; return NS(output_text=self.text)


def test_evidence_is_banded_facts_and_events_only():
    ev = da.assemble_evidence("AUTHXX", PAY, LEDGER, AUDIT)
    assert ev["facts_at_decision"] == {"amount_band": "low", "country_mismatch": "no", "cvc_check": "pass", "network_velocity_band": "low"}
    assert "token" not in ev["facts_at_decision"] and [e["event"] for e in ev["issuer_events"]] == ["verify", "authorize"]
    assert ev["checks"]["security_code"] == "pass" and ev["checks"]["billing_country_matched_buyer"]
    assert ev["checks"]["ledger_chain_intact"]
    assert not any(find_leaks(v) for v in json_values(ev))
    tampered = [dict(LEDGER[0], status="BLOCKED")]
    assert da.assemble_evidence("AUTHXX", PAY, tampered, AUDIT)["checks"]["ledger_chain_intact"] is False


def test_evidence_with_a_card_like_value_is_refused():
    bad = {**PAY, "facts": {**FACTS, "amount_band": "4242424242424242"}}
    with pytest.raises(ValueError):
        da.assemble_evidence("A", bad, LEDGER, AUDIT)


def test_draft_template_and_model_paths():
    ev = da.assemble_evidence("AUTHXX", PAY, LEDGER, AUDIT)
    from cardguard.decision import llm
    t = da.draft_response(ev, client=None) if llm.client() is None else None
    assert t is None or (t["by"] == "template" and "AUTHXX" in t["text"])
    llm = FakeLLM("We contest this chargeback: the security code check passed and the issuer authorized it.")
    out = da.draft_response(ev, client=llm)
    assert out["by"] != "template" and "tok_" not in llm.sent["input"]
    leaky = da.draft_response(ev, client=FakeLLM("card 4242 4242 4242 4242 was used"))
    assert leaky["by"] == "template" and leaky["error"] == "rejected_output"


def test_merchant_endpoint(monkeypatch):
    from cardguard.payment_processing import merchant
    from cardguard.payment_processing.issuer import Issuer
    from tests.test_merchant import buy
    monkeypatch.setattr(merchant, "issuer", Issuer(merchants={merchant.MERCHANT_ID: "s"}))
    monkeypatch.setattr(merchant, "REVIEWER_TOKEN", "rev-token"); H = {"Authorization": "Bearer rev-token"}
    merchant.ledger = merchant.MerchantLedger(); merchant.pending.clear(); merchant.seen.clear(); merchant.payments.clear(); merchant.labels.clear(); merchant._checkout_calls.clear()
    c = merchant.app.test_client()
    code = buy(c)["payment"]["auth_code"]
    assert c.get(f"/payments/{code}/evidence").status_code == 401             # reviewer only
    assert c.get(f"/payments/{code}/evidence", headers=H).status_code == 404  # not disputed yet
    c.post(f"/payments/{code}/dispute", headers=H)
    out = c.get(f"/payments/{code}/evidence", headers=H).get_json()
    assert out["evidence"]["auth_code"] == code and out["draft"]["text"] and "tok_" not in str(out["evidence"])
