import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react';
import { useReducedMotion } from 'motion/react';
import { api, newTraceId } from '../api/client';
import type { CheckoutRequest, CheckoutResult, Config, TraceEvent } from '../api/types';
import { derive, dwell, type Investigation } from './derive';

// One typed stream: `events` is exactly what GET /trace returned, in order. `applied` is how many of
// them the picture shows. The player only ever moves `applied` forward toward events.length, at a
// pace a person can follow; it never invents a step.

export type RunStatus = 'idle' | 'tokenizing' | 'running' | 'error';

interface State {
  status: RunStatus;
  traceId: string | null;
  events: TraceEvent[];
  applied: number;
  serverDone: boolean;
  rawCardShared: number | null;
  result: CheckoutResult | null;
  error: string | null;
  ciphertext: string | null;
  runs: number;
}

type Action =
  | { type: 'tokenizing' }
  | { type: 'start'; traceId: string; ciphertext: string }
  | { type: 'page'; events: TraceEvent[]; done: boolean; rawCardShared?: number }
  | { type: 'advance' }
  | { type: 'skip' }
  | { type: 'replay' }
  | { type: 'result'; result: CheckoutResult; traceId?: string }
  | { type: 'error'; error: string };

const initial: State = {
  status: 'idle', traceId: null, events: [], applied: 0, serverDone: false, rawCardShared: null,
  result: null, error: null, ciphertext: null, runs: 0,
};

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case 'tokenizing':
      return { ...initial, status: 'tokenizing', runs: s.runs };
    case 'start':
      return { ...initial, status: 'running', traceId: a.traceId, ciphertext: a.ciphertext, runs: s.runs + 1 };
    case 'page': {
      const known = new Set(s.events.map((e) => e.seq));
      const fresh = a.events.filter((e) => !known.has(e.seq));
      return {
        ...s, events: fresh.length ? [...s.events, ...fresh].sort((x, y) => x.seq - y.seq) : s.events,
        serverDone: a.done, rawCardShared: a.rawCardShared ?? s.rawCardShared,
      };
    }
    case 'advance':
      return s.applied < s.events.length ? { ...s, applied: s.applied + 1 } : s;
    case 'skip':
      return { ...s, applied: s.events.length };
    case 'replay':
      return { ...s, applied: 0 };
    case 'result': // a late answer to an earlier checkout never lands in a newer one
      return a.traceId && a.traceId !== s.traceId ? s : { ...s, result: a.result };
    case 'error':
      return { ...s, status: s.traceId ? 'running' : 'error', error: a.error };
  }
}

export type RunInput = Omit<CheckoutRequest, 'blob' | 'trace_id'>;

export function useInvestigation(config: Config | null) {
  const [state, dispatch] = useReducer(reducer, initial);
  const reduced = useReducedMotion() ?? false;
  const lastSeq = useRef(-1);

  const lastOutcome = [...state.events].reverse().find((e) => e.kind === 'outcome');
  const awaitingHuman = lastOutcome?.outcome === 'needs_review';
  // Finished: a final outcome arrived, or the checkout answered and its trace closed without one
  // (a refusal before any step, e.g. the rate limit), or nothing was ever traced.
  const finished = (lastOutcome !== undefined && !awaitingHuman)
    || (state.serverDone && state.result !== null && state.result.outcome !== 'needs_review' && !awaitingHuman)
    || (state.error !== null && state.events.length === 0);

  // Poll the trace: fast while the decision runs, slowly while a person decides, not at all after.
  useEffect(() => {
    if (!state.traceId || finished) return;
    let cancelled = false;
    const id = state.traceId;
    const tick = async () => {
      try {
        const page = await api.trace(id, lastSeq.current);
        if (cancelled) return;
        const newest = page.events.at(-1);
        if (newest) lastSeq.current = newest.seq;
        dispatch({ type: 'page', events: page.events, done: page.done, rawCardShared: page.card_numbers_seen_by_coordinator });
      } catch {
        /* the next tick retries; the picture simply waits */
      }
    };
    void tick();
    const timer = window.setInterval(tick, awaitingHuman ? 1200 : 300);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [state.traceId, finished, awaitingHuman]);

  // The player: apply the next real event after the previous one has had time to be seen.
  useEffect(() => {
    if (state.applied >= state.events.length) return;
    const prev = state.applied > 0 ? state.events[state.applied - 1] : undefined;
    const wait = state.applied === 0 ? 0 : dwell(prev) * (reduced ? 0.3 : 1);
    const timer = window.setTimeout(() => dispatch({ type: 'advance' }), wait);
    return () => window.clearTimeout(timer);
  }, [state.applied, state.events, reduced]);

  const mode: 'flower' | 'in-process' = config?.federation ? 'flower' : 'in-process';
  const vocabulary = config?.wire_vocabulary;
  const view: Investigation = useMemo(
    () => derive(state.events.slice(0, state.applied), mode, vocabulary ?? {}, state.rawCardShared),
    [state.events, state.applied, mode, vocabulary, state.rawCardShared],
  );
  // The picture one event earlier: a message's destination keeps this until the message lands.
  const prevView: Investigation = useMemo(
    () => derive(state.events.slice(0, Math.max(0, state.applied - 1)), mode, vocabulary ?? {}, state.rawCardShared),
    [state.events, state.applied, mode, vocabulary, state.rawCardShared],
  );
  // the latest gate: after a Flower fallback the store node's own gate supersedes the unbound one
  const gateKey = [...state.events.slice(0, state.applied)].reverse().find((e) => e.kind === 'gate.decision')?.seq ?? -1;

  const run = useCallback(async (input: RunInput, getCiphertext: () => Promise<string>): Promise<CheckoutResult | null> => {
    dispatch({ type: 'tokenizing' });
    let blob: string;
    try {
      blob = await getCiphertext();
    } catch (err) {
      dispatch({ type: 'error', error: err instanceof Error ? err.message : String(err) });
      return null;
    }
    const traceId = newTraceId();
    lastSeq.current = -1;
    dispatch({ type: 'start', traceId, ciphertext: blob });
    try {
      const result = await api.checkout({ ...input, blob, trace_id: traceId });
      dispatch({ type: 'result', result, traceId });
      if (result.error) dispatch({ type: 'error', error: result.error });
      return result;
    } catch (err) {
      dispatch({ type: 'error', error: err instanceof Error ? err.message : String(err) });
      return null;
    }
  }, []);

  const decide = useCallback(async (reviewId: string, action: 'approve' | 'decline') => {
    const result = await api.review(reviewId, action);
    if (result.error) throw new Error(result.error);
    dispatch({ type: 'result', result });
    return result;
  }, []);

  return {
    state,
    view,
    prevView,
    gateKey,
    live: state.applied < state.events.length || (state.status === 'running' && !finished && !awaitingHuman),
    awaitingHuman,
    finished,
    run,
    decide,
    skip: () => dispatch({ type: 'skip' }),
    replay: () => dispatch({ type: 'replay' }),
  };
}
