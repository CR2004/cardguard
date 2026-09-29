import { Check, X } from 'lucide-react';
import { motion } from 'motion/react';
import { factLabel, shortLabel, type EvidenceItem } from '../investigation/derive';

// A privacy-safe attestation: a fact name and a band from the closed vocabulary, never raw data.
export function EvidenceChip({ item, showRound = false, compact = false }: { item: EvidenceItem; showRound?: boolean; compact?: boolean }) {
  const title = `${factLabel(item.key)}: ${item.value.replace(/_/g, ' ')} (${item.status === 'verified'
    ? 'verified by the coordinator' : item.status === 'rejected' ? 'rejected' : 'attested, not yet verified'}${
    item.conflict ? '; part of the evidence conflict' : ''}${item.points ? `; the policy gate counted ${item.points} risk point${item.points === 1 ? '' : 's'}` : ''})`;
  return (
    <motion.span
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.22, ease: [0.2, 0.8, 0.2, 1] }}
      className={`chip${item.conflict ? ' is-conflict' : ''}`}
      data-status={item.status}
      data-risky={item.points ? 'true' : undefined}
      title={title}
    >
      {item.status === 'verified' && <Check size={10} strokeWidth={3} aria-hidden />}
      {item.status === 'rejected' && <X size={10} strokeWidth={3} aria-hidden />}
      {compact ? shortLabel(item.key) : factLabel(item.key)} <b>{item.value.replace(/_/g, ' ')}</b>
      {showRound && item.round === 2 && <span className="chip__round">R2</span>}
      {item.points ? <span className="chip__points">+{item.points}</span> : null}
    </motion.span>
  );
}
