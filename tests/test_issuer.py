"""Issuer node: sealed-card verification, replay/tamper checks, authorization. No card data leaks."""
import json
import os
import shutil
import subprocess

import pytest

from cardguard.payment_processing import issuer as issuer_mod
from cardguard.decision.guard import TOKEN_RE, find_leaks
from cardguard.payment_processing.issuer import Issuer, IssuerReject

VISA = {"p": "4242424242424242", "e": "12/30", "c": "123"}


def seal(node, pan="4242424242424242", exp="12/30", cvc="123", amount=2000, merchant="W", nonce=None, ts=None):
    return node.seal({"p": pan, "e": exp, "c": cvc, "a": amount, "m": merchant,
                      "n": nonce or os.urandom(12).hex(), "t": ts if ts is not None else node.clock()})


@pytest.fixture
def node():
    return Issuer(merchants={"W": "wsecret", "C": "csecret"}, admin_token="admin-t")


def signed(secret, method, path, body=b"", mid="W", ts=None):
    from cardguard.payment_processing.merchant import sign_request
    h = sign_request(secret, method, path, body, ts)
    h["X-Merchant-Id"] = mid
    return h


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_browser_seal_script_round_trips_into_python_verify(node):
    """The most important test: card_seal.js (WebCrypto, as in the browser) -> Issuer.verify()."""
    script = ("const {seal}=require('./card_seal.js');"
              "seal(process.argv[1], {pan:'4242 4242 4242 4242', exp:'12/30', cvc:'123', amount_cents:2000, merchant_id:'W'}, process.argv[2])"
              ".then(b=>process.stdout.write(b))")
    blob = subprocess.run(["node", "-e", script, node.pubkey_b64(), node.kid], capture_output=True, text=True,
                          check=True, cwd=os.path.dirname(os.path.abspath(issuer_mod.__file__))).stdout.strip()
    assert "4242" not in blob and blob.startswith(node.kid + ".")
    out = node.verify(blob, 2000, "W")
    assert out["cvc_check"] == "pass" and out["country"] == "US" and TOKEN_RE.match(out["card_ref"])


def test_verify_returns_only_non_sensitive_facts(node):
    out = node.verify(seal(node), 2000, "W")
    assert set(out) == {"verification_id", "card_ref", "country", "funding", "cvc_check"}
    assert not any(find_leaks(str(v)) for v in out.values()) and "4242" not in json.dumps(out)
    assert not any(find_leaks(str(v)) for e in node.audit for k, v in e.items() if k not in {"hash", "prev"})


def test_same_card_same_ref_everywhere(node):
    a = node.verify(seal(node, merchant="W"), 2000, "W")["card_ref"]
    b = node.verify(seal(node, merchant="C"), 2000, "C")["card_ref"]
    c = node.verify(seal(node, pan="5555555555554444", exp="11/29", cvc="456"), 2000, "W")["card_ref"]
    assert a == b != c


def test_replay_rejected(node):
    blob = seal(node)
    node.verify(blob, 2000, "W")
    with pytest.raises(IssuerReject, match="replay"):
        node.verify(blob, 2000, "W")


def test_amount_and_merchant_tampering_rejected(node):
    with pytest.raises(IssuerReject, match="amount"):
        node.verify(seal(node, amount=2000), 200000, "W")
    with pytest.raises(IssuerReject, match="merchant"):
        node.verify(seal(node, merchant="W"), 2000, "evil-shop")


def test_unknown_card_wrong_expiry_stale_seal_garbage(node):
    with pytest.raises(IssuerReject, match="unknown card"):
        node.verify(seal(node, pan="4111111111111111"), 2000, "W")
    with pytest.raises(IssuerReject, match="expiry"):
        node.verify(seal(node, exp="01/31"), 2000, "W")
    with pytest.raises(IssuerReject, match="stale"):
        node.verify(seal(node, ts=node.clock() - 3600), 2000, "W")
    with pytest.raises(IssuerReject, match="undecryptable"):
        node.verify(node.kid + ".bm90IGEgYmxvYg==", 2000, "W")
    with pytest.raises(IssuerReject, match="unknown key id"):
        node.verify(Issuer().seal({"p": "4242424242424242"}), 2000, "W")   # sealed for a different issuer key


def test_expired_card_rejected_by_clock():
    node = Issuer(clock=lambda: 4102444800.0)   # 2100-01-01: every test card has expired
    with pytest.raises(IssuerReject, match="expiry"):
        node.verify(seal(node), 2000, "W")


def test_wrong_cvc_is_a_fact_not_a_reject_until_the_card_locks(node):
    out = node.verify(seal(node, cvc="999"), 2000, "W")
    assert out["cvc_check"] == "fail"
    node.verify(seal(node, cvc="998"), 2000, "W")
    node.verify(seal(node, cvc="997"), 2000, "W")
    with pytest.raises(IssuerReject, match="card locked"):      # enumeration defence
        node.verify(seal(node, cvc="123"), 2000, "W")
    other = node.verify(seal(node, pan="5555555555554444", exp="11/29", cvc="456"), 2000, "W")
    assert other["cvc_check"] == "pass"                           # only that card is locked


def test_blob_size_cap_and_merchant_rate_limit(node, monkeypatch):
    with pytest.raises(IssuerReject, match="too large"):
        node.verify(node.kid + "." + "A" * 5000, 2000, "C")   # every attempt counts toward the caller's limit
    monkeypatch.setattr(issuer_mod, "VERIFY_RATE_PER_MINUTE", 2)
    node.verify(seal(node), 2000, "W"); node.verify(seal(node), 2000, "W")
    with pytest.raises(IssuerReject, match="rate limit"):
        node.verify(seal(node), 2000, "W")
    node.verify(seal(node, merchant="C"), 2000, "C")             # another merchant (1 attempt so far) is unaffected


def test_hmac_key_file_persists_card_references(tmp_path):
    path = str(tmp_path / "hmac.key")
    k1 = issuer_mod.load_or_create_hmac_key(path)
    assert oct(os.stat(path).st_mode)[-3:] == "600" and issuer_mod.load_or_create_hmac_key(path) == k1
    a = Issuer(hmac_key=k1, merchants={"W": "s"}); b = Issuer(hmac_key=k1, merchants={"W": "s"})
    assert a.card_ref("4242424242424242") == b.card_ref("4242424242424242")   # survives a restart


def test_authorize_once_then_reuse_fails_and_zero_balance_declines(node):
    vid = node.verify(seal(node), 2000, "W")["verification_id"]
    assert node.authorize(vid)["status"] == "succeeded"
    with pytest.raises(IssuerReject, match="already used"):
        node.authorize(vid)
    with pytest.raises(IssuerReject):
        node.void(vid)
    vid0 = node.verify(seal(node, pan="4000000000000002", exp="09/29", cvc="321"), 2000, "W")["verification_id"]
    assert node.authorize(vid0)["status"] == "issuer_declined"
    vid2 = node.verify(seal(node), 2000, "W")["verification_id"]
    assert node.void(vid2)["status"] == "voided"


def test_merchant_scoping_in_core(node):
    vid = node.verify(seal(node, merchant="W"), 2000, "W")["verification_id"]
    with pytest.raises(IssuerReject, match="another merchant"):
        node.authorize(vid, "C")
    with pytest.raises(IssuerReject, match="another merchant"):
        node.void(vid, "C")
    assert node.authorize(vid, "W")["status"] == "succeeded"


def test_key_rotation_and_unknown_key_id(tmp_path):
    old = issuer_mod.load_or_create_key(str(tmp_path / "old.pem"))
    new = issuer_mod.load_or_create_key(str(tmp_path / "new.pem"))
    assert oct(os.stat(tmp_path / "new.pem").st_mode)[-3:] == "600"
    assert issuer_mod.load_or_create_key(str(tmp_path / "new.pem")).private_numbers() == new.private_numbers()
    node = Issuer(private_key=new, previous_key=old, merchants={"W": "s"})
    fields = lambda: {"p": VISA["p"], "e": VISA["e"], "c": VISA["c"], "a": 2000, "m": "W", "n": os.urandom(12).hex(), "t": node.clock()}
    assert node.verify(node.seal(fields(), key=old), 2000, "W")["cvc_check"] == "pass"   # old key still accepted
    with pytest.raises(IssuerReject, match="unknown key id"):
        node.verify(node.seal(fields(), key=issuer_mod.load_or_create_key()), 2000, "W")


def test_audit_log_is_hash_chained_and_tamper_evident(node):
    node.verify(seal(node), 2000, "W")
    with pytest.raises(IssuerReject):
        node.verify(seal(node, pan="4111111111111111"), 2000, "W")
    ok, bad = node.verify_audit()
    assert ok and bad is None and node.audit[1]["prev"] == node.audit[0]["hash"]
    node.audit[0]["status"] = "rejected"        # rewrite history
    assert node.verify_audit() == (False, 0)


def test_http_surface_requires_signed_merchant_and_admin_token(monkeypatch):
    fresh = Issuer(merchants={"W": "wsecret"}, admin_token="admin-t")
    monkeypatch.setattr(issuer_mod, "issuer", fresh)
    c = issuer_mod.app.test_client()
    pub = c.get("/pubkey").get_json()
    assert pub["spki_b64"] == fresh.pubkey_b64() and pub["kid"] == fresh.kid
    body = json.dumps({"blob": seal(fresh), "amount_cents": 2000, "merchant_id": "W"}).encode()
    post = lambda path, data, headers=None: c.post(path, data=data, content_type="application/json", headers=headers or {})
    assert post("/verify", body).status_code == 401                                                        # unsigned
    assert post("/verify", body, signed("wrong", "POST", "/verify", body)).status_code == 401               # bad secret
    assert post("/verify", body, signed("wsecret", "POST", "/verify", body, ts=int(fresh.clock()) - 3600)).status_code == 401  # stale
    res = post("/verify", body, signed("wsecret", "POST", "/verify", body))
    assert res.status_code == 200 and res.headers["Access-Control-Allow-Origin"] == issuer_mod.MERCHANT_ORIGIN
    vid = res.get_json()["verification_id"]
    auth_body = json.dumps({"verification_id": vid}).encode()
    assert post("/authorize", auth_body, signed("wsecret", "POST", "/authorize", auth_body)).get_json()["status"] == "succeeded"
    assert post("/authorize", auth_body, signed("wsecret", "POST", "/authorize", auth_body)).status_code == 409
    bad = json.dumps({"blob": "zz", "amount_cents": 1, "merchant_id": "W"}).encode()
    assert post("/verify", bad, signed("wsecret", "POST", "/verify", bad)).status_code == 402
    assert c.get("/audit").status_code == 401
    aud = c.get("/audit", headers={"Authorization": "Bearer admin-t"}).get_json()
    assert aud["chain_ok"] and aud["entries"][-1]["event"] == "verify"
    frame = c.get("/card-frame")
    assert frame.status_code == 200 and "frame-ancestors" in frame.headers["Content-Security-Policy"]
    assert c.get("/card-seal.js").status_code == 200


def test_self_signed_tls_certificate(tmp_path):
    from cryptography import x509
    issuer_mod.self_signed_cert(str(tmp_path / "c.crt"), str(tmp_path / "c.key"))
    cert = x509.load_pem_x509_certificate((tmp_path / "c.crt").read_bytes())
    assert "127.0.0.1" in str(cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value)
    assert oct(os.stat(tmp_path / "c.key").st_mode)[-3:] == "600"
