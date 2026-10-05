import { Inject, Injectable, Logger, OnApplicationBootstrap } from '@nestjs/common';
import { AppConfig } from '../../config/app-config';
import { DB, Db } from '../db/db';
import { PiiCipher } from './crypto';
import { PII_COLUMNS } from './pii-columns';
import { countUnreadable } from './reencrypt';

/**
 * U2: bila ada versi kunci yang dipensiunkan (`PII_RETIRED_VERSIONS`), BFF memastikan saat start bahwa TIDAK ada
 * ciphertext yang memakai versi di luar key ring. Bila ada → gagal start (pesan: jalankan `pii-reencrypt` dulu),
 * karena data itu tidak akan bisa didekripsi (notifikasi, DSAR, /me).
 * DB tidak terjangkau / tabel belum ada → hanya peringatan (start tidak diblokir; dekripsi tetap gagal keras nanti).
 */
@Injectable()
export class PiiKeyRingCheck implements OnApplicationBootstrap {
  private readonly logger = new Logger(PiiKeyRingCheck.name);

  constructor(
    private readonly config: AppConfig,
    private readonly cipher: PiiCipher,
    @Inject(DB) private readonly db: Db,
  ) {}

  async onApplicationBootstrap(): Promise<void> {
    if (this.config.piiRetiredVersions.size === 0) return;
    const problems = await this.findUnreadable();
    if (problems.length > 0) {
      throw new Error(
        `Masih ada ciphertext PII dengan versi kunci pensiun/tidak dimuat (${problems.join(', ')}). ` +
          'Jalankan `node dist/cli/pii-reencrypt.js` dengan kunci lama masih dimuat, baru set PII_RETIRED_VERSIONS.',
      );
    }
  }

  async findUnreadable(): Promise<string[]> {
    const loaded = this.cipher.loadedVersions;
    const problems: string[] = [];
    for (const c of PII_COLUMNS) {
      try {
        const n = await countUnreadable(this.db, c, loaded);
        if (n > 0) problems.push(`${c.table}.${c.column}=${n}`);
      } catch (err) {
        this.logger.warn(`Cek key ring PII ${c.table}.${c.column} dilewati: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    return problems;
  }
}
