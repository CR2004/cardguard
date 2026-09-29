import type { Metrics } from '../investigation/derive';
import { formatBytes } from '../format';

interface Item { label: string; value: string; tone?: 'zero' | 'bad' | 'flower'; title: string }

export function MetricsStrip({ metrics, flower }: { metrics: Metrics; flower: boolean }) {
  const items: Item[] = [
    { label: 'Raw card data shared', value: metrics.rawCardShared === null ? '—' : String(metrics.rawCardShared),
      tone: metrics.rawCardShared === 0 ? 'zero' : metrics.rawCardShared ? 'bad' : undefined,
      title: 'Card-like values in anything this node ever disclosed: a re-scan of its hash-chained ledger' },
    { label: 'Raw histories shared', value: String(metrics.offVocabulary), tone: metrics.offVocabulary ? 'bad' : 'zero',
      title: 'Values that crossed a boundary outside the closed band vocabulary, re-checked in this browser' },
    { label: 'Flower messages', value: flower ? String(metrics.flowerMessages) : '—', tone: flower ? 'flower' : undefined,
      title: flower ? 'Questions and replies carried by Flower Grid in this decision' : 'No Flower run: this node decides in-process' },
    { label: 'Verified evidence', value: String(metrics.verifiedEvidence), title: 'Banded facts the coordinator re-verified on arrival' },
    { label: 'Rejected messages', value: String(metrics.rejectedMessages), tone: metrics.rejectedMessages ? 'bad' : undefined,
      title: 'Messages stopped at a privacy or identity boundary' },
    { label: 'Data exchanged', value: formatBytes(metrics.bytes), title: 'Bytes that crossed a node boundary: the payment-method id, questions and banded replies' },
  ];
  return (
    <div className="metrics" aria-label="Privacy metrics for this investigation">
      {items.map((m) => (
        <div key={m.label} className={`metric${m.tone ? ` metric--${m.tone}` : ''}`} title={m.title}>
          <div className="metric__value">{m.value}</div>
          <div className="metric__label">{m.label}</div>
        </div>
      ))}
    </div>
  );
}
