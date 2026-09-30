import { Check, CreditCard, Nfc } from 'lucide-react';
import { IdChip } from '../components/IdChip';
import { Guilloche } from '../components/Guilloche';
import type { CardUi } from './StripeCard';

interface Props {
  country: string;
  tokenizing: boolean;
  paymentMethod: string | null; // the pm_ id Stripe returned; the card itself never reaches this page
  ui: CardUi; // brand, focus and completeness from Stripe Elements: never a digit
}

/** The brand Stripe detected from the number typed so far, drawn as a simple mark (not a logo file). */
function BrandMark({ brand }: { brand: string }) {
  if (brand === 'visa') return <span className="brand-mark brand-mark--visa">VISA</span>;
  if (brand === 'mastercard') return <span className="brand-mark brand-mark--mc" role="img" aria-label="Mastercard"><i /><i /></span>;
  if (brand === 'amex') return <span className="brand-mark brand-mark--amex">AMEX</span>;
  if (brand !== 'unknown' && brand) return <span className="brand-mark brand-mark--text">{brand}</span>;
  return <CreditCard size={22} className="brand-mark brand-mark--none" aria-hidden />;
}

const Done = () => <Check size={11} strokeWidth={3.2} className="paycard__done" aria-hidden />;

/** The checkout as a card that follows Stripe's fields. Where a card number would be, it shows what the
 *  store actually holds: nothing until Stripe returns a payment-method id, then that id. */
export function PaymentCard({ country, tokenizing, paymentMethod, ui }: Props) {
  const state = tokenizing ? 'tokenizing' : paymentMethod ? 'token' : 'entry';
  const face = state === 'entry' && ui.focus === 'cvc' ? 'back' : 'front';
  const zone = (id: 'number' | 'expiry') => ({
    'data-focus': (state === 'entry' && ui.focus === id) || undefined,
    'data-done': ui.complete[id] || undefined,
  });
  return (
    <div className="paycard-3d" data-face={face}>
      <div className="paycard-3d__inner">
        <div className="paycard paycard--front" data-state={state} data-brand={ui.brand} aria-hidden={face === 'back' || undefined}>
          <Guilloche size={260} className="paycard__rosette" colors={['#9a8fe0', '#7cc9bd', '#d7c49a', '#aab2c8']} strokeWidth={0.6} />
          <div className="paycard__top">
            <span className="paycard__brand">CardGuard</span>
            <span className="paycard__test">Test mode</span>
          </div>
          <div className="paycard__chip-row">
            <svg width="38" height="29" viewBox="0 0 38 29" aria-hidden>
              <rect x="0.5" y="0.5" width="37" height="28" rx="6" fill="url(#chipGold)" stroke="rgba(90,70,30,.35)" />
              <path d="M0.5 10h12M0.5 19h12M25.5 10h12M25.5 19h12M12.5 0.5v28M25.5 0.5v28M12.5 14.5h13" stroke="rgba(90,70,30,.35)" fill="none" />
              <defs>
                <linearGradient id="chipGold" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0" stopColor="#f3e2b3" /><stop offset="0.55" stopColor="#d9bf82" /><stop offset="1" stopColor="#b99a5c" />
                </linearGradient>
              </defs>
            </svg>
            <Nfc size={20} className="paycard__nfc" aria-hidden />
          </div>
          <div className="paycard__number" {...zone('number')} aria-live="polite">
            {state === 'token' && paymentMethod ? (
              <>
                <span className="paycard__caption">Stripe returned</span>
                <IdChip id={paymentMethod} label="payment-method id" />
              </>
            ) : state === 'tokenizing' ? (
              <span className="paycard__pending">Stripe is tokenizing…</span>
            ) : (
              <>
                <span className="paycard__digits" aria-hidden>•••• •••• •••• ••••</span>
                <span className="paycard__caption">
                  {ui.complete.number ? <><Done /> Number held by Stripe</> : 'The number stays in Stripe’s field'}
                </span>
              </>
            )}
          </div>
          <div className="paycard__bottom">
            <div>
              <span className="paycard__caption">Buyer</span>
              <span className="paycard__value">{country}</span>
            </div>
            <div className="paycard__exp" {...zone('expiry')}>
              <span className="paycard__caption">Expires</span>
              <span className="paycard__value">{ui.complete.expiry ? <><Done /> Set</> : 'MM/YY'}</span>
            </div>
            <BrandMark brand={ui.brand} />
          </div>
        </div>
        <div className="paycard paycard--back" aria-hidden={face === 'front' || undefined}>
          <div className="paycard__magstripe" />
          <div className="paycard__sign">
            <span className="paycard__sign-strip" />
            <span className="paycard__cvc" data-done={ui.complete.cvc || undefined}>{ui.complete.cvc ? <Done /> : '•••'}</span>
          </div>
          <p className="paycard__back-note">The CVC goes into Stripe’s field and only Stripe checks it. CardGuard learns pass or fail.</p>
        </div>
      </div>
    </div>
  );
}
