import 'server-only';
import { headers } from 'next/headers';
import { getBrandById, type Brand } from './brands';

/** Brand aktif untuk request ini (di-set oleh middleware.ts). Next 15: headers() async. */
export async function getBrand(): Promise<Brand> {
  const h = await headers();
  return getBrandById(h.get('x-efn-brand'));
}
