// The whole picture is a pure function of the trace events applied so far. There is no other
// animation state: a packet, a node's state, a chip or a gate line exists only because a real
// backend event says so, and the player (useInvestigation) decides only how fast they are applied.
import type { Bands, Decision, GateLine, JevVote, PartySignal, TraceEvent } from '../api/types';

export type NodeId = 'tx' | 'store' | 'bank' | 'coordinator' | 'network' | 'gate' | 'human';
export type NodeStatus =
  | 'idle' | 'requested' | 'processing' | 'responded' | 'verified'
  | 'disagreement' | 'rejected' | 'timeout' | 'complete';
export type Channel = 'stripe' | 'signed' | 'grid' | 'inprocess' | 'state' | 'code' | 'review';
export type Party = 'stripe' | 'bank' | 'store' | 'network';
export type EdgeId = 'tx-store' | 'store-bank' | 'store-coordinator' | 'coordinator-network' | 'coordinator-gate' | 'gate-human';
export type Phase = 'idle' | 'tokenizing' | 'intake' | 'round1' | 'conflict' | 'round2' | 'gate' | 'review' | 'settling' | 'done';

export interface NodeView {
  status: NodeStatus;
  action: string;
  latencyMs?: number;
  touchedRound: number; // last investigation round this node took part in
}

export interface EvidenceItem {
  key: string;
  value: string;
  party: Party;
  round: 1 | 2;
  status: 'attested' | 'verified' | 'rejected';
  conflict?: boolean;
  points?: number; // risk points the policy gate gave this fact (from gate.decision contributions)
}

export interface Transfer {
  seq: number;
  from: NodeId;
  to: NodeId;
  edge: EdgeId;
  channel: Channel;
  label: string;
  blocked: boolean;
  stopAt?: 'source' | 'destination'; // which organization's boundary stopped it
  reason?: string;
}

export interface GateView {
  decision: Decision;
  decidedBy: string;
  score: number | null;
  lines: GateLine[];
  parties: PartySignal[];
  explanation?: { text: string; by: string; via?: string; error?: string };
  jev?: JevVote | null;
}

export interface EdgeView {
  channel: Channel;
  used: boolean; // carried traffic during this investigation
  blocked: boolean;
}

export interface Metrics {
  rawCardShared: number | null;
  offVocabulary: number;
  flowerMessages: number;
  verifiedEvidence: number;
  rejectedMessages: number;
  bytes: number;
}

export interface Investigation {
  phase: Phase;
  mode: 'flower' | 'in-process';
  round: number;
  nodes: Record<NodeId, NodeView>;
  edges: Record<EdgeId, EdgeView>;
  evidence: EvidenceItem[];
  transfer: Transfer | null; // the message moved by the most recently applied event
  blocked: Transfer[];
  conflict?: string;
  gate?: GateView;
  review?: { id: string; decided?: 'approve' | 'decline' };
  payment?: { processor: string; approve: boolean; status: string; authCode?: string | null; reason?: string };
  outcome?: { outcome: string; charged: boolean };
  runId?: string;
  supernodes: string[];
  amountCents?: number;
  store?: string;
  pmBytes?: number; // size of the Stripe payment-method id the store received
  storePrivate: Record<string, string | number>;
  metrics: Metrics;
}

// Which party a fact comes from (mirrors trace.PARTY_OF on the merchant node).
export const PARTY_OF: Record<string, Party> = {
  cvc_check: 'stripe', card_funding: 'stripe',
  issuer_behavior: 'bank', issuer_recent_declines: 'bank', travel_check: 'bank',
  amount_band: 'store', country_mismatch: 'store', velocity_band: 'store', new_customer: 'store', model_risk_band: 'store',
  network_velocity_band: 'network',
};

const CHANNEL_OF_EDGE: Record<EdgeId, Channel> = {
  'tx-store': 'stripe', 'store-bank': 'signed', 'store-coordinator': 'grid',
  'coordinator-network': 'state', 'coordinator-gate': 'code', 'gate-human': 'review',
};

const IDLE_ACTION: Record<NodeId, string> = {
  tx: 'Card entry and payment (TEST)',
  store: 'Holds its own order history',
  bank: 'Keeps cardholder history private',
  coordinator: 'Waiting for a decision request',
  network: 'Remembers cards across stores',
  gate: 'Fixed rules, no model',
  human: 'On call for held payments',
};

function edgeBetween(a: NodeId, b: NodeId): EdgeId | null {
  const key = [a, b].sort().join('|');
  const map: Record<string, EdgeId> = {
    'store|tx': 'tx-store', 'bank|store': 'store-bank', 'coordinator|store': 'store-coordinator',
    'coordinator|network': 'coordinator-network', 'coordinator|gate': 'coordinator-gate', 'gate|human': 'gate-human',
  };
  return map[key] ?? null;
}

export function initialInvestigation(mode: 'flower' | 'in-process' = 'in-process'): Investigation {
  const nodes = {} as Record<NodeId, NodeView>;
  (Object.keys(IDLE_ACTION) as NodeId[]).forEach((id) => {
    nodes[id] = { status: 'idle', action: IDLE_ACTION[id], touchedRound: 0 };
  });
  const edges = {} as Record<EdgeId, EdgeView>;
  (Object.keys(CHANNEL_OF_EDGE) as EdgeId[]).forEach((id) => {
    edges[id] = { channel: id === 'store-coordinator' && mode !== 'flower' ? 'inprocess' : CHANNEL_OF_EDGE[id],
      used: false, blocked: false };
  });
  return {
    phase: 'idle', mode, round: 0, nodes, edges, evidence: [], transfer: null, blocked: [], supernodes: [],
    storePrivate: {},
    metrics: { rawCardShared: null, offVocabulary: 0, flowerMessages: 0, verifiedEvidence: 0, rejectedMessages: 0, bytes: 0 },
  };
}

const bandsSize = (b: Bands | null | undefined) => (b ? JSON.stringify(b).length : 0);
const pretty = (v: string) => v.replace(/_/g, ' ');

export function factLabel(key: string): string {
  return ({
    cvc_check: 'CVC check', card_funding: 'Funding', travel_check: 'Travel',
    amount_band: 'Amount', country_mismatch: 'Country mismatch', velocity_band: 'Velocity 24 h',
    new_customer: 'New customer', model_risk_band: 'Federated model', network_velocity_band: 'Network velocity',
    issuer_behavior: 'Bank: behaviour', issuer_recent_declines: 'Bank: recent declines',
    specialist_stack_band: 'Specialist models',
  } as Record<string, string>)[key] ?? pretty(key);
}

export function shortLabel(key: string): string {
  return ({
    cvc_check: 'CVC', card_funding: 'Funding', travel_check: 'Travel', amount_band: 'Amount',
    country_mismatch: 'Country diff', velocity_band: 'Velocity', new_customer: 'New', model_risk_band: 'Model',
    network_velocity_band: 'Velocity', issuer_behavior: 'Behaviour', issuer_recent_declines: 'Declines',
    specialist_stack_band: 'Specialists',
  } as Record<string, string>)[key] ?? pretty(key);
}

export type Explainer = { kind: 'endeavor' | 'model'; model: string }
  | { kind: 'template'; reason: 'unavailable' | 'rejected' | 'not_asked' };

/** Who wrote the explanation, from the gate event itself: Endeavor is named only when Flower's endpoint
 *  answered with the Endeavor model, never from the model id alone. */
export function explainerOf(gate: Pick<GateView, 'explanation'>): Explainer | null {
  const ex = gate.explanation;
  if (!ex?.text || !ex.by) return null;
  if (ex.by === 'template') {
    return { kind: 'template', reason: ex.error === 'rejected_output' ? 'rejected' : ex.error === 'not_asked' ? 'not_asked' : 'unavailable' };
  }
  return { kind: /endeavor/i.test(ex.by) && ex.via === 'flower' ? 'endeavor' : 'model', model: ex.by };
}

/** What Stripe did with the money, from payment.settled; a void that Stripe did not confirm never reads as voided. */
export function settlementOf(approve: boolean, status: string, reason?: string | null):
  { word: string; note: string; tone: 'good' | 'bad' | 'attention' | 'neutral' } {
  if (approve) {
    if (status === 'succeeded') return { word: 'Charged', note: 'PaymentIntent succeeded', tone: 'good' };
    // a capture error is not a decline: a capture that went through cannot be cancelled (stripe_processor.authorize)
    if (status === 'processor_error') return { word: 'Charge unconfirmed', note: 'Stripe did not confirm the capture', tone: 'bad' };
    return { word: 'Not charged', note: reason || pretty(status), tone: 'bad' };
  }
  if (status === 'voided') return { word: 'Voided', note: 'Nothing charged', tone: 'neutral' };
  if (status === 'void_failed') return { word: 'Void pending', note: 'Hold not released yet; nothing captured', tone: 'attention' };
  return { word: 'Void unconfirmed', note: reason ? `Stripe did not confirm the void: ${reason}` : 'Stripe did not confirm the void', tone: 'bad' };
}

/** The review and decline lines, as the gate's own score line states them ("review from 3, decline from 8"). */
export function gateThresholds(gate: { lines: GateLine[] }): { review: number; decline: number } | null {
  const m = gate.lines.find((l) => l.rule === 'score')?.text.match(/review from (\d+), decline from (\d+)/);
  return m ? { review: Number(m[1]), decline: Number(m[2]) } : null;
}

/** A guard refusal in plain words ("'amount_band': card-number-like digits" -> "Amount carried card-number-like digits"). */
export function plainReason(reason: string): string {
  const leak = reason.match(/^'(\w+)': (.+)$/);
  if (leak?.[1] && leak[2]) return `${factLabel(leak[1])} carried ${leak[2]}`;
  const band = reason.match(/^value for '(\w+)' not in vocabulary$/);
  if (band?.[1]) return `${factLabel(band[1])} was not an allowed band`;
  const size = reason.match(/^bad value type\/size for '(\w+)'$/);
  if (size?.[1]) return `${factLabel(size[1])} was not a short band`;
  return reason;
}

/** Apply one event. Returns a new object; never mutates the input. */
export function applyEvent(prev: Investigation, e: TraceEvent, vocabulary: Record<string, string[]> = {}): Investigation {
  const s: Investigation = {
    ...prev,
    nodes: { ...prev.nodes },
    edges: { ...prev.edges },
    evidence: prev.evidence.map((x) => ({ ...x })),
    blocked: [...prev.blocked],
    metrics: { ...prev.metrics },
    transfer: null,
  };
  const node = (id: NodeId, status: NodeStatus, action?: string, latencyMs?: number) => {
    const cur = s.nodes[id];
    s.nodes[id] = {
      ...cur, status, action: action ?? cur.action,
      latencyMs: latencyMs ?? cur.latencyMs,
      touchedRound: Math.max(cur.touchedRound, s.round),
    };
  };
  const move = (from: NodeId, to: NodeId, label: string, blocked = false, reason?: string, channel?: Channel) => {
    const edge = edgeBetween(from, to);
    if (!edge) return;
    const ch = channel ?? s.edges[edge].channel;
    s.edges[edge] = { ...s.edges[edge], channel: ch, used: true, blocked: s.edges[edge].blocked || blocked };
    s.transfer = { seq: e.seq, from, to, edge, channel: ch, label, blocked, reason,
      stopAt: blocked ? (e.kind === 'guard.blocked' ? 'source' : 'destination') : undefined };
    if (blocked) {
      s.blocked.push(s.transfer);
      s.metrics.rejectedMessages += 1;
    }
  };
  const addEvidence = (bands: Bands | null | undefined, status: EvidenceItem['status'], round: 1 | 2, partyOverride?: Party) => {
    if (!bands) return;
    for (const [key, value] of Object.entries(bands)) {
      if (key === 'token') continue;
      const party = partyOverride ?? PARTY_OF[key] ?? 'store';
      const existing = s.evidence.find((x) => x.key === key);
      if (existing) {
        existing.value = value;
        if (status === 'verified' || existing.status !== 'verified') existing.status = status;
        existing.round = existing.round === 2 ? 2 : round;
      } else {
        s.evidence.push({ key, value, party, round, status });
      }
      const allowed = vocabulary[key];
      if (status === 'verified' && allowed && !allowed.includes(value)) s.metrics.offVocabulary += 1;
    }
    s.metrics.verifiedEvidence = s.evidence.filter((x) => x.status === 'verified').length;
  };
  const viaFlower = e.via === 'flower' || (e.via === undefined && s.mode === 'flower');
  const round = (e.round === 2 ? 2 : 1) as 1 | 2;

  switch (e.kind) {
    case 'payment.started': {
      s.phase = 'intake';
      s.amountCents = e.amount_cents;
      s.store = e.store;
      s.pmBytes = e.bytes;
      s.mode = e.federation ? 'flower' : 'in-process';
      s.edges['store-coordinator'] = { ...s.edges['store-coordinator'], channel: s.mode === 'flower' ? 'grid' : 'inprocess' };
      s.metrics.bytes += e.bytes ?? 0;
      node('tx', 'complete', 'Card tokenized in Stripe Elements');
      node('store', 'requested', 'Received a payment-method id, not a card');
      move('tx', 'store', `pm_ id · ${e.bytes ?? 0} B`);
      break;
    }
    case 'processor.verify.request': {
      node('store', 'processing', 'Asking Stripe for the card checks');
      node('tx', 'processing', 'Looking up the payment method');
      move('store', 'tx', 'payment-method id');
      break;
    }
    case 'processor.verify.reply': {
      if (e.status === 'rejected') {
        node('tx', 'rejected', `Refused: ${e.reason ?? 'not a usable payment method'}`, e.latency_ms);
        node('store', 'rejected', 'Stripe refused the payment method');
        move('store', 'tx', 'refused by Stripe', true, e.reason);
        s.phase = 'done';
      } else {
        const ev = e.evidence ?? {};
        node('tx', 'responded', 'Card checks returned', e.latency_ms);
        node('store', 'processing', 'Received Stripe’s card checks');
        addEvidence(ev, 'attested', 1, 'stripe');
        move('tx', 'store', Object.entries(ev).map(([k, v]) => `${shortLabel(k)}: ${pretty(v)}`).join(' · ') || 'card checks');
      }
      break;
    }
    case 'bank.attest.request': {
      node('store', 'processing', 'Asking the bank about the cardholder');
      node('bank', 'processing', 'Checking private cardholder history');
      move('store', 'bank', 'card_ref + issuing region');
      break;
    }
    case 'bank.attest.reply': {
      const ev = e.evidence ?? {};
      if (e.status === 'ok') node('bank', 'responded', `Attested: behaviour ${ev.issuer_behavior ?? '?'}`, e.latency_ms);
      else node('bank', 'timeout', 'No answer from the bank', e.latency_ms);
      node('store', 'processing', 'Received the bank’s attestation');
      addEvidence(ev, 'attested', 1, 'bank');
      move('bank', 'store', Object.entries(ev).map(([k, v]) => `${shortLabel(k)}: ${pretty(v)}`).join(' · ') || 'attestation');
      break;
    }
    case 'store.facts': {
      s.storePrivate = { ...(e.private ?? {}) };
      node('store', 'processing', 'Banding its private history locally');
      addEvidence(e.evidence, 'attested', 1, 'store');
      break;
    }
    case 'guard.blocked': {
      node('store', 'rejected', 'Wire guard stopped an outgoing message');
      move('store', 'coordinator', 'prohibited value', true, e.reason);
      break;
    }
    case 'flower.run.request': {
      s.mode = 'flower';
      s.phase = 'round1';
      s.round = 1;
      node('coordinator', 'requested', 'Flower run requested');
      break;
    }
    case 'flower.run.started': {
      s.runId = e.run_id;
      node('coordinator', 'processing', 'Flower run started');
      break;
    }
    case 'coord.nodes': {
      s.supernodes = e.nodes ?? [];
      node('coordinator', 'processing', `Found ${s.supernodes.length} SuperNode${s.supernodes.length === 1 ? '' : 's'}`);
      break;
    }
    case 'coord.question': {
      s.round = round;
      s.phase = round === 2 ? 'round2' : 'round1';
      const count = Math.max(1, e.nodes?.length ?? 1);
      if (viaFlower) {
        s.metrics.flowerMessages += count;
        s.metrics.bytes += (e.bytes ?? 0) * count;
      }
      node('coordinator', 'processing', round === 2 ? 'Round 2: asking the bank only' : 'Round 1: asking the store');
      node('store', 'requested', round === 2 ? 'Relaying a question to its bank' : 'Question received');
      move('coordinator', 'store', round === 2 ? 'travel-check?' : 'fraud-risk?', false, undefined, viaFlower ? 'grid' : 'inprocess');
      break;
    }
    case 'node.read': {
      if (e.status === 'blocked') node('store', 'rejected', `Ledger refused: ${e.reason ?? 'blocked'}`);
      else if (e.status === 'relayed') node('store', 'processing', 'Relaying to the bank over the signed channel');
      else node('store', 'processing', 'SuperNode read the guarded facts');
      break;
    }
    case 'coord.reply':
    case 'store.disclosed': {
      const r = e.kind === 'store.disclosed' ? 1 : round;
      if (e.kind === 'store.disclosed') {
        s.round = 1;
        s.phase = 'round1';
      }
      const size = e.bytes ?? bandsSize(e.evidence);
      if (viaFlower && e.kind === 'coord.reply') s.metrics.flowerMessages += 1;
      s.metrics.bytes += size;
      if (e.status === 'verified') {
        addEvidence(e.evidence, 'verified', r);
        const n = Object.keys(e.evidence ?? {}).filter((k) => k !== 'token').length;
        node('store', 'responded', r === 2 ? 'Relayed the bank’s answer' : 'Disclosed banded facts only', e.latency_ms);
        node('coordinator', 'verified', `Verified ${n} fact${n === 1 ? '' : 's'} on arrival`);
        move('store', 'coordinator', r === 2 ? 'travel_check' : `${n} banded facts`, false, undefined,
          e.kind === 'coord.reply' && viaFlower ? 'grid' : 'inprocess');
      } else if (e.status === 'error') {
        node('store', 'timeout', 'No reply from the node', e.latency_ms);
      } else {
        node('coordinator', 'rejected', 'Rejected a reply at the boundary');
        move('store', 'coordinator', 'reply', true, e.reason, viaFlower ? 'grid' : 'inprocess');
      }
      break;
    }
    case 'coord.network': {
      const band = e.evidence?.network_velocity_band ?? 'low';
      node('network', 'responded', e.stores ? `Card seen at ${e.stores} store${e.stores === 1 ? '' : 's'} in 10 min` : 'Checked other stores’ sightings');
      addEvidence(e.evidence, 'verified', 1, 'network');
      move('network', 'coordinator', `velocity: ${band}`);
      break;
    }
    case 'coord.conflict': {
      s.phase = 'conflict';
      s.conflict = e.reason ?? 'the store and the bank disagree';
      s.round = 2;
      node('coordinator', 'disagreement', 'Evidence conflict: follow-up required');
      for (const x of s.evidence) {
        if ((x.key === 'country_mismatch' && x.value === 'yes') || (x.key === 'issuer_behavior' && x.value === 'low')) x.conflict = true;
      }
      break;
    }
    case 'bank.travel.request': {
      s.round = 2;
      node('bank', 'processing', 'Analyzing private travel history');
      node('store', 'processing', 'Waiting for the bank');
      move('store', 'bank', 'travel? (region only)');
      break;
    }
    case 'bank.travel.reply': {
      if (e.status === 'unavailable') {
        node('bank', 'timeout', 'No travel answer', e.latency_ms);
        break;
      }
      const v = e.evidence?.travel_check ?? 'unknown';
      node('bank', 'responded', `Travel ${v}`, e.latency_ms);
      addEvidence(e.evidence, 'attested', 2, 'bank');
      move('bank', 'store', `travel: ${v}`);
      break;
    }
    case 'gate.decision': {
      s.phase = 'gate';
      s.gate = {
        decision: e.decision ?? 'step_up', decidedBy: e.decided_by ?? 'rules', score: e.score ?? null,
        lines: e.lines ?? [], parties: e.parties ?? [], explanation: e.explanation, jev: e.jev ?? null,
      };
      for (const c of e.contributions ?? []) {
        const x = s.evidence.find((it) => it.key === c.fact.split('=')[0]);
        if (x && c.points > 0) x.points = c.points;
      }
      node('coordinator', 'complete', 'Handed verified evidence to the policy gate');
      node('gate', 'complete', { approve: 'Approve', step_up: 'Hold for a person', decline: 'Decline' }[s.gate.decision]);
      move('coordinator', 'gate', 'verified evidence');
      break;
    }
    case 'verdict.received': {
      if (e.status === 'accepted') node('store', 'verified', 'Accepted the verdict from its own node');
      else node('store', 'rejected', 'Ignored a verdict it could not bind');
      move('coordinator', 'store', 'verdict', e.status !== 'accepted', e.reason, 'grid');
      break;
    }
    case 'fallback': {
      node('coordinator', 'timeout', 'No Flower verdict: decided on the store node');
      s.mode = 'in-process';
      // the Flower verdict was not bound to this decision: it is superseded by the store node's own gate
      s.gate = undefined;
      for (const x of s.evidence) delete x.points;
      break;
    }
    case 'flower.run.finished':
      break;
    case 'review.opened': {
      s.phase = 'review';
      s.review = { id: e.review_id ?? '' };
      node('human', 'requested', 'A person must decide');
      move('gate', 'human', 'evidence for review');
      break;
    }
    case 'review.decided': {
      const action = e.action === 'approve' ? 'approve' : 'decline';
      s.review = { id: e.review_id ?? s.review?.id ?? '', decided: action };
      node('human', 'complete', action === 'approve' ? 'Approved by a person' : 'Declined by a person');
      move('human', 'gate', action === 'approve' ? 'approved by a person' : 'declined by a person');
      s.phase = 'settling';
      break;
    }
    case 'payment.settled': {
      s.payment = { processor: e.processor ?? 'stripe', approve: Boolean(e.approve), status: e.status ?? '?',
        authCode: e.auth_code, reason: e.reason };
      if (s.phase !== 'review') s.phase = 'settling';
      const st = settlementOf(Boolean(e.approve), e.status ?? '', e.reason);
      node('tx', e.status === 'succeeded' || e.status === 'voided' ? 'complete' : 'rejected', `${st.word}: ${st.note}`);
      move('store', 'tx', e.approve ? 'capture PaymentIntent' : 'void');
      break;
    }
    case 'outcome': {
      s.outcome = { outcome: e.outcome ?? '', charged: Boolean(e.charged) };
      s.phase = e.outcome === 'needs_review' ? 'review' : 'done';
      if (e.outcome === 'blocked' || e.outcome === 'processor_rejected') node('gate', 'rejected', 'Stopped before any decision');
      break;
    }
    default:
      break;
  }
  return s;
}

export function derive(events: TraceEvent[], mode: 'flower' | 'in-process', vocabulary: Record<string, string[]> = {},
  rawCardShared: number | null = null): Investigation {
  let s = initialInvestigation(mode);
  for (const e of events) s = applyEvent(s, e, vocabulary);
  s.metrics.rawCardShared = rawCardShared;
  return s;
}

/** Whether a node sits out the current round (drawn dimmed so round 2 is visibly targeted). */
export function sitsOut(s: Investigation, id: NodeId): boolean {
  if (s.round < 2 || s.phase === 'gate' || s.phase === 'review' || s.phase === 'settling' || s.phase === 'done') return false;
  return id === 'network' || id === 'tx';
}

/** How long the player lingers on an event before applying the next one (ms, full motion). */
export function dwell(e: TraceEvent | undefined): number {
  if (!e) return 0;
  switch (e.kind) {
    case 'gate.decision': return 700 + (e.lines?.length ?? 0) * 360;
    case 'coord.conflict': return 1300;
    case 'payment.started': case 'processor.verify.request': case 'processor.verify.reply': case 'bank.attest.request':
    case 'bank.attest.reply': case 'coord.question':
    case 'coord.reply': case 'store.disclosed': case 'coord.network': case 'bank.travel.request':
    case 'bank.travel.reply': case 'verdict.received': case 'review.opened': case 'guard.blocked':
      return 900;
    case 'store.facts': return 700;
    case 'payment.settled': return 700;
    default: return 260;
  }
}
