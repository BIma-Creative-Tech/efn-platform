import { Inject, Injectable } from '@nestjs/common';
import { createHash, createHmac, randomBytes, randomInt, timingSafeEqual } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { KV_STORE, KvStore } from '../common/redis/kv-store';

/**
 * Server ALTCHA (docs/04 §8.1, SEC-06) — proof-of-work open source, self-hosted, tanpa pihak ketiga.
 *
 * Implementasi MANUAL dengan `node:crypto` (bukan paket npm `altcha-lib`): versi/API paket tidak dapat diverifikasi
 * dari lingkungan penulis (tanpa Node/npm) dan logikanya kecil. Mengikuti format ALTCHA v1 (altcha.org, "Server
 * integration"), sehingga widget resmi `altcha` (frontend) dapat dipakai apa adanya:
 *
 *   challenge JSON (GET /v1/auth/challenge):
 *     { algorithm: "SHA-256", challenge, maxnumber, salt, signature }
 *     salt      = hex(12 byte acak) + "?expires=<unix detik>&"
 *     number    = randomInt(0..maxnumber)            (rahasia server, tidak dikirim)
 *     challenge = hex(SHA-256(salt + number))
 *     signature = hex(HMAC-SHA256(altcha_hmac_key, challenge))
 *   payload widget (field `altcha` di POST /v1/auth/otp/request) = base64(JSON
 *     { algorithm, challenge, number, salt, signature, took? }).
 *
 * Verifikasi: algoritma SHA-256; salt WAJIB berformat persis di atas (diakhiri `&`, sehingga digit tidak bisa
 * dipindah antara salt dan number — mitigasi "salt splicing"); `expires` belum lewat (dan tidak lebih jauh dari TTL);
 * signature = HMAC(challenge) (waktu-konstan) → challenge benar buatan server, salt/expires tidak bisa diubah karena
 * challenge = SHA-256(salt+number); number bulat 0..maxnumber tanpa nol di depan dan SHA-256(salt+number) = challenge;
 * terakhir **sekali pakai**: `SET altcha:used:<signature> NX EX <sisa TTL + 60>` (Redis).
 * Semua kegagalan dijawab sama (`400 captcha_required`) — tidak ada oracle.
 */
export const ALTCHA_ALGORITHM = 'SHA-256';
const SALT_RE = /^[0-9a-f]{24}\?expires=(\d{10})&$/;
const HEX64_RE = /^[0-9a-f]{64}$/;
const NUMBER_RE = /^(0|[1-9]\d{0,7})$/;
const MAX_PAYLOAD_LENGTH = 2048;
const USED_MARKER_GRACE_SECONDS = 60;

export interface AltchaChallenge {
  algorithm: typeof ALTCHA_ALGORITHM;
  challenge: string;
  maxnumber: number;
  salt: string;
  signature: string;
}

export function sha256Hex(s: string): string {
  return createHash('sha256').update(s, 'utf8').digest('hex');
}

@Injectable()
export class AltchaService {
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(KV_STORE) private readonly kv: KvStore,
  ) {}

  createChallenge(): AltchaChallenge {
    const expires = this.nowSec() + this.config.altchaTtlSeconds;
    const salt = `${randomBytes(12).toString('hex')}?expires=${expires}&`;
    const maxnumber = this.config.altchaMaxNumber;
    const challenge = sha256Hex(`${salt}${randomInt(0, maxnumber + 1)}`);
    return { algorithm: ALTCHA_ALGORITHM, challenge, maxnumber, salt, signature: this.sign(challenge) };
  }

  /** @returns true bila payload valid DAN belum pernah dipakai (sekaligus menandainya terpakai). */
  async verify(payload: string | undefined): Promise<boolean> {
    const p = this.decode(payload);
    if (!p) return false;
    const nowSec = this.nowSec();
    const m = SALT_RE.exec(p.salt);
    if (!m) return false;
    const expires = Number(m[1]);
    if (expires < nowSec || expires > nowSec + this.config.altchaTtlSeconds + USED_MARKER_GRACE_SECONDS) return false;
    if (!safeHexEqual(this.sign(p.challenge), p.signature)) return false;
    if (!NUMBER_RE.test(p.number) || Number(p.number) > this.config.altchaMaxNumber) return false;
    if (!safeHexEqual(sha256Hex(`${p.salt}${p.number}`), p.challenge)) return false;
    return this.kv.setNxEx(`altcha:used:${p.signature}`, '1', expires - nowSec + USED_MARKER_GRACE_SECONDS);
  }

  private decode(payload: string | undefined): { challenge: string; number: string; salt: string; signature: string } | null {
    if (typeof payload !== 'string' || payload.length === 0 || payload.length > MAX_PAYLOAD_LENGTH) return null;
    if (!/^[A-Za-z0-9+/=_-]+$/.test(payload)) return null;
    let o: unknown;
    try {
      o = JSON.parse(Buffer.from(payload, 'base64').toString('utf8'));
    } catch {
      return null;
    }
    if (typeof o !== 'object' || o === null || Array.isArray(o)) return null;
    const r = o as Record<string, unknown>;
    if (r.algorithm !== ALTCHA_ALGORITHM) return null;
    if (typeof r.challenge !== 'string' || !HEX64_RE.test(r.challenge)) return null;
    if (typeof r.signature !== 'string' || !HEX64_RE.test(r.signature)) return null;
    if (typeof r.salt !== 'string' || r.salt.length > 64) return null;
    // Widget mengirim number sebagai angka JSON; string angka juga diterima
    const num = typeof r.number === 'number' && Number.isSafeInteger(r.number) ? String(r.number) : r.number;
    if (typeof num !== 'string') return null;
    return { challenge: r.challenge, number: num, salt: r.salt, signature: r.signature };
  }

  private sign(challenge: string): string {
    return createHmac('sha256', this.config.altchaHmacKey).update(challenge, 'utf8').digest('hex');
  }

  private nowSec(): number {
    return Math.floor(this.now() / 1000);
  }
}

function safeHexEqual(a: string, b: string): boolean {
  const ba = Buffer.from(a, 'hex');
  const bb = Buffer.from(b, 'hex');
  return ba.length === 32 && bb.length === 32 && timingSafeEqual(ba, bb);
}

/** Helper test/dev: selesaikan challenge (brute force seperti widget). Tidak dipakai jalur produksi. */
export function solveAltcha(c: AltchaChallenge): string {
  for (let n = 0; n <= c.maxnumber; n += 1) {
    if (sha256Hex(`${c.salt}${n}`) === c.challenge) {
      return Buffer.from(
        JSON.stringify({ algorithm: c.algorithm, challenge: c.challenge, number: n, salt: c.salt, signature: c.signature }),
      ).toString('base64');
    }
  }
  throw new Error('challenge ALTCHA tidak terpecahkan');
}
