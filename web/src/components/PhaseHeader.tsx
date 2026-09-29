import { CircleCheck, CircleX, Gavel, Hand, Search, Split, Target } from 'lucide-react';
import type { Investigation } from '../investigation/derive';

const OUTCOME_TEXT: Record<string, [string, string]> = {
  approved: ['Approved and charged', 'The policy gate approved; the payment was authorized.'],
  declined: ['Declined', 'The policy gate declined; the verification was voided and nothing was charged.'],
  blocked: ['Stopped at the boundary', 'A prohibited message never left its node. Nothing was charged.'],
  processor_rejected: ['Refused by Stripe', 'Stripe refused the payment method before any evidence existed. Nothing was charged.'],
  approved_by_human: ['Approved by a person', 'A reviewer approved the held payment; it was authorized.'],
  declined_by_human: ['Declined by a person', 'A reviewer declined the held payment; nothing was charged.'],
  no_model_endpoint: ['No model endpoint', 'The model-driven agent had no endpoint configured. Nothing was charged.'],
};

export function PhaseHeader({ view, tokenizing, hint }: { view: Investigation; tokenizing: boolean; hint?: { title: string; watch: string } }) {
  const flower = view.mode === 'flower';
  let icon = <Search size={20} aria-hidden />;
  let title = 'Ready for a checkout';
  let sub = 'The card goes only to Stripe. Between the parties, only banded evidence moves.';
  let tone = '';
  if (hint && view.phase === 'idle' && !tokenizing) {
    title = hint.title;
    sub = `Watch for this: ${hint.watch}`;
  } else if (tokenizing) {
    title = 'Tokenizing with Stripe';
    sub = 'The card goes from Stripe Elements to Stripe. The store will receive a payment-method id, never the card.';
  } else if (view.outcome && view.phase === 'done') {
    const [t, s] = OUTCOME_TEXT[view.outcome.outcome] ?? [view.outcome.outcome, ''];
    title = t;
    sub = s;
    const bad = ['blocked', 'processor_rejected', 'declined', 'declined_by_human'].includes(view.outcome.outcome);
    icon = bad ? <CircleX size={20} aria-hidden /> : <CircleCheck size={20} aria-hidden />;
    tone = bad ? 'rejected' : '';
  } else {
    switch (view.phase) {
      case 'intake':
        title = 'Payment method received';
        sub = 'The store asks Stripe for the card checks and the bank for its attestation. Neither sends card data.';
        break;
      case 'round1':
        title = 'Round 1: independent evidence';
        sub = flower
          ? 'The coordinator asks the store over Flower Grid; its network memory checks other stores.'
          : 'The coordinator runs in-process on the store node: no Flower run for this decision.';
        break;
      case 'conflict':
        icon = <Split size={20} aria-hidden />;
        title = 'Evidence conflict: a follow-up is needed';
        sub = view.conflict ?? '';
        tone = 'conflict';
        break;
      case 'round2':
        icon = <Target size={20} aria-hidden />;
        title = 'Round 2: one targeted question';
        sub = 'Only the bank is asked, through the store’s node. The network sits this round out.';
        break;
      case 'gate':
        icon = <Gavel size={20} aria-hidden />;
        title = 'Policy gate';
        sub = 'Fixed rules decide. Models may vote or explain; they never decide alone.';
        break;
      case 'review':
        icon = <Hand size={20} aria-hidden />;
        title = 'Human review';
        sub = 'Automation paused. The payment is held until a person decides.';
        tone = 'review';
        break;
      case 'settling':
        title = 'Settling';
        sub = 'The decision is final; the processor moves the money or voids the verification.';
        break;
      default:
        break;
    }
  }
  return (
    <div className={`phase${tone ? ` phase--${tone}` : ''}`} aria-live="polite">
      <div className="phase__title">{icon}{title}</div>
      <div className="phase__sub">{sub}</div>
    </div>
  );
}
