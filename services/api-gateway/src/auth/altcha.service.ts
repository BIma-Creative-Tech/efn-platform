import { Inject, Injectable, Optional } from '@nestjs/common';
import { createHash, createHmac, randomBytes, randomInt, timingSafeEqual } from 'node:crypto';
import { isIP } from 'node:net';
import { AppConfig } from '../config/app-config';
import { MetricsService } from '../common/metrics/metrics.service';
import { KV_STORE, KvStore } from '../common/redis/kv-store';

/**
 * Server ALTCHA (docs/04 §8.1, SEC-06, SEC-34) — proof-of-work open source, self-hosted, tanpa pihak ketiga.
 *
 * Implementasi MANUAL dengan `node:crypto` (bukan paket npm `altcha-lib`): logikanya kecil dan format mengikuti
 * ALTCHA v1 (altcha.org, "Server integration"), sehingga widget resmi `altcha` (frontend) dapat dipakai apa adanya:
 *
 *   challenge JSON (GET /v1/auth/challenge):
 *     { algorithm: "SHA-256", challenge, maxnumber, salt, signature }
 *     salt      = hex(12 byte acak) + "?expires=<unix detik>&"
 *     number    = randomInt(0..maxnumber)            (rahasia server, tidak dikirim)
 *     challenge = hex(SHA-256(salt + number))
 *     signature = hex(HMAC-SHA256(altcha_hmac_key, "efn-altcha:v2\n" + challenge + "\n" + ip_binding))
 *   payload widget (field `altcha` di POST /v1/auth/otp/request) = base64(JSON
 *     { algorithm, challenge, number, salt, signature, took? }).
 *
 * SEC-34 (Gelombang 12):
 * - **Terikat IP klien**: `ip_binding` = IPv4 utuh, atau prefix /64 untuk IPv6 (alamat privasi IPv6 berganti di
 *   dalam /64 yang sama). IP tidak dikirim ke klien dan tidak disimpan — hanya masuk ke input HMAC. Solusi yang
 *   dipecahkan di satu IP (mis. farm solver) tidak dapat dipakai dari IP lain → `400 captcha_required`.
 *   Widget tidak memverifikasi signature, jadi format JSON tetap kompatibel.
 * - **Biaya adaptif**: `maxnumber` dipilih per challenge oleh `AuthService` (lihat `altchaPressureLevel`).
 *   Verifikasi menerima number ≤ `ALTCHA_MAX_NUMBER_CAP` (nilai number sendiri ditentukan server lewat challenge,
 *   tidak dapat dipilih penyerang).
 *
 * Verifikasi: algoritma SHA-256; salt WAJIB berformat persis di atas (diakhiri `&` — mitigasi "salt splicing");
 * `expires` belum lewat (dan tidak lebih jauh dari TTL); signature cocok untuk IP pemanggil (waktu-konstan);
 * number bulat 0..cap tanpa nol di depan dan SHA-256(salt+number) = challenge; terakhir **sekali pakai**:
 * `SET altcha:used:<signature> NX EX <sisa TTL + 60>` (Redis). Semua kegagalan dijawab sama
 * (`400 captcha_required`) — tidak ada oracle (termasuk tidak membedakan "IP berbeda").
 */
export const ALTCHA_ALGORITHM = 'SHA-256';
const SIGNATURE_DOMAIN = 'efn-altcha:v2';
const SALT_RE = /^[0-9a-f]{24}\?expires=(\d{10})&$/;
const HEX64_RE = /^[0-9a-f]{64}$/;
const NUMBER_RE = /^(0|[1-9]\d{0,7})$/;
const MAX_PAYLOAD_LENGTH = 2048;
const USED_MARKER_GRACE_SECONDS = 60;

/** Ambang rasio pemakaian kuota per IP (challenge atau OTP request) → level 1/2/3. */
export const ALTCHA_IP_THRESHOLDS = [0.25, 0.5, 0.75] as const;
/** Ambang rasio budget OTP login global per jam → level 1/2/3 (lebih tinggi: trafik puncak sah tidak dihukum). */
export const ALTCHA_GLOBAL_THRESHOLDS = [0.5, 0.75, 0.9] as const;
/**
 * SEC-54 (Gelombang 13c): ambang rasio GAGAL per IP (ALTCHA tidak valid / kode OTP salah) → level 1/2/3. Rasio, bukan
 * volume: IP CGNAT/kantor dengan banyak pengguna sah punya volume tinggi tetapi rasio gagal rendah, sedangkan bot
 * (solver/penebak kode) gagal jauh lebih sering.
 */
export const ALTCHA_FAIL_THRESHOLDS = [0.3, 0.5, 0.75] as const;

export interface AltchaChallenge {
  algorithm: typeof ALTCHA_ALGORITHM;
  challenge: string;
  maxnumber: number;
  salt: string;
  signature: string;
}

/** Counter Redis yang sudah ada (rate limiter), dibaca tanpa menambah. */
export interface AltchaPressure {
  ipChallenges: number;
  ipChallengeLimit: number;
  ipOtpRequests: number;
  ipOtpLimit: number;
  globalOtpRequests: number;
  globalOtpBudget: number;
  /** SEC-54: counter kegagalan per IP (opsional). Rasio hanya dihitung bila percobaan ≥ `minSample`. */
  failures?: {
    altchaFails: number;
    altchaAttempts: number;
    otpVerifyFails: number;
    otpVerifyAttempts: number;
    minSample: number;
  };
}

/** SEC-54: rasio gagal tertinggi (ALTCHA, verify OTP) per IP; 0 bila sampel belum cukup. */
export function altchaFailRatio(f: NonNullable<AltchaPressure['failures']>): number {
  const ratio = (fails: number, attempts: number): number =>
    attempts >= Math.max(1, f.minSample) ? Math.min(1, fails / attempts) : 0;
  return Math.max(ratio(f.altchaFails, f.altchaAttempts), ratio(f.otpVerifyFails, f.otpVerifyAttempts));
}

function levelFor(ratio: number, thresholds: readonly number[]): number {
  return thresholds.filter((t) => ratio >= t).length;
}

/** Level tekanan 0..3 = maksimum level volume per IP, level global, dan level rasio gagal per IP (SEC-54). */
export function altchaPressureLevel(p: AltchaPressure): number {
  const ipRatio = Math.max(p.ipChallenges / Math.max(1, p.ipChallengeLimit), p.ipOtpRequests / Math.max(1, p.ipOtpLimit));
  const globalRatio = p.globalOtpRequests / Math.max(1, p.globalOtpBudget);
  const failLevel = p.failures ? levelFor(altchaFailRatio(p.failures), ALTCHA_FAIL_THRESHOLDS) : 0;
  return Math.max(levelFor(ipRatio, ALTCHA_IP_THRESHOLDS), levelFor(globalRatio, ALTCHA_GLOBAL_THRESHOLDS), failLevel);
}

/** maxnumber = base × 2^level, dipotong ke cap. */
export function adaptiveAltchaMaxNumber(base: number, cap: number, level: number): number {
  return Math.min(cap, base * 2 ** Math.max(0, Math.min(3, level)));
}

/** Pengikat IP untuk HMAC: IPv4 utuh, IPv6 → prefix /64 kanonik, lainnya → 'unknown'. */
export function altchaIpBinding(ip: string | null): string {
  if (!ip) return 'unknown';
  const fam = isIP(ip);
  if (fam === 4) return ip;
  if (fam === 6) return ipv6Prefix64(ip);
  return 'unknown';
}

function ipv6Prefix64(ip: string): string {
  let s = ip.toLowerCase();
  const zone = s.indexOf('%');
  if (zone >= 0) s = s.slice(0, zone);
  const lastColon = s.lastIndexOf(':');
  const tail = s.slice(lastColon + 1);
  if (tail.includes('.')) {
    const p = tail.split('.').map(Number);
    s = `${s.slice(0, lastColon + 1)}${((p[0] << 8) | p[1]).toString(16)}:${((p[2] << 8) | p[3]).toString(16)}`;
  }
  const parts = s.split('::');
  const head = parts[0] ? parts[0].split(':') : [];
  const rest = parts.length > 1 && parts[1] ? parts[1].split(':') : [];
  const groups = parts.length > 1 ? [...head, ...Array<string>(8 - head.length - rest.length).fill('0'), ...rest] : head;
  return `${groups
    .slice(0, 4)
    .map((g) => parseInt(g, 16).toString(16))
    .join(':')}::/64`;
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
    @Optional() private readonly metrics?: MetricsService,
  ) {}

  /**
   * @param ip IP klien (sudah melalui trust proxy) — diikat ke signature.
   * @param maxnumber biaya challenge (default `ALTCHA_MAX_NUMBER`), dipotong ke 1000..`ALTCHA_MAX_NUMBER_CAP`.
   * @param level level tekanan (hanya untuk metrik).
   */
  createChallenge(ip: string | null, maxnumber: number = this.config.altchaMaxNumber, level = 0): AltchaChallenge {
    const mx = Math.max(1000, Math.min(this.config.altchaMaxNumberCap, Math.floor(maxnumber)));
    const expires = this.nowSec() + this.config.altchaTtlSeconds;
    const salt = `${randomBytes(12).toString('hex')}?expires=${expires}&`;
    const challenge = sha256Hex(`${salt}${randomInt(0, mx + 1)}`);
    this.metrics?.recordAltchaChallenge(level);
    return { algorithm: ALTCHA_ALGORITHM, challenge, maxnumber: mx, salt, signature: this.sign(challenge, ip) };
  }

  /** @returns true bila payload valid untuk IP ini DAN belum pernah dipakai (sekaligus menandainya terpakai). */
  async verify(payload: string | undefined, ip: string | null): Promise<boolean> {
    const p = this.decode(payload);
    if (!p) return false;
    const nowSec = this.nowSec();
    const m = SALT_RE.exec(p.salt);
    if (!m) return false;
    const expires = Number(m[1]);
    if (expires < nowSec || expires > nowSec + this.config.altchaTtlSeconds + USED_MARKER_GRACE_SECONDS) return false;
    if (!safeHexEqual(this.sign(p.challenge, ip), p.signature)) return false;
    if (!NUMBER_RE.test(p.number) || Number(p.number) > this.config.altchaMaxNumberCap) return false;
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

  private sign(challenge: string, ip: string | null): string {
    return createHmac('sha256', this.config.altchaHmacKey)
      .update(`${SIGNATURE_DOMAIN}\n${challenge}\n${altchaIpBinding(ip)}`, 'utf8')
      .digest('hex');
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
