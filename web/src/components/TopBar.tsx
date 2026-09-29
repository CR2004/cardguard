import { FolderClosed, Rewind, SkipForward } from 'lucide-react';
import type { Config } from '../api/types';
import { formatMs } from '../format';
import { Guilloche } from './Guilloche';

interface Props {
  config: Config | null;
  status: { tone: 'idle' | 'live' | 'review' | 'done' | 'error'; text: string };
  canReplay: boolean;
  canSkip: boolean;
  onReplay: () => void;
  onSkip: () => void;
  onOperations: () => void;
  elapsedMs?: number;
}

export function TopBar({ config, status, canReplay, canSkip, onReplay, onSkip, onOperations, elapsedMs }: Props) {
  const flower = Boolean(config?.federation);
  return (
    <header className="topbar">
      <div className="brand">
        <Guilloche size={28} colors={['#a399ff', '#3fd9c4', '#d9c9a3', '#8a94b3']} strokeWidth={0.9} />
        <span>CardGuard</span>
      </div>
      <ul className="env" aria-label="Environment">
        <li title="Who moves the money: Stripe, in TEST mode"><i data-c="stripe" aria-hidden />Stripe test</li>
        <li title="The bank attests about the cardholder; it never processes the payment">
          <i data-c={config?.bank_attestation ? 'bank' : 'off'} aria-hidden />{config?.bank_attestation ? 'Bank connected' : 'No bank'}
        </li>
        <li title={flower ? `The coordinator runs as a Flower AgentApp on ${config?.federation}` : 'No Flower run: the coordinator runs in the store process'}>
          <i data-c={flower ? 'flower' : 'local'} aria-hidden />{flower ? `Flower ${config?.federation}` : 'In-process'}
        </li>
      </ul>
      <div className="topbar__spacer" />
      <div className="run-status" role="status" aria-live="polite" data-tone={status.tone}>
        <span className="run-status__dot" aria-hidden />
        <b>{status.text}</b>
        {elapsedMs !== undefined && elapsedMs > 0 && (
          <span title="Real time on the nodes, from checkout to the latest step">{formatMs(elapsedMs)}</span>
        )}
      </div>
      <div className="topbar__actions">
        <button type="button" className="icon-btn" onClick={onReplay} disabled={!canReplay} aria-label="Replay" title="Play this investigation again from its recorded events">
          <Rewind size={15} aria-hidden /><span>Replay</span>
        </button>
        <button type="button" className="icon-btn" onClick={onSkip} disabled={!canSkip} aria-label="Skip" title="Show every event received so far">
          <SkipForward size={15} aria-hidden /><span>Skip</span>
        </button>
        <button type="button" className="icon-btn icon-btn--solid" onClick={onOperations} aria-label="Node records">
          <FolderClosed size={15} aria-hidden /><span>Records</span>
        </button>
      </div>
    </header>
  );
}
