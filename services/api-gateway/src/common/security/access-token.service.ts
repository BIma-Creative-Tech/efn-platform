import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { sign, verify } from 'jsonwebtoken';
import { AppConfig } from '../../config/app-config';
import { isUuid } from '../validation';

/** Pengguna terautentikasi (diisi JwtAuthGuard setelah memeriksa member aktif di DB). */
export interface AuthUser {
  /** `member.id` (UUID, docs/04 §8.2) = JWT `sub`. */
  memberId: string;
  /** member_hash (kontrak §1) untuk ledger, laporan, consent. */
  memberHash: string;
}

export interface AccessClaims {
  memberId: string;
}

export const ACCESS_TOKEN_ISSUER = 'efn-bff';
export const ACCESS_TOKEN_AUDIENCE = 'efn-superapp';
/** docs/04 §8.4 / PDP_DSAR_DESIGN §3: token step-up `purpose: dsar`, exp 10 menit. */
export const STEP_UP_TTL_SECONDS = 600;
export const STEP_UP_PURPOSE = 'dsar';
const MAX_TOKEN_LENGTH = 4096;

/**
 * Access token pelanggan (kontrak §2/§3.2, v3 §8.2): JWT HS256 (`jwt_secret`), exp 1 jam.
 * Klaim: `sub` = member.id (UUID — BUKAN nomor HP), `typ` = "access", `iss`, `aud`, `iat`, `exp`.
 * Token step-up (DSAR): `typ` = "step_up", `purpose` = "dsar", `jti`, exp 10 menit, `sub` sama.
 * `typ` dibedakan agar token step-up tidak bisa dipakai sebagai access token dan sebaliknya.
 * Verifikasi mengunci algoritma ke HS256 (mencegah alg confusion / `none`).
 */
@Injectable()
export class AccessTokenService {
  now: () => number = () => Date.now();

  constructor(private readonly config: AppConfig) {}

  issue(memberId: string): { accessToken: string; expiresIn: number } {
    const ttl = this.config.accessTokenTtlSeconds;
    return { accessToken: this.sign({ sub: memberId, typ: 'access' }, ttl), expiresIn: ttl };
  }

  /** @returns klaim bila token valid, `null` bila tidak (alasan tidak dibedakan ke klien). */
  verify(token: string): AccessClaims | null {
    const decoded = this.decode(token);
    if (!decoded || decoded.typ !== 'access') return null;
    const sub = decoded.sub;
    if (!isUuid(sub)) return null;
    return { memberId: sub.toLowerCase() };
  }

  issueStepUp(memberId: string): { token: string; expiresIn: number } {
    return {
      token: this.sign({ sub: memberId, typ: 'step_up', purpose: STEP_UP_PURPOSE, jti: randomUUID() }, STEP_UP_TTL_SECONDS),
      expiresIn: STEP_UP_TTL_SECONDS,
    };
  }

  /** true bila token step-up valid DAN milik member yang sama dengan access token. */
  verifyStepUp(token: string | undefined, memberId: string): boolean {
    if (typeof token !== 'string') return false;
    const decoded = this.decode(token);
    if (!decoded || decoded.typ !== 'step_up' || decoded.purpose !== STEP_UP_PURPOSE) return false;
    const sub = decoded.sub;
    return typeof sub === 'string' && sub.toLowerCase() === memberId.toLowerCase();
  }

  private sign(claims: Record<string, unknown>, ttl: number): string {
    const iat = Math.floor(this.now() / 1000);
    return sign({ ...claims, iat, exp: iat + ttl }, this.config.jwtSecret, {
      algorithm: 'HS256',
      issuer: ACCESS_TOKEN_ISSUER,
      audience: ACCESS_TOKEN_AUDIENCE,
    });
  }

  private decode(token: string): Record<string, unknown> | null {
    if (token.length === 0 || token.length > MAX_TOKEN_LENGTH) return null;
    try {
      const decoded = verify(token, this.config.jwtSecret, {
        algorithms: ['HS256'],
        issuer: ACCESS_TOKEN_ISSUER,
        audience: ACCESS_TOKEN_AUDIENCE,
        clockTimestamp: Math.floor(this.now() / 1000),
      });
      return typeof decoded === 'string' ? null : (decoded as Record<string, unknown>);
    } catch {
      return null;
    }
  }
}
