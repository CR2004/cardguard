import type { ReactNode } from 'react';
import { Loader2, Lock } from 'lucide-react';
import type { EvidenceItem, NodeId, NodeView } from '../investigation/derive';
import { NODE_POS } from '../investigation/layout';
import { EvidenceChip } from './EvidenceChip';
import { StatePill } from './StatePill';

export interface VaultItem { label: string; value?: string }

interface Props {
  id: NodeId;
  title: string;
  subtitle: string;
  icon: ReactNode;
  view: NodeView;
  vault?: { title: string; items: VaultItem[] };
  chips?: EvidenceItem[];
  extra?: ReactNode;
  sitsOut?: boolean;
  className?: string;
}

const busy = (s: NodeView['status']) => s === 'processing' || s === 'requested';

export function AgentNode({ id, title, subtitle, icon, view, vault, chips, extra, sitsOut, className = '' }: Props) {
  const pos = NODE_POS[id];
  return (
    <section
      className={`node node--${id} ${className}${sitsOut ? ' sits-out' : ''}`}
      style={{ left: pos.left, top: pos.top }}
      data-status={view.status}
      aria-label={`${title}: ${view.status}. ${view.action}`}
    >
      <div className="node__head">
        <div className="node__icon" aria-hidden>{icon}</div>
        <div className="node__names">
          <div className="node__title">{title}</div>
          <div className="node__role">{subtitle}</div>
        </div>
      </div>
      <div className="node__status">
        <StatePill status={view.status} />
        {view.latencyMs !== undefined && <span>{view.latencyMs.toLocaleString()} ms</span>}
        {sitsOut && <span>sits out round 2</span>}
      </div>
      <div className="node__action" aria-live="polite">
        {busy(view.status) && <Loader2 size={13} className="spin" aria-hidden />}
        <span>{view.action}</span>
      </div>
      {extra}
      {vault && (
        <div className={`vault${view.status === 'processing' ? ' is-working' : ''}`}>
          <div className="vault__title"><Lock size={10} strokeWidth={2.5} aria-hidden /> {vault.title}</div>
          {vault.items.map((it) => (
            <div className="vault__item" key={it.label}>
              <Lock size={10} aria-hidden />
              <span>{it.label}</span>
              {it.value !== undefined && <b>{it.value}</b>}
            </div>
          ))}
        </div>
      )}
      {chips !== undefined && (
        <div className="chips">
          {chips.map((c) => <EvidenceChip key={c.key} item={c} showRound compact />)}
        </div>
      )}
    </section>
  );
}
