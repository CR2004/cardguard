import { describe, expect, it } from 'vitest';
import type { TraceEvent } from '../api/types';
import flower from './fixtures/flower-collaborative.json';
import { steps, type ClientSide, type StepId, type StepState } from './steps';

// A real trace recorded from the local Flower deployment (SuperLink + one SuperNode).
const events = flower as TraceEvent[];
const idle: ClientSide = { tokenizing: false, paymentMethod: null, failed: false };
const withPm: ClientSide = { ...idle, paymentMethod: 'pm_visa' };
const upTo = (kind: string, nth = 1) => {
  let seen = 0;
  const i = events.findIndex((e) => e.kind === kind && ++seen === nth);
  return events.slice(0, i + 1);
};
const state = (evs: TraceEvent[], client = withPm) =>
  Object.fromEntries(steps(evs, client).map((s) => [s.id, s.state])) as Record<StepId, StepState>;
const detail = (evs: TraceEvent[], id: StepId) => steps(evs, withPm).find((s) => s.id === id)?.detail;

let seq = 0;
const ev = (kind: string, extra: Partial<TraceEvent> = {}): TraceEvent => ({ seq: seq++, t: seq, kind, ...extra });

describe('steps: the rail is a function of real events only', () => {
  it('nothing is done before Stripe returns a payment method', () => {
    expect(Object.values(state([], idle)).every((s) => s === 'pending')).toBe(true);
    expect(state([], { ...idle, tokenizing: true }).token).toBe('active');
    expect(state([], { ...idle, failed: true }).token).toBe('failed');
    expect(state([], withPm).token).toBe('done');
    expect(state([], withPm).intake).toBe('pending');
  });

  it('a step that is done stays done as more real events arrive', () => {
    let before = new Set<StepId>();
    for (let k = 0; k <= events.length; k++) {
      const done = new Set(steps(events.slice(0, k), withPm).filter((s) => s.state === 'done').map((s) => s.id));
      for (const id of before) expect(done.has(id)).toBe(true);
      before = done;
    }
  });

  it('walks the recorded Flower collaborative investigation in order', () => {
    expect(state(upTo('payment.started')).intake).toBe('active');
    expect(state(upTo('flower.run.request'))).toMatchObject({ intake: 'done', round1: 'active', analysis: 'pending' });
    const r1 = upTo('coord.reply', 1);
    expect(state(r1)).toMatchObject({ round1: 'done', analysis: 'active', round2: 'pending' });
    expect(detail(r1, 'round1')).toMatch(/facts verified$/);
    expect(state(upTo('coord.conflict'))).toMatchObject({ analysis: 'done', round2: 'active', gate: 'pending' });
    expect(detail(upTo('coord.conflict'), 'analysis')).toBe('Conflict found');
    const r2 = upTo('coord.reply', 2);
    expect(state(r2)).toMatchObject({ round2: 'done', gate: 'pending' }); // nothing is evaluating until gate.decision arrives
    expect(detail(r2, 'round2')).toBe('Travel implausible');
    expect(state(events)).toMatchObject({ gate: 'done', review: 'waiting', payment: 'waiting' });
    expect(detail(events, 'gate')).toBe('Hold for a person');
    expect(steps(upTo('gate.decision'), { ...withPm, gateRevealing: true }).find((s) => s.id === 'gate'))
      .toMatchObject({ state: 'active', detail: 'Evaluating' }); // in step with the seal while its rules reveal
  });

  it('a person deciding and Stripe settling complete the last two steps', () => {
    const after = [...events, ev('review.decided', { action: 'approve' }),
      ev('payment.settled', { approve: true, status: 'succeeded', processor: 'stripe' }),
      ev('outcome', { outcome: 'approved_by_human', charged: true })];
    expect(state(after)).toMatchObject({ review: 'done', payment: 'done' });
    expect(detail(after, 'review')).toBe('Person approved');
    expect(detail(after, 'payment')).toBe('Charged $64.00');
  });

  it('an in-process approval marks round 2 and the human as not needed', () => {
    const run = [ev('payment.started', { amount_cents: 2400 }), ev('processor.verify.request'),
      ev('processor.verify.reply', { status: 'ok' }), ev('bank.attest.request'), ev('bank.attest.reply', { status: 'ok' }),
      ev('store.facts'), ev('store.disclosed', { status: 'verified', evidence: { amount_band: 'low', token: 'tok_x' } }),
      ev('coord.network'), ev('gate.decision', { decision: 'approve' }),
      ev('payment.settled', { approve: true, status: 'succeeded' }), ev('outcome', { outcome: 'approved', charged: true })];
    expect(state(run)).toMatchObject({ round1: 'done', analysis: 'done', round2: 'skipped', gate: 'done', review: 'skipped',
      payment: 'done' });
    expect(detail(run, 'round1')).toBe('1 fact verified');
  });

  it('a rogue store fails round 1 and nothing after it claims to have run', () => {
    const run = [ev('payment.started', { amount_cents: 2400 }), ev('processor.verify.reply', { status: 'ok' }),
      ev('store.facts'), ev('guard.blocked', { status: 'rejected', reason: "'amount_band': card-number-like digits" }),
      ev('guard.blocked', { status: 'rejected', reason: "value for 'velocity_band' not in vocabulary" }),
      ev('payment.settled', { approve: false, status: 'voided' }), ev('outcome', { outcome: 'blocked', charged: false })];
    expect(state(run)).toMatchObject({ round1: 'failed', analysis: 'skipped', round2: 'skipped', gate: 'skipped',
      review: 'skipped', payment: 'done' });
    expect(detail(run, 'round1')).toBe('2 leaks stopped');
    expect(detail(run, 'payment')).toBe('Voided, no charge');
    expect(steps(run, withPm).find((s) => s.id === 'payment')?.tone).toBe('neutral'); // a void is not a success colour
  });

  it('Stripe refusing the card fails intake and charges nothing', () => {
    const run = [ev('payment.started'), ev('processor.verify.reply', { status: 'rejected' }),
      ev('outcome', { outcome: 'processor_rejected', charged: false })];
    expect(state(run)).toMatchObject({ intake: 'failed', round1: 'skipped', gate: 'skipped', payment: 'skipped' });
  });

  const base = () => [ev('payment.started', { amount_cents: 2400 }), ev('processor.verify.reply', { status: 'ok' }),
    ev('bank.attest.reply', { status: 'ok' }), ev('store.facts')];
  const disclosed = () => ev('store.disclosed', { status: 'verified', evidence: { amount_band: 'low', token: 'tok_x' } });
  const at = (evs: TraceEvent[], id: StepId) => steps(evs, withPm).find((x) => x.id === id);

  it('never calls a void that Stripe did not confirm "voided"', () => {
    const run = (status: string, approve = false) => [...base(), disclosed(), ev('gate.decision', { decision: approve ? 'approve' : 'decline' }),
      ev('payment.settled', { approve, status }), ev('outcome', { outcome: approve ? 'approved' : 'declined' })];
    expect(at(run('voided'), 'payment')).toMatchObject({ detail: 'Voided, no charge', tone: 'neutral' });
    expect(at(run('void_failed'), 'payment')).toMatchObject({ detail: 'Void pending', tone: 'attention' });
    expect(at(run('processor_error'), 'payment')).toMatchObject({ detail: 'Void unconfirmed', tone: 'bad' });
    expect(at(run('processor_declined', true), 'payment')).toMatchObject({ detail: 'Not charged', tone: 'bad' });
  });

  it('a gate decline needs no person; a person declining is shown as theirs', () => {
    const declined = [...base(), disclosed(), ev('gate.decision', { decision: 'decline' }),
      ev('payment.settled', { approve: false, status: 'voided' }), ev('outcome', { outcome: 'declined' })];
    expect(at(declined, 'gate')).toMatchObject({ detail: 'Decline', tone: 'bad' });
    expect(at(declined, 'review')).toMatchObject({ state: 'skipped', detail: 'Not needed' });
    const human = [...events, ev('review.decided', { action: 'decline' })];
    expect(at(human, 'review')).toMatchObject({ detail: 'Person declined', tone: 'bad' });
    expect(at(human, 'payment')).toMatchObject({ state: 'active', detail: 'Settling' }); // decided, not settled yet
  });

  it('a tampered model draft is ignored while the code facts carry on', () => {
    const run = [...base(), ev('guard.blocked', { status: 'rejected', tampered: true, reason: 'integrity: agent altered amount_band' }),
      disclosed(), ev('coord.network'), ev('gate.decision', { decision: 'approve' }),
      ev('payment.settled', { approve: true, status: 'succeeded' }), ev('outcome', { outcome: 'approved', charged: true })];
    expect(at(run, 'round1')).toMatchObject({ state: 'done', detail: 'Altered draft ignored', tone: 'attention' });
    expect(at(run, 'payment')).toMatchObject({ state: 'done', tone: 'good' });
  });

  it('a rate limit is not called a leak, and unused SuperNode replies do not fail round 1', () => {
    const limited = [...base(), ev('guard.blocked', { status: 'rejected', reason: 'token rate limit' }),
      ev('payment.settled', { approve: false, status: 'voided' }), ev('outcome', { outcome: 'blocked' })];
    expect(at(limited, 'round1')).toMatchObject({ state: 'failed', detail: 'Rate limit reached' });
    expect(at(limited, 'review')).toMatchObject({ state: 'skipped', detail: 'Not reached' });
    const r1 = upTo('coord.reply', 1);
    const extra = [...r1, ev('coord.reply', { round: 1, status: 'rejected', reason: 'not used' })];
    expect(at(extra, 'round1')).toMatchObject({ state: 'done', tone: 'attention' });
    expect(at(extra, 'round1')?.detail).toMatch(/verified, 1 rejected$/);
  });

  it('round 2 failures are named, and a round-2 stop does not fail round 1', () => {
    const conflict = [...base(), disclosed(), ev('coord.network'), ev('coord.conflict', { round: 2 }), ev('coord.question', { round: 2 })];
    const unavailable = [...conflict, ev('bank.travel.request', { round: 2 }), ev('bank.travel.reply', { round: 2, status: 'unavailable' }),
      ev('coord.reply', { round: 2, status: 'error' })];
    expect(at(unavailable, 'round2')).toMatchObject({ state: 'done', detail: 'Bank could not say', tone: 'attention' });
    const blocked = [...conflict, ev('guard.blocked', { round: 2, status: 'rejected', reason: 'x' })];
    expect(at(blocked, 'round2')).toMatchObject({ state: 'failed' });
    expect(at(blocked, 'round1')?.state).toBe('done');
    expect(at([...conflict, ev('gate.decision', { decision: 'step_up' })], 'round2')).toMatchObject({ detail: 'No answer' });
    const plausible = [...conflict, ev('bank.travel.reply', { round: 2, status: 'ok', evidence: { travel_check: 'plausible' } }),
      ev('coord.reply', { round: 2, status: 'verified', evidence: { travel_check: 'plausible' } })];
    expect(at(plausible, 'round2')).toMatchObject({ detail: 'Travel plausible', tone: undefined });
  });

  it('after a Flower fallback the store node\'s own gate is the one that counts', () => {
    const flower = [...base(), ev('flower.run.request'), ev('coord.nodes', { nodes: [] }), ev('gate.decision', { decision: 'step_up' }),
      ev('verdict.received', { status: 'ignored' }), ev('fallback')];
    expect(at(flower, 'gate')).toMatchObject({ state: 'active', detail: 'Deciding locally' });
    expect(at(flower, 'round1')?.detail).toBe('Asking the store');
    expect(at(flower, 'analysis')?.state).toBe('pending');
    const local = [...flower, disclosed(), ev('coord.network'), ev('gate.decision', { decision: 'approve' }),
      ev('payment.settled', { approve: true, status: 'succeeded' }), ev('outcome', { outcome: 'approved', charged: true })];
    expect(at(local, 'gate')).toMatchObject({ detail: 'Approve', tone: 'good' });
    expect(at(local, 'review')).toMatchObject({ detail: 'Not needed' });
    expect(at(local, 'payment')?.detail).toBe('Charged $24.00');
  });

  it('a checkout stopped before any decision shows the later steps as not reached', () => {
    const run = [...base(), ev('payment.settled', { approve: false, status: 'voided' }), ev('outcome', { outcome: 'no_model_endpoint' })];
    expect(state(run)).toMatchObject({ intake: 'done', round1: 'skipped', analysis: 'skipped', round2: 'skipped', gate: 'skipped',
      review: 'skipped', payment: 'done' });
    expect(at(run, 'round2')?.detail).toBe('Not reached');
  });

  it('names what the store is waiting on during intake', () => {
    const s0 = [ev('payment.started'), ev('bank.attest.request')];
    expect(at(s0, 'intake')?.detail).toBe('Asking the bank');
    expect(at([...s0, ev('bank.attest.reply', { status: 'ok' })], 'intake')?.detail).toBe('Bank answered');
  });

  it('after a fallback, facts the store\'s own SuperNode already read still count as round 1', () => {
    const run = [...base(), ev('flower.run.request'), ev('coord.question', { round: 1 }), ev('node.read', { status: 'disclosed' }),
      ev('gate.decision', { decision: 'step_up' }), ev('verdict.received', { status: 'ignored' }), ev('fallback'), ev('coord.network'),
      ev('gate.decision', { decision: 'approve' }), ev('payment.settled', { approve: true, status: 'succeeded' }),
      ev('outcome', { outcome: 'approved', charged: true })];
    expect(at(run, 'round1')).toMatchObject({ state: 'done', detail: 'Read by its node' });
    expect(at(run, 'analysis')).toMatchObject({ state: 'done', detail: 'No conflict' });
    const beforeFallback = run.slice(0, 7); // node.read without a fallback is not yet evidence of anything
    expect(at(beforeFallback, 'round1')?.state).toBe('active');
  });

  it('a capture error is not reported as "not charged"', () => {
    const run = [...base(), disclosed(), ev('gate.decision', { decision: 'approve' }),
      ev('payment.settled', { approve: true, status: 'processor_error' }), ev('outcome', { outcome: 'approved', charged: false })];
    expect(at(run, 'payment')).toMatchObject({ detail: 'Charge unconfirmed', tone: 'bad' });
  });
});
