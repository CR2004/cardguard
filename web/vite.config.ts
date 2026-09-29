import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// The merchant node (Flask, :4242) serves dist/ at its own origin: the issuer's card frame only
// answers postMessage from that origin, so the UI is always built and served there, never proxied.
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
