import type { NodeStatus } from '../investigation/derive';

// Nine states, five visual families. The word always says which one; colour is never alone.
const FAMILY: Record<NodeStatus, 'idle' | 'active' | 'good' | 'attention' | 'timeout' | 'bad'> = {
  idle: 'idle', requested: 'active', processing: 'active', responded: 'good', verified: 'good', complete: 'good',
  disagreement: 'attention', timeout: 'timeout', rejected: 'bad',
};

const WORD: Record<NodeStatus, string> = {
  idle: 'Idle', requested: 'Asked', processing: 'Working', responded: 'Answered', verified: 'Verified', complete: 'Done',
  disagreement: 'Conflict', timeout: 'No answer', rejected: 'Rejected',
};

export function StatePill({ status }: { status: NodeStatus }) {
  return (
    <span className="state-pill" data-family={FAMILY[status]}>
      <i aria-hidden />
      {WORD[status]}
    </span>
  );
}
