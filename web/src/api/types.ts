// Shapes the merchant node sends to this page. Every value here is a band from the closed wire
// vocabulary, a count, an id, a fixed reason string, or ciphertext: the page never receives card data.

export interface Config {
  merchant_id: string;
  vertical: string;
  federation: string | null;
  stores: string[];
  port?: number;
  peers?: { store: string; url: string }[]; // the other merchant nodes of this demo network, one SuperNode each
  bank_attestation: boolean; // a bank attestation node is configured (it never processes payments)
  publishable_key: string; // Stripe TEST publishable key
  demo_controls: boolean;
  wire_vocabulary: Record<string, string[]>;
  amount_cuts: { medium_from: number; high_from: number; basis: string };
}

export type Bands = Record<string, string>;

export interface GateLine {
  rule: string;
  state: 'pass' | 'warn' | 'fail' | 'info';
  text: string;
}

export interface PartySignal {
  party: 'stripe' | 'bank' | 'store' | 'network';
  facts: Bands;
  points: number;
  hard: boolean;
  floor: boolean;
  level: 'low' | 'medium' | 'high';
}

export type Decision = 'approve' | 'step_up' | 'decline';

/** One step of the live investigation (GET /trace/<id>). Optional fields depend on `kind`. */
export interface TraceEvent {
  seq: number;
  t: number; // ms since the checkout started
  kind: string;
  src?: string;
  dst?: string;
  round?: number;
  status?: string;
  reason?: string;
  detail?: string;
  via?: 'flower' | 'in-process' | string;
  purpose?: string;
  node?: string;
  nodes?: string[];
  run_id?: string;
  bytes?: number;
  latency_ms?: number;
  evidence?: Bands | null;
  private?: Record<string, string | number>;
  attack?: string | null;
  amount_cents?: number;
  store?: string;
  processor?: string;
  federation?: string | null;
  stores?: number;
  outcome?: string;
  charged?: boolean;
  approve?: boolean;
  auth_code?: string | null;
  review_id?: string;
  action?: string;
  tampered?: boolean;
  // gate.decision
  decision?: Decision;
  decided_by?: string;
  score?: number | null;
  lines?: GateLine[];
  contributions?: { fact: string; points: number; party: string }[];
  parties?: PartySignal[];
  round_2?: boolean;
  explanation?: { text: string; by: string };
}

export interface TracePage {
  events: TraceEvent[];
  done: boolean;
  known: boolean;
  card_numbers_seen_by_coordinator?: number;
}

export interface CheckoutRequest {
  blob: string; // a Stripe payment-method id (pm_...), never a card
  amount_cents: number;
  buyer_country: string;
  store?: string;
  attack: string | null;
  model_agent: boolean;
  gift_message: string;
  trace_id: string;
}

export interface CheckoutResult {
  outcome: string;
  verdict?: { decision?: string; decided_by?: string; network?: { band: string; merchants: string[] } };
  reason?: string;
  review_id?: string;
  charged?: boolean;
  error?: string;
  hint?: string;
  blocked?: string[];
  payment?: { status: string; auth_code?: string; reason?: string };
}

export interface LedgerEntry {
  status: 'DISCLOSED' | 'BLOCKED';
  source: string;
  purpose: string;
  fields?: Bands;
  reason?: string;
  note?: Record<string, string>;
  t: number;
}

export interface LedgerPage {
  entries: LedgerEntry[];
  chain_ok: boolean;
  chain_head: string;
  card_numbers_seen_by_coordinator: number;
}

export interface PaymentRow {
  auth_code: string;
  amount: number;
  store: string;
  disputed: boolean;
}

export interface AlertRow {
  token: string;
  merchants: string[];
  t: number;
}

export interface NodesInfo {
  count: number;
  labels: number;
  dp: { epsilon: number; delta: number; noise_multiplier: number; clipping_norm: number } | null;
}
