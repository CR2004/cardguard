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
  icon: ReactNode;
  view: NodeView;
  vault?: { title: string; items: VaultItem[]; note?: string };
  chips?: EvidenceItem[];
  sitsOut?: boolean;
  className?: string;
}

const busy = (s: NodeView['status']) => s === 'processing' || s === 'requested';

export function AgentNode({ id, title, icon, view, vault, chips, sitsOut, className = '' }: Props) {
  const pos = NODE_POS[id];
  const inline = title.length <= 5; // "Bank", "Store": the status fits beside the title
  return (
    <section
      className={`node node--${id} ${className}${sitsOut ? ' sits-out' : ''}`}
      style={{ left: pos.left, top: pos.top }}
      data-status={view.status}
      aria-label={`${title}: ${view.status}. ${view.action}${sitsOut ? '. Sits out round 2' : ''}`}
    >
      <div className="node__head">
        <div className="node__icon" aria-hidden>{icon}</div>
        <div className="node__title">{title}</div>
        {inline && <StatePill status={view.status} />}
      </div>
      {!inline && <div className="node__status"><StatePill status={view.status} /></div>}
      <div className="node__action" aria-live="polite">
        {busy(view.status) && <Loader2 size={14} className="spin" aria-hidden />}
        <span>{view.action}</span>
      </div>
      {vault && (
        <div className={`vault${view.status === 'processing' ? ' is-working' : ''}`} title={vault.note}>
          <div className="vault__title"><Lock size={11} strokeWidth={2.5} aria-hidden /> {vault.title}</div>
          {vault.items.map((it) => (
            <div className="vault__item" key={it.label}>
              <span>{it.label}</span>
              {it.value !== undefined && <b>{it.value}</b>}
            </div>
          ))}
        </div>
      )}
      {chips !== undefined && chips.length > 0 && (
        <div className="chips">
          {chips.map((c) => <EvidenceChip key={c.key} item={c} showRound compact />)}
        </div>
      )}
    </section>
  );
}
