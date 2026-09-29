"""Start the bank attestation node (:4243) and the merchant node (:4242) with fresh credentials.

    python run_demo.py                              # Stripe TEST keys required (from .env or the shell)
    python run_demo.py --federation local-agent     # decisions run as Flower AgentApps on a local SuperLink
    python run_demo.py --stores store-a,store-b,store-c   # three stores on one node: the fraud-ring demo
    python run_demo.py --federation local-agent --stores store-a,store-b,store-c --node-per-store
                                                    # one merchant process AND one SuperNode per store
                                                    # (:4242, :4252, :4262; start scripts/run_supernode.py --index 0/1/2)

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
NODE_PORT_STEP = 10  # merchant ports 4242, 4252, 4262 ... (the bank keeps :4243)


def node_plan(stores: list[str], base_port: int = 4242) -> list[tuple[str, int]]:
    """One merchant process per store: (store, port). Every node lists the others as peers."""
    return [(s, base_port + NODE_PORT_STEP * i) for i, s in enumerate(stores)]


def node_env(base: dict, store: str, port: int, plan: list[tuple[str, int]], card_ref_key: str, bank_secret: str) -> dict:
    """The environment of one store's merchant node: its own id, port, host allowlist, peers, bank secret and
    label files; the network-wide card reference key; never STORES (one store per node)."""
    env = {k: v for k, v in base.items() if k != "STORES"}
    env.update(MERCHANT_ID=store, MERCHANT_PORT=str(port), BANK_SECRET=bank_secret, CARD_REF_KEY=card_ref_key,
               MERCHANT_HOSTS=f"127.0.0.1:{port},localhost:{port},127.0.0.1,localhost",
               MERCHANT_PEERS=",".join(f"{s}=http://127.0.0.1:{p}" for s, p in plan),
               LABELS_FILE=os.path.join(HERE, ".demo", f"labels_{store}.jsonl"),
               REVIEW_AUDIT_FILE=os.path.join(HERE, ".demo", f"review_audit_{store}.jsonl"))
    return env


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
    per_store = "--node-per-store" in sys.argv
    stores = [s.strip() for s in os.environ.get("STORES", "").split(",") if s.strip()] if per_store else []
    if per_store and len(stores) < 2:
        print("--node-per-store needs --stores with at least two stores", file=sys.stderr)
        return 2
    plan = node_plan(stores)
    # The bank gets an allowlisted environment: none of the Stripe, model or reviewer credentials.
    bank_env = {k: v for k, v in os.environ.items()
                if k in {"PATH", "HOME", "LANG", "TMPDIR", "VIRTUAL_ENV", "SYSTEMROOT"} or k.startswith(("LC_", "PYTHON"))}
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("BANK_")},  # never the bank's registry
           "PYTHONUNBUFFERED": "1", "REVIEWER_TOKEN": reviewer, "MERCHANT_ID": merchant_id,
           "DEMO_CONTROLS": os.environ.get("DEMO_CONTROLS", "1"), "BANK_URL": BANK_URL, "BANK_SECRET": bank_secret}
    if per_store:
        secrets_by_store = {s: secrets.token_hex(16) for s, _ in plan}
        card_ref_key = secrets.token_hex(32)  # one reference per card across the whole demo network
        bank_env.update(PYTHONUNBUFFERED="1", BANK_MERCHANTS=",".join(f"{s}:{sec}" for s, sec in secrets_by_store.items()))
        envs = [node_env(env, s, p, plan, card_ref_key, secrets_by_store[s]) for s, p in plan]
    else:
        bank_env.update(PYTHONUNBUFFERED="1", BANK_MERCHANTS=f"{merchant_id}:{bank_secret}")
        envs = [env]
    if preset:
        print("reviewer token: using REVIEWER_TOKEN from the environment (the page asks for it once)")
    else:
        print(f"reviewer token (the page asks for it once): {reviewer}")
    if per_store:
        for s, p in plan:
            print(f"merchant node {s}: http://127.0.0.1:{p}   (its SuperNode: python scripts/run_supernode.py --index {plan.index((s, p))})")
        print("open the first one; the ring scenario checks out at every node\n")
    else:
        print("open http://127.0.0.1:4242\n")
    bank = subprocess.Popen([sys.executable, "-m", "cardguard.bank.node"], cwd=HERE, env=bank_env)
    merchants = []
    try:
        for e in envs:
            merchants.append(subprocess.Popen([sys.executable, "-m", "cardguard.payment_processing.merchant"], cwd=HERE, env=e))
        return merchants[0].wait()
    except KeyboardInterrupt:
        return 0
    finally:
        for proc in [bank, *merchants]:
            proc.terminate()
        for proc in [bank, *merchants]:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
