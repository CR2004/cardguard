# CardGuard - context for Claude Code

Hackathon project for the **Flower Labs Collaborative Agent Hackathon, Stanford, Tue Sep 29 2026**
(9:30am-7:30pm PT, demos start 5:15pm, submission reminder 4:30pm). Team of 2-4.

## The pitch
Several parties' agents (merchant, coordinator, issuer) make one card-payment risk decision
together, while **card data never enters any model's context**. The buyer's browser and the issuer
node are the only parties that ever see card details: the card is sealed in an issuer-served frame,
the merchant forwards the ciphertext unread, and only banded, allowlisted facts cross node
boundaries. Every disclosure and every blocked leak is logged; a human approves risky charges; the
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
| cardguard/decision/coordinator.py | rules() + ask_jev(); hard decline on cvc_check=fail; decide() re-guards input, more cautious vote wins, retry-then-rules; Verifier (receiving side) |
| cardguard/decision/explain.py | Endeavor one-sentence explanation from verdict + recognised cites only; template fallback |
| cardguard/data/ieee_cis.py | Real transactions: 5 ProductCD verticals = 5 merchants, 9 features relative to each vertical, time holdout, cache in datasets/ |
| cardguard/training/fl.py | numpy logistic regression, local_train, fedavg; synthetic 3-merchant data for offline tests; real-data report |
| cardguard/training/flower_app.py | Flower 1.39 ServerApp + ClientApp, one SuperNode per merchant; writes fl_weights.json at repo root |
| cardguard/payment_processing/issuer.py | Issuer node :4243. RSA-OAEP decrypt of sealed cards, replay/tamper/expiry checks, cvc_check fact, authorize/void on a simulated ledger. Only party that sees card data. |
| cardguard/payment_processing/card_frame.html, card_seal.js | Issuer-served card frame; WebCrypto RSA-OAEP-SHA256 seal of {pan, exp, cvc, amount, merchant, nonce, ts} |
| cardguard/payment_processing/merchant.py | Merchant node :4242. Forwards the blob unread, banded facts, relative amount bands, federated band, ledger rate limits, review queue, attack modes |
| cardguard/payment_processing/stripe_processor.py | Stripe test mode behind the issuer's three calls; PROCESSOR=stripe selects it; live keys refused |
| cardguard/payment_processing/errors.py, processor_base.py | IssuerReject (so the merchant never imports issuer.py); shared ids / one-use verifications / chained audit |
| cardguard/decision/llm.py, cardguard/httpjson.py | the one OpenAI-compatible client (endpoint+key chosen as a pair) and `ask()`; the one JSON-over-HTTP helper |
| cardguard/payment_processing/agent_llm.py | Deliberately model-driven merchant agent for the live injection demo; contained by guard + integrity check |
| cardguard/payment_processing/checkout.html | Buy page: issuer iframe (buyer confirms amount inside it), presets, attack picker, gift message, review queue, ledger, ciphertext box |
| cardguard/agentapp/agent_app.py | The Flower AgentApp: coordinator role (SuperLink: get_nodes/push/pull, Verifier, decide, explain, emits `cardguard.verdict`) and merchant role (SuperNode: fetches guarded facts from its node's /agent/facts, push_reply_message). Code drives every Grid call. |
| cardguard/agentapp/launch.py | Starts one decision as a run via the SuperLink Control API (local FAB + user prompt + `agent.decision-id` override) and reads the verdict from the event stream |
| cardguard/decision/audit.py | Hash-chained append-only log used by the merchant ledger and the issuer audit |
| cardguard/decision/network.py | network_velocity_band: same card reference at 1/2/3+ merchants in 10 min; NetworkWatch persisted in context.state (run series) |
| cardguard/training/retrain.py, join.py, privacy.py | human labels -> federated round + local fine-tune; node registry + join CLI; DP wrapper (server-side fixed clipping + RdpAccountant, equal node weights) |
| cardguard/agentapp/dispute_agent.py | chargeback evidence + drafted response, leak-scanned (cvv_words=False for displayed text) |
| pyproject.toml | Python project + `[tool.flwr.app]` (agentapp component, fab-include: only .py/.json/.md/LICENSE) |
| run_demo.py | Starts issuer and merchant together; `--federation local-agent` makes checkout decide over Flower; `--tls` for https issuer |
| tests/ | 80+ offline tests. Jev, Endeavor, the LLM are faked; the issuer runs in-process; real-data tests skip without the CSV |
| datasets/ | IEEE-CIS train_transaction.csv (git-ignored, Kaggle competition licence) and the feature cache |

## Invariants - never break these
1. Card data (number, expiry, CVC) exists only in the issuer's card frame (browser) and the issuer
   node. The merchant forwards the sealed blob unread and never logs it; the ledger never holds the
   blob, a card number, or a verification id. The buyer confirms amount and merchant inside the frame.
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
   The issuer is the one hard dependency: no issuer, no payment.
6. Synthetic test cards and synthetic or licensed research data only. Never put keys in the repo.
7. Keep all tests passing: `python -m pytest -q`. Add a test for every new safety property.

## Commands
    uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
    source .venv/bin/activate
    python -m pytest -q                          # all tests, offline
    python -m cardguard.training.fl              # federated vs local-only tables
    python -m cardguard.training.flower_app      # Flower simulation, writes fl_weights.json
    python run_demo.py                           # issuer :4243 + merchant :4242 -> http://127.0.0.1:4242
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
  `flower-endeavor-v1.0` (unconfirmed - ask the Flower team).
- STORES=store-a,store-b,store-c: one merchant node fronting several stores (fraud-ring demo).
- FL_DP_NOISE / FL_DP_CLIP: differential privacy on flower_app training (0 = off).
- REVIEWER_TOKEN: credential for human actions on the merchant node (review, chargeback, evidence,
  retrain, join). run_demo.py mints and prints it; the page asks once. CHECKOUT_RATE_PER_MINUTE (30).
- DEMO_CONTROLS=1 (run_demo sets it; tests set it in conftest): the page may choose buyer country,
  hour, attack mode and the model-driven agent; the merchant shows detailed issuer reasons; "latest"
  decision ids are allowed. Unset = production: country from geolocate(), hour from the clock, no
  attacks, generic refusal text, explicit decision ids only.
- MERCHANT_HOSTS: Host allowlist (DNS-rebinding guard). AUDIT_KEY (hex): HMAC-keyed hash chains.
  FL_ROBUST=1: FedMedian instead of FedAvg in flower_app (hostile-node resistance).
- run_demo.py gives the issuer and the merchant SEPARATE environments: the merchant never gets the
  key file paths, the merchant registry or the admin token; the issuer never gets the merchant secret.
- PROCESSOR=issuer|stripe. Stripe needs STRIPE_SECRET_KEY / STRIPE_PUBLISHABLE_KEY (test keys).
  Demo plan: Stripe run = "works with a real processor"; issuer run = "behind the scenes" (attacks).
- ISSUER_URL (merchant -> issuer, default http://127.0.0.1:4243), MERCHANT_ORIGIN (issuer CORS /
  frame-ancestors, default http://127.0.0.1:4242), MERCHANT_ID, MERCHANT_VERTICAL (W/C/R/H/S).
- LLM_BASE_URL / LLM_API_KEY / LLM_MODEL only for the live injection demo.
- Flower-served models: FLWR_MODEL_API_KEY (flower.ai Profile -> Settings -> API Keys).

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
Done: everything in the layout table; real-data federated training (0.77 AUC, matches pooled,
beats any lone merchant); issuer node with sealed cards, signed merchant requests, key files with
rotation, chained audit, CVC lockout, rate limits, optional TLS; attack modes (leak, tamper,
replay); model-driven agent demo; boundary tests; package layout; the AgentApp with both roles,
verified live on a local SuperLink + SuperNode (100+ tests).
Not yet run against real services: Jev, Endeavor, an LLM endpoint, SuperGrid.
Left: (1) credentials + `flwr login supergrid`, (2) run the same FAB on SuperGrid (needs a
SuperNode we control for the merchant role, or run the merchant role on the laptop's SuperNode
joined to SuperGrid), (3) optional DP on FedAvg, (4) publish to Flower Hub + GitHub + backup
video by 4pm, (5) pitch, (6) UI polish.

## Working style
- Small verified steps. Run tests after each change. Don't rewrite working modules wholesale.
- Before any Flower Agent code, check the installed flwr version and read the actual source.
- If something external is missing (key, endpoint, allow-listing), stop and say exactly what to get.
- Keep the demo path working at all times; it's more important than new features.
