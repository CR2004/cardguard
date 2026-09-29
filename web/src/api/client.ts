import type {
  AlertRow, CheckoutRequest, CheckoutResult, Config, LedgerPage, NodesInfo, PaymentRow, TracePage,
} from './types';

async function json<T>(res: Response): Promise<T> {
  const body = (await res.json().catch(() => ({}))) as T & { error?: string };
  if (!res.ok && !(body && typeof body === 'object' && 'outcome' in body)) {
    throw new Error(body?.error || `${res.status} ${res.statusText}`);
  }
  return body;
}

const get = <T,>(path: string) => fetch(path, { cache: 'no-store' }).then((r) => json<T>(r));

const post = <T,>(path: string, body?: unknown, headers: Record<string, string> = {}) =>
  fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body ?? {}),
  }).then((r) => json<T>(r));

// Human actions carry the reviewer credential that run_demo.py prints. It lives in this tab only.
const REVIEWER_KEY = 'cardguard.reviewer';
export function reviewerToken(): string {
  try {
    return sessionStorage.getItem(REVIEWER_KEY) ?? '';
  } catch {
    return '';
  }
}
export function setReviewerToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(REVIEWER_KEY, token);
    else sessionStorage.removeItem(REVIEWER_KEY);
  } catch {
    /* storage blocked: the token is asked again next time */
  }
}
const asReviewer = () => ({ Authorization: `Bearer ${reviewerToken()}` });

export const api = {
  config: () => get<Config>('/config'),
  checkout: (body: CheckoutRequest) => post<CheckoutResult>('/checkout', body),
  trace: (id: string, after: number) => get<TracePage>(`/trace/${id}?after=${after}`),
  review: (id: string, action: 'approve' | 'decline') =>
    post<CheckoutResult>(`/reviews/${id}/${action}`, {}, asReviewer()),
  ledger: () => get<LedgerPage>('/ledger'),
  payments: () => get<PaymentRow[]>('/payments'),
  alerts: () => get<{ alerts: AlertRow[] }>('/alerts'),
  nodes: () => get<NodesInfo>('/agent/nodes'),
  retrain: () =>
    post<{ nodes: number; labels: number; before: string[]; after: string[]; error?: string }>(
      '/agent/retrain', {}, asReviewer()),
  dispute: (auth: string) => post<{ disputed?: string; error?: string }>(`/payments/${auth}/dispute`, {}, asReviewer()),
  evidence: (auth: string) =>
    fetch(`/payments/${auth}/evidence`, { headers: asReviewer() }).then((r) =>
      json<{ draft?: { text: string; by: string }; error?: string }>(r)),
};

/** Trace ids are minted here, letters a-p only, so they can never resemble a card number. */
export function newTraceId(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => 'abcdefghijklmnop'[b & 15]).join('');
}
