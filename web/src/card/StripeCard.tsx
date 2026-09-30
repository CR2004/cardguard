import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { AlertCircle, Check, LockKeyhole } from 'lucide-react';
import { CopyButton } from '../components/IdChip';

// Stripe TEST mode is the only payment rail: the card goes from Stripe Elements (Stripe's own iframes)
// to Stripe. This page receives a payment-method id (pm_...), never the card. From the three fields it
// learns only safe UI state (card brand, which field has focus, complete or not, Stripe's error text).

export type FieldId = 'number' | 'expiry' | 'cvc';
const FIELDS: FieldId[] = ['number', 'expiry', 'cvc'];
const TYPE = { number: 'cardNumber', expiry: 'cardExpiry', cvc: 'cardCvc' } as const;

interface ElementChange { complete: boolean; empty: boolean; brand?: string; error?: { message?: string } }
interface StripeElement {
  mount: (el: HTMLElement) => void;
  destroy: () => void;
  on(event: 'change', handler: (e: ElementChange) => void): void;
  on(event: 'focus' | 'blur' | 'ready', handler: () => void): void;
}
interface StripeLike {
  elements: (opts?: object) => { create: (type: (typeof TYPE)[FieldId], opts?: object) => StripeElement };
  createPaymentMethod: (opts: { type: 'card'; card: StripeElement }) =>
    Promise<{ paymentMethod?: { id: string }; error?: { message?: string } }>;
}
declare global {
  interface Window { Stripe?: (key: string) => StripeLike }
}

export interface StripeCardHandle {
  tokenize: () => Promise<string>;
  isComplete: () => boolean;
}

/** What the visual card may react to. No digit ever leaves Stripe's iframes. */
export interface CardUi {
  ready: boolean;
  brand: string; // Stripe's brand for the number typed so far: 'visa', 'mastercard', 'amex', 'unknown', ...
  focus: FieldId | null;
  complete: Record<FieldId, boolean>;
  invalid: Record<FieldId, boolean>;
}
const none = { number: false, expiry: false, cvc: false };
export const EMPTY_CARD_UI: CardUi = { ready: false, brand: 'unknown', focus: null, complete: none, invalid: none };

// A real Stripe TEST publishable key. A placeholder such as "pk_test_x" (the test suite's fake) loads
// Stripe.js fine and fails only once a card is typed ("Invalid API Key provided"): refuse it up front.
const PUBLISHABLE_TEST_KEY = /^pk_test_[A-Za-z0-9]{24,}$/;
export function keyProblem(key: string | null | undefined): string | null {
  return key && PUBLISHABLE_TEST_KEY.test(key) ? null
    : 'Stripe is not configured on this store node: it has no real Stripe TEST publishable key (only a placeholder '
      + 'such as pk_test_x). Set STRIPE_PUBLISHABLE_KEY to the pk_test_… key from the Stripe dashboard and restart python run_demo.py.';
}

function loadStripe(): Promise<void> {
  if (window.Stripe) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://js.stripe.com/v3/';
    s.onload = () => resolve();
    s.onerror = () => reject(new Error('Stripe.js did not load (offline?)'));
    document.head.appendChild(s);
  });
}

const STYLE = {
  base: {
    color: '#F2F4FA', fontSize: '16px', iconColor: '#A399FF', fontWeight: '500', letterSpacing: '0.02em',
    fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif',
    '::placeholder': { color: '#6F7A99' },
  },
  invalid: { color: '#FF8A7F', iconColor: '#FF6B5E' },
};

// A published Stripe TEST number from the scenario hint, e.g. "Stripe test Visa 4242 4242 4242 4242".
const testNumber = (hint?: string) => hint?.match(/\d{4}(?: \d{4}){3}/)?.[0];

const LABEL: Record<FieldId, string> = { number: 'Card number', expiry: 'Expiry', cvc: 'CVC' };

interface Props {
  publishableKey: string | null;
  hint?: string;
  onUi?: (ui: CardUi) => void;
}

export const StripeCard = forwardRef<StripeCardHandle, Props>(function StripeCard({ publishableKey, hint, onUi }, ref) {
  const hosts = { number: useRef<HTMLDivElement>(null), expiry: useRef<HTMLDivElement>(null), cvc: useRef<HTMLDivElement>(null) };
  const stripe = useRef<StripeLike | null>(null);
  const elements = useRef<Partial<Record<FieldId, StripeElement>>>({});
  const [ui, setUi] = useState<CardUi>(EMPTY_CARD_UI);
  const [errors, setErrors] = useState<Record<FieldId, string | null>>({ number: null, expiry: null, cvc: null });
  const [loadError, setLoadError] = useState<string | null>(null);
  const problem = keyProblem(publishableKey);
  const latest = useRef(ui);
  latest.current = ui;

  useEffect(() => { onUi?.(ui); }, [ui, onUi]);

  useEffect(() => {
    if (problem || !publishableKey) return;
    let cancelled = false;
    const made: StripeElement[] = [];
    loadStripe().then(() => {
      if (cancelled || !window.Stripe) return;
      stripe.current = window.Stripe(publishableKey);
      const group = stripe.current.elements();
      for (const id of FIELDS) {
        const host = hosts[id].current;
        if (!host) return;
        const el = group.create(TYPE[id], { style: STYLE, ...(id === 'number' ? { showIcon: true, disableLink: true } : {}) });
        el.on('change', (e) => {
          setUi((u) => ({ ...u, brand: id === 'number' ? (e.brand ?? 'unknown') : u.brand,
            complete: { ...u.complete, [id]: e.complete }, invalid: { ...u.invalid, [id]: Boolean(e.error) } }));
          setErrors((cur) => ({ ...cur, [id]: e.error?.message ?? null }));
        });
        el.on('focus', () => setUi((u) => ({ ...u, focus: id })));
        el.on('blur', () => setUi((u) => (u.focus === id ? { ...u, focus: null } : u)));
        if (id === 'number') el.on('ready', () => setUi((u) => ({ ...u, ready: true })));
        el.mount(host);
        made.push(el);
        elements.current[id] = el;
      }
    }).catch((e: Error) => setLoadError(e.message));
    return () => {
      cancelled = true;
      for (const el of made) el.destroy();
      elements.current = {};
      setUi(EMPTY_CARD_UI);
    };
    // hosts are stable refs; the elements are rebuilt only for a new key
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [publishableKey, problem]);

  useImperativeHandle(ref, () => ({
    async tokenize() {
      if (problem) throw new Error(problem);
      const card = elements.current.number;
      if (!stripe.current || !card) throw new Error('Stripe Elements is not ready.');
      const { paymentMethod, error: err } = await stripe.current.createPaymentMethod({ type: 'card', card });
      if (err || !paymentMethod) throw new Error(err?.message ?? 'Stripe did not return a payment method.');
      return paymentMethod.id;
    },
    isComplete: () => FIELDS.every((id) => latest.current.complete[id]),
  }), [problem]);

  const number = testNumber(hint);
  const error = loadError ?? FIELDS.map((id) => errors[id]).find(Boolean) ?? null;
  const field = (id: FieldId) => (
    <div className="sfield" data-field={id} data-focus={ui.focus === id || undefined}
      data-invalid={ui.invalid[id] || undefined} data-complete={ui.complete[id] || undefined}>
      {/* the input lives in Stripe's iframe, which carries its own accessible name */}
      <span className="sfield__label" id={`sfield-${id}`}>{LABEL[id]}</span>
      <div className="sfield__box">
        <div ref={hosts[id]} className="sfield__host" role="group" aria-labelledby={`sfield-${id}`} />
        {ui.complete[id] && !ui.invalid[id] && <Check className="sfield__ok" size={14} strokeWidth={3} aria-label="complete" />}
      </div>
    </div>
  );

  return (
    <div className="card-entry" data-ready={ui.ready || undefined}>
      <div className="card-entry__head">
        <span className="card-entry__title">Card details</span>
        <span className="card-entry__secure"><LockKeyhole size={12} aria-hidden /> Stripe Elements</span>
      </div>
      {problem ? (
        <div className="card-entry__config" role="alert">
          <AlertCircle size={16} aria-hidden />
          <span>{problem}</span>
        </div>
      ) : (
        <div className="card-entry__fields">
          {field('number')}
          <div className="card-entry__row">
            {field('expiry')}
            {field('cvc')}
          </div>
        </div>
      )}
      {error && (
        <div className="card-entry__error" role="alert"><AlertCircle size={14} aria-hidden /> <span>{error}</span></div>
      )}
      {number && !problem && (
        <div className="card-entry__hint">
          <span>Test card <b className="mono">{number}</b> · any future date · any CVC</span>
          <CopyButton text={number.replace(/ /g, '')} label="the test card number" className="hint-copy" />
        </div>
      )}
      {hint?.includes('CVC check fails') && <div className="card-entry__hint card-entry__hint--note">This test card fails its CVC check at Stripe.</div>}
    </div>
  );
});
