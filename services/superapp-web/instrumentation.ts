/**
 * Hook start server Next.js (15/16). Validasi transport BFF saat boot (docs/04 §7.1) — lihat
 * `lib/instrumentation-node.ts`. Import dinamis di balik guard runtime agar API Node
 * (`node:fs`, `process.exit`) tidak masuk bundle Edge.
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    const { verifyBffTransportAtBoot } = await import('./lib/instrumentation-node');
    verifyBffTransportAtBoot();
  }
}
