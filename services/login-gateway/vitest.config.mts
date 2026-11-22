import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

const root = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  resolve: {
    // Sama dengan tsconfig `paths: { "@/*": ["./*"] }`.
    alias: [{ find: /^@\//, replacement: `${resolve(root)}/` }],
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
});
