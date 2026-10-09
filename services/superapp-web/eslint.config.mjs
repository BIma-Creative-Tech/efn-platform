// ESLint 9 flat config. eslint-config-next 16 sudah native flat config (tanpa FlatCompat/@eslint/eslintrc).
// Dipakai oleh `npm run lint` (eslint .). Next 16 tidak lagi menjalankan lint saat `next build`.
import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTs from 'eslint-config-next/typescript';

const config = [
  { ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts', 'coverage/**'] },
  ...nextVitals,
  ...nextTs,
];

export default config;
