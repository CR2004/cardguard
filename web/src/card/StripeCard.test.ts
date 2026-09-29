import { describe, expect, it } from 'vitest';
import { keyProblem } from './StripeCard';

describe('keyProblem: only a real Stripe TEST publishable key reaches Stripe.js', () => {
  it('refuses placeholders, live keys, secret keys and a missing key with a clear configuration error', () => {
    for (const bad of ['pk_test_x', 'pk_test_offline', `pk_live_${'A'.repeat(40)}`, `sk_test_${'A'.repeat(40)}`, '', null, undefined]) {
      expect(keyProblem(bad)).toMatch(/Stripe is not configured.*STRIPE_PUBLISHABLE_KEY/);
    }
  });

  it('accepts the shape of a real test publishable key', () => {
    expect(keyProblem(`pk_test_51${'AbCdEfGhIj'.repeat(9)}`)).toBeNull();
  });
});
