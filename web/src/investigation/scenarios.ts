// One-click demo scenarios. Each is only a set of checkout inputs; what happens is whatever the
// nodes decide. The card itself is typed into Stripe Elements (Stripe's own iframe): this page never
// holds it. The hints name Stripe's published TEST card numbers, which are not cardholder data.

export interface Scenario {
  id: 'normal' | 'collaborative' | 'fraud' | 'rogue' | 'ring';
  title: string;
  story: string;
  watch: string;
  testCard: string; // what to type in the Stripe field (a published Stripe TEST number)
  amountCents: number;
  buyerCountry: string;
  attack: '' | 'leak';
  ring?: boolean; // the same card at every store of this node, one checkout after another
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
  {
    id: 'ring',
    title: 'Card-testing ring',
    story: 'One card buys $24 at three different stores within a minute.',
    watch: 'Each store alone sees one ordinary purchase. The coordinator\'s network memory sees the same card reference at one, two, then three merchants: low, medium, high. The third checkout is held for a person and every store that saw the card is alerted.',
    testCard: 'Stripe test Visa 4242 4242 4242 4242',
    amountCents: 2400, buyerCountry: 'US', attack: '', ring: true,
  },
];

/** What one checkout of the ring scenario came back with: shown while the next store runs. */
export interface RingStep {
  store: string;
  outcome: string;
  band: string | null;
}

export const COUNTRIES: { code: string; name: string }[] = [
  { code: 'US', name: 'United States' }, { code: 'CA', name: 'Canada' }, { code: 'DE', name: 'Germany' },
  { code: 'GB', name: 'United Kingdom' }, { code: 'BR', name: 'Brazil' }, { code: 'NG', name: 'Nigeria' },
];

export const ATTACKS: { id: Scenario['attack']; name: string; detail: string }[] = [
  { id: '', name: 'Honest store', detail: 'No attack' },
  { id: 'leak', name: 'Leak card data', detail: 'Three tries to put the card number on the wire' },
];
