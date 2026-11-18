/**
 * Hook start server Next.js: validasi konfigurasi saat boot (fail closed) -- lihat lib/instrumentation-node.ts.
 * Import dinamis di balik guard runtime agar API Node tidak masuk bundle Edge.
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    const { verifyConfigAtBoot } = await import('./lib/instrumentation-node');
    verifyConfigAtBoot();
  }
}
