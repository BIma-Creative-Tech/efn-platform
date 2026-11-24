/**
 * Asersi aktor `ppa2` (docs/04 §30.17.7): bentuk, urutan 8 kunci, ikatan method + path + hash body, umur 60 dtk, jti, aturan
 * `sid` kosong. Pemverifikasi referensi di bawah menuliskan ulang urutan pemeriksaan prodplan (regex -> HMAC -> kunci tepat ->
 * aud -> waktu -> m/p -> bh -> jti, lalu aturan sid) sehingga test ini juga menjaga kecocokan byte dengan sisi Python.
 */
import { createHash, createHmac, timingSafeEqual } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import {
  ACTOR_RE,
  ActorError,
  buildActorPayload,
  bodyHash,
  newJti,
  signActor,
  type ActorInput,
} from '@/lib/prodplan-token';

const KEY = 'ab'.repeat(32);
const NOW = 1_795_000_000;
const JTI = 'AAAAAAAAAAAAAAAAAAAAAA';
const SID = 'S'.repeat(43);
const KEYS = ['aud', 'bh', 'exp', 'iat', 'jti', 'm', 'p', 'sid'];

const input = (over: Partial<ActorInput> = {}): ActorInput => ({
  session: { sid: SID },
  method: 'GET',
  path: '/v1/meta',
  body: '',
  key: KEY,
  nowSec: NOW,
  jti: JTI,
  ...over,
});

type Verdict = { ok: true; payload: Record<string, unknown> } | { ok: false; reason: string };

/** Pemverifikasi referensi (mirror §30.17.7). `known` = kumpulan sid yang dikenal sesi prodplan. */
function verifyActor(
  header: string,
  key: string,
  now: number,
  method: string,
  path: string,
  body: string,
  seenJti = new Set<string>(),
  known: ReadonlySet<string> = new Set([SID]),
): Verdict {
  if (!ACTOR_RE.test(header) || Buffer.byteLength(header) > 2048) return { ok: false, reason: 'regex' };
  const [prefix, payloadB64, sigB64] = header.split('.');
  const want = createHmac('sha256', Buffer.from(key, 'ascii')).update(`${prefix}.${payloadB64}`, 'ascii').digest();
  const got = Buffer.from(sigB64, 'base64url');
  if (got.length !== 32 || !timingSafeEqual(got, want)) return { ok: false, reason: 'hmac' };
  const payload = JSON.parse(Buffer.from(payloadB64, 'base64url').toString('utf8')) as Record<string, unknown>;
  if (JSON.stringify(Object.keys(payload)) !== JSON.stringify(KEYS)) return { ok: false, reason: 'keys' };
  if (payload.aud !== 'prodplan') return { ok: false, reason: 'aud' };
  const iat = payload.iat as number;
  const exp = payload.exp as number;
  if (iat > now + 30 || now >= exp || exp - iat !== 60) return { ok: false, reason: 'time' };
  if (payload.m !== method || payload.p !== path) return { ok: false, reason: 'mp' };
  if (payload.bh !== createHash('sha256').update(body, 'utf8').digest('base64url')) return { ok: false, reason: 'bh' };
  const jti = payload.jti as string;
  if (seenJti.has(jti)) return { ok: false, reason: 'replay' };
  seenJti.add(jti);
  const sid = payload.sid as string;
  const sidless = (method === 'POST' && path === '/v1/session') || (method === 'GET' && path === '/v1/meta');
  if (sid === '' && !sidless) return { ok: false, reason: 'actor_invalid' };
  if (sid !== '' && !known.has(sid)) return { ok: false, reason: 'session_expired' };
  return { ok: true, payload };
}

describe('ppa2: bentuk dan vektor byte-exact', () => {
  it('payload = JSON tanpa spasi, tepat 8 kunci berurutan (vektor tetap)', () => {
    const emptyHash = createHash('sha256').update('').digest('base64url');
    expect(buildActorPayload(input())).toBe(
      `{"aud":"prodplan","bh":"${emptyHash}","exp":${NOW + 60},"iat":${NOW},"jti":"${JTI}","m":"GET","p":"/v1/meta","sid":"${SID}"}`,
    );
  });

  it('vektor login: sid kosong, bh = hash body persis (bukan hash password)', () => {
    const body = JSON.stringify({ username: 'rina', password: 'rahasia', n: 'N'.repeat(22) });
    const p = buildActorPayload(input({ session: { sid: '' }, method: 'POST', path: '/v1/session', body }));
    expect(p).toBe(
      `{"aud":"prodplan","bh":"${bodyHash(body)}","exp":${NOW + 60},"iat":${NOW},"jti":"${JTI}","m":"POST","p":"/v1/session","sid":""}`,
    );
    expect(p).not.toContain('rahasia');
    expect(bodyHash(body)).not.toBe(bodyHash('rahasia'));
  });

  it('header: regex §30.17.7, <= 2048 byte, tanda tangan = HMAC-SHA256(byte ASCII string hex kunci, "ppa2." + payload_b64url)', () => {
    const h = signActor(input());
    expect(ACTOR_RE.test(h)).toBe(true);
    expect(Buffer.byteLength(h)).toBeLessThanOrEqual(2048);
    const [prefix, b64, sig] = h.split('.');
    expect(prefix).toBe('ppa2');
    expect(sig).toBe(createHmac('sha256', Buffer.from(KEY, 'ascii')).update(`ppa2.${b64}`).digest('base64url'));
    // BUKAN kunci hasil hex-decode
    expect(sig).not.toBe(createHmac('sha256', Buffer.from(KEY, 'hex')).update(`ppa2.${b64}`).digest('base64url'));
    expect(sig).toHaveLength(43);
    expect(b64).not.toMatch(/[=+/]/);
  });

  it('pemverifikasi referensi menerima token sah untuk GET, POST dengan body, dan login tanpa sid', () => {
    const get = signActor(input({ path: '/v1/plans?brand=pb' }));
    expect(verifyActor(get, KEY, NOW + 1, 'GET', '/v1/plans?brand=pb', '').ok).toBe(true);
    const body = JSON.stringify({ row_version: 3 });
    const post = signActor(input({ method: 'POST', path: '/v1/plans/x/submit', body }));
    expect(verifyActor(post, KEY, NOW + 1, 'POST', '/v1/plans/x/submit', body).ok).toBe(true);
    const lb = JSON.stringify({ username: 'a', password: 'b', n: 'x'.repeat(22) });
    const login = signActor(input({ session: { sid: '' }, method: 'POST', path: '/v1/session', body: lb }));
    expect(verifyActor(login, KEY, NOW + 1, 'POST', '/v1/session', lb).ok).toBe(true);
    const meta = signActor(input({ session: { sid: '' } }));
    expect(verifyActor(meta, KEY, NOW + 1, 'GET', '/v1/meta', '').ok).toBe(true);
    const del = signActor(input({ method: 'DELETE', path: '/v1/session' }));
    expect(verifyActor(del, KEY, NOW + 1, 'DELETE', '/v1/session', '').ok).toBe(true);
  });

  it('jti acak 22 karakter base64url (16 byte) dan berbeda tiap panggilan', () => {
    const a = newJti();
    const b = newJti();
    expect(a).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(a).not.toBe(b);
    const payload = JSON.parse(buildActorPayload(input({ jti: undefined }))) as { jti: string };
    expect(payload.jti).toMatch(/^[A-Za-z0-9_-]{22}$/);
  });
});

describe('ppa2: ikatan method + path + body, kedaluwarsa, replay', () => {
  const body = JSON.stringify({ row_version: 1 });
  const h = signActor(input({ method: 'POST', path: '/v1/plans/x/approve', body }));

  it('method, path (termasuk query), atau body berbeda -> ditolak', () => {
    expect(verifyActor(h, KEY, NOW, 'PUT', '/v1/plans/x/approve', body)).toEqual({ ok: false, reason: 'mp' });
    expect(verifyActor(h, KEY, NOW, 'POST', '/v1/plans/y/approve', body)).toEqual({ ok: false, reason: 'mp' });
    expect(verifyActor(h, KEY, NOW, 'POST', '/v1/plans/x/approve?a=1', body)).toEqual({ ok: false, reason: 'mp' });
    expect(verifyActor(h, KEY, NOW, 'POST', '/v1/plans/x/approve', JSON.stringify({ row_version: 2 }))).toEqual({ ok: false, reason: 'bh' });
    expect(verifyActor(h, KEY, NOW, 'POST', '/v1/plans/x/approve', '')).toEqual({ ok: false, reason: 'bh' });
  });

  it('umur 60 dtk: now >= exp ditolak, iat di masa depan > 30 dtk ditolak', () => {
    const t = signActor(input());
    expect(verifyActor(t, KEY, NOW + 59, 'GET', '/v1/meta', '').ok).toBe(true);
    expect(verifyActor(t, KEY, NOW + 60, 'GET', '/v1/meta', '')).toEqual({ ok: false, reason: 'time' });
    expect(verifyActor(t, KEY, NOW - 31, 'GET', '/v1/meta', '')).toEqual({ ok: false, reason: 'time' });
    expect(verifyActor(t, KEY, NOW - 30, 'GET', '/v1/meta', '').ok).toBe(true);
  });

  it('kunci salah atau payload diubah -> HMAC gagal; jti dipakai ulang -> replay', () => {
    const t = signActor(input());
    expect(verifyActor(t, 'cd'.repeat(32), NOW, 'GET', '/v1/meta', '')).toEqual({ ok: false, reason: 'hmac' });
    const [pre, b64, sig] = t.split('.');
    const tampered = Buffer.from(Buffer.from(b64, 'base64url').toString('utf8').replace(SID, 'T'.repeat(43)), 'utf8').toString('base64url');
    expect(verifyActor(`${pre}.${tampered}.${sig}`, KEY, NOW, 'GET', '/v1/meta', '')).toEqual({ ok: false, reason: 'hmac' });
    const seen = new Set<string>();
    expect(verifyActor(t, KEY, NOW, 'GET', '/v1/meta', '', seen).ok).toBe(true);
    expect(verifyActor(t, KEY, NOW, 'GET', '/v1/meta', '', seen)).toEqual({ ok: false, reason: 'replay' });
  });

  it('sid tak dikenal -> session_expired (di sisi prodplan)', () => {
    const t = signActor(input({ session: { sid: 'Z'.repeat(43) } }));
    expect(verifyActor(t, KEY, NOW, 'GET', '/v1/meta', '')).toEqual({ ok: false, reason: 'session_expired' });
  });
});

describe('ppa2: tanpa identitas dari insight-web; aturan sid', () => {
  const reason = (i: ActorInput): string => {
    try {
      signActor(i);
      return 'ok';
    } catch (e) {
      return e instanceof ActorError ? e.reason : 'other';
    }
  };

  it('payload tidak memuat sub/name/roles/codes (identitas hanya dari sesi prodplan)', () => {
    const p = JSON.parse(buildActorPayload(input())) as Record<string, unknown>;
    for (const k of ['sub', 'name', 'roles', 'codes']) expect(p).not.toHaveProperty(k);
  });

  it('sid kosong HANYA untuk POST /v1/session dan GET /v1/meta; selain itu ditolak sebelum ditandatangani', () => {
    expect(reason(input({ session: { sid: '' }, method: 'POST', path: '/v1/session' }))).toBe('ok');
    expect(reason(input({ session: { sid: '' }, method: 'GET', path: '/v1/meta' }))).toBe('ok');
    expect(reason(input({ session: { sid: '' }, method: 'GET', path: '/v1/plans' }))).toBe('bad_sid');
    expect(reason(input({ session: { sid: '' }, method: 'DELETE', path: '/v1/session' }))).toBe('bad_sid');
    expect(reason(input({ session: { sid: '' }, method: 'GET', path: '/v1/session' }))).toBe('bad_sid');
    expect(reason(input({ session: { sid: '' }, method: 'POST', path: '/v1/meta' }))).toBe('bad_sid');
  });

  it('sid bukan 43 karakter base64url -> ditolak', () => {
    expect(reason(input({ session: { sid: 'short' } }))).toBe('bad_sid');
    expect(reason(input({ session: { sid: `${'a'.repeat(42)}=` } }))).toBe('bad_sid');
    expect(reason(input({ session: { sid: 'a'.repeat(44) } }))).toBe('bad_sid');
  });

  it('kunci tidak sah, method/path tidak sah -> ditolak', () => {
    expect(reason(input({ key: 'abc' }))).toBe('bad_key');
    expect(reason(input({ key: 'zz'.repeat(32) }))).toBe('bad_key');
    expect(reason(input({ method: 'TRACE' }))).toBe('bad_method');
    expect(reason(input({ path: '/healthz' }))).toBe('bad_path');
    expect(reason(input({ path: '/v1/../x' }))).toBe('bad_path');
    expect(reason(input({ path: '/v1/a b' }))).toBe('bad_path');
  });

  it('bodyHash: body kosong = hash string kosong; base64url tanpa padding 43 karakter', () => {
    expect(bodyHash('')).toBe(createHash('sha256').update('').digest('base64url'));
    expect(bodyHash('{}')).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });
});
