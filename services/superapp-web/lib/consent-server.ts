/**
 * Pemuatan consent untuk Server Components (docs/04 §8.4). SERVER-SIDE SAJA.
 * Gagal memuat -> dianggap TIDAK ada persetujuan (fail-closed: lokasi tidak ditawarkan).
 */
import 'server-only';
import { bffFetch } from './bff';
import { parseConsents, parsePrivacyRecord, type ConsentPurpose, type ConsentRecord } from './consent';

type PrivacyRecord = { version: string; updated_at: string | null } | null;
export type ConsentLoad =
  | { status: 'ok'; records: Record<ConsentPurpose, ConsentRecord>; privacy: PrivacyRecord }
  | { status: 'unauthenticated' }
  | { status: 'error'; records: Record<ConsentPurpose, ConsentRecord>; privacy: PrivacyRecord };

export async function loadConsents(token: string): Promise<ConsentLoad> {
  const result = await bffFetch<unknown>('/v1/me/consents', { token });
  if (result.ok) return { status: 'ok', records: parseConsents(result.data), privacy: parsePrivacyRecord(result.data) };
  if (result.status === 401) return { status: 'unauthenticated' };
  return { status: 'error', records: parseConsents([]), privacy: null };
}
