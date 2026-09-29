"""Stripe behind the same three calls as the issuer. The SDK is faked; no network, no keys."""
from types import SimpleNamespace as NS

import pytest

from cardguard.decision.guard import TOKEN_RE, find_leaks
from cardguard.payment_processing import merchant
from cardguard.payment_processing.issuer import IssuerReject
from cardguard.payment_processing.stripe_processor import StripeProcessor


class CardError(Exception):
    user_message = "Your card was declined."


def fake_sdk(decline=False, cvc="pass"):
    card = NS(country="US", funding="credit", fingerprint="fp_visa_4242", checks=NS(cvc_check=cvc))
    def create(**kw):
        if decline:
            raise CardError()
        return NS(status="succeeded", id="pi_test_abc")
    return NS(api_key=None, PaymentMethod=NS(retrieve=lambda pm: NS(card=card)),
              PaymentIntent=NS(create=create), error=NS(CardError=CardError))


def test_refuses_live_keys():
    with pytest.raises(SystemExit):
        StripeProcessor("sk_live_x", "pk_test_x", sdk=fake_sdk())
    with pytest.raises(SystemExit):
        StripeProcessor("sk_test_x", "pk_live_x", sdk=fake_sdk())


def test_verify_returns_the_same_five_facts_as_the_issuer():
    p = StripeProcessor("sk_test_x", "pk_test_x", sdk=fake_sdk())
    out = p.verify("pm_abc", 2000, "m")
    assert set(out) == {"verification_id", "card_ref", "country", "funding", "cvc_check"}
    assert TOKEN_RE.match(out["card_ref"]) and out["cvc_check"] == "pass" and out["funding"] == "credit"
    assert not any(find_leaks(str(v)) for v in out.values())
    assert p.verify("pm_abc", 2000, "m")["card_ref"] == out["card_ref"]        # stable per card
    with pytest.raises(IssuerReject, match="payment method"):
        p.verify("4242424242424242", 2000, "m")                               # a card number is not accepted


def test_authorize_once_scoped_and_bank_decline():
    p = StripeProcessor("sk_test_x", "pk_test_x", sdk=fake_sdk())
    vid = p.verify("pm_abc", 2000, "m")["verification_id"]
    with pytest.raises(IssuerReject, match="another merchant"):
        p.authorize(vid, "other")
    assert p.authorize(vid, "m") == {"status": "succeeded", "auth_code": "pi_test_abc"}
    with pytest.raises(IssuerReject, match="already used"):
        p.authorize(vid, "m")
    d = StripeProcessor("sk_test_x", "pk_test_x", sdk=fake_sdk(decline=True))
    vid = d.verify("pm_abc", 2000, "m")["verification_id"]
    assert d.authorize(vid, "m")["status"] == "issuer_declined"
    vid2 = d.verify("pm_abc", 2000, "m")["verification_id"]
    assert d.void(vid2, "m") == {"status": "voided"} and d.audit[-1]["event"] == "void"


def test_merchant_flow_on_stripe(monkeypatch):
    monkeypatch.setattr(merchant, "PROCESSOR", "stripe")
    monkeypatch.setattr(merchant, "issuer", StripeProcessor("sk_test_x", "pk_test_x", sdk=fake_sdk()))
    merchant.ledger = merchant.MerchantLedger(); merchant.pending.clear(); merchant.seen.clear(); merchant._checkout_calls.clear()
    c = merchant.app.test_client()
    assert c.get("/config").get_json()["processor"] == "stripe"
    res = c.post("/checkout", json={"blob": "pm_abc", "amount_cents": 2000, "buyer_country": "US", "hour": 14}).get_json()
    assert res["outcome"] == "approved" and res["payment"]["status"] == "succeeded"
    led = c.get("/ledger").get_json()
    assert "pm_abc" not in str(led) and led["card_numbers_seen_by_coordinator"] == 0
    tamper = c.post("/checkout", json={"blob": "pm_abc", "amount_cents": 2000, "buyer_country": "US", "hour": 14, "attack": "tamper"}).get_json()
    assert tamper["outcome"] == "issuer_rejected" and "only demonstrable" in tamper["reason"]
    leak = c.post("/checkout", json={"blob": "pm_abc", "amount_cents": 2000, "buyer_country": "US", "hour": 14, "attack": "leak"}).get_json()
    assert leak["outcome"] == "blocked" and len(leak["blocked"]) == 3
