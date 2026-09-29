// Stage geometry (design units; the stage is scaled to fit). Left to right is cause to effect.
import type { EdgeId, NodeId } from './derive';

export const STAGE = { width: 1040, height: 760 };

export type Point = readonly [number, number];
export type RegionId = 'bank' | 'merchant' | 'coordinator';

export const REGIONS: Record<RegionId, { x: number; y: number; w: number; h: number }> = {
  bank: { x: 128, y: 10, w: 264, h: 322 },
  merchant: { x: 128, y: 380, w: 264, h: 370 },
  coordinator: { x: 542, y: 10, w: 240, h: 740 },
};

export const REGION_OF: Partial<Record<NodeId, RegionId>> = {
  bank: 'bank', store: 'merchant', coordinator: 'coordinator', network: 'coordinator',
};

/** Top-left of each node's box. */
export const NODE_POS: Record<NodeId, { left: number; top: number }> = {
  tx: { left: 0, top: 480 },
  bank: { left: 142, top: 28 },
  store: { left: 142, top: 396 },
  network: { left: 550, top: 40 },
  coordinator: { left: 550, top: 450 },
  gate: { left: 808, top: 300 },
  human: { left: 820, top: 30 },
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
  'tx-store': { a: 'tx', b: 'store', from: [112, 540], to: [142, 540], label: [127, 526], crossings: { merchant: [128, 540] } },
  'store-bank': { a: 'store', b: 'bank', from: [260, 396], to: [260, 322], label: [260, 356],
    crossings: { merchant: [260, 380], bank: [260, 332] } },
  'store-coordinator': { a: 'store', b: 'coordinator', from: [378, 520], to: [550, 520], label: [467, 500],
    crossings: { merchant: [392, 520], coordinator: [542, 520] } },
  'coordinator-network': { a: 'coordinator', b: 'network', from: [662, 450], to: [662, 214], label: [662, 330], crossings: {} },
  'coordinator-gate': { a: 'coordinator', b: 'gate', from: [774, 510], to: [846, 414], label: [812, 500],
    crossings: { coordinator: [782, 499] } },
  'gate-human': { a: 'gate', b: 'human', from: [918, 300], to: [918, 160], label: [918, 230], crossings: {} },
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
