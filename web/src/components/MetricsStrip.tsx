import type { Metrics } from '../investigation/derive';
import { formatBytes } from '../format';

interface Item { label: string; value: string; tone?: 'zero' | 'bad' | 'flower'; title: string }

/** The privacy proof for this investigation, in the few numbers a viewer can check at a glance. */
export function MetricsStrip({ metrics, flower }: { metrics: Metrics; flower: boolean }) {
  const items: Item[] = [
    { label: 'card numbers shared', value: metrics.rawCardShared === null ? '—' : String(metrics.rawCardShared),
      tone: metrics.rawCardShared === 0 ? 'zero' : metrics.rawCardShared ? 'bad' : undefined,
      title: 'Card-like values in anything this node ever disclosed: a re-scan of its hash-chained ledger' },
    { label: 'raw histories shared', value: String(metrics.offVocabulary), tone: metrics.offVocabulary ? 'bad' : 'zero',
      title: 'Values that crossed a boundary outside the closed band vocabulary, re-checked in this browser' },
    { label: 'verified facts', value: String(metrics.verifiedEvidence), title: 'Banded facts the coordinator re-verified on arrival' },
    metrics.rejectedMessages
      ? { label: 'stopped at a boundary', value: String(metrics.rejectedMessages), tone: 'bad',
        title: 'Messages stopped at a privacy or identity boundary' }
      : flower
        ? { label: 'Flower messages', value: String(metrics.flowerMessages), tone: 'flower',
          title: 'Questions and replies carried by Flower Grid in this decision' }
        : { label: 'moved in total', value: formatBytes(metrics.bytes),
          title: 'Bytes that crossed a node boundary: the payment-method id, questions and banded replies' },
  ];
  return (
    <ul className="proof" aria-label="Privacy proof for this investigation">
      {items.map((m) => (
        <li key={m.label} className={`proof__item${m.tone ? ` proof__item--${m.tone}` : ''}`} title={m.title}>
          <b>{m.value}</b> <span>{m.label}</span>
        </li>
      ))}
    </ul>
  );
}
