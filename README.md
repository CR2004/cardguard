# CardGuard - multi-party card-risk decisions where card data never enters any model's context

Built for the Flower Collaborative Agent Hackathon (Stanford, Sep 29 2026).

Several parties' agents (merchant, coordinator, issuer) make one card-payment risk decision
together. The buyer types the card into a frame served by the issuer node, which seals it with the
issuer's public key; the merchant forwards the ciphertext unread and receives back only
non-sensitive facts. Everything that crosses a node boundary is one of eight banded, allowlisted facts.
Every disclosure and every blocked leak is logged, a human approves anything risky, and the
final verdict is computed in code: models vote or explain, they never decide alone. A fraud
model is trained across merchants with Flower (FedAvg) so that only weights ever leave a node.

## Layout

| Path | What it does |
|---|---|
| cardguard/decision/guard.py | Allowlist schema (8 facts, closed vocabularies), Luhn / expiry / CVV / injection scanner, disclosure ledger |
| cardguard/decision/coordinator.py | Rules + Jev vote; hard decline on a failed CVC check; retry-then-fallback; receiving-side `Verifier` |
| cardguard/decision/explain.py | One-sentence explanation by Flower Endeavor from the verdict and its cited signals only; template fallback |
| cardguard/data/ieee_cis.py | Real transactions (IEEE-CIS via Kaggle) split into 5 product verticals; 9 features relative to each vertical; time holdout |
| cardguard/training/fl.py | Logistic regression + FedAvg in numpy; synthetic 3-merchant data for offline tests; real 5-vertical report |
| cardguard/training/flower_app.py | Same training as a Flower 1.39 ServerApp + ClientApp, one SuperNode per merchant; writes fl_weights.json |
| cardguard/payment_processing/issuer.py | Issuer node: decrypts sealed cards, rejects replay / tampering / bad expiry, returns facts, authorizes or voids |
| cardguard/payment_processing/card_frame.html + card_seal.js | Issuer-served card frame; seals the card with WebCrypto RSA-OAEP-SHA256 |
| cardguard/payment_processing/merchant.py | Merchant node: forwards the blob unread, banded facts, federated risk band, ledger rate limits, review queue, attack modes |
| cardguard/payment_processing/stripe_processor.py | Stripe test mode behind the same three calls (verify / authorize / void): a real processor moves the money, the layer above does not change |
| cardguard/payment_processing/agent_llm.py | Deliberately model-driven merchant agent for the live injection demo; contained by the guard and an integrity check |
| cardguard/payment_processing/checkout.html | Checkout page: issuer iframe, presets, attack picker, gift message, review queue, live ledger, ciphertext box |
| cardguard/agentapp/agent_app.py | The Flower AgentApp: coordinator role on the SuperLink asks over Grid, merchant role on a SuperNode answers with guarded facts |
| cardguard/agentapp/dispute_agent.py | Dispute evidence agent: assembles what the node may say about a charged-back payment and drafts a response for a human to approve |
| cardguard/decision/network.py | The network agent's fact: the same card at several merchants within ten minutes, persisted across Flower runs |
| cardguard/training/retrain.py, join.py | Self-improving loop: human reviews and chargebacks become labels, one federated round spreads the lesson; one command joins a node |
| cardguard/training/privacy.py | Differential privacy on the federated round (Flower server-side clipping + RDP accountant) with the spent budget |
| cardguard/agentapp/launch.py | Starts one decision as a Flower run via the SuperLink Control API and reads the verdict back |
| run_demo.py | Starts the issuer (:4243) and the merchant (:4242) together; `--federation local-agent` decides over Flower |
| tests/ | Offline tests. Jev, Endeavor and the LLM are faked; the issuer runs in-process; real-data tests skip without the CSV |

## Setup and run

Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/) (or plain venv + pip). Node 22+ only for one test.

    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python -r requirements.txt
    source .venv/bin/activate

    python -m pytest -q                        # all tests, offline
    python -m cardguard.training.fl            # federated vs local-only tables (synthetic, and real if present)
    python -m cardguard.training.flower_app    # federated training on Flower, one SuperNode per merchant
    python run_demo.py                         # issuer + merchant -> open http://127.0.0.1:4242

Decide over Flower (multi-agent): start a local SuperLink and one SuperNode, then run the demo in
federation mode. Every checkout becomes a Flower run: the coordinator agent on the SuperLink asks
the merchant agent on the SuperNode over Grid, verifies the reply, decides, and the verdict comes
back through the run's event stream.

    # ~/.flwr/config.toml:  [superlink.local-agent]  address = "127.0.0.1:8010"  insecure = true
    flower-superlink --insecure --host 127.0.0.1 --port 8010      # venv bin on PATH
    flower-supernode --insecure --superlink 127.0.0.1:9092 --node-config partition-id=0
    python run_demo.py --federation local-agent
    flwr build                                 # the FAB to publish on Flower Hub

Keys: env vars only, never in the repo. Every model falls back when its key is missing.

    export TYPESAFE_API_KEY=...                        # Jev; without it, rules decide alone (logged)
    export JEV_MODEL=jev-x.y.z                         # optional: pin the Jev version
    export ENDEAVOR_BASE_URL=... ENDEAVOR_API_KEY=...  # or FLWR_RUNTIME_* inside an AgentApp; else template
    export REVIEWER_TOKEN=...                        # human actions on the merchant node; run_demo.py mints one
    export MERCHANT_VERTICAL=W                         # which real vertical seeds this merchant's amount bands
    export LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=...   # only for the live injection demo

Real data (optional, recommended): see datasets/README.md. Put IEEE-CIS `train_transaction.csv` in
`datasets/`; the loader splits it into the five ProductCD verticals (W, C, R, H, S) as five merchants
and the Flower app trains across them. Without it, training and tests use synthetic data.

## The agents

| Agent | Runs on | Sees | Does |
|---|---|---|---|
| Merchant agent | a SuperNode (the merchant's machine) | its own node's guarded facts | answers one purpose-tagged question per decision through the ledger |
| Coordinator agent | the SuperLink | the merchant's reply only | re-guards it, hard-declines a failed CVC, runs rules + Jev, explains, emits the verdict |
| Issuer | its own service | the sealed card | verifies, answers facts, authorizes or voids |
| Human reviewer | the merchant's queue | facts and reasons | approves or declines anything the code will not |

Code drives every Grid call; no model chooses a tool, sees a Grid payload, or decides alone.

## Two demos, one decision layer

    python run_demo.py --processor stripe --federation local-agent   # "it works with a real processor": Stripe test mode moves the money
    python run_demo.py --federation local-agent                       # "behind the scenes": our issuer node, sealed cards, tamper/replay attacks

Same agents, same guard, same ledger, same model. Only who verifies the card and moves the money
changes: `PROCESSOR=stripe` (needs `STRIPE_SECRET_KEY=sk_test_...` and `STRIPE_PUBLISHABLE_KEY=pk_test_...`;
live keys are refused) or the default issuer node. The tamper and replay attacks are only demonstrable
against the issuer, because Stripe binds the payment method itself; the leak attack works on both.

## Collaboration features, each demonstrable live

- **Cross-merchant fraud ring.** Three stores each see one clean $20 purchase of the same card. The
  coordinator, the network agent, sees the same card reference at three stores within ten minutes,
  scores `network_velocity_band=high`, sends the third purchase to a human and alerts all three stores.
  Its memory persists across Flower runs through the run series. (`--stores store-a,store-b,store-c`)
- **Self-improving loop.** Every human review decision and every chargeback becomes a label on the
  merchant's node. "Retrain" runs one federated round across every node (each trains on its own rows
  plus its own labels, weighted) and a local fine-tune on the human labels; the page shows the model
  band for the flagged pattern before and after. Humans teach the agents; the lesson spreads without
  sharing data.
- **Join the network.** `python -m cardguard.training.join --name store-d --source H` registers a
  node; it trains in the next round.
- **Differential privacy.** `FL_DP_NOISE=1.0 python -m cardguard.training.flower_app` clips and
  noises every update with Flower's DP strategy and reports the cumulative (epsilon, delta) from
  Flower's RDP accountant; the page shows the budget spent on the shipped weights.
- **Dispute evidence agent.** On a chargeback, an agent assembles the banded facts, the issuer's
  verify/authorize events and the network view, and drafts the response; a human approves it.

## Assumptions and simplifications, stated plainly

- **The issuer is a simulated bank**: four synthetic test cards with balances. The cryptography,
  replay/tamper checks, credentials and audit around it are real; the card network is not.
- **Synthetic transactions** exist only for the offline tests and for machines without the CSV;
  the shipped model and every headline number come from the real IEEE-CIS data.
- **Fallbacks are not mocks, and they say so**: with no Jev key the rules decide alone
  (`decided_by: rules`); with no model endpoint the explanation and the dispute draft are templates
  (`by: template`). Endeavor's model id `flower-endeavor-v1.0` is unconfirmed.
- **Feature approximations between training and checkout**: training's "country mismatch" is a
  missing or foreign billing country on the platform; checkout's is card country versus buyer country.
  Training's hour of day comes from a time delta with an unknown reference, so "night" is weak (and
  its weight is near zero). The buyer's country at checkout is a demo control; production plugs an
  IP geolocation into `geolocate()`, which today returns the merchant's own country.
- **Hand-set parameters**: the rules weights and thresholds, the velocity cuts (2, 5 purchases),
  the score band cuts (recomputed from held-out quantiles whenever the model changes), the
  human-label weight (50), the 200-charge minimum before a merchant's own amount quantiles replace
  the seed cuts, the ten-minute ring window, lock and rate limits.
- **One SuperNode fronting several stores** is a demo convenience; in production one node is one
  merchant, and the network view keys on the authenticated node id.
- **Differential privacy** is available, not on by default: the shipped weights are the non-DP model
  and the page says so. The DP run's epsilon (about 40 at delta 1e-5 with noise 1.0 over 30 rounds)
  is large; a smaller budget costs accuracy.
- **Stripe mode is tested with a faked SDK only** until test keys are available.

## What is real and what is simulated

Real: the client-side encryption (RSA-OAEP-SHA256 in the browser, decrypted only at the issuer),
the replay and tampering checks, the wire guard and ledger, the fraud agents and their decision, the
federated model and its training on real transactions.
Simulated: the card network and the bank ledger. The issuer node holds four synthetic test cards
with balances instead of talking to a real bank.

## Headline numbers

**Real data, IEEE-CIS (590,540 transactions, 3.5% fraud).** Five product verticals act as five
merchants. AUC on each vertical's held-out *later* transactions (last 20% of the time window):

| Model, 9 wire-safe features | all verticals |
|---|---|
| Hand-written rules (coordinator.py) | 0.70 |
| Best single vertical training alone | 0.74 |
| **Federated (Flower FedAvg, 5 SuperNodes)** | **0.77** |
| Centralized (pooled data, not allowed) | 0.77 |

Federated matches pooling the data and beats any merchant alone, without any vertical seeing
another's rows. What crosses the network per node per round is 10 numbers. Absolute AUC is
modest by design: the model uses only features that can be banded, cited and sent without
leaking. Kaggle entries reach 0.95 on this data with 400 raw columns; the gap is the price of the
boundary. More accuracy comes from richer models on the node (0.85 measured with Vesta's local
count features), never from more bits on the wire. The 8 wire facts form 144 value combinations
over 590k transactions; the smallest group has 9 transactions, so no message singles anyone out.

**Synthetic data (offline tests).** Three merchants, each mostly seeing one fraud type; share of
each type caught at p >= 0.5:

| Model | high-ticket | cross-border | card testing | legit flagged |
|---|---|---|---|---|
| Electronics merchant alone | 93% | 50% | 41% | 2.7% |
| Travel merchant alone | 50% | 94% | 30% | 4.3% |
| Digital-goods merchant alone | 16% | 9% | 99% | 1.3% |
| **Federated (Flower, FedAvg)** | **88%** | **83%** | **98%** | 3.2% |

## Decision logic

- The merchant node computes nine local features (relative amount, country mismatch, funding,
  velocity, first purchase, night, card age, days since previous order) and scores them with the
  federated model. Only the resulting band crosses the wire.
- Amount bands are relative to the merchant's own order history (below median / up to p90 / above),
  seeded from its vertical's real quantiles and moved only by completed charges.
- A failed card security-code check is a hard decline before any vote; an unavailable check scores a point.
- Rules and Jev each vote approve / step_up / decline; the more cautious wins. An approval with
  Jev confidence < 0.8 becomes step_up (human review). A malformed or failed Jev answer is retried
  once, then rules decide alone. The verdict records which.
- Endeavor writes one sentence from the verdict and its recognised cites; display only.

## Security properties, each with a test

- **No attacker byte reaches a model.** Across all 1,296 valid fact combinations, the exact strings
  sent to Jev and Endeavor contain only vocabulary words and fixed code strings (test_boundary.py).
  `decide()` re-guards its input; `explain()` drops unrecognised cites.
- **Fail-closed wire guard.** Unknown key, off-vocabulary value, >40 chars, Luhn-valid digit run,
  expiry, CVV word or malformed token: BLOCKED and logged. The token is letters-only (`tok_[a-p]{16}`).
- **Bounded covert channels.** One disclosure per decision, at most three attempts, ten disclosures
  per token per hour; the coordinator's `Verifier` re-checks every incoming fact set and accepts one
  per decision.
- **Tokens nobody can reverse.** The card reference is an HMAC-SHA256 of the card number under an
  issuer-held key, mapped to letters. Same card, same reference at every merchant; the merchant cannot
  derive it and it can never look like a card number.
- **Integrity, not just confidentiality.** A model-driven agent's draft that passes the guard but
  differs from the code-computed facts is logged as an integrity failure and ignored.
- **Verdicts bound to the node that fetched the facts.** A hostile SuperNode cannot answer for a
  merchant: the merchant accepts a Grid verdict only from its own node and for the named decision;
  two nodes answering one decision means a human decides. Network sightings are keyed by Flower's
  authenticated node id.
- **Human actions need a credential.** Approving, declining, chargebacks, evidence, retraining and
  joining require the reviewer token; checkout is validated, size-capped and rate-limited; public
  views mask card references; the issuer locks a card for 15 minutes after repeated bad CVC or
  expiry guesses and tells the buyer only that the card could not be verified.
- **Nothing to leak.** The merchant node never holds a card number: the issuer's frame seals it in
  the browser, the merchant forwards ciphertext, and the issuer returns a letters-only card reference.
- **Replay and tampering rejected at the issuer.** The seal binds amount, merchant, a nonce and a
  timestamp; a re-sent blob, a changed amount or merchant, or a stale seal is refused before any agent runs.

## Demo script (about four minutes)

1. **Works with a real processor.** `--processor stripe`: $20 with the 4242 test card. The store receives
   a pm_ id; approved via Flower; the PaymentIntent shows in the Stripe test dashboard.
2. **Behind the scenes.** Issuer mode: $900 with the DE debit card 5555 5555 5555 4444 11/29 456. Confirm
   the amount inside the issuer's frame; the store receives ciphertext; the SuperLink log shows the
   coordinator asking the merchant SuperNode; the ledger shows eight words; it lands in the human queue.
   Decline -> the issuer voids.
3. **Fraud ring.** Same card at store-a, store-b, store-c within minutes: two clean approvals, then the
   coordinator's network view turns the third red and alerts all three stores.
4. **Break it.** Wrong CVC -> hard decline, Jev never asked. Attack tamper / replay -> the issuer refuses
   before any agent runs. Attack leak -> three attempts blocked, counter stays at 0.
5. **Humans teach the agents.** Chargeback on an approved payment -> a label on the node -> Retrain ->
   one federated round across five nodes -> the flagged pattern's band goes from low to high.
   "Draft dispute response" -> the evidence agent's draft, for a human to approve.
6. **The network learns, privately.** The real-data table (any merchant alone 0.74, federated 0.77,
   pooled 0.77, hand-written rules 0.70) and the DP run's spent budget.

## Still to do on the day

- Run the decision as Flower AgentApps on SuperGrid (coordinator on the SuperLink, merchant on a SuperNode).
- Verify with real keys: one live Jev call, one Endeavor sentence, one LLM-driven agent run.
- Publish to Flower Hub, push to GitHub, record a backup video.

Say "shrinks PCI scope" / "card data never enters a model's context", never "PCI compliant".
Synthetic test cards and synthetic or licensed research data only.
