"""A faked Stripe SDK for the offline tests. Payment-method ids stand in for test cards.

Shaped like real Stripe TEST mode (checked against it on Sep 29): a payment method alone reports
cvc_check "unchecked"; the security code is checked only when a PaymentIntent authorizes, and the
result is on that authorization's charge (an id only, unless expanded). A declining card fails the
authorization itself."""
from itertools import count
from types import SimpleNamespace as NS

from cardguard.payment_processing.stripe_processor import StripeProcessor

CARDS = {
    "pm_visa":    dict(country="US", funding="credit", fingerprint="fp_visa", cvc="pass"),
    "pm_de":      dict(country="DE", funding="debit",  fingerprint="fp_de",   cvc="pass"),
    "pm_prepaid": dict(country="US", funding="prepaid", fingerprint="fp_pre", cvc="pass"),
    "pm_zero":    dict(country="US", funding="credit", fingerprint="fp_zero", cvc="pass", decline=True),
    "pm_cvcfail": dict(country="US", funding="credit", fingerprint="fp_visa", cvc="fail"),
}


class CardError(Exception):
    user_message = "Your card was declined."


def fake_sdk(broken: bool = False):
    intents: dict[str, NS] = {}  # every PaymentIntent created, by id: tests read their final status
    ids = count(1)

    def card(pm, cvc):
        c = CARDS.get(pm) or (_ for _ in ()).throw(KeyError(pm))
        return NS(country=c["country"], funding=c["funding"], fingerprint=c["fingerprint"], checks=NS(cvc_check=cvc))

    def retrieve(pm):  # like Stripe: nothing is checked before an authorization
        if broken:
            raise ConnectionError("stripe unreachable")
        return NS(card=card(pm, "unchecked"))

    def create(**kw):
        if broken:
            raise ConnectionError("stripe unreachable")
        pm = kw["payment_method"]
        checked = card(pm, CARDS.get(pm, {}).get("cvc"))
        if CARDS[pm].get("decline"):
            raise CardError()
        pi = NS(id=f"pi_test_{pm[3:]}_{next(ids)}", amount=kw["amount"],
                status="requires_capture" if kw.get("capture_method") == "manual" else "succeeded",
                latest_charge=NS(payment_method_details=NS(card=checked)) if "latest_charge" in kw.get("expand", []) else "ch_test")
        intents[pi.id] = pi
        return pi

    def move(frm, to):
        def call(pid):
            pi = intents[pid]
            assert pi.status == frm, f"{pid} is {pi.status}, not {frm}"
            pi.status = to
            return pi
        return call

    return NS(api_key=None, PaymentMethod=NS(retrieve=retrieve), intents=intents, error=NS(CardError=CardError),
              PaymentIntent=NS(create=create, capture=move("requires_capture", "succeeded"),
                               cancel=move("requires_capture", "canceled")))


def processor(broken: bool = False, clock=None) -> StripeProcessor:
    kw = {"clock": clock} if clock else {}
    return StripeProcessor("sk_test_x", "pk_test_x", sdk=fake_sdk(broken), **kw)
