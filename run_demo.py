"""Start the merchant node (:4242) with a fresh reviewer token.

    python run_demo.py                              # Stripe TEST keys required (from .env or the shell)
    python run_demo.py --federation local-agent     # decisions run as Flower AgentApps on a local SuperLink
    python run_demo.py --stores store-a,store-b,store-c   # three stores on one node: the fraud-ring demo

The runner mints the reviewer token for this session and prints it; the page asks for it once.
Ctrl-C stops the node.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cardguard import dotenv as _dotenv  # noqa: E402

_dotenv.load()


def main() -> int:
    if "--federation" in sys.argv:
        os.environ["CARDGUARD_FEDERATION"] = sys.argv[sys.argv.index("--federation") + 1]
    if "--stores" in sys.argv:
        os.environ["STORES"] = sys.argv[sys.argv.index("--stores") + 1]
    if not os.environ.get("STRIPE_SECRET_KEY", "").startswith("sk_test_") or \
            not os.environ.get("STRIPE_PUBLISHABLE_KEY", "").startswith("pk_test_"):
        print("Stripe TEST keys required: STRIPE_SECRET_KEY=sk_test_... STRIPE_PUBLISHABLE_KEY=pk_test_... (put them in .env)",
              file=sys.stderr)
        return 2
    reviewer = secrets.token_hex(8)
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "REVIEWER_TOKEN": reviewer,
           "DEMO_CONTROLS": os.environ.get("DEMO_CONTROLS", "1")}
    print("reviewer token (the page asks for it once): %s" % reviewer)
    print("open http://127.0.0.1:4242\n")
    try:
        return subprocess.call([sys.executable, "-m", "cardguard.payment_processing.merchant"], cwd=HERE, env=env)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
