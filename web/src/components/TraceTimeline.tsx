import { useState } from 'react';
import { ChevronDown, ChevronUp, ListTree } from 'lucide-react';
import type { TraceEvent } from '../api/types';
import { formatMs, pretty } from '../format';

const KIND: Record<string, string> = {
  'payment.started': 'Checkout started', 'processor.verify.request': 'Card checks asked of Stripe',
  'processor.verify.reply': 'Stripe answered', 'bank.attest.request': 'Attestation asked of bank', 'bank.attest.reply': 'Bank attested',
  'store.facts': 'Store banded its facts', 'guard.blocked': 'Wire guard blocked', 'flower.run.request': 'Flower run requested',
  'flower.run.started': 'Flower run started', 'coord.nodes': 'SuperNodes found', 'coord.question': 'Question sent',
  'node.read': 'SuperNode read facts', 'coord.reply': 'Reply received', 'store.disclosed': 'Facts disclosed',
  'coord.network': 'Network memory checked', 'coord.conflict': 'Evidence conflict', 'bank.travel.request': 'Travel question',
  'bank.travel.reply': 'Bank travel answer', 'gate.decision': 'Policy gate decided', 'verdict.received': 'Verdict bound to node',
  fallback: 'Fell back to in-process', 'flower.run.finished': 'Flower run finished', 'review.opened': 'Held for a person',
  'review.decided': 'Person decided', 'payment.settled': 'Payment settled', outcome: 'Outcome', redacted: 'Step redacted',
};

type Ch = 'grid' | 'signed' | 'state' | 'stripe' | 'code' | 'review' | 'inprocess' | '';

function channelOf(e: TraceEvent): Ch {
  if (e.kind.startsWith('bank.')) return 'signed';
  if (e.kind === 'payment.settled' || e.kind.startsWith('processor.')) return 'stripe';
  if (e.kind === 'coord.question' || e.kind === 'coord.reply' || e.kind === 'verdict.received') return e.via === 'flower' ? 'grid' : 'inprocess';
  if (e.kind === 'store.disclosed') return 'inprocess';
  if (e.kind === 'coord.network') return 'state';
  if (e.kind === 'payment.started') return 'stripe';
  if (e.kind === 'gate.decision') return 'code';
  if (e.kind === 'review.opened') return 'review';
  return '';
}

const CH_NAME: Record<Ch, string> = { grid: 'Flower Grid', signed: 'signed', state: 'state', stripe: 'Stripe', code: 'code',
  review: 'review', inprocess: 'in-process', '': '' };

function detail(e: TraceEvent): string {
  if (e.evidence) return Object.entries(e.evidence).filter(([k]) => k !== 'token').map(([k, v]) => `${pretty(k)}=${v}`).join('  ');
  if (e.lines) return `${pretty(e.decision ?? '')} · ${e.decided_by ?? ''}`;
  if (e.kind === 'coord.nodes') return `${e.nodes?.length ?? 0} node(s)`;
  if (e.run_id) return `Flower run ${e.run_id}`;
  if (e.kind === 'payment.settled') return `${e.processor}: ${pretty(e.status ?? '')}${e.reason ? ` (${e.reason})` : ''}`;
  if (e.kind === 'outcome') return `${pretty(e.outcome ?? '')}${e.charged ? ', charged' : ', not charged'}`;
  if (e.kind === 'review.decided') return e.action ?? '';
  return [e.purpose, e.status, e.reason ?? e.detail].filter(Boolean).join(' · ');
}

const bad = (e: TraceEvent) => e.status === 'rejected' || e.status === 'blocked' || e.kind === 'guard.blocked' || e.status === 'ignored';

export function TraceTimeline({ events, applied }: { events: TraceEvent[]; applied: number }) {
  const [open, setOpen] = useState(false);
  const maxT = Math.max(1, ...events.map((e) => e.t));
  const last = events[applied - 1];
  return (
    <section className="timeline" aria-label="Technical timeline">
      <div className="timeline__bar">
        <button type="button" className="timeline__toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <ListTree size={14} aria-hidden /> Trace {open ? <ChevronDown size={14} aria-hidden /> : <ChevronUp size={14} aria-hidden />}
        </button>
        <span className="timeline__last">
          {last ? <>{formatMs(last.t)} · {KIND[last.kind] ?? last.kind}{last.src ? ` · ${last.src}${last.dst ? ` → ${last.dst}` : ''}` : ''} · {detail(last)}</>
            : 'Technical trace: every step the nodes report, with its real time since checkout.'}
        </span>
        <span className="timeline__count">{applied} of {events.length} events</span>
      </div>
      {open && (
        <div className="timeline__body">
          {events.map((e, i) => {
            const ch = channelOf(e);
            const milestone = (e.kind === 'coord.question') || e.kind === 'coord.conflict' || e.kind === 'gate.decision';
            return (
              <div key={e.seq} className={`tl-row${i >= applied ? ' is-pending' : ''}`}>
                <span className="tl-row__t mono">{formatMs(e.t)}</span>
                <span className="tl-row__kind" style={milestone ? { fontWeight: 620 } : undefined}>
                  {KIND[e.kind] ?? e.kind}{e.round ? ` · R${e.round}` : ''}
                </span>
                <span className="tl-row__route">
                  {e.src ?? ''}{e.dst ? ` → ${e.dst}` : ''}
                  {ch && <span className={`channel-tag channel-tag--${ch}`}>{CH_NAME[ch]}</span>}
                </span>
                <span className="tl-row__detail" title={detail(e)}>{detail(e)}{e.latency_ms !== undefined ? ` · ${e.latency_ms} ms` : ''}</span>
                <span className="tl-row__bar" aria-hidden>
                  <i className={bad(e) ? 'bad' : ch === 'grid' ? 'grid' : ch === 'signed' ? 'signed' : e.status === 'verified' ? 'ok' : ''}
                    style={{ left: `${(e.t / maxT) * 96}%`, width: e.latency_ms ? `${Math.max(1, (e.latency_ms / maxT) * 96)}%` : undefined,
                      transform: e.latency_ms ? `translateX(-100%)` : undefined }} />
                </span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
