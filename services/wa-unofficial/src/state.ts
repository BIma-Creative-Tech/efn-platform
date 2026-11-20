import { EncryptedDir } from './encrypted-files';

/**
 * State lokal terenkripsi (volume sesi, AES-256-GCM) — service TIDAK punya akses PostgreSQL/Redis (ADR-044 §3.2):
 * - `processed`: idempotency_key yang sudah diproses (48 jam) → consumer idempoten walau pesan dikirim ulang broker;
 * - kuota harian global + per penerima (`to_hash`), hari = tanggal WIB → batas bertahan saat container restart;
 * - `optOut`: to_hash yang membalas STOP (7 hari) — penekan lokal sampai BFF mencabut consent.
 * Isi hanya pseudonim (to_hash HMAC) + kunci idempotensi; tidak ada nomor/isi pesan.
 */
const LOGICAL = 'state';
const PROCESSED_TTL_MS = 48 * 3600_000;
const OPTOUT_TTL_MS = 7 * 24 * 3600_000;
const WIB_MS = 7 * 3600_000;

interface StateData {
  v: 1;
  processed: Record<string, number>;
  day: string;
  dayCount: number;
  perMember: Record<string, number>;
  optOut: Record<string, number>;
}

export function wibDay(nowMs: number): string {
  return new Date(nowMs + WIB_MS).toISOString().slice(0, 10);
}

function emptyState(nowMs: number): StateData {
  return { v: 1, processed: {}, day: wibDay(nowMs), dayCount: 0, perMember: {}, optOut: {} };
}

export interface StateBackend {
  read(logical: string): Promise<Buffer | null>;
  write(logical: string, data: Buffer): Promise<void>;
}

export class PersistentState {
  private constructor(
    private readonly backend: StateBackend,
    private data: StateData,
  ) {}

  static async load(backend: StateBackend | EncryptedDir, nowMs: number): Promise<PersistentState> {
    const raw = await backend.read(LOGICAL);
    let data = emptyState(nowMs);
    if (raw) {
      const parsed = JSON.parse(raw.toString('utf8')) as Partial<StateData>;
      if (parsed && parsed.v === 1) {
        data = {
          v: 1,
          processed: parsed.processed ?? {},
          day: typeof parsed.day === 'string' ? parsed.day : wibDay(nowMs),
          dayCount: typeof parsed.dayCount === 'number' ? parsed.dayCount : 0,
          perMember: parsed.perMember ?? {},
          optOut: parsed.optOut ?? {},
        };
      }
    }
    const s = new PersistentState(backend, data);
    s.roll(nowMs);
    s.prune(nowMs);
    return s;
  }

  private roll(nowMs: number): void {
    const d = wibDay(nowMs);
    if (this.data.day !== d) {
      this.data.day = d;
      this.data.dayCount = 0;
      this.data.perMember = {};
    }
  }

  prune(nowMs: number): void {
    for (const [k, t] of Object.entries(this.data.processed)) if (nowMs - t > PROCESSED_TTL_MS) delete this.data.processed[k];
    for (const [k, t] of Object.entries(this.data.optOut)) if (nowMs - t > OPTOUT_TTL_MS) delete this.data.optOut[k];
  }

  isProcessed(key: string): boolean {
    return Object.prototype.hasOwnProperty.call(this.data.processed, key);
  }

  markProcessed(key: string, nowMs: number): void {
    this.data.processed[key] = nowMs;
  }

  isOptedOut(hash: string, nowMs: number): boolean {
    const t = this.data.optOut[hash];
    return typeof t === 'number' && nowMs - t <= OPTOUT_TTL_MS;
  }

  markOptOut(hash: string, nowMs: number): void {
    this.data.optOut[hash] = nowMs;
  }

  dayCount(nowMs: number): number {
    this.roll(nowMs);
    return this.data.dayCount;
  }

  memberCount(hash: string, nowMs: number): number {
    this.roll(nowMs);
    return this.data.perMember[hash] ?? 0;
  }

  recordSend(hash: string, nowMs: number): void {
    this.roll(nowMs);
    this.data.dayCount += 1;
    this.data.perMember[hash] = (this.data.perMember[hash] ?? 0) + 1;
  }

  async flush(): Promise<void> {
    await this.backend.write(LOGICAL, Buffer.from(JSON.stringify(this.data), 'utf8'));
  }
}

/** Jendela geser 60 detik untuk `WA_UNOFFICIAL_RATE_PER_MIN` (di memori; restart = jendela kosong, aman karena jitter). */
export class MinuteWindow {
  private readonly stamps: number[] = [];

  constructor(private readonly limit: number) {}

  /** ms yang harus ditunggu sebelum slot berikut tersedia (0 = boleh sekarang). */
  waitMs(nowMs: number): number {
    while (this.stamps.length > 0 && nowMs - this.stamps[0] >= 60_000) this.stamps.shift();
    if (this.stamps.length < this.limit) return 0;
    return this.stamps[0] + 60_000 - nowMs;
  }

  record(nowMs: number): void {
    this.stamps.push(nowMs);
  }
}
