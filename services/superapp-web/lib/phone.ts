/**
 * Normalisasi nomor HP Indonesia ke format `member_ref` (E.164 tanpa `+`, mis. 6281234567890).
 * Dipakai di client (UX) dan server (Route Handler). BFF tetap validator utama.
 */

/** Mengembalikan `628xxxxxxxxx` atau null bila tidak valid. */
export function normalizeIndonesianPhone(input: string): string | null {
  if (typeof input !== 'string') return null;
  let digits = input.replace(/[\s\-().]/g, '');
  if (digits.startsWith('+')) digits = digits.slice(1);
  if (!/^\d+$/.test(digits)) return null;

  if (digits.startsWith('0')) digits = `62${digits.slice(1)}`;
  else if (digits.startsWith('8')) digits = `62${digits}`;

  // Seluler Indonesia: 08xx dengan total 10-13 digit -> 628 + 8..11 digit.
  if (!/^628\d{8,11}$/.test(digits)) return null;
  return digits;
}

/** Mask untuk tampilan/log: 6281234567890 -> 62812****890 (docs/04 §1). */
export function maskPhone(memberRef: string): string {
  if (memberRef.length < 8) return '****';
  return `${memberRef.slice(0, 5)}****${memberRef.slice(-3)}`;
}
