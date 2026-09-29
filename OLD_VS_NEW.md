# CardGuard: what it was, what changed, and how the pieces fit

A plain-words guide for the team. For the measured numbers and caveats see the
[Fraud specialists section of the README](README.md#fraud-specialists-experiment-branch-specialists-experiment).

## The goal

When someone pays online, the system decides: **approve, ask a human, or decline**. No AI model ever sees the
card number. Models only see rough labels such as "amount: high" or "risk: medium".

## What a "node" is

A node is one participant that keeps its own data and never hands it over.

| Kind | Who | What it holds |
|---|---|---|
| **Merchant node** | One store. In the demo, one program on the laptop. | Its own customers' transactions and each card's history |
| **Coordinator** | The decision-maker | No customer data, only the rough labels sent to it |

For training we use a real dataset (IEEE-CIS, 590,540 transactions) and split it into **5 groups by product type
(W, C, R, H, S). Each group is treated as one merchant.** So the 5 merchants are a simulation. Each one trains only
on its own slice of rows, and each has its own typical order size, fraud rate and customers.

## Before and after

| | Earlier | Now |
|---|---|---|
| Fraud model | One scorer with 9 inputs | The same scorer **plus** four specialist models |
| Inputs | Amount high/low, country mismatch, credit card, purchase count, new customer, night, card age, days since previous | Those, regrouped into four families, plus round amounts, "unusual hour for this card", "amount unusual for this card", history length |
| What crosses the wire | 8 banded facts | The same 8 plus **one new fact**, `specialist_stack_band` (9 in total) |
| Federated training | Flower app, 10 numbers per merchant | The 10-number model is unchanged. The specialists add 4 more small models, trained with the same averaging idea. |
| Per-merchant tuning | Only after human reviews | Every merchant fine-tunes the shared weights on its own rows |
| Fraud-suspected cases | Only medium-risk cases reached a human. **A decline was final.** | Medium-risk cases **and soft declines** wait for a human. A hard decline (failed CVC check) stays final. |
| The human's input | Approve or decline | Approve or decline, a **reason from a fixed list**, an optional private note, a reviewer id |
| Audit of human decisions | Not recorded | Tamper-evident log of every opinion and every expiry |
| Human labels | Kept in memory, **lost on restart** | Saved to a file, reloaded at startup, with the decision id attached |
| Visibility | None | `GET /review-stats`: how often the model and reviewers agree, how often a decline is overturned, time to review |

## The four specialists

Each sees one family of signals only, and each returns a low / medium / high band.

| Specialist | Looks at |
|---|---|
| Transaction | Amount vs this merchant's usual, round amounts, how many purchases in 24 hours |
| Identity | Credit vs debit, card age, first time this merchant sees the card |
| Geo | Card country differs from the buyer's country |
| Behavior | Is this purchase unusual for this card's own history: hour, amount, days since the last one |

A small final model combines the four bands into one band, `specialist_stack_band`. The four individual bands stay
in the merchant's local audit note, so a reviewer can see *which* family fired, but only the combined band leaves
the node.

## What each node holds, and what is trained on what

- **Trained on:** each merchant's own transactions, labelled fraud or not fraud.
- **Merchant-specific (never shared):** the raw rows, the amount cutoffs ("high" means high *for this store*),
  each card's history, and the fine-tuned weights.
- **Shared across merchants:** the averaged starting weights.
- **The only thing that moves between nodes:** lists of numbers called weights. Never rows.
  Before: 10 numbers. Now: those 10, plus four short lists (6, 4, 2 and 8 numbers) and 9 numbers for the final combiner.

## How the federated learning works

```
Each merchant keeps its own transactions (never shared)
        |
        |  train on its own rows only
        v
   a small list of numbers ("weights")   <-- the ONLY thing sent out
        |
        v
   the server averages the lists from all 5 merchants  -->  one shared list
        |
        v   (repeat about 50 times)
   each merchant then adjusts the shared list a little on its OWN data ("fine-tune")
        |
        v
   saved to specialist_weights.json  -->  each merchant node loads its own version
```

Two different ways of splitting the data are involved:

- **Across merchants (horizontal):** same kind of data, different customers. This is what the averaging above does.
  It was already there.
- **Across signal types (vertical, new):** the specialists see different columns of the same transactions. Their
  weights mean different things, so they cannot be averaged. A final model combines their bands instead.

## What happens at a live checkout

```
buyer pays  -->  processor verifies the card (the merchant never sees the number)
   |
   v
merchant node builds banded facts from its own data:
   amount band, country match, card type, CVC result, purchase count, new customer,
   model_risk_band (the old model), specialist_stack_band (the four specialists)
   |
   v   every fact is checked against the allowed list; anything else is blocked and logged
coordinator: rules score + Jev's vote, more cautious wins  -->  verdict computed in code
   |
   +-- approve ......... charge the card
   +-- hard decline .... void, final (CVC failed)
   +-- step-up or soft decline .... waits in the review queue (voided after 1 hour if nobody looks)
                                        |
                                        v
                          human picks approve / decline + a reason
                                        |
                          audit log entry  +  saved training label
                                        |
                          later: a retrain round spreads the lesson to the other merchants
```

## Where the model got better, honestly

**Not much on accuracy.**

- With **every column the dataset offers**, the specialists reach AUC **0.866** against **0.773** for the old model.
  But those columns (purchase counters, match flags, device recognition) were engineered by the company that owned
  the data, and **our checkout does not collect them**.
- With only what **our checkout can really compute**, the specialists score about **0.77**, the same as the old model.

What did get better:

- **Explainability:** a reviewer sees which signal family fired.
- **Per-merchant tuning:** fine-tuning helps the small merchants (for example merchant S: 0.525 with fine-tuning,
  0.344 without).
- **Human oversight:** fraud flags can be reviewed, and reviews become audited labels.
- **Groundwork:** the design is ready for richer signals (device, identity, geography providers) if they become
  available.

## Two things to be clear about

1. **The specialist training does not run through Flower.** The original model has a real Flower app
   (`cardguard/training/flower_app.py`). The specialists' averaging is plain Python that simulates the 5 merchants
   in one process. The maths is the same, but it is not yet on real Flower SuperNodes.
2. **Live, there is one merchant node, not five.** It loads its own vertical's version of the weights and scores each
   checkout. The five-merchant averaging happens offline, before the demo, when `specialist_weights.json` is generated.

## Where things are

| What | Where |
|---|---|
| Specialist scoring at checkout | `cardguard/specialists/live.py` |
| Training and export of the specialists | `cardguard/specialists/export.py` -> `specialist_weights.json` |
| Offline experiments and measurements | `cardguard/specialists/experiment.py`, `federated.py` |
| Human review, audit, saved labels | `cardguard/payment_processing/review_store.py`, `merchant.py` |
| The new wire fact and its weight | `cardguard/decision/guard.py`, `cardguard/decision/coordinator.py` |
| The original federated model | `cardguard/training/fl.py`, `flower_app.py` -> `fl_weights.json` |

## Settings you may want to change

| Setting | Effect |
|---|---|
| `SPECIALIST_WEIGHTS=none` | Turn the specialists off; the node decides as before |
| `SECOND_LOOK_ON_DECLINE=0` | Soft declines are refused on the spot again, as they were earlier |
| `TWO_REVIEWER_ABOVE_CENTS=N` | Approving above N cents needs two different reviewers |
| `LABELS_FILE`, `REVIEW_AUDIT_FILE` | Where labels and the review audit are saved (default `.demo/`) |
