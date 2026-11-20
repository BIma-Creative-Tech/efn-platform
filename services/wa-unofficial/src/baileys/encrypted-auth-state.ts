import { EncryptedDir } from '../encrypted-files';

/**
 * Auth state Baileys TERENKRIPSI (pengganti `useMultiFileAuthState` bawaan yang menulis JSON polos; ADR-044 §3.5).
 * Pola sama dengan bawaan: satu berkas `creds`, satu berkas per kunci sinyal `<type>-<id>`; setiap berkas dienkripsi
 * AES-256-GCM (`EncryptedDir`, AAD = nama logis). Serialisasi memakai `BufferJSON` milik Baileys (Buffer ↔ JSON).
 * Kunci `app-state-sync-key` dibangun ulang dengan `proto.Message.AppStateSyncKeyData.fromObject` seperti bawaan.
 *
 * Antarmuka Baileys yang dipakai (struktural, agar tidak terkunci ke tipe versi tertentu): `initAuthCreds`,
 * `BufferJSON.{replacer,reviver}`, `proto.Message.AppStateSyncKeyData.fromObject`.
 */
export interface BaileysAuthHelpers {
  initAuthCreds: () => unknown;
  BufferJSON: { replacer: (k: string, v: unknown) => unknown; reviver: (k: string, v: unknown) => unknown };
  proto?: { Message?: { AppStateSyncKeyData?: { fromObject: (o: unknown) => unknown } } };
}

export interface SignalKeyStoreLike {
  get(type: string, ids: string[]): Promise<Record<string, unknown>>;
  set(data: Record<string, Record<string, unknown> | undefined>): Promise<void>;
}

export interface EncryptedAuthState {
  state: { creds: Record<string, unknown>; keys: SignalKeyStoreLike };
  saveCreds(): Promise<void>;
  /** Hapus semua material sesi (logout/ban). */
  clear(): Promise<void>;
  /** true bila creds sudah terdaftar (pairing selesai). */
  isRegistered(): boolean;
}

export async function useEncryptedAuthState(dir: EncryptedDir, b: BaileysAuthHelpers): Promise<EncryptedAuthState> {
  await dir.init();
  const readJson = async (logical: string): Promise<unknown> => {
    const raw = await dir.read(logical);
    return raw ? (JSON.parse(raw.toString('utf8'), b.BufferJSON.reviver) as unknown) : null;
  };
  const writeJson = (logical: string, value: unknown): Promise<void> =>
    dir.write(logical, Buffer.from(JSON.stringify(value, b.BufferJSON.replacer), 'utf8'));

  const stored = (await readJson('creds')) as Record<string, unknown> | null;
  const creds: Record<string, unknown> = stored ?? (b.initAuthCreds() as Record<string, unknown>);

  const keys: SignalKeyStoreLike = {
    async get(type, ids) {
      const out: Record<string, unknown> = {};
      await Promise.all(
        ids.map(async (id) => {
          let value = await readJson(`${type}-${id}`);
          if (type === 'app-state-sync-key' && value && b.proto?.Message?.AppStateSyncKeyData) {
            value = b.proto.Message.AppStateSyncKeyData.fromObject(value);
          }
          out[id] = value;
        }),
      );
      return out;
    },
    async set(data) {
      const tasks: Promise<void>[] = [];
      for (const category of Object.keys(data)) {
        const entries = data[category] ?? {};
        for (const id of Object.keys(entries)) {
          const value = entries[id];
          const logical = `${category}-${id}`;
          tasks.push(value ? writeJson(logical, value) : dir.remove(logical));
        }
      }
      await Promise.all(tasks);
    },
  };

  return {
    state: { creds, keys },
    saveCreds: () => writeJson('creds', creds),
    clear: () => dir.clear(),
    isRegistered: () => creds.registered === true || (typeof creds.me === 'object' && creds.me !== null),
  };
}
