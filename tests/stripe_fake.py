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
    confirmed = set()

    def retrieve(pm):
        if broken:
            raise ConnectionError("stripe unreachable")
        c = CARDS.get(pm) or (_ for _ in ()).throw(KeyError(pm))
        # like Stripe: the CVC result is unknown until the method has been confirmed once
        cvc = c["cvc"] if pm in confirmed else None
        return NS(card=NS(country=c["country"], funding=c["funding"], fingerprint=c["fingerprint"], checks=NS(cvc_check=cvc)))

    def setup(**kw):
        confirmed.add(kw["payment_method"]); return NS(status="succeeded")

    def attach(pm, customer=None):
        confirmed.add(pm); return NS(id=pm)

    def create(**kw):
        c = CARDS.get(kw.get("payment_method"), {})
        if c.get("decline"):
            raise CardError()
        return NS(status="succeeded", id="pi_test_" + kw.get("payment_method", "x")[3:])

    return NS(api_key=None, PaymentMethod=NS(retrieve=retrieve, attach=attach), PaymentIntent=NS(create=create),
              SetupIntent=NS(create=setup), Customer=NS(create=lambda **kw: NS(id="cus_test")), error=NS(CardError=CardError))


def processor(broken: bool = False, clock=None) -> StripeProcessor:
    kw = {"clock": clock} if clock else {}
    return StripeProcessor("sk_test_x", "pk_test_x", sdk=fake_sdk(broken), **kw)
