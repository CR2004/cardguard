"""CardGuard as a Flower AgentApp: one FAB, two roles, one decision per task.

Coordinator role (runs on the SuperLink; prompt is the user's/CLI's text):
  1. get_nodes            -> the merchant SuperNodes in the federation
  2. push_messages        -> one purpose-tagged question per merchant: {"purpose", "decision_id"}
  3. pull_messages        -> the merchant's reply: guarded banded facts as JSON, or {"error"}
  4. Verifier.accept      -> re-run the wire guard on what arrived; one fact set per decision
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
        if purpose != PURPOSE:
            raise ValueError("unknown purpose")
        merchant_api = str(context.run_config.get("agent.merchant-api", DEFAULT_MERCHANT_API))
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
                        trust_declared_stores: bool = False) -> dict:
    """Verify every reply on the receiving side; decide only if exactly one verified fact set arrived
    (two nodes answering for one decision is a forgery attempt, and a human decides).
    With a network watch, add the coordinator's own fact: the same card at several merchants."""
    facts, merchant_id, fact_sets = None, "", 0
    for r in replies:
        if r.get("error") or r.get("payload") is None:
            continue
        try:
            payload = json.loads(r["payload"])
            if payload.get("decision_id", decision_id) != decision_id or not isinstance(payload.get("facts"), dict):
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
            facts = verifier.accept(decision_id, PURPOSE, payload["facts"])
        except (WireViolation, ValueError, TypeError, KeyError) as e:
            verifier.rejected.append({"decision_id": decision_id, "purpose": PURPOSE, "reason": str(e)})
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
    verdict = coord.decide(facts)
    verdict["facts"] = facts
    verdict["merchant_id"] = merchant_id
    if network:
        verdict["network"] = network
        if network["band"] == "high":  # tell every store that saw this card
            verdict["network_alert"] = {"token": facts["token"], "merchants": network["merchants"]}
    return verdict


def coordinator_role(agent: AgentSession, context: Context) -> dict:
    decision_id = str(context.run_config.get("agent.decision-id", "") or "latest")
    pull_timeout = min(float(context.run_config.get("agent.pull-timeout", PULL_TIMEOUT)), 300.0)
    trust_stores = str(context.run_config.get("agent.trust-declared-stores", "false")).lower() == "true"
    verifier = coord.Verifier()
    watch = net.load_from_context(context)  # the network agent's memory, persisted in the run series

    nodes = grid_call(agent, "get_nodes", sample_size=None)["nodes"]
    if not nodes:
        verdict = {"decision": "step_up", "cites": ["no_merchant_nodes"], "decided_by": "no_facts", "score": None}
    else:
        question = safe_wire_text(json.dumps({"purpose": PURPOSE, "decision_id": decision_id}, separators=(",", ":")))
        pushed = grid_call(agent, "push_messages", messages=[
            {"dst_node_id": n["id"], "payload": question, "reply_to_message_id": None} for n in nodes])
        ids = [r["message_id"] for r in pushed["results"] if r.get("message_id")]
        pulled = grid_call(agent, "pull_messages", message_ids=ids, timeout=pull_timeout) if ids else {"messages": []}
        for m in pulled["messages"]:
            if m.get("payload") is not None:
                safe_wire_text(m["payload"])  # a card-like reply never gets further than this line
        verdict = decide_from_replies(pulled["messages"], decision_id, verifier, watch, trust_declared_stores=trust_stores)
        net.save_to_context(context, watch)
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
