/**
 * Asersi aktor `ppa1` (docs/04 §30.7): bentuk, urutan 11 kunci, ikatan method + path + hash body, umur 60 dtk, jti, penolakan sesi
 * tanpa peran PP. Pemverifikasi referensi di bawah menuliskan ulang urutan pemeriksaan prodplan (regex -> HMAC -> kunci tepat -> aud ->
 * waktu -> m/p -> bh -> jti -> tipe) sehingga test ini juga menjaga kecocokan byte dengan sisi Python.
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
  type ActorSession,
} from '@/lib/prodplan-token';

const KEY = 'ab'.repeat(32);
const NOW = 1_795_000_000;
const JTI = 'AAAAAAAAAAAAAAAAAAAAAA';
const KEYS = ['aud', 'bh', 'codes', 'exp', 'iat', 'jti', 'm', 'name', 'p', 'roles', 'sub'];

const session = (over: Partial<ActorSession> = {}): ActorSession => ({
  sub: 21,
  name: 'Rina Planner',
  codes: ['PB'],
  roles: ['pp_planner', 'user'],
  ...over,
});

const input = (over: Partial<ActorInput> = {}): ActorInput => ({
  session: session(),
  method: 'GET',
  path: '/v1/meta',
  body: '',
  key: KEY,
  nowSec: NOW,
  jti: JTI,
  ...over,
});

type Verdict = { ok: true; payload: Record<string, unknown> } | { ok: false; reason: string };

/** Pemverifikasi referensi (mirror §30.7). */
function verifyActor(header: string, key: string, now: number, method: string, path: string, body: string, seenJti = new Set<string>()): Verdict {
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
  return { ok: true, payload };
}

describe('ppa1: bentuk dan vektor byte-exact', () => {
  it('payload = JSON tanpa spasi, tepat 11 kunci berurutan (vektor tetap)', () => {
    const emptyHash = createHash('sha256').update('').digest('base64url');
    expect(buildActorPayload(input())).toBe(
      `{"aud":"prodplan","bh":"${emptyHash}","codes":["PB"],"exp":${NOW + 60},"iat":${NOW},"jti":"${JTI}","m":"GET","name":"Rina Planner","p":"/v1/meta","roles":["pp_planner"],"sub":21}`,
    );
  });

  it('header: regex §30.7, <= 2048 byte, tanda tangan = HMAC-SHA256(byte ASCII string hex kunci, "ppa1." + payload_b64url)', () => {
    const h = signActor(input());
    expect(ACTOR_RE.test(h)).toBe(true);
    expect(Buffer.byteLength(h)).toBeLessThanOrEqual(2048);
    const [prefix, b64, sig] = h.split('.');
    expect(prefix).toBe('ppa1');
    expect(sig).toBe(createHmac('sha256', Buffer.from(KEY, 'ascii')).update(`ppa1.${b64}`).digest('base64url'));
    // BUKAN kunci hasil hex-decode
    expect(sig).not.toBe(createHmac('sha256', Buffer.from(KEY, 'hex')).update(`ppa1.${b64}`).digest('base64url'));
    expect(sig).toHaveLength(43);
    expect(b64).not.toMatch(/[=+/]/);
  });

  it('pemverifikasi referensi menerima token sah untuk GET dan POST dengan body', () => {
    const get = signActor(input());
    expect(verifyActor(get, KEY, NOW + 1, 'GET', '/v1/meta', '').ok).toBe(true);
    const body = JSON.stringify({ row_version: 3 });
    const post = signActor(input({ method: 'POST', path: '/v1/plans/x/submit', body }));
    expect(verifyActor(post, KEY, NOW + 1, 'POST', '/v1/plans/x/submit', body).ok).toBe(true);
  });

  it('nama non-ASCII ditulis \\uXXXX (selaras json.dumps ensure_ascii) dan dipotong 64 code point', () => {
    const p = buildActorPayload(input({ session: session({ name: 'Dewi Çelik' }) }));
    expect(p).toContain('"name":"Dewi \\u00c7elik"');
    const long = buildActorPayload(input({ session: session({ name: 'x'.repeat(100) }) }));
    expect((JSON.parse(long) as { name: string }).name).toHaveLength(64);
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

describe('ppa1: ikatan method + path + body, kedaluwarsa, replay', () => {
  const body = JSON.stringify({ row_version: 1 });
  const h = signActor(input({ method: 'POST', path: '/v1/plans/x/approve', body }));

  it('method, path (termasuk query), atau body berbeda -> ditolak', () => {
    expect(verifyActor(h, KEY, NOW, 'PUT', '/v1/plans/x/approve', body)).toEqual({ ok: false, reason: 'mp' });
    expect(verifyActor(h, KEY, NOW, 'POST', '/v1/plans/y/approve', body)).toEqual({ ok: false, reason: 'mp' });
    expect(verifyActor(h, KEY, NOW, 'POST', '/v1/plans/x/approve?a=1', body)).toEqual({ ok: false, reason: 'mp' });
    expect(verifyActor(h, KEY, NOW, 'POST', '/v1/plans/x/approve', JSON.stringify({ row_version: 2 }))).toEqual({ ok: false, reason: 'bh' });
    expect(verifyActor(h, KEY, NOW, 'POST', '/v1/plans/x/approve', '')).toEqual({ ok: false, reason: 'bh' });
  });

  it('path dengan query ikut terikat persis', () => {
    const t = signActor(input({ path: '/v1/plans?brand=pb&limit=50' }));
    expect(verifyActor(t, KEY, NOW, 'GET', '/v1/plans?brand=pb&limit=50', '').ok).toBe(true);
    expect(verifyActor(t, KEY, NOW, 'GET', '/v1/plans?brand=pb&limit=51', '')).toEqual({ ok: false, reason: 'mp' });
  });

  it('umur 60 dtk: now >= exp ditolak, iat di masa depan > 30 dtk ditolak', () => {
    const t = signActor(input());
    expect(verifyActor(t, KEY, NOW + 59, 'GET', '/v1/meta', '').ok).toBe(true);
    expect(verifyActor(t, KEY, NOW + 60, 'GET', '/v1/meta', '')).toEqual({ ok: false, reason: 'time' });
    expect(verifyActor(t, KEY, NOW - 31, 'GET', '/v1/meta', '')).toEqual({ ok: false, reason: 'time' });
    expect(verifyActor(t, KEY, NOW - 30, 'GET', '/v1/meta', '').ok).toBe(true);
    const p = JSON.parse(buildActorPayload(input())) as { exp: number; iat: number };
    expect(p.exp - p.iat).toBe(60);
  });

  it('kunci salah atau payload diubah -> HMAC gagal; jti dipakai ulang -> replay', () => {
    const t = signActor(input());
    expect(verifyActor(t, 'cd'.repeat(32), NOW, 'GET', '/v1/meta', '')).toEqual({ ok: false, reason: 'hmac' });
    const [pre, b64, sig] = t.split('.');
    const tampered = Buffer.from(Buffer.from(b64, 'base64url').toString('utf8').replace('"sub":21', '"sub":22'), 'utf8').toString('base64url');
    expect(verifyActor(`${pre}.${tampered}.${sig}`, KEY, NOW, 'GET', '/v1/meta', '')).toEqual({ ok: false, reason: 'hmac' });
    const seen = new Set<string>();
    expect(verifyActor(t, KEY, NOW, 'GET', '/v1/meta', '', seen).ok).toBe(true);
    expect(verifyActor(t, KEY, NOW, 'GET', '/v1/meta', '', seen)).toEqual({ ok: false, reason: 'replay' });
  });
});

describe('ppa1: klaim berasal dari sesi, bukan dari tempat lain', () => {
  it('codes = sesi ∩ ESB_CODES (SMD dibuang) terurut; roles = sesi ∩ PP_ROLES terurut', () => {
    const p = JSON.parse(buildActorPayload(input({ session: session({ codes: ['SMD', 'WTZ', 'PB'], roles: ['pp_warehouse', 'exec', 'pp_planner', 'user'] }) }))) as {
      codes: string[];
      roles: string[];
    };
    expect(p.codes).toEqual(['PB', 'WTZ']);
    expect(p.roles).toEqual(['pp_planner', 'pp_warehouse']);
  });

  it('sesi tanpa peran PP, tanpa kode ESB, sub tidak sah -> ActorError (fail closed)', () => {
    const reason = (i: ActorInput): string => {
      try {
        signActor(i);
        return 'ok';
      } catch (e) {
        return e instanceof ActorError ? e.reason : 'other';
      }
    };
    expect(reason(input({ session: session({ roles: ['exec', 'user'] }) }))).toBe('no_pp_role');
    expect(reason(input({ session: session({ codes: ['SMD'] }) }))).toBe('no_esb_code');
    expect(reason(input({ session: session({ sub: 0 }) }))).toBe('bad_session');
  });

  it('kunci tidak sah, method/path tidak sah -> ditolak', () => {
    const reason = (i: ActorInput): string => {
      try {
        signActor(i);
        return 'ok';
      } catch (e) {
        return e instanceof ActorError ? e.reason : 'other';
      }
    };
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
