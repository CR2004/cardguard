# CardGuard: card-payment risk decisions by collaborating agents, where card data never enters any model's context

Built for the Flower Collaborative Agent Hackathon (Stanford, Sep 29 2026).

Merchants cannot fight fraud together because the one thing they would have to share is the card.
CardGuard is a decision layer between "card entered" and "money moves": the card goes from Stripe
Elements to Stripe and nobody else ever sees it; the merchant reduces the payment to eight banded
facts (nine when the specialist model file is present); agents on different Flower nodes decide together on those facts; a human approves anything
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
142 offline tests (131 pass in a plain environment; 4 tests and 2 test files need `flwr`); two code reviews (dead code, security) applied.

**Left, in order:** review and merge the `specialists-experiment` pull request (checklist below); Stripe test keys and the Flower model key in `.env`;
`flwr login supergrid` and the SuperGrid run (needs a SuperNode we control); personalisation at
merchant startup; UI polish (the new review panel has not been opened in a browser); Flower Hub publish; backup video; pitch.

**On branch `specialists-experiment` (pushed, not yet merged): fraud specialists and human-in-the-loop upgrades.**
- *Specialists.* Four small models, each seeing one signal family (transaction, identity, geography, behavior), are
  trained with FedAvg across the five merchants, fine-tuned on each merchant's own rows, and stacked into one new
  banded fact, `specialist_stack_band`, which the merchant node adds when `specialist_weights.json` exists. Level with
  the original model overall (AUC 0.774 against 0.768) and better at the small merchants.
- *Human in the loop.* Soft declines get a second look; a human's opinion has a reason from a fixed list, is
  audited in a hash-chained log, and becomes a saved training label.
- Scoreboard of what is live: [Scoreboard](#fraud-specialists-four-models-trained-merchant-by-merchant). Details, the live wiring and the plan: [Fraud specialists](#fraud-specialists-four-models-trained-merchant-by-merchant).
  How the training and the federation work, in plain words: [TRAINING.md](TRAINING.md).

**Before merging that branch:**
1. Run `python -m pytest -q` in the project `.venv` (with `flwr`). On the machine that built the branch `flwr`
   was missing, so 4 tests and 2 test files could not run; everything else passes.
2. Open the checkout page and try a queued review: the reason dropdown, note box and "Review stats" button were
   written and tested through the API only.
3. Decide `SECOND_LOOK_ON_DECLINE`. With the default (on), a very risky purchase waits for a human instead of
   being refused on the spot; `SECOND_LOOK_ON_DECLINE=0` restores the old behaviour.
4. `specialist_weights.json` is trained offline on the IEEE-CIS verticals and committed. Regenerate it with
   `python -m cardguard.specialists.export` if the features change; delete it (or set `SPECIALIST_WEIGHTS=none`) to
   switch the specialists off.

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
For the specialist models run `python -m cardguard.specialists.export` (writes `specialist_weights.json`).

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
   local features; only its band leaves the node. When `specialist_weights.json` exists, four small
   one-signal-family models also score the checkout and their stacked band, `specialist_stack_band`, joins
   the facts (their four individual bands stay in the local audit note).
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
   human, and so does a soft decline (a second look; a failed CVC stays a final decline). Decline voids. The
   human gives a reason from a fixed list, which is written to a tamper-evident audit. Human decisions and
   chargebacks become labels on the node, saved to disk; "Retrain" runs a
   federated round and a local fine-tune; a dispute agent drafts the chargeback response for a human.

Code drives every Grid call. No model chooses a tool, sees a Grid payload, or decides alone.

## The demo (about four minutes)

1. **A real purchase.** $20 with 4242 4242 4242 4242: the store shows a pm_ token, the SuperLink log
   shows the agents talking, the ledger shows eight words (nine with the specialist band), the PaymentIntent lands in Stripe's dashboard.
2. **Human in the loop.** $900 with 4000 0027 6000 3184 (German card): band high, human queue; the reviewer
   picks a reason, decline voids, approve charges. Show `/review-stats` and the audit entry; the four per-family
   bands are in the ledger note.
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
| Injection into the decision models (anyone) | Structural: Jev and Endeavor receive only vocabulary words; tests enumerate all 11,664 fact combinations | No path exists |
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

**Live today:** the *Plain FedAvg* column, the shipped `fl_weights.json`. The last column is measured but not yet
shipped (personalisation at startup is on the Left list); a node reaches it only after a human-triggered retrain.

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

Specialist models (four signal families, stacked, trained merchant by merchant): AUC 0.774 against 0.768 for the model
above, with the clearest gains at the small merchants. Scoreboard and caveats: [Fraud specialists](#fraud-specialists-four-models-trained-merchant-by-merchant).

## Fraud specialists: four models, trained merchant by merchant

Four small models, each seeing one family of signals, are trained across the five merchants with FedAvg, then
fine-tuned on each merchant's own rows. Every merchant node scores each checkout with its own copy and sends one new
banded fact, `specialist_stack_band`. Training is per merchant: no model here is trained on all merchants' rows in
one place.

### Scoreboard: what runs today

AUC and "top-5% catch" (share of all fraud that lands in the top 5% of scores) on the held-out last 20% of the
IEEE-CIS window, per merchant. **LIVE marks what the merchant node uses now.**

| Model | All | Top-5% catch | W | H | C | S | R | In the live system? |
|---|---|---|---|---|---|---|---|---|
| Original 9-feature model (`fl_weights.json`, FedAvg) | 0.768 | 22% | 0.726 | 0.536 | 0.635 | 0.381 | 0.565 | **LIVE** (unchanged) |
| **Specialists, FedAvg + local fine-tune** | **0.774** | **25%** | 0.717 | 0.548 | 0.623 | **0.525** | **0.647** | **LIVE (new)** |
| Specialists, plain FedAvg | 0.776 | 19% | 0.734 | 0.606 | 0.599 | 0.344 | 0.621 | not live |
| Specialists, each merchant alone | 0.744 | 21% | 0.669 | 0.504 | 0.622 | 0.502 | 0.653 | not live |

"All" pools every merchant's test rows. The specialists' band that is actually sent has three levels, which is
coarser than the score: 0.708 AUC. Merchant sizes (training rows / test fraud cases): W 232,506 / 1,810;
C 38,658 / 1,617; R 27,462 / 257; H 26,073 / 197; S 6,003 / 183. With 183 to 257 fraud cases, differences of a few
hundredths for S, H and R are within noise.

**What it shows**
- The specialists are level with the original model overall (0.774 against 0.768) and catch a bit more in the top 5%
  (25% against 22%). The clear gains are at the small merchants: S 0.525 against 0.381, R 0.647 against 0.565.
  They are slightly worse at the two large ones (W by 0.009, C by 0.012).
- Fine-tuning is what makes it work per merchant. Plain FedAvg has the best overall AUC but catches fewer frauds
  (19%) and collapses at S (0.344). A merchant training alone is worse overall (0.744), notably at W (0.669), which is
  why the federation is worth having.

### The idea

Today's federation is *horizontal*: every merchant has the same features on different customers, and FedAvg
averages their weights. The specialists add a *vertical* view: each model sees a different family of columns of the
same transaction, so its weights mean something different and cannot be averaged with another family's. A small
final model combines their three-level bands instead. Only that one combined band crosses the wire.

### The four specialists

| Specialist | Looks at | Features |
|---|---|---|
| Transaction | amount above / below this merchant's 90th / 10th percentile, whole-dollar and whole-ten-dollar amounts, purchases by this card in the last 24 hours | 5 |
| Identity | credit card, card age, first time this merchant sees the card | 3 |
| Geo | card country differs from the buyer's country | 1 |
| Behavior | night hour, hour unusual for this card, history length, amount deviation from this card's own habit, days since the previous purchase | 7 |

All 16 features are computed by the merchant node from what it already holds (`cardguard/specialists/live.py`); a
parity test checks that the live history features equal the training ones exactly.

### How they are trained

- **Data.** IEEE-CIS transactions, split by product type into five groups that act as five merchants. Each merchant
  trains only on its own rows.
- **Protocol.** The first 80% of the time window is training and the last 20% is the test period, which never fits
  anything. Within training, the first 70% fits the specialists and the last 30% fits the stacker. Cutoffs, caps and
  percentiles use training rows only, and history features look only at earlier rows; tests check both.
- **FedAvg.** 50 rounds. Each round every merchant trains 6 epochs from the global weights on its own rows and the
  server averages the weights by row count. Only weights move. Then each merchant fine-tunes for 40 epochs on its own
  rows.
- **Stacker.** Logistic regression on the four bands, with one set of cutoffs shared by every merchant (about 5% of
  scores "high", the next 15% "medium"). Per-merchant cutoffs were tried and rejected: they force about 5% "high" at
  every merchant and erase that fraud rates differ about five times between merchants (the pooled AUC of the three-level band fell from
  0.71 to 0.59).

Each specialist on its own, overall AUC, by how it was trained:

| Specialist | Merchant alone | Plain FedAvg | FedAvg + fine-tune (live) |
|---|---|---|---|
| Transaction | 0.763 | 0.567 | 0.637 |
| Identity | 0.750 | 0.653 | 0.726 |
| Geo | 0.694 | **0.344** | 0.694 |
| Behavior | 0.766 | 0.615 | 0.745 |

**Why plain FedAvg breaks a feature.** Geo has one feature, `country_mismatch`, and it means different things at
different merchants. At C, 99.8% of transactions are mismatches and every match is legitimate. At W (232k rows) a
mismatch is rare and *less* fraudulent than average. The merchants' own weights for it are +0.47 at C, +0.86 at H,
-0.39 at W and -3.4 at S. FedAvg weights by rows, so W dominates and the shared weight becomes -0.38: the model points
the wrong way (AUC 0.344). Fine-tuning gives each merchant back its own sign (0.694). This is the general lesson:
merchant-level averaging is safe for signals with a stable meaning and needs a per-merchant step for the rest.

### What is wired into the live node (and what is not)

Four models score every checkout on the merchant node and become one new banded fact.

```
python -m cardguard.specialists.export        offline: FedAvg across the 5 merchants, then local fine-tune,
   |                                          stack the four bands  ->  specialist_weights.json (6 KB, weights only)
   v
merchant.py loads it at startup for its own MERCHANT_VERTICAL   (SPECIALIST_WEIGHTS=none disables; absent = old behaviour)
   v  each checkout: 16 features from state the node already holds + per-card history (bounded, 20k cards)
four specialists -> four bands (kept in the local audit note only) -> logistic stack -> specialist_stack_band
   v
guard.WIRE_SCHEMA (a 9th closed-vocabulary fact) -> coordinator: +1 medium / +2 high, Jev sees it -> verdict in code
```

- **Only one band crosses the wire.** `specialist_stack_band` is the only new fact (invariant 2). The four
  per-specialist bands stay in the ledger's local note so a reviewer can see *which* signal family fired. No new Grid
  roles, so invariant 1a (one reply per decision) is untouched.
- **Fails safe.** A missing, unreadable or non-validating file (wrong features, non-finite weights) disables the
  specialists and the node decides exactly as before (invariant 5). Tests cover each case.
- **Regenerate:** `python -m cardguard.specialists.export` (needs the dataset and the feature cache, a few minutes).
  The exported model on the test period: pooled AUC 0.774, top-5% catch 24.8%.
- **Known risks.** `specialist_stack_band` partly overlaps `model_risk_band` and both add to the score (kept at the
  same weight so neither dominates). The behavior features need per-card history, so a freshly started node scores
  them near zero. Training's `country_mismatch` means "billing country missing or not the home country", while live
  it means "card country differs from the buyer's country".

**Not built:** separate specialist nodes on the Grid (needs invariant 1a reworked); broadcasting a human label to the
specialists (the label already stores its decision id for this).

### Human in the loop: what was missing, and what is now implemented

Before this work a `step_up` verdict went to a review queue (void after one hour, reviewer token to act, the
decision became a local label, a federated round could retrain on the labels). Checked against the code, six gaps
stood between that and "once classified as fraud, add a human opinion". They were closed by a subagent working in an
isolated worktree, then merged and checked here (13 tests in `tests/test_human_review.py`). The behaviour and its
environment variables are documented in **Human in the loop: what is implemented** below.

| # | Gap found | Status |
|---|---|---|
| G1 | A `decline` was final: no human saw it, so "fraud" was only a weighted vote | **Done.** A soft decline is a second look in the same queue; a hard decline (CVC failed) stays final. `SECOND_LOOK_ON_DECLINE=0` restores the old behaviour. |
| G2 | Labels lived in an in-memory list and were lost on restart | **Done.** JSONL file, reloaded at startup, capped at 2000, tolerant of a missing or corrupt file |
| G3 | Approve/decline only: no reason, no reviewer, nothing in the audit chain | **Done.** Closed reason vocabulary, reviewer id, leak-scanned note kept on the node, hash-chained audit of every opinion and expiry |
| G4 | Retraining manual and per node; labels usable only by the 9-feature model | **Partly.** Labels carry a `decision_id` so they can be broadcast to specialists; the broadcast and automatic retraining are not built |
| G5 | No visibility of human-versus-model disagreement | **Done.** `GET /review-stats`: agreement, overturn rate of soft declines, time to review, counts by reason |
| G6 | `GET /reviews` needs no credential | **Unchanged.** It lists banded facts only; acting on a review still needs the reviewer token. Decide if that is intended. |

Two guardrails were added: no action can override a hard decline, and an optional two-reviewer rule
(`TWO_REVIEWER_ABOVE_CENTS`) where an approval above the amount needs a second, different reviewer while a decline
needs one. **Behaviour change to know about:** with the default on, a very risky purchase (rules score of 8 or more,
or a Jev decline) now waits in the review queue instead of being refused on the spot. It is still not charged, and it
is voided after an hour if nobody looks. The checkout-page changes were not opened in a browser, so give that page a
look before the demo.

### Integration status and what is left

| Phase | What | Status |
|---|---|---|
| 1 | Human in the loop after classification (queue, structured audited opinion, persisted labels, stats, guardrails) | **done** |
| 2 | Specialist band as an extra fact from the same node | **done** (one fact, `specialist_stack_band`) |
| 3 | Broadcast a human label to the specialists and retrain the stack (label already carries `decision_id`) | not built |
| 4 | True specialist nodes on the Grid: one band per node, invariant 1a reworked to accept one reply per `(decision_id, node_id)` with a quorum and abstain states | not built; larger change |
| 5 | Train the specialists inside a Flower ServerApp/ClientApp like the original model, instead of our own one-process averaging code | not built |

Rules that carry through every phase: each new band is a closed vocabulary with a test (invariant 2); models only
ever see banded facts; the verdict stays computed in code; no card data anywhere near a specialist.

### Caveats, stated plainly

- **Level with the original model, not a leap.** 0.774 against 0.768 overall; the visible gains are at the small
  merchants and in the explainability of four readable bands. The specialists' inputs are the ones a checkout has.
- **Slightly optimistic.** We looked at test-period results while choosing the training recipe (FedAvg + fine-tune,
  shared cutoffs). A clean figure would pick every setting on the stacker's validation slice and touch the test
  period once at the end.
- **Specialists are not trained inside a Flower app.** Their FedAvg is our own Python code over five merchant slices of
  one dataset in one process; the original model's training is the real Flower app. The maths is the same; the
  transport is not. At decision time both models take part through the Flower agent (their bands are facts on the Grid).
- **Small merchants are noisy.** S has 183 fraud cases in its test period, R 257, H 197.
- Wording: this narrows what any one agent sees. It does not make anything "PCI compliant".

### Run it

```
python -m cardguard.specialists.experiment --synthetic          # offline, seconds, four injected fraud types
python -m cardguard.specialists.experiment                      # real data; ~2 min the first time (builds the cache)
python -m cardguard.specialists.experiment --rebuild            # ignore datasets/specialist_features.npz
python -m cardguard.specialists.experiment --limit 50000        # quick look at the first 50k rows
python -m cardguard.specialists.export                          # train the live specialists -> specialist_weights.json
python -m cardguard.specialists.export --mode federated         # the same with plain FedAvg (no local fine-tune)
python -m pytest tests/test_specialists.py tests/test_specialists_live.py -q   # 20 tests, offline
```
Needs `datasets/train_transaction.csv`. Synthetic mode is a wiring check only: its fraud types are built so each is
visible to one specialist, so its numbers say nothing about real performance.

### Recommendation and decision for the team

1. **Present the specialists as they are:** four readable bands, trained merchant by merchant, level with the original
   model overall and better at the small merchants. The story is that a shared model needs a per-merchant step, and
   that vertical federation lets parties who cannot pool their signal families still combine them.
2. **Do not claim that merchant-level federation beats a merchant training alone in general.** It matters most for the
   smallest merchants, and it breaks features whose meaning differs by merchant unless each merchant fine-tunes.
3. **The demo moment is the human in the loop**: a flagged payment waits, a reviewer gives a reason, it lands in the
   audit chain and becomes a label. Show `/review-stats` and the per-family bands in the ledger.
4. **Next, if time allows:** broadcast labels to the specialists (phase 3). Leave true specialist nodes (phase 4)
   for after the hackathon.

## Layout

| Path | Role |
|---|---|
| cardguard/decision/ | guard (schema, leak scanner, ledger), coordinator (rules, Jev, Verifier), explain (Endeavor), network (ring fact), audit (hash chain), llm (one model client) |
| cardguard/agentapp/ | agent_app (coordinator and merchant roles over Grid), launch (start a run via the SuperLink Control API), dispute_agent |
| cardguard/payment_processing/ | merchant (Flask node), review_store (human opinions, audit, saved labels), stripe_processor, processor_base, agent_llm (the vulnerable demo agent), trace (the live investigation trace the UI animates) |
| cardguard/bank/ | node (bank attestation: synthetic private history, bands only, signed requests), client (the merchant's side) |
| web/ | the investigation UI: React + Vite + TypeScript; the graph animates only real trace events |
| cardguard/training/ | fl (numpy logistic regression + FedAvg), flower_app (Flower ServerApp/ClientApp), retrain, join, privacy |
| cardguard/data/ieee_cis.py | real data: five verticals, features relative to each, time holdout |
| cardguard/specialists/ | four signal-family models scored live (`live.py`), trained and exported (`export.py`), plus the offline experiment (`experiment.py`, `federated.py`) |
| tests/ | offline; Jev, Endeavor, the LLM and the Stripe SDK are faked; the bank node runs in-process |
| run_demo.py, scripts/ | demo runner; SuperLink and SuperNode launchers |

## Human in the loop: what is implemented

After the agents flag a payment as fraud-suspected, a human gives a structured opinion that is audited,
persisted and fed back as a training label. Card data never appears in any of it.

- **Fraud-suspected = `step_up` or a soft `decline`.** Soft declines (no `hard` flag) go to the same
  review queue as a second look: the verification stays open, nothing is charged, and a human confirms
  (decline, label 1) or overturns (approve, label 0). Unreviewed items are voided after one hour.
  Hard declines (`cvc_check=fail`) stay final and are never queued. `SECOND_LOOK_ON_DECLINE=0` restores
  final soft declines (default on).
- **Structured opinion.** `POST /reviews/<id>/approve|decline` (reviewer token) takes an optional JSON
  body `{"reason": ..., "note": ...}`. Reasons are a closed vocabulary: `card_testing`, `ring_pattern`,
  `known_customer`, `customer_verified`, `amount_out_of_pattern`, `other`; anything else is a 400 and
  changes nothing. The note (max 200 chars) is scanned for card-like data, stays on this node, and is
  never sent to the coordinator, a model or `Ledger.disclose()`. `X-Reviewer-Id` (letters, digits, `-_`,
  max 32) names the reviewer.
- **Audit.** Every opinion (and every expiry) is appended to a hash-chained log (`REVIEW_AUDIT_FILE`,
  default `.demo/review_audit.jsonl`): review id, decision, reason, reviewer, the model band and cites the
  reviewer was shown, timestamp, time to review, and only the note's length and SHA-256. A file whose
  chain fails verification is moved aside at startup.
- **Persisted labels.** Human reviews and chargebacks append to `LABELS_FILE` (default
  `.demo/labels.jsonl`): the 9 local features, label, source (`review`|`chargeback`), reason,
  decision id, timestamp. Loaded at startup (last 2000; corrupt lines are skipped).
- **Metrics.** `GET /review-stats` (reviewer token): reviews done, queue length and oldest age,
  model-versus-human agreement (band high/medium counts as "model says fraud"), overturn rate of soft
  declines, median seconds to review, counts by reason, audit chain status. The checkout page has a reason
  dropdown and note box on each queued item and a "Review stats" button.
- **Two reviewers.** `TWO_REVIEWER_ABOVE_CENTS` (default 0 = off): above that amount the first approval
  is recorded as `awaiting_second` (HTTP 202) and a different `X-Reviewer-Id` must approve to complete
  it. A decline needs one reviewer.

## Environment (.env, see .env.example)

`STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY` (test keys, required) · `FLWR_MODEL_API_KEY` (live
explanations through Flower; model `Flwrlabs/endeavor-v1.0`) · `TYPESAFE_API_KEY` (Jev) ·
`LLM_BASE_URL/LLM_API_KEY/LLM_MODEL` (direct calls for the injection demo and dispute drafts) ·
`MERCHANT_VERTICAL` · `STORES` · `FL_DP_NOISE`, `FL_DP_CLIP`, `FL_ROBUST` · `DEMO_CONTROLS` (set by
run_demo: page may choose country, hour, attack, agent mode; unset = production behaviour) ·
`SPECIALIST_WEIGHTS` (path, or `none` to switch the specialist models off) · `SECOND_LOOK_ON_DECLINE` (default 1) ·
`TWO_REVIEWER_ABOVE_CENTS` (default 0 = off) · `LABELS_FILE`, `REVIEW_AUDIT_FILE` (default under `.demo/`).

## Assumptions, stated plainly

Stripe test mode is real Stripe with no real money. Synthetic data is used only by offline tests.
Fallbacks announce themselves: `decided_by: rules` without Jev, `by: template` without a model
endpoint. The buyer's country is a demo control; production plugs IP geolocation into `geolocate()`.
Rules weights (including the one for `specialist_stack_band`), band cut-offs, velocity cuts, the human-label weight
and the ring window are hand-set. The specialists' FedAvg is run by our own Python code over the five merchants in one
process (`cardguard/specialists/export.py`), not yet inside a Flower app; the original model's training is
the real Flower app. Live, one node loads its own vertical's weights.
Several stores on one node is a demo convenience; in production one node is one merchant.

Traps: port 8000 may be taken (the SuperLink uses 8010); `flwr run` refuses AgentApps without a
prompt (use run_demo.py or `cardguard.agentapp.launch`); the SuperLink needs the venv on PATH (the
scripts do this); every pin in pyproject.toml must resolve with flwr's own pins, since the AgentApp
runtime env is rebuilt from it on every run; nothing under datasets/, .demo/ or .env is committed.

License: MIT. Data: IEEE-CIS under the Kaggle competition rules (research use).
