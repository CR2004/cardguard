import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { LockKeyhole } from 'lucide-react';

// Stripe TEST mode is the only payment rail: the card goes from Stripe Elements (Stripe's own iframe)
// to Stripe. This page receives a payment-method id (pm_...), never the card, and holds nothing else.

interface StripeElement {
  mount: (el: HTMLElement) => void;
  destroy: () => void;
  on: (event: 'change', handler: (e: { complete: boolean; error?: { message?: string } }) => void) => void;
}
interface StripeLike {
  elements: (opts?: object) => { create: (type: 'card', opts?: object) => StripeElement };
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

export const StripeCard = forwardRef<StripeCardHandle, { publishableKey: string; hint?: string }>(
  function StripeCard({ publishableKey, hint }, ref) {
    const host = useRef<HTMLDivElement>(null);
    const stripe = useRef<StripeLike | null>(null);
    const card = useRef<StripeElement | null>(null);
    const complete = useRef(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
      let cancelled = false;
      loadStripe().then(() => {
        if (cancelled || !window.Stripe || !host.current) return;
        stripe.current = window.Stripe(publishableKey);
        card.current = stripe.current.elements().create('card', {
          style: { base: { color: '#E9EDF7', fontSize: '15px', iconColor: '#9aa5ff', '::placeholder': { color: '#6E7A99' } } },
        });
        card.current.on('change', (e) => {
          complete.current = e.complete;
          setError(e.error?.message ?? null);
        });
        card.current.mount(host.current);
      }).catch((e: Error) => setError(e.message));
      return () => {
        cancelled = true;
        card.current?.destroy();
      };
    }, [publishableKey]);

    useImperativeHandle(ref, () => ({
      async tokenize() {
        if (!stripe.current || !card.current) throw new Error('Stripe Elements is not ready.');
        const { paymentMethod, error: err } = await stripe.current.createPaymentMethod({ type: 'card', card: card.current });
        if (err || !paymentMethod) throw new Error(err?.message ?? 'Stripe did not return a payment method.');
        return paymentMethod.id;
      },
      isComplete: () => complete.current,
    }), []);

    return (
      <div className="card-frame">
        <div className="card-frame__label">
          <LockKeyhole size={13} aria-hidden />
          <span>Card entry runs in Stripe Elements (TEST mode); the store gets a payment-method id.</span>
        </div>
        <div ref={host} className="stripe-host" />
        {hint && <div className="card-frame__hint">Type: {hint}, any future expiry, any CVC.</div>}
        {error && <div className="card-frame__prompt is-error" role="alert">{error}</div>}
      </div>
    );
  },
);
