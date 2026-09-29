# CardGuard - context for Claude Code

Hackathon project for the **Flower Labs Collaborative Agent Hackathon, Stanford, Tue Sep 29 2026**
(9:30am-7:30pm PT, demos start 5:15pm, submission reminder 4:30pm). Team of 2-4.

## The pitch
Several parties' agents (merchant, bank, coordinator, network) make one card-payment risk decision
together, while **card data never enters any model's context**. The card goes from Stripe Elements
to Stripe; the merchant holds only a Stripe token and a letters-only card reference, and only
banded, allowlisted facts cross node boundaries. Stripe is the only payment rail; the bank node only
attests (bands about the cardholder, and a round-2 travel_check), from private history it keeps. Every disclosure and every blocked leak is logged; a human approves risky charges; the
verdict is computed in code. A federated fraud model (Flower, FedAvg) is trained across merchants on
real transactions (IEEE-CIS), and its score crosses the wire only as a band.

Wording rules for anything user-facing: say "shrinks PCI scope" or "card data never enters a
model's context". NEVER say "PCI compliant" or "out of PCI scope". This is the same shape as
client-side encryption and hosted card fields that processors already use; our addition is the
multi-agent fraud decision around it, the federated model, and the verifiable boundary.

## Judging criteria (weight all equally)
Impact, Innovation, Use of Flower (Flower Agent + SuperGrid), Technical execution, Demo and
delivery, Safety and oversight. **Bonus points for using Flower's Endeavor model.**
Submission: team details, short description, **published Flower Hub app**, GitHub repo link.
Demo: 3-5 minutes.

## Layout
| Path | Role |
|---|---|
| cardguard/decision/guard.py | WIRE_SCHEMA allowlist, find_leaks() (Luhn, expiry, CVV, injection), strip_for_wire(), Ledger. File is read-only on disk on purpose. |
| cardguard/decision/coordinator.py | rules() + ask_jev(); hard decline on cvc_check=fail; decide() re-guards input, more cautious vote wins, retry-then-rules; Verifier (receiving side); needs_travel_check() (round-2 trigger), REVIEW_FLOOR, hold_unanswered() |
| cardguard/decision/explain.py | Endeavor one-sentence explanation from verdict + recognised cites only; template fallback |
| cardguard/data/ieee_cis.py | Real transactions: 5 ProductCD verticals = 5 merchants, 9 features relative to each vertical, time holdout, cache in datasets/ |
| cardguard/training/fl.py | numpy logistic regression, local_train, fedavg; synthetic 3-merchant data for offline tests; real-data report |
| cardguard/training/flower_app.py | Flower 1.39 ServerApp + ClientApp, one SuperNode per merchant; writes fl_weights.json at repo root |
| cardguard/payment_processing/merchant.py | Merchant node :4242. Stripe token in, banded facts, relative amount bands, federated band, ledger rate limits, review queue, attack modes |
| cardguard/payment_processing/stripe_processor.py | The processor: Stripe test mode, verify / authorize / void; live keys refused; module-level instance needs sk_test_/pk_test_ keys (tests set fake ones in conftest) |
| cardguard/payment_processing/errors.py, processor_base.py | ProcessorReject; letters-only ids, one-use verifications, chained audit |
| cardguard/decision/llm.py, cardguard/httpjson.py | the one OpenAI-compatible client (endpoint+key chosen as a pair) and `ask()`; the one JSON-over-HTTP helper |
| cardguard/payment_processing/agent_llm.py | Deliberately model-driven merchant agent for the live injection demo; contained by guard + integrity check |
| web/ | The investigation UI (pnpm + Vite + React + TypeScript + Motion), built to web/dist and served by the merchant node at /. Stripe Elements card field, four scenarios, the Investigation Graph animated only from GET /trace/<id>, policy gate, human review, node records |
| cardguard/payment_processing/trace.py | Live investigation trace per checkout (display only): leak-scanned steps, masked card references, single-use trace ids, the gate's rules line by line |
| cardguard/bank/node.py, client.py | Bank attestation node :4243 (signed requests; synthetic private cardholder history: city, hours since last in-person use, declines). Answers only issuer_behavior / issuer_recent_declines (round 1) and travel_check (round 2, once per decision). Never sees a card, never moves money. client.py = the merchant's signed client + coarse regions |
| cardguard/agentapp/agent_app.py | The Flower AgentApp: coordinator role (SuperLink: get_nodes/push/pull, Verifier, decide, explain, emits `cardguard.verdict`) and merchant role (SuperNode: fetches guarded facts from its node's /agent/facts, push_reply_message). Code drives every Grid call. |
| cardguard/agentapp/launch.py | Starts one decision as a run via the SuperLink Control API (local FAB + user prompt + `agent.decision-id` override) and reads the verdict from the event stream |
| cardguard/decision/audit.py | Hash-chained append-only log used by the merchant ledger and the processor audit |
| cardguard/decision/network.py | network_velocity_band: same card reference at 1/2/3+ merchants in 10 min; NetworkWatch persisted in context.state (run series) |
| cardguard/training/retrain.py, join.py, privacy.py | human labels -> federated round + local fine-tune; node registry + join CLI; DP wrapper (server-side fixed clipping + RdpAccountant, equal node weights) |
| cardguard/agentapp/dispute_agent.py | chargeback evidence + drafted response, leak-scanned (cvv_words=False for displayed text) |
| pyproject.toml | Python project + `[tool.flwr.app]` (agentapp component, fab-include: only .py/.json/.md/LICENSE) |
| run_demo.py | Loads .env, checks Stripe TEST keys, mints the reviewer token and a merchant-to-bank secret, starts the bank attestation node and the merchant; `--federation local-agent`, `--stores a,b,c` |
| tests/ | Offline tests. Jev, Endeavor, the LLM and the Stripe SDK are faked; the bank node runs in-process (tests/bank_fake.py); real-data tests skip without the CSV |
| datasets/ | IEEE-CIS train_transaction.csv (git-ignored, Kaggle competition licence) and the feature cache |

## Invariants - never break these
1. Card data (number, expiry, CVC) exists only in Stripe Elements (browser) and at Stripe. The merchant
   holds a pm_ token and never logs it; the ledger never holds the token, a card number, or a
   verification id. (The self-hosted issuer node was removed on Sep 29; Stripe test mode is the only processor.)
1c. The bank node attests, it never processes a payment and never sees a card. It knows a card only by the
   pseudonymous card_ref (keyed hash of Stripe's TEST card fingerprint; the raw fingerprint never leaves
   stripe_processor.py) plus the card's coarse issuing region. Its history (city, hours, declines) never
   leaves it; only the WIRE_SCHEMA bands do. This is a demo correlation over Stripe TEST data, not an
   issuer-network identity protocol.
1d. Round 2 is one targeted question, asked only when coordinator.needs_travel_check() holds, only of the
   node that answered round 1, and it returns only travel_check. implausible raises approve to step_up
   and never declines; the model never sees travel_check; an unanswered round 2 holds for a person.
1a. A Grid verdict is accepted by the merchant only if it names the decision and came from the
   SuperNode that fetched the facts through /agent/facts (node id recorded there). Two nodes
   answering one decision = conflicting replies = human review. Network-velocity identity is the
   authenticated node id; declared stores count separately only with agent.trust-declared-stores.
1b. On the Grid, only two message shapes exist: the coordinator's question {"purpose","decision_id"}
   and the merchant's reply {"purpose","decision_id","facts"} or {"error"}. Both pass safe_wire_text
   (value scan + size cap) before sending; the coordinator re-guards every reply with Verifier.
   No model ever chooses a Grid tool or sees a Grid payload.
2. Everything crossing a node boundary goes through `Ledger.disclose()` -> `strip_for_wire()`.
   New facts must be added to `WIRE_SCHEMA` as a closed vocabulary (banded values, never free text).
   Tokens are letters-only (`tok_[a-p]{16}`) so they can never look like a card number.
3. Jev and Endeavor only ever see banded facts / verdict + recognised cites. Jev is not
   adversary-hardened. tests/test_boundary.py proves no attacker byte reaches either.
4. The final verdict is computed in code. Models vote or explain; they never decide alone.
   A model-driven agent's draft is validated, never sent; altered facts are an integrity failure.
5. Model/endpoint failures degrade gracefully (rules decide, template explains). Never block on them.
   Stripe is the one hard dependency: no test keys, no payment (no mock mode). The bank node is optional:
   without it its facts are "unknown" and no round 2 is asked.
6. Synthetic test cards and synthetic or licensed research data only. Never put keys in the repo.
7. Keep all tests passing: `python -m pytest -q`. Add a test for every new safety property.

## Commands
    uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
    source .venv/bin/activate
    python -m pytest -q                          # all tests, offline
    python -m cardguard.training.fl              # federated vs local-only tables
    python -m cardguard.training.flower_app      # Flower simulation, writes fl_weights.json
    pnpm --dir web install && pnpm --dir web build   # the UI (web/dist); also: typecheck, lint, test
    python run_demo.py                           # bank :4243 + merchant :4242 -> http://127.0.0.1:4242 (Stripe TEST keys in .env)
    flwr build                                   # FAB of the AgentApp (only .py/.json/.md/LICENSE inside)

Local Flower deployment for the AgentApp (verified 1.39.0). Port 8000 is taken by a local LLM
server on this laptop, so the SuperLink Runtime API runs on 8010:
    # ~/.flwr/config.toml:  [superlink.local-agent]  address = "127.0.0.1:8010"  insecure = true
    python scripts/run_superlink.py              # = flower-superlink --insecure --host 127.0.0.1 --port 8010 (venv on PATH!)
    python scripts/run_supernode.py              # = flower-supernode --insecure --superlink 127.0.0.1:9092 ...
    python run_demo.py --federation local-agent  # checkout -> launch.decide_over_flower -> AgentApp run
    python -c "from cardguard.agentapp.launch import decide_over_flower; print(decide_over_flower('local-agent','latest'))"
`flwr run . local-agent` refuses AgentApps ("a user prompt is required"); the launcher passes
user_prompt via StartRunRequest exactly like `flwr chat` does. The SuperLink spawns
`flower-superexec` by name, so the venv's bin must be on PATH.

## Env vars
- TYPESAFE_API_KEY (Jev), optional JEV_MODEL to pin a version
- Endeavor: FLWR_RUNTIME_BASE_URL / FLWR_RUNTIME_API_KEY are injected inside an AgentApp;
  elsewhere ENDEAVOR_BASE_URL / ENDEAVOR_API_KEY. ENDEAVOR_MODEL defaults to
  `Flwrlabs/endeavor-v1.0` (confirmed Sep 29: the model to call with the Flower API key).
- STORES=store-a,store-b,store-c: one merchant node fronting several stores (fraud-ring demo).
- FL_DP_NOISE / FL_DP_CLIP: differential privacy on flower_app training (0 = off).
- REVIEWER_TOKEN: credential for human actions on the merchant node (review, chargeback, evidence,
  retrain, join). run_demo.py mints and prints it; the page asks once. CHECKOUT_RATE_PER_MINUTE (30).
- DEMO_CONTROLS=1 (run_demo sets it; tests set it in conftest): the page may choose buyer country,
  hour, attack mode and the model-driven agent; the merchant shows detailed processor reasons; "latest"
  decision ids are allowed. Unset = production: country from geolocate(), hour from the clock, no
  attacks, generic refusal text, explicit decision ids only.
- MERCHANT_HOSTS: Host allowlist (DNS-rebinding guard). AUDIT_KEY (hex): HMAC-keyed hash chains.
  FL_ROBUST=1: FedMedian instead of FedAvg in flower_app (hostile-node resistance).
- STRIPE_SECRET_KEY / STRIPE_PUBLISHABLE_KEY (test keys, required). MERCHANT_ID, MERCHANT_VERTICAL (W/C/R/H/S).
- BANK_URL / BANK_SECRET (merchant side) and BANK_MERCHANTS=id:secret (bank side): run_demo.py mints and wires
  them; the bank process never receives the Stripe keys or the reviewer token.
- .env at the repo root is loaded by run_demo.py and scripts/run_super*.py (cardguard/dotenv.py); .env.example lists everything.
- LLM_BASE_URL / LLM_API_KEY / LLM_MODEL only for the live injection demo.
- Flower-served models: FLWR_MODEL_API_KEY (flower.ai Profile -> Settings -> API Keys); model `Flwrlabs/endeavor-v1.0`.

## Flower facts (verified against flwr 1.39.0 locally; see the installed source, not memory)
- Training: `from flwr.app import ArrayRecord, Context, Message, MetricRecord, RecordDict`,
  `from flwr.clientapp import ClientApp`, `from flwr.serverapp import Grid, ServerApp`,
  `from flwr.serverapp.strategy import FedAvg`; `run_simulation(server_app, client_app, num_supernodes)`.
- AgentApp: `from flwr.agentapp import AgentApp, AgentSession`; `@app.main() def main(agent, context)`;
  `agent.prompt`, `agent.events.emit()`, `agent.grid.tools()` / `agent.grid.call()`. SuperLink-side
  tools: get_nodes, push_messages, pull_messages(timeout<=300). SuperNode-side: push_reply_message.
  A SuperNode run's prompt is JSON {message_id, src_node_id, payload}. Credentials: FLWR_RUNTIME_BASE_URL /
  FLWR_RUNTIME_API_KEY injected per task; OpenAI client with `responses.create`.
- SuperGrid: `flwr login supergrid`, `flwr build`, `flwr run . supergrid --stream`, `flwr list`,
  `flwr log <run-id> supergrid --show`. Local: `flower-superlink --insecure`, `~/.flwr/config.toml`
  `[superlink.local-agent] address="127.0.0.1:8000" insecure=true`, `flwr run . local-agent`.
- Each SuperGrid task times out 5 minutes after it starts Running. One decision per task.
- Run series: launch.py stores the series id in .demo/series_<superlink>.txt and passes it to
  StartRunRequest, so the coordinator's context.state (the network table) persists across decisions.
  Verified live: three stores, three runs, third purchase flagged with an alert naming all three.
  A restarted SuperLink forgets saved series: the launcher then starts a new series once (logged)
  instead of falling back to in-process on every decision.
- The AgentApp's runtime env is built by `uv sync` from pyproject.toml on every run: every pin there
  must resolve together with flwr's own pins (flwr 1.39 pins cryptography <47). pip-audit flags
  cryptography 46.x; that is an upstream constraint, not something to "fix" in pyproject.
- DP: DifferentialPrivacyServerSideFixedClipping(strategy, noise_multiplier, clipping_norm,
  num_sampled_clients, accountant=RdpAccountant(PrivacyConfig(...))); strategy.privacy_spent().
  Accounted aggregation requires equal client weights: report num-examples=1 in DP mode.
  Our coordinator pulls once with agent.pull-timeout (default 90 s, capped 300); merchant GRID_TIMEOUT 240 s.
- A SuperNode run's tools are only push_reply_message; its prompt is JSON {message_id, src_node_id,
  payload}. Verified live: SuperLink -> SuperNode -> reply -> verdict event in ~6 s on this laptop.
- Flower Hub accepts only .py .toml .md .yaml .json .jsonl; an app cannot declare both agentapp and
  serverapp. The AgentApp will be a separate project from the training app.

## Jev (TypeSafe System One) facts
    from typesafe_sdk import TypeSafeClient, Choice, Score, Noul
    client.system_one(state={...}, questions={"k": Choice(instructions=..., criteria={...})})
    resp.answers["k"].choice / .confidence / .probabilities   (Choice)
    .score / .confidence (Score)   .noul (Noul, 0..1)   resp.model
Limits: 1,200 req/min, 64k tokens. No arithmetic, weak on dates, literal about negations.

## Status
Done: see README "Status for the team". Accuracy claims were corrected on Sep 29: plain FedAvg does
NOT beat lone merchants on their own data for large verticals; FedAvg + local training never loses
and helps small merchants (<~1,000 rows). Never say "federated beats any merchant". The ring
detection is not validated by IEEE-CIS (69 cross-vertical sightings in 590k rows).
Not yet run against real services: Jev, Endeavor / Flower-served models, Stripe, SuperGrid.
Left: commit + push; `flwr login supergrid` + SuperGrid run (needs a SuperNode we control);
FLWR_MODEL_API_KEY / TYPESAFE_API_KEY / Stripe test keys; ship personalisation at merchant startup;
dispute draft + injection demo through a Flower task; UI pass; Hub publish; video; pitch.

## Working style
- Small verified steps. Run tests after each change. Don't rewrite working modules wholesale.
- Before any Flower Agent code, check the installed flwr version and read the actual source.
- If something external is missing (key, endpoint, allow-listing), stop and say exactly what to get.
- Keep the demo path working at all times; it's more important than new features.
