import { FolderClosed, Rewind, SkipForward } from 'lucide-react';
import type { Config } from '../api/types';
import { formatMs } from '../format';
import { Guilloche } from './Guilloche';

interface Props {
  config: Config | null;
  status: { tone: 'idle' | 'live' | 'review' | 'done' | 'error'; text: string; detail?: string };
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
        <Guilloche size={26} colors={['#9aa5ff', '#43c9b7', '#cdbb93', '#6f7b9a']} strokeWidth={0.9} />
        <span>CardGuard</span>
        <small>multi-party card risk, card data never in a model</small>
      </div>
      <div className="env" aria-label="Environment">
        <span className={`env-chip${flower ? ' env-chip--flower' : ''}`} title="Where the coordinator runs">
          <b>{flower ? `Flower · ${config?.federation}` : 'In-process · no Flower'}</b>
        </span>
        <span className="env-chip env-chip--stripe" title="Who moves the money"><b>Stripe TEST</b></span>
        <span className="env-chip" title="The bank attests about the cardholder; it never processes the payment">
          Bank attestation <b>{config?.bank_attestation ? 'connected' : 'not configured'}</b>
        </span>
        {config?.demo_controls && <span className="env-chip" title="The page may choose buyer country and attack modes">Demo controls</span>}
      </div>
      <div className="topbar__spacer" />
      <div className="run-status" role="status" aria-live="polite">
        <span className={`dot dot--${status.tone}`} aria-hidden />
        <b>{status.text}</b>
        {status.detail && <span>{status.detail}</span>}
        {elapsedMs !== undefined && elapsedMs > 0 && <span title="Real time on the nodes, from checkout to the latest step">· {formatMs(elapsedMs)} on the nodes</span>}
      </div>
      <button type="button" className="icon-btn" onClick={onReplay} disabled={!canReplay} title="Play this investigation again from its recorded events">
        <Rewind size={14} aria-hidden /> Replay
      </button>
      <button type="button" className="icon-btn" onClick={onSkip} disabled={!canSkip} title="Show every event received so far">
        <SkipForward size={14} aria-hidden /> Skip
      </button>
      <button type="button" className="icon-btn" onClick={onOperations}>
        <FolderClosed size={14} aria-hidden /> Node records
      </button>
    </header>
  );
}
