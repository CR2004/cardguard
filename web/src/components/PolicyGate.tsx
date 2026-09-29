import { useEffect, useState } from 'react';
import { motion } from 'motion/react';
import { Check, Info, ShieldAlert, TriangleAlert, UserRoundCheck, X } from 'lucide-react';
import type { GateLine } from '../api/types';
import { gateThresholds, type GateView, type Investigation } from '../investigation/derive';
import { NODE_POS } from '../investigation/layout';
import { riskPoints } from '../format';
import { rosettes } from './Guilloche';

const SEAL = 150;
const LINE_MS = 360;

export const VERDICT_WORD: Record<string, string> = { approve: 'Approve', step_up: 'Human review', decline: 'Decline' };
const VERDICT_COLOR: Record<string, string> = { approve: '#3ddc97', step_up: '#ffb23f', decline: '#ff6b5e', stopped: '#ff6b5e' };

/** Why nothing reached the gate, when a checkout stopped before any decision. */
export function stoppedText(view: Investigation): string | undefined {
  if (view.gate || !view.outcome) return undefined;
  return ({
    processor_rejected: 'Stripe refused the payment method: no evidence, nothing charged',
    no_model_endpoint: 'No model endpoint for the model-driven agent: nothing charged',
    blocked: 'A message was rejected at the boundary: nothing charged',
  } as Record<string, string>)[view.outcome.outcome];
}

export interface GateReveal { shown: number; decided: boolean; evaluating: boolean }

/** Reveals the gate's rules one by one, in the order evaluated; the seal and the rulebook share it. */
export function useGateReveal(gate: GateView | undefined, key: number, reduced: boolean): GateReveal {
  const total = gate?.lines.length ?? 0;
  const [shown, setShown] = useState(0);
  const [lastKey, setLastKey] = useState(key);
  if (lastKey !== key) {
    setLastKey(key);
    setShown(0);
  }
  useEffect(() => {
    if (!gate || shown > total) return;
    const timer = window.setTimeout(() => setShown((n) => n + 1), reduced ? 0 : shown === 0 ? 200 : LINE_MS);
    return () => window.clearTimeout(timer);
  }, [gate, shown, total, reduced]);
  const decided = gate !== undefined && shown > total;
  return { shown, decided, evaluating: gate !== undefined && !decided };
}

/** Short wording for the rulebook; the gate's full sentence stays in the tooltip. */
function shortRule(l: GateLine, score: number | null): string {
  switch (l.rule) {
    case 'evidence': return l.state === 'pass' ? 'Evidence complete' : 'No verified evidence';
    case 'privacy': return 'Privacy validation passed';
    case 'hard_stop': return l.state === 'pass' ? 'No hard stop' : 'Hard stop: CVC check failed';
    case 'score': return score === null ? 'Risk points' : riskPoints(score);
    case 'round_2':
      if (l.text.includes('no verified answer')) return 'Round 2: no verified answer';
      return l.state === 'warn' ? 'Round 2: travel implausible' : l.state === 'pass' ? 'Round 2: travel plausible' : 'Round 2: bank could not say';
    case 'rules':
      if (l.text.startsWith('Below the review line, but')) return 'Round 2 requires a person';
      return l.state === 'pass' ? 'Below the review line' : l.state === 'warn' ? 'Auto-decline line not reached' : 'Decline line reached';
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

export function RiskMeter({ gate }: { gate: GateView }) {
  const cut = gateThresholds(gate); // from the gate's own score line, never from here
  if (!cut || gate.score === null) return null;
  const max = Math.max(cut.decline + 4, gate.score + 1);
  const at = (n: number) => `${Math.min(100, (n / max) * 100)}%`;
  return (
    <div className="meter" role="img" aria-label={`${riskPoints(gate.score)}; review from ${cut.review}, decline from ${cut.decline}`}>
      <div className="meter__track">
        <span className="meter__zone meter__zone--ok" style={{ width: at(cut.review) }} />
        <span className="meter__zone meter__zone--review" style={{ left: at(cut.review), width: `calc(${at(cut.decline)} - ${at(cut.review)})` }} />
        <span className="meter__zone meter__zone--decline" style={{ left: at(cut.decline), right: 0 }} />
        <span className="meter__mark" style={{ left: at(gate.score) }} />
      </div>
      <div className="meter__ticks">
        <span style={{ left: at(cut.review) }}>Review {cut.review}</span>
        <span style={{ left: at(cut.decline) }}>Decline {cut.decline}</span>
      </div>
    </div>
  );
}

interface SealProps {
  gate?: GateView;
  stopped?: string;
  reveal: GateReveal;
  evaluatingKey: number;
  reduced: boolean;
}

/** The deterministic authority, drawn as a seal on the stage. Its rules are listed in the rail. */
export function GateSeal({ gate, stopped, reveal, evaluatingKey, reduced }: SealProps) {
  const { decided, evaluating } = reveal;
  const verdictKey = gate ? gate.decision : stopped ? 'stopped' : undefined;
  const ring = decided || stopped ? VERDICT_COLOR[verdictKey ?? ''] : 'rgba(170, 185, 230, 0.3)';
  const pos = NODE_POS.gate;
  const paths = rosettes(SEAL);
  const total = gate?.lines.length ?? 0;
  return (
    <section className="gate" style={{ left: pos.left, top: pos.top }} data-verdict={decided || stopped ? verdictKey : undefined}
      aria-label={`Policy gate: ${decided && gate ? VERDICT_WORD[gate.decision] : stopped ? 'stopped' : evaluating ? 'evaluating' : 'waiting'}`}>
      <div className="gate__seal">
        <svg viewBox={`0 0 ${SEAL} ${SEAL}`} aria-hidden>
          <circle cx={SEAL / 2} cy={SEAL / 2} r={SEAL / 2 - 2} fill="#0d1428" stroke={ring} strokeWidth={decided || stopped ? 3 : 1.5} />
          <circle cx={SEAL / 2} cy={SEAL / 2} r={SEAL / 2 - 9} fill="none" stroke="#d9c9a3" strokeWidth={0.6} strokeDasharray="1.5 3" opacity={0.6} />
          <motion.g
            style={{ originX: '50%', originY: '50%' }}
            animate={{ rotate: evaluating && !reduced ? 24 : 0 }}
            transition={{ duration: evaluating ? (total * LINE_MS) / 1000 + 0.3 : 0.6, ease: 'easeOut' }}
          >
            {paths.map((d, i) => (
              <path key={i} d={d} fill="none" stroke={i % 2 ? '#8e7c55' : '#d9c9a3'} strokeWidth={0.55}
                opacity={decided || stopped ? 0.3 : 0.75 - i * 0.12} />
            ))}
          </motion.g>
          <circle cx={SEAL / 2} cy={SEAL / 2} r={52} fill="#0d1428" stroke="#d9c9a3" strokeWidth={0.6} opacity={0.9} />
        </svg>
        <div className="gate__center">
          {decided || stopped ? (
            <motion.div
              key={`${evaluatingKey}-${verdictKey}`}
              initial={reduced ? false : { scale: 1.25, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ duration: 0.32, ease: [0.2, 0.8, 0.2, 1] }}
              className={`gate__verdict gate__verdict--${verdictKey}`}
            >
              {stopped ? 'Stopped' : VERDICT_WORD[gate?.decision ?? '']}
            </motion.div>
          ) : (
            <div className="gate__title">{evaluating ? 'Evaluating' : 'Policy gate'}</div>
          )}
        </div>
      </div>
      <div className="gate__caption">{gate && decided ? (gate.score !== null ? riskPoints(gate.score) : 'Hard stop') : 'Fixed rules, no model'}</div>
    </section>
  );
}

interface RulesProps {
  gate?: GateView;
  human?: 'approve' | 'decline';
  stopped?: string;
  reveal: GateReveal;
  evaluatingKey: number;
  reduced: boolean;
}

/** The rules the gate evaluated, from the gate.decision event, in order. */
export function GateRules({ gate, human, stopped, reveal, evaluatingKey, reduced }: RulesProps) {
  const { shown, decided } = reveal;
  return (
    <ol className="rules" aria-label="Rules evaluated">
      {!gate && !stopped && <li className="rule rule--idle"><Info size={13} aria-hidden /> Waits for verified evidence</li>}
      {stopped && <li className="rule" data-state="fail"><ShieldAlert size={13} aria-hidden /> {stopped}</li>}
      {gate && gate.lines.slice(0, shown).map((l) => (
        <motion.li
          key={`${evaluatingKey}-${l.rule}`}
          className="rule"
          data-state={l.state}
          initial={reduced ? false : { opacity: 0, x: -6 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.2 }}
          title={l.text}
        >
          <LineIcon state={l.state} />
          <span>{shortRule(l, gate.score)}</span>
        </motion.li>
      ))}
      {decided && gate && human && (
        <li className="rule rule--result" data-state={human === 'approve' ? 'pass' : 'fail'}>
          <UserRoundCheck size={13} strokeWidth={2.4} aria-hidden />
          <span>{human === 'approve' ? 'Approved by a person' : 'Declined by a person'}</span>
        </li>
      )}
    </ol>
  );
}
