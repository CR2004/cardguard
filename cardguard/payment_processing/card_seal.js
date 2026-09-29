// Seals card details for the issuer with RSA-OAEP-SHA256 (WebCrypto). Runs in the browser card
// frame and in Node 22+ (tests). Nothing here ever posts card details anywhere except into the
// ciphertext returned to the caller.
(function (root) {
  const subtle = (root.crypto && root.crypto.subtle) || (typeof globalThis !== 'undefined' && globalThis.crypto && globalThis.crypto.subtle);
  const b64ToBytes = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
  const bytesToB64 = b => btoa(String.fromCharCode(...new Uint8Array(b)));
  const hex = b => Array.from(b, x => x.toString(16).padStart(2, '0')).join('');

  async function seal(spkiB64, fields, kid) {
    // fields: {pan, exp, cvc, amount_cents, merchant_id}. Compact keys: RSA-2048 OAEP-SHA256 holds <= 190 bytes.
    const key = await subtle.importKey('spki', b64ToBytes(spkiB64), { name: 'RSA-OAEP', hash: 'SHA-256' }, false, ['encrypt']);
    const nonce = new Uint8Array(12); (root.crypto || globalThis.crypto).getRandomValues(nonce);
    const payload = { p: String(fields.pan).replace(/\s+/g, ''), e: String(fields.exp).trim(), c: String(fields.cvc).trim(),
      a: Number(fields.amount_cents), m: String(fields.merchant_id), n: hex(nonce), t: Math.floor(Date.now() / 1000) };
    const raw = new TextEncoder().encode(JSON.stringify(payload));
    if (raw.length > 190) throw new Error('card payload too large to seal');
    const cipher = bytesToB64(await subtle.encrypt({ name: 'RSA-OAEP' }, key, raw));
    return (kid ? kid + '.' : '') + cipher;   // key id in the clear so the issuer can rotate keys
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { seal };
  else root.cardSeal = { seal };
})(typeof window !== 'undefined' ? window : globalThis);
