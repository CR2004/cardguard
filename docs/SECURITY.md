# CardGuard security and privacy

What CardGuard protects, from whom, and which control stops each attack. This describes a hackathon prototype running
on Stripe TEST data; it shrinks PCI scope rather than certifying anything, and makes no compliance claim.

## What is protected

- **Card data** (number, expiry, CVC) exists only in Stripe Elements in the browser and at Stripe. The merchant node
  holds a `pm_...` id and never logs it; React, the Flower Grid, the ledger and every model never see card data.
- **Each party's private data**: the store's exact amounts and card history, the bank's cardholder history, the raw
  model scores. Only closed-vocabulary bands leave a node.
- **The decision itself**: computed in code, bound to the node that supplied the facts, and final only through the
  Policy Gate or an authenticated person.

## Threat model

| Attack (who) | Stopped by | Seen as |
|---|---|---|
| Put the card on the wire (compromised merchant agent) | Wire guard: closed vocabulary, Luhn scan, length cap, at the sender and again at the receiver | BLOCKED ledger entries; card numbers shared: 0 |
| Prompt injection through customer text (buyer) | The model-driven agent's draft is guarded and integrity-checked against facts computed in code; card-like text is refused before any model call | Draft blocked, or "altered draft ignored" |
| Injection into the decision models (anyone) | Structural: Jev and Endeavor receive only vocabulary words and fixed code strings | No path exists (`tests/test_boundary.py` enumerates every allowed fact combination) |
| Slow leak through allowed values (compromised merchant) | One disclosure per decision, three attempts, ten per card per hour, receiving-side verifier | Rate-limit BLOCKED entries |
| Forge a verdict for another merchant (hostile SuperNode) | Verdict accepted only from the node that fetched the facts; two replies to one decision go to a person | Verdict rejected |
| Poison the network memory (hostile SuperNode) | Sightings keyed by Flower's authenticated node id | Alerts name the node |
| Approve your own held payment, poison retraining (buyer) | A reviewer credential on every human action | Refused |
| Sweep the bank for a card's whereabouts (compromised merchant) | Signed requests per merchant, request skew limit, per-card limits on travel questions and buyer regions | Refused by the bank |
| A compromised or failing model provider | Jev can only add caution; Endeavor is display-only and scanned; every model call degrades to rules or a template | Delays, never an approval |

No single control is load-bearing.

## Privacy boundaries

1. Everything that crosses a node boundary goes through `Ledger.disclose()` and `strip_for_wire()` against
   `WIRE_SCHEMA`, a closed vocabulary of banded values (`cardguard/decision/guard.py`, read-only on disk on purpose).
2. Card references are letters only (`tok_` + 16 letters from a-p), so they can never look like a card number. Stripe's
   raw fingerprint never leaves `stripe_processor.py`.
3. On the Grid only two message shapes exist, both value-scanned and size-capped; the coordinator re-guards every reply.
4. The bank sees a card reference and coarse regions, never an amount, a city or a card. Its history never leaves it.
5. The UI animates only events the nodes report (`GET /trace/<id>`); trace steps are leak-scanned and card references
   are masked before they are served.

Link by link, what is sent and what is never sent: [ARCHITECTURE.md](ARCHITECTURE.md#what-crosses-each-link).

## Rogue-node protection

The **Rogue node** scenario makes the store's agent try to push card-like values onto the wire. Each attempt is stopped
at the sender by the wire guard, logged in the hash-chained ledger, and shown on the graph as "Stopped at the boundary";
nothing reaches the Policy Gate and nothing is charged. A hostile SuperNode that answers for another merchant is refused
by the verdict binding, and duplicate replies send the decision to a person.

## Deterministic policy

The Policy Gate (`cardguard/decision/coordinator.py`) is fixed rules in code: weighted facts, a review line and a decline
line, a hard decline on a failed CVC check, and a review floor for an implausible round-2 answer. Jev's vote is applied
as "the more cautious vote wins", and an approval Jev is less than 80% sure of goes to a person. Endeavor writes a
sentence after the verdict from the verdict and its recognised cites only. A model can never approve on its own, make
a decision less cautious, choose a Grid tool or see a Grid payload: Jev's vote can only move a payment towards review
or decline, and Endeavor's sentence cannot change the verdict.

## Reviewer authorization

Human actions (review decisions, chargebacks, dispute evidence, retraining, joining the network) require
`REVIEWER_TOKEN`, compared in constant time. With demo controls on, the node gives only its own local page an HttpOnly,
SameSite=Strict session cookie derived from the token, so the token itself never reaches the browser. This demo
reviewer session is a convenience for the stage, not a production identity system. Host headers are allow-listed
(`MERCHANT_HOSTS`) against DNS rebinding, and checkouts are rate-limited per client. Never put the demo behind a proxy
that rewrites Host to localhost.

## Audit and exports

Every disclosure and every blocked attempt is written to a hash-chained, append-only ledger (HMAC-keyed when
`AUDIT_KEY` is set), and the review decisions to their own chained audit. Exported PDF and JSON reports are built from the same
trace and pass their own privacy scan before download: see [REPORTING.md](REPORTING.md#privacy-scan).

## Limits

- Jev is not adversary-hardened; it is safe here only because it receives nothing an attacker controls.
- The bank's history is synthetic and its card reference is a demo correlation over Stripe TEST data.
- The nodes run on Flask's development server; the demo reviewer session and in-memory demo state are not production
  infrastructure.
