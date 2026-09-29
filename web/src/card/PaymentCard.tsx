import { Nfc } from 'lucide-react';
import { IdChip } from '../components/IdChip';
import { Guilloche } from '../components/Guilloche';
import { formatMoney } from '../format';

interface Props {
  amountCents: number;
  country: string;
  tokenizing: boolean;
  paymentMethod: string | null; // the pm_ id Stripe returned; the card itself never reaches this page
}

/** The checkout at a glance. Where a card number would be, it shows what the store actually holds. */
export function PaymentCard({ amountCents, country, tokenizing, paymentMethod }: Props) {
  const state = tokenizing ? 'tokenizing' : paymentMethod ? 'token' : 'empty';
  return (
    <div className="paycard" data-state={state}>
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
      <div className="paycard__number" aria-live="polite">
        {state === 'token' && paymentMethod ? (
          <>
            <span className="paycard__caption">Store received</span>
            <IdChip id={paymentMethod} label="payment-method id" />
          </>
        ) : state === 'tokenizing' ? (
          <span className="paycard__pending">Stripe is tokenizing…</span>
        ) : (
          <span className="paycard__pending">Card number stays with Stripe</span>
        )}
      </div>
      <div className="paycard__bottom">
        <div>
          <span className="paycard__caption">Buyer</span>
          <span className="paycard__value">{country}</span>
        </div>
        <div className="paycard__amount">
          <span className="paycard__caption">Total</span>
          <span className="paycard__total">{formatMoney(amountCents)}</span>
        </div>
      </div>
    </div>
  );
}
