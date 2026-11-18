/**
 * Rasio kontras WCAG 2.x (luminans relatif). Dipakai test token warna dan untuk memilih warna teks di sel heatmap.
 */
export function parseHex(hex: string): [number, number, number] {
  const m = /^#?([0-9a-fA-F]{6})$/.exec(hex.trim());
  if (!m) throw new Error(`warna hex tidak valid: ${hex}`);
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function channel(v: number): number {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

export function luminance(hex: string): number {
  const [r, g, b] = parseHex(hex);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrastRatio(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  const [hi, lo] = la >= lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * Pilih warna teks (putih/hitam) dengan kontras lebih tinggi terhadap latar. Dengan putih/hitam murni kontras
 * terburuk ~4,58:1 (>= 4,5 untuk semua latar); teks gelap #10201e bisa turun ke ~4,1:1 di latar menengah.
 */
export function textOn(bg: string, light = '#ffffff', dark = '#000000'): string {
  return contrastRatio(bg, light) >= contrastRatio(bg, dark) ? light : dark;
}
