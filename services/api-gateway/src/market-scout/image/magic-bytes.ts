/**
 * Deteksi tipe gambar dari magic bytes (R-S06). Header `Content-Type`/nama file dari klien TIDAK dipercaya.
 * - JPEG: FF D8 FF
 * - PNG : 89 50 4E 47 0D 0A 1A 0A
 * - WebP: "RIFF" <4 byte ukuran> "WEBP"
 * Lolos magic bytes belum berarti aman (polyglot) → tetap wajib di-decode & re-encode dengan sharp.
 */
export type ImageKind = 'jpeg' | 'png' | 'webp';

const PNG_SIG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

function startsWith(buf: Uint8Array, sig: readonly number[], offset = 0): boolean {
  if (buf.length < offset + sig.length) return false;
  for (let i = 0; i < sig.length; i += 1) if (buf[offset + i] !== sig[i]) return false;
  return true;
}

export function detectImageKind(buf: Uint8Array): ImageKind | null {
  if (startsWith(buf, [0xff, 0xd8, 0xff])) return 'jpeg';
  if (startsWith(buf, PNG_SIG)) return 'png';
  // R I F F . . . . W E B P
  if (startsWith(buf, [0x52, 0x49, 0x46, 0x46]) && startsWith(buf, [0x57, 0x45, 0x42, 0x50], 8)) return 'webp';
  return null;
}
