import { useState } from 'react';
import { Check, CreditCard, Hand, KeyRound, Landmark, Network, Store, UserRoundCheck, X } from 'lucide-react';
import { reviewerToken, setReviewerToken } from '../api/client';
import { formatMoney } from '../format';
import type { Investigation, Party } from '../investigation/derive';
import { EvidenceChip } from './EvidenceChip';

const PARTY_NAME: Record<Party, [string, typeof Store]> = {
  stripe: ['Stripe card checks', CreditCard],
  bank: ['Bank attestation', Landmark], store: ['Store', Store], network: ['Network memory', Network],
};

interface Props {
  view: Investigation;
  onDecide: (reviewId: string, action: 'approve' | 'decline') => Promise<unknown>;
}

/** Automation has stopped. A person reads the evidence and makes the final call. */
export function HumanReviewPanel({ view, onDecide }: Props) {
  const [token, setToken] = useState(reviewerToken());
  const [chosen, setChosen] = useState<'approve' | 'decline' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const decided = view.review?.decided;
  const reasons = view.gate?.lines.filter((l) => l.state === 'warn' || l.state === 'fail') ?? [];

  async function decide(action: 'approve' | 'decline') {
    if (!view.review?.id) return;
    setReviewerToken(token.trim());
    setChosen(action);
    setError(null);
    try {
      await onDecide(view.review.id, action);
    } catch (e) {
      setChosen(null);
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.includes('token') ? 'The reviewer credential was not accepted. run_demo.py prints it at start-up.' : msg);
      if (msg.includes('token')) setReviewerToken('');
    }
  }

  return (
    <aside className="panel panel--right review-panel" aria-label="Human review">
      <div className="review-panel__head">
        <div className="review-panel__title"><Hand size={16} aria-hidden /> Human review</div>
        <div className="review-panel__why">
          {view.amountCents !== undefined && <>{formatMoney(view.amountCents)} at {view.store}. </>}
          The policy gate held this payment instead of approving or declining it.
        </div>
      </div>

      {reasons.length > 0 && (
        <div>
          <h2 className="section-title">Why it is held</h2>
          {reasons.map((r) => <div key={r.rule} className="rule" data-state={r.state}><Hand size={12} aria-hidden /><span>{r.text}</span></div>)}
        </div>
      )}

      <div>
        <h2 className="section-title">Evidence by party <small>all verified bands</small></h2>
        {(['stripe', 'bank', 'store', 'network'] as Party[]).map((p) => {
          const [name, Icon] = PARTY_NAME[p];
          const items = view.evidence.filter((x) => x.party === p);
          if (!items.length) return null;
          return (
            <div className="party" key={p}>
              <div className="party__head"><Icon size={14} aria-hidden /> {name}</div>
              <div className="chips">{items.map((x) => <EvidenceChip key={x.key} item={x} showRound />)}</div>
            </div>
          );
        })}
      </div>

      {decided ? (
        <div className="human-decided" role="status">
          <UserRoundCheck size={20} color={decided === 'approve' ? '#4cc38a' : '#ff7a66'} aria-hidden />
          <div>
            <b>{decided === 'approve' ? 'Approved by a person' : 'Declined by a person'}</b>
            <div className="faint" style={{ fontSize: 12 }}>
              {decided === 'approve' ? 'The final decision was human. The card is charged.' : 'The final decision was human. Nothing is charged.'}
            </div>
          </div>
        </div>
      ) : (
        <div>
          {!reviewerToken() && (
            <div className="field" style={{ marginTop: 0, marginBottom: 12 }}>
              <label htmlFor="reviewer"><KeyRound size={11} aria-hidden /> Reviewer credential (printed by run_demo.py)</label>
              <input id="reviewer" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
            </div>
          )}
          <div className="review-actions">
            <button type="button" className={`review-btn review-btn--approve${chosen === 'approve' ? ' is-chosen' : ''}`}
              onClick={() => void decide('approve')} disabled={chosen !== null || !token.trim()}>
              <Check size={16} aria-hidden /> Approve payment
            </button>
            <button type="button" className={`review-btn review-btn--decline${chosen === 'decline' ? ' is-chosen' : ''}`}
              onClick={() => void decide('decline')} disabled={chosen !== null || !token.trim()}>
              <X size={16} aria-hidden /> Decline payment
            </button>
          </div>
          <p className="review-final" style={{ marginTop: 8 }}>
            This decision is final. Approving charges the card; declining voids the bank’s verification.
            Either way it becomes a training label that never leaves this node.
          </p>
          {error && <div className="error-note" role="alert">{error}</div>}
        </div>
      )}
    </aside>
  );
}
