'use client';
/**
 * Utilitas navigasi klien: drill-down (menambah filter dari klik chart/tabel) dan pengumuman aria-live.
 */
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback } from 'react';
import { beginNav } from './loading-store';

export type DrillParam = 'branch' | 'cat' | 'product';

export function announce(message: string): void {
  const el = document.getElementById('live-region');
  if (el) {
    el.textContent = '';
    // Ubah isi pada tick berikutnya agar pembaca layar mengumumkan ulang pesan yang sama.
    window.setTimeout(() => {
      el.textContent = message;
    }, 30);
  }
}

export function addFilterValue(current: URLSearchParams, param: DrillParam, value: string): URLSearchParams {
  const next = new URLSearchParams(current.toString());
  if (param === 'cat') {
    if (!next.getAll('cat').includes(value)) next.append('cat', value);
  } else {
    const cur = (next.get(param) ?? '').split(',').filter(Boolean);
    if (!cur.includes(value)) cur.push(value);
    next.set(param, cur.join(','));
  }
  return next;
}

export function useDrill(): (param: DrillParam, value: string, label: string) => void {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  return useCallback(
    (param, value, label) => {
      const next = addFilterValue(new URLSearchParams(sp.toString()), param, value);
      if (next.toString() !== sp.toString()) beginNav();
      router.push(`${pathname}?${next.toString()}`);
      const what = param === 'branch' ? 'cabang' : param === 'cat' ? 'kategori' : 'produk';
      announce(`Filter ditambahkan: ${what} ${label}. Data diperbarui.`);
    },
    [router, pathname, sp],
  );
}
