"""Bank attestation node: the issuing bank's private view of a card, answered only as bands.

Stripe is the payment rail. This node takes no card entry, moves no money and never sees a card
number. It answers two signed questions from a registered merchant node about a pseudonymous card
reference:

  POST /attest        {card_ref, card_region}                            -> round 1
                      {issuer_behavior: low|medium|high, issuer_recent_declines: none|some|many}
  POST /travel-check  {card_ref, card_region, buyer_region, decision_id} -> round 2, once per decision
                      {travel_check: plausible|implausible|unknown}

The history behind those answers stays in this process: where the card was last used in person
(a city), when, and its recent declines. It is SYNTHETIC: the first time the bank is asked about a
card_ref it creates an active cardholder at home in the card's issuing region, used in person one to
six hours earlier, with no recent declines; that history then ages with the clock. So in a demo
session travel_check reads as a region-and-recency check: another region within 12 hours of the last
in-person use is implausible. A card's region is fixed at first sight. card_ref is a demo correlation over Stripe TEST data
(the merchant's keyed hash of Stripe's card fingerprint), not an issuer-network identity protocol.

    python -m cardguard.bank.node        # http://127.0.0.1:4243; run_demo.py starts it
"""
from __future__ import annotations

import collections
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass

from flask import Flask, jsonify, request

from cardguard.bank.client import REGION_NAMES

MIN_TRAVEL_HOURS = 12     # a different region is reachable only this long after the last in-person use
MAX_REQUEST_SKEW = 300    # seconds a signed request stays valid
TRAVEL_PER_CARD_PER_HOUR = 10  # bounds how often one card's whereabouts can be asked about
REGIONS_PER_CARD_PER_HOUR = 2  # distinct buyer regions a merchant may ask about per card: no sweeping the map
CARD_REF_RE = re.compile(r"^tok_[a-p]{16}$")
CITIES = {"North America": ("San Francisco", "Chicago", "Toronto"), "Europe": ("Berlin", "Lisbon", "London"),
          "Latin America": ("Sao Paulo",), "Africa": ("Lagos",), "Elsewhere": ("Singapore",)}


class Refused(Exception):
    """A question the bank will not answer; str(e) is the reason (never history)."""


@dataclass
class History:
    city: str
    region: str
    last_in_person: float  # timestamp of the card's last in-person use
    declines_30d: int


class BankAttestor:
    """The private history and the only functions that read it. Answers are bands; nothing else leaves."""

    def __init__(self, seed: bytes | None = None, history: dict[str, History] | None = None, clock=time.time):
        self._seed = seed or secrets.token_bytes(32)
        self._history: dict[str, History] = dict(history or {})
        self._answered: collections.OrderedDict[tuple[str, str], None] = collections.OrderedDict()  # (merchant, decision)
        self._travel_asks: dict[str, list[float]] = {}
        self._regions_asked: dict[tuple[str, str], dict[str, float]] = {}  # (merchant, card) -> region -> when
        self.clock = clock
        self.lock = threading.Lock()

    def _card(self, card_ref: str, card_region: str) -> History:
        if not CARD_REF_RE.match(card_ref):
            raise Refused("malformed card reference")
        if card_region not in REGION_NAMES:
            raise Refused("unknown region")
        h = self._history.get(card_ref)
        if h is None:  # first sight: a synthetic, active cardholder at home (deterministic per card)
            n = int.from_bytes(hmac.new(self._seed, card_ref.encode(), hashlib.sha256).digest()[:8], "big")
            cities = CITIES[card_region]
            h = History(city=cities[n % len(cities)], region=card_region,
                        last_in_person=self.clock() - 3600 * (1 + (n >> 8) % 6), declines_30d=0)
            self._history[card_ref] = h
        elif h.region != card_region:
            raise Refused("region does not match this card")
        return h

    def attest(self, card_ref: str, card_region: str) -> dict:
        with self.lock:
            h = self._card(card_ref, card_region)
        declines = "none" if h.declines_30d == 0 else "some" if h.declines_30d < 3 else "many"
        return {"issuer_behavior": {"none": "low", "some": "medium", "many": "high"}[declines],
                "issuer_recent_declines": declines}

    def travel_check(self, card_ref: str, card_region: str, buyer_region: str, decision_id: str,
                     merchant_id: str = "") -> dict:
        """Can the cardholder be in the buyer's region, given the card's last in-person use? One answer
        per (merchant, decision); per card, a bounded number per hour and at most two buyer regions per
        merchant, so the answers cannot be used to sweep the map for where a card was last used."""
        now = self.clock()
        with self.lock:
            h = self._card(card_ref, card_region)
            if buyer_region not in REGION_NAMES:
                raise Refused("unknown region")
            key = (merchant_id, decision_id)
            if not decision_id or key in self._answered:
                raise Refused("already answered for this decision")
            asks = [t for t in self._travel_asks.get(card_ref, []) if now - t < 3600]
            if len(asks) >= TRAVEL_PER_CARD_PER_HOUR:
                raise Refused("too many travel questions about this card")
            regions = {r: t for r, t in self._regions_asked.get((merchant_id, card_ref), {}).items() if now - t < 3600}
            if buyer_region not in regions and len(regions) >= REGIONS_PER_CARD_PER_HOUR:
                raise Refused("too many regions asked about this card")
            self._travel_asks[card_ref] = asks + [now]
            self._regions_asked[(merchant_id, card_ref)] = {**regions, buyer_region: now}
            self._answered[key] = None
            while len(self._answered) > 10000:
                self._answered.popitem(last=False)
        hours_since = (now - h.last_in_person) / 3600
        ok = h.region == buyer_region or hours_since >= MIN_TRAVEL_HOURS
        return {"travel_check": "plausible" if ok else "implausible"}


class Gate:
    """Signed-request check for registered merchants, with nonce replay protection."""

    def __init__(self, merchants: dict[str, str], clock=time.time):
        self.merchants, self.clock = merchants, clock
        self.nonces: dict[str, float] = {}
        self.lock = threading.Lock()

    def check(self, merchant_id: str, ts: str, nonce: str, signature: str, method: str, path: str, body: bytes) -> str:
        secret = self.merchants.get(merchant_id)
        try:
            fresh = abs(self.clock() - int(ts)) <= MAX_REQUEST_SKEW
            expected = hmac.new((secret or "").encode(), f"{ts}\n{method}\n{path}\n{nonce}\n".encode() + body,
                                hashlib.sha256).hexdigest()
            ok = bool(secret) and bool(nonce) and fresh and hmac.compare_digest(expected, str(signature or ""))
        except (TypeError, ValueError):
            ok = False
        with self.lock:
            now = self.clock()
            self.nonces = {n: t for n, t in self.nonces.items() if now - t <= MAX_REQUEST_SKEW}
            if ok and nonce in self.nonces:
                ok = False  # a captured signed request cannot be replayed
            if ok:
                self.nonces[nonce] = now
        if not ok:
            raise PermissionError("unauthenticated merchant")
        return merchant_id


def parse_merchants(spec: str) -> dict[str, str]:
    """"id:secret,id2:secret2" -> {id: secret}."""
    out = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        mid, _, secret = part.partition(":")
        if mid and secret:
            out[mid] = secret
    return out


app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024
bank = BankAttestor()
gate = Gate(parse_merchants(os.environ.get("BANK_MERCHANTS", "")))


def _signed(fn):
    try:
        merchant_id = gate.check(request.headers.get("X-Merchant-Id", ""), request.headers.get("X-Timestamp", ""),
                                 request.headers.get("X-Nonce", ""), request.headers.get("X-Signature", ""),
                                 request.method, request.path, request.get_data())
        body = request.get_json(force=True)
        return jsonify(fn(body, merchant_id))
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    except Refused as e:
        return jsonify({"error": str(e)}), 409
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "bad request"}), 400


@app.post("/attest")
def attest():
    return _signed(lambda b, _m: bank.attest(str(b["card_ref"]), str(b["card_region"])))


@app.post("/travel-check")
def travel_check():
    return _signed(lambda b, m: bank.travel_check(str(b["card_ref"]), str(b["card_region"]), str(b["buyer_region"]),
                                                  str(b["decision_id"]), merchant_id=m))


if __name__ == "__main__":
    print("Bank attestation node on http://127.0.0.1:4243  (synthetic private history; answers bands only)")
    app.run(port=4243)
