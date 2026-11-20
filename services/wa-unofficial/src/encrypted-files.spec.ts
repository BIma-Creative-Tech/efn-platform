import { randomBytes } from 'node:crypto';
import { mkdtemp, readdir, readFile, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { AeadError, open, seal } from './aead';
import { useEncryptedAuthState } from './baileys/encrypted-auth-state';
import { EncryptedDir } from './encrypted-files';
import { PersistentState, wibDay } from './state';

describe('AES-256-GCM sesi (aead + EncryptedDir)', () => {
  let dir: string;
  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), 'wa-unofficial-test-'));
  });
  afterEach(async () => {
    await rm(dir, { recursive: true, force: true });
  });

  it('seal/open; kunci salah atau AAD lain (berkas dipindah) → AeadError', () => {
    const k = randomBytes(32);
    const blob = seal(k, Buffer.from('rahasia-sesi'), 'efn:wa:session:v1:creds');
    expect(open(k, blob, 'efn:wa:session:v1:creds').toString()).toBe('rahasia-sesi');
    expect(() => open(randomBytes(32), blob, 'efn:wa:session:v1:creds')).toThrow(AeadError);
    expect(() => open(k, blob, 'efn:wa:session:v1:pre-key-1')).toThrow(AeadError);
    const tampered = Buffer.from(blob);
    tampered[20] ^= 0xff;
    expect(() => open(k, tampered, 'efn:wa:session:v1:creds')).toThrow(AeadError);
  });

  it('berkas di disk tidak memuat plaintext; mode 0600 (POSIX)', async () => {
    const d = new EncryptedDir(dir, randomBytes(32), 'session');
    await d.init();
    await d.write('creds', Buffer.from('{"noiseKey":"PLAINTEXT-MARKER"}'));
    const names = (await readdir(dir)).filter((n) => n.endsWith('.bin'));
    expect(names).toEqual(['creds.bin']);
    const raw = await readFile(join(dir, names[0]));
    expect(raw.toString('latin1')).not.toContain('PLAINTEXT-MARKER');
    if (process.platform !== 'win32') expect((await stat(join(dir, names[0]))).mode & 0o777).toBe(0o600);
    expect((await d.read('creds'))?.toString()).toContain('PLAINTEXT-MARKER');
    await d.clear();
    expect(await d.read('creds')).toBeNull();
  });

  it('auth state Baileys terenkripsi: creds baru dibuat, disimpan, dimuat ulang; kunci sinyal set/get/hapus', async () => {
    const key = randomBytes(32);
    const helpers = {
      initAuthCreds: () => ({ registrationId: 7, noiseKey: { private: 'p', public: 'q' } }),
      BufferJSON: { replacer: (_k: string, v: unknown) => v, reviver: (_k: string, v: unknown) => v },
      proto: { Message: { AppStateSyncKeyData: { fromObject: (o: unknown) => ({ wrapped: o }) } } },
    };
    const a = await useEncryptedAuthState(new EncryptedDir(dir, key, 'session'), helpers);
    expect(a.isRegistered()).toBe(false);
    a.state.creds.me = { id: '6281100000000:1@s.whatsapp.net' };
    await a.saveCreds();
    await a.state.keys.set({ 'pre-key': { '1': { k: 1 }, '2': { k: 2 } }, 'app-state-sync-key': { AAA: { x: 1 } } });
    await a.state.keys.set({ 'pre-key': { '2': undefined } });
    const b = await useEncryptedAuthState(new EncryptedDir(dir, key, 'session'), helpers);
    expect(b.isRegistered()).toBe(true);
    expect(await b.state.keys.get('pre-key', ['1', '2'])).toEqual({ '1': { k: 1 }, '2': null });
    expect(await b.state.keys.get('app-state-sync-key', ['AAA'])).toEqual({ AAA: { wrapped: { x: 1 } } });
    // kunci lain → gagal dimuat (tidak ada fallback ke sesi kosong diam-diam)
    await expect(useEncryptedAuthState(new EncryptedDir(dir, randomBytes(32), 'session'), helpers)).rejects.toThrow(AeadError);
    const all = await readdir(dir);
    for (const n of all) expect((await readFile(join(dir, n))).toString('latin1')).not.toContain('6281100000000');
  });

  it('PersistentState: kuota per hari WIB reset tengah malam WIB, bertahan setelah muat ulang', async () => {
    const d = new EncryptedDir(dir, randomBytes(32), 'state');
    await d.init();
    const t = Date.UTC(2026, 10, 20, 16, 30, 0); // 23:30 WIB
    const s = await PersistentState.load(d, t);
    s.recordSend('h1', t);
    await s.flush();
    const again = await PersistentState.load(d, t + 60_000);
    expect(again.dayCount(t + 60_000)).toBe(1);
    expect(again.memberCount('h1', t + 60_000)).toBe(1);
    const nextDay = t + 31 * 60_000; // 00:01 WIB
    expect(wibDay(nextDay)).not.toBe(wibDay(t));
    expect(again.dayCount(nextDay)).toBe(0);
    expect(again.memberCount('h1', nextDay)).toBe(0);
  });
});
