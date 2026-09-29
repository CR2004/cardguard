// The step rail is a pure function of the same applied trace events as the graph, plus the one thing
// the browser itself knows: whether Stripe is tokenizing right now and whether it returned a
// payment-method id. A step is done, skipped or failed only because a real event says so.
import type { TraceEvent } from '../api/types';
import { formatMoney } from '../format';
import { settlementOf } from './derive';

export type StepId = 'token' | 'intake' | 'round1' | 'analysis' | 'round2' | 'gate' | 'review' | 'payment';
export type StepState = 'pending' | 'active' | 'waiting' | 'done' | 'skipped' | 'failed';
export type StepTone = 'good' | 'attention' | 'bad' | 'neutral';

export interface Step {
  id: StepId;
  title: string;
  state: StepState;
  detail: string;
  tone?: StepTone;
}

export interface ClientSide {
  tokenizing: boolean;
  paymentMethod: string | null; // the pm_ id Stripe returned to this page
  failed: boolean; // Stripe did not tokenize, or the checkout never started
  gateRevealing?: boolean; // the gate's rules are still being shown one by one, as on the seal
}

const TITLES: Record<StepId, string> = {
  token: 'Tokenized', intake: 'Store intake', round1: 'Round 1', analysis: 'Coordinator',
  round2: 'Round 2', gate: 'Policy gate', review: 'Human review', payment: 'Stripe payment',
};

const FINAL = new Set(['approved', 'declined', 'blocked', 'processor_rejected', 'no_model_endpoint',
  'approved_by_human', 'declined_by_human']);

// A leak attempt, as the wire guard words its refusals (guard.strip_for_wire), versus other stops.
const LEAKY = /card-number-like|expiry|cvv|not in vocabulary|bad value/;

function stoppedDetail(stops: TraceEvent[]): string {
  const n = stops.length;
  if (stops.every((e) => LEAKY.test(e.reason ?? ''))) return n > 1 ? `${n} leaks stopped` : 'Leak stopped';
  if (stops.some((e) => e.reason === 'token rate limit')) return 'Rate limit reached';
  return n > 1 ? `${n} messages stopped` : 'Message stopped';
}

export function steps(events: TraceEvent[], client: ClientSide): Step[] {
  const find = (kind: string, ok: (e: TraceEvent) => boolean = () => true) => events.find((e) => e.kind === kind && ok(e));
  const r2 = (e: TraceEvent) => e.round === 2;
  const r1 = (e: TraceEvent) => e.round !== 2;

  const started = find('payment.started');
  const refused = find('processor.verify.reply', (e) => e.status === 'rejected');
  const final = events.some((e) => e.kind === 'outcome' && FINAL.has(e.outcome ?? ''));
  // A Flower verdict the store could not bind is superseded: after `fallback` the store decides on its own node.
  const fellBack = events.findIndex((e) => e.kind === 'fallback');
  const gate = [...(fellBack >= 0 ? events.slice(fellBack + 1) : events)].reverse().find((e) => e.kind === 'gate.decision');
  const opened = find('review.opened');
  const decided = find('review.decided');
  const settled = [...events].reverse().find((e) => e.kind === 'payment.settled');
  const conflict = find('coord.conflict');

  const r1Start = events.find((e) => ['flower.run.request', 'store.disclosed'].includes(e.kind)
    || ((e.kind === 'coord.question' || e.kind === 'node.read' || e.kind === 'guard.blocked') && r1(e)));
  const r1Verified = events.find((e) => (e.kind === 'store.disclosed' || (e.kind === 'coord.reply' && r1(e))) && e.status === 'verified');
  // After a fallback the store decides on the facts its own SuperNode already read: node.read is their only trace.
  const r1Read = fellBack >= 0 ? find('node.read', (e) => r1(e) && e.status === 'disclosed') : undefined;
  const r1Guarded = events.filter((e) => e.kind === 'guard.blocked' && r1(e));
  const r1Stops = r1Guarded.filter((e) => !e.tampered); // a tampered draft is logged and ignored; the code facts continue
  const r1Rejected = events.filter((e) => e.kind === 'coord.reply' && r1(e) && e.status === 'rejected');
  const r2Start = events.find((e) => (e.kind === 'coord.question' || e.kind === 'node.read') && r2(e)) ?? find('bank.travel.request');
  const r2Reply = find('coord.reply', r2);
  const travel = find('bank.travel.reply');
  const r2Blocked = find('guard.blocked', r2);

  const out: Record<StepId, Omit<Step, 'id' | 'title'>> = {} as Record<StepId, Omit<Step, 'id' | 'title'>>;

  // 1. Stripe Elements turns the card into a payment-method id (the page's own call to Stripe)
  if (client.tokenizing) out.token = { state: 'active', detail: 'Stripe is tokenizing' };
  else if (client.paymentMethod || started) out.token = { state: 'done', detail: 'Payment method' };
  else if (client.failed) out.token = { state: 'failed', detail: 'No payment method', tone: 'bad' };
  else out.token = { state: 'pending', detail: 'Stripe Elements' };

  // 2. The store receives the id, asks Stripe for card checks and the bank for its attestation
  if (refused) out.intake = { state: 'failed', detail: 'Stripe refused the card', tone: 'bad' };
  else if (r1Start || (started && final)) {
    const bank = find('bank.attest.reply');
    out.intake = { state: 'done', detail: bank?.status === 'ok' ? 'Stripe and bank' : 'Stripe checks' };
  } else if (started) {
    const lastKind = events.at(-1)?.kind;
    const asking = lastKind === 'bank.attest.request' ? 'Asking the bank' : lastKind === 'bank.attest.reply' ? 'Bank answered'
      : lastKind === 'store.facts' ? 'Banding its facts' : 'Asking Stripe';
    out.intake = { state: 'active', detail: asking };
  } else out.intake = { state: 'pending', detail: 'Merchant node' };

  // 3. Round 1: the coordinator asks the store; only verified banded facts count
  if (r1Verified) {
    const n = Object.keys(r1Verified.evidence ?? {}).filter((k) => k !== 'token').length;
    out.round1 = r1Guarded.some((e) => e.tampered) ? { state: 'done', detail: 'Altered draft ignored', tone: 'attention' }
      : r1Rejected.length ? { state: 'done', detail: `${n} verified, ${r1Rejected.length} rejected`, tone: 'attention' }
        : { state: 'done', detail: `${n} fact${n === 1 ? '' : 's'} verified` };
  } else if (r1Read) {
    out.round1 = { state: 'done', detail: r1Rejected.length ? 'Read, replies unused' : 'Read by its node', tone: 'attention' };
  } else if (r1Stops.length) {
    out.round1 = { state: 'failed', detail: stoppedDetail(r1Stops), tone: 'bad' };
  } else if (r1Rejected.length) {
    out.round1 = { state: final ? 'failed' : 'active', detail: 'Reply rejected', tone: 'bad' };
  } else if (r1Start && !final) {
    const overFlower = events.some((e) => e.kind === 'flower.run.request') && fellBack < 0;
    out.round1 = { state: 'active', detail: overFlower ? 'Over Flower Grid' : 'Asking the store' };
  } else out.round1 = final ? { state: 'skipped', detail: 'Not reached' } : { state: 'pending', detail: 'Store answers' };

  // 4. The coordinator checks the network memory and looks for conflicting evidence
  if (conflict) out.analysis = { state: 'done', detail: 'Conflict found', tone: 'attention' };
  else if (gate && (r1Verified || r1Read)) out.analysis = { state: 'done', detail: 'No conflict' };
  else if ((r1Verified || r1Read) && !final) {
    out.analysis = { state: 'active', detail: find('coord.network') ? 'Weighing evidence' : 'Checking stores' };
  } else out.analysis = final ? { state: 'skipped', detail: 'Not reached' } : { state: 'pending', detail: 'Weighs evidence' };

  // 5. Round 2: one targeted question to the bank, only when the evidence conflicts
  const answer = r2Reply?.evidence?.travel_check ?? travel?.evidence?.travel_check;
  if (r2Blocked) out.round2 = { state: 'failed', detail: 'Reply stopped', tone: 'bad' };
  else if (r2Reply || (travel && gate)) {
    out.round2 = answer
      ? { state: 'done', detail: `Travel ${answer}`, tone: answer === 'plausible' ? undefined : 'attention' }
      : { state: 'done', detail: 'Bank could not say', tone: 'attention' };
  } else if (conflict || r2Start) {
    out.round2 = final || gate ? { state: 'done', detail: 'No answer', tone: 'attention' }
      : { state: 'active', detail: travel ? `Travel ${travel.evidence?.travel_check ?? 'unknown'}` : 'Asking the bank' };
  } else if (gate) out.round2 = { state: 'skipped', detail: 'Not needed' };
  else if (final) out.round2 = { state: 'skipped', detail: 'Not reached' };
  else out.round2 = { state: 'pending', detail: 'Only if needed' };

  // 6. The policy gate: fixed rules compute the verdict
  if (gate && client.gateRevealing) out.gate = { state: 'active', detail: 'Evaluating' };
  else if (gate) {
    const d = gate.decision ?? 'step_up';
    out.gate = { state: 'done', detail: { approve: 'Approve', step_up: 'Hold for a person', decline: 'Decline' }[d],
      tone: d === 'approve' ? 'good' : d === 'decline' ? 'bad' : 'attention' };
  } else if (final) out.gate = { state: 'skipped', detail: 'Not reached' };
  else if (fellBack >= 0) out.gate = { state: 'active', detail: 'Deciding locally' };
  else out.gate = { state: 'pending', detail: 'Rules decide' };

  // 7. Human review: only when the gate holds the payment
  if (decided) {
    const approve = decided.action === 'approve';
    out.review = { state: 'done', detail: approve ? 'Person approved' : 'Person declined', tone: approve ? 'good' : 'bad' };
  } else if (opened) out.review = { state: 'waiting', detail: 'Needs a person', tone: 'attention' };
  else if (final || settled) {
    out.review = { state: 'skipped', detail: gate && gate.decision !== 'step_up' ? 'Not needed' : 'Not reached' };
  } else out.review = { state: 'pending', detail: 'Only if held' };

  // 8. What Stripe did with the money, kept apart from the decision
  if (settled) {
    const s = settlementOf(Boolean(settled.approve), settled.status ?? '', settled.reason);
    const amount = started?.amount_cents;
    const detail = s.word === 'Charged' && amount !== undefined ? `Charged ${formatMoney(amount)}`
      : s.word === 'Voided' ? 'Voided, no charge' : s.word;
    out.payment = { state: 'done', detail, tone: s.tone };
  } else if (decided) out.payment = { state: 'active', detail: 'Settling' };
  else if (opened) out.payment = { state: 'waiting', detail: 'Held, no charge', tone: 'attention' };
  else if (final) out.payment = { state: 'skipped', detail: 'Nothing charged' };
  else out.payment = { state: 'pending', detail: 'Charge or void' };

  return (Object.keys(TITLES) as StepId[]).map((id) => ({ id, title: TITLES[id], ...out[id] }));
}
