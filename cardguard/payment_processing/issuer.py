"""Issuer node: the only party besides the buyer's browser that ever sees card details.

The buyer types the card into a frame served from THIS origin (card_frame.html + card_seal.js),
which seals {pan, exp, cvc, amount, merchant, nonce, ts} with RSA-OAEP-SHA256 under the issuer's
public key. The merchant forwards the sealed blob unread. The issuer:
  - decrypts and rejects replay (nonce reuse), stale seals (> 10 min), tampering (sealed amount or
    merchant differ from what the merchant claims), unknown card, bad or expired expiry;
  - answers with non-sensitive facts only: {verification_id, card_ref, country, funding, cvc_check}.
    A wrong CVC is NOT a hard reject; it returns cvc_check="fail" for the fraud logic to weigh.
  - later authorizes (balance check, deduct, auth code; one use per verification) or voids.

Card numbers and CVCs are stored only as HMAC-SHA256 under an issuer key. card_ref is a letters-only
keyed hash of the card number (tok_[a-p]{16}), so it can never look like a card number and is the
same at every merchant.

Simulated: the card network and the bank ledger (balances). Real: the encryption, replay/tamper
checks, and everything the fraud agents do with the facts.

    python -m cardguard.payment_processing.issuer     # http://127.0.0.1:4243
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from flask import Flask, Response, jsonify, request, send_from_directory

from cardguard.decision import audit
from cardguard.decision.guard import HEX_TO_LETTERS as LETTERS
from cardguard.decision.guard import find_leaks
from cardguard.payment_processing.errors import IssuerReject
from cardguard.payment_processing.processor_base import ProcessorBase

MERCHANT_ORIGIN = os.environ.get("MERCHANT_ORIGIN", "http://127.0.0.1:4242")
MAX_SEAL_AGE = 600        # seconds a sealed card stays valid
MAX_REQUEST_SKEW = 300    # seconds a signed merchant request stays valid
MAX_BLOB_LEN = 2048       # a 2048-bit RSA blob is ~350 chars; anything bigger is not a seal
MAX_CVC_FAILURES = 3      # failed security-code or expiry checks before a card is locked
LOCK_SECONDS = 900        # a lock expires: a stranger with the number cannot lock a card for good
MAX_AMOUNT_CENTS = 10_000_000
VERIFY_RATE_PER_MINUTE = int(os.environ.get("ISSUER_RATE_PER_MINUTE", "120"))  # per merchant
HMAC_KEY_FILE = os.environ.get("ISSUER_HMAC_KEY_FILE", "")          # persisted card-hashing key (0600)
KEY_FILE = os.environ.get("ISSUER_KEY_FILE", "")                    # PEM; generated and saved if missing
PREVIOUS_KEY_FILE = os.environ.get("ISSUER_PREVIOUS_KEY_FILE", "")  # still accepted during rotation
ADMIN_TOKEN = os.environ.get("ISSUER_ADMIN_TOKEN", "")              # bearer token for /audit


def load_or_create_key(path: str = ""):
    """RSA-2048 from a PEM file (created with mode 0600 if missing), or ephemeral when no path.
    A production issuer keeps this key in an HSM/KMS and never exports it."""
    if path and os.path.exists(path):
        with open(path, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if path:
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
    return key


def load_or_create_hmac_key(path: str = "") -> bytes:
    """32 random bytes from a file (created 0600 if missing), or ephemeral. Kept next to the RSA key;
    rotating it changes every card reference, so rotate both together on a schedule."""
    if path and os.path.exists(path):
        with open(path, "rb") as f:
            return bytes.fromhex(f.read().decode().strip())
    key = secrets.token_bytes(32)
    if path:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key.hex())
    return key


def key_id(key) -> str:
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()[:8].translate(LETTERS)  # letters-only, never card-like


def parse_merchants(spec: str) -> dict[str, str]:
    """ISSUER_MERCHANTS="id:secret,id2:secret2" -> {id: secret}."""
    out = {}
    for part in filter(None, (s.strip() for s in spec.split(","))):
        mid, _, secret = part.partition(":")
        if mid and secret:
            out[mid] = secret
    return out

# Synthetic test cards only. (pan, exp, cvc, country, funding, balance_cents)
TEST_CARDS = [
    ("4242424242424242", "12/30", "123", "US", "credit", 10_000_000),
    ("5555555555554444", "11/29", "456", "DE", "debit", 10_000_000),
    ("4000056655665556", "10/28", "789", "US", "prepaid", 10_000_000),
    ("4000000000000002", "09/29", "321", "US", "credit", 0),  # zero balance -> issuer_declined
]


class Issuer(ProcessorBase):
    def __init__(self, private_key=None, cards=TEST_CARDS, clock=time.time, merchants=None,
                 previous_key=None, hmac_key: bytes | None = None, admin_token: str = ""):
        super().__init__(clock)
        self._key = private_key or load_or_create_key()
        self._keys = {key_id(self._key): self._key}
        if previous_key is not None:
            self._keys[key_id(previous_key)] = previous_key  # rotation: old seals still verify
        self._hmac_key = hmac_key or secrets.token_bytes(32)
        self.merchants: dict[str, str] = dict(merchants or {})  # merchant id -> shared secret
        self.admin_token = admin_token
        self.cards = {self._h("pan|" + pan): {"cvc": self._h("cvc|" + cvc), "exp": exp, "country": country,
                                              "funding": funding, "balance": balance, "cvc_failures": 0, "locked_until": 0.0}
                      for pan, exp, cvc, country, funding, balance in cards}
        self.nonces: dict[str, float] = {}  # nonce -> seal time; forgotten once the seal itself has expired
        self.verify_calls: dict[str, list[float]] = {}
        self.request_nonces: dict[str, float] = {}  # signed-request nonces within the skew window

    # ---- keys and hashing ----
    def _h(self, value: str) -> str:
        return hmac.new(self._hmac_key, value.encode(), hashlib.sha256).hexdigest()

    def card_ref(self, pan: str) -> str:
        return self._card_ref(self._hmac_key, pan)

    @property
    def kid(self) -> str:
        return key_id(self._key)

    def pubkey_b64(self) -> str:
        der = self._key.public_key().public_bytes(serialization.Encoding.DER,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo)
        return base64.b64encode(der).decode()

    def seal(self, fields: dict, key=None) -> str:
        """Encrypt like the browser does (tests only; the merchant never has anything to seal).
        The key id travels in the clear as a prefix: <kid>.<ciphertext>."""
        key = key or self._key
        raw = json.dumps(fields, separators=(",", ":")).encode()
        return key_id(key) + "." + base64.b64encode(key.public_key().encrypt(raw, _oaep())).decode()

    # ---- merchant authentication (HTTP layer calls this; tests may call it directly) ----
    def authenticate(self, merchant_id: str, ts: str, signature: str, method: str, path: str, body: bytes,
                     nonce: str = "") -> str:
        secret = self.merchants.get(merchant_id)
        try:
            skew = abs(self.clock() - int(ts))
            expected = hmac.new((secret or "").encode(), f"{ts}\n{method}\n{path}\n{nonce}\n".encode() + body,
                                hashlib.sha256).hexdigest()
            ok = secret is not None and skew <= MAX_REQUEST_SKEW and hmac.compare_digest(expected, str(signature or ""))
        except (TypeError, ValueError):  # non-ASCII or malformed headers are just a bad credential
            ok = False
        now = self.clock()
        self.request_nonces = {n: t for n, t in self.request_nonces.items() if now - t <= MAX_REQUEST_SKEW}
        if ok and nonce:
            if nonce in self.request_nonces:
                ok = False  # a captured signed request cannot be replayed
            else:
                self.request_nonces[nonce] = now
        if not ok:
            self._log("auth", merchant_id, "rejected", reason="bad credential, signature, timestamp or nonce")
            raise PermissionError("unauthenticated merchant")
        return merchant_id

    # ---- verify ----
    def verify(self, blob_b64: str, amount_cents: int, merchant_id: str) -> dict:
        now = self.clock()
        recent = [t for t in self.verify_calls.get(merchant_id, []) if now - t < 60]
        if len(recent) >= VERIFY_RATE_PER_MINUTE:
            return self._reject("verify", merchant_id, "merchant rate limit")
        self.verify_calls[merchant_id] = recent + [now]
        self.nonces = {n: t for n, t in self.nonces.items() if now - t <= MAX_SEAL_AGE}  # expired seals cannot replay
        if len(blob_b64) > MAX_BLOB_LEN:
            return self._reject("verify", merchant_id, "blob too large")
        try:
            kid, _, cipher = blob_b64.partition(".")
            key = self._keys.get(kid)
            if key is None:
                return self._reject("verify", merchant_id, "unknown key id")
            sealed = json.loads(key.decrypt(base64.b64decode(cipher), _oaep()))
        except IssuerReject:
            raise
        except Exception:
            return self._reject("verify", merchant_id, "undecryptable blob")
        try:
            pan, exp, cvc = str(sealed["p"]), str(sealed["e"]), str(sealed["c"])
            nonce, ts = str(sealed["n"]), float(sealed["t"])
            s_amount, s_merchant = int(sealed["a"]), str(sealed["m"])
        except (KeyError, TypeError, ValueError):
            return self._reject("verify", merchant_id, "malformed seal")
        if nonce in self.nonces:
            return self._reject("verify", merchant_id, "replay: nonce reused")
        if abs(self.clock() - ts) > MAX_SEAL_AGE:
            return self._reject("verify", merchant_id, "stale seal")
        if not 1 <= int(amount_cents) <= MAX_AMOUNT_CENTS:
            return self._reject("verify", merchant_id, "amount out of range")
        if s_amount != int(amount_cents):
            return self._reject("verify", merchant_id, "tampering: sealed amount differs from request")
        if s_merchant != merchant_id:
            return self._reject("verify", merchant_id, "tampering: sealed merchant differs from request")
        card_key = self._h("pan|" + pan)
        card = self.cards.get(card_key)
        if card is None:
            return self._reject("verify", merchant_id, "unknown card")
        if card["locked_until"] > now:
            return self._reject("verify", merchant_id, "card locked: too many failed checks")
        if not _expiry_ok(exp, card["exp"], now):
            self._count_failure(card, now)  # guessing expiries locks the card like guessing CVCs
            return self._reject("verify", merchant_id, "wrong or expired expiry")
        self.nonces[nonce] = ts
        if hmac.compare_digest(self._h("cvc|" + cvc), card["cvc"]):
            cvc_check, card["cvc_failures"] = "pass", 0
        else:
            cvc_check = "fail"
            self._count_failure(card, now)
        vid = self._new_verification(merchant_id, amount_cents, card=card_key)
        self._log("verify", merchant_id, "ok", cvc_check=cvc_check)
        out = {"verification_id": vid, "card_ref": self.card_ref(pan), "country": card["country"],
               "funding": card["funding"], "cvc_check": cvc_check}
        if any(find_leaks(str(v)) for v in out.values()):  # keys are ours; values must be clean
            return self._reject("verify", merchant_id, "internal: output failed leak scan")
        return out

    def _count_failure(self, card: dict, now: float) -> None:
        card["cvc_failures"] += 1
        if card["cvc_failures"] >= MAX_CVC_FAILURES:
            card["cvc_failures"], card["locked_until"] = 0, now + LOCK_SECONDS

    # ---- money movement (simulated ledger) ----
    def authorize(self, vid: str, merchant_id: str | None = None) -> dict:
        v = self._take(vid, merchant_id, "authorize")
        card = self.cards[v["card"]]
        with self.lock:  # check-then-deduct is atomic
            if card["balance"] < v["amount"]:
                self._log("authorize", v["merchant"], "issuer_declined")
                return {"status": "issuer_declined", "reason": "insufficient funds"}
            card["balance"] -= v["amount"]
        code = secrets.token_hex(3).translate(LETTERS).upper()
        self._log("authorize", v["merchant"], "succeeded")
        return {"status": "succeeded", "auth_code": code}

    def void(self, vid: str, merchant_id: str | None = None) -> dict:
        v = self._take(vid, merchant_id, "void")
        self._log("void", v["merchant"], "voided")
        return {"status": "voided"}


def _oaep():
    return padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def _expiry_ok(given: str, on_file: str, now: float) -> bool:
    if given != on_file:
        return False
    try:
        mm, yy = given.split("/")
        month, year = int(mm), 2000 + int(yy)
    except ValueError:
        return False
    t = time.gmtime(now)
    return (year, month) >= (t.tm_year, t.tm_mon)


# ---------- HTTP surface ----------

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024  # a verify request is well under 1 KB
HERE = os.path.dirname(os.path.abspath(__file__))
issuer: Issuer | None = None  # built from the environment on first request (tests inject their own)


def issuer_from_env() -> Issuer:
    """The one place the private key and the card-hashing key are loaded: inside the issuer process."""
    global issuer
    if issuer is None:
        issuer = Issuer(private_key=load_or_create_key(KEY_FILE),
                        previous_key=load_or_create_key(PREVIOUS_KEY_FILE) if PREVIOUS_KEY_FILE and os.path.exists(PREVIOUS_KEY_FILE) else None,
                        merchants=parse_merchants(os.environ.get("ISSUER_MERCHANTS", "")),
                        hmac_key=load_or_create_hmac_key(HMAC_KEY_FILE), admin_token=ADMIN_TOKEN)
    return issuer


def _authenticated_merchant() -> str:
    """Every money-moving call must be signed by a registered merchant."""
    return issuer_from_env().authenticate(request.headers.get("X-Merchant-Id", ""), request.headers.get("X-Timestamp", ""),
                               request.headers.get("X-Signature", ""), request.method, request.path,
                               request.get_data(), nonce=request.headers.get("X-Nonce", ""))


@app.after_request
def _headers(resp: Response):
    resp.headers["Access-Control-Allow-Origin"] = MERCHANT_ORIGIN
    resp.headers["Vary"] = "Origin"
    return resp


@app.get("/card-frame")
def card_frame():
    resp = send_from_directory(HERE, "card_frame.html")
    resp.headers["Content-Security-Policy"] = f"frame-ancestors {MERCHANT_ORIGIN}"
    return resp


@app.get("/card-seal.js")
def card_seal_js():
    return send_from_directory(HERE, "card_seal.js", mimetype="application/javascript")


@app.get("/pubkey")
def pubkey():
    iss = issuer_from_env()
    return jsonify({"kid": iss.kid, "spki_b64": iss.pubkey_b64(), "merchant_origin": MERCHANT_ORIGIN})


@app.post("/verify")
def verify():
    try:
        merchant_id = _authenticated_merchant()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    body = request.get_json(force=True)
    try:
        if str(body.get("merchant_id", merchant_id)) != merchant_id:
            return jsonify({"error": "merchant id does not match credential"}), 403
        return jsonify(issuer_from_env().verify(str(body["blob"]), int(body["amount_cents"]), merchant_id))
    except IssuerReject as e:
        return jsonify({"error": str(e)}), 402
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "bad request"}), 400


def _settle(action):
    """authorize / void with the verification id in the body, so it never appears in URL access logs."""
    try:
        merchant_id = _authenticated_merchant()
        vid = str(request.get_json(force=True)["verification_id"])
        return jsonify(action(vid, merchant_id))
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "bad request"}), 400
    except IssuerReject as e:
        return jsonify({"error": str(e)}), 409


@app.post("/authorize")
def authorize():
    return _settle(issuer_from_env().authorize)


@app.post("/void")
def void():
    return _settle(issuer_from_env().void)


@app.get("/audit")
def audit_log():
    """Admin role only: the chained audit log and its head hash."""
    iss = issuer_from_env()
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not iss.admin_token or not hmac.compare_digest(token, iss.admin_token):
        return jsonify({"error": "admin token required"}), 401
    ok, _ = iss.verify_audit()
    return jsonify({"entries": iss.audit[-100:], "chain_ok": ok, "chain_head": audit.head(iss.audit)})


def self_signed_cert(cert_path: str, key_path: str, host: str = "127.0.0.1") -> None:
    """Write a self-signed TLS certificate for the demo. Production uses a CA-issued certificate."""
    import datetime
    import ipaddress
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host)),
                                                        x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256()))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))


def _cert_expired(cert_path: str) -> bool:
    import datetime
    from cryptography import x509
    with open(cert_path, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())
    return cert.not_valid_after_utc <= datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)


if __name__ == "__main__":
    tls_cert, tls_key = os.environ.get("ISSUER_TLS_CERT", ""), os.environ.get("ISSUER_TLS_KEY", "")
    if tls_cert and (not os.path.exists(tls_cert) or _cert_expired(tls_cert)):
        self_signed_cert(tls_cert, tls_key)
    scheme = "https" if tls_cert else "http"
    if not issuer_from_env().merchants:
        print("WARNING: no ISSUER_MERCHANTS configured; every merchant call will be refused (401)")
    print(f"Issuer node on {scheme}://127.0.0.1:4243  (sees card data; key id {issuer.kid}; merchant origin {MERCHANT_ORIGIN})")
    app.run(port=4243, ssl_context=(tls_cert, tls_key) if tls_cert else None)
