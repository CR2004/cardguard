import { useState } from 'react';
import { Check, Hand, KeyRound, TriangleAlert, X } from 'lucide-react';
import { reviewerToken, setReviewerToken } from '../api/client';
import { formatMoney } from '../format';
import type { Investigation } from '../investigation/derive';
import { EvidenceGroups, Roles, Settlement } from './Inspector';

interface Props {
  view: Investigation;
  onDecide: (reviewId: string, action: 'approve' | 'decline') => Promise<unknown>;
}

/** Automation has stopped. A person reads the evidence and makes the final call. */
export function HumanReviewPanel({ view, onDecide }: Props) {
  const [token, setToken] = useState(reviewerToken());
  const [chosen, setChosen] = useState<'approve' | 'decline' | null>(null);
  const [error, setError] = useState<string | null>(null);
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
    <aside className="rail" aria-label="Human review">
      <section className="review-sheet">
        <div className="review-sheet__badge"><Hand size={18} aria-hidden /></div>
        <h2 className="review-sheet__title">Your decision</h2>
        <p className="review-sheet__why">
          {view.amountCents !== undefined && <b>{formatMoney(view.amountCents)}</b>} is held at {view.store ?? 'the store'}.
          The policy gate would not approve or decline it alone. Nothing is charged until you decide.
        </p>

        {reasons.length > 0 && (
          <ul className="review-reasons" aria-label="Why it is held">
            {reasons.map((r) => <li key={r.rule}><TriangleAlert size={14} aria-hidden /><span>{r.text}</span></li>)}
          </ul>
        )}

        {!reviewerToken() && (
          <div className="field">
            <label htmlFor="reviewer"><KeyRound size={12} aria-hidden /> Reviewer credential</label>
            <input id="reviewer" type="password" autoComplete="off" value={token} placeholder="Printed by run_demo.py"
              onChange={(e) => setToken(e.target.value)} />
          </div>
        )}
        <div className="review-actions">
          <button type="button" className={`review-btn review-btn--approve${chosen === 'approve' ? ' is-chosen' : ''}`}
            onClick={() => void decide('approve')} disabled={chosen !== null || !token.trim()}>
            <Check size={17} strokeWidth={2.6} aria-hidden /> Approve and charge
          </button>
          <button type="button" className={`review-btn review-btn--decline${chosen === 'decline' ? ' is-chosen' : ''}`}
            onClick={() => void decide('decline')} disabled={chosen !== null || !token.trim()}>
            <X size={17} strokeWidth={2.6} aria-hidden /> Decline and void
          </button>
        </div>
        {error && <div className="error-note" role="alert">{error}</div>}
        <p className="review-sheet__final">Final. Either way it becomes a training label that never leaves this node.</p>
      </section>

      {view.gate && (
        <section className="roles-panel" aria-label="Who took part in this decision">
          <h3 className="rail-title">Who took part</h3>
          <Roles gate={view.gate} review={view.review} />
        </section>
      )}

      <Settlement view={view} />

      <section aria-label="Evidence by party">
        <h3 className="rail-title">Evidence <span>verified bands only</span></h3>
        <EvidenceGroups view={view} />
      </section>
    </aside>
  );
}
