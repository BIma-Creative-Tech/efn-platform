/**
 * Validasi konfigurasi saat boot (satu modul: lib/config.ts). Tidak fatal: tujuan yang tidak valid ditandai nonaktif
 * (halaman menampilkan "belum tersedia", /go -> 503). Log hanya nama variabel, tidak pernah nilai URL.
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== 'nodejs') return;
  const { loadConfig, describeProblems } = await import('./lib/config');
  const problems = describeProblems(loadConfig());
  for (const p of problems) console.warn(`[login-gateway] tujuan nonaktif - ${p}`);
}
