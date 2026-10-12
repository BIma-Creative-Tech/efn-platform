/**
 * Konfirmasi usia minimum saat login/daftar (G18 W-19, keputusan pemilik Q-P3). MURNI (client + server + test).
 *
 * Kontrak BFF (docs/agent-reports/efn-bff-engineer.md "Gelombang 18"): `POST /v1/auth/otp/verify`
 * `{ challenge_id, code, age_confirmed: true }`. Tidak ada/`false` -> `400 age_confirmation_required`, dicek SEBELUM OTP
 * sehingga challenge tidak hangus: pengguna cukup mencentang lalu mengirim lagi. Diminta di setiap login (login = daftar).
 * Tanpa tanggal lahir (minimisasi data UU PDP).
 *
 * TEKS PELANGGAN = DRAF, menunggu persetujuan Legal (lihat README).
 */

/** Hanya untuk teks; BFF `MEMBER_MIN_AGE` (17..21) adalah sumber kebenaran aturan. */
export const MIN_AGE = 17;

export const AGE_CHECKBOX_LABEL = `Saya berusia ${MIN_AGE} tahun atau lebih`;

export const AGE_REQUIRED_MESSAGE = `Centang pernyataan bahwa Anda berusia minimal ${MIN_AGE} tahun, lalu tekan Masuk lagi. Kode yang sama masih bisa dipakai.`;

/** `age_confirmed` hanya sah bila boolean `true` persis (bukan "true", 1, dsb.). */
export function isAgeConfirmed(v: unknown): v is true {
  return v === true;
}
