import { AlertTriangle, Check, CircleDashed, Clock3, Loader2, Minus, Split, X } from 'lucide-react';
import type { NodeStatus } from '../investigation/derive';

// Nine states, five visual families. The word always says which one; colour is never alone.
const FAMILY: Record<NodeStatus, 'idle' | 'active' | 'good' | 'attention' | 'timeout' | 'bad'> = {
  idle: 'idle', requested: 'active', processing: 'active', responded: 'good', verified: 'good', complete: 'good',
  disagreement: 'attention', timeout: 'timeout', rejected: 'bad',
};

const ICON: Record<NodeStatus, typeof Check> = {
  idle: Minus, requested: CircleDashed, processing: Loader2, responded: Check, verified: Check, complete: Check,
  disagreement: Split, timeout: Clock3, rejected: X,
};

export function StatePill({ status }: { status: NodeStatus }) {
  const Icon = status === 'disagreement' ? AlertTriangle : ICON[status];
  return (
    <span className="state-pill" data-family={FAMILY[status]}>
      <Icon size={10} strokeWidth={2.6} aria-hidden />
      {status}
    </span>
  );
}
