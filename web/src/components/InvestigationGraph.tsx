import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { Building2, CreditCard, Landmark, Network, ShieldCheck, Split, Store, UserRound } from 'lucide-react';
import { formatMoney } from '../format';
import type { Channel, EdgeId, Investigation, NodeId, Party, Transfer } from '../investigation/derive';
import { sitsOut } from '../investigation/derive';
import { EDGES, NODE_POS, REGIONS, route, STAGE } from '../investigation/layout';
import { AgentNode } from './AgentNode';
import { EvidenceChip } from './EvidenceChip';
import { StatePill } from './StatePill';
import { CHANNEL_COLOR, MessagePacket, TRAVEL_MS } from './MessagePacket';
import { PolicyGate } from './PolicyGate';

const CHANNEL_NAME: Record<Channel, string> = {
  stripe: '', signed: 'Local signed channel', grid: 'Flower Grid', inprocess: 'In-process',
  state: 'Coordinator state', code: 'In code', review: 'Held for review',
};

function useFitScale(ref: React.RefObject<HTMLDivElement | null>) {
  const [scale, setScale] = useState(1);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fit = () => {
      const s = Math.min(el.clientWidth / STAGE.width, el.clientHeight / STAGE.height);
      setScale(Math.max(0.2, s * 0.98));
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return scale;
}

/** The destination only reacts once the message has landed: keep its previous view until then. */
function useLanded(transfer: Transfer | null, reduced: boolean) {
  const [landedSeq, setLandedSeq] = useState<number | null>(null);
  useEffect(() => {
    if (!transfer) return;
    const timer = window.setTimeout(() => setLandedSeq(transfer.seq), reduced ? 0 : TRAVEL_MS);
    return () => window.clearTimeout(timer);
  }, [transfer, reduced]);
  return (id: NodeId) => !transfer || transfer.blocked || transfer.to !== id || landedSeq === transfer.seq;
}

function Edge({ id, view, active }: { id: EdgeId; view: Investigation; active: Transfer | null }) {
  const g = EDGES[id];
  const e = view.edges[id];
  const isActive = active?.edge === id;
  const color = CHANNEL_COLOR[e.channel];
  const [x1, y1] = g.from;
  const [x2, y2] = g.to;
  const opacity = isActive ? 1 : e.used ? 0.6 : 0.28;
  const width = isActive ? 2.4 : e.used ? 1.5 : 1.2;
  const grid = e.channel === 'grid';
  const dash = e.channel === 'state' || e.channel === 'inprocess' ? '2 5' : e.channel === 'review' ? '6 5' : undefined;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.hypot(dx, dy) || 1;
  const nx = (-dy / len) * 3.2;
  const ny = (dx / len) * 3.2;
  const label = CHANNEL_NAME[e.channel];
  const [lx, ly] = g.label;
  const vertical = Math.abs(dx) < Math.abs(dy);
  return (
    <g>
      {grid ? (
        <>
          <line x1={x1 + nx} y1={y1 + ny} x2={x2 + nx} y2={y2 + ny} className="edge" stroke={color} strokeWidth={width * 0.8} opacity={opacity} />
          <line x1={x1 - nx} y1={y1 - ny} x2={x2 - nx} y2={y2 - ny} className="edge" stroke={color} strokeWidth={width * 0.8} opacity={opacity} />
        </>
      ) : (
        <line x1={x1} y1={y1} x2={x2} y2={y2} className="edge" stroke={isActive || e.used ? color : '#2f3f63'}
          strokeWidth={width} strokeDasharray={dash} opacity={isActive || e.used ? opacity : 0.9} />
      )}
      {e.channel === 'signed' && (
        // stitched ticks: a signed, authenticated channel
        Array.from({ length: Math.floor(len / 12) }, (_, i) => {
          const t = (i + 0.5) / Math.floor(len / 12);
          const px = x1 + dx * t;
          const py = y1 + dy * t;
          return <line key={i} x1={px - nx * 1.3} y1={py - ny * 1.3} x2={px + nx * 1.3} y2={py + ny * 1.3}
            stroke={color} strokeWidth={1} opacity={opacity * 0.9} />;
        })
      )}
      {isActive && !active?.blocked && (
        <motion.line
          key={active?.seq}
          x1={x1} y1={y1} x2={x2} y2={y2}
          stroke={color} strokeWidth={6} strokeLinecap="round" opacity={0.18}
          initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: TRAVEL_MS / 1000 }}
        />
      )}
      {Object.values(g.crossings).map(([cx, cy], i) => (
        <g key={i} aria-hidden>
          <rect x={cx - 4} y={cy - 4} width={8} height={8} rx={2} transform={`rotate(45 ${cx} ${cy})`} fill="#0a1020"
            stroke={e.blocked ? '#ff7a66' : '#cdbb93'} strokeWidth={1.1} opacity={0.9} />
        </g>
      ))}
      <g transform={`translate(${lx} ${ly})`}>
        <text className={`edge-label edge-label--${e.channel}`} textAnchor={vertical ? 'start' : 'middle'}
          x={vertical ? 10 : 0} y={vertical ? 4 : 0} fill={e.used || isActive ? color : '#6f7b9a'}
          stroke="#0a1020" strokeWidth={4} paintOrder="stroke">
          {label}{id === 'store-coordinator' && view.metrics.flowerMessages > 0 ? ` · ${view.metrics.flowerMessages} msgs` : ''}
        </text>
      </g>
    </g>
  );
}

function BlockedStubs({ view }: { view: Investigation }) {
  const byEdge = new Map<string, { t: Transfer; count: number }>();
  for (const t of view.blocked) {
    const key = `${t.edge}|${t.from}|${t.stopAt}`;
    const cur = byEdge.get(key);
    byEdge.set(key, { t, count: (cur?.count ?? 0) + 1 });
  }
  return (
    <>
      {[...byEdge.values()].map(({ t, count }) => {
        const { start, end } = route(t.edge, t.from, t.stopAt, t.to);
        return (
          <g key={`${t.edge}-${t.from}-${t.stopAt}`}>
            <line x1={start[0]} y1={start[1]} x2={end[0]} y2={end[1]} stroke="#ff7a66" strokeWidth={2} strokeDasharray="4 4" />
            <motion.g initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ delay: 0.4, duration: 0.2 }}
              style={{ originX: `${end[0]}px`, originY: `${end[1]}px` }}>
              <circle cx={end[0]} cy={end[1]} r={11} fill="#0a1020" stroke="#ff7a66" strokeWidth={2} />
              <path d={`M${end[0] - 4} ${end[1] - 4}L${end[0] + 4} ${end[1] + 4}M${end[0] + 4} ${end[1] - 4}L${end[0] - 4} ${end[1] + 4}`}
                stroke="#ff7a66" strokeWidth={2.2} strokeLinecap="round" />
              {count > 1 && (
                <text x={end[0] + 14} y={end[1] + 18} fill="#ff7a66" fontSize={11} fontWeight={700}
                  stroke="#0a1020" strokeWidth={3} paintOrder="stroke">×{count}</text>
              )}
            </motion.g>
          </g>
        );
      })}
    </>
  );
}

function Callout({ view }: { view: Investigation }) {
  const last = view.blocked.at(-1);
  if (!last) return null;
  const { start, end } = route(last.edge, last.from, last.stopAt, last.to);
  // Beside a vertical edge, below a horizontal one, in the open space between organizations.
  const vertical = Math.abs(end[0] - start[0]) < Math.abs(end[1] - start[1]);
  const place = vertical ? { left: end[0] + 18, top: end[1] } : { left: 467, top: end[1] + 20 };
  // the wrapper owns the placement transform; motion owns only the inner element's transform
  return (
    <div className={`anchor ${vertical ? 'anchor--right' : 'anchor--below'}`} style={place}>
      <motion.div
        key={last.seq}
        className="callout"
        initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.45, duration: 0.2 }}
        role="alert"
      >
        <b>REJECTED AT THE BOUNDARY</b>
        {last.reason ?? 'refused'}
      </motion.div>
    </div>
  );
}

interface Props {
  view: Investigation;
  prev: Investigation;
  reduced: boolean;
  buyerCountry?: string;
  gateKey: number;
}

export function InvestigationGraph({ view, prev, reduced, buyerCountry, gateKey }: Props) {
  const fitRef = useRef<HTMLDivElement>(null);
  const scale = useFitScale(fitRef);
  const landed = useLanded(view.transfer, reduced);
  const nodeView = (id: NodeId) => (landed(id) ? view.nodes[id] : prev.nodes[id]);
  const chipsFor = (party: Party) => view.evidence.filter((x) => x.party === party);
  const flower = view.mode === 'flower';
  const stopped = !view.gate && view.outcome && ['blocked', 'processor_rejected', 'no_model_endpoint'].includes(view.outcome.outcome)
    ? (view.outcome.outcome === 'processor_rejected' ? 'Stripe refused the payment method: no evidence, nothing charged'
      : view.outcome.outcome === 'no_model_endpoint' ? 'No model endpoint for the model-driven agent: nothing charged'
        : 'A message was rejected at the boundary: nothing charged')
    : undefined;
  const sp = view.storePrivate;

  return (
    <div className="stage-fit" ref={fitRef}>
      <div className="stage" style={{ transform: `translate(-50%, -50%) scale(${scale})` }}>
        {/* organization boundaries: private data is drawn inside, only attestations cross */}
        <div className="region" style={{ left: REGIONS.bank.x, top: REGIONS.bank.y, width: REGIONS.bank.w, height: REGIONS.bank.h }}>
          <div className="region__label"><Landmark size={11} aria-hidden /> Issuing bank</div>
        </div>
        <div className="region" style={{ left: REGIONS.merchant.x, top: REGIONS.merchant.y, width: REGIONS.merchant.w, height: REGIONS.merchant.h }}>
          <div className="region__label"><Building2 size={11} aria-hidden /> Merchant</div>
        </div>
        <div className={`region ${flower ? 'region--flower' : 'region--local'}`}
          style={{ left: REGIONS.coordinator.x, top: REGIONS.coordinator.y, width: REGIONS.coordinator.w, height: REGIONS.coordinator.h }}>
          <div className="region__label">
            {flower ? <>Flower SuperLink · AgentApp</> : <>Coordinator · in-process</>}
          </div>
        </div>

        <svg className="edges" width={STAGE.width} height={STAGE.height} viewBox={`0 0 ${STAGE.width} ${STAGE.height}`} aria-hidden>
          {(Object.keys(EDGES) as EdgeId[]).map((id) => <Edge key={id} id={id} view={view} active={view.transfer} />)}
          <BlockedStubs view={view} />
        </svg>

        {/* the payment rail: card entry and payment state, never a party to the investigation */}
        <section className={`tx${sitsOut(view, 'tx') ? ' sits-out' : ''}`} style={{ left: NODE_POS.tx.left, top: NODE_POS.tx.top }}
          data-status={nodeView('tx').status} aria-label={`Stripe: ${nodeView('tx').status}. ${nodeView('tx').action}`}>
          <div className="tx__label"><CreditCard size={12} aria-hidden /> Stripe <span className="faint">TEST</span></div>
          <div className="tx__amount">{view.amountCents !== undefined ? formatMoney(view.amountCents) : '—'}</div>
          <StatePill status={nodeView('tx').status} />
          <div className="tx__meta">{nodeView('tx').action}</div>
          {buyerCountry && <div className="tx__meta faint">buyer IP {buyerCountry}</div>}
          <div className="chips">{chipsFor('stripe').map((c) => <EvidenceChip key={c.key} item={c} compact />)}</div>
        </section>

        <AgentNode
          id="bank" title="Bank" subtitle="Attestation only · moves no money" icon={<Landmark size={15} />}
          view={nodeView('bank')}
          vault={{ title: 'Private · never leaves the bank', items: [
            { label: 'Last in-person use (city, time)' }, { label: 'Recent declines' }, { label: 'Cardholder behaviour' },
          ] }}
          chips={chipsFor('bank')}
          sitsOut={sitsOut(view, 'bank')}
        />
        <AgentNode
          id="store" title="Store" subtitle={flower ? 'Merchant node + SuperNode' : 'Merchant node'} icon={<Store size={15} />}
          view={nodeView('store')}
          vault={{ title: 'Private · stays in the store', items: [
            { label: 'Exact amount', value: view.amountCents !== undefined ? formatMoney(view.amountCents) : undefined },
            { label: 'Buyer IP region', value: sp.buyer_region !== undefined ? String(sp.buyer_region) : undefined },
            { label: 'This card here, 24 h', value: sp.purchases_here_24h !== undefined ? `${sp.purchases_here_24h} before` : undefined },
          ] }}
          chips={chipsFor('store')}
          sitsOut={sitsOut(view, 'store')}
        />
        <AgentNode
          id="network" title="Network memory" subtitle="Coordinator state" icon={<Network size={15} />}
          view={nodeView('network')}
          chips={chipsFor('network')}
          sitsOut={sitsOut(view, 'network')}
          className="node--compact"
        />
        <AgentNode
          id="coordinator" title="Coordinator" subtitle={flower ? 'Flower AgentApp' : 'In the store’s process'}
          icon={flower ? <FlowerMark /> : <ShieldCheck size={15} />}
          view={nodeView('coordinator')}
          className={flower ? '' : 'node--local'}
          extra={
            <div className="node__meta">
              {view.runId && <span className="mono">run {view.runId.slice(-6)}</span>}
              {view.supernodes.length > 0 && <span>{view.supernodes.length} SuperNode{view.supernodes.length === 1 ? '' : 's'}</span>}
              <span>{view.metrics.verifiedEvidence} verified</span>
              {view.round > 0 && <span>round {view.round}</span>}
            </div>
          }
        />
        <AgentNode
          id="human" title="Reviewer" subtitle="At the merchant" icon={<UserRound size={15} />}
          view={nodeView('human')}
          className="human"
        />

        <AnimatePresence>
          {view.phase === 'conflict' && view.conflict && (
            <div className="anchor anchor--below" style={{ left: 662, top: 388 }}>
              <motion.div
                className="conflict-banner"
                initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.25 }}
                role="status"
              >
                <Split size={14} aria-hidden /> Evidence conflict · follow-up required
              </motion.div>
            </div>
          )}
        </AnimatePresence>

        <PolicyGate gate={view.gate} human={view.review?.decided} stopped={stopped} evaluatingKey={gateKey} reduced={reduced} />
        {/* messages travel above the cards so their labels stay readable */}
        <svg className="edges packets" width={STAGE.width} height={STAGE.height} viewBox={`0 0 ${STAGE.width} ${STAGE.height}`} aria-hidden>
          <AnimatePresence>
            {view.transfer && <MessagePacket key={view.transfer.seq} transfer={view.transfer} reduced={reduced} />}
          </AnimatePresence>
        </svg>
        <Callout view={view} />
      </div>
    </div>
  );
}

function FlowerMark() {
  // a simple five-petal mark for Flower-run components (not the Flower logo)
  return (
    <svg width={15} height={15} viewBox="0 0 16 16" aria-hidden>
      {[0, 72, 144, 216, 288].map((a) => (
        <ellipse key={a} cx={8} cy={4.2} rx={2.3} ry={3.6} fill="currentColor" opacity={0.85} transform={`rotate(${a} 8 8)`} />
      ))}
      <circle cx={8} cy={8} r={1.8} fill="#0a1020" />
    </svg>
  );
}
