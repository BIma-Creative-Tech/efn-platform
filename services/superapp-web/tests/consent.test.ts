import { createHash } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import {
  CONSENT_TEXTS,
  isConsentActive,
  isConsentOutdated,
  parseConsents,
  validateConsentUpdate,
} from '../lib/consent';

describe('parseConsents', () => {
  it('default: semua tidak aktif (tidak pre-checked)', () => {
    const r = parseConsents([]);
    expect(isConsentActive(r, 'location')).toBe(false);
    expect(isConsentActive(r, 'marketing')).toBe(false);
  });

  it('membaca bentuk kontrak §8.4', () => {
    const r = parseConsents([
      { purpose: 'location', granted: true, version: CONSENT_TEXTS.location.version, updated_at: '2026-10-06T01:00:00Z' },
      { purpose: 'marketing', granted: false, version: CONSENT_TEXTS.marketing.version, updated_at: '2026-10-06T01:00:00Z' },
    ]);
    expect(isConsentActive(r, 'location')).toBe(true);
    expect(isConsentActive(r, 'marketing')).toBe(false);
    expect(r.location.updated_at).toBe('2026-10-06T01:00:00Z');
  });

  it('toleran terhadap { items: [...] } dan membuang purpose asing / granted non-boolean', () => {
    const r = parseConsents({
      items: [
        { purpose: 'tracking', granted: true },
        { purpose: 'location', granted: 'true' },
        { purpose: '__proto__', granted: true },
      ],
    });
    expect(isConsentActive(r, 'location')).toBe(false);
    expect(Object.keys(r).sort()).toEqual(['location', 'marketing']);
  });

  it('input sampah -> default', () => {
    for (const v of [null, undefined, 'x', 42, { items: 'x' }]) {
      expect(isConsentActive(parseConsents(v), 'location')).toBe(false);
    }
  });
});

describe('isConsentOutdated', () => {
  it('aktif dengan versi lama -> outdated', () => {
    const r = parseConsents([{ purpose: 'location', granted: true, version: 'location@2020-01-01' }]);
    expect(isConsentOutdated(r.location)).toBe(true);
  });
  it('tidak aktif tidak pernah outdated', () => {
    const r = parseConsents([{ purpose: 'location', granted: false, version: 'location@2020-01-01' }]);
    expect(isConsentOutdated(r.location)).toBe(false);
  });
});

describe('validateConsentUpdate', () => {
  it('menerima pemberian dengan versi terbaru', () => {
    expect(
      validateConsentUpdate({ purpose: 'location', granted: true, version: CONSENT_TEXTS.location.version }),
    ).toEqual({ ok: true, purpose: 'location', granted: true, version: CONSENT_TEXTS.location.version });
  });

  it('pemberian dengan versi usang -> 409 stale_text', () => {
    const v = validateConsentUpdate({ purpose: 'marketing', granted: true, version: 'marketing@2020-01-01' });
    expect(v).toMatchObject({ ok: false, status: 409, code: 'stale_text' });
  });

  it('penarikan selalu diterima dan dicatat dengan versi terbaru', () => {
    expect(validateConsentUpdate({ purpose: 'marketing', granted: false, version: 'apa saja' })).toEqual({
      ok: true,
      purpose: 'marketing',
      granted: false,
      version: CONSENT_TEXTS.marketing.version,
    });
  });

  it.each([
    null,
    [],
    {},
    { purpose: 'tracking', granted: true, version: 'x' },
    { purpose: 'location', granted: 'true', version: CONSENT_TEXTS.location.version },
    { purpose: 'location', granted: true },
  ])('menolak %j -> 400', (body) => {
    expect(validateConsentUpdate(body)).toMatchObject({ ok: false, status: 400 });
  });
});

/**
 * G6 QA — kontrak frontend <-> BFF (docs/04 §8.7/§8.9): BFF mencatat `text_sha256` teks AKTIF dari env
 * `CONSENT_<P>_VERSION` / `CONSENT_<P>_TEXT_SHA256` (dev: infra/compose/docker-compose.dev.yml; prod: env deploy).
 * Kanonik: SHA-256 UTF-8 dari `body` persis. Bila teks/versi diubah, test ini gagal -> perbarui compose dev + env prod.
 */
describe('versi & hash teks consent = env BFF (dev compose)', () => {
  const sha = (s: string) => createHash('sha256').update(s, 'utf8').digest('hex');
  it.each([
    ['location', 'location@2026-10-06', '938707dc56c73323db27d0360d69386bf95e290ce0b0a465741de78ca19c96e5'],
    ['marketing', 'marketing@2026-10-27', 'cbf5fd4dddcb361e1512b7b10ae7e0a80799c587db3d9dab951810e65afc750e'],
  ] as const)('%s', (purpose, version, hash) => {
    expect(CONSENT_TEXTS[purpose].version).toBe(version);
    expect(CONSENT_TEXTS[purpose].version).toMatch(/^[A-Za-z0-9._@-]{1,32}$/); // regex BFF privacy.dto.ts
    expect(sha(CONSENT_TEXTS[purpose].body)).toBe(hash);
  });
});
describe('G27 §18.3a: persetujuan Kebijakan Privasi', () => {
  it('teks & versi = env BFF (sha256) dan app', async () => {
    const { createHash } = await import('node:crypto');
    const { PRIVACY_CONSENT } = await import('@/lib/consent');
    expect(PRIVACY_CONSENT.version).toBe('privacy@2026-10-27');
    expect(createHash('sha256').update(PRIVACY_CONSENT.body, 'utf8').digest('hex')).toBe('b66c21588d1d7ecd2c9edcde1bc813c4e138f00708f8b3c6004226f80a1593e2');
  });
  it('parsePrivacyRecord: hanya granted true; purpose privacy tidak masuk toggle', async () => {
    const { parsePrivacyRecord, parseConsents: pc } = await import('@/lib/consent');
    const data = [
      { purpose: 'privacy', granted: true, version: 'privacy@2026-10-27', updated_at: '2026-10-27T00:00:00.000Z' },
      { purpose: 'marketing', granted: false, version: null },
    ];
    expect(parsePrivacyRecord(data)).toEqual({ version: 'privacy@2026-10-27', updated_at: '2026-10-27T00:00:00.000Z' });
    expect(parsePrivacyRecord([{ purpose: 'privacy', granted: false, version: 'x' }])).toBeNull();
    expect(Object.keys(pc(data))).toEqual(['location', 'marketing']);
  });
});
