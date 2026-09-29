import { motion } from 'motion/react';
import type { Channel, Transfer } from '../investigation/derive';
import { route, STAGE } from '../investigation/layout';

export const CHANNEL_COLOR: Record<Channel, string> = {
  stripe: '#c9d0e3', signed: '#8fb0d6', grid: '#a399ff', inprocess: '#b4bdd6', state: '#8a94b3',
  code: '#f2f4fa', review: '#ffb23f',
};

/** The stage's own ground colour: labels and markers are knocked out of it so lines never cross text. */
export const STAGE_BG = '#0a0f1f';

export const TRAVEL_MS = 560;

/** One real message in flight: from its source toward its destination, or to the boundary that stopped it. */
export function MessagePacket({ transfer, reduced }: { transfer: Transfer; reduced: boolean }) {
  const { start, end } = route(transfer.edge, transfer.from, transfer.stopAt, transfer.to);
  const color = transfer.blocked ? '#ff6b5e' : CHANNEL_COLOR[transfer.channel];
  const label = transfer.label.length > 34 ? `${transfer.label.slice(0, 33)}…` : transfer.label;
  const vertical = Math.abs(end[0] - start[0]) < Math.abs(end[1] - start[1]);
  const leftSide = vertical && start[0] > 760; // near the stage's right edge the label goes on the left
  const rightward = end[0] >= start[0];
  const duration = reduced ? 0 : (transfer.blocked ? TRAVEL_MS * 0.8 : TRAVEL_MS) / 1000;
  // the label sits on its own solid pill so it stays readable over nodes and lines
  const w = label.length * 8 + 16;
  // trail the packet, unless that would push the pill off the stage at its destination
  let anchor: 'start' | 'end' = vertical ? (leftSide ? 'end' : 'start') : rightward ? 'end' : 'start';
  if (anchor === 'end' && end[0] - w < 4) anchor = 'start';
  if (anchor === 'start' && end[0] + w > STAGE.width - 4) anchor = 'end';
  const tx = vertical ? (anchor === 'end' ? -18 : 18) : anchor === 'end' ? 12 : -12;
  const ty = vertical ? 5 : 30;
  const rx = anchor === 'start' ? tx - 8 : tx - w + 8;
  return (
    <motion.g
      initial={{ x: start[0], y: start[1], opacity: 0 }}
      animate={{ x: end[0], y: end[1], opacity: 1 }}
      exit={{ opacity: 0, transition: { duration: 0.2 } }}
      transition={{ duration, ease: [0.45, 0, 0.2, 1], opacity: { duration: 0.12 } }}
    >
      <circle r={14} fill={color} opacity={0.18} />
      <rect x={-9} y={-5.5} width={18} height={11} rx={5.5} fill={color} />
      <rect x={rx} y={ty - 15} width={w} height={22} rx={11} fill={STAGE_BG} stroke={color} strokeOpacity={0.45} />
      <text className="packet-label" x={tx} y={ty} textAnchor={anchor} fill={color}>
        {label}
      </text>
    </motion.g>
  );
}
