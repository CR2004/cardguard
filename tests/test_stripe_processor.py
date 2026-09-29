"""Stripe behind the same three calls as the issuer. The SDK is faked; no network, no keys."""
from types import SimpleNamespace as NS

import pytest

from cardguard.decision.guard import TOKEN_RE, find_leaks
from cardguard.payment_processing import merchant
from cardguard.payment_processing.errors import ProcessorReject
from cardguard.payment_processing.stripe_processor import StripeProcessor


class CardError(Exception):
    user_message = "Your card was declined."


def fake_sdk(decline=False, cvc="pass", status="requires_capture", capture_error=None, cancel_error=None, no_card=False):
    """Like real Stripe: the payment method says 'unchecked'; the authorization's charge has the result, and
    latest_charge is only an id unless it was expanded. Records every cancel call."""
    intents, cancels = {}, []
    def card(check):
        return NS(country="US", funding="credit", fingerprint="fp_visa_4242", checks=NS(cvc_check=check))
    def create(**kw):
        if decline:
            raise CardError()
        charge = NS(payment_method_details=None if no_card else NS(card=card(cvc))) \
            if "latest_charge" in kw.get("expand", []) else "ch_test"
        pi = NS(id=f"pi_test_{len(intents)}", status=status if kw.get("capture_method") == "manual" else "succeeded",
                latest_charge=charge)
        intents[pi.id] = pi
        return pi
    def capture(pid):
        if capture_error:
            raise capture_error
        assert intents[pid].status == "requires_capture"
        intents[pid].status = "succeeded"
        return intents[pid]
    def cancel(pid):
        cancels.append(pid)
        if cancel_error:
            raise cancel_error
        if intents[pid].status == "succeeded":
            raise RuntimeError("a captured PaymentIntent cannot be canceled")
        intents[pid].status = "canceled"
        return intents[pid]
    return NS(api_key=None, PaymentMethod=NS(retrieve=lambda pm: NS(card=card("unchecked"))), intents=intents, cancels=cancels,
              PaymentIntent=NS(create=create, capture=capture, cancel=cancel), error=NS(CardError=CardError))


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
    with pytest.raises(ProcessorReject, match="payment method"):
        p.verify("4242424242424242", 2000, "m")                               # a card number is not accepted


def test_authorize_once_scoped_and_bank_decline():
    sdk = fake_sdk()
    p = StripeProcessor("sk_test_x", "pk_test_x", sdk=sdk)
    vid = p.verify("pm_abc", 2000, "m")["verification_id"]
    with pytest.raises(ProcessorReject, match="another merchant"):
        p.authorize(vid, "other")
    assert p.authorize(vid, "m") == {"status": "succeeded", "auth_code": "pi_test_0"}
    with pytest.raises(ProcessorReject, match="already used"):
        p.authorize(vid, "m")
    vid2 = p.verify("pm_abc", 2000, "m")["verification_id"]
    assert p.void(vid2, "m") == {"status": "voided"} and p.audit[-1]["event"] == "void"
    assert sdk.intents["pi_test_1"].status == "canceled"                       # the hold is released
    d = StripeProcessor("sk_test_x", "pk_test_x", sdk=fake_sdk(decline=True))
    with pytest.raises(ProcessorReject, match="declined"):                      # the bank refuses the hold itself
        d.verify("pm_abc", 2000, "m")


def test_cvc_result_comes_from_an_authorization_hold():
    """Regression (real Stripe TEST, Sep 29): a payment method reports cvc_check 'unchecked' until an
    authorization runs. Reading it there made every real card 'unavailable', so round 2 and the CVC hard
    decline never fired and a CVC-failing card could be charged. verify() must hold (manual capture),
    read the check from that authorization, and charge nothing until authorize()."""
    for cvc in ("pass", "fail"):
        sdk = fake_sdk(cvc=cvc)
        out = StripeProcessor("sk_test_x", "pk_test_x", sdk=sdk).verify("pm_abc", 2000, "m")
        assert out["cvc_check"] == cvc
        assert [pi.status for pi in sdk.intents.values()] == ["requires_capture"]   # held, not charged


def test_merchant_flow_on_stripe(monkeypatch):
    monkeypatch.setattr(merchant, "processor", StripeProcessor("sk_test_x", "pk_test_x", sdk=fake_sdk()))
    merchant.ledger = merchant.MerchantLedger(); merchant.pending.clear(); merchant.seen.clear(); merchant._checkout_calls.clear()
    c = merchant.app.test_client()
    res = c.post("/checkout", json={"blob": "pm_abc", "amount_cents": 2000, "buyer_country": "US", "hour": 14}).get_json()
    assert res["outcome"] == "approved" and res["payment"]["status"] == "succeeded"
    led = c.get("/ledger").get_json()
    assert "pm_abc" not in str(led) and led["card_numbers_seen_by_coordinator"] == 0
    leak = c.post("/checkout", json={"blob": "pm_abc", "amount_cents": 2000, "buyer_country": "US", "hour": 14, "attack": "leak"}).get_json()
    assert leak["outcome"] == "blocked" and len(leak["blocked"]) == 3


def test_a_hold_nobody_settled_is_released_when_its_verification_expires():
    """A checkout that dies between verify and settle (or a review older than the TTL) must not leave
    an authorization hold on the card: expired, unused verifications cancel their hold. Fresh or
    settled ones are never touched."""
    from cardguard.payment_processing.processor_base import VERIFICATION_TTL
    now = [1000.0]
    sdk = fake_sdk()
    p = StripeProcessor("sk_test_x", "pk_test_x", sdk=sdk, clock=lambda: now[0])
    captured = p.verify("pm_abc", 2000, "m")["verification_id"]
    p.verify("pm_abc", 2000, "m")                              # never authorized or voided
    now[0] += VERIFICATION_TTL / 2
    pending = p.verify("pm_abc", 2000, "m")["verification_id"]  # a review still inside the TTL
    assert p.authorize(captured, "m")["status"] == "succeeded"  # settled late: old, but used
    now[0] += VERIFICATION_TTL / 2 + 1
    p.verify("pm_abc", 2000, "m")                              # the next checkout prunes
    assert sdk.cancels == ["pi_test_1"]                        # only the stale, unsettled hold
    assert [pi.status for pi in sdk.intents.values()] == ["succeeded", "canceled", "requires_capture", "requires_capture"]
    assert p.authorize(pending, "m")["status"] == "succeeded"  # the review inside the TTL still captures


@pytest.mark.parametrize("status,no_card", [("requires_action", False), ("processing", False), ("requires_capture", True)])
def test_a_hold_stripe_did_not_complete_is_released_and_refused(status, no_card):
    sdk = fake_sdk(status=status, no_card=no_card)              # e.g. 3-D Secure wants the buyer; no card details
    p = StripeProcessor("sk_test_x", "pk_test_x", sdk=sdk)
    with pytest.raises(ProcessorReject, match=status):
        p.verify("pm_abc", 2000, "m")
    assert [pi.status for pi in sdk.intents.values()] == ["canceled"] and p.verifications == {}


def test_a_failed_capture_releases_the_hold_and_reports_not_charged():
    for err, status in ((CardError(), "processor_declined"), (ConnectionError("down"), "processor_error")):
        sdk = fake_sdk(capture_error=err)
        p = StripeProcessor("sk_test_x", "pk_test_x", sdk=sdk)
        out = p.authorize(p.verify("pm_abc", 2000, "m")["verification_id"], "m")
        assert out["status"] == status and "auth_code" not in out
        assert [pi.status for pi in sdk.intents.values()] == ["canceled"]


def test_a_failed_cancel_is_reported_not_raised():
    sdk = fake_sdk(cancel_error=ConnectionError("down"))
    p = StripeProcessor("sk_test_x", "pk_test_x", sdk=sdk)
    assert p.void(p.verify("pm_abc", 2000, "m")["verification_id"], "m") == {"status": "void_failed"}
    assert p.audit[-1]["event"] == "void" and p.audit[-1]["status"] == "void_failed"
