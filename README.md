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

**Experiment, now wired in: fraud specialists.** Four small models, each seeing one signal family (transaction,
identity, geography, behavior), are trained with FedAvg across the five merchants, fine-tuned locally, and stacked
into one new fact, `specialist_stack_band`, that the merchant node adds when `specialist_weights.json` exists.
Honest result: with only the features a checkout can compute, the stack is on par with the current model
(AUC about 0.77); the large gain (0.866) needs columns the dataset's processor engineered. Details, the live
wiring and the human-in-the-loop plan: [Fraud specialists](#fraud-specialists-experiment-branch-specialists-experiment).

## Quick start

Python 3.11 or 3.12, [uv](https://docs.astral.sh/uv/).

    git clone https://github.com/CR2004/cardguard.git && cd cardguard
    uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
    source .venv/bin/activate
    python -m pytest -q                              # 119 tests, offline, seconds

    cp .env.example .env                             # then fill in: STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY (test keys,
                                                     # required), FLWR_MODEL_API_KEY (live explanations), TYPESAFE_API_KEY (Jev)
    python run_demo.py                               # merchant on http://127.0.0.1:4242; prints the reviewer token

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
2. **Facts.** The merchant asks Stripe for the card's metadata and turns it, plus its own history,
   into eight banded facts: amount relative to its own order sizes, country mismatch, funding, CVC
   result, velocity, first sighting, and the federated model's risk band. The model scores nine raw
   local features; only its band leaves the node.
3. **Wire guard.** The facts pass through a ledger that allows one disclosure per decision, limits
   disclosures per card, and rejects unknown keys, off-vocabulary values, oversized strings and
   card-like digit runs. Everything is logged in a hash-chained ledger.
4. **Agents on Flower.** The merchant starts a Flower run. The coordinator agent on the SuperLink asks
   the merchant agent on the SuperNode a purpose-tagged question over Grid; the merchant agent fetches
   the guarded facts from its own node and replies; the coordinator re-guards the reply, adds its own
   fact (how many merchants saw this card in the last ten minutes), runs the rules and Jev's vote in
   code, hard-declines a failed CVC, sends low-confidence approvals to a human, asks Endeavor for one
   sentence, and emits the verdict. The merchant accepts it only from its own node, for its own decision.
5. **Outcome.** Approve confirms a Stripe test-mode PaymentIntent. Step-up waits for a credentialed
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

## Fraud specialists experiment (branch `specialists-experiment`)

**Status: offline experiment on real data. Nothing here is wired into the demo, the wire schema or the
coordinator, and the live pipeline (`fl.FEATURES`, `fl_weights.json`, `merchant.py`, `BAND_CUTS`) is unchanged.**

### The idea
Today's federation is *horizontal*: every merchant has the same 9 features on different transactions, and
FedAvg averages their weights. This experiment asks a different question. Instead of one agent per merchant,
make **each agent a fraud specialist that only sees one family of signals for the same transactions**, then let
a global agent combine the specialists' opinions into one fraud decision.

This is vertical (feature-partitioned) federation plus stacking, **not FedAvg**: the specialists hold different
columns, so their weights cannot be averaged. What would cross a node boundary is one banded score per
specialist (`low` / `medium` / `high`), which fits the existing rule that only banded, allowlisted facts travel.
The Device specialist never sees amounts, and the Geo specialist never sees devices.

### The seven specialists

| Specialist | What it looks at | Features | Notes |
|---|---|---|---|
| Transaction | amount rank inside its vertical, round and sub-cent amounts, `C1-C14` counts, 24 h velocity | 21 | strongest by far |
| Identity | payer/recipient email, `M1-M9` match flags, card age, new customer, credit / card network | 31 | |
| Device | identity-file flags: device type and family, OS, browser, screen, proxy, new-device flags | 32 | **abstains** on the ~80% of transactions with no identity record |
| Geo | country mismatch, address rarity, `dist1/dist2`, address change since the card's last seen | 10 | |
| Behavior | deviation from this card's own history: amount, hour, new product, `D` timedeltas | 20 | |
| Merchant | ProductCD, recipient-email rarity | 7 | thin: IEEE-CIS has no MCC |
| Network | distinct cards sharing an address+email, an email, a recipient email; emails per card | 4 | |

Design points worth knowing:
- **Explicit "no data" state.** Only about a quarter of transactions (144k of 590k) have identity rows. Where the
  Device specialist has no record it contributes zero plus a coverage flag, so the stacker can tell "no evidence"
  from "evidence of nothing". It is never given a fake zero.
- **No lookahead.** History features (velocity, card history, shared-entity counts) are computed in time order over
  *earlier* rows only. A test proves a row's features do not change when later rows are removed.
- **No test-period leakage.** Percentile cuts, caps and rarity counts come from the training period only. A test
  distorts test-period rows and checks that training rows are untouched.
- **Protocol.** The first 80% of the time window is training, the last 20% is the test (same cut as the existing
  pipeline). Within training, the first 70% fits each specialist and the last 30% fits the stacker on scores the
  specialists have not seen. The test set never fits anything.

### Results with every dataset column (real IEEE-CIS: 590,540 transactions, 3.5% fraud, 118,108 test rows)

**These use columns our live checkout does not collect; see "The catch" below for the checkout-computable numbers.**

"Catch at 5%" is the share of all fraud that lands in the top 5% of scores. Specialists are LightGBM models; the
stacker is always logistic regression so the combining step stays simple to audit.

| Model | AUC | Frauds caught in top 5% |
|---|---|---|
| Current 9-feature model (for reference) | 0.773 | 21% |
| Specialist: Transaction | 0.847 | 48% |
| Specialist: Identity | 0.792 | 25% |
| Specialist: Behavior | 0.787 | 32% |
| Specialist: Merchant | 0.701 | 27% |
| Specialist: Geo | 0.696 | 20% |
| Specialist: Network | 0.686 | 24% |
| Specialist: Device (covers 20% of rows) | 0.665 | 27% |
| **Stack of specialists (scores)** | **0.866** | **50%** |
| **Stack of specialists (bands only)** | **0.839** | **47%** |
| One model on all columns pooled (ceiling; needs every column in one place) | 0.882 | 51% |

The same experiment with plain logistic-regression specialists (no extra dependencies):

| Model | AUC | Catch at 5% |
|---|---|---|
| Best single specialist (Transaction) | 0.830 | 43% |
| Stack (scores) | 0.846 | 45% |
| Stack (bands only) | 0.815 | 44% |
| Pooled | 0.852 | 43% |
| Current 9-feature model | 0.772 | 23% |

**What this shows**
- The stack beats the current 9-feature model by about 0.09 AUC and finds more than twice as much fraud in the top
  5% (50% against 21%). Passing only bands still gains about 0.066 AUC.
- Splitting by signal family costs about 0.016 AUC against a pooled model (0.866 against 0.882): no specialist
  sees interactions across families. That is the price of keeping each family's columns apart.
- Leave-one-specialist-out (AUC of the stack without it): Transaction 0.822 (-0.043), Behavior 0.860 (-0.006),
  Identity 0.863 (-0.002), Device 0.864 (-0.002), Merchant, Network and Geo 0.866 (no change). **Transaction does
  most of the work; Transaction, Behavior and Identity carry nearly all of the gain.**

### The catch: most of the gain needs columns a live checkout does not have

The table above uses every column the dataset offers. Many of them (`C1-C14` counts, `D` timedeltas, `M` match
flags, payer/recipient email, `addr1`/`dist1`, the device-recognition flags) were engineered by the payment
processor that produced the data. Our checkout collects none of them. So we re-ran the same experiment with
`--deployable`: each specialist may only use features the merchant node can compute from what it already holds
(the amount, its own per-card history, the buyer country, the clock, its own vertical). The Device and Network
specialists have no such feature and drop out.

| Model (checkout-computable features only) | LightGBM AUC | Catch at 5% | Logistic AUC | Catch at 5% |
|---|---|---|---|---|
| Current 9-feature model | 0.773 | 21% | 0.772 | 23% |
| Best single specialist (Transaction) | 0.748 | 22% | 0.748 | 21% |
| **Stack (scores)** | **0.787** | **25%** | **0.783** | **23%** |
| **Stack (bands only)** | **0.763** | **27%** | **0.758** | **24%** |
| Pooled, one model | 0.792 | 24% | 0.786 | 22% |

Leave-one-out (LightGBM stack): without Transaction 0.765, Identity 0.775, Behavior 0.780, Merchant 0.785,
Geo 0.787. Behavior falls back to AUC 0.65 because the `D` timedeltas that lifted it are not available.

**How to read this**
- With inputs a checkout can compute, the stack is only about **+0.014 AUC** over the current model, and the version
  that ships only bands is **-0.010 AUC** (though it catches slightly more in the top 5%). That is roughly
  break-even, not an improvement worth wiring into the live path.
- The 0.09 AUC gain in the full-feature table comes from **richer inputs**, not from the specialist topology. This
  matches the earlier finding in this README that the level is bounded by the features, not the model.
- The specialist idea is worth most where different parties really do hold different signal families (a device
  intelligence provider, an identity/KYC provider, a geo provider, the merchant) and cannot pool them. The
  IEEE-CIS columns all came from one processor, so this dataset shows what the combination could reach, not what
  our single-merchant demo checkout can produce.

### Federated measurement: the specialists trained with FedAvg across the five merchants

Everything above trains each specialist on all merchants' rows pooled in one process, so it says nothing about
federated learning. `--federated` fixes that. The five ProductCD verticals (W, C, R, H, S) act as five merchant
nodes, and every specialist is trained four ways. Rows never leave their merchant except in the first mode, which
is there only as the pooled reference.

| Mode | What happens |
|---|---|
| central | all merchants' rows pooled (the reference; needs everyone's data in one place) |
| local | each merchant trains alone on its own rows |
| federated | FedAvg: 50 rounds, each merchant trains 6 epochs from the global weights on its own rows, the server averages the weights by row count. Only weights move. |
| personalised | the federated weights, then 40 local epochs on the merchant's own rows |

Specialists are logistic regression here because FedAvg averages weight vectors. The stacker (the coordinator) is
trained pooled in every mode, so the modes differ only in how the specialists were trained. Same time-ordered
splits and test period as above. Merchant sizes (fit rows / test fraud): W 232,506 / 1,810; C 38,658 / 1,617;
R 27,462 / 257; H 26,073 / 197; S 6,003 / 183.

**Stack of scores, all dataset columns** (AUC on the test period; "all" pools every merchant's test rows):

| Specialists trained | All | Catch at 5% | W | H | C | S | R |
|---|---|---|---|---|---|---|---|
| central (pooled) | 0.846 | 45% | 0.776 | 0.867 | 0.870 | 0.639 | 0.932 |
| local (alone) | 0.852 | 43% | 0.779 | 0.872 | 0.892 | 0.490 | 0.933 |
| **federated (FedAvg)** | **0.840** | **40%** | **0.784** | **0.841** | **0.825** | **0.526** | **0.912** |
| personalised | 0.844 | 38% | 0.782 | 0.855 | 0.850 | 0.488 | 0.920 |

**Stack of scores, checkout-computable features only** (`--deployable`):

| Specialists trained | All | Catch at 5% | W | H | C | S | R |
|---|---|---|---|---|---|---|---|
| central (pooled) | 0.783 | 23% | 0.741 | 0.519 | 0.626 | 0.404 | 0.558 |
| local (alone) | 0.794 | 23% | 0.763 | 0.578 | 0.621 | 0.364 | 0.676 |
| **federated (FedAvg)** | **0.777** | **17%** | **0.758** | **0.517** | **0.609** | **0.383** | **0.559** |
| personalised | 0.790 | 20% | 0.761 | 0.549 | 0.621 | 0.384 | 0.639 |

Stacks of bands only (all columns): central 0.815, local 0.830, federated 0.829, personalised 0.819; with
checkout-computable features: 0.758, 0.744, 0.778, 0.771. These are within noise of each other.

**Single specialists, overall AUC, pooled versus FedAvg:**

| Specialist | All columns: central -> federated | Checkout-only: central -> federated |
|---|---|---|
| Transaction | 0.830 -> 0.810 | 0.748 -> 0.557 |
| Identity | 0.781 -> 0.770 | 0.677 -> 0.653 |
| Behavior | 0.765 -> 0.680 | 0.665 -> 0.615 |
| Network | 0.710 -> 0.635 | (no checkout feature) |
| Merchant | 0.694 -> 0.685 | 0.694 -> 0.674 |
| Geo | 0.685 -> **0.470** | 0.656 -> **0.344** |
| Device | 0.664 -> 0.654 | (no checkout feature) |

**What this shows, without flattering the federation**
- **The specialist split survives federation overall.** With all columns the federated stack scores 0.840 against
  0.846 pooled, so it loses about 0.006 AUC while no row leaves its merchant.
- **But merchants training alone do as well overall (0.852), and better for three of the five.** FedAvg beats
  alone only for the biggest merchant W (+0.005) and the smallest S (+0.036). It is worse for H (-0.031),
  C (-0.067) and R (-0.021). This repeats the earlier finding in this README: one shared linear model cannot fit
  five different fraud mixes.
- **Small merchants are the real beneficiary, and only pooling fully helps them.** S goes 0.490 alone, 0.526
  federated, 0.639 pooled. S has 183 fraud cases in its test period, so differences of a few hundredths for S and
  H are within noise.
- **Personalising did not rescue it** with these settings (0.844, still under alone at 0.852). The fine-tune
  step was not tuned.
- **A feature that means different things at different merchants breaks under FedAvg.** Geo's only
  checkout-computable feature, `country_mismatch`, shows why. At C, 99.8% of transactions are mismatches and
  every match is legitimate. At W (232k rows) a mismatch is rare and *less* fraudulent than average. Local weights
  are +0.47 at C, +0.86 at H, -0.39 at W, -3.4 at S. FedAvg weights by rows, so W dominates and the shared weight
  becomes -0.38, while the pooled model gets +1.58 because it can see that mismatches cluster in the fraud-heavy
  merchant. That is a between-merchant effect that no single merchant can see, so the pooled specialist AUCs
  in the earlier tables are partly flattered by merchant mix.
- **Catch at 5% falls more than AUC** under FedAvg with checkout-only features (23% pooled, 17% federated).

**What it means for the design**
1. The vertical split does not need merchant-level averaging to work: it costs about 0.006 AUC to keep rows local.
2. Apply merchant-level FedAvg selectively. With all columns it costs little for signals with a stable meaning
   across merchants (Transaction, Identity, Device lose 0.010-0.019) and a lot for signals tied to a merchant's
   own mix (Geo, Behavior, Network lose 0.075-0.216). Even Transaction is not safe when reduced to checkout-only
   features: it loses 0.19 there, because its sub-cent flag mostly tracks which merchant a row came from. Give
   merchant-specific signals a per-merchant model, or leave them to the party that owns them.
3. Where FedAvg pays is the smallest merchants, as before. Do not claim it beats a merchant training alone
   in general.

### What the data told us, and what we changed
- The first real run left Behavior weakest (AUC 0.665, catching 8% in the top 5%) because `new_product` and
  `hour_unusual` fire on under 2% of rows: the card proxy has sparse history. Adding the unused `D2, D4, D5, D10,
  D11, D15` timedeltas lifted it to 0.765 with logistic and 0.787 with LightGBM.
- Much of the signal is *missingness*: `r_missing`, `addr2_missing` and `ProductCD = C` are among the strongest
  single features, a known property of this dataset.
- The Network specialist finds no fraud-ring signal here: `cards_per_pair` points the wrong way. This matches the
  caveat above that the dataset cannot validate ring detection.
- Switching each specialist from logistic regression to LightGBM lifts the stack from 0.846 to 0.866.

### Caveats, stated plainly
- **Slightly optimistic.** We looked at test-set results twice while choosing features and models. A clean figure
  would pick every setting on the stacker's validation split and touch the test set once at the end.
- **Identity-file parsing** was written from community descriptions of the value formats. We checked the real
  file's values for `DeviceType`, `id_15/23/28/29/30/31/33` and `DeviceInfo` and they match, but the meaning of the
  masked `C`, `D` and `M` columns is inferred, not documented.
- **No "each specialist catches something the others miss" story.** On real data one specialist dominates. The
  honest claim is that the stack beats the current model while no specialist sees another's columns, not that
  every specialist is needed.
- **Not deployable as is.** Most of the gain uses dataset columns our checkout does not collect (see "The catch"). The banded stack also needs its cut points re-derived whenever a specialist changes.
- Wording: this narrows what any one agent sees. It does not make anything "PCI compliant".

### Run it
```
pip install -r requirements-experiments.txt          # scikit-learn, lightgbm: only for --model lgbm
python -m cardguard.specialists.experiment --synthetic          # offline, seconds, six injected fraud types
python -m cardguard.specialists.experiment                      # real data, ~2 min first run (builds the cache)
python -m cardguard.specialists.experiment --model lgbm         # LightGBM specialists, ~1.5 min
python -m cardguard.specialists.experiment --deployable         # only checkout-computable features
python -m cardguard.specialists.experiment --federated          # FedAvg across the 5 merchants, 4 training modes (several minutes)
python -m cardguard.specialists.experiment --rebuild            # ignore datasets/specialist_features.npz
python -m cardguard.specialists.experiment --limit 50000        # quick look at the first 50k rows
python -m pytest tests/test_specialists.py -q                   # 14 tests, offline
```
Needs `datasets/train_transaction.csv` and `datasets/train_identity.csv` (the `test_*` files have no labels and
are not used). Synthetic mode is a wiring check only: its fraud types are built so each is visible to one
specialist, so its numbers say nothing about real performance.

### What is integrated today (checked against the code)

| Question | Answer | Where |
|---|---|---|
| Does any live code import the specialists? | No. It is an offline package plus tests. | `cardguard/specialists/` |
| How does the federated model reach a decision now? | FedAvg -> `fl_weights.json` -> merchant scores 9 checkout features -> one `model_risk_band` -> coordinator adds +1 (medium) or +2 (high) to a rules score | `flower_app.py:71`, `merchant.py:171,454-459`, `coordinator.py:36` |
| What may cross a node boundary? | 8 closed-vocabulary facts. No specialist key exists. | `guard.py:18-28` |
| Can several nodes answer one decision? | No: the coordinator accepts one fact set per `(decision_id, purpose)`, and two nodes answering is a conflict that goes to human review | `coordinator.py:65-75`, invariant 1a |

### If the specialists win: what the federated learning becomes

There are two independent ways to split fraud data, and the design uses both:

```
                     HORIZONTAL (across merchants: same columns, different customers)
                      merchant W     merchant C     merchant R    ...
   VERTICAL          +------------+ +------------+ +------------+
   (across           | Transaction| | Transaction| | Transaction|   FedAvg inside a specialist,
   signal families:  | Behavior   | | Behavior   | | Behavior   |   weights only (logistic)
   different columns,| Identity   | | Identity   | | Identity   |
   same transactions)+------------+ +------------+ +------------+
                      Device / Geo / Network / Merchant: held by the party that owns that signal
                                 |   one band per specialist: low / medium / high
                                 v
                        coordinator: stacker (logistic on bands) + rules + Jev vote
                                 v
                        verdict in code -> human review -> label -> back to every specialist
```

- **Vertical axis (new).** Each specialist trains locally on its own columns and returns only a band. Nothing is
  averaged across specialists, because their weights mean different things.
- **Horizontal axis (existing).** Inside a specialist that every merchant runs, merchants can FedAvg their copies.
  Measured (see "Federated measurement"): it helps only the smallest merchant and hurts where a feature means
  different things at different merchants, so use it selectively (Transaction, Identity, Device), not for Geo,
  Behavior or Network.
- **Model type matters.** FedAvg needs models that are weight vectors (logistic regression, small nets). LightGBM
  trees cannot be averaged that way (Flower has tree strategies in some versions; not checked for 1.39, so treat
  as unverified). The measurements say this costs little: with checkout-computable features logistic scores 0.783
  against LightGBM 0.787, and with all columns 0.846 against 0.866. Use logistic where FedAvg runs, and LightGBM
  only for a specialist held by a single party.
- **The stacker.** It needs the specialists' bands and the true label for the same transactions, joined on a
  pseudonymous `decision_id`. It is a handful of weights, kept as JSON (Flower Hub accepts `.json`, not model files).
- **Labels flow back by `decision_id`.** A human verdict is broadcast to every specialist; each stores
  (its own features, label) locally and never sees another specialist's features.

### Human in the loop: what exists (verified) and what is missing

**Already there, with tests:**
- **Review queue.** A `step_up` verdict (rules score 3-7, or Jev says step_up, or Jev's confidence in an approve is
  too low) holds the payment in a queue (`merchant.py:555-559`, `coordinator.py:89,172`). Unreviewed items are voided
  after one hour, so it fails closed (`merchant.py:355`).
- **Only a credentialed human moves money.** Approve or decline needs the reviewer token (`merchant.py:573`; test:
  `test_merchant.py:62`). The reviewer sees banded facts and the verdict, never card data (`test_merchant.py:101`).
- **The human's decision becomes a label** (approve = 0, decline = 1), stays on the node (`merchant.py:580`), and
  chargebacks add fraud labels (`merchant.py:597-607`); a dispute-evidence agent drafts a response a human approves.
- **Labels retrain the federated model.** `/agent/retrain` runs a federated round with each human label weighted 50
  ordinary rows, then fine-tunes locally, and reports the band before and after (`retrain.py`,
  `merchant.py:657-674`; tests: `test_retrain.py`, `test_review_fixes.py`).

**Gaps against "once classified as fraud, add a human opinion":**

| # | Gap | Evidence |
|---|---|---|
| G1 | A `decline` is final: no human ever sees it. "Fraud" is not a separate stage, only a weighted vote (a `high` model band alone adds just +2, below the step_up threshold of 3). | `merchant.py:560`, `coordinator.py:36,89` |
| G2 | Human labels live in an in-memory list: lost on restart, not persisted. | `merchant.py:269,360` |
| G3 | The human gives only approve/decline: no reason, no reviewer identity, and the human's opinion itself is not recorded in the audit chain (only the payment settlement that follows it). | `merchant.py:571-588` |
| G4 | Retraining is manual and per node; labels are the 9 current features, so they cannot train the specialists. | `retrain.py`, `merchant.py:657` |
| G5 | No tracking of human-versus-model disagreement, so nobody can see false positives or how often the model is overruled. | not implemented |
| G6 | `GET /reviews` needs no credential (it shows banded facts only). Acting on a review does need one. Decide if that is intended. | `merchant.py:564` |

**Planned changes (each with a test):**
1. **A "fraud suspected" stage after classification.** Suspected = any soft decline, any `step_up`, or
   `model_risk_band = high` together with at least one other signal. Soft declines go to a second-look queue: the
   payment stays voided unless a human overturns it. A hard decline (CVC failed) stays final.
2. **A structured human opinion.** `not_fraud` / `confirm_fraud` / `unsure`, plus a reason from a closed vocabulary
   (for example `card_testing`, `ring_pattern`, `known_customer`, `customer_verified`, `other`). A free-text note is
   leak-scanned and kept on the node. Each opinion is appended to the hash-chained audit with a reviewer id.
3. **Persist labels** to a local file so they survive restarts.
4. **Label propagation by `decision_id`** (needed once specialists exist): every specialist stores its own features
   with the label, and the stacker stores the bands with it.
5. **Retrain trigger and metrics.** Retrain after N new labels; show model-versus-human agreement, overturn rate and
   time to review on the page.
6. **Guardrails.** No human action can override a hard decline. Optionally require two reviewers above an amount.

### Real-time integration plan

Every phase leaves the demo path working. Time estimates are rough guesses.

| Phase | What | Files | Gate to continue | Est. |
|---|---|---|---|---|
| 0 | Experiment, full and checkout-only results, this plan | `cardguard/specialists/`, README | done | done |
| 1 | **Decide.** Read the two result tables above and pick a path. | none | team agrees | 10 min |
| 2 | **Human in the loop after classification** (changes 1-3 above, then 5) | `merchant.py`, `coordinator.py` (stage flag only), tests | all old tests still pass; new tests for queue, closed reasons, audit entry, persistence | 1.5-2 h |
| 3A | **Replay demo of the specialists.** Export trained specialists (logistic) to JSON; a replay endpoint scores a chosen IEEE-CIS test transaction and shows each specialist's band and the stack. Labelled "replay of real data", not live. | new `specialists/export.py`, replay route, page panel | export reproduces the experiment's AUC | 2-3 h |
| 3B | **Specialist bands as extra facts from the same node.** Add up to three `*_risk_band` keys to `WIRE_SCHEMA`, `WEIGHTS` and `FACT_MEANINGS` (checkout-computable features only). Invariant 1a stays intact. | `guard.py`, `coordinator.py`, `merchant.py`, tests | only if the deployable stack beats the current model on the same holdout | 2 h |
| 4 | **True specialist nodes.** One SuperNode per specialist, one band each. Needs invariant 1a reworked: accept one reply per `(decision_id, node_id)` with a quorum and abstain states, and treat the same node answering twice as the conflict. Label propagation (change 4), stacker retraining, per-specialist band cuts. | `agent_app.py`, `Verifier`, `merchant.py`, `retrain.py` | after the hackathon | days |
| 5 | **Production.** Collect the richer signals for real (device intelligence, identity, geo), monitor drift, re-derive band cuts on a schedule, DP on the stacker. | | | |

Rules that carry through every phase: each new band is a closed vocabulary with a test (invariant 2); models only
ever see banded facts; the verdict stays computed in code; no card data anywhere near a specialist.

### Recommendation and decision for the team

1. **Present the specialist result honestly:** 0.866 against 0.773 on all columns, and about 0.787 against 0.773 on
   checkout-computable columns. The story is that signal richness drives accuracy, and that vertical federation
   lets parties who cannot pool their signal families still combine them.
2. **Spend the build time on phase 2** (human in the loop after classification). It is safe, it is a visible demo
   moment, it uses only existing pieces, and it answers the safety and oversight criterion directly.
3. **Do not claim that merchant-level federation beats a merchant training alone.** The measurement above shows it matches the pooled stack (0.840 against 0.846) but merchants alone reach 0.852; it helps the smallest merchant.
4. **Add phase 3A only if time allows.** Skip 3B unless the deployable gain looks better on a re-check, and leave
   phase 4 for after the hackathon.

## Layout

| Path | Role |
|---|---|
| cardguard/decision/ | guard (schema, leak scanner, ledger), coordinator (rules, Jev, Verifier), explain (Endeavor), network (ring fact), audit (hash chain), llm (one model client) |
| cardguard/agentapp/ | agent_app (coordinator and merchant roles over Grid), launch (start a run via the SuperLink Control API), dispute_agent |
| cardguard/payment_processing/ | merchant (Flask node), stripe_processor, processor_base, agent_llm (the vulnerable demo agent), checkout.html |
| cardguard/training/ | fl (numpy logistic regression + FedAvg), flower_app (Flower ServerApp/ClientApp), retrain, join, privacy |
| cardguard/data/ieee_cis.py | real data: five verticals, features relative to each, time holdout |
| cardguard/specialists/ | four signal-family models scored live (`live.py`), trained and exported (`export.py`), plus the offline experiment (`experiment.py`, `federated.py`) |
| tests/ | offline; Jev, Endeavor, the LLM and the Stripe SDK are faked |
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
