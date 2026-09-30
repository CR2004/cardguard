import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Which credential a human action carries. Demo controls: the node's HttpOnly session cookie (sent by the
// browser, unreadable here) plus a header; never the reviewer token. Otherwise: the token the reviewer typed.
let fetchMock: ReturnType<typeof vi.fn>;
let store: Map<string, string>;

beforeEach(() => {
  vi.resetModules(); // the demo switch is module state: every test starts from a fresh page
  store = new Map();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
  vi.stubGlobal('sessionStorage', { getItem: () => null, removeItem: () => undefined });
  fetchMock = vi.fn(async () => new Response(JSON.stringify({ outcome: 'approved_by_human' }), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const sentHeaders = (call = 0) =>
  ((fetchMock.mock.calls[call]?.[1] as RequestInit | undefined)?.headers ?? {}) as Record<string, string>;

describe('reviewer credential', () => {
  it('demo controls: the session header, no Authorization, and a stored token is dropped', async () => {
    store.set('cardguard.reviewer', 'stored-by-an-older-build');
    const { api, enableDemoReviewerSession } = await import('./client');
    enableDemoReviewerSession();
    expect(store.has('cardguard.reviewer')).toBe(false);
    await api.review('rid', 'approve');
    await api.review('rid', 'decline');
    await api.dispute('auth');
    for (const call of [0, 1, 2]) {
      expect(sentHeaders(call)['X-CardGuard-Demo-Reviewer']).toBe('1');
      expect(sentHeaders(call)).not.toHaveProperty('Authorization');
    }
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(['/reviews/rid/approve', '/reviews/rid/decline', '/payments/auth/dispute']);
  });

  it('without demo controls: the typed token as a Bearer credential, no demo header', async () => {
    const { api, setReviewerToken } = await import('./client');
    setReviewerToken('typed-reviewer-token');
    await api.review('rid', 'approve');
    expect(sentHeaders().Authorization).toBe('Bearer typed-reviewer-token');
    expect(sentHeaders()).not.toHaveProperty('X-CardGuard-Demo-Reviewer');
  });

  it('a refused credential rejects with the node\'s message, which the review panel recognises', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ error: 'reviewer token required' }), { status: 401 }));
    const { api, enableDemoReviewerSession } = await import('./client');
    enableDemoReviewerSession();
    await expect(api.review('rid', 'approve')).rejects.toThrow('reviewer token required');
  });
});
