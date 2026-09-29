import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { LockKeyhole } from 'lucide-react';
import { CopyButton } from '../components/IdChip';

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

// A published Stripe TEST number from the scenario hint, e.g. "Stripe test Visa 4242 4242 4242 4242".
const testNumber = (hint?: string) => hint?.match(/\d{4}(?: \d{4}){3}/)?.[0];

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
          disableLink: true, // no Link button over the expiry and CVC; this demo only takes Stripe's test cards
          style: {
            base: {
              color: '#F2F4FA', fontSize: '16px', iconColor: '#A399FF', fontWeight: '500',
              fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif',
              '::placeholder': { color: '#7C87A6' },
            },
            invalid: { color: '#FF8A7F', iconColor: '#FF6B5E' },
          },
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

    const number = testNumber(hint);
    return (
      <div className="card-field">
        <div className="card-field__head">
          <span className="card-field__label" id="card-field-label">Card details</span>
          <span className="card-field__secure"><LockKeyhole size={12} aria-hidden /> Stripe Elements</span>
        </div>
        <div ref={host} className="stripe-host" role="group" aria-labelledby="card-field-label" />
        {number && (
          <div className="card-field__hint">
            <span>Test card <b className="mono">{number}</b>, any future date, any CVC</span>
            <CopyButton text={number.replace(/ /g, '')} label="the test card number" className="hint-copy" />
          </div>
        )}
        {hint?.includes('CVC check fails') && <div className="card-field__hint card-field__hint--note">This test card fails its CVC check at Stripe.</div>}
        {error && <div className="card-field__error" role="alert">{error}</div>}
      </div>
    );
  },
);
