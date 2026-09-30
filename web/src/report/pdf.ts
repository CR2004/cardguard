// The human-readable report: a paginated PDF drawn from the DecisionReport alone (never from the page), so it
// holds nothing the JSON does not. jsPDF's built-in Helvetica keeps it small, and the library is loaded only
// when someone exports.
import type { jsPDF } from 'jspdf';
import type { GateLine } from '../api/types';
import { formatMs, pretty } from '../format';
import { factLabel } from '../investigation/derive';
import { outcomeWord, PARTY_NAME, type DecisionReport } from './report';

export async function renderPdf(report: DecisionReport): Promise<Blob> {
  const { jsPDF } = await import('jspdf');
  return drawReport(new jsPDF({ unit: 'pt', format: 'letter', compress: true }), report).output('blob');
}

/** jsPDF's built-in fonts are WinAnsi: typographic punctuation is mapped to plain text, anything else to '?'. */
export function pdfText(s: string): string {
  return s
    .replace(/\u200b/g, '')
    .replace(/\s+/g, ' ')
    .replace(/[‘’‛]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/…/g, '...')
    .replace(/[‒-―]/g, '-')
    .replace(/→/g, '->')
    .replace(/•/g, '·')
    .replace(/[^\x20-\x7e¡-ÿ]/g, '?');
}

const W = 612;
const H = 792;
const M = 48;
const CW = W - 2 * M;
const TOP = 66; // content top on continuation pages, below the running header
const BOTTOM = H - 56; // content bottom, above the footer
const LH = 1.3;

// The page's own palette, darkened where needed to read on white paper.
const C = {
  ink: '#1a2033', ink2: '#4b5570', ink3: '#848da3', rule: '#e1e5ee', panel: '#f4f6fa',
  navy: '#0d1328', vellum: '#d9c9a3', vellumInk: '#8e7c55', mint: '#1b8a5a', mute: '#b8c0d8',
};
const TONE: Record<string, [ink: string, tint: string]> = {
  approve: ['#1b8a5a', '#e8f6ef'], step_up: ['#a86200', '#fdf2df'], decline: ['#c0392b', '#fbe9e7'],
  stopped: ['#c0392b', '#fbe9e7'], pending: ['#4b5570', '#eef0f6'],
};
const STATE: Record<GateLine['state'], [label: string, ink: string, tint: string]> = {
  pass: ['PASS', '#1b8a5a', '#e8f6ef'], warn: ['CAUTION', '#a86200', '#fdf2df'],
  fail: ['FAIL', '#c0392b', '#fbe9e7'], info: ['NOTE', '#4b5570', '#eef0f6'],
};

type Style = 'normal' | 'bold' | 'italic';
type Cell = string | { text: string; color?: string; bold?: boolean };

const stamp = (iso: string | null) => (iso ? new Date(iso).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'long' }) : '—');
const points = (n: number) => `${n} risk point${n === 1 ? '' : 's'}`;
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
/** Running header and footer are one line: a long id is shortened there; the body always prints it whole. */
const shortId = (id: string) => (id.length > 28 ? `${id.slice(0, 16)}...${id.slice(-8)}` : id);

export function drawReport(doc: jsPDF, r: DecisionReport): jsPDF {
  let y = 0;
  doc.setLineHeightFactor(LH);
  doc.setProperties({
    title: `CardGuard decision ${r.decision_id}`, subject: 'CardGuard decision report', author: 'CardGuard', creator: 'CardGuard',
  });

  // ------------------------------------------------------------ primitives
  const font = (size: number, style: Style = 'normal', color = C.ink) => {
    doc.setFont('helvetica', style);
    doc.setFontSize(size);
    doc.setTextColor(color);
  };
  const wrap = (text: string, width: number): string[] => doc.splitTextToSize(pdfText(text || '—'), width) as string[];
  const tall = (lines: number, size: number) => lines * size * LH;
  const put = (text: string | string[], x: number, top: number, opts: { align?: 'left' | 'right' | 'center'; charSpace?: number } = {}) =>
    doc.text(typeof text === 'string' ? pdfText(text) : text, x, top, { baseline: 'top', ...opts });
  const room = (h: number) => {
    if (y + h > BOTTOM) {
      doc.addPage();
      y = TOP;
    }
  };
  const rule = (at: number, color = C.rule, width = 0.6) => {
    doc.setDrawColor(color);
    doc.setLineWidth(width);
    doc.line(M, at, W - M, at);
  };

  const section = (title: string, kicker?: string) => {
    room(41 + 72); // the heading (41 pt) never ends a page: room for it and its first block (tiles, table rows, a kv row)
    y += 10;
    font(12.5, 'bold');
    put(title, M, y);
    if (kicker) {
      font(8, 'normal', C.ink3);
      put(kicker, W - M, y + 3.5, { align: 'right' });
    }
    y += 20;
    rule(y, C.rule, 0.8);
    y += 11;
  };

  const para = (text: string, size = 10, color = C.ink, style: Style = 'normal', indent = 0) => {
    font(size, style, color);
    for (const line of wrap(text, CW - indent)) {
      room(tall(1, size));
      put(line, M + indent, y);
      y += tall(1, size);
    }
    y += 5;
  };

  const kv = (pairs: [string, string][], cols = 2) => {
    const gap = 20;
    const w = (CW - gap * (cols - 1)) / cols;
    for (let i = 0; i < pairs.length; i += cols) {
      const row = pairs.slice(i, i + cols);
      font(10);
      const cells = row.map(([, v]) => wrap(v, w));
      const h = 12 + Math.max(...cells.map((c) => tall(c.length, 10)));
      room(h + 10);
      row.forEach(([k], j) => {
        const x = M + j * (w + gap);
        font(7, 'bold', C.ink3);
        put(k.toUpperCase(), x, y, { charSpace: 0.6 });
        font(10, 'normal', C.ink);
        put(cells[j] ?? [], x, y + 11.5);
      });
      y += h + 10;
    }
  };

  const table = (cols: { title: string; w: number }[], rows: Cell[][], size = 9) => {
    const pad = size < 8 ? 4 : 6; // the dense appendix timeline gets tighter rows
    const header = () => {
      doc.setFillColor(C.panel);
      doc.rect(M, y, CW, 18, 'F');
      font(6.8, 'bold', C.ink2);
      let x = M;
      for (const c of cols) {
        put(c.title.toUpperCase(), x + pad, y + 6, { charSpace: 0.5 });
        x += c.w;
      }
      y += 18;
    };
    room(18 + tall(2, size) + 2 * pad);
    header();
    for (const row of rows) {
      const cells = row.map((cell, j) => {
        const c = typeof cell === 'string' ? { text: cell } : cell;
        font(size, c.bold ? 'bold' : 'normal');
        return { ...c, lines: wrap(c.text, (cols[j]?.w ?? CW) - 2 * pad) };
      });
      const h = Math.max(...cells.map((c) => tall(c.lines.length, size))) + 2 * pad - 2;
      if (y + h > BOTTOM) {
        doc.addPage();
        y = TOP;
        header();
      }
      let x = M;
      cells.forEach((c, j) => {
        font(size, c.bold ? 'bold' : 'normal', c.color ?? C.ink);
        put(c.lines, x + pad, y + pad - 1);
        x += cols[j]?.w ?? 0;
      });
      y += h;
      rule(y, C.rule, 0.5);
    }
    y += 12;
  };

  const badge = (label: string, x: number, top: number, ink: string, tint: string, w = 54) => {
    doc.setFillColor(tint);
    doc.roundedRect(x, top, w, 13, 3, 3, 'F');
    font(6.5, 'bold', ink);
    put(label, x + w / 2, top + 3.4, { align: 'center' });
  };

  const tiles = (items: { value: string; label: string; color: string }[]) => {
    const gap = 10;
    const w = (CW - gap * (items.length - 1)) / items.length;
    const h = 58;
    room(h + 12);
    items.forEach((t, i) => {
      const x = M + i * (w + gap);
      doc.setFillColor(C.panel);
      doc.rect(x, y, w, h, 'F');
      doc.setFillColor(t.color);
      doc.rect(x, y, w, 2.5, 'F');
      font(21, 'bold', t.color);
      put(t.value, x + 12, y + 11);
      font(8.5, 'normal', C.ink2);
      put(wrap(t.label, w - 24), x + 12, y + 38);
    });
    y += h + 12;
  };

  // ------------------------------------------------------------ page 1 header
  doc.setFillColor(C.navy);
  doc.rect(0, 0, W, 96, 'F');
  doc.setFillColor(C.vellum);
  doc.rect(0, 96, W, 2.5, 'F');
  doc.setDrawColor(C.vellum);
  doc.setLineWidth(0.9);
  doc.circle(M + 14, 47, 14, 'S');
  doc.setLineWidth(0.5);
  doc.circle(M + 14, 47, 9, 'S');
  doc.circle(M + 14, 47, 4, 'S');
  font(22, 'bold', '#ffffff');
  put('CardGuard', M + 38, 29);
  font(10, 'normal', C.vellum);
  put('Decision report', M + 38, 56);
  const pill = r.environment.stripe_mode === 'test' ? 'STRIPE TEST MODE' : 'STRIPE MODE UNKNOWN';
  font(7.5, 'bold', C.vellum);
  const pw = doc.getTextWidth(pill) + 18;
  doc.setDrawColor(C.vellum);
  doc.setLineWidth(0.8);
  doc.roundedRect(W - M - pw, 28, pw, 17, 8.5, 8.5, 'S');
  put(pill, W - M - pw / 2, 32.8, { align: 'center' });
  font(8.5, 'normal', C.mute);
  put(`Generated ${stamp(r.created_at)}`, W - M, 57, { align: 'right' });
  y = 122;

  // ------------------------------------------------------------ verdict
  const [ink, tint] = TONE[r.verdict.final] ?? TONE.pending ?? ['#4b5570', '#eef0f6'];
  const leftW = CW - 230;
  const rx = M + CW - 196;
  const rw = 176;
  font(10);
  const by = wrap(r.verdict.decided_by, leftW);
  font(8.5);
  const scoreNote = r.verdict.risk_score === null
    ? wrap(r.verdict.hard_stop ? 'CVC check failed: automatic decline, no vote.' : 'Nothing reached the policy gate.', rw)
    : [];
  const blockH = Math.max(72 + tall(by.length, 10), 58 + tall(scoreNote.length, 8.5), 84) + 16;
  room(blockH);
  doc.setFillColor(tint);
  doc.rect(M, y, CW, blockH, 'F');
  doc.setFillColor(ink);
  doc.rect(M, y, 4, blockH, 'F');
  font(7.5, 'bold', C.ink2);
  put(r.verdict.human_final ? 'DECIDED BY A PERSON' : 'CARDGUARD DECISION', M + 20, y + 16, { charSpace: 0.8 });
  font(30, 'bold', ink);
  put(r.verdict.label, M + 20, y + 30);
  font(10, 'normal', C.ink2);
  put(by, M + 20, y + 72);
  font(7.5, 'bold', C.ink2);
  put('RISK SCORE', rx, y + 16, { charSpace: 0.8 });
  if (r.verdict.risk_score !== null) {
    font(16, 'bold', C.ink);
    put(points(r.verdict.risk_score), rx, y + 30);
    const cut = r.verdict.thresholds;
    if (cut) {
      // the same scale as the page's meter: review and decline lines from the gate's own score line
      const max = Math.max(cut.decline + 4, r.verdict.risk_score + 1);
      const at = (n: number) => rx + Math.min(1, n / max) * rw;
      const my = y + 60;
      doc.setFillColor('#cdeedd');
      doc.rect(rx, my, at(cut.review) - rx, 6, 'F');
      doc.setFillColor('#fbe2b5');
      doc.rect(at(cut.review), my, at(cut.decline) - at(cut.review), 6, 'F');
      doc.setFillColor('#f6cbc6');
      doc.rect(at(cut.decline), my, rx + rw - at(cut.decline), 6, 'F');
      doc.setFillColor(C.navy);
      doc.rect(at(r.verdict.risk_score) - 1.5, my - 4, 3, 14, 'F');
      font(7, 'normal', C.ink2);
      put(`Review ${cut.review}`, at(cut.review), my + 13, { align: 'center' });
      put(`Decline ${cut.decline}`, at(cut.decline), my + 13, { align: 'center' });
    }
  } else {
    font(15, 'bold', C.ink);
    put(r.verdict.hard_stop ? 'Hard stop' : 'Not scored', rx, y + 30);
    font(8.5, 'normal', C.ink2);
    put(scoreNote, rx, y + 52);
  }
  y += blockH + 18;

  // ------------------------------------------------------------ key facts
  const t = r.transaction;
  const hr = r.human_review;
  const ex = r.execution;
  kv([
    ['Decision ID', r.decision_id],
    ['Amount', t.amount ? `${t.amount} ${t.currency}` : '—'],
    ['Merchant / store', t.store && t.store !== r.environment.merchant_id ? `${t.store} (merchant node ${r.environment.merchant_id})` : t.store ?? r.environment.merchant_id],
    ['Buyer', [t.buyer_country && `Country ${t.buyer_country}`, t.buyer_region && `region ${t.buyer_region}`].filter(Boolean).join(', ') || '—'],
    ['Human reviewer', hr.decision ? `Made the final decision: ${hr.decision === 'approve' ? 'approved' : 'declined'}`
      : hr.required ? 'Required: a person must decide'
        : r.policy_gate.reached ? 'Not required: the policy gate decided' : 'Not required: stopped before the policy gate'],
    ['Stripe payment (TEST)', `${r.payment.state} · ${r.payment.note}`],
    ['Execution', ex.flower_used && ex.verdict_binding === 'accepted' ? `Flower AgentApp on ${ex.federation}`
      : ex.flower_used ? 'Flower run requested; decided on the store node' : 'In-process on the store node'],
    ['Card issuing region', t.card_issuing_region ?? '—'],
  ]);

  // ------------------------------------------------------------ why
  section('Why this decision');
  if (r.verdict.explanation) {
    font(10.5, 'italic', C.ink);
    const lines = wrap(`"${r.verdict.explanation.text}"`, CW - 16);
    room(tall(lines.length, 10.5) + 26);
    doc.setFillColor(C.vellum);
    doc.rect(M, y, 3, tall(lines.length, 10.5), 'F');
    put(lines, M + 14, y);
    y += tall(lines.length, 10.5) + 6;
    const author = r.verdict.explanation.kind === 'template' ? `the ${r.verdict.explanation.author.toLowerCase()}` : r.verdict.explanation.author;
    para(`Written by ${author} after the verdict. It explains; it cannot change the decision.`, 8.5, C.ink3, 'normal', 14);
    if (r.verdict.human_final) para(`A person then ${hr.decision === 'approve' ? 'approved' : 'declined'} it.`, 10, C.ink, 'bold');
  } else {
    para(r.verdict.summary, 10.5);
  }

  // ------------------------------------------------------------ participants
  section('Who took part', 'the policy gate is always the final authority');
  table([{ title: 'Component', w: 132 }, { title: 'Role in this decision', w: 282 }, { title: 'Status', w: 102 }],
    r.participants.map((p) => [{ text: p.component, bold: true }, p.role, p.status]));

  // ------------------------------------------------------------ rounds
  const order = ['stripe', 'bank', 'store', 'network'];
  const factRows = (facts: DecisionReport['evidence']['facts']) => [...facts]
    .sort((a, b) => order.indexOf(a.party) - order.indexOf(b.party))
    .map((f): Cell[] => [
      PARTY_NAME[f.party] ?? f.party, f.label, { text: pretty(f.value), bold: true },
      `${f.status === 'verified' ? 'Verified' : f.status === 'attested' ? 'Attested' : 'Rejected'}${f.conflict ? ' · conflict' : ''}`,
      f.risk_points ? { text: `+${f.risk_points}`, color: TONE.step_up?.[0] } : '—',
    ]);
  const factCols = [{ title: 'Party', w: 116 }, { title: 'Evidence', w: 142 }, { title: 'Band', w: 100 }, { title: 'Status', w: 94 }, { title: 'Risk pts', w: 64 }];
  for (const round of ex.rounds) {
    const via = round.channel === 'flower_grid' ? 'over Flower Grid' : 'in-process';
    if (round.round === 1) {
      section('Round 1 · evidence from every party', via);
      para(round.summary);
      para(`Asked: ${round.asked.join(', ') || '—'}.`, 8.5, C.ink2);
      if (round.facts.length) table(factCols, factRows(round.facts));
    } else {
      section('Round 2 · one targeted question', via);
      para(round.summary);
      kv([
        ['Why it was asked', round.trigger ?? '—'],
        ['Asked', `${round.asked.join(', ')}: travel_check only`],
        ['Answer', round.facts.filter((f) => f.status === 'verified').map((f) => `${f.fact} = ${f.value}`).join(', ') || 'No verified answer'],
        ['Effect on the decision', round.effect ?? '—'],
      ]);
    }
  }

  // ------------------------------------------------------------ policy gate
  const g = r.policy_gate;
  section('Policy gate · final authority', 'fixed rules, no model');
  if (g.reached) {
    for (const l of g.rules) {
      const [label, lInk, lTint] = STATE[l.state];
      font(9.5);
      const lines = wrap(l.text, CW - 68);
      const h = tall(lines.length, 9.5) + 8;
      room(h);
      badge(label, M, y + 0.5, lInk, lTint);
      font(9.5, l.state === 'warn' || l.state === 'fail' ? 'bold' : 'normal', C.ink);
      put(lines, M + 66, y + 1.5);
      y += h;
    }
    y += 4;
    const scored = g.contributions.filter((c) => c.points > 0);
    if (scored.length) {
      const named = scored.map((c) => {
        const [key = '', value = ''] = c.fact.split('=');
        return `${factLabel(key)} ${pretty(value)} (+${c.points})`;
      });
      para(`Risk points by fact: ${named.join(', ')}.`, 9, C.ink2);
    }
    para(`Gate decision: ${g.label} (${g.decided_by === 'rules+jev' ? 'fixed rules, then the more cautious model vote' : 'fixed rules'}).`, 9.5, C.ink, 'bold');
  } else {
    para(g.stopped_reason ?? 'The policy gate was not reached.', 10, C.ink, 'bold');
  }

  // ------------------------------------------------------------ rejected
  const s = r.security;
  if (s.rejected_messages.length || s.redacted_trace_steps) {
    section('Rejected at a boundary', 'none of these entered the evidence');
    if (s.rejected_messages.length) {
      table([{ title: 'Step', w: 44 }, { title: 'Message', w: 136 }, { title: 'Stopped at', w: 86 }, { title: 'Reason', w: 250 }],
        s.rejected_messages.map((m) => [`#${m.seq}`, `${m.from} -> ${m.to}`,
          m.stopped_at === 'sender' ? 'Sender (wire guard)' : 'Receiver', { text: m.reason_plain, bold: true }]));
    }
    if (s.redacted_trace_steps) para(`${plural(s.redacted_trace_steps, 'trace step')} held a card-like value and was redacted on the node.`, 9, C.ink2);
  }

  // ------------------------------------------------------------ human review
  if (hr.required) {
    section('Human review');
    kv([
      ['Why it was held', g.fired.map((l) => l.text).join('. ') || 'The policy gate would not decide alone'],
      ['Outcome', hr.decision ? `${hr.decision === 'approve' ? 'Approved' : 'Declined'} by a person: final` : 'Pending: nothing is charged until a person decides'],
      ['Review ID', hr.review_id ?? '—'],
      ['Decided', hr.decided_at_ms !== null ? `${formatMs(hr.decided_at_ms)} after the checkout started` : '—'],
    ]);
  }

  // ------------------------------------------------------------ payment
  const p = r.payment;
  section('Payment · Stripe', p.mode === 'test' ? 'TEST mode: no real money moves' : undefined);
  kv([
    ['Final state', p.state],
    ['Detail', p.note],
    ['Charged', p.charged ? 'Yes' : 'No'],
    ['CardGuard outcome', outcomeWord(p.outcome)],
    ['PaymentIntent', p.payment_intent_id ?? '—'],
    ['Processor', `Stripe (${p.mode === 'test' ? 'TEST mode' : 'mode unknown'})`],
  ]);

  // ------------------------------------------------------------ privacy
  section('Privacy', 'card data never enters a model’s context');
  tiles([
    { value: s.raw_card_data_shared === null ? 'n/a' : String(s.raw_card_data_shared), label: 'Raw card data shared',
      color: s.raw_card_data_shared === null ? C.ink3 : s.raw_card_data_shared === 0 ? C.mint : '#c0392b' },
    { value: String(s.raw_card_history_shared), label: 'Raw card history shared', color: s.raw_card_history_shared === 0 ? C.mint : '#c0392b' },
    { value: String(s.privacy_safe_facts_crossed), label: 'Privacy-safe facts crossed a boundary', color: C.ink },
  ]);
  const clean = s.raw_card_data_shared === 0 && s.raw_card_history_shared === 0;
  para(`${clean ? 'Only privacy-safe attestations crossed node boundaries: banded values from a closed vocabulary'
    : 'Attention: the counts above do not confirm that only privacy-safe attestations crossed node boundaries'} (${s.bytes_across_boundaries} bytes in all, `
    + `${plural(s.off_vocabulary_values, 'off-vocabulary value')}, ${plural(s.rejected_messages.length, 'message')} rejected at a boundary). `
    + 'The card goes from Stripe Elements to Stripe; the merchant holds only a Stripe payment-method id, and the bank’s history never leaves the bank.',
  9.5, clean ? C.ink : '#c0392b');
  para(`Raw card data counts ${s.raw_card_data_basis}${s.raw_card_data_shared === null ? ' (not reported for this checkout)' : ''}. `
    + `Raw card history counts ${s.raw_card_history_basis}. This report omits card numbers, CVC, expiry, keys, credentials, `
    + 'the card reference and the parties’ private history.', 8, C.ink3);

  // ------------------------------------------------------------ Flower
  if (ex.flower_used) {
    section('Flower execution', ex.federation ?? undefined);
    kv([
      ['Federation', ex.federation ?? '—'],
      ['Run ID', ex.run_id ?? '—'],
      ['SuperNodes', ex.supernodes.length ? `${ex.supernodes.length}: ${ex.supernodes.join(', ')}` : '—'],
      ['Grid messages', String(ex.grid_messages)],
      ['Verdict binding', ex.verdict_binding === 'accepted' ? 'Accepted: bound to the SuperNode that read the facts'
        : ex.verdict_binding === 'ignored' ? 'Ignored: it could not be bound to this decision' : '—'],
      ['Fallback', ex.fallback ? 'Yes: the store node decided in its own process' : 'No'],
    ]);
  }

  // ------------------------------------------------------------ appendix
  section('Technical appendix');
  kv([
    ['Report version', r.report_version],
    ['Decision ID (trace)', r.decision_id],
    ['Flower run ID', ex.run_id ?? '—'],
    ['Review ID', hr.review_id ?? '—'],
    ['Checkout sent (browser clock)', stamp(r.checkout_sent_at)],
    ['Report generated', stamp(r.created_at)],
    ['Time on the nodes', ex.duration_ms !== null ? formatMs(ex.duration_ms) : '—'],
    ['Trace steps', `${r.trace.length} (full record in the JSON export)`],
  ]);
  table([{ title: '#', w: 30 }, { title: 't', w: 56 }, { title: 'Step', w: 150 }, { title: 'Route', w: 120 }, { title: 'Result', w: 160 }],
    r.trace.map((e) => [
      String(e.seq), formatMs(e.t), e.kind, [e.src, e.dst].filter(Boolean).join(' -> ') || '—',
      [e.status, e.decision && pretty(e.decision), e.outcome && pretty(e.outcome), e.round ? `round ${e.round}` : null]
        .filter(Boolean).join(' · ') || '—',
    ]), 7.5);
  if (s.export_redactions.length) para(`${plural(s.export_redactions.length, 'value')} redacted by the export's own privacy scan.`, 8.5, C.ink2);

  // ------------------------------------------------------------ running header and footer
  const pages = doc.getNumberOfPages();
  for (let i = 1; i <= pages; i++) {
    doc.setPage(i);
    if (i > 1) {
      font(8.5, 'bold', C.ink);
      put('CardGuard', M, 30);
      const bw = doc.getTextWidth('CardGuard');
      font(8.5, 'normal', C.ink3);
      put('  ·  Decision report', M + bw, 30);
      put(shortId(r.decision_id), W - M, 30, { align: 'right' });
      rule(45, C.vellum, 0.8);
    }
    rule(H - 40);
    font(7.5, 'normal', C.ink3);
    put(`CardGuard decision report · ${shortId(r.decision_id)}${r.environment.stripe_mode === 'test' ? ' · Stripe TEST data' : ''}`, M, H - 32);
    put(`Page ${i} of ${pages}`, W - M, H - 32, { align: 'right' });
  }
  return doc;
}
