"""Stripe as the processor: verify / authorize / void.

The store receives a Stripe payment-method id (pm_...) created by Stripe Elements in the browser:
the card number goes from the browser to Stripe, never to us. verify() turns that id into five
non-sensitive facts; authorize() confirms a TEST-mode PaymentIntent; void() drops the pending
verification (nothing was charged yet). Test keys only: sk_live_ / pk_live_ are refused.
"""
from __future__ import annotations

import secrets
import time

from cardguard.payment_processing.processor_base import ProcessorBase


class StripeProcessor(ProcessorBase):
    """verify / authorize / void, plus a chained audit of what Stripe answered (dispute evidence)."""

    def __init__(self, secret_key: str, publishable_key: str, sdk=None, clock=time.time):
        super().__init__(clock)
        if not secret_key.startswith("sk_test_") or not publishable_key.startswith("pk_test_"):
            raise SystemExit("Refusing to run: STRIPE_SECRET_KEY / STRIPE_PUBLISHABLE_KEY must be TEST keys "
                             "(sk_test_ / pk_test_); live keys are never accepted")
        if sdk is None:
            import stripe as sdk  # noqa: PLC0415 - optional dependency, imported only when used
        self.sdk = sdk
        self.sdk.api_key = secret_key
        self.publishable_key = publishable_key
        self._ref_key = secrets.token_bytes(32)  # card references are stable per process

    def card_ref(self, fingerprint: str) -> str:
        return self._card_ref(self._ref_key, fingerprint)

    def verify(self, pm_id: str, amount_cents: int, merchant_id: str) -> dict:
        if not str(pm_id).startswith("pm_"):
            return self._reject("verify", merchant_id, "not a Stripe payment method id")
        try:
            card = self.sdk.PaymentMethod.retrieve(pm_id).card
        except Exception as e:  # noqa: BLE001 - any SDK/network failure is a refusal, never card data
            return self._reject("verify", merchant_id, f"stripe: {type(e).__name__}")
        vid = self._new_verification(merchant_id, amount_cents, pm=pm_id)
        checks = getattr(card, "checks", None)
        cvc = getattr(checks, "cvc_check", None) if checks else None
        self._log("verify", merchant_id, "ok", cvc_check=cvc or "unavailable")
        return {"verification_id": vid, "card_ref": self.card_ref(card.fingerprint), "country": card.country,
                "funding": card.funding or "unknown", "cvc_check": cvc if cvc in {"pass", "fail"} else "unavailable"}

    def authorize(self, vid: str, merchant_id: str | None = None) -> dict:
        v = self._take(vid, merchant_id, "authorize")
        try:
            pi = self.sdk.PaymentIntent.create(amount=v["amount"], currency="usd", payment_method=v["pm"], confirm=True,
                                               automatic_payment_methods={"enabled": True, "allow_redirects": "never"})
        except self.sdk.error.CardError as e:  # the bank said no
            self._log("authorize", v["merchant"], "processor_declined")
            return {"status": "processor_declined", "reason": getattr(e, "user_message", "card declined")}
        except Exception as e:  # noqa: BLE001 - network/SDK failure: never a 500, never a retry that double-charges
            self._log("authorize", v["merchant"], "processor_error", reason=type(e).__name__)
            return {"status": "processor_error", "reason": type(e).__name__}
        self._log("authorize", v["merchant"], pi.status)
        return {"status": pi.status, "auth_code": pi.id}

    def void(self, vid: str, merchant_id: str | None = None) -> dict:
        v = self._take(vid, merchant_id, "void")
        self._log("void", v["merchant"], "voided")
        return {"status": "voided"}

