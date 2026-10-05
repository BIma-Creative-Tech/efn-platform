/**
 * dHash 64-bit dari raster grayscale 9x8 (baris-mayor): bit = piksel kiri > piksel kanan.
 * Hasil 16 hex → kolom `price_hunter_report.image_phash` untuk deteksi foto duplikat (R-B05).
 * Fungsi murni (tanpa sharp) agar mudah diuji.
 */
export function dHashFromGrey9x8(raw: Uint8Array): string {
  if (raw.length < 72) throw new Error('dHash membutuhkan 72 piksel');
  let hex = '';
  for (let y = 0; y < 8; y += 1) {
    let byte = 0;
    for (let x = 0; x < 8; x += 1) {
      const left = raw[y * 9 + x];
      const right = raw[y * 9 + x + 1];
      byte = (byte << 1) | (left > right ? 1 : 0);
    }
    hex += byte.toString(16).padStart(2, '0');
  }
  return hex;
}
