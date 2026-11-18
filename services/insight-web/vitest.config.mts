import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

const root = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  resolve: {
    alias: [
      // Paket `server-only` melempar error di luar bundle server Next; di test diganti stub kosong.
      { find: /^server-only$/, replacement: resolve(root, 'tests/stubs/server-only.ts') },
      // Sama dengan tsconfig `paths: { "@/*": ["./*"] }`.
      { find: /^@\//, replacement: `${resolve(root)}/` },
    ],
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
});
