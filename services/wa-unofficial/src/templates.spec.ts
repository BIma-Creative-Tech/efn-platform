import { createHmac, randomBytes } from 'node:crypto';
import { WA_CATEGORIES, WA_TEMPLATES, parseWaMessage, toHash } from './contract';
import { jidToE164, senderE164 } from './jid';
import { STOP_FOOTER, isStopText, renderWaText } from './templates';

describe('template, STOP, JID, kontrak', () => {
  it('allowlist kategori persis promo|notify_restock|price_alert; setiap template berkategori allowlist', () => {
    expect(WA_CATEGORIES).toEqual(['promo', 'notify_restock', 'price_alert']);
    for (const t of Object.values(WA_TEMPLATES)) expect(WA_CATEGORIES).toContain(t.category);
  });

  it('setiap template memuat footer STOP', () => {
    const p = { title: 'Promo', body: 'Isi', product_name: 'Gula', outlet_name: 'Outlet', new_price_idr: '15000', valid_to: '30 Nov' };
    for (const name of Object.keys(WA_TEMPLATES)) expect(renderWaText(name, p, 'Promo SMD')).toContain(STOP_FOOTER);
    expect(() => renderWaText('otp_code', p, 'x')).toThrow();
  });

  it('isStopText', () => {
    for (const s of ['STOP', 'stop', ' Stop. ', 'berhenti', 'UNSUBSCRIBE', 'stop promo']) expect(isStopText(s)).toBe(true);
    for (const s of ['stopkontak', 'jangan stop', '', 'ok', 'S T O P']) expect(isStopText(s)).toBe(false);
  });

  it('JID → nomor: hanya @s.whatsapp.net Indonesia; LID/grup → null; alternatif senderPn dipakai', () => {
    expect(jidToE164('6281234567890:12@s.whatsapp.net')).toBe('6281234567890');
    expect(jidToE164('12345678901234@lid')).toBeNull();
    expect(senderE164({ remoteJid: '1203630@g.us', participant: '6281234567890@s.whatsapp.net' })).toBeNull();
    expect(senderE164({ remoteJid: '9988776655@lid', senderPn: '6281234567890@s.whatsapp.net' })).toBe('6281234567890');
  });

  it('to_hash = HMAC-SHA256(kunci, e164) hex (sama dengan BFF)', () => {
    const k = Buffer.from(randomBytes(32).toString('hex'), 'utf8');
    expect(toHash(k, '6281234567890')).toBe(createHmac('sha256', k).update('6281234567890').digest('hex'));
    const h = toHash(k, '6281234567890');
    const ok = parseWaMessage(
      {
        schema_version: 1,
        idempotency_key: `wa:campaign:c1:${h}`,
        to_e164: '6281234567890',
        to_hash: h,
        category: 'promo',
        template: 'promo_generic',
        params: { title: 'A', body: 'B\r\nC' },
        expires_at: '2026-11-21T00:00:00Z',
      },
      k,
    );
    expect(ok.ok && ok.msg.params).toEqual({ title: 'A', body: 'B C' });
  });
});
