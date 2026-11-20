import { randomUUID } from 'node:crypto';

/** Fixture test kampanye (bukan runtime). Tanpa data member; kode voucher sintetis. */
export const campaignPayload = (extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  campaign_id: 7,
  company_code: 'SMD',
  kind: 'segment',
  segment_run_id: randomUUID(),
  segment_code: 'viewed_not_bought',
  channels: ['inbox', 'push', 'email'],
  voucher_mode: 'anonymous_code',
  voucher_program_ref: 'loyalty.program:12',
  voucher_codes: ['SMD-AAAA-0001', 'SMD-AAAA-0002'],
  voucher_value_idr: 10000,
  budget_idr: null,
  cap_per_member_week: null,
  valid_from: '2026-11-20T00:00:00Z',
  valid_to: '2026-11-27T00:00:00Z',
  message: { title: 'Diskon susu', body: 'Voucher Rp10.000 untuk susu favoritmu.' },
  ...extra,
});
