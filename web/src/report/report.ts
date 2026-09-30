// The exported decision report: one privacy-safe record built from the same trace the page animates
// (GET /trace/<id>) and the same derived view the page shows, so the export can never disagree with the
// screen. Nothing is invented: a section exists only when an event says it happened. The PDF is drawn
// from this object, so it can never hold anything the JSON does not.
import type { Config, GateLine, TraceEvent } from '../api/types';
import { verdictOf } from '../components/Inspector';
import { stoppedText, VERDICT_WORD } from '../components/PolicyGate';
import { formatMoney, pretty } from '../format';
import {
  derive, explainerOf, factLabel, gateThresholds, paymentStateOf, plainReason, type Investigation,
} from '../investigation/derive';

export const REPORT_VERSION = '1.0';

/** Everything the page already holds about one checkout. */
export interface ReportSource {
  traceId: string; // minted by this page (letters a-p only): the decision's reference on the page
  events: TraceEvent[]; // exactly what GET /trace returned, in order
  config: Pick<Config, 'merchant_id' | 'vertical' | 'federation' | 'bank_attestation' | 'publishable_key'
    | 'demo_controls' | 'wire_vocabulary'>;
  input: { buyer_country?: string } | null; // what this page sent with the checkout
  startedAt: number | null; // this browser's clock when the checkout was sent
  rawCardShared: number | null; // the ledger's count of card numbers the coordinator saw
  forbidden?: string[]; // values this page holds that must never appear in an export (payment-method id, credential)
}

export interface ReportFact {
  fact: string;
  label: string;
  value: string;
  party: string;
  round: 1 | 2;
  status: string;
  conflict: boolean;
  risk_points: number | null;
}

export interface ReportRound {
  round: 1 | 2;
  channel: 'flower_grid' | 'in_process';
  summary: string;
  asked: string[];
  trigger: string | null; // round 2 only: the conflict that made the coordinator ask
  question: { purpose: string; bytes: number | null; supernodes: number } | null;
  reply: { status: string; bytes: number | null; latency_ms: number | null; reason: string | null } | null;
  effect: string | null; // round 2 only: what the policy gate made of the answer
  facts: ReportFact[];
}

export interface ReportParticipant {
  component: string;
  role: string;
  status: string;
}

export interface RejectedMessage {
  seq: number;
  t_ms: number | null;
  step: string;
  from: string;
  to: string;
  stopped_at: 'sender' | 'receiver';
  reason: string | null;
  reason_plain: string;
}

export interface DecisionReport {
  report_version: string;
  generator: string;
  decision_id: string;
  created_at: string;
  checkout_sent_at: string | null;
  environment: {
    merchant_id: string; vertical: string; stripe_mode: 'test' | 'unknown'; demo_controls: boolean;
    bank_attestation: boolean; federation: string | null;
  };
  transaction: {
    amount_cents: number | null; amount: string | null; currency: 'USD'; store: string | null;
    buyer_country: string | null; buyer_region: string | null; card_issuing_region: string | null; attack_mode: string | null;
  };
  verdict: {
    final: string; label: string; decided_by: string; human_final: boolean; gate_decision: string | null;
    gate_label: string | null; risk_score: number | null; thresholds: { review: number; decline: number } | null;
    hard_stop: boolean; summary: string; explanation: { text: string; author: string; kind: string } | null;
  };
  participants: ReportParticipant[];
  execution: {
    mode: 'flower' | 'in-process'; flower_used: boolean; federation: string | null; run_id: string | null;
    supernodes: string[]; grid_messages: number; verdict_binding: 'accepted' | 'ignored' | null; fallback: boolean;
    duration_ms: number | null; rounds: ReportRound[];
  };
  evidence: {
    facts: ReportFact[];
    parties: { party: string; level: string; points: number; hard: boolean; floor: boolean; facts: Record<string, string> }[];
  };
  models: {
    jev: { used: boolean; status: 'voted' | 'unavailable' | 'not_configured' | 'not_reached'; vote: string | null;
      confidence: number | null; raised: boolean | null };
    endeavor: { used: boolean; status: 'explained' | 'other_model' | 'template' | 'not_reached'; model: string | null;
      via: string | null; fallback_reason: string | null };
  };
  policy_gate: {
    reached: boolean; decision: string | null; label: string | null; decided_by: string | null; score: number | null;
    thresholds: { review: number; decline: number } | null; rules: GateLine[]; fired: GateLine[];
    contributions: { fact: string; points: number; party: string }[]; stopped_reason: string | null;
  };
  human_review: {
    required: boolean; review_id: string | null; status: 'not_required' | 'pending' | 'decided';
    decision: 'approve' | 'decline' | null; final_decision_by_human: boolean; opened_at_ms: number | null;
    decided_at_ms: number | null;
  };
  payment: {
    processor: 'stripe'; mode: 'test' | 'unknown'; state: string; note: string; status: string | null;
    approved: boolean | null; charged: boolean; payment_intent_id: string | null; outcome: string | null;
  };
  security: {
    raw_card_data_shared: number | null; raw_card_data_basis: string;
    raw_card_history_shared: number; raw_card_history_basis: string;
    privacy_safe_facts_crossed: number; bytes_across_boundaries: number; off_vocabulary_values: number;
    rejected_messages: RejectedMessage[]; redacted_trace_steps: number; export_redactions: string[];
  };
  trace: TraceEvent[];
}

/** A report exists once a checkout reached an outcome: a verdict (held or final) or a stop before the gate. Never
 *  mid-settlement: after a person decides, the report waits for Stripe's answer and the final outcome. */
export function reportReady(view: Investigation): boolean {
  return Boolean(view.outcome) && (view.phase === 'review' || view.phase === 'done')
    && (Boolean(view.gate) || Boolean(stoppedText(view)));
}

export function reportFilename(report: Pick<DecisionReport, 'decision_id'>, kind: 'pdf' | 'json'): string {
  return `cardguard-decision-${report.decision_id.replace(/[^a-z0-9-]/gi, '')}.${kind}`;
}

export const PARTY_NAME: Record<string, string> = {
  stripe: 'Stripe card checks', bank: 'Bank attestation', store: 'Merchant (store)', network: 'Network memory',
};
const OUTCOME_WORD: Record<string, string> = {
  approved: 'Approved', declined: 'Declined', needs_review: 'Held for human review',
  approved_by_human: 'Approved by a person', declined_by_human: 'Declined by a person',
  blocked: 'Stopped at the privacy boundary', processor_rejected: 'Refused by Stripe',
  no_model_endpoint: 'Stopped: no model endpoint',
};
export const outcomeWord = (o: string | null) => (o ? OUTCOME_WORD[o] ?? pretty(o) : '—');

const last = (events: TraceEvent[], kind: string, pred: (e: TraceEvent) => boolean = () => true) =>
  [...events].reverse().find((e) => e.kind === kind && pred(e));
const count = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

function roundsOf(events: TraceEvent[], view: Investigation, facts: ReportFact[], rules: GateLine[]): ReportRound[] {
  const rounds: ReportRound[] = [];
  const q1 = events.find((e) => e.kind === 'coord.question' && (e.round ?? 1) === 1);
  // with several SuperNodes the Grid carries one reply per node: ours is the verified one, others are refused. After a
  // Flower fallback the store discloses in-process instead: the last verified reply is the one the gate decided on.
  const r1 = [...events].reverse().find((e) => e.status === 'verified'
      && ((e.kind === 'coord.reply' && (e.round ?? 1) === 1) || e.kind === 'store.disclosed'))
    ?? last(events, 'coord.reply', (e) => (e.round ?? 1) === 1) ?? last(events, 'store.disclosed');
  const r1Facts = facts.filter((f) => f.round === 1);
  if (r1Facts.length || r1 || q1) {
    const asked: string[] = [];
    if (events.some((e) => e.kind === 'processor.verify.reply')) asked.push('Stripe (card checks)');
    if (events.some((e) => e.kind === 'bank.attest.request')) asked.push('Bank attestation');
    if (events.some((e) => e.kind === 'store.facts')) asked.push('Merchant (store)');
    if (events.some((e) => e.kind === 'coord.network')) asked.push('Network memory');
    const verified = r1Facts.filter((f) => f.status === 'verified').length;
    rounds.push({
      round: 1,
      channel: r1?.kind === 'coord.reply' && r1.via === 'flower' ? 'flower_grid' : 'in_process',
      summary: r1?.status === 'verified'
        ? `${count(verified, 'banded fact')} reached the coordinator and were verified on arrival.`
        : r1 ? `The store's reply was ${r1.status === 'error' ? 'not received' : 'rejected at the boundary'}.`
          : `${count(r1Facts.length, 'banded fact')} collected; none reached the coordinator.`,
      asked, trigger: null, effect: null,
      question: q1 ? { purpose: q1.purpose ?? 'fraud-risk', bytes: q1.bytes ?? null, supernodes: q1.nodes?.length ?? 0 } : null,
      reply: r1 ? { status: r1.status ?? '?', bytes: r1.bytes ?? null, latency_ms: r1.latency_ms ?? null, reason: r1.reason ?? null } : null,
      facts: r1Facts,
    });
  }
  const conflict = last(events, 'coord.conflict');
  if (conflict || events.some((e) => e.round === 2)) {
    const q2 = events.find((e) => e.kind === 'coord.question' && e.round === 2);
    const r2 = last(events, 'coord.reply', (e) => e.round === 2 && e.status === 'verified') ?? last(events, 'coord.reply', (e) => e.round === 2);
    // the bank's answer counts only once it crossed to the coordinator and was verified there
    const travel = facts.find((f) => f.fact === 'travel_check' && f.status === 'verified');
    rounds.push({
      round: 2,
      channel: r2?.via === 'flower' || q2?.via === 'flower' ? 'flower_grid' : 'in_process',
      summary: travel ? `The bank answered one question: travel_check = ${travel.value}.`
        : 'The bank gave no verified answer, so a person decides.',
      asked: ['Bank attestation (through the store’s node)'],
      trigger: conflict?.reason ?? view.conflict ?? null,
      question: q2 ? { purpose: q2.purpose ?? 'travel-check', bytes: q2.bytes ?? null, supernodes: q2.nodes?.length ?? 0 } : null,
      reply: r2 ? { status: r2.status ?? '?', bytes: r2.bytes ?? null, latency_ms: r2.latency_ms ?? null, reason: r2.reason ?? null } : null,
      effect: rules.find((l) => l.rule === 'round_2')?.text ?? null,
      facts: facts.filter((f) => f.round === 2),
    });
  }
  return rounds;
}

function participantsOf(r: Omit<DecisionReport, 'participants' | 'security' | 'trace'>, events: TraceEvent[],
  view: Investigation): ReportParticipant[] {
  const out: ReportParticipant[] = [];
  const verify = last(events, 'processor.verify.reply');
  if (verify) {
    const ev = verify.evidence ?? {};
    out.push(verify.status === 'rejected'
      ? { component: 'Stripe (card checks)', role: 'Refused the payment method; no evidence was produced', status: 'Refused' }
      : { component: 'Stripe (card checks)', role: `Card checks returned as bands: CVC ${ev.cvc_check ?? '?'}, funding ${ev.card_funding ?? '?'}`, status: 'Answered' });
  }
  if (events.some((e) => e.kind === 'store.facts')) {
    const blocked = events.filter((e) => e.kind === 'guard.blocked').length;
    const disclosed = r.execution.rounds[0]?.reply?.status === 'verified';
    out.push({
      component: 'Merchant (store)',
      role: `Banded its own order history locally${disclosed ? '; disclosed banded facts only' : ''}`
        + (blocked ? `; ${count(blocked, 'outgoing message')} stopped at the wire guard` : ''),
      status: disclosed ? 'Disclosed' : blocked ? 'Blocked' : 'Not disclosed',
    });
  }
  const attest = last(events, 'bank.attest.reply');
  const travel = last(events, 'bank.travel.reply');
  if (attest) {
    out.push({
      component: 'Bank attestation',
      role: attest.status === 'ok'
        ? `Attested cardholder bands from its private history${travel?.status === 'ok' ? '; answered the round-2 travel check' : ''}`
        : 'No answer: its facts count as unknown',
      status: attest.status === 'ok' ? 'Answered' : 'Unavailable',
    });
  } else if (!r.environment.bank_attestation) {
    out.push({ component: 'Bank attestation', role: 'No bank attestation node configured', status: 'Not configured' });
  }
  if (r.policy_gate.reached || events.some((e) => e.kind.startsWith('coord.') || e.kind === 'store.disclosed')) {
    const flower = r.execution.flower_used && r.execution.verdict_binding === 'accepted';
    out.push({
      component: flower ? 'Coordinator (Flower AgentApp)' : 'Coordinator',
      role: flower
        ? `Ran as a Flower AgentApp on ${r.execution.federation}; asked the store's SuperNode over Grid and re-verified every reply`
        : r.execution.fallback ? 'Flower gave no bound verdict; the store node decided in its own process'
          : 'Ran in the store process; re-verified the disclosed facts',
      status: 'Verified',
    });
  }
  const network = last(events, 'coord.network');
  if (network) {
    out.push({
      component: 'Network memory',
      role: `Same card reference ${network.stores ? `seen at ${count(network.stores, 'store')} in 10 min` : 'checked across stores'}; velocity ${network.evidence?.network_velocity_band ?? 'low'}`,
      status: 'Answered',
    });
  }
  const jev = r.models.jev;
  if (jev.used) {
    out.push({
      component: 'Jev (TypeSafe)',
      role: `Risk advisory vote: ${pretty(jev.vote ?? '?')} at ${Math.round((jev.confidence ?? 0) * 100)}% confidence; it can only add caution`,
      status: jev.raised ? 'Added caution' : 'Advisory',
    });
  }
  const ex = r.models.endeavor;
  if (ex.used) {
    out.push({ component: 'Flower Endeavor', role: 'Wrote the explanation after the verdict; it cannot change it', status: 'Explanation only' });
  } else if (ex.status === 'other_model') {
    out.push({ component: `Explanation model (${ex.model})`, role: 'Wrote the explanation after the verdict; it cannot change it', status: 'Explanation only' });
  }
  out.push(r.policy_gate.reached
    ? {
      component: 'Policy Gate',
      role: view.review ? 'Fixed rules held it for a person' : jev.raised ? 'Fixed rules, then the more cautious vote' : 'Fixed rules decided',
      status: `Final authority: ${r.policy_gate.label}`,
    }
    : { component: 'Policy Gate', role: r.policy_gate.stopped_reason ?? 'Not reached', status: 'Not reached' });
  if (r.human_review.required) {
    out.push({
      component: 'Human reviewer',
      role: r.human_review.decision ? `Made the final call: ${r.human_review.decision === 'approve' ? 'approved' : 'declined'}` : 'Must decide; nothing is charged until then',
      status: r.human_review.decision ? 'Final decision' : 'Pending',
    });
  }
  return out;
}

function rejectedOf(view: Investigation, events: TraceEvent[]): RejectedMessage[] {
  return view.blocked.map((b) => {
    const e = events.find((x) => x.seq === b.seq);
    return {
      seq: b.seq, t_ms: e?.t ?? null, step: e?.kind ?? '?', from: b.from, to: b.to,
      stopped_at: b.stopAt === 'source' ? 'sender' : 'receiver',
      reason: b.reason ?? null, reason_plain: b.reason ? plainReason(b.reason) : 'refused',
    };
  });
}

/** A trace step as the report keeps it: the card reference and the store's private readings stay out (only the
 *  coarse buyer and card regions are reported, under `transaction`; the bank already receives both). */
function tracedStep(e: TraceEvent): TraceEvent {
  const copy: TraceEvent = { ...e };
  if (copy.evidence) copy.evidence = Object.fromEntries(Object.entries(copy.evidence).filter(([k]) => k !== 'token'));
  delete copy.private;
  return copy;
}

// ---------------------------------------------------------------- last line of defence
// Every string in the report is scanned before it is handed out. The trace is already leak-scanned on the
// node; this makes the export itself refuse a card number, expiry, CVC, a Stripe key, payment-method or setup id or
// webhook secret, the full card reference, or any value this page holds privately, even if an upstream check
// regressed. The PaymentIntent id is kept on purpose: it is the charge's reference and useless without a secret key.

const ID_KEYS = new Set(['run_id', 'node', 'nodes', 'supernodes']); // SuperLink ids: digits by shape, like the node's own exemption
const SENSITIVE: [RegExp, string][] = [
  [/\b(?:sk|rk|pk)_(?:test|live)_\w+/i, 'Stripe key'],
  [/\b(?:pm|seti|whsec)_[A-Za-z0-9]{6,}/, 'Stripe object id or secret'],
  [/\btok_[a-p]{16}\b/, 'full card reference'],
  [/\b(?:0[1-9]|1[0-2])\s?\/\s?(?:20)?\d{2}\b/, 'expiry-like date'],
  [/\b(?:cvc|cvv2?|csc|security code)\W{0,3}\d{3,4}\b/i, 'CVC-like value'],
];

function luhn(digits: string): boolean {
  let sum = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2) d = d * 2 > 9 ? d * 2 - 9 : d * 2;
    sum += d;
  }
  return sum % 10 === 0;
}

/** Why a string may not leave the page, or null when it is safe. */
export function sensitiveReason(value: string, forbidden: string[] = []): string | null {
  for (const run of value.replace(/_/g, ' ').match(/\d(?:[ -]?\d){12,18}/g) ?? []) { // '_' prints as a space in the PDF
    if (luhn(run.replace(/\D/g, ''))) return 'card-number-like digits';
  }
  for (const [re, why] of SENSITIVE) if (re.test(value)) return why;
  if (forbidden.some((f) => f.length >= 8 && value.includes(f))) return 'a value this page holds privately';
  return null;
}

function scrub<T>(value: T, forbidden: string[], found: string[], path = '$', key = ''): T {
  if (typeof value === 'string') {
    if (ID_KEYS.has(key) && /^\d{1,20}$/.test(value)) return value;
    const why = sensitiveReason(value, forbidden);
    if (!why) return value;
    found.push(`${path}: ${why}`);
    return '[redacted]' as T;
  }
  if (Array.isArray(value)) return value.map((v, i) => scrub(v, forbidden, found, `${path}[${i}]`, key)) as T;
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, scrub(v, forbidden, found, `${path}.${k}`, k)])) as T;
  }
  return value;
}

// ---------------------------------------------------------------- the report

export function buildReport(src: ReportSource, now: Date = new Date()): DecisionReport {
  const { events, config } = src;
  const view = derive(events, config.federation ? 'flower' : 'in-process', config.wire_vocabulary, src.rawCardShared);
  const v = verdictOf(view, true);
  const gate = view.gate;
  const gateEvent = gate ? last(events, 'gate.decision') : undefined;
  const started = events.find((e) => e.kind === 'payment.started');
  const storeFacts = last(events, 'store.facts');
  const stopped = stoppedText(view) ?? null;
  const rules = gate?.lines ?? [];
  const thresholds = gate ? gateThresholds(gate) : null;
  const human = view.review?.decided ?? null;
  const ex = gate ? explainerOf(gate) : null;
  const jev = gate?.jev;
  const binding = last(events, 'verdict.received');
  const stripeMode = config.publishable_key?.startsWith('pk_test_') ? 'test' : 'unknown';
  const pay = paymentStateOf(view);
  const hardStop = rules.some((l) => l.rule === 'hard_stop' && l.state === 'fail'); // declined before any model is asked

  const facts: ReportFact[] = view.evidence.map((x) => ({
    fact: x.key, label: factLabel(x.key), value: x.value, party: x.party, round: x.round, status: x.status,
    conflict: Boolean(x.conflict), risk_points: x.points ?? null,
  }));
  const fired = rules.filter((l) => l.state === 'warn' || l.state === 'fail');
  const why = gate?.explanation?.text || stopped || fired.map((l) => l.text).join('. ') || v.by;

  const base: Omit<DecisionReport, 'participants' | 'security' | 'trace'> = {
    report_version: REPORT_VERSION,
    generator: 'CardGuard investigation UI',
    decision_id: src.traceId,
    created_at: now.toISOString(),
    checkout_sent_at: src.startedAt ? new Date(src.startedAt).toISOString() : null,
    environment: {
      merchant_id: config.merchant_id, vertical: config.vertical, stripe_mode: stripeMode,
      demo_controls: config.demo_controls, bank_attestation: config.bank_attestation, federation: config.federation,
    },
    transaction: {
      amount_cents: view.amountCents ?? null,
      amount: view.amountCents !== undefined ? formatMoney(view.amountCents) : null,
      currency: 'USD',
      store: view.store ?? null,
      // the node honours the page's country only with demo controls; otherwise it geolocates the buyer
      buyer_country: config.demo_controls ? src.input?.buyer_country ?? null : null,
      buyer_region: typeof storeFacts?.private?.buyer_region === 'string' ? storeFacts.private.buyer_region : null,
      card_issuing_region: typeof storeFacts?.private?.card_region === 'string' ? storeFacts.private.card_region : null,
      attack_mode: started?.attack ?? null,
    },
    verdict: {
      final: v.key,
      label: v.word,
      decided_by: v.by,
      human_final: Boolean(human),
      gate_decision: gate?.decision ?? null,
      gate_label: gate ? VERDICT_WORD[gate.decision] ?? gate.decision : null,
      risk_score: gate?.score ?? null,
      thresholds,
      hard_stop: hardStop,
      summary: human ? `${why.replace(/\.?$/, '.')} A person then ${human === 'approve' ? 'approved' : 'declined'} it.` : why,
      explanation: gate?.explanation?.text && ex
        ? {
          text: gate.explanation.text,
          kind: ex.kind,
          author: ex.kind === 'template'
            ? 'Template fallback' // the reason (no model answered, answer rejected, not asked) is models.endeavor.fallback_reason
            : ex.kind === 'endeavor' ? `Flower Endeavor (${ex.model})` : ex.model,
        }
        : null,
    },
    execution: {
      mode: view.mode,
      flower_used: events.some((e) => e.kind === 'flower.run.started'),
      federation: started?.federation ?? null,
      run_id: view.runId ?? null,
      supernodes: view.supernodes,
      grid_messages: view.metrics.flowerMessages,
      verdict_binding: binding ? (binding.status === 'accepted' ? 'accepted' : 'ignored') : null,
      fallback: events.some((e) => e.kind === 'fallback'),
      duration_ms: events.at(-1)?.t ?? null,
      rounds: roundsOf(events, view, facts, rules),
    },
    evidence: {
      facts,
      parties: (gate?.parties ?? []).map((p) => ({ party: p.party, level: p.level, points: p.points, hard: p.hard, floor: p.floor, facts: p.facts })),
    },
    models: {
      jev: jev && 'action' in jev
        ? { used: true, status: 'voted', vote: jev.action, confidence: jev.confidence, raised: Boolean(jev.raised) }
        : { used: false, status: !gate || hardStop ? 'not_reached' : jev ? 'unavailable' : 'not_configured', vote: null, confidence: null, raised: null },
      endeavor: !ex
        ? { used: false, status: 'not_reached', model: null, via: null, fallback_reason: null }
        : ex.kind === 'template'
          ? { used: false, status: 'template', model: null, via: null, fallback_reason: ex.reason }
          : { used: ex.kind === 'endeavor', status: ex.kind === 'endeavor' ? 'explained' : 'other_model', model: ex.model,
            via: gate?.explanation?.via || null, fallback_reason: null },
    },
    policy_gate: {
      reached: Boolean(gate),
      decision: gate?.decision ?? null,
      label: gate ? VERDICT_WORD[gate.decision] ?? gate.decision : null,
      decided_by: gate?.decidedBy ?? null,
      score: gate?.score ?? null,
      thresholds,
      rules,
      fired,
      contributions: gateEvent?.contributions ?? [],
      stopped_reason: stopped,
    },
    human_review: {
      required: Boolean(view.review),
      review_id: view.review?.id || null,
      status: !view.review ? 'not_required' : human ? 'decided' : 'pending',
      decision: human,
      final_decision_by_human: Boolean(human),
      opened_at_ms: last(events, 'review.opened')?.t ?? null,
      decided_at_ms: last(events, 'review.decided')?.t ?? null,
    },
    payment: {
      processor: 'stripe', mode: stripeMode, state: pay.word, note: pay.note, status: view.payment?.status ?? null,
      approved: view.payment ? view.payment.approve : null, charged: Boolean(view.outcome?.charged),
      payment_intent_id: view.payment?.authCode ?? null, outcome: view.outcome?.outcome ?? null,
    },
  };

  const found: string[] = [];
  const report: DecisionReport = {
    ...base,
    participants: participantsOf(base, events, view),
    security: {
      raw_card_data_shared: src.rawCardShared,
      raw_card_data_basis: 'card numbers the coordinator has seen, as counted by the merchant node’s ledger (node-wide, not only this decision)',
      raw_card_history_shared: view.metrics.offVocabulary,
      raw_card_history_basis: 'verified values that crossed a boundary outside the closed band vocabulary',
      privacy_safe_facts_crossed: view.metrics.verifiedEvidence,
      bytes_across_boundaries: view.metrics.bytes,
      off_vocabulary_values: view.metrics.offVocabulary,
      rejected_messages: rejectedOf(view, events),
      redacted_trace_steps: events.filter((e) => e.kind === 'redacted').length,
      export_redactions: [],
    },
    trace: events.map(tracedStep),
  };
  const safe = scrub(report, src.forbidden ?? [], found);
  safe.security.export_redactions = found;
  return safe;
}
