"""A faked Stripe SDK for the offline tests. Payment-method ids stand in for test cards."""
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
    def retrieve(pm):
        if broken:
            raise ConnectionError("stripe unreachable")
        c = CARDS.get(pm) or (_ for _ in ()).throw(KeyError(pm))
        return NS(card=NS(country=c["country"], funding=c["funding"], fingerprint=c["fingerprint"], checks=NS(cvc_check=c["cvc"])))

    def create(**kw):
        c = CARDS.get(kw.get("payment_method"), {})
        if c.get("decline"):
            raise CardError()
        return NS(status="succeeded", id="pi_test_" + kw.get("payment_method", "x")[3:])

    return NS(api_key=None, PaymentMethod=NS(retrieve=retrieve), PaymentIntent=NS(create=create), error=NS(CardError=CardError))


def processor(broken: bool = False, clock=None) -> StripeProcessor:
    kw = {"clock": clock} if clock else {}
    return StripeProcessor("sk_test_x", "pk_test_x", sdk=fake_sdk(broken), **kw)
