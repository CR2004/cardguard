# CardGuard - context for Claude Code

Hackathon project for the **Flower Labs Collaborative Agent Hackathon, Stanford, Tue Sep 29 2026**
(9:30am-7:30pm PT, demos start 5:15pm, submission reminder 4:30pm). Team of 2-4.

## The pitch
Several parties' agents (merchant, and optionally issuer / network) make one card-payment risk
decision together, while **card data never enters any model's context**. Only banded, allowlisted
facts cross node boundaries; every disclosure and every blocked leak is logged; a human approves
risky charges. A federated fraud model (Flower, FedAvg) is trained across merchants who each see
a different fraud type, and its score crosses the wire only as a band.

Wording rules for anything user-facing: say "shrinks PCI scope" or "card data never enters a
model's context". NEVER say "PCI compliant" or "out of PCI scope" (AI that influences payment
decisions can stay in scope). Vault companies (Skyflow, VGS, Basis Theory) already tokenize for a
single agent; our novelty is multi-party agent collaboration with a verifier and human gate.

## Judging criteria (weight all equally)
Impact, Innovation, Use of Flower (Flower Agent + SuperGrid), Technical execution, Demo and
delivery, Safety and oversight. **Bonus points for using Flower's Endeavor model.**
Submission: team details, short description, **published Flower Hub app**, GitHub repo link.
Demo: 3-5 minutes.

## Files
| File | Role |
|---|---|
| guard.py | WIRE_SCHEMA allowlist, find_leaks() (Luhn card numbers, expiry, CVV, injection), strip_for_wire(), Ledger |
| fl.py | Synthetic data for 3 merchants, logistic regression (9 weights), local_train, fedavg, catch_rates, report |
| flower_app.py | Flower 1.39 ServerApp + ClientApp around fl.py; run_simulation with 3 SuperNodes; writes fl_weights.npy |
| coordinator.py | rules() + ask_jev(); decide() = more cautious vote wins, approve with Jev confidence < 0.8 -> step_up |
| explain.py | Endeavor one-sentence explanation via OpenAI-compatible Responses API; display only; template fallback |
| merchant.py | Flask app on :4242. Stripe test mode (refuses sk_live_), banded facts, model_risk_band, review queue |
| checkout.html | Buy page, presets, sabotage toggle, review queue, ledger, "card numbers seen by coordinator" |
| test_*.py | 37 offline tests. Jev, Endeavor, Stripe are faked/mocked |

## Invariants - never break these
1. Raw card data (number, expiry, CVV), Stripe payment-method IDs and customer free text never
   leave merchant.py. Everything crossing a boundary goes through `Ledger.disclose()` -> `strip_for_wire()`.
2. New facts must be added to `WIRE_SCHEMA` as a closed vocabulary (banded values, never free text).
   Tokens are letters-only (`tok_[a-p]{16}`) so they can never look like a card number.
3. Jev and Endeavor only ever see banded facts / verdict + cites. Jev is not adversary-hardened.
4. The final verdict is computed in code. Models vote or explain; they never decide alone.
5. Model/endpoint failures degrade gracefully (rules decide, template explains). Never block on them.
6. Test card numbers and synthetic data only. Never put keys in the repo; env vars only.
7. Keep all tests passing: `python -m pytest -q`. Add a test for every new safety property.

## Commands
    pip install -r requirements.txt
    python -m pytest -q        # 37 tests
    python fl.py               # federated vs local-only table
    python flower_app.py       # Flower simulation, writes fl_weights.npy
    python merchant.py         # http://127.0.0.1:4242 (MOCK mode without Stripe keys)

## Env vars
- STRIPE_SECRET_KEY=sk_test_... , STRIPE_PUBLISHABLE_KEY=pk_test_...
- TYPESAFE_API_KEY (Jev), optional JEV_MODEL to pin a version
- Endeavor: FLWR_RUNTIME_BASE_URL / FLWR_RUNTIME_API_KEY are injected inside an AgentApp;
  elsewhere ENDEAVOR_BASE_URL / ENDEAVOR_API_KEY. ENDEAVOR_MODEL defaults to
  `flower-endeavor-v1.0` (taken from another team's repo - CONFIRM with the Flower team).
- Flower-served models: FLWR_MODEL_API_KEY (from flower.ai Profile -> Settings -> API Keys),
  model names in OpenRouter format. Nebius Token Factory: FLWR_MODEL_API_ENDPOINT=
  https://api.tokenfactory.tf-ca1.nebius.com/v1/responses , key shared in Slack #hackathon_stanford_2026.

## Flower facts (verified against flwr 1.39.0 locally)
- Imports: `from flwr.app import ArrayRecord, Context, Message, MetricRecord, RecordDict`,
  `from flwr.clientapp import ClientApp`, `from flwr.serverapp import Grid, ServerApp`,
  `from flwr.serverapp.strategy import FedAvg` (also DifferentialPrivacyClientSideFixedClipping etc.).
- ClientApp: `@client.train()` handler `(msg, context) -> Message(content=RecordDict(...), reply_to=msg)`;
  weights in `msg.content["arrays"]`, reply metrics must include `"num-examples"`.
- ServerApp: `@server.main()` `(grid, context)`; `FedAvg(...).start(grid=..., initial_arrays=..., num_rounds=...)`
  returns a Result with `.arrays`.
- `flwr.simulation.run_simulation(server_app, client_app, num_supernodes)`.
- Flower Agent / SuperGrid: CLI `flwr login supergrid`, `flwr build`, `flwr run . supergrid --stream`,
  `flwr list supergrid`, `flwr log <run-id> supergrid --show`, `flwr supernode list supergrid --verbose`.
  Starter: `flwr new @flwrlabs/hackathon-collab-agent-recipe` and the Collaborative AgentApp
  (https://flower.ai/apps/flwrlabs/collaborative-agent), which has Grid tools to sample other
  agents, push messages and pull replies. Docs: https://flower.ai/docs/agent/
- **Each SuperGrid task times out 5 minutes after it starts Running.** One decision per task.
- Flower Hub accepts only .py .toml .md .yaml .json .jsonl in a published app (no HTML/JS),
  and rejects a FAB that declares both agentapp and serverapp surfaces (per team Axomic's notes).
- The Agent API is NOT verified yet. Read the docs / tutorial code before writing AgentApp code;
  do not guess signatures.

## Jev (TypeSafe System One) facts
    from typesafe_sdk import TypeSafeClient, Choice, Score, Noul
    client.system_one(state={...}, questions={"k": Choice(instructions=..., criteria={...})})
    resp.answers["k"].choice / .confidence / .probabilities   (Choice)
    .score / .confidence (Score)   .noul (Noul, 0..1)   resp.model
Limits: 1,200 req/min, 64k tokens. No arithmetic, weak on dates, literal about negations.

## Status
Done: everything in the file table, all 37 tests passing, Flower simulation verified.
Not yet run against real services: Stripe test mode, Jev, Endeavor.
Left: (1) credentials + endpoints working, (2) coordinator (and ideally merchant) as Flower
AgentApps on SuperGrid talking over Grid, (3) optional DP on FedAvg / issuer node,
(4) publish to Flower Hub + GitHub + backup video by 4pm, (5) pitch.

## Working style
- Small verified steps. Run tests after each change. Don't rewrite working modules wholesale.
- Before any Flower Agent code, check the installed flwr version and read the actual docs/tutorials.
- If something external is missing (key, endpoint, allow-listing), stop and tell me exactly what to get.
- Keep the demo path working at all times; it's more important than new features.
