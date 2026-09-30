# CardGuard

**Card-payment risk decisions by collaborating agents on Flower, where card data never enters any model's context.**

Built for the Flower Collaborative Agent Hackathon, Stanford, September 29 2026.

- Flower Hub app: `@ac007/cardguard` (this repository is the app: `flwr app publish .`)
- Four-minute demo walkthrough: [DEMO.md](DEMO.md)
- How the models are trained, in plain words: [TRAINING.md](TRAINING.md)

## The problem

Fraud is a network problem. A card-testing ring looks like one ordinary purchase at each store it hits, and a stolen card
looks normal to a merchant who has never seen its owner. The parties who could catch it together, several merchants and
the card's bank, cannot pool what they know, because the one thing that would link their records is the card itself.
Sharing card numbers with each other, or with an AI model, is exactly what payment security forbids.

## What CardGuard does

CardGuard is a decision layer between "card entered" and "money moves". Several parties' agents decide one payment
together, and none of them ever sees the card:

- **The card goes from Stripe Elements straight to Stripe.** The merchant receives a `pm_...` payment-method id, never
  the number, expiry or CVC. Stripe is the only payment rail and moves the money.
- **Each party reduces what it knows to banded facts.** "Amount: high for this store", "bank sees an ordinary
  cardholder", "card seen at 3 stores in 10 minutes". Only words from a closed vocabulary cross a node boundary, and a
  wire guard refuses anything else.
- **A coordinator agent on Flower asks the questions**, re-verifies every answer, and asks one follow-up question only
  when two parties disagree.
- **Fixed rules in code make the decision.** Models vote or explain; they never decide alone.
- **A person decides the uncertain cases**, and each human decision retrains the fraud model on that node at once.
- **Every disclosure and every blocked leak is logged** in a hash-chained ledger that anyone can re-verify.

This shrinks PCI scope rather than certifying anything: it is the same shape as the hosted card fields processors
already use. What we add is the multi-agent fraud decision around it, a fraud model trained across merchants with
federated learning, and a privacy boundary you can verify.

## Humans and agents, together

We are building the part of a payment that happens between "the buyer pressed Pay" and "the money moved": a shared
fraud decision. Its job is to stop fraudulent charges before they happen, and to learn from the ones that get through,
without any party handing over card data.

The work is split by what each side is good at.

**Agents gather evidence, at machine speed, each from its own private data.** When a checkout starts, the merchant agent
describes the purchase in bands ("high for this store", "first time we see this card"), the bank node describes its
cardholder ("an ordinary customer", "no recent declines"), and the coordinator agent adds what only it can see across
merchants ("this card was at two other stores in the last ten minutes"). If the store and the bank disagree, say the
buyer is abroad but the cardholder looks ordinary, the coordinator asks the bank one more question instead of guessing.
All of this takes seconds, and no agent ever learns another party's raw data.

**Code decides the clear cases.** Fixed rules turn the evidence into approve, decline or hold. A clean purchase is charged
without anyone waiting. A card that fails its security check is declined on the spot. A model can vote to be more careful,
but it can never approve on its own, and an approval the model is unsure about is not allowed through automatically.

**People decide the uncertain cases.** When the evidence is mixed, the payment is held and nothing is charged. A
credentialed reviewer sees why it was held, in the gate's own words, and approves or declines it. The reviewer sees the
same bands the agents saw, never the card.

**People teach the agents.** Every human decision becomes a training label on that merchant's node, and the node's fraud
model updates inside the same request, so the next checkout already reflects it. When a fraudulent charge slips through
and comes back as a chargeback, it becomes a label the same way, and a dispute agent drafts the chargeback response for a
person to send. A federated round then spreads the lesson to the other merchants through model weights, never through
transactions.

**Everyone can check the work.** Every fact that crossed a boundary, and every attempt that was blocked, is in a
hash-chained ledger. The screen shows, for each payment, how many card numbers were shared (zero), which party
contributed which fact, and which rule decided.

**Why not just Stripe Radar?** Radar decides with what Stripe sees. CardGuard adds what Stripe does not: the merchant's own
history, the issuing bank's view of its cardholder, and sightings across merchants, combined without sharing a card
number, a raw history or a model score. It runs beside the processor, not instead of it.

## The agents

| Agent | Runs on | Knows privately | Shares |
|---|---|---|---|
| **Merchant agent** (one per store) | A Flower SuperNode beside the merchant node | Exact amount, buyer region, this card's history at the store, two fraud models' raw scores | Seven banded facts, two model bands, and a letters-only card reference |
| **Bank attestation node** | Its own process, signed requests only | Synthetic cardholder history: last in-person city and time, recent declines | Two bands in round 1; one word in round 2 |
| **Coordinator agent** | A Flower AgentApp on the SuperLink | Only the bands it received | The verdict |
| **Network memory** | The coordinator's state, kept across Flower runs | Which card reference each store saw in the last 10 minutes | One band: `network_velocity_band` |
| **Policy gate** | Code, no model | Nothing | The decision: approve, hold for a person, or decline |
| **Human reviewer** | The merchant's review panel, credentialed | Their judgement | A label that never leaves the node |
| **Jev** (TypeSafe) | Called by the coordinator | Banded facts only | A vote, which can only add caution |
| **Endeavor** (Flower-served model) | Called by the coordinator through the Flower runtime | The verdict and its recognised reasons only | One sentence of explanation for people |

## How a payment flows

```
 Buyer --card--> Stripe Elements --> Stripe                       (the card stops here)
                                       | pm_ id + card checks
                                       v
   Bank  <--signed: card reference + coarse region--  Merchant node  (bands its own history)
   Bank  --signed: issuer_behavior, recent_declines-->     |
                                                           | SuperNode: merchant agent
                          Flower Grid: {purpose, decision_id} / banded facts
                                                           |
                                                SuperLink: coordinator AgentApp
                                          re-guards the reply, adds network memory,
                                          round 2 only on disagreement (travel_check),
                                          rules + Jev vote -> policy gate -> Endeavor sentence
                                                           |
                  verdict, accepted only from the node that supplied the facts
                                                           v
                  approve: Stripe charges | hold: a person decides | decline: voided
```

1. **Card entry.** Stripe Elements sends the card to Stripe. The store posts only the payment-method id.
2. **Stripe lookup.** The merchant asks Stripe for the card checks: funding type, CVC result, card country. Stripe's
   card fingerprint becomes a letters-only card reference (a keyed hash, `tok_` plus 16 letters) and never leaves the
   Stripe adapter.
3. **Bank attestation, round 1.** Over a signed channel the merchant sends the card reference and the card's coarse
   issuing region. The bank answers `issuer_behavior` and `issuer_recent_declines`. It never sees a card and never
   moves money.
4. **Banded facts and the wire guard.** The merchant computes its facts, scores two fraud models locally, and discloses
   through a ledger that allows one disclosure per decision, limits disclosures per card, and refuses unknown keys,
   off-vocabulary values, long strings and card-like digit runs.
5. **Agents on Flower.** The merchant starts a Flower run. The coordinator AgentApp on the SuperLink sends the merchant's
   SuperNode a purpose-tagged question over Grid. The merchant agent fetches the guarded facts from its own node and
   replies. The coordinator re-verifies the reply and adds the network memory's band.
6. **Round 2, only on disagreement.** If the store sees a buyer abroad while the card checks pass and the bank sees an
   ordinary cardholder, the coordinator asks one targeted question through the same node: is this travel plausible?
   The bank answers `travel_check` from history it never shares. "Implausible" holds the payment for a person and
   never declines by itself; no answer also holds it.
7. **Decision.** Fixed rules score the facts. A failed CVC is a hard decline with no vote. Jev's vote can make the
   decision more cautious, never less, and an approval Jev is less than 80% sure of goes to a person. Endeavor writes
   one sentence of explanation, or a template does if the model is down.
8. **Outcome.** Approve charges through Stripe test mode. Decline voids the verification. Both are final. A hold waits
   for a credentialed person, whose decision becomes a training label that updates the fraud model on that node inside
   the same request.

Code drives every Grid call. No model chooses a tool, sees a Grid payload, or decides alone.

## How we use Flower

- **Flower Agent (AgentApp).** One AgentApp with two roles. The coordinator role runs on the SuperLink and drives the
  Grid tools in code: `get_nodes`, `push_messages`, `pull_messages`. The merchant role runs on each SuperNode and answers
  with `push_reply_message`. Each checkout is one Flower run, started through the SuperLink Control API with the
  decision as the prompt.
- **Run series as agent memory.** Consecutive runs share a Flower run series, so the coordinator's network memory
  persists in `context.state`. That is how a card seen at three stores becomes a fact no single store could produce.
  The merchant accepts a verdict only after the run reports completed, so the next decision reads the saved state.
- **Identity from Flower.** A verdict is accepted only from the SuperNode that read the facts, and network sightings are
  keyed by Flower's node id, so a hostile node cannot speak for another merchant.
- **SuperGrid.** Verified live: a SuperNode registered on SuperGrid in the deployment federation `@ac007/cardguard`
  answered a coordinator run on Flower's infrastructure end to end.
- **Endeavor.** The coordinator calls `flwrlabs/endeavor-1.0` through the Flower runtime for the explanation sentence.
  It sees only the verdict and its recognised reasons. When Flower's provider is unavailable, a template answers and the
  decision is unaffected.
- **Federated training.** The shipped fraud model is trained with a Flower ServerApp and ClientApp, one simulated
  SuperNode per merchant, with FedAvg. FedMedian (for hostile nodes) and differential privacy with an RDP accountant
  are available. Human labels feed a federated round from the review panel.

## Safety and oversight

| Attack (who) | Stopped by | Seen as |
|---|---|---|
| Put the card on the wire (compromised merchant agent) | Wire guard: closed vocabulary, Luhn scan, length cap | BLOCKED ledger entries; card-like values seen: 0 |
| Prompt injection through customer text (buyer) | The model-driven agent's draft is guarded and integrity-checked against facts computed in code; card-like text is refused before any model call | Draft blocked, or "altered draft ignored" |
| Injection into the decision models (anyone) | Structural: Jev and Endeavor receive only vocabulary words. A test enumerates all 559,872 fact combinations | No path exists |
| Slow leak through allowed values (compromised merchant) | One disclosure per decision, three attempts, ten per card per hour, receiving-side verifier | Rate-limit BLOCKED entries |
| Forge a verdict for another merchant (hostile SuperNode) | Verdict accepted only from the node that fetched the facts; two replies to one decision go to a person | Verdict rejected |
| Poison the network memory (hostile SuperNode) | Sightings keyed by Flower's authenticated node id | Alerts name the node |
| Approve your own held payment, poison retraining (buyer) | A reviewer credential on every human action | Refused |
| A compromised or failing model provider | Jev can only add caution; Endeavor is display-only and scanned; every model call degrades to rules or a template | Delays, never an approval |

No single control is load-bearing. The human stays in the loop where the system is unsure: holds wait for a person,
the reviewer sees the gate's own reasons, and every human decision is recorded, persisted and learned from without
leaving the node. Weights are recomputed from the federated base each time, so labels cannot compound, and no weight
moves more than 3.0 from the federated weights.

## The demo

Six one-click scenarios, each a real checkout in Stripe test mode. The full script, with what to click and what to say,
is in [DEMO.md](DEMO.md).

| Scenario | What happens |
|---|---|
| Normal purchase | Approved and charged. Every fact that crossed a boundary is on screen; card numbers shared: 0 |
| Collaborative investigation | The store and the bank disagree; round 2 asks the bank one question; held for a person |
| Obvious fraud | The CVC check fails at Stripe: hard decline, no vote, nothing charged |
| Rogue node | A compromised merchant agent tries three ways to leak the card; each is stopped at the boundary |
| Prompt injection | A gift message tries to steer the model-driven agent; its draft is checked, never sent |
| Card-testing ring | One card at three stores in a minute: network band low, medium, high; the third is held and all three stores are alerted |

Then a person declines the held payment, and the fraud model on that node learns from it at once.

## Results, honestly

IEEE-CIS: 590,540 real card transactions. The five product types act as five merchants, and each model is scored on
each merchant's own later transactions (the last 20% of the time window never trains anything).

| Model | AUC, all merchants | Share of fraud in the top 5% | Smallest merchant (S) |
|---|---|---|---|
| Federated fraud model, 9 features (shipped, Flower FedAvg) | 0.768 | 22% | 0.381 |
| Four specialist models, FedAvg then local fine-tune (shipped) | 0.774 | 25% | 0.525 |
| The specialists, each merchant training alone | 0.744 | 21% | 0.502 |

- Federation helps most where data is scarce. For the nine-feature model, federated training followed by a local
  fine-tune beat training alone at every one of the five merchants, with the largest gains at the small ones. Plain
  averaging alone can hurt a large merchant, which the fine-tune fixes. We do not claim federation beats every merchant
  on every model.
- The ceiling is the features a checkout has, not the model: Kaggle winners reach 0.95 with 400 columns a merchant does
  not see at checkout. More accuracy comes from richer computation on each node, never from more bits on the wire.
- Differential privacy is available: epsilon about 40 at delta 1e-5 over 30 rounds, costing 0.015 AUC.
- The fraud-ring signal is real code, but this dataset cannot validate it: it has only 69 cross-merchant sightings.

Details, per-merchant tables and caveats: [TRAINING.md](TRAINING.md).

## Quick start

Python 3.11 to 3.13, [uv](https://docs.astral.sh/uv/), pnpm, and Stripe test keys.

```bash
git clone https://github.com/CR2004/cardguard.git && cd cardguard
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
source .venv/bin/activate
python -m pytest -q
pnpm --dir web install && pnpm --dir web build
cp .env.example .env
```

Fill in `.env`: `STRIPE_SECRET_KEY` and `STRIPE_PUBLISHABLE_KEY` (test keys, required; live keys are refused),
`FLWR_MODEL_API_KEY` (Endeavor through Flower), `TYPESAFE_API_KEY` (Jev), and optionally `REVIEWER_TOKEN`
(eight or more characters; otherwise one is minted at every start). Without the model keys, rules decide and a template
explains.

Decisions over Flower, three terminals:

First add the local SuperLink to `~/.flwr/config.toml`, if it is not there yet:

```toml
[superlink.local-agent]
address = "127.0.0.1:8010"
insecure = true
```

```bash
python scripts/run_superlink.py
python scripts/run_supernode.py
python run_demo.py --federation local-agent --stores store-a,store-b,store-c
```

Open http://127.0.0.1:4242. Without `--federation`, the coordinator runs inside the merchant process with the same rules.

Training on real data: put IEEE-CIS `train_transaction.csv` in `datasets/` (see [datasets/README.md](datasets/README.md)),
then run `python -m cardguard.training.flower_app` for the federated model and `python -m cardguard.specialists.export`
for the specialists. Without it, training and tests use synthetic data.

## Repository layout

| Path | What it is |
|---|---|
| `cardguard/agentapp/` | The Flower AgentApp (coordinator and merchant roles), the run launcher, the dispute agent |
| `cardguard/decision/` | Wire guard and ledger, coordinator rules and verifier, network memory, Endeavor explanation, hash-chained audit |
| `cardguard/payment_processing/` | The merchant node, Stripe test-mode processor, review store, live investigation trace, the model-driven demo agent |
| `cardguard/bank/` | The bank attestation node and its signed client |
| `cardguard/training/` | Federated learning (Flower ServerApp and ClientApp), human-label retraining, differential privacy, join-the-network |
| `cardguard/specialists/` | The four signal-family models: live scoring and training |
| `cardguard/data/` | IEEE-CIS loading and features |
| `web/` | The investigation UI (React, Vite, TypeScript); it animates only real events the nodes report |
| `tests/` | Offline tests: Stripe, Jev, Endeavor and the model client are faked; the bank runs in-process |

## Status and limitations

Verified live on September 29: Stripe test mode, Jev, Endeavor through Flower, the AgentApp over a local SuperLink and
SuperNode, a decision over SuperGrid, the fraud ring across stores, and instant learning from human review. 219 Python
tests and 36 UI tests pass offline.

Stated plainly:

- **The bank's history is synthetic,** and its card reference is a keyed hash of Stripe's test card fingerprint: a demo
  correlation, not an issuer-network identity protocol.
- **One SuperNode fronts three stores in the demo.** In production each merchant would be its own SuperNode; the
  coordinator already keys identity by node.
- **The specialists' federated averaging runs in our own Python code,** over five merchant slices in one process. The
  nine-feature model is trained in a real Flower app. Only the nine-feature model learns from human labels so far.
- **Endeavor depends on Flower's provider,** which returned errors intermittently on the day. A template covers it and
  decisions never depend on it.
- **A decision over SuperGrid takes about three minutes,** mostly task scheduling. Locally it takes 6 to 12 seconds.
- **The buyer's country and hour are demo controls.** In production they come from IP geolocation and the clock.
- **Hand-set values:** the rule weights, band cutoffs and instant-learning label share.

## License and data

MIT. IEEE-CIS data is used under the Kaggle competition rules for research and is not included in this repository.
