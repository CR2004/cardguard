"""Start the bank attestation node (:4243) and the merchant node (:4242) with fresh credentials.

    python run_demo.py                              # Stripe TEST keys required (from .env or the shell)
    python run_demo.py --federation local-agent     # decisions run as Flower AgentApps on a local SuperLink
    python run_demo.py --stores store-a,store-b,store-c   # three stores on one node: the fraud-ring demo

Stripe is the payment rail. The bank node only attests (bands about the cardholder, and round 2's
travel_check); it never sees a card or moves money. The runner uses REVIEWER_TOKEN from .env when one
is set (a memorable demo value that survives restarts), otherwise mints one and prints it; the page
asks for it once. It also mints a merchant-to-bank signing secret. The bank process gets an allowlisted
environment (no Stripe, model or reviewer credentials); the merchant never gets the bank's registry.
Ctrl-C stops both.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cardguard import dotenv as _dotenv  # noqa: E402

BANK_URL = "http://127.0.0.1:4243"
MIN_REVIEWER_TOKEN = 8


def reviewer_token(environ=os.environ) -> tuple[str, bool]:
    """(token, preset): a REVIEWER_TOKEN of at least MIN_REVIEWER_TOKEN characters is honoured;
    anything shorter is refused (a trivial credential guards every human action), unset means mint."""
    preset = environ.get("REVIEWER_TOKEN", "").strip()
    if preset:
        if len(preset) < MIN_REVIEWER_TOKEN:
            raise ValueError(f"REVIEWER_TOKEN must be at least {MIN_REVIEWER_TOKEN} characters")
        return preset, True
    return secrets.token_hex(8), False


def main() -> int:
    _dotenv.load()  # here, not at import: tests import this module and must never see the real .env
    if "--federation" in sys.argv:
        os.environ["CARDGUARD_FEDERATION"] = sys.argv[sys.argv.index("--federation") + 1]
    if "--stores" in sys.argv:
        os.environ["STORES"] = sys.argv[sys.argv.index("--stores") + 1]
    if not os.environ.get("STRIPE_SECRET_KEY", "").startswith("sk_test_") or \
            not os.environ.get("STRIPE_PUBLISHABLE_KEY", "").startswith("pk_test_"):
        print("Stripe TEST keys required: STRIPE_SECRET_KEY=sk_test_... STRIPE_PUBLISHABLE_KEY=pk_test_... (put them in .env)",
              file=sys.stderr)
        return 2
    if not os.path.exists(os.path.join(HERE, "web", "dist", "index.html")):
        print("The UI is not built yet: pnpm --dir web install && pnpm --dir web build", file=sys.stderr)
    merchant_id = os.environ.get("MERCHANT_ID", "cardguard-store")
    try:
        reviewer, preset = reviewer_token()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    bank_secret = secrets.token_hex(16)
    # The bank gets an allowlisted environment: none of the Stripe, model or reviewer credentials.
    bank_env = {k: v for k, v in os.environ.items()
                if k in {"PATH", "HOME", "LANG", "TMPDIR", "VIRTUAL_ENV", "SYSTEMROOT"} or k.startswith(("LC_", "PYTHON"))}
    bank_env.update(PYTHONUNBUFFERED="1", BANK_MERCHANTS=f"{merchant_id}:{bank_secret}")
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("BANK_")},  # never the bank's registry
           "PYTHONUNBUFFERED": "1", "REVIEWER_TOKEN": reviewer, "MERCHANT_ID": merchant_id,
           "DEMO_CONTROLS": os.environ.get("DEMO_CONTROLS", "1"), "BANK_URL": BANK_URL, "BANK_SECRET": bank_secret}
    if preset:
        print("reviewer token: using REVIEWER_TOKEN from the environment (the page asks for it once)")
    else:
        print(f"reviewer token (the page asks for it once): {reviewer}")
    print("open http://127.0.0.1:4242\n")
    bank = subprocess.Popen([sys.executable, "-m", "cardguard.bank.node"], cwd=HERE, env=bank_env)
    try:
        return subprocess.call([sys.executable, "-m", "cardguard.payment_processing.merchant"], cwd=HERE, env=env)
    except KeyboardInterrupt:
        return 0
    finally:
        bank.terminate()
        try:
            bank.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bank.kill()


if __name__ == "__main__":
    sys.exit(main())
