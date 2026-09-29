"""Start the issuer node (:4243) and the merchant node (:4242) together.

    python run_demo.py          # then open http://127.0.0.1:4242
    python run_demo.py --tls    # issuer on https with a self-signed certificate (browser will warn once)
    python run_demo.py --federation local-agent   # decisions run as Flower AgentApps on a local SuperLink
    python run_demo.py --processor stripe         # Stripe test mode moves the money (needs sk_test_/pk_test_ keys)
    python run_demo.py --stores store-a,store-b,store-c   # three stores on one node: the fraud-ring demo

The runner mints a merchant credential and an admin token for this session and hands them to
both processes; the issuer's RSA key is kept in .demo/issuer_key.pem (0600) so it survives restarts.
Localhost is a secure context, so WebCrypto in the card frame works over plain http here.
Ctrl-C stops both.
"""
from __future__ import annotations

import os
import secrets
import signal
import ssl
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, ".demo")


def wait_for(url: str, seconds: float = 15, cafile: str | None = None) -> bool:
    if not url.startswith(("http://", "https://")):
        raise ValueError("only http(s) URLs are polled")
    deadline = time.time() + seconds
    ctx = ssl.create_default_context(cafile=cafile) if cafile else None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1, context=ctx)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def main() -> int:
    tls = "--tls" in sys.argv
    if "--federation" in sys.argv:  # decide over Flower Grid instead of in-process
        os.environ["CARDGUARD_FEDERATION"] = sys.argv[sys.argv.index("--federation") + 1]
    if "--stores" in sys.argv:       # one merchant node fronting several stores (fraud-ring demo)
        os.environ["STORES"] = sys.argv[sys.argv.index("--stores") + 1]
    if "--processor" in sys.argv:   # "issuer" (default, behind-the-scenes demo) or "stripe" (test keys required)
        os.environ["PROCESSOR"] = sys.argv[sys.argv.index("--processor") + 1]
        if os.environ["PROCESSOR"] == "stripe" and not os.environ.get("STRIPE_SECRET_KEY", "").startswith("sk_test_"):
            print("PROCESSOR=stripe needs STRIPE_SECRET_KEY=sk_test_... and STRIPE_PUBLISHABLE_KEY=pk_test_...", file=sys.stderr)
            return 2
    os.makedirs(STATE, exist_ok=True)
    merchant_id, secret, admin, reviewer = "cardguard-store", secrets.token_hex(16), secrets.token_hex(16), secrets.token_hex(8)
    # Two processes, two environments: the merchant never receives the issuer's key paths, the
    # merchant registry, or the admin token; the issuer never receives the merchant secret or reviewer token.
    shared = {k: v for k, v in os.environ.items() if not k.startswith(("ISSUER_", "MERCHANT_", "REVIEWER_"))}
    shared["PYTHONUNBUFFERED"] = "1"
    issuer_url = os.environ.get("ISSUER_URL", ("https" if tls else "http") + "://127.0.0.1:4243")
    tls_files = {"ISSUER_TLS_CERT": os.path.join(STATE, "issuer_tls.crt"), "ISSUER_TLS_KEY": os.path.join(STATE, "issuer_tls.key")} if tls else {}
    issuer_env = {**shared, "MERCHANT_ORIGIN": "http://127.0.0.1:4242",
                  "ISSUER_KEY_FILE": os.path.join(STATE, "issuer_key.pem"),
                  "ISSUER_HMAC_KEY_FILE": os.path.join(STATE, "issuer_hmac.key"),
                  "ISSUER_MERCHANTS": f"{merchant_id}:{secret}", "ISSUER_ADMIN_TOKEN": admin, **tls_files}
    merchant_env = {**shared, "ISSUER_URL": issuer_url, "MERCHANT_ID": merchant_id, "MERCHANT_SECRET": secret,
                    "REVIEWER_TOKEN": reviewer, "DEMO_CONTROLS": os.environ.get("DEMO_CONTROLS", "1"),
                    **({"ISSUER_CA_FILE": tls_files["ISSUER_TLS_CERT"]} if tls else {})}
    for k in ("CARDGUARD_FEDERATION", "STORES", "PROCESSOR", "MERCHANT_VERTICAL", "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY"):
        if os.environ.get(k):
            merchant_env[k] = os.environ[k]
    env = merchant_env  # for the messages below
    procs = []
    try:
        procs.append(subprocess.Popen([sys.executable, "-m", "cardguard.payment_processing.issuer"], cwd=HERE, env=issuer_env))
        if not wait_for(issuer_url + "/pubkey", cafile=merchant_env.get("ISSUER_CA_FILE")):
            print("issuer did not start", file=sys.stderr)
            return 1
        procs.append(subprocess.Popen([sys.executable, "-m", "cardguard.payment_processing.merchant"], cwd=HERE, env=merchant_env))
        if not wait_for("http://127.0.0.1:4242/config"):
            print("merchant did not start", file=sys.stderr)
            return 1
        print("\nCardGuard demo up: open http://127.0.0.1:4242  (issuer at %s). Ctrl-C to stop." % env["ISSUER_URL"])
        print("issuer audit: curl -H 'Authorization: Bearer %s' %s/audit" % (admin, env["ISSUER_URL"]))
        print("reviewer token (the page asks for it once): %s\n" % reviewer)
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    sys.exit(main())
