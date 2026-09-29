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

| | Original model | Specialist models (new) |
|---|---|---|
| What | one logistic regression | four small logistic regressions + one that combines them |
| Sees | 9 checkout features | 16 features in 4 families: transaction, identity, geo, behavior |
| Sends to the coordinator | one band, `model_risk_band` | one band, `specialist_stack_band` |
| Saved as | `fl_weights.json` (10 numbers) | `specialist_weights.json` (about 6 KB) |
| Trained by | `python -m cardguard.training.flower_app` | `python -m cardguard.specialists.export` |
| Training run | a Flower app (ServerApp + ClientApp), one simulated SuperNode per merchant | our own Python code running the same averaging steps, not yet inside a Flower app |

Logistic regression is just `score = bias + weight1 x feature1 + ...`, squashed into a 0-to-1 fraud probability. A
model that is only a list of numbers can be averaged, and its files are tiny.

## Training the original model

```
each merchant: train 5 epochs on its own rows, starting from the current global weights
        |   sends back its 10 weights
        v
server: average them, weighted by how many rows each merchant has   -> new global weights
        |
        v   repeat for 30 rounds
save fl_weights.json  ->  every merchant node loads it at startup
```

Options: `FL_ROBUST=1` averages with a median so one bad node can't dominate; `FL_DP_NOISE` adds differential privacy.

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

## Human feedback and retraining

A reviewer's decision on a flagged payment becomes a label stored on that merchant's node: its 9 features plus
fraud / not fraud. Clicking **Retrain** runs a short federated round over all registered nodes, with each human label
counting as much as 50 ordinary rows, and then a short fine-tune on that merchant's own labels.

- The labels are saved to `.demo/labels.jsonl` and survive a restart.
- The retrained weights are kept **in memory only**, so a restart returns to `fl_weights.json`.
- Human labels do not retrain the specialists yet. Each label already stores its decision id, so that is a small
  next step.

## Does the federation help?

- Merchants training **alone** score lower overall than the federated setup (0.744 vs 0.774 for the specialists),
  notably at the largest merchant.
- Plain averaging alone can hurt a merchant, which is what the fine-tune step fixes; the specialists' fine-tuned
  version helps the small merchants most (merchant S: 0.525 against 0.381 for the original model).
- Overall, the specialists are level with the original model (0.774 against 0.768). Details are in the README.

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
- **Original model:** a real Flower ServerApp and ClientApp, run locally with one simulated SuperNode per merchant.
- **Specialists:** trained by our own Python code that performs the same FedAvg steps over the five merchant slices,
  one process, one machine. The maths is the same, but it is not yet inside a Flower app. Moving it there is a
  moderate change: the per-merchant training step stays as it is, and the fine-tune becomes a second, client-side
  step after the global rounds.

## Where things live

| What | Where |
|---|---|
| Original model: Flower training | `cardguard/training/flower_app.py`, `fl.py` |
| Human-label retraining | `cardguard/training/retrain.py` |
| Specialists: training and export | `cardguard/specialists/export.py`, `federated.py`, `features.py` |
| Specialists: live scoring at checkout | `cardguard/specialists/live.py` |
| Measuring the ways of training | `python -m cardguard.specialists.experiment` |
