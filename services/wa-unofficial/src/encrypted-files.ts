import { mkdir, readFile, readdir, rename, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';
import { open, seal } from './aead';

/**
 * Direktori berkas terenkripsi (AES-256-GCM, `aead.ts`). Tulis atomik (tmp + rename), mode 0600, direktori 0700.
 * Penulisan ke berkas yang sama diserialkan (antrian per nama) agar `creds.update` beruntun tidak saling menimpa.
 * Nama berkas = nama logis yang disanitasi; AAD = `efn:wa:<namespace>:v1:<nama logis>`.
 */
export class EncryptedDir {
  private readonly chains = new Map<string, Promise<unknown>>();

  constructor(
    private readonly dir: string,
    private readonly key: Buffer,
    private readonly namespace: string,
  ) {}

  async init(): Promise<void> {
    await mkdir(this.dir, { recursive: true, mode: 0o700 });
  }

  static fileName(logical: string): string {
    return `${logical.replace(/\//g, '__').replace(/:/g, '-').replace(/[^A-Za-z0-9._@=-]/g, '_')}.bin`;
  }

  private aad(logical: string): string {
    return `efn:wa:${this.namespace}:v1:${logical}`;
  }

  async read(logical: string): Promise<Buffer | null> {
    let blob: Buffer;
    try {
      blob = await readFile(join(this.dir, EncryptedDir.fileName(logical)));
    } catch (err) {
      if ((err as { code?: string }).code === 'ENOENT') return null;
      throw err;
    }
    return open(this.key, blob, this.aad(logical));
  }

  write(logical: string, data: Buffer): Promise<void> {
    return this.serial(logical, async () => {
      const name = EncryptedDir.fileName(logical);
      const tmp = join(this.dir, `.${name}.${randomBytes(6).toString('hex')}.tmp`);
      await writeFile(tmp, seal(this.key, data, this.aad(logical)), { mode: 0o600 });
      await rename(tmp, join(this.dir, name));
    });
  }

  remove(logical: string): Promise<void> {
    return this.serial(logical, async () => {
      await rm(join(this.dir, EncryptedDir.fileName(logical)), { force: true });
    });
  }

  /** Hapus semua berkas `.bin` di direktori (logout / sesi dicabut). */
  async clear(): Promise<void> {
    let names: string[] = [];
    try {
      names = await readdir(this.dir);
    } catch {
      return;
    }
    await Promise.all(names.filter((n) => n.endsWith('.bin') || n.endsWith('.tmp')).map((n) => rm(join(this.dir, n), { force: true })));
  }

  private serial<T>(logical: string, fn: () => Promise<T>): Promise<T> {
    const prev = this.chains.get(logical) ?? Promise.resolve();
    const next = prev.then(fn, fn);
    const settled = next.catch(() => undefined);
    this.chains.set(logical, settled);
    void settled.then(() => {
      if (this.chains.get(logical) === settled) this.chains.delete(logical);
    });
    return next;
  }
}
