import { useEffect, useState } from 'react';
import { motion } from 'motion/react';
import { ArrowRight, Check, Info, ShieldAlert, TriangleAlert, UserRoundCheck, X } from 'lucide-react';
import type { GateLine } from '../api/types';
import type { GateView } from '../investigation/derive';
import { NODE_POS } from '../investigation/layout';
import { rosettes } from './Guilloche';

const SEAL = 160;
const LINE_MS = 360;

export const VERDICT_WORD: Record<string, string> = { approve: 'Approve', step_up: 'Human review', decline: 'Decline' };
const VERDICT_COLOR: Record<string, string> = { approve: '#4cc38a', step_up: '#f2b84b', decline: '#ff7a66', stopped: '#ff7a66' };

/** Short wording for the seal card; the full sentence from the gate stays in the tooltip. */
function shortRule(l: GateLine, score: number | null): string {
  switch (l.rule) {
    case 'evidence': return l.state === 'pass' ? 'Evidence complete' : 'No verified evidence';
    case 'privacy': return 'Privacy validation passed';
    case 'hard_stop': return l.state === 'pass' ? 'No hard stop' : 'Hard stop: CVC check failed';
    case 'score': return `Risk points: ${score ?? '?'} (review 3, decline 8)`;
    case 'round_2':
      if (l.text.includes('no verified answer')) return 'Round 2: no verified answer';
      return l.state === 'warn' ? 'Round 2: travel implausible' : l.state === 'pass' ? 'Round 2: travel plausible' : 'Round 2: bank could not say';
    case 'rules':
      if (l.text.startsWith('Below the review line, but')) return 'Round 2 requires a person';
      return l.state === 'pass' ? 'Below the review line' : l.state === 'warn' ? 'Auto-decline threshold not reached' : 'Decline threshold reached';
    case 'model': return l.text.startsWith('Model vote:') ? (l.text.split(';')[0] ?? l.text) : 'No model vote: rules alone';
    case 'confidence': return 'Model not confident: a person decides';
    default: return l.text;
  }
}

function LineIcon({ state }: { state: GateLine['state'] }) {
  if (state === 'pass') return <Check size={13} strokeWidth={3} aria-label="passed" />;
  if (state === 'fail') return <X size={13} strokeWidth={3} aria-label="failed" />;
  if (state === 'warn') return <TriangleAlert size={13} strokeWidth={2.4} aria-label="caution" />;
  return <Info size={13} strokeWidth={2.2} aria-label="note" />;
}

interface Props {
  gate?: GateView;
  human?: 'approve' | 'decline'; // a person's final decision on a held payment
  stopped?: string; // nothing reached the gate: why
  evaluatingKey: number; // changes when a new gate result arrives, to replay its rules in order
  reduced: boolean;
}

/** The deterministic authority. Rules come from the gate.decision event, shown in the order evaluated. */
export function PolicyGate({ gate, human, stopped, evaluatingKey, reduced }: Props) {
  const total = gate?.lines.length ?? 0;
  const [shown, setShown] = useState(0);
  const [lastKey, setLastKey] = useState(evaluatingKey);
  if (lastKey !== evaluatingKey) {
    setLastKey(evaluatingKey);
    setShown(0);
  }
  useEffect(() => {
    if (!gate || shown > total) return;
    const timer = window.setTimeout(() => setShown((n) => n + 1), reduced ? 0 : shown === 0 ? 200 : LINE_MS);
    return () => window.clearTimeout(timer);
  }, [gate, shown, total, reduced]);

  const decided = gate !== undefined && shown > total;
  const verdictKey = gate ? gate.decision : stopped ? 'stopped' : undefined;
  const ring = decided || stopped ? VERDICT_COLOR[verdictKey ?? ''] : '#3a4a70';
  const evaluating = gate !== undefined && !decided;
  const pos = NODE_POS.gate;
  const paths = rosettes(SEAL);

  return (
    <section className="gate" style={{ left: pos.left, top: pos.top }} aria-label="Policy gate: deterministic rules decide">
      <div className="gate__seal">
        <svg viewBox={`0 0 ${SEAL} ${SEAL}`} aria-hidden>
          <circle cx={SEAL / 2} cy={SEAL / 2} r={SEAL / 2 - 2} fill="#0c1428" stroke={ring} strokeWidth={decided || stopped ? 3 : 1.5} />
          <circle cx={SEAL / 2} cy={SEAL / 2} r={SEAL / 2 - 9} fill="none" stroke="#cdbb93" strokeWidth={0.6} strokeDasharray="1.5 3" opacity={0.7} />
          <motion.g
            style={{ originX: '50%', originY: '50%' }}
            animate={{ rotate: evaluating && !reduced ? 24 : 0 }}
            transition={{ duration: evaluating ? (total * LINE_MS) / 1000 + 0.3 : 0.6, ease: 'easeOut' }}
          >
            {paths.map((d, i) => (
              <path key={i} d={d} fill="none" stroke={i % 2 ? '#8e7c55' : '#cdbb93'} strokeWidth={0.55}
                opacity={decided || stopped ? 0.35 : 0.8 - i * 0.12} />
            ))}
          </motion.g>
          <circle cx={SEAL / 2} cy={SEAL / 2} r={56} fill="#0c1428" stroke="#cdbb93" strokeWidth={0.6} />
        </svg>
        <div className="gate__center">
          {decided || stopped ? (
            <motion.div
              key={`${evaluatingKey}-${verdictKey}`}
              initial={reduced ? false : { scale: 1.25, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ duration: 0.32, ease: [0.2, 0.8, 0.2, 1] }}
            >
              <div className={`gate__verdict gate__verdict--${verdictKey}`}>
                {stopped ? 'Stopped' : VERDICT_WORD[gate?.decision ?? '']}
              </div>
              <div className="gate__by">{stopped ? 'before any decision' : 'by fixed policy'}</div>
            </motion.div>
          ) : (
            <div>
              <div className="gate__title">POLICY GATE</div>
              <div className="gate__by">{evaluating ? 'evaluating…' : 'fixed rules'}</div>
            </div>
          )}
        </div>
      </div>
      <div className="rules" role="list" aria-label="Rules evaluated">
        <div className="rules__head">
          <span>Fixed rules</span>
          {gate && <span>{gate.score !== null ? `${gate.score} risk pts` : 'hard stop'}</span>}
        </div>
        {!gate && !stopped && <div className="rule faint" role="listitem"><Info size={13} aria-hidden /> Waiting for verified evidence</div>}
        {stopped && (
          <div className="rule" data-state="fail" role="listitem"><ShieldAlert size={13} aria-hidden /> {stopped}</div>
        )}
        {gate && gate.lines.slice(0, shown).map((l) => (
          <motion.div
            key={`${evaluatingKey}-${l.rule}`}
            className="rule"
            data-state={l.state}
            role="listitem"
            initial={reduced ? false : { opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.2 }}
          >
            <LineIcon state={l.state} />
            <span title={l.text}>{shortRule(l, gate.score)}</span>
          </motion.div>
        ))}
        {decided && gate && (
          <div className="rule rule--result" data-state={gate.decision === 'approve' ? 'pass' : gate.decision === 'decline' ? 'fail' : 'warn'} role="listitem">
            <ArrowRight size={13} strokeWidth={2.6} aria-hidden />
            <span style={{ color: VERDICT_COLOR[gate.decision] }}>{VERDICT_WORD[gate.decision]}</span>
          </div>
        )}
        {decided && human && (
          <div className="rule rule--result" data-state={human === 'approve' ? 'pass' : 'fail'} role="listitem">
            <UserRoundCheck size={13} strokeWidth={2.4} aria-hidden />
            <span style={{ color: VERDICT_COLOR[human] }}>{human === 'approve' ? 'Approved by a person' : 'Declined by a person'}</span>
          </div>
        )}
      </div>
    </section>
  );
}
