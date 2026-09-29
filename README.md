# CardGuard - multi-party card-risk decisions where card data never enters any model's context

Built for the Flower Collaborative Agent Hackathon (Stanford, Sep 29 2026).
Card data stays inside Stripe Elements and the merchant node. Only banded, allowlisted
facts cross the wire; every disclosure and every blocked leak is logged.

## Pieces

| File | What it does |
|---|---|
| guard.py | Allowlist schema, Luhn / expiry / CVV / injection scanner, disclosure ledger |
| fl_data.py | Real transactions: IEEE-CIS (Vesta) split into 5 product verticals, features relative to each vertical, time-based holdout |
| fl.py | Logistic regression + FedAvg in numpy; synthetic 3-merchant data for offline tests, real 5-vertical training when the data is present |
| flower_app.py | Same training as a Flower 1.39 ServerApp + ClientApp: one SuperNode per vertical (5) or per synthetic merchant (3); saves fl_weights.json |
| explain.py | One-line plain-English explanation of each verdict by Flower Endeavor (display only; template fallback) |
| coordinator.py | Jev (TypeSafe System One) + rules; more cautious vote wins, low-confidence approvals go to a human |
| merchant.py | Flask merchant node: Stripe test mode, federated risk band, review queue |
| checkout.html | Checkout page, human review queue, live ledger |

## Setup and run

Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/) (or plain venv + pip).

    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python -r requirements.txt
    source .venv/bin/activate

    python -m pytest -q            # 46 tests, offline (Jev, Endeavor and Stripe faked/mocked)
    python fl.py                   # federated vs local-only tables (synthetic, and real if present)
    python flower_app.py           # federated training on Flower, one SuperNode per merchant, writes fl_weights.json
    python merchant.py             # http://127.0.0.1:4242  (MOCK mode without keys)

Real data (optional, recommended): see data/README.md. Put IEEE-CIS `train_transaction.csv`
in `data/`; `fl_data.py` splits it into the five ProductCD verticals (W, C, R, H, S) as five
merchants and `flower_app.py` trains across them. Without it, training and tests use synthetic data.

Keys (all optional; each piece falls back when missing). Env vars only, never in the repo:

    export STRIPE_SECRET_KEY=sk_test_...   STRIPE_PUBLISHABLE_KEY=pk_test_...   # refuses sk_live_
    export TYPESAFE_API_KEY=...            # Jev; without it, rules decide alone
    export JEV_MODEL=jev-x.y.z             # optional: pin the Jev version
    export ENDEAVOR_BASE_URL=... ENDEAVOR_API_KEY=...   # or FLWR_RUNTIME_* inside an AgentApp

## Headline numbers

**Real data, IEEE-CIS (590,540 transactions, 3.5% fraud), `python fl.py`.** Five product
verticals act as five merchants. AUC on each vertical's held-out *later* transactions, from a
7-feature logistic regression (the same features merchant.py computes at checkout):

| Model | W | C | R | H | S | all verticals |
|---|---|---|---|---|---|---|
| Any single vertical alone (best) | 0.654 | 0.624 | 0.668 | 0.609 | 0.361 | 0.678 |
| **Federated (Flower, FedAvg, 5 SuperNodes)** | 0.651 | 0.621 | 0.552 | 0.526 | 0.348 | **0.730** |
| Centralized (pooled data, not allowed) | 0.653 | 0.625 | 0.553 | 0.517 | 0.351 | 0.732 |

Federated matches pooling the data (0.730 vs 0.732) and beats every single-vertical model on
the cross-vertical test, without any vertical seeing another's rows. Absolute AUC is modest
because only 7 coarse, wire-safe features are used; S drifts between its train and test windows.

**Synthetic data (offline tests).** Three merchants, each mostly seeing one fraud type; share of
each type caught at p >= 0.5:

| Model | high-ticket | cross-border | card testing | legit flagged |
|---|---|---|---|---|
| Electronics merchant alone | 92% | 33% | 21% | 3.8% |
| Travel merchant alone | 41% | 95% | 18% | 4.8% |
| Digital-goods merchant alone | 6% | 5% | 99% | 0.9% |
| **Federated (Flower, FedAvg)** | **86%** | **84%** | **97%** | 4.5% |
| Centralized (pooled data, not allowed) | 86% | 84% | 97% | 4.5% |

## Decision logic

- Jev sees only the banded facts plus one-line meanings. Never tokens, payment IDs or customer text
  (Jev is not adversary-hardened, so attacker-controlled content must not reach it).
- A failed card security-code check (cvc_check=fail) is a hard decline before any vote.
- Rules and Jev each vote approve / step_up / decline; the more cautious one wins.
- An approval with Jev confidence < 0.8 becomes step_up (human review).
- A malformed or failed Jev answer is retried once; then rules decide alone. The verdict records which.
- Amount bands are relative to the merchant's own order history (below median / up to p90 / above),
  seeded from its vertical's quantiles and updated only by completed charges, so "high" means
  unusual for this merchant and the wire never carries a dollar amount.

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
