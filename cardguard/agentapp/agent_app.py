"""CardGuard as a Flower AgentApp: one FAB, two roles, one decision per task.

Coordinator role (runs on the SuperLink; prompt is the user's/CLI's text):
  1. get_nodes            -> the merchant SuperNodes in the federation
  2. push_messages        -> one purpose-tagged question per merchant: {"purpose", "decision_id"}
  3. pull_messages        -> the merchant's reply: guarded banded facts as JSON, or {"error"}
  4. Verifier.accept      -> re-run the wire guard on what arrived; one fact set per decision
  4b. round 2, only when the store and the bank disagree (coordinator.needs_travel_check): one
      targeted question to the node that answered, whose bank answers travel_check; verified again
  5. coordinator.decide   -> rules + Jev vote, in code;  explain.explain -> one sentence
  6. emit + print CARDGUARD_VERDICT {...} so the merchant node (or Flower Chat) can read it

Merchant role (runs on a SuperNode; prompt is JSON {message_id, src_node_id, payload}):
  - reads the question, asks its own merchant node's local API for the facts of that decision
    (that call is where Ledger.disclose() runs), and answers with push_reply_message.
  - never sees, holds or forwards anything but the guarded facts.

No model chooses a tool or a verdict: code drives every Grid call. Models vote or explain only.
Endeavor is reached through FLWR_RUNTIME_BASE_URL / FLWR_RUNTIME_API_KEY, injected per task.
Timing: one pull of at most PULL_TIMEOUT seconds, inside the 5-minute task window.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Callable

from flwr.agentapp import AgentApp, AgentSession
from flwr.app import Context

from cardguard.decision import coordinator as coord
from cardguard.decision import network as net
from cardguard.decision.explain import explain
from cardguard.decision.guard import WireViolation, find_leaks, json_values

app = AgentApp()

PURPOSE = "fraud-risk"
PURPOSES = {PURPOSE, coord.TRAVEL_PURPOSE}
PULL_TIMEOUT = 90.0           # seconds; the SuperGrid task budget is 300
MAX_MESSAGE_LEN = 2000        # a fact set is ~250 chars; anything larger is not a fact set
DEFAULT_MERCHANT_API = "http://127.0.0.1:4242"


# ---------------------------------------------------------------- Grid helpers (code-driven)

def grid_call(agent: AgentSession, name: str, **arguments) -> dict:
    """Call one Grid tool exactly as a model would, and parse its JSON output."""
    out = agent.grid.call({"type": "function_call", "name": name, "call_id": "call_" + uuid.uuid4().hex[:12],
                           "arguments": json.dumps(arguments)})
    return json.loads(out["output"])


def role_of(prompt: str) -> str:
    """A SuperNode run's prompt is JSON with src_node_id; anything else is the coordinator."""
    try:
        obj = json.loads(prompt)
    except (TypeError, ValueError):
        return "coordinator"
    return "merchant" if isinstance(obj, dict) and "src_node_id" in obj and "payload" in obj else "coordinator"


_values = json_values  # one walker for the whole package


def safe_wire_text(text: str) -> str:
    """Refuse to put anything card-like or oversized on the Grid, in either direction."""
    if len(text) > MAX_MESSAGE_LEN:
        raise WireViolation("message too large")
    try:
        parts = list(_values(json.loads(text)))
    except (TypeError, ValueError):
        parts = [text]
    leaks = sorted({reason for part in parts for reason in find_leaks(part)})
    if leaks:
        raise WireViolation("outgoing message: " + ", ".join(leaks))
    return text


# ---------------------------------------------------------------- merchant role

def merchant_api_for(context) -> str:
    """This SuperNode's own merchant process. A run's config is shared by every node, so per-node settings win:
    the SuperNode's `--node-config merchant-api=...`, then its CARDGUARD_MERCHANT_API environment, then the
    run config, then the default (one node, one merchant on :4242)."""
    node_cfg = getattr(context, "node_config", None)
    candidates = [node_cfg.get("merchant-api") if hasattr(node_cfg, "get") else None,
                  os.environ.get("CARDGUARD_MERCHANT_API"),
                  context.run_config.get("agent.merchant-api") if hasattr(context, "run_config") else None]
    return next((str(c).rstrip("/") for c in candidates if c), DEFAULT_MERCHANT_API)


def fetch_facts(merchant_api: str, decision_id: str, purpose: str, http: Callable | None = None,
                node_id: str = "") -> dict:
    """Ask this node's merchant process for the guarded facts of one decision (Ledger.disclose runs there).
    The node id lets the merchant accept only a verdict that came back through this very node."""
    body = {"decision_id": decision_id, "purpose": purpose, "node_id": node_id}
    if http is not None:
        return http(body)
    from cardguard.httpjson import post_json
    return post_json(merchant_api + "/agent/facts", body, timeout=10)


def merchant_role(agent: AgentSession, context: Context, http: Callable | None = None) -> dict:
    incoming = json.loads(agent.prompt)
    try:
        question = json.loads(incoming["payload"])
        purpose, decision_id = str(question["purpose"]), str(question.get("decision_id", "latest"))
        if purpose not in PURPOSES:
            raise ValueError("unknown purpose")
        merchant_api = merchant_api_for(context)
        facts = fetch_facts(merchant_api, decision_id, purpose, http, node_id=str(getattr(context, "node_id", "")))
        reply = {"purpose": purpose, "decision_id": decision_id, "facts": facts.get("facts"),
                 "merchant_id": str(facts.get("merchant_id", "")),
                 **({"error": facts["error"]} if facts.get("error") else {})}
    except Exception as e:  # a wrong question or a blocked disclosure: say so, never say more
        reply = {"error": type(e).__name__ if not isinstance(e, (ValueError, KeyError)) else str(e)}
    text = safe_wire_text(json.dumps(reply, separators=(",", ":")))
    result = grid_call(agent, "push_reply_message", payload=text)
    agent.events.emit({"type": "message", "role": "assistant", "content": "merchant replied: " +
                       ("facts" if reply.get("facts") else "error")})
    return {"reply": reply, "result": result}


# ---------------------------------------------------------------- coordinator role

def decide_from_replies(replies: list[dict], decision_id: str, verifier: coord.Verifier,
                        watch: net.NetworkWatch | None = None, now: float | None = None,
                        trust_declared_stores: bool = False,
                        follow_up: Callable[[str, dict], str | None] | None = None) -> dict:
    """Verify every reply on the receiving side; decide only if exactly one verified fact set arrived
    (two nodes answering for one decision is a forgery attempt, and a human decides).
    With a network watch, add the coordinator's own fact: the same card at several merchants.
    follow_up(node, facts) asks round 2 of the node that answered, when the facts call for it; with
    no verified answer a person decides (never an automatic approve)."""
    facts, merchant_id, fact_sets = None, "", 0
    for r in replies:
        if r.get("error") or r.get("payload") is None:
            continue
        try:
            payload = json.loads(r["payload"])
            if not isinstance(payload, dict) or payload.get("decision_id", decision_id) != decision_id \
                    or not isinstance(payload.get("facts"), dict):
                raise WireViolation("reply is not a fact set for this decision")
            # Identity for the network view: the SuperNode id, which the SuperLink assigned and the
            # reply carries authentically, plus the store the node declares. A compromised node can
            # only ever speak for stores under its own node id, never for another merchant.
            declared = str(payload.get("merchant_id") or "")
            if declared and not net.MERCHANT_ID_RE.match(declared):
                raise WireViolation("bad merchant id")
            node = str(r.get("src_node_id") or "")
            fact_sets += 1
            if fact_sets > 1:
                raise WireViolation("conflicting replies for one decision")
            merchant_id = f"{node}:{declared}" if node and declared else (node or declared)
            accepted = verifier.accept(decision_id, PURPOSE, payload["facts"])
            if "travel_check" in accepted:  # only the bank's round-2 answer may carry it
                raise WireViolation("travel_check is round-2 evidence only")
            facts = accepted
        except (WireViolation, ValueError, TypeError, KeyError) as e:
            verifier.rejected.append({"decision_id": decision_id, "purpose": PURPOSE, "reason": str(e),
                                      "node": str(r.get("src_node_id") or "")})
            if fact_sets > 1:
                facts = None  # neither reply is trusted once two nodes claim the same decision
    if facts is None:  # nothing verifiable arrived: a human decides, never an automatic approve
        return {"decision": "step_up", "score": None, "cites": ["no_verified_facts"], "decided_by": "no_facts",
                "rejected": verifier.rejected}
    network = None
    if watch is not None and facts.get("token") and merchant_id:  # no id: no network view, never a guess
        # Production identity is the authenticated node; a node's self-declared stores only count
        # separately when the run explicitly trusts them (the one-node-many-stores demo).
        identity = merchant_id if trust_declared_stores else merchant_id.split(":", 1)[0]
        band, merchants = watch.observe(facts["token"], identity, now)
        facts = {**facts, "network_velocity_band": band}
        network = {"band": band, "merchants": merchants}
    unanswered = False
    if follow_up is not None and coord.needs_travel_check(facts):
        travel = follow_up(merchant_id.split(":", 1)[0], facts)
        unanswered = travel is None
        if travel is not None:
            facts = {**facts, "travel_check": travel}
    verdict = coord.decide(facts)
    if unanswered:
        verdict = coord.hold_unanswered(verdict)
    verdict["facts"] = facts
    verdict["merchant_id"] = merchant_id
    if network:
        verdict["network"] = network
        if network["band"] == "high":  # tell every store that saw this card
            verdict["network_alert"] = {"token": facts["token"], "merchants": network["merchants"]}
    return verdict


def step(agent: AgentSession, **fields) -> None:
    """Progress for the merchant's live trace: ids, counts, bands and fixed reasons only. It is a run
    event, not a Grid message, and display only: a failure here never touches the decision."""
    try:
        agent.events.emit({"type": "cardguard.step", "t": time.time(), **fields})
    except Exception:  # noqa: BLE001 - display only
        pass


def reply_steps(pulled: list[dict], accepted: str, facts: dict | None, rejected: list[dict],
                latency_ms: float) -> list[dict]:
    """One progress record per round-1 reply: verified (the fact set the decision used, as the node sent
    it), rejected (with the receiving side's reason) or an error reply."""
    reasons = {r.get("node", ""): r.get("reason", "") for r in rejected}
    sent = {k: v for k, v in (facts or {}).items() if k not in {"network_velocity_band", "travel_check"}}
    out = []
    for m in pulled:
        node = str(m.get("src_node_id") or "")
        size = len(m.get("payload") or "")
        if node and node == accepted:
            out.append({"node": node, "status": "verified", "facts": sent, "bytes": size})
        elif m.get("error") or m.get("payload") is None:
            out.append({"node": node, "status": "error", "reason": "no reply from the node", "bytes": size})
        else:
            out.append({"node": node, "status": "rejected", "reason": reasons.get(node, "not used"), "bytes": size})
        out[-1]["latency_ms"] = latency_ms
    return out


def ask_round_two(agent: AgentSession, nodes: list[dict], node: str, decision_id: str, verifier: coord.Verifier,
                  pull_timeout: float) -> str | None:
    """Round 2: the travel question to the one node that answered round 1 (its bank answers behind it).
    Same message shapes as round 1; the reply is verified again and must carry travel_check only."""
    dst = next((n["id"] for n in nodes if str(n["id"]) == node), None)
    if dst is None:
        return None
    step(agent, step="conflict", round=2, reason="store: the buyer is outside the card's country; "
                                                 "bank: an ordinary cardholder, card checks pass")
    question = safe_wire_text(json.dumps({"purpose": coord.TRAVEL_PURPOSE, "decision_id": decision_id},
                                         separators=(",", ":")))
    pushed = grid_call(agent, "push_messages", messages=[{"dst_node_id": dst, "payload": question,
                                                          "reply_to_message_id": None}])
    ids = [r["message_id"] for r in pushed["results"] if r.get("message_id")]
    step(agent, step="question", round=2, to=[node], purpose=coord.TRAVEL_PURPOSE, bytes=len(question))
    asked = time.time()
    pulled = grid_call(agent, "pull_messages", message_ids=ids, timeout=pull_timeout) if ids else {"messages": []}
    latency_ms = round((time.time() - asked) * 1000)
    for m in pulled["messages"]:
        size = len(m.get("payload") or "")
        try:
            if str(m.get("src_node_id") or "") != node or m.get("payload") is None:
                raise WireViolation("reply is not from the node that was asked")
            payload = json.loads(safe_wire_text(m["payload"]))
            if not isinstance(payload, dict) or payload.get("decision_id") != decision_id \
                    or not isinstance(payload.get("facts"), dict):
                raise WireViolation("reply is not a fact set for this decision")
            facts = verifier.accept(decision_id, coord.TRAVEL_PURPOSE, payload["facts"])
            if set(facts) != {"travel_check"}:
                raise WireViolation("round 2 carries travel_check only")
        except (WireViolation, ValueError, TypeError) as e:
            step(agent, step="reply", round=2, node=str(m.get("src_node_id") or ""), status="rejected",
                 reason=str(e), bytes=size, latency_ms=latency_ms)
            continue
        step(agent, step="reply", round=2, node=node, status="verified", facts=facts, bytes=size, latency_ms=latency_ms)
        return facts["travel_check"]
    return None


def coordinator_role(agent: AgentSession, context: Context) -> dict:
    decision_id = str(context.run_config.get("agent.decision-id", "") or "latest")
    pull_timeout = min(float(context.run_config.get("agent.pull-timeout", PULL_TIMEOUT)), 300.0)
    trust_stores = str(context.run_config.get("agent.trust-declared-stores", "false")).lower() == "true"
    verifier = coord.Verifier()
    watch = net.load_from_context(context)  # the network agent's memory, persisted in the run series

    nodes = grid_call(agent, "get_nodes", sample_size=None)["nodes"]
    step(agent, step="nodes", nodes=[str(n["id"]) for n in nodes])
    if not nodes:
        verdict = {"decision": "step_up", "cites": ["no_merchant_nodes"], "decided_by": "no_facts", "score": None}
    else:
        question = safe_wire_text(json.dumps({"purpose": PURPOSE, "decision_id": decision_id}, separators=(",", ":")))
        pushed = grid_call(agent, "push_messages", messages=[
            {"dst_node_id": n["id"], "payload": question, "reply_to_message_id": None} for n in nodes])
        ids = [r["message_id"] for r in pushed["results"] if r.get("message_id")]
        step(agent, step="question", round=1, to=[str(n["id"]) for n in nodes], bytes=len(question))
        asked = time.time()
        pulled = grid_call(agent, "pull_messages", message_ids=ids, timeout=pull_timeout) if ids else {"messages": []}
        latency_ms = round((time.time() - asked) * 1000)
        for m in pulled["messages"]:
            if m.get("payload") is not None:
                try:
                    safe_wire_text(m["payload"])  # a card-like reply never gets further than this line
                except WireViolation as e:
                    step(agent, step="reply", round=1, node=str(m.get("src_node_id") or ""), status="rejected",
                         reason=str(e), bytes=len(m["payload"]), latency_ms=latency_ms)
                    raise
        shown = False

        def show_round_one(node: str, facts: dict | None) -> None:
            """Round-1 progress, once, before any round-2 step (display only)."""
            nonlocal shown
            if shown:
                return
            shown = True
            for r in reply_steps(pulled["messages"], node, facts, verifier.rejected, latency_ms):
                step(agent, step="reply", round=1, **r)
            if facts and "network_velocity_band" in facts:
                step(agent, step="network", band=facts["network_velocity_band"])

        def follow_up(node: str, facts: dict) -> str | None:
            show_round_one(node, facts)
            return ask_round_two(agent, nodes, node, decision_id, verifier, pull_timeout)

        verdict = decide_from_replies(pulled["messages"], decision_id, verifier, watch, trust_declared_stores=trust_stores,
                                      follow_up=follow_up)
        net.save_to_context(context, watch)
        accepted = str(verdict.get("merchant_id", "")).split(":", 1)[0] if verdict.get("decided_by") != "no_facts" else ""
        show_round_one(accepted, verdict.get("facts"))
    verdict["explanation"] = explain(verdict) if verdict.get("decided_by") != "no_facts" else \
        {"text": "Sent to a human reviewer because no verified facts arrived from the merchant.", "by": "template"}
    verdict["decision_id"] = decision_id
    line = "CARDGUARD_VERDICT " + json.dumps(verdict, separators=(",", ":"), default=str)
    print(line, flush=True)
    summary = f"{verdict['decision']} ({verdict.get('decided_by')}): {verdict['explanation']['text']}"
    agent.events.emit({"type": "cardguard.verdict", "verdict": json.loads(json.dumps(verdict, default=str))})
    agent.events.emit({"type": "response.output_text.delta", "delta": summary})   # renders in Flower Chat
    agent.events.emit({"type": "response.completed", "response": {"output": [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": summary}]}]}})
    return verdict


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    if role_of(agent.prompt) == "merchant":
        merchant_role(agent, context)
    else:
        coordinator_role(agent, context)
