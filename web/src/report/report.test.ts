import { jsPDF } from 'jspdf';
import { describe, expect, it } from 'vitest';
import type { Config, TraceEvent } from '../api/types';
import { verdictOf } from '../components/Inspector';
import { derive } from '../investigation/derive';
import flower from '../investigation/fixtures/flower-collaborative.json';
import { drawReport, pdfText } from './pdf';
import { buildReport, reportFilename, reportReady, sensitiveReason, type ReportSource } from './report';

const TRACE_ID = 'abcdefghijklmnop';
const CONFIG: ReportSource['config'] = {
  merchant_id: 'cardguard-store', vertical: 'W', federation: null, bank_attestation: true,
  publishable_key: 'pk_test_placeholder', demo_controls: true, wire_vocabulary: {},
};
const FLOWER: ReportSource['config'] = { ...CONFIG, federation: 'local-agent' };

let seq = 0;
const ev = (kind: string, fields: Partial<TraceEvent> = {}): TraceEvent => ({ seq: seq++, t: seq * 10, kind, ...fields });
const source = (events: TraceEvent[], config = CONFIG, extra: Partial<ReportSource> = {}): ReportSource => ({
  traceId: TRACE_ID, events, config, input: { buyer_country: 'US' }, startedAt: Date.UTC(2026, 8, 29, 17), rawCardShared: 0, ...extra,
});
const lines = (rules: [string, 'pass' | 'warn' | 'fail' | 'info', string][]) => rules.map(([rule, state, text]) => ({ rule, state, text }));

// The recorded Flower collaborative investigation: round 2, then held for a person.
const collaborative = flower as TraceEvent[];

// A person approves the held payment; Stripe captures it.
function humanApproved(): TraceEvent[] {
  seq = collaborative.length;
  return [...collaborative, ev('review.decided', { action: 'approve', review_id: '03a9ce25' }),
    ev('payment.settled', { approve: true, status: 'succeeded', processor: 'stripe', auth_code: 'pi_3QabcDEF456ghiJKL789' }),
    ev('outcome', { outcome: 'approved_by_human', charged: true })];
}

// Normal purchase, in-process, with a Jev vote and an Endeavor explanation served through Flower.
function normal(): TraceEvent[] {
  seq = 0;
  return [
    ev('payment.started', { amount_cents: 2400, store: 'cardguard-store', processor: 'stripe', federation: null, attack: null, bytes: 27 }),
    ev('processor.verify.request'),
    ev('processor.verify.reply', { status: 'ok', evidence: { cvc_check: 'pass', card_funding: 'credit' } }),
    ev('bank.attest.request'),
    ev('bank.attest.reply', { status: 'ok', evidence: { issuer_behavior: 'low', issuer_recent_declines: 'none' } }),
    ev('store.facts', { evidence: { amount_band: 'low', country_mismatch: 'no' },
      private: { buyer_region: 'North America', card_region: 'North America', purchases_here_24h: 1 } }),
    ev('store.disclosed', { status: 'verified', evidence: { amount_band: 'low', country_mismatch: 'no', cvc_check: 'pass',
      token: 'tok_ab…nop' }, bytes: 200 }),
    ev('coord.network', { evidence: { network_velocity_band: 'low' }, stores: 1 }),
    ev('gate.decision', {
      decision: 'approve', decided_by: 'rules+jev', score: 0, via: 'in-process', round_2: false,
      lines: lines([['evidence', 'pass', 'Evidence complete: one verified fact set for this decision'],
        ['hard_stop', 'pass', 'No hard stop: the CVC check did not fail'],
        ['score', 'info', 'Risk points 0: review from 3, decline from 8'],
        ['rules', 'pass', 'Below the review line: rules approve'],
        ['model', 'info', 'Model vote: approve at 91% confidence; the more cautious vote wins']]),
      contributions: [], parties: [], jev: { action: 'approve', confidence: 0.91, raised: false },
      explanation: { text: 'Approved: every party reported ordinary activity.', by: 'flwrlabs/endeavor-1.0', via: 'flower', error: '' },
    }),
    ev('payment.settled', { approve: true, status: 'succeeded', processor: 'stripe', auth_code: 'pi_3QnormalPaymentIntent01' }),
    ev('outcome', { outcome: 'approved', charged: true }),
  ];
}

// Obvious fraud: Stripe's CVC check failed, a hard decline with no score and no second round.
function fraud(): TraceEvent[] {
  seq = 0;
  return [
    ev('payment.started', { amount_cents: 90000, store: 'cardguard-store', processor: 'stripe', federation: null, attack: null }),
    ev('processor.verify.reply', { status: 'ok', evidence: { cvc_check: 'fail', card_funding: 'credit' } }),
    ev('store.facts', { evidence: { amount_band: 'high', country_mismatch: 'yes' } }),
    ev('store.disclosed', { status: 'verified', evidence: { amount_band: 'high', country_mismatch: 'yes', cvc_check: 'fail' } }),
    ev('gate.decision', {
      decision: 'decline', decided_by: 'rules', score: null,
      lines: lines([['evidence', 'pass', 'Evidence complete: one verified fact set for this decision'],
        ['hard_stop', 'fail', 'CVC check failed: automatic decline, no vote']]),
      contributions: [{ fact: 'cvc_check=fail', points: 0, party: 'stripe' }], parties: [], jev: null,
      explanation: { text: 'Declined: the card failed its security-code check.', by: 'template', via: '', error: 'unavailable' },
    }),
    ev('payment.settled', { approve: false, status: 'voided', processor: 'stripe' }),
    ev('outcome', { outcome: 'declined', charged: false }),
  ];
}

// Rogue node: a compromised store agent tries three times to put the card on the wire.
function rogue(): TraceEvent[] {
  seq = 0;
  return [
    ev('payment.started', { amount_cents: 2400, store: 'cardguard-store', processor: 'stripe', federation: null, attack: 'leak' }),
    ev('processor.verify.reply', { status: 'ok', evidence: { cvc_check: 'pass', card_funding: 'credit' } }),
    ev('store.facts', { evidence: { amount_band: 'low' } }),
    ev('guard.blocked', { status: 'rejected', reason: "'amount_band': card-number-like digits" }),
    ev('guard.blocked', { status: 'rejected', reason: "'new_customer': card-number-like digits" }),
    ev('guard.blocked', { status: 'rejected', reason: "value for 'velocity_band' not in vocabulary" }),
    ev('payment.settled', { approve: false, status: 'voided', processor: 'stripe' }),
    ev('outcome', { outcome: 'blocked', charged: false }),
  ];
}

const pdfOf = (report: ReturnType<typeof buildReport>) => {
  const doc = drawReport(new jsPDF({ unit: 'pt', format: 'letter', compress: false }), report);
  return { text: doc.output(), pages: doc.getNumberOfPages() };
};

describe('decision report: built from the trace the page shows, nothing else', () => {
  it('the recorded Flower investigation: round 2, held for a person, no model named that did not answer', () => {
    const r = buildReport(source(collaborative, FLOWER));
    expect(JSON.parse(JSON.stringify(r))).toEqual(r);
    expect(r).toMatchObject({ report_version: '1.0', decision_id: TRACE_ID });
    const view = derive(collaborative, 'flower');
    expect(r.verdict.label).toBe(verdictOf(view, true).word); // the same word the verdict pane shows
    expect(r.verdict).toMatchObject({ final: 'step_up', label: 'Human review', human_final: false });
    expect(r.execution).toMatchObject({ flower_used: true, run_id: '11102192617470830952', verdict_binding: 'accepted', fallback: false });
    expect(r.execution.rounds.map((x) => x.round)).toEqual([1, 2]);
    expect(r.execution.rounds[1]).toMatchObject({ channel: 'flower_grid', facts: [expect.objectContaining({ fact: 'travel_check', value: 'implausible' })] });
    expect(r.execution.rounds[1]?.trigger).toMatch(/outside the card's country/);
    expect(r.models).toMatchObject({ jev: { used: false, status: 'not_configured' }, endeavor: { used: false, status: 'template' } });
    expect(r.participants.map((p) => p.component)).not.toEqual(expect.arrayContaining(['Jev (TypeSafe)']));
    expect(r.participants.map((p) => p.component)).not.toContain('Flower Endeavor');
    expect(r.human_review).toMatchObject({ required: true, status: 'pending', review_id: '03a9ce25' });
    expect(r.payment).toMatchObject({ state: 'Held', charged: false, outcome: 'needs_review' });
    expect(r.transaction).toMatchObject({ amount_cents: 6400, buyer_country: 'US', buyer_region: 'Europe' });
  });

  it('a person approving makes the report say so, and Stripe’s capture is the payment state', () => {
    const r = buildReport(source(humanApproved(), FLOWER));
    expect(r.verdict).toMatchObject({ final: 'approve', label: 'Approved', human_final: true, gate_decision: 'step_up' });
    expect(r.human_review).toMatchObject({ status: 'decided', decision: 'approve', final_decision_by_human: true });
    expect(r.payment).toMatchObject({ state: 'Charged', charged: true, payment_intent_id: 'pi_3QabcDEF456ghiJKL789' });
    expect(r.participants.at(-1)).toMatchObject({ component: 'Human reviewer', status: 'Final decision' });
  });

  it('a normal approval: one round, and Jev and Endeavor appear because they answered', () => {
    const r = buildReport(source(normal()));
    expect(r.verdict).toMatchObject({ final: 'approve', label: 'Approve', risk_score: 0, thresholds: { review: 3, decline: 8 } });
    expect(r.execution.rounds.map((x) => x.round)).toEqual([1]);
    expect(r.execution.flower_used).toBe(false);
    expect(r.models.jev).toMatchObject({ used: true, vote: 'approve', confidence: 0.91 });
    expect(r.models.endeavor).toMatchObject({ used: true, model: 'flwrlabs/endeavor-1.0', via: 'flower' });
    expect(r.participants.map((p) => p.component)).toEqual(expect.arrayContaining(['Jev (TypeSafe)', 'Flower Endeavor', 'Policy Gate']));
    expect(r.human_review.required).toBe(false);
  });

  it('obvious fraud: a hard decline with no score, no round 2 and a voided hold', () => {
    const r = buildReport(source(fraud()));
    expect(r.verdict).toMatchObject({ final: 'decline', label: 'Decline', hard_stop: true, risk_score: null });
    expect(r.execution.rounds).toHaveLength(1);
    expect(r.payment).toMatchObject({ state: 'Voided', charged: false });
    expect(r.models.endeavor).toMatchObject({ used: false, status: 'template', fallback_reason: 'unavailable' });
    expect(r.models.jev).toMatchObject({ used: false, status: 'not_reached' }); // a hard stop never asks Jev
  });

  it('rogue node: stopped before the gate, every leak attempt listed as rejected at the sender', () => {
    const events = rogue();
    expect(reportReady(derive(events, 'in-process'))).toBe(true);
    const r = buildReport(source(events));
    expect(r.verdict).toMatchObject({ final: 'stopped', label: 'Stopped' });
    expect(r.policy_gate).toMatchObject({ reached: false, stopped_reason: expect.stringMatching(/rejected at the boundary/) });
    expect(r.security.rejected_messages).toHaveLength(3);
    expect(r.security.rejected_messages.every((m) => m.stopped_at === 'sender')).toBe(true);
    expect(r.security.rejected_messages[0]?.reason_plain).toBe('Amount carried card-number-like digits');
    expect(r.transaction.attack_mode).toBe('leak');
    expect(r.payment.state).toBe('Voided');
  });

  it('is offered only once the checkout reached an outcome', () => {
    expect(reportReady(derive(normal().slice(0, 8), 'in-process'))).toBe(false); // gate not reached yet
    expect(reportReady(derive(normal(), 'in-process'))).toBe(true);
    expect(reportReady(derive(collaborative, 'flower'))).toBe(true); // held for a person
    const decided = humanApproved();
    expect(reportReady(derive(decided.slice(0, -2), 'flower'))).toBe(false); // a person decided; Stripe has not answered
    expect(reportReady(derive(decided, 'flower'))).toBe(true);
    expect(reportFilename({ decision_id: TRACE_ID }, 'pdf')).toBe(`cardguard-decision-${TRACE_ID}.pdf`);
    expect(reportFilename({ decision_id: TRACE_ID }, 'json')).toBe(`cardguard-decision-${TRACE_ID}.json`);
  });
});

describe('decision report: privacy', () => {
  it('drops the card reference and the parties’ private readings from the exported trace', () => {
    const r = buildReport(source(collaborative, FLOWER));
    const json = JSON.stringify(r);
    expect(json).not.toMatch(/tok_/);
    expect(json).not.toMatch(/purchases_here_24h|"private"/);
    expect(json).not.toMatch(/pk_test|sk_test|pm_/);
    expect(r.security).toMatchObject({ raw_card_data_shared: 0, raw_card_history_shared: 0, export_redactions: [] });
  });

  it('refuses card numbers, expiry, CVC, Stripe secrets and ids, and values the page holds, even if the trace carried them', () => {
    const events = normal();
    events.push(ev('redacted', { detail: 'card 4242 4242 4242 4242 exp 12/34 cvc 123' }),
      ev('note', { detail: 'sk_test_51Habc and pm_1PqRsTuV and tok_abcdefghijklmnop' }),
      ev('note', { reason: 'the reviewer said hunter2-reviewer-credential' }));
    const r = buildReport(source(events, CONFIG, { forbidden: ['hunter2-reviewer-credential', 'pm_notsent'] }));
    const json = JSON.stringify(r);
    for (const leak of ['4242 4242', '12/34', 'cvc 123', 'sk_test_', 'pm_1Pq', 'tok_abcdefghijklmnop', 'hunter2']) {
      expect(json).not.toContain(leak);
    }
    expect(r.security.export_redactions.length).toBe(3);
    expect(r.security.redacted_trace_steps).toBe(1);
  });

  it('keeps SuperLink ids, which are digits by shape, exactly as the node does', () => {
    const r = buildReport(source(collaborative, FLOWER));
    expect(r.execution.supernodes).toEqual(['11167642584137410316']);
    expect(sensitiveReason('4000000000000101')).toBe('card-number-like digits');
    expect(sensitiveReason('Risk points 6: review from 3, decline from 8')).toBeNull();
    expect(sensitiveReason('2026-09-29T17:00:00.000Z')).toBeNull();
    expect(sensitiveReason('pi_3QabcDEF456ghiJKL789')).toBeNull(); // a PaymentIntent id is not card data
  });
});

describe('decision report: PDF', () => {
  const cases = { normal, fraud, rogue, collaborative: () => collaborative, humanApproved };
  for (const [name, events] of Object.entries(cases)) {
    it(`renders the ${name} scenario as a readable, paginated PDF with no sensitive data`, () => {
      const config: Config['federation'] = name === 'collaborative' || name === 'humanApproved' ? 'local-agent' : null;
      const r = buildReport(source(events(), { ...CONFIG, federation: config }));
      const { text, pages } = pdfOf(r);
      expect(text.startsWith('%PDF-1.')).toBe(true);
      expect(text.trimEnd().endsWith('%%EOF')).toBe(true);
      expect(pages).toBeGreaterThanOrEqual(2);
      expect(text).toContain(`(Page ${pages} of ${pages})`);
      expect(text).toContain(`(${pdfText(r.verdict.label)})`);
      expect(text.includes('Round 2 ') ).toBe(r.execution.rounds.some((x) => x.round === 2));
      expect(text.includes('(Jev \\(TypeSafe\\))')).toBe(r.models.jev.used);
      expect(text.includes('(Flower Endeavor)')).toBe(r.models.endeavor.used);
      for (const leak of [/tok_/, /pm_/, /sk_test/, /pk_test/, /purchases_here/, /4242 ?4242/]) expect(text).not.toMatch(leak);
    });
  }

  it('wraps a long id inside its column instead of running off the page', () => {
    const longId = 'abcdefghijklmnop'.repeat(6);
    const r = buildReport({ ...source(normal()), traceId: longId });
    const doc = drawReport(new jsPDF({ unit: 'pt', format: 'letter', compress: false }), r);
    doc.setFontSize(10);
    const wrapped = doc.splitTextToSize(longId, 248) as string[];
    expect(wrapped.length).toBeGreaterThan(1);
    expect(wrapped.join('')).toBe(longId);
    expect(doc.output()).toContain(`(${wrapped[0]})`);
  });
});

describe('decision report: edge cases the node can emit', () => {
  it('counts off-vocabulary values as raw history shared and says so in the PDF; an unreported card count is n/a', () => {
    const r = buildReport(source(normal(), { ...CONFIG, wire_vocabulary: { amount_band: ['medium', 'high'] } }, { rawCardShared: null }));
    expect(r.security).toMatchObject({ off_vocabulary_values: 1, raw_card_history_shared: 1, raw_card_data_shared: null });
    const { text } = pdfOf(r);
    expect(text).toContain('(n/a)');
    expect(text).toMatch(/\(Attention: the counts above do not confirm/);
    expect(pdfOf(buildReport(source(normal()))).text).toContain('(Only privacy-safe attestations crossed node boundaries');
  });

  it('keeps a Luhn-valid SuperLink id under its own key and refuses the same digits anywhere else', () => {
    seq = collaborative.length;
    const events = [...collaborative.map((e) => (e.kind === 'flower.run.started' ? { ...e, run_id: '4242424242424242' } : e)),
      ev('note', { detail: '4242424242424242' }), ev('note', { detail: '4242_4242_4242_4242' })];
    const r = buildReport(source(events, FLOWER));
    expect(r.execution.run_id).toBe('4242424242424242');
    expect(r.trace.filter((e) => e.kind === 'note').map((e) => e.detail)).toEqual(['[redacted]', '[redacted]']);
    expect(sensitiveReason('4242_4242_4242_4242')).toBe('card-number-like digits');
  });

  it('the PDF is drawn from the scrubbed report, so injected secrets never reach it either', () => {
    const events = normal();
    events.push(ev('note', { detail: 'card 4242 4242 4242 4242 sk_test_51Habc hunter2-reviewer-credential' }));
    const r = buildReport(source(events, CONFIG, { forbidden: ['hunter2-reviewer-credential'] }));
    const { text } = pdfOf(r);
    for (const leak of ['4242 4242', 'sk_test', 'hunter2']) expect(text).not.toContain(leak);
    expect(text).toContain('redacted by the export');
  });

  it('an in-process round 2 whose answer never crossed the boundary is reported as unanswered', () => {
    seq = 0;
    const events = [
      ev('payment.started', { amount_cents: 6400, store: 'store-a', federation: null, attack: null }),
      ev('processor.verify.reply', { status: 'ok', evidence: { cvc_check: 'pass', card_funding: 'credit' } }),
      ev('bank.attest.reply', { status: 'ok', evidence: { issuer_behavior: 'low', issuer_recent_declines: 'none' } }),
      ev('store.facts', { evidence: { amount_band: 'low', country_mismatch: 'yes' } }),
      ev('store.disclosed', { status: 'verified', evidence: { amount_band: 'low', country_mismatch: 'yes', issuer_behavior: 'low' } }),
      ev('coord.network', { evidence: { network_velocity_band: 'low' }, stores: 1 }),
      ev('coord.conflict', { round: 2, reason: 'store: the buyer is outside the card\'s country; bank: an ordinary cardholder' }),
      ev('coord.question', { round: 2, purpose: 'travel-check', via: 'in-process' }),
      ev('bank.travel.request', { round: 2 }),
      ev('bank.travel.reply', { round: 2, status: 'ok', evidence: { travel_check: 'implausible' } }),
      ev('guard.blocked', { round: 2, status: 'rejected', reason: 'token rate limit' }),
      ev('coord.reply', { round: 2, via: 'in-process', status: 'error', evidence: null }),
      ev('gate.decision', { decision: 'step_up', decided_by: 'rules', score: 2, contributions: [], parties: [], jev: null,
        lines: lines([['score', 'info', 'Risk points 2: review from 3, decline from 8'],
          ['round_2', 'warn', 'Round 2 got no verified answer from the bank, so a person decides']]) }),
      ev('review.opened', { review_id: 'ab12cd34' }),
      ev('outcome', { outcome: 'needs_review', charged: false }),
    ];
    const r = buildReport(source(events));
    expect(r.execution.rounds.map((x) => x.round)).toEqual([1, 2]);
    expect(r.execution.rounds[1]).toMatchObject({ channel: 'in_process', summary: expect.stringMatching(/no verified answer/) });
    const { text } = pdfOf(r);
    expect(text).toContain('(No verified answer)');
    expect(text).not.toContain('travel_check = implausible');
  });

  it('with a second SuperNode, round 1 reports our verified reply, not the foreign one the coordinator refused', () => {
    const i = collaborative.findIndex((e) => e.kind === 'coord.reply');
    const foreign: TraceEvent = { seq: 900, t: 3000, kind: 'coord.reply', round: 1, status: 'rejected', node: '999', reason: 'not used', via: 'flower' };
    const events = [...collaborative.slice(0, i + 1), foreign, ...collaborative.slice(i + 1)];
    const r = buildReport(source(events, FLOWER));
    expect(r.execution.rounds[0]?.reply?.status).toBe('verified');
    expect(r.participants.find((p) => p.component === 'Merchant (store)')?.status).toBe('Disclosed');
    expect(r.security.rejected_messages).toHaveLength(1);
  });

  it('after a Flower fallback, round 1 reports the in-process disclosure the gate used, not the failed Grid reply', () => {
    const events = normal().map((e) => (e.kind === 'payment.started' ? { ...e, federation: 'local-agent' } : e));
    const i = events.findIndex((e) => e.kind === 'store.disclosed');
    const failed: TraceEvent[] = [
      { seq: 900, t: 100, kind: 'flower.run.started' },
      { seq: 901, t: 110, kind: 'coord.question', round: 1, purpose: 'fraud-risk', via: 'flower', nodes: ['5574125614239010036'] },
      { seq: 902, t: 120, kind: 'coord.reply', round: 1, status: 'error', via: 'flower', reason: 'no facts' },
      { seq: 903, t: 130, kind: 'fallback', detail: 'no verdict from the federation: decided on this node instead' },
    ];
    const r = buildReport(source([...events.slice(0, i), ...failed, ...events.slice(i)], FLOWER));
    expect(r.execution.rounds[0]).toMatchObject({ channel: 'in_process', reply: { status: 'verified' } });
    expect(r.execution.fallback).toBe(true);
    expect(r.participants.find((p) => p.component === 'Merchant (store)')?.status).toBe('Disclosed');
  });

  it('Jev unavailable is recorded as such and not listed as a participant', () => {
    const events = normal().map((e) => (e.kind === 'gate.decision' ? { ...e, jev: { unavailable: true as const } } : e));
    const r = buildReport(source(events));
    expect(r.models.jev).toMatchObject({ used: false, status: 'unavailable' });
    expect(r.participants.map((p) => p.component)).not.toContain('Jev (TypeSafe)');
  });

  it('is not offered between the gate and the outcome', () => {
    const events = normal();
    const gate = events.findIndex((e) => e.kind === 'gate.decision');
    expect(reportReady(derive(events.slice(0, gate + 1), 'in-process'))).toBe(false);
    expect(reportReady(derive(events.slice(0, -1), 'in-process'))).toBe(false); // settled, outcome not yet in
  });

  it('paginates a long trace inside the page, repeating the table header, and shortens a long id only in the running header', () => {
    const events = normal();
    const out = events.pop() as TraceEvent;
    for (let k = 0; k < 300; k++) events.push(ev('node.read', { status: 'disclosed', src: 'store' }));
    events.push({ ...out, seq: seq++ });
    const longId = 'abcdefghijklmnop'.repeat(12);
    const r = buildReport({ ...source(events), traceId: longId });
    const { text, pages } = pdfOf(r);
    expect(pages).toBeGreaterThanOrEqual(8);
    // page content streams in order: from the timeline's first page to the last, each page repeats its header once
    const perPage = text.split('endstream').filter((st) => st.includes(' Td')).map((st) => (st.match(/\(#\) Tj/g) ?? []).length);
    expect(perPage).toHaveLength(pages);
    const first = perPage.indexOf(1);
    expect(first).toBeGreaterThan(0);
    expect(perPage.slice(first).every((n) => n === 1)).toBe(true);
    expect(pages - first).toBeGreaterThanOrEqual(6); // 300 rows really span several pages
    expect(text).toContain(`(${longId.slice(0, 16)}...${longId.slice(-8)})`);
    for (const [, x, y] of text.matchAll(/(-?[\d.]+) (-?[\d.]+) Td/g)) {
      expect(Number(x)).toBeGreaterThanOrEqual(0);
      expect(Number(x)).toBeLessThanOrEqual(612);
      expect(Number(y)).toBeGreaterThanOrEqual(20);
      expect(Number(y)).toBeLessThanOrEqual(792);
    }
  });
});

describe('decision report: PDF pagination', () => {
  // Each page's drawn strings with their font size, in order (uncompressed PDF content streams).
  const pageTexts = (text: string) => text.split('endstream').filter((st) => st.includes(' Td'))
    .map((st) => [...st.matchAll(/\/F\d+ ([\d.]+) Tf[\s\S]*?\((.*?)\) Tj/g)].map((m) => ({ size: Number(m[1]), text: m[2] ?? '' })));

  it('never ends a page on a section heading, wherever the sections happen to fall', () => {
    for (let words = 0; words <= 160; words += 4) {
      const events = normal().map((e) => (e.kind === 'gate.decision'
        ? { ...e, explanation: { text: `Approved.${' Ordinary activity reported by every party'.repeat(words / 4)}`, by: 'template', via: '', error: '' } }
        : e));
      for (const [i, texts] of pageTexts(pdfOf(buildReport(source(events))).text).entries()) {
        const heading = texts.map((t) => t.size).lastIndexOf(12.5); // section titles are the only 12.5 pt text
        if (heading < 0) continue;
        // after the last heading on the page: its body, not just its kicker, the running header or the footer
        const body = texts.slice(heading + 1).filter((t) => ![8, 7.5, 8.5].includes(t.size));
        expect(body.length, `page ${i + 1} ends on "${texts[heading]?.text}" (explanation ${words} words)`).toBeGreaterThan(0);
      }
    }
  });
});
