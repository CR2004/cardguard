// One-click demo scenarios. Each is only a set of checkout inputs; what happens is whatever the
// nodes decide. The card itself is typed into Stripe Elements (Stripe's own iframe): this page never
// holds it. The hints name Stripe's published TEST card numbers, which are not cardholder data.

export interface Scenario {
  id: 'normal' | 'collaborative' | 'fraud' | 'rogue';
  title: string;
  story: string;
  watch: string;
  testCard: string; // what to type in the Stripe field (a published Stripe TEST number)
  amountCents: number;
  buyerCountry: string;
  attack: '' | 'leak';
}

export const SCENARIOS: Scenario[] = [
  {
    id: 'normal',
    title: 'Normal purchase',
    story: 'A US card buys $24 of accessories from a US address.',
    watch: 'One round of clean evidence. On a fresh card history the gate approves; repeat purchases raise velocity.',
    testCard: 'Stripe test Visa 4242 4242 4242 4242',
    amountCents: 2400, buyerCountry: 'US', attack: '',
  },
  {
    id: 'collaborative',
    title: 'Collaborative investigation',
    story: 'The same US card, minutes later, from a German address.',
    watch: 'The store sees a foreign buyer, the bank an ordinary cardholder: round 2 asks the bank alone about travel.',
    testCard: 'Stripe test Visa 4242 4242 4242 4242',
    amountCents: 6400, buyerCountry: 'DE', attack: '',
  },
  {
    id: 'fraud',
    title: 'Obvious fraud',
    story: '$900 from Nigeria, and the card fails its security-code check.',
    watch: 'Stripe reports the CVC check failed: a hard decline, no second round.',
    testCard: 'Stripe test card 4000 0000 0000 0101 (CVC check fails)',
    amountCents: 90000, buyerCountry: 'NG', attack: '',
  },
  {
    id: 'rogue',
    title: 'Rogue node',
    story: 'A compromised store agent tries to push the card number onto the wire.',
    watch: 'Each attempt stops at the privacy boundary; nothing is charged.',
    testCard: 'Stripe test Visa 4242 4242 4242 4242',
    amountCents: 2400, buyerCountry: 'US', attack: 'leak',
  },
];

export const COUNTRIES: { code: string; name: string }[] = [
  { code: 'US', name: 'United States' }, { code: 'CA', name: 'Canada' }, { code: 'DE', name: 'Germany' },
  { code: 'GB', name: 'United Kingdom' }, { code: 'BR', name: 'Brazil' }, { code: 'NG', name: 'Nigeria' },
];

export const ATTACKS: { id: Scenario['attack']; name: string; detail: string }[] = [
  { id: '', name: 'Honest store', detail: 'No attack' },
  { id: 'leak', name: 'Leak card data', detail: 'Three tries to put the card number on the wire' },
];
