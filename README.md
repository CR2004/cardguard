# CardGuard: card-payment risk decisions by collaborating agents, where card data never enters any model's context

Built for the Flower Collaborative Agent Hackathon (Stanford, Sep 29 2026).

Merchants cannot fight fraud together because the one thing they would have to share is the card.
CardGuard is a decision layer between "card entered" and "money moves": the card goes from Stripe
Elements to Stripe and nobody else ever sees it; the merchant reduces the payment to eight banded
facts; agents on different Flower nodes decide together on those facts; a human approves anything
risky; every disclosure is logged; and a fraud model is trained across merchants with Flower so only
weights ever leave a node. Stripe moves the money. We decide whether it should.

Say "shrinks PCI scope" or "card data never enters a model's context". Never "PCI compliant".

## Status (Sep 29)

**Done and verified live on a laptop:** Stripe test mode as the processor; wire guard and
hash-chained ledger; coordinator (rules + Jev vote, hard CVC decline); Flower AgentApp with
coordinator and merchant roles over Grid, launched from checkout, verdicts bound to the node that
fetched the facts; fraud-ring detection across stores with memory across Flower runs; human review
with a reviewer credential; chargebacks and reviews that retrain the network (federated round +
local fine-tune); join-the-network command; differential privacy with a reported budget; dispute
evidence agent; live prompt-injection demo contained by the guard; real-data training on IEEE-CIS;
119 offline tests; two code reviews (dead code, security) applied.

**Left, in order:** commit and push; Stripe test keys and the Flower model key in `.env`;
`flwr login supergrid` and the SuperGrid run (needs a SuperNode we control); personalisation at
merchant startup; UI polish; Flower Hub publish; backup video; pitch.

## Quick start

Python 3.11 or 3.12, [uv](https://docs.astral.sh/uv/).

    git clone https://github.com/CR2004/cardguard.git && cd cardguard
    uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
    source .venv/bin/activate
    python -m pytest -q                              # offline, seconds
    pnpm --dir web install && pnpm --dir web build   # the investigation UI (web/dist), served by the merchant node

    cp .env.example .env                             # then fill in: STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY (test keys,
                                                     # required), FLWR_MODEL_API_KEY (live explanations), TYPESAFE_API_KEY (Jev)
    python run_demo.py                               # bank attestation :4243 + merchant http://127.0.0.1:4242;
                                                     # prints the reviewer token

**Decisions over Flower** (the multi-agent path), three terminals:

    mkdir -p ~/.flwr && printf '[superlink.local-agent]\naddress = "127.0.0.1:8010"\ninsecure = true\n' > ~/.flwr/config.toml
    python scripts/run_superlink.py                  # SuperLink (Control API :8010, Fleet API :9092)
    python scripts/run_supernode.py                  # the merchant's SuperNode
    python run_demo.py --federation local-agent --stores store-a,store-b,store-c

**Real data** (what the shipped model was trained on): put IEEE-CIS `train_transaction.csv` in
`datasets/` (see datasets/README.md), then `python -m cardguard.data.ieee_cis` and
`python -m cardguard.training.flower_app`. Without it, training and tests use synthetic data.

## How a payment flows

1. **Card entry.** Stripe Elements sends the number, expiry and CVC to Stripe. The store receives a
   payment-method token and posts only that.
2. **Bank attestation (round 1).** The merchant asks the bank attestation node, over a signed local
   channel, about the card's pseudonymous reference and coarse issuing region. The bank answers two
   bands (`issuer_behavior`, `issuer_recent_declines`) from synthetic private cardholder history that
   never leaves it. The bank never sees a card and never moves money; Stripe is the only payment rail.
   The card reference is a keyed hash of Stripe's TEST card fingerprint: a demo correlation, not an
   issuer-network identity protocol.
3. **Facts.** The merchant asks Stripe for the card's metadata and turns it, plus its own history,
   into eight banded facts: amount relative to its own order sizes, country mismatch, funding, CVC
   result, velocity, first sighting, and the federated model's risk band. The model scores nine raw
   local features; only its band leaves the node.
4. **Wire guard.** The facts pass through a ledger that allows one disclosure per decision, limits
   disclosures per card, and rejects unknown keys, off-vocabulary values, oversized strings and
   card-like digit runs. Everything is logged in a hash-chained ledger.
5. **Agents on Flower.** The merchant starts a Flower run. The coordinator agent on the SuperLink asks
   the merchant agent on the SuperNode a purpose-tagged question over Grid; the merchant agent fetches
   the guarded facts from its own node and replies; the coordinator re-guards the reply, adds its own
   fact (how many merchants saw this card in the last ten minutes), runs the rules and Jev's vote in
   code, hard-declines a failed CVC, sends low-confidence approvals to a human, asks Endeavor for one
   sentence, and emits the verdict. The merchant accepts it only from its own node, for its own decision.
6. **Round 2, only on disagreement.** When the store sees a buyer outside the card's country while the
   card checks pass and the bank sees an ordinary cardholder, the coordinator sends one targeted
   question over Grid to the node that answered round 1; that node relays it to the bank, which checks
   its private history locally and returns only `travel_check` (plausible / implausible / unknown).
   `implausible` puts a person in the loop and never declines on its own; the model never sees it; a
   round 2 with no verified answer holds the payment for a person.
7. **Outcome.** Approve confirms a Stripe test-mode PaymentIntent. Step-up waits for a credentialed
   human. Decline voids. Human decisions and chargebacks become labels on the node; "Retrain" runs a
   federated round and a local fine-tune; a dispute agent drafts the chargeback response for a human.

Code drives every Grid call. No model chooses a tool, sees a Grid payload, or decides alone.

## The demo (about four minutes)

1. **A real purchase.** $20 with 4242 4242 4242 4242: the store shows a pm_ token, the SuperLink log
   shows the agents talking, the ledger shows eight words, the PaymentIntent lands in Stripe's dashboard.
2. **Human in the loop.** $900 with 4000 0027 6000 3184 (German card): band high, human queue; decline
   voids, approve charges.
3. **Fraud ring.** The same card at store-a, store-b, store-c within minutes: two clean approvals, then
   the coordinator's network view turns the third red and alerts all three stores.
4. **Break it.** 4000 0000 0000 0101: CVC fails, hard decline, Jev never asked. 4000 0000 0000 0002:
   agents approve, the bank declines. Attack "leak": three attempts blocked, counter stays at 0.
   Model-driven agent + an injection in the gift message: draft blocked, or altered facts logged and ignored.
5. **Humans teach the agents.** Chargeback an approved payment, click Retrain: one federated round
   across five nodes, the flagged pattern's band goes from low to high. "Draft dispute response".
6. **The numbers.** The per-merchant table below and the differential-privacy budget.

## Attacks we demonstrate, and where each is stopped

| Attack (who) | Stopped by | Seen as |
|---|---|---|
| Put the card on the wire (compromised merchant) | Wire guard: closed vocabulary, Luhn scan, length cap | 3 BLOCKED ledger entries, counter stays 0 |
| Prompt injection through customer text (buyer) | The vulnerable agent's draft is guarded, then integrity-checked against code-computed facts | Draft BLOCKED, or "altered X: ignored" |
| Injection into the decision models (anyone) | Structural: Jev and Endeavor receive only vocabulary words; tests enumerate all 1,296 fact combinations | No path exists |
| Slow leak through allowed values (compromised merchant) | One disclosure per decision, three attempts, ten per card per hour, receiving-side Verifier | Rate-limit BLOCKED entries |
| Forge a verdict for another merchant (hostile SuperNode) | Verdict accepted only from the node that fetched the facts; two replies for one decision go to a human | Verdict rejected |
| Poison the network view (hostile SuperNode) | Sightings keyed by Flower's authenticated node id | Alerts name the node |
| Poison retraining, approve your own review (buyer) | Reviewer credential on every human action | 401 |
| Compromised model endpoint (provider) | Jev can only make a decision more cautious; Endeavor is display-only and scanned | Delays, never approves |

Every attack is stopped by a different layer; no single control is load-bearing.

## Accuracy, honestly

IEEE-CIS, 590,540 real transactions, five product verticals as five merchants, nine features a
merchant can compute at checkout, AUC on each merchant's own later transactions:

| Merchant (rows) | Trains alone | Plain FedAvg | FedAvg, then trains locally |
|---|---|---|---|
| W (346k) | 0.719 | 0.710 | 0.723 |
| C (56k) | 0.638 | 0.645 | 0.640 |
| R (32k) | 0.681 | 0.574 | 0.693 |
| H (30k) | 0.584 | 0.527 | 0.591 |
| S (8k) | 0.305 | 0.385 | 0.314 |

Plain FedAvg hurts large verticals (one linear model cannot fit five fraud mixes); training locally
from the federated start removes the loss. Federation pays for small merchants: at 250 rows per
merchant (11 fraud cases), the mean AUC goes from 0.564 alone to 0.587 federated and the worst
merchant from 0.17 to 0.28; above about 1,000 rows a merchant learns as well alone with this model.
The level is bounded by the features, not the model: a lookup table over all feature combinations
tops out at the same place, and a neural net does no better. Kaggle winners reach 0.95 with 400
columns a merchant does not have at checkout. More accuracy comes from richer node-local computation
(0.78 to 0.85 measured), never from more bits on the wire. The ring detection is real code but this
dataset cannot validate it: 69 cross-vertical sightings in 590k rows. Differential privacy is
available (`FL_DP_NOISE=1.0`): epsilon about 40 at delta 1e-5 over 30 rounds, costing 0.015 AUC.

## Layout

| Path | Role |
|---|---|
| cardguard/decision/ | guard (schema, leak scanner, ledger), coordinator (rules, Jev, Verifier), explain (Endeavor), network (ring fact), audit (hash chain), llm (one model client) |
| cardguard/agentapp/ | agent_app (coordinator and merchant roles over Grid), launch (start a run via the SuperLink Control API), dispute_agent |
| cardguard/payment_processing/ | merchant (Flask node), stripe_processor, processor_base, agent_llm (the vulnerable demo agent), trace (the live investigation trace the UI animates) |
| cardguard/bank/ | node (bank attestation: synthetic private history, bands only, signed requests), client (the merchant's side) |
| web/ | the investigation UI: React + Vite + TypeScript; the graph animates only real trace events |
| cardguard/training/ | fl (numpy logistic regression + FedAvg), flower_app (Flower ServerApp/ClientApp), retrain, join, privacy |
| cardguard/data/ieee_cis.py | real data: five verticals, features relative to each, time holdout |
| tests/ | offline; Jev, Endeavor, the LLM and the Stripe SDK are faked; the bank node runs in-process |
| run_demo.py, scripts/ | demo runner; SuperLink and SuperNode launchers |

## Environment (.env, see .env.example)

`STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY` (test keys, required) · `FLWR_MODEL_API_KEY` (live
explanations through Flower; model `Flwrlabs/endeavor-v1.0`) · `TYPESAFE_API_KEY` (Jev) ·
`LLM_BASE_URL/LLM_API_KEY/LLM_MODEL` (direct calls for the injection demo and dispute drafts) ·
`MERCHANT_VERTICAL` · `STORES` · `FL_DP_NOISE`, `FL_DP_CLIP`, `FL_ROBUST` · `DEMO_CONTROLS` (set by
run_demo: page may choose country, hour, attack, agent mode; unset = production behaviour).

## Assumptions, stated plainly

Stripe test mode is real Stripe with no real money. Synthetic data is used only by offline tests.
Fallbacks announce themselves: `decided_by: rules` without Jev, `by: template` without a model
endpoint. The buyer's country is a demo control; production plugs IP geolocation into `geolocate()`.
Rules weights, band cut-offs, velocity cuts, the human-label weight and the ring window are hand-set.
Several stores on one node is a demo convenience; in production one node is one merchant.

Traps: port 8000 may be taken (the SuperLink uses 8010); `flwr run` refuses AgentApps without a
prompt (use run_demo.py or `cardguard.agentapp.launch`); the SuperLink needs the venv on PATH (the
scripts do this); every pin in pyproject.toml must resolve with flwr's own pins, since the AgentApp
runtime env is rebuilt from it on every run; nothing under datasets/, .demo/ or .env is committed.

License: MIT. Data: IEEE-CIS under the Kaggle competition rules (research use).
