# CardGuard - multi-party card-risk decisions where card data never enters any model's context

Built for the Flower Collaborative Agent Hackathon (Stanford, Sep 29 2026).
Card data stays inside Stripe Elements and the merchant node. Only banded, allowlisted
facts cross the wire; every disclosure and every blocked leak is logged.

## Pieces

| File | What it does |
|---|---|
| guard.py | Allowlist schema, Luhn / expiry / CVV / injection scanner, disclosure ledger |
| fl.py | Synthetic transactions for 3 merchants (each sees a different fraud type), logistic regression, FedAvg |
| flower_app.py | Same training as a Flower 1.39 ServerApp + ClientApp (3 SuperNodes), saves fl_weights.npy |
| explain.py | One-line plain-English explanation of each verdict by Flower Endeavor (display only; template fallback) |
| coordinator.py | Jev (TypeSafe System One) + rules; more cautious vote wins, low-confidence approvals go to a human |
| merchant.py | Flask merchant node: Stripe test mode, federated risk band, review queue |
| checkout.html | Checkout page, human review queue, live ledger |

## Setup and run

Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/) (or plain venv + pip).

    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python -r requirements.txt
    source .venv/bin/activate

    python -m pytest -q            # 37 tests, offline (Jev, Endeavor and Stripe faked/mocked)
    python fl.py                   # federated vs local-only catch-rate table
    python flower_app.py           # federated training on Flower (3 SuperNodes), writes fl_weights.npy
    python merchant.py             # http://127.0.0.1:4242  (MOCK mode without keys)

Keys (all optional; each piece falls back when missing). Env vars only, never in the repo:

    export STRIPE_SECRET_KEY=sk_test_...   STRIPE_PUBLISHABLE_KEY=pk_test_...   # refuses sk_live_
    export TYPESAFE_API_KEY=...            # Jev; without it, rules decide alone
    export JEV_MODEL=jev-x.y.z             # optional: pin the Jev version
    export ENDEAVOR_BASE_URL=... ENDEAVOR_API_KEY=...   # or FLWR_RUNTIME_* inside an AgentApp

## Headline numbers (synthetic data, python fl.py)

Share of each fraud type caught at p >= 0.5:

| Model | high-ticket | cross-border | card testing | legit flagged |
|---|---|---|---|---|
| Electronics merchant alone | 91% | 39% | 50% | 0.6% |
| Travel merchant alone | 66% | 96% | 61% | 4.9% |
| Digital-goods merchant alone | 22% | 25% | 98% | 0.2% |
| **Federated (Flower, FedAvg)** | **91%** | **89%** | **98%** | 2.4% |
| Centralized (pooled data, not allowed) | 91% | 89% | 98% | 2.5% |

Federated matches pooling all the data, without pooling it.

## Decision logic

- Jev sees only the banded facts plus one-line meanings. Never tokens, payment IDs or customer text
  (Jev is not adversary-hardened, so attacker-controlled content must not reach it).
- Rules and Jev each vote approve / step_up / decline; the more cautious one wins.
- An approval with Jev confidence < 0.8 becomes step_up (human review).
- Jev down or no key -> rules decide alone; the verdict records which.

## Demo script

1. Preset normal, card 4242 4242 4242 4242 -> approved, payment lands in the Stripe test dashboard.
2. Preset risky ($900, buyer in DE) -> federated model says high, goes to the human queue -> Decline -> no charge.
3. Card 4000 0000 0000 0002 -> agents approve, processor declines.
4. Tick "Compromise the merchant agent" -> 3 leak attempts blocked, "card numbers seen by coordinator" stays 0.
5. Show the federated table above.

## Still to do on the day

- Run merchant / coordinator as Flower AgentApps on SuperGrid instead of in-process calls.
- Verify with real keys: one Stripe test purchase, one live Jev call.
- Publish to Flower Hub, push to GitHub, record a backup video.

Say "shrinks PCI scope" / "card data never enters a model's context", never "PCI compliant".
Test card numbers and synthetic data only.
