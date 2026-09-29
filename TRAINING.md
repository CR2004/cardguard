# How training works: nodes, federation, and what moves

A plain-words guide to how the fraud models are trained, who does what, and what is (and is not) shared.
Measured results and caveats are in the [README](README.md#fraud-specialists-four-models-trained-merchant-by-merchant).

## The idea in one paragraph

Merchants cannot pool their customers' transactions, but each can learn from the others. So every merchant trains a
small model on **its own** transactions, and only the model's **weights** (a short list of numbers) are shared and
averaged. That is federated learning. No transaction row ever leaves a merchant.

## The data and the nodes

- **Data:** IEEE-CIS, 590,540 real transactions. The last 20% of the time window is a **test period** that never
  trains anything.
- **Merchant nodes:** the dataset has five product types (W, C, R, H, S). Each one is treated as one merchant,
  so **five merchant nodes**. Each node holds only its own rows, its own typical order size, its own fraud rate.
- **The server:** during training, one server averages the weights the merchants send back. It never sees rows.
- **Not part of training:** the bank node (it answers questions from synthetic history) and the decision-time
  coordinator (it combines facts into a verdict). Neither trains anything.

> The five "merchants" are slices of one dataset, so training is a simulation of five separate companies. The code
> keeps their rows apart, and only weights cross between them.

## What is trained

Two models are trained and used together at every checkout.

**Federated fraud model** (sends `model_risk_band`)
- **What:** one logistic regression.
- **Sees:** 9 checkout features: amount high or low for this merchant, country mismatch, credit card, purchases in
  the last 24 hours, first time this merchant sees the card, night hour, card age, days since the previous purchase.
- **Saved as:** `fl_weights.json` (10 numbers).
- **Trained by:** `python -m cardguard.training.flower_app`, a Flower app (ServerApp + ClientApp) with one simulated
  SuperNode per merchant.

**Specialist models** (send `specialist_stack_band`)
- **What:** four small logistic regressions, one per signal family, plus one that combines their bands.
- **Sees:** 16 features in four families: transaction, identity, geo, behavior.
- **Saved as:** `specialist_weights.json` (about 6 KB).
- **Trained by:** `python -m cardguard.specialists.export`, our own Python code running the same averaging steps
  (not yet inside a Flower app).

Logistic regression is just `score = bias + weight1 x feature1 + ...`, squashed into a 0-to-1 fraud probability. A
model that is only a list of numbers can be averaged, and its files are tiny.

## The exact features

Every feature is a number between 0 and 1. Each has two forms: how it is built from the dataset when the models are
trained, and how the merchant node builds it from a live checkout. A test checks that the history features are
identical in both forms.

**Card key.** Training has no card number, so it identifies "the same card" by the community-standard key
`(card1, card2, card3, card5, addr1, P_emaildomain)`. Live, the node uses the card's keyed reference. Nothing here is
a card number.

**Day counts** (`card_age`, `days_since_prev`) are scaled as `min(log(1 + days) / log(1 + 365), 1)`.

### Specialist 1: Transaction (5 features, 6 weights)

| Feature | Meaning | Training (dataset column) | Live checkout |
|---|---|---|---|
| `high_amount` | amount above this merchant's 90th percentile | `TransactionAmt` vs the merchant's own p90 (training rows only) | dollars vs the merchant's p90 (its own quantiles after 200 completed charges, before that the seed cuts for its vertical) |
| `micro_amount` | amount below this merchant's 10th percentile | same, p10 | same, p10 |
| `round_1` | a whole-dollar amount | `TransactionAmt` has no cents | `amount_cents % 100 == 0` |
| `round_10` | a multiple of $10 | `TransactionAmt % 10 == 0` | `amount_cents % 1000 == 0` |
| `velocity` | purchases by this card in the previous 24 hours, `min(n, 10) / 10` | earlier rows with the same card key within 24 h of `TransactionDT` | this card's earlier purchases at this store in the last 24 h |

### Specialist 2: Identity (3 features, 4 weights)

| Feature | Meaning | Training | Live |
|---|---|---|---|
| `credit` | the card is a credit card | `card6 == credit` | funding type from Stripe is credit |
| `card_age` | how long since this card was first seen (scaled) | `D1` | days since this store first saw the card |
| `new_customer` | first time this merchant sees the card | `D1 == 0` | no earlier sighting at this store |

### Specialist 3: Geo (1 feature, 2 weights)

| Feature | Meaning | Training | Live |
|---|---|---|---|
| `country_mismatch` | the buyer looks to be in a different country | billing country code (`addr2`) missing or not the home country (87) | the card's issuing country differs from the buyer's country |

The training and live meanings are close but not identical, which matters for this feature: see "Why the fine-tune?" below.

### Specialist 4: Behavior (7 features, 8 weights)

Built from this card's own earlier purchases only (never the current one or later ones).

| Feature | Meaning | Training | Live |
|---|---|---|---|
| `night` | purchase between midnight and 6 am | hour from `TransactionDT` | buyer's hour (clock, or chosen in the demo) |
| `hour_unusual` | the card has 3 or more earlier purchases and none within 3 hours of this hour | hour histogram per card key | the same, kept per store and card |
| `has_hist` | the card has 2 or more earlier purchases, so a comparison is possible | count of earlier rows | the same |
| `amt_dev` | how far this amount is from the card's usual, capped at 1 | `abs(log(1+amount) - mean of earlier log(1+amount)) / max(std, 0.25) / 4` | the same formula |
| `days_since_prev` | days since the card's previous purchase (scaled; 0 if none) | `D3` | now minus the last purchase time |
| `d3_missing` | there was no previous purchase | `D3` missing | no earlier purchase |
| `prior_count` | how many earlier purchases the card has, scaled | `min(log(1+k) / log(1+cap), 1)`; `cap` is the training 99th percentile | the same, with `cap` read from `specialist_weights.json` |

Dataset columns used by the specialists: `TransactionDT`, `TransactionAmt`, `addr1`, `addr2`, `D1`, `D3`, `card1`,
`card2`, `card3`, `card5`, `ProductCD` (which merchant), `card6`, `P_emaildomain`, and `isFraud` as the label.

### The combiner (8 inputs, 9 weights)

For each specialist in the order Transaction, Identity, Geo, Behavior it takes two yes/no inputs: "is the band medium"
and "is the band high". Its output becomes the final `specialist_stack_band`.

**Bands.** A specialist's score is `low`, `medium` or `high` using two cutoffs: the 80th and 95th percentiles of the
scores on the pooled stacking slice (so roughly 20% of payments are at least medium and 5% are high). One set of
cutoffs is used for every merchant, so "high" means the same risk everywhere.

### The federated fraud model (9 features, 10 weights)

Amount above / below the merchant's p90 / p10 (`high_amount`, `micro_amount`), `country_mismatch`, `credit`,
`velocity`, `new_customer`, `night`, `card_age`, `days_since_prev`: the same definitions as above. It has no
`round_1`, `round_10`, `hour_unusual`, `has_hist`, `amt_dev`, `d3_missing` or `prior_count`.

### Column names, in weight order

These are the names stored under `"features"` in `fl_weights.json` and `specialist_weights.json`, in the order the
weights use. Position 0 of every weight list is the **bias**; the features follow from position 1.

**Federated fraud model** (`fl_weights.json`, 10 weights)

| Position | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|---|
| Column | bias | `high_amount` | `micro_amount` | `country_mismatch` | `credit` | `velocity` | `new_customer` | `night` | `card_age` | `days_since_prev` |

**Specialist models** (`specialist_weights.json`, one set per merchant)

| Model | Weights | Columns by position (0 is the bias) |
|---|---|---|
| `transaction` | 6 | 1 `high_amount`, 2 `micro_amount`, 3 `round_1`, 4 `round_10`, 5 `velocity` |
| `identity` | 4 | 1 `credit`, 2 `card_age`, 3 `new_customer` |
| `geo` | 2 | 1 `country_mismatch` |
| `behavior` | 8 | 1 `night`, 2 `hour_unusual`, 3 `has_hist`, 4 `amt_dev`, 5 `days_since_prev`, 6 `d3_missing`, 7 `prior_count` |
| combiner (`stack`) | 9 | 1 `transaction_medium`, 2 `transaction_high`, 3 `identity_medium`, 4 `identity_high`, 5 `geo_medium`, 6 `geo_high`, 7 `behavior_medium`, 8 `behavior_high` |

The combiner's inputs are named here for readability; the file stores the specialist order
(`transaction, identity, geo, behavior`) and the weights follow it, a "medium" then a "high" input for each.

**Dataset columns that feed the features** (IEEE-CIS `train_transaction.csv`)

| Dataset column | What it is | Features it feeds |
|---|---|---|
| `TransactionDT` | seconds from a reference time | `night`, `velocity`, `hour_unusual`, and the order of a card's purchases behind `has_hist`, `amt_dev`, `prior_count`; also the time split |
| `TransactionAmt` | the amount | `high_amount`, `micro_amount`, `round_1`, `round_10`, `amt_dev` |
| `addr2` | billing country code | `country_mismatch` |
| `card6` | debit or credit | `credit` |
| `D1` | days since the card was first seen | `card_age`, `new_customer` |
| `D3` | days since the card's previous purchase | `days_since_prev`, `d3_missing` |
| `card1`, `card2`, `card3`, `card5`, `addr1`, `P_emaildomain` | together the card key | `velocity` and all the behavior history features |
| `ProductCD` | product type, used as the merchant | which merchant a row belongs to, and the amount cutoffs behind `high_amount` and `micro_amount` |
| `isFraud` | the label | not a feature: what the models learn to predict |

### The merchants and their data

Each product type in the dataset acts as one merchant. "Training rows" are the first 70% of the training period, used
to fit the specialists; the last 20% of the whole window is the test period.

| Merchant | Training rows (specialists) | Fraud cases in the test period |
|---|---|---|
| W | 232,506 | 1,810 |
| C | 38,658 | 1,617 |
| R | 27,462 | 257 |
| H | 26,073 | 197 |
| S | 6,003 | 183 |

## Training the federated fraud model

```
each merchant: train 5 epochs on its own rows, starting from the current global weights
        |   sends back its 10 weights
        v
server: average them, weighted by how many rows each merchant has   -> new global weights
        |
        v   repeat for 30 rounds
save fl_weights.json  ->  every merchant node loads it at startup
```

Settings: plain gradient descent with step size 1.0, 5 epochs per round, fraud rows counted 10 times as much as
normal rows (fraud is rare), averaged by row count.

## Training the specialists

Same idea, with one extra step so each merchant ends up with its own version.

```
1. FedAvg      50 rounds: each merchant trains 6 epochs on its own rows from the global weights,
               the server averages the weights            -> shared starting weights
2. Fine-tune   each merchant trains 40 more epochs on ONLY its own rows, from the shared weights
                                                          -> one set of weights per merchant
3. Bands       each model's score becomes low / medium / high (about 5% high, next 15% medium),
               using one set of cutoffs for every merchant
4. Combiner    a small logistic model learns how to weigh the four bands
5. Save        specialist_weights.json  ->  each merchant node loads ITS OWN vertical's weights
```

Settings: Adam optimizer, fraud rows counted 10 times, a light L2 penalty (`1e-4`). FedAvg step size starts at
0.05 and decays to 0.005 over the 50 rounds (the optimizer restarts every round, so the step shrinks to keep the
weights steady). Fine-tune step size 0.01. The combiner trains 300 epochs at step size 0.1 on the stacking slice.

**Why the fine-tune?** A feature can mean different things at different merchants. For example, "billing country
mismatch" is almost always true at one merchant and rare at another, so the averaged weight can point the wrong way
for one of them. Fine-tuning lets each merchant correct the shared weights with its own data.

**Time split, so nothing leaks:** the first 80% of the window is training. Within it, the first 70% fits the four
specialists and the later 30% fits the combiner on scores the specialists have not seen. The last 20% is only used to
measure. Cutoffs and scales come from training rows only.

## What moves between nodes, and what does not

| Moves | Never moves |
|---|---|
| Weight lists (10 numbers, or the 4 short lists) | Transaction rows |
| The averaged weights back to each merchant | Card data (it never reaches a merchant either) |
| At decision time: one banded fact per model | The four individual specialist bands (they stay on the merchant node) |

## Human feedback: the model learns at once

Every human decision on a flagged payment (approve, decline, or a later chargeback) teaches the federated fraud
model **immediately**, inside the same request.

```
reviewer approves / declines  ->  label saved (the payment's 9 features + fraud or not fraud)
                              ->  the node recomputes its weights right away:
                                    start from the last federated weights
                                    train 30 short steps on: a sample of the node's own ordinary rows
                                                             + ALL its human labels
                              ->  the very next checkout is scored with the new weights
                              ->  the reply tells the reviewer this payment's model band before and after
```

- **One decision counts as 10% of the sample of ordinary rows.** So one label nudges the model, and a pattern
  reviewed a few times moves it further.
- **The ordinary rows keep the rest steady.** Training on the labels alone was tried and rejected: one "decline"
  pushed 99% of all payments into the high-risk band. With the ordinary rows mixed in, on real data from merchant W:
  training on the sample alone changes the band of 1.7% of payments; approving a high-risk payment moves that payment
  from 0.58 to 0.30 (about 7% of others change band); declining a low-risk-looking payment moves it from 0.10 to 0.32
  (about 21% of others change band, because that pattern is common).
- **Recomputed, never stacked.** The weights are always recalculated from the same starting point plus all saved
  labels. So they cannot compound, the order the labels arrived in does not matter, and no weight can move more than
  3.0 from the federated weights however many labels arrive.
- **It survives a restart.** The labels are saved to `.demo/labels.jsonl`; at startup the node applies them again.
- **It never blocks a decision.** If learning fails, the human's decision still goes through with the last good weights.
- **Clicking Retrain** still runs a short federated round over all registered nodes; the result becomes the new
  starting point, and this node's labels are then applied the same way.
- **Settings:** `INSTANT_LEARNING=0` turns it off; `INSTANT_LABEL_SHARE` (default `0.10`) is how much one decision counts.
- **Not covered yet:** the specialist models do not learn from labels. That needs their 16 features stored with each
  label and a sample of ordinary rows for them; each label already stores its decision id for this.

## Does the federation help?

- Merchants training **alone** score lower overall than the federated setup (0.744 vs 0.774 for the specialists),
  notably at the largest merchant.
- Plain averaging alone can hurt a merchant, which is what the fine-tune step fixes; the specialists' fine-tuned
  version helps the small merchants most (merchant S: 0.525 against 0.381 for the federated fraud model).
- Overall, the specialists are level with the federated fraud model (0.774 against 0.768). Details are in the README.

## Where the Flower agent comes in

Flower shows up in two separate places, and it helps to keep them apart.

**1. Making each decision (both models).** This is the Flower agent.

```
merchant node          SuperNode (merchant agent)          SuperLink (coordinator agent)
 builds the banded  --> fetches the guarded facts   --Grid--> asks a purpose-tagged question,
 facts, incl. both      from its own node                     re-checks the reply, runs the rules
 model_risk_band and    and replies with them                 and Jev's vote in code, emits the verdict
 specialist_stack_band
```

Both models take part the same way: each one's band is just a fact in the reply. Only banded, allowlisted values
travel, and the coordinator accepts a verdict only from the node that supplied the facts. (The Flower path could not
be run on the machine that built the specialists because `flwr` was not installed there; the same facts are covered
by the in-process tests, and by reading the code they take the same route. Please run it once with `flwr`.)

**2. Training the models.**
- **Federated fraud model:** a real Flower ServerApp and ClientApp, run locally with one simulated SuperNode per merchant.
- **Specialists:** trained by our own Python code that performs the same FedAvg steps over the five merchant slices,
  one process, one machine. The maths is the same, but it is not yet inside a Flower app. Moving it there is a
  moderate change: the per-merchant training step stays as it is, and the fine-tune becomes a second, client-side
  step after the global rounds.

## Where things live

| What | Where |
|---|---|
| Federated fraud model: Flower training | `cardguard/training/flower_app.py`, `fl.py` |
| Human-label retraining | `cardguard/training/retrain.py` |
| Specialists: training and export | `cardguard/specialists/export.py`, `federated.py`, `features.py` |
| Specialists: live scoring at checkout | `cardguard/specialists/live.py` |
| Measuring the ways of training | `python -m cardguard.specialists.experiment` |
