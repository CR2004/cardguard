import { useEffect, useState } from 'react';
import { Check, Copy } from 'lucide-react';

/** Copies `text`; the icon confirms for a moment. */
export function CopyButton({ text, label, className = 'id-chip__copy' }: { text: string; label: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 1400);
    return () => window.clearTimeout(t);
  }, [copied]);
  const copy = () => {
    navigator.clipboard?.writeText(text).then(() => setCopied(true), () => setCopied(false));
  };
  return (
    <button type="button" className={className} onClick={copy} aria-label={copied ? `${label} copied` : `Copy ${label}`}>
      {copied ? <Check size={12} strokeWidth={3} aria-hidden /> : <Copy size={12} aria-hidden />}
    </button>
  );
}

/** A long identifier (pm_..., pi_...) shortened in the middle so it never overflows; the full id copies. */
export function IdChip({ id, label }: { id: string; label: string }) {
  const short = id.length > 14 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
  return (
    <span className="id-chip" title={id}>
      <span className="id-chip__text mono">{short}</span>
      <CopyButton text={id} label={label} />
    </span>
  );
}
