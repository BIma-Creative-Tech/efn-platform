// ESLint 9 flat config (sama dengan superapp-web). `npm run lint` = `eslint .`.
import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTs from 'eslint-config-next/typescript';

const config = [
  { ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts', 'coverage/**'] },
  ...nextVitals,
  ...nextTs,
];

export default config;
