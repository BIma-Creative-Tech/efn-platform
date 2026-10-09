'use client';

import { useMemo } from 'react';
import qrcode from 'qrcode-generator';

/**
 * QR dirender DI PERANGKAT (library lokal `qrcode-generator`, tanpa layanan QR pihak ketiga — docs/04 §10.3.5, §10.12).
 * Output SVG React (bukan innerHTML) -> tidak perlu perubahan CSP. Isi QR tidak pernah di-log/dikirim ke mana pun.
 */
export function QrCode({ value, label, className }: { value: string; label: string; className?: string }) {
  const path = useMemo(() => {
    try {
      const qr = qrcode(0, 'M');
      qr.addData(value, 'Byte');
      qr.make();
      const count = qr.getModuleCount();
      let d = '';
      for (let r = 0; r < count; r += 1) {
        for (let c = 0; c < count; c += 1) {
          if (qr.isDark(r, c)) d += `M${c + 4} ${r + 4}h1v1h-1z`;
        }
      }
      return { d, size: count + 8 };
    } catch {
      return null;
    }
  }, [value]);

  if (!path) {
    return (
      <p className="alert alert-error" role="alert">
        Kode QR tidak dapat ditampilkan. Muat ulang halaman.
      </p>
    );
  }
  return (
    <svg
      className={className}
      viewBox={`0 0 ${path.size} ${path.size}`}
      role="img"
      aria-label={label}
      shapeRendering="crispEdges"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect width={path.size} height={path.size} fill="#ffffff" />
      <path d={path.d} fill="#000000" />
    </svg>
  );
}
