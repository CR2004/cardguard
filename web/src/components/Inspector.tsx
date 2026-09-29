import { Bot, ChevronDown, CreditCard, Landmark, Network, ShieldCheck, Store, X } from 'lucide-react';
import { formatBytes, riskPoints } from '../format';
import { plainReason, settlementOf, type Investigation, type Party } from '../investigation/derive';
import { EvidenceChip } from './EvidenceChip';
import { IdChip } from './IdChip';
import { GateRules, RiskMeter, stoppedText, VERDICT_WORD, type GateReveal } from './PolicyGate';

const PARTY: Record<Party, { name: string; icon: typeof Store }> = {
  stripe: { name: 'Stripe card checks', icon: CreditCard },
  bank: { name: 'Bank attestation', icon: Landmark },
  store: { name: 'Store', icon: Store },
  network: { name: 'Network memory', icon: Network },
};
const PARTIES: Party[] = ['stripe', 'bank', 'store', 'network'];

const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

type VerdictKey = 'approve' | 'step_up' | 'decline' | 'stopped' | 'pending';

function verdictOf(view: Investigation, decided: boolean): { key: VerdictKey; word: string; by: string } {
  if (view.review?.decided) {
    const approve = view.review.decided === 'approve';
    return { key: approve ? 'approve' : 'decline', word: approve ? 'Approved' : 'Declined',
      by: 'The policy gate held it; a person made the final call.' };
  }
  if (view.gate && decided) {
    return { key: view.gate.decision, word: VERDICT_WORD[view.gate.decision] ?? view.gate.decision,
      by: view.gate.decidedBy === 'rules+jev' ? 'Decided by fixed rules. A model vote could only add caution.' : 'Decided by fixed rules.' };
  }
  if (stoppedText(view)) return { key: 'stopped', word: 'Stopped', by: 'Nothing reached the policy gate.' };
  if (view.gate) return { key: 'pending', word: 'Evaluating', by: 'The policy gate is checking its rules.' };
  if (view.phase !== 'idle') return { key: 'pending', word: 'Investigating', by: 'Only the policy gate can produce a verdict.' };
  return { key: 'pending', word: 'No decision yet', by: 'Pick a scenario and pay to start a real checkout.' };
}

/** Stripe's side, kept apart from CardGuard's decision: what happened to the money. */
export function Settlement({ view }: { view: Investigation }) {
  const pay = view.payment;
  const held = !pay && view.phase === 'review';
  const s = pay ? settlementOf(pay.approve, pay.status, pay.reason) : null;
  const ended = !pay && !held && view.phase === 'done'; // stopped before Stripe was asked to settle
  const payText = s ? s.word : held ? 'Held' : ended ? 'Not charged' : '—';
  const payNote = s ? s.note : held ? 'Nothing charged yet' : ended ? 'Nothing was charged' : 'Waiting for a decision';
  const tone = s ? s.tone : held ? 'attention' : ended ? 'neutral' : undefined;
  return (
    <section className="settle" data-tone={tone} aria-label="Stripe payment, separate from the decision">
      <div className="settle__row">
        <span className="settle__who"><CreditCard size={14} aria-hidden /> Stripe payment <em>Test</em></span>
        <b className="settle__value">{payText}</b>
      </div>
      <span className="settle__note">{payNote}</span>
      {pay?.authCode && (
        <div className="settle__id"><span>PaymentIntent</span><IdChip id={pay.authCode} label="PaymentIntent id" /></div>
      )}
    </section>
  );
}

export function EvidenceGroups({ view }: { view: Investigation }) {
  const levels = new Map(view.gate?.parties.map((p) => [p.party, p.level]) ?? []);
  return (
    <div className="parties">
      {PARTIES.map((p) => {
        const items = view.evidence.filter((x) => x.party === p);
        if (!items.length) return null;
        const Icon = PARTY[p].icon;
        return (
          <div className="party" key={p}>
            <div className="party__head">
              <Icon size={14} aria-hidden /> {PARTY[p].name}
              {levels.get(p) && <span className={`level level--${levels.get(p)}`}>{sentence(`${levels.get(p)} risk`)}</span>}
            </div>
            <div className="chips">{items.map((x) => <EvidenceChip key={x.key} item={x} showRound />)}</div>
          </div>
        );
      })}
    </div>
  );
}

interface Props {
  view: Investigation;
  reveal: GateReveal;
  gateKey: number;
  reduced: boolean;
}

export function Inspector({ view, reveal, gateKey, reduced }: Props) {
  const v = verdictOf(view, reveal.decided);
  const verified = view.evidence.filter((x) => x.status === 'verified').length;
  const raw = view.metrics.rawCardShared ?? 0;
  const stopped = stoppedText(view);
  return (
    <aside className="rail" aria-label="Decision, payment and evidence">
      <section className="verdict" data-verdict={v.key} data-live={view.phase !== 'idle' && v.key === 'pending' ? 'true' : undefined}
        aria-live="polite">
        <div className="verdict__head">
          <span><ShieldCheck size={13} aria-hidden /> {view.review?.decided ? 'Decided by a person' : 'CardGuard decision'}</span>
          {view.gate && reveal.decided && <span>{view.gate.score !== null ? riskPoints(view.gate.score) : 'Hard stop'}</span>}
        </div>
        <div className="verdict__word">{v.word}</div>
        <p className="verdict__by">{v.by}</p>
        {view.gate && reveal.decided && <RiskMeter gate={view.gate} />}
        {view.gate?.explanation?.text && reveal.decided && (
          <figure className="advisory">
            <blockquote>{view.gate.explanation.text}</blockquote>
            <figcaption><Bot size={12} aria-hidden />
              {view.gate.explanation.by === 'template' ? 'Template explanation' : `Explained by ${view.gate.explanation.by}`}, after the verdict. It cannot change it.
            </figcaption>
          </figure>
        )}
      </section>

      <Settlement view={view} />

      {view.blocked.length > 0 && (
        <section className="blocked" aria-label="Rejected at a boundary">
          <h3 className="rail-title">Stopped at a boundary</h3>
          {view.blocked.map((b) => (
            <div key={b.seq} className="blocked__row">
              <X size={13} strokeWidth={3} aria-hidden />
              <span title={b.reason}>{b.reason ? plainReason(b.reason) : 'refused'}</span>
            </div>
          ))}
          <p className="blocked__note">None of these entered the evidence.</p>
        </section>
      )}

      <section className="rulebook" aria-label="Policy gate rules">
        <h3 className="rail-title">Policy gate <span>fixed rules, no model</span></h3>
        <GateRules gate={view.gate} human={view.review?.decided} stopped={stopped} reveal={reveal} evaluatingKey={gateKey} reduced={reduced} />
      </section>

      <details className="disclose">
        <summary>
          <span>Evidence</span>
          <span className="disclose__meta">{view.evidence.length ? `${verified} verified bands` : 'None yet'}</span>
          <ChevronDown size={15} className="disclose__chev" aria-hidden />
        </summary>
        {view.evidence.length ? <EvidenceGroups view={view} /> : <p className="disclose__empty">Evidence appears here as each party answers. Only bands, never raw data.</p>}
      </details>

      <details className="disclose">
        <summary>
          <span>Privacy proof</span>
          <span className="disclose__meta" data-ok={raw === 0 && view.metrics.offVocabulary === 0}>
            {raw > 0 ? 'Card data found' : 'No card data shared'}
          </span>
          <ChevronDown size={15} className="disclose__chev" aria-hidden />
        </summary>
        <dl className="privacy-list">
          <dt>Card number or CVC outside Stripe</dt>
          <dd className={raw > 0 ? 'bad' : 'ok'}>{raw > 0 ? 'found' : 'none'}</dd>
          <dt>Banded facts that crossed a boundary</dt><dd>{verified}</dd>
          <dt>Off-vocabulary values</dt><dd className={view.metrics.offVocabulary ? 'bad' : 'ok'}>{view.metrics.offVocabulary}</dd>
          <dt>Messages rejected at a boundary</dt><dd className={view.metrics.rejectedMessages ? 'bad' : ''}>{view.metrics.rejectedMessages}</dd>
          <dt>Bytes across boundaries</dt><dd>{formatBytes(view.metrics.bytes)}</dd>
        </dl>
        <dl className="privacy-list privacy-list--kept">
          <dt>Stays in the bank</dt><dd>last in-person use (city, time), recent declines, cardholder behaviour</dd>
          <dt>Stays in the store</dt><dd>exact amount, buyer IP region, this card&rsquo;s history here</dd>
        </dl>
        <p className="disclose__empty">Stripe moves the money in TEST mode. CardGuard only decides whether it should; it is not Stripe Radar, and Flower does not run inside Stripe.</p>
      </details>
    </aside>
  );
}
