import { motion } from 'motion/react';
import type { Channel, Transfer } from '../investigation/derive';
import { route } from '../investigation/layout';

export const CHANNEL_COLOR: Record<Channel, string> = {
  stripe: '#b8c2d9', signed: '#8fa6c4', grid: '#9aa5ff', inprocess: '#a3aecb', state: '#7f8aa8',
  code: '#e9edf7', review: '#f2b84b',
};

export const TRAVEL_MS = 560;

/** One real message in flight: from its source toward its destination, or to the boundary that stopped it. */
export function MessagePacket({ transfer, reduced }: { transfer: Transfer; reduced: boolean }) {
  const { start, end } = route(transfer.edge, transfer.from, transfer.stopAt, transfer.to);
  const color = transfer.blocked ? '#ff7a66' : CHANNEL_COLOR[transfer.channel];
  const label = transfer.label.length > 34 ? `${transfer.label.slice(0, 33)}…` : transfer.label;
  const vertical = Math.abs(end[0] - start[0]) < Math.abs(end[1] - start[1]);
  const leftSide = vertical && start[0] > 820; // near the stage's right edge the label goes on the left
  const duration = reduced ? 0 : (transfer.blocked ? TRAVEL_MS * 0.8 : TRAVEL_MS) / 1000;
  return (
    <motion.g
      initial={{ x: start[0], y: start[1], opacity: 0 }}
      animate={{ x: end[0], y: end[1], opacity: 1 }}
      exit={{ opacity: 0, transition: { duration: 0.2 } }}
      transition={{ duration, ease: [0.45, 0, 0.2, 1], opacity: { duration: 0.12 } }}
    >
      <circle r={11} fill={color} opacity={0.16} />
      <rect x={-8} y={-5} width={16} height={10} rx={5} fill={color} />
      <text
        className="packet-label"
        x={vertical ? (leftSide ? -14 : 14) : 0}
        y={vertical ? 4 : 24}
        textAnchor={vertical ? (leftSide ? 'end' : 'start') : 'middle'}
        fill={color}
        stroke="#0a1020"
        strokeWidth={3}
        paintOrder="stroke"
      >
        {label}
      </text>
    </motion.g>
  );
}
