"""Stripe as the processor: verify / authorize / void.

The store receives a Stripe payment-method id (pm_...) created by Stripe Elements in the browser:
the card number goes from the browser to Stripe, never to us. Stripe checks the security code only
when it authorizes (a payment method alone says cvc_check "unchecked"), so verify() places a TEST-mode
authorization hold (a manual-capture PaymentIntent) and turns its answer into five non-sensitive
facts; authorize() captures that hold; void() cancels it, so nothing is charged.
Test keys only: sk_live_ / pk_live_ are refused.
"""
from __future__ import annotations

import secrets
import time

from cardguard.payment_processing.processor_base import VERIFICATION_TTL, ProcessorBase


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
        """The only card identity that leaves this adapter: a letters-only keyed hash of Stripe's card
        fingerprint. The merchant's wire token and the bank attestation node's pseudonym for the card.
        A demo correlation over Stripe TEST data, not an issuer-network identity protocol; the raw
        fingerprint is never returned, logged or sent anywhere."""
        return self._card_ref(self._ref_key, fingerprint)

    def verify(self, pm_id: str, amount_cents: int, merchant_id: str) -> dict:
        if not str(pm_id).startswith("pm_"):
            return self._reject("verify", merchant_id, "not a Stripe payment method id")
        try:
            pi = self.sdk.PaymentIntent.create(amount=int(amount_cents), currency="usd", payment_method=pm_id, confirm=True,
                                               capture_method="manual", expand=["latest_charge"],
                                               automatic_payment_methods={"enabled": True, "allow_redirects": "never"})
        except self.sdk.error.CardError as e:  # the bank refused the hold: nothing to decide, nothing charged
            return self._reject("verify", merchant_id, f"stripe: {getattr(e, 'user_message', None) or 'card declined'}")
        except Exception as e:  # noqa: BLE001 - any SDK/network failure is a refusal, never card data
            return self._reject("verify", merchant_id, f"stripe: {type(e).__name__}")
        card = getattr(getattr(pi.latest_charge, "payment_method_details", None), "card", None)
        if pi.status != "requires_capture" or card is None:  # e.g. 3-D Secure wants the buyer: release the hold
            reason = f"stripe: payment {pi.status}"
            self._cancel(pi.id)
            return self._reject("verify", merchant_id, reason)
        vid = self._new_verification(merchant_id, amount_cents, pi=pi.id)
        checks = getattr(card, "checks", None)
        cvc = getattr(checks, "cvc_check", None) if checks else None
        self._log("verify", merchant_id, "ok", cvc_check=cvc or "unavailable")
        return {"verification_id": vid, "card_ref": self.card_ref(card.fingerprint), "country": card.country,
                "funding": card.funding or "unknown", "cvc_check": cvc if cvc in {"pass", "fail"} else "unavailable"}

    def authorize(self, vid: str, merchant_id: str | None = None) -> dict:
        v = self._take(vid, merchant_id, "authorize")
        try:
            pi = self.sdk.PaymentIntent.capture(v["pi"])
        except self.sdk.error.CardError as e:  # the bank said no
            self._cancel(v["pi"])
            self._log("authorize", v["merchant"], "processor_declined")
            return {"status": "processor_declined", "reason": getattr(e, "user_message", "card declined")}
        except Exception as e:  # noqa: BLE001 - network/SDK failure: never a 500, never a retry that double-charges
            self._cancel(v["pi"])  # release the hold; a capture that did go through cannot be cancelled
            self._log("authorize", v["merchant"], "processor_error", reason=type(e).__name__)
            return {"status": "processor_error", "reason": type(e).__name__}
        self._log("authorize", v["merchant"], pi.status)
        return {"status": pi.status, "auth_code": pi.id}

    def void(self, vid: str, merchant_id: str | None = None) -> dict:
        v = self._take(vid, merchant_id, "void")
        if not self._cancel(v["pi"]):  # an uncancelled hold lapses on its own; never a 500
            self._log("void", v["merchant"], "void_failed")
            return {"status": "void_failed"}
        self._log("void", v["merchant"], "voided")
        return {"status": "voided"}

    def _prune(self) -> None:
        """Before forgetting old verifications, release any hold nobody captured or cancelled
        (a checkout that died after verify, or a review older than the TTL)."""
        now = self.clock()
        with self.lock:  # claimed like _take, so a late authorize or void cannot race the cancel
            expired = [v for v in self.verifications.values() if not v["used"] and now - v["t"] > VERIFICATION_TTL]
            for v in expired:
                v["used"] = True
        for v in expired:
            self._cancel(v["pi"])
        with self.lock:
            super()._prune()

    def _cancel(self, pi_id: str) -> bool:
        try:
            self.sdk.PaymentIntent.cancel(pi_id)
        except Exception:  # noqa: BLE001
            return False
        return True

