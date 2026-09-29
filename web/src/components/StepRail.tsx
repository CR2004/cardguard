import { Banknote, Check, CreditCard, Hand, Minus, Radio, Scale, Store, Target, Waypoints, X } from 'lucide-react';
import type { Step, StepId } from '../investigation/steps';

const ICON: Record<StepId, typeof Check> = {
  token: CreditCard, intake: Store, round1: Radio, analysis: Waypoints, round2: Target, gate: Scale, review: Hand,
  payment: Banknote,
};

// Spoken with each step so its state never depends on colour alone.
const STATE_WORD: Record<Step['state'], string> = {
  pending: 'not started', active: 'in progress', waiting: 'waiting', done: 'done', skipped: 'not needed', failed: 'stopped',
};

/** The investigation as eight steps, each lit only by the real events it names (see steps.ts). */
export function StepRail({ steps }: { steps: Step[] }) {
  const current = steps.findIndex((s) => s.state === 'active' || s.state === 'waiting');
  return (
    <ol className="steps">
      {steps.map((s, i) => {
        const Icon = s.state === 'done' ? Check : s.state === 'failed' ? X : s.state === 'skipped' ? Minus : ICON[s.id];
        const next = steps[i + 1];
        const passed = s.state !== 'pending' && s.state !== 'active' && s.state !== 'waiting' && next !== undefined && next.state !== 'pending';
        return (
          <li key={s.id} className="step" data-state={s.state} data-tone={s.tone} data-passed={passed || undefined}
            aria-current={i === current ? 'step' : undefined}>
            <span className="step__dot" aria-hidden><Icon size={16} strokeWidth={s.state === 'done' ? 3 : 2.2} /></span>
            {next && <span className="step__line" aria-hidden />}
            <span className="step__title">{s.title}</span>
            <span className="step__detail" title={s.detail}><span className="sr-only">{STATE_WORD[s.state]}: </span>{s.detail}</span>
          </li>
        );
      })}
    </ol>
  );
}
