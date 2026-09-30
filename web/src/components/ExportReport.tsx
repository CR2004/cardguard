import { useState } from 'react';
import { Braces, Check, FileDown, FileText, LoaderCircle } from 'lucide-react';
import { buildReport, reportFilename, type ReportSource } from '../report/report';

type Kind = 'pdf' | 'json';

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** Export the finished decision: a PDF for people, the full privacy-safe record as JSON for machines. */
export function ExportReport({ source }: { source: ReportSource }) {
  const [busy, setBusy] = useState<Kind | null>(null);
  const [saved, setSaved] = useState<{ kind: Kind; name: string } | null>(null);
  const [error, setError] = useState<string | null>(null); // all three reset when the next checkout starts (unmount)

  async function save(kind: Kind) {
    setBusy(kind);
    setSaved(null);
    setError(null);
    try {
      const report = buildReport(source);
      const blob = kind === 'json'
        ? new Blob([`${JSON.stringify(report, null, 2)}\n`], { type: 'application/json' })
        : await import('../report/pdf').then(({ renderPdf }) => renderPdf(report));
      const name = reportFilename(report, kind);
      download(blob, name);
      setSaved({ kind, name });
    } catch (e) {
      setError(`Could not export the ${kind === 'pdf' ? 'PDF' : 'JSON'} report: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  const icon = (kind: Kind) => (busy === kind ? <LoaderCircle size={14} className="export__spin" aria-hidden />
    : saved?.kind === kind ? <Check size={14} strokeWidth={2.6} aria-hidden />
      : kind === 'pdf' ? <FileText size={14} aria-hidden /> : <Braces size={14} aria-hidden />);

  return (
    <section className="export" aria-label="Export this decision">
      <div className="export__row">
        <span className="export__label"><FileDown size={14} aria-hidden /> Export report</span>
        <div className="export__actions">
          <button type="button" className="export__btn" onClick={() => void save('pdf')} disabled={busy !== null}
            title="A readable report for reviewers: verdict, evidence, rules, payment and privacy">
            {icon('pdf')} PDF report
          </button>
          <button type="button" className="export__btn" onClick={() => void save('json')} disabled={busy !== null}
            title="The full privacy-safe decision record, including the trace">
            {icon('json')} Technical JSON
          </button>
        </div>
      </div>
      <p className="export__status" role="status" data-tone={error ? 'error' : undefined}>
        {error ?? (busy ? `Preparing the ${busy === 'pdf' ? 'PDF' : 'JSON'} report…` : saved ? `Downloaded ${saved.name}` : '')}
      </p>
    </section>
  );
}
