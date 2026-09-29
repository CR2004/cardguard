import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { api } from '../api/client';
import type { AlertRow, LedgerPage, NodesInfo, PaymentRow } from '../api/types';
import { formatMoney } from '../format';

// What the store node itself keeps: its hash-chained disclosure ledger, network alerts, recent
// payments (chargebacks become labels), and the federated retrain. All values are bands or ids.
export function OperationsDrawer({ onClose, refreshKey }: { onClose: () => void; refreshKey: number }) {
  const [ledger, setLedger] = useState<LedgerPage | null>(null);
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [payments, setPayments] = useState<PaymentRow[]>([]);
  const [nodes, setNodes] = useState<NodesInfo | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let live = true;
    Promise.all([api.ledger(), api.alerts(), api.payments(), api.nodes()])
      .then(([l, a, p, n]) => {
        if (!live) return;
        setLedger(l);
        setAlerts(a.alerts);
        setPayments(p);
        setNodes(n);
      })
      .catch((e: Error) => live && setNote(e.message));
    return () => {
      live = false;
    };
  }, [refreshKey, version]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const act = async (fn: () => Promise<{ error?: string } & Record<string, unknown>>, done: (r: Record<string, unknown>) => string) => {
    try {
      const r = await fn();
      setNote(r.error ? r.error : done(r));
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    }
    setVersion((v) => v + 1);
  };

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} aria-hidden />
      <div className="drawer" role="dialog" aria-modal="true" aria-label="Store node records">
        <div className="drawer__head">
          <h2>Store node records</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><X size={14} aria-hidden /></button>
        </div>
        {note && <div className="error-note" role="status">{note}</div>}

        <div>
          <h3 className="section-title">Federated model <small>{nodes ? `${nodes.count} nodes · ${nodes.labels} human labels here` : ''}</small></h3>
          <p className="faint" style={{ margin: '0 0 8px', fontSize: 12 }}>
            {nodes?.dp ? `Shipped weights trained with differential privacy: ε=${nodes.dp.epsilon} at δ=${nodes.dp.delta}.`
              : 'Shipped weights were trained without differential privacy.'}
          </p>
          <button type="button" className="small-btn" onClick={() => void act(() => api.retrain(),
            (r) => `Federated round done across ${String(r.nodes)} nodes with ${String(r.labels)} labels.`)}>
            Run one federated round with this node’s labels
          </button>
        </div>

        <div>
          <h3 className="section-title">Network alerts <small>same card, 3+ stores in 10 min</small></h3>
          {alerts.length === 0 ? <p className="faint" style={{ margin: 0 }}>None. Alerts appear when the network memory sees a card at three stores.</p>
            : alerts.map((a) => (
              <div className="list-row" key={`${a.token}-${a.t}`}>
                <span><span className="mono">{a.token}</span> at {a.merchants.join(', ')}</span>
              </div>
            ))}
        </div>

        <div>
          <h3 className="section-title">Recent approved payments</h3>
          {payments.length === 0 ? <p className="faint" style={{ margin: 0 }}>None yet. Approved payments appear here and can be charged back.</p>
            : payments.map((p) => (
              <div className="list-row" key={p.auth_code}>
                <span>{formatMoney(p.amount)} · {p.store} · <span className="mono">{p.auth_code}</span>{p.disputed ? ' · charged back' : ''}</span>
                {p.disputed ? (
                  <button type="button" className="small-btn" onClick={() => void act(() => api.evidence(p.auth_code),
                    (r) => `Draft (${String((r.draft as { by?: string })?.by ?? '')}), a person approves before sending: ${String((r.draft as { text?: string })?.text ?? '')}`)}>
                    Draft dispute response
                  </button>
                ) : (
                  <button type="button" className="small-btn" onClick={() => void act(() => api.dispute(p.auth_code),
                    () => 'Marked as fraud: it is now a training label on this node.')}>
                    Charge back
                  </button>
                )}
              </div>
            ))}
        </div>

        <div>
          <h3 className="section-title">Disclosure ledger
            <small>{ledger ? `chain ${ledger.chain_ok ? 'intact' : 'BROKEN'} · card-like values: ${ledger.card_numbers_seen_by_coordinator}` : ''}</small>
          </h3>
          {ledger?.entries.slice().reverse().map((e, i) => (
            <div className="ledger-row" key={`${e.t}-${i}`}>
              <span className={`status--${e.status}`}>{e.status.toLowerCase()}</span>
              <span>
                {e.source} → {e.purpose}:{' '}
                {e.fields ? Object.entries(e.fields).map(([k, v]) => `${k}=${v}`).join(' ') : e.reason}
              </span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
