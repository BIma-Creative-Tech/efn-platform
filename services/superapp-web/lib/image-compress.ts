/**
 * Kompres foto di browser sebelum upload: resize sisi terpanjang <= 2048 px, re-encode JPEG.
 * Re-encode lewat canvas juga MEMBUANG metadata EXIF (termasuk GPS kamera) — minimisasi data (UU PDP).
 * BFF tetap melakukan re-encode sendiri (sharp) — ini optimasi bandwidth/UX, bukan kontrol keamanan.
 * Browser modern menerapkan orientasi EXIF saat decode <img> (image-orientation: from-image).
 */

export const MAX_DIMENSION = 2048;
export const MAX_UPLOAD_BYTES = 8 * 1024 * 1024;
const ACCEPTED = ['image/jpeg', 'image/png', 'image/webp'];

export class ImageCompressError extends Error {}

function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new ImageCompressError('Gambar tidak dapat dibaca.'));
    };
    img.decoding = 'async';
    img.src = url;
  });
}

function canvasToBlob(canvas: HTMLCanvasElement, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new ImageCompressError('Gagal memproses gambar.'))),
      'image/jpeg',
      quality,
    );
  });
}

export async function compressImage(file: File): Promise<File> {
  if (!ACCEPTED.includes(file.type)) {
    throw new ImageCompressError('Format foto harus JPEG, PNG, atau WebP.');
  }
  const img = await loadImage(file);
  const w = img.naturalWidth;
  const h = img.naturalHeight;
  if (!w || !h) throw new ImageCompressError('Gambar tidak dapat dibaca.');

  const scale = Math.min(1, MAX_DIMENSION / Math.max(w, h));
  const targetW = Math.max(1, Math.round(w * scale));
  const targetH = Math.max(1, Math.round(h * scale));

  const canvas = document.createElement('canvas');
  canvas.width = targetW;
  canvas.height = targetH;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new ImageCompressError('Browser tidak mendukung pemrosesan gambar.');
  // Latar putih untuk PNG/WebP transparan (JPEG tidak punya alpha).
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, targetW, targetH);
  ctx.drawImage(img, 0, 0, targetW, targetH);

  let blob: Blob | null = null;
  for (const q of [0.85, 0.75, 0.6]) {
    blob = await canvasToBlob(canvas, q);
    if (blob.size <= MAX_UPLOAD_BYTES) break;
  }
  if (!blob || blob.size > MAX_UPLOAD_BYTES) {
    throw new ImageCompressError('Ukuran foto terlalu besar setelah dikompres.');
  }
  const baseName = file.name.replace(/\.[^.]+$/, '') || 'foto';
  return new File([blob], `${baseName}.jpg`, { type: 'image/jpeg', lastModified: Date.now() });
}
