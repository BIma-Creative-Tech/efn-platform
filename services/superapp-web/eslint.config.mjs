// ESLint 9 flat config. eslint-config-next 15 masih format eslintrc -> dibungkus FlatCompat
// (pola resmi create-next-app 15). Dipakai oleh `npm run lint` (eslint .) dan oleh `next build`.
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { FlatCompat } from '@eslint/eslintrc';

const __dirname = dirname(fileURLToPath(import.meta.url));
const compat = new FlatCompat({ baseDirectory: __dirname });

const config = [
  { ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts', 'coverage/**'] },
  ...compat.extends('next/core-web-vitals', 'next/typescript'),
];

export default config;
