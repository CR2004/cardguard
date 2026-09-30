// Stage geometry (design units; the stage is scaled to fit). Left to right is cause to effect.
import type { EdgeId, NodeId } from './derive';

export const STAGE = { width: 930, height: 770 };

export type Point = readonly [number, number];
export type RegionId = 'bank' | 'merchant' | 'coordinator';

export const REGIONS: Record<RegionId, { x: number; y: number; w: number; h: number }> = {
  bank: { x: 124, y: 10, w: 244, h: 280 },
  merchant: { x: 124, y: 330, w: 244, h: 430 },
  coordinator: { x: 486, y: 10, w: 236, h: 750 },
};

export const REGION_OF: Partial<Record<NodeId, RegionId>> = {
  bank: 'bank', store: 'merchant', coordinator: 'coordinator', network: 'coordinator',
};

/** Top-left of each node's box. */
export const NODE_POS: Record<NodeId, { left: number; top: number }> = {
  tx: { left: 0, top: 424 },
  bank: { left: 136, top: 26 },
  store: { left: 136, top: 346 },
  network: { left: 492, top: 36 },
  coordinator: { left: 498, top: 400 },
  gate: { left: 752, top: 262 },
  human: { left: 750, top: 36 },
};

interface EdgeGeom {
  a: NodeId;
  b: NodeId;
  from: Point; // at a
  to: Point; // at b
  label: Point;
  crossings: Partial<Record<RegionId, Point>>;
}

export const EDGES: Record<EdgeId, EdgeGeom> = {
  'tx-store': { a: 'tx', b: 'store', from: [112, 480], to: [136, 480], label: [124, 466], crossings: { merchant: [124, 480] } },
  'store-bank': { a: 'store', b: 'bank', from: [246, 346], to: [246, 280], label: [246, 312],
    crossings: { merchant: [246, 330], bank: [246, 290] } },
  'store-coordinator': { a: 'store', b: 'coordinator', from: [356, 470], to: [498, 470], label: [427, 452],
    crossings: { merchant: [368, 470], coordinator: [486, 470] } },
  'coordinator-network': { a: 'coordinator', b: 'network', from: [604, 400], to: [604, 214], label: [604, 310], crossings: {} },
  'coordinator-gate': { a: 'coordinator', b: 'gate', from: [710, 450], to: [784, 390], label: [752, 425],
    crossings: { coordinator: [722, 440] } },
  'gate-human': { a: 'gate', b: 'human', from: [837, 262], to: [837, 176], label: [837, 219], crossings: {} },
};

/** Start and end of a message on its edge; a blocked one ends at the boundary that stopped it. */
export function route(edge: EdgeId, from: NodeId, blockedAt?: 'source' | 'destination', to?: NodeId): { start: Point; end: Point } {
  const g = EDGES[edge];
  const forward = g.a === from;
  const start = forward ? g.from : g.to;
  let end = forward ? g.to : g.from;
  if (blockedAt) {
    const owner = blockedAt === 'source' ? from : to;
    const region = owner ? REGION_OF[owner] : undefined;
    const stop = region ? g.crossings[region] : undefined;
    if (stop) end = stop;
  }
  return { start, end };
}
