import { ArrowRight, Bot, CreditCard, Landmark, Network, Store, X } from 'lucide-react';
import { formatBytes, pretty } from '../format';
import type { Investigation, Party } from '../investigation/derive';
import { EvidenceChip } from './EvidenceChip';
import { VERDICT_WORD } from './PolicyGate';

const PARTY: Record<Party, { name: string; icon: typeof Store }> = {
  stripe: { name: 'Stripe card checks', icon: CreditCard },
  bank: { name: 'Bank attestation', icon: Landmark },
  store: { name: 'Store', icon: Store },
  network: { name: 'Network memory', icon: Network },
};

export function StripeStatus({ view }: { view: Investigation }) {
  const decision = view.review?.decided
    ? `${view.review.decided === 'approve' ? 'Approved' : 'Declined'} by a person`
    : view.gate ? VERDICT_WORD[view.gate.decision] : view.outcome ? 'No decision' : '—';
  const pay = view.payment;
  const payText = !pay ? (view.phase === 'review' ? 'Held, not charged' : '—')
    : pay.approve ? (pay.status === 'succeeded' ? 'succeeded' : pretty(pay.status))
      : 'voided, not charged';
  return (
    <div>
      <h2 className="section-title">Decision and payment <small>kept separate</small></h2>
      <div className="pay-flow">
        <div className="pay-box">
          <div className="pay-box__label">CardGuard decision</div>
          <div className="pay-box__value">{decision}</div>
        </div>
        <div className="pay-flow__arrow"><ArrowRight size={14} aria-hidden /></div>
        <div className="pay-box pay-box--stripe">
          <div className="pay-box__label">Stripe payment (TEST)</div>
          <div className="pay-box__value">{payText}</div>
          {pay?.authCode && <div className="pay-box__label mono">{pay.authCode}</div>}
          {pay?.reason && <div className="pay-box__label">{pay.reason}</div>}
        </div>
      </div>
      <p className="faint" style={{ fontSize: 11.5, margin: '6px 0 0' }}>
        Stripe moves the money in TEST mode. CardGuard decides whether it should; it is not Stripe Radar, and Flower
        does not run inside Stripe.</p>
    </div>
  );
}

export function Inspector({ view }: { view: Investigation }) {
  const parties: Party[] = ['stripe', 'bank', 'store', 'network'];
  const signals = new Map(view.gate?.parties.map((p) => [p.party, p]) ?? []);
  const crossed = view.evidence.filter((x) => x.status === 'verified').length;
  return (
    <aside className="panel panel--right" aria-label="Evidence, privacy and policy">
      <div className="verdict-card">
        <div className="verdict-card__row">
          <span className="muted">Verdict</span>
          {view.gate && <span className="faint">{view.gate.score !== null ? `${view.gate.score} risk points` : 'hard stop'}</span>}
        </div>
        {view.review?.decided ? (
          <>
            <div className={`verdict-word verdict-word--${view.review.decided === 'approve' ? 'approve' : 'decline'}`}>
              {view.review.decided === 'approve' ? 'Approved by a person' : 'Declined by a person'}
            </div>
            <div className="verdict-card__by">The policy gate held it for review; a person made the final decision.</div>
          </>
        ) : (
          <>
            <div className={`verdict-word verdict-word--${view.gate?.decision ?? (view.outcome ? 'stopped' : '')}`}>
              {view.gate ? VERDICT_WORD[view.gate.decision] : view.outcome ? 'No decision: stopped' : 'Pending'}
            </div>
            <div className="verdict-card__by">
              {view.gate
                ? `Computed by the deterministic policy gate${view.gate.decidedBy === 'rules+jev' ? '; a model vote could only add caution' : ''}.`
                : 'Only the policy gate can produce a verdict.'}
            </div>
          </>
        )}
        {view.gate?.explanation?.text && (
          <div className="advisory">
            <q>{view.gate.explanation.text}</q>
            <div className="advisory__by"><Bot size={12} aria-hidden />
              Explanation only, written {view.gate.explanation.by === 'template' ? 'from a template' : `by ${view.gate.explanation.by}`} after the verdict. It cannot change it.
            </div>
          </div>
        )}
      </div>

      <StripeStatus view={view} />

      {view.blocked.length > 0 && (
        <div>
          <h2 className="section-title">Rejected at a boundary <small>never entered the evidence</small></h2>
          {view.blocked.map((b) => (
            <div key={b.seq} className="rule" data-state="fail">
              <X size={13} strokeWidth={3} aria-hidden />
              <span>{b.from} → {b.to}: {b.reason ?? 'refused'}</span>
            </div>
          ))}
        </div>
      )}

      <div>
        <h2 className="section-title">Evidence by party <small>bands only</small></h2>
        {parties.map((p) => {
          const items = view.evidence.filter((x) => x.party === p);
          const Icon = PARTY[p].icon;
          const signal = signals.get(p);
          return (
            <div className="party" key={p}>
              <div className="party__head">
                <Icon size={14} aria-hidden /> {PARTY[p].name}
                {signal && <span className={`level level--${signal.level}`}>{signal.level}</span>}
                <small>{items.length ? `${items.filter((x) => x.status === 'verified').length}/${items.length} verified` : 'nothing yet'}</small>
              </div>
              <div className="chips">
                {items.map((x) => <EvidenceChip key={x.key} item={x} showRound />)}
              </div>
            </div>
          );
        })}
      </div>

      <div>
        <h2 className="section-title">Privacy <small>what moved, what stayed</small></h2>
        <dl className="privacy-list">
          <dt>Card number or CVC outside the bank</dt>
          <dd className={(view.metrics.rawCardShared ?? 0) > 0 ? '' : 'ok'}>{(view.metrics.rawCardShared ?? 0) > 0 ? 'found' : 'none'}</dd>
          <dt>Banded facts that crossed a boundary</dt><dd>{crossed}</dd>
          <dt>Off-vocabulary values</dt><dd className={view.metrics.offVocabulary ? '' : 'ok'}>{view.metrics.offVocabulary}</dd>
          <dt>Bytes across boundaries</dt><dd>{formatBytes(view.metrics.bytes)}</dd>
        </dl>
      </div>
    </aside>
  );
}
