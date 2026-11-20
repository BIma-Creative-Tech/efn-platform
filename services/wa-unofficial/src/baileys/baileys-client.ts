import { EncryptedDir } from '../encrypted-files';
import { e164ToJid, jidToE164, senderE164 } from '../jid';
import { Log, errMsg, maskMsisdn } from '../log';
import type { PairResult, SessionState, WaClient } from '../wa-client';
import { BaileysAuthHelpers, EncryptedAuthState, useEncryptedAuthState } from './encrypted-auth-state';

/**
 * Adapter Baileys (`@whiskeysockets/baileys`, lisensi MIT, library TIDAK RESMI — ADR-044). Versi di-pin exact di
 * package.json; pembaruan hanya lewat PR yang dibaca manusia (R-S21).
 *
 * Desain:
 * - Modul dimuat dinamis (`import()` dengan specifier variabel) dan dipakai lewat antarmuka struktural minimal: tidak
 *   ada ketergantungan tipe kompilasi ke Baileys, kompatibel dengan build CJS (6.x) maupun ESM (7.x).
 * - Auth state terenkripsi (`encrypted-auth-state.ts`), tidak pernah `useMultiFileAuthState` (JSON polos).
 * - Logger Baileys diganti adaptor yang membuang objek (bisa memuat JID/nomor) dan hanya meneruskan pesan warn/error.
 * - `printQRInTerminal: false`, `markOnlineOnConnect: false`, `syncFullHistory: false`; QR hanya lewat endpoint admin.
 * - Proxy egress opsional (`WA_UNOFFICIAL_PROXY_URL`) untuk websocket + fetch media (allowlist host = devsecops).
 * - Putus: 401 (logged out) → `logged_out`, tidak reconnect (perlu pairing); 403 → `banned`, tidak reconnect;
 *   515 (restart required) → reconnect segera; lainnya → reconnect backoff 2–60 dtk.
 *
 * PERILAKU INI BELUM DIUJI dengan nomor WhatsApp nyata (lihat README §Belum terverifikasi).
 */
const BAILEYS_SPECIFIER = '@whiskeysockets/baileys';

interface BaileysSocket {
  ev: { on(event: string, cb: (arg: unknown) => void): void };
  user?: { id?: unknown };
  sendMessage(jid: string, content: { text: string }): Promise<unknown>;
  onWhatsApp(...jids: string[]): Promise<unknown>;
  requestPairingCode(phone: string): Promise<string>;
  logout(msg?: string): Promise<void>;
  end(err: Error | undefined): void;
}

interface BaileysModule extends BaileysAuthHelpers {
  makeWASocket: (opts: Record<string, unknown>) => BaileysSocket;
  makeCacheableSignalKeyStore?: (store: unknown, logger: unknown) => unknown;
  Browsers?: { ubuntu?: (name: string) => unknown };
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return typeof v === 'object' && v !== null ? (v as Record<string, unknown>) : null;
}

async function loadBaileys(): Promise<BaileysModule> {
  const spec = BAILEYS_SPECIFIER;
  const raw = asRecord(await import(spec)) ?? {};
  const def = asRecord(raw.default);
  // CJS lewat import(): ekspor bernama bisa berada di `default`
  const pick = (k: string): unknown => raw[k] ?? def?.[k];
  const make = typeof raw.default === 'function' ? raw.default : pick('makeWASocket') ?? pick('default');
  const mod = {
    makeWASocket: make,
    initAuthCreds: pick('initAuthCreds'),
    BufferJSON: pick('BufferJSON'),
    proto: pick('proto'),
    makeCacheableSignalKeyStore: pick('makeCacheableSignalKeyStore'),
    Browsers: pick('Browsers'),
  } as Record<string, unknown>;
  if (typeof mod.makeWASocket !== 'function' || typeof mod.initAuthCreds !== 'function' || !asRecord(mod.BufferJSON)) {
    throw new Error('modul Baileys tidak memiliki API yang diharapkan (versi tidak cocok?)');
  }
  return mod as unknown as BaileysModule;
}

/** Logger kompatibel pino minimal untuk Baileys: membuang objek/argumen terstruktur. */
function baileysLogger(log: Log): Record<string, unknown> {
  const pickMsg = (args: unknown[]): string => {
    const s = args.find((a) => typeof a === 'string');
    return typeof s === 'string' ? s.replace(/\d{8,}/g, '[angka]').slice(0, 200) : '(tanpa pesan)';
  };
  const self: Record<string, unknown> = {
    level: 'warn',
    child: () => self,
    trace: () => undefined,
    debug: () => undefined,
    info: () => undefined,
    warn: (...a: unknown[]) => log.warn(`baileys: ${pickMsg(a)}`),
    error: (...a: unknown[]) => log.error(`baileys: ${pickMsg(a)}`),
    fatal: (...a: unknown[]) => log.error(`baileys: ${pickMsg(a)}`),
  };
  return self;
}

function statusCodeOf(update: Record<string, unknown>): number | undefined {
  const err = asRecord(asRecord(update.lastDisconnect)?.error);
  const out = asRecord(err?.output);
  const code = out?.statusCode;
  return typeof code === 'number' ? code : undefined;
}

export interface BaileysClientOptions {
  stateDir: string;
  sessionKey: Buffer;
  log: Log;
  proxyUrl: string | null;
}

export class BaileysWaClient implements WaClient {
  private sock: BaileysSocket | null = null;
  private auth: EncryptedAuthState | null = null;
  private mod: BaileysModule | null = null;
  private current: SessionState = 'idle';
  private latestQr: { value: string; at: number } | null = null;
  private qrWaiters: ((qr: string) => void)[] = [];
  private inbound: ((from: string | null, text: string) => void)[] = [];
  private stateHandlers: ((s: SessionState) => void)[] = [];
  private backoff = 2000;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private stopped = false;
  private readonly dir: EncryptedDir;

  constructor(private readonly o: BaileysClientOptions) {
    this.dir = new EncryptedDir(`${o.stateDir}/session`, o.sessionKey, 'session');
  }

  state(): SessionState {
    return this.current;
  }

  selfMasked(): string | null {
    const e = jidToE164(this.sock?.user?.id);
    return e ? maskMsisdn(e) : null;
  }

  onInboundText(h: (from: string | null, text: string) => void): void {
    this.inbound.push(h);
  }

  onStateChange(h: (s: SessionState) => void): void {
    this.stateHandlers.push(h);
  }

  /** Mulai bila sudah pernah dipasangkan; bila belum → tetap `logged_out` sampai operator memanggil pair. */
  async start(): Promise<void> {
    this.stopped = false;
    this.mod = await loadBaileys();
    this.auth = await useEncryptedAuthState(this.dir, this.mod);
    if (this.auth.isRegistered()) await this.connect();
    else this.setState('logged_out');
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.sock?.end(undefined);
    this.sock = null;
  }

  async isOnWhatsApp(e164: string): Promise<boolean> {
    const s = this.requireSocket();
    const r = await s.onWhatsApp(e164ToJid(e164));
    const first = Array.isArray(r) ? asRecord(r[0]) : null;
    return first?.exists === true;
  }

  async sendText(e164: string, text: string): Promise<string | undefined> {
    const s = this.requireSocket();
    const m = asRecord(await s.sendMessage(e164ToJid(e164), { text }));
    const id = asRecord(m?.key)?.id;
    return typeof id === 'string' ? id : undefined;
  }

  async pair(method: 'qr' | 'code', phoneE164?: string): Promise<PairResult> {
    if (!this.mod || !this.auth) throw new Error('klien belum dimulai');
    if (this.current === 'banned') throw new Error('nomor diblokir');
    if (this.auth.isRegistered() && this.current !== 'logged_out') throw new Error('sudah dipasangkan');
    if (this.current === 'logged_out' && this.auth.isRegistered()) {
      await this.auth.clear();
      this.auth = await useEncryptedAuthState(this.dir, this.mod);
    }
    if (!this.sock) await this.connect();
    const qr = await this.waitQr(20_000);
    if (method === 'qr') return { qr };
    if (!phoneE164) throw new Error('nomor wajib untuk kode pairing');
    const code = await this.requireSocket().requestPairingCode(phoneE164);
    return { code };
  }

  async logout(): Promise<void> {
    try {
      if (this.sock) await this.sock.logout();
    } catch (err) {
      this.o.log.warn('logout Baileys gagal; material sesi tetap dihapus', { err: errMsg(err) });
    }
    this.sock?.end(undefined);
    this.sock = null;
    await this.auth?.clear();
    if (this.mod) this.auth = await useEncryptedAuthState(this.dir, this.mod);
    this.setState('logged_out');
  }

  private requireSocket(): BaileysSocket {
    if (!this.sock || this.current !== 'connected') throw new Error('sesi WhatsApp tidak terhubung');
    return this.sock;
  }

  private setState(s: SessionState): void {
    if (this.current === s) return;
    this.current = s;
    this.o.log.info('status sesi WhatsApp', { state: s });
    for (const h of this.stateHandlers) h(s);
  }

  private waitQr(timeoutMs: number): Promise<string> {
    if (this.latestQr && Date.now() - this.latestQr.at < 50_000) return Promise.resolve(this.latestQr.value);
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => {
        this.qrWaiters = this.qrWaiters.filter((w) => w !== done);
        reject(new Error('QR tidak diterima tepat waktu'));
      }, timeoutMs);
      t.unref();
      const done = (qr: string): void => {
        clearTimeout(t);
        resolve(qr);
      };
      this.qrWaiters.push(done);
    });
  }

  private async connect(): Promise<void> {
    const mod = this.mod;
    const auth = this.auth;
    if (!mod || !auth) throw new Error('klien belum dimulai');
    this.setState('connecting');
    const logger = baileysLogger(this.o.log);
    let agent: unknown;
    if (this.o.proxyUrl) {
      const { HttpsProxyAgent } = await import('https-proxy-agent');
      agent = new HttpsProxyAgent(this.o.proxyUrl);
    }
    const keys = mod.makeCacheableSignalKeyStore ? mod.makeCacheableSignalKeyStore(auth.state.keys, logger) : auth.state.keys;
    const sock = mod.makeWASocket({
      auth: { creds: auth.state.creds, keys },
      logger,
      printQRInTerminal: false,
      markOnlineOnConnect: false,
      syncFullHistory: false,
      generateHighQualityLinkPreview: false,
      ...(mod.Browsers?.ubuntu ? { browser: mod.Browsers.ubuntu('Chrome') } : {}),
      ...(agent ? { agent, fetchAgent: agent } : {}),
    });
    this.sock = sock;
    sock.ev.on('creds.update', () => {
      auth.saveCreds().catch((err: unknown) => this.o.log.error('gagal menyimpan creds terenkripsi', { err: errMsg(err) }));
    });
    sock.ev.on('connection.update', (u) => this.onConnectionUpdate(sock, asRecord(u) ?? {}));
    sock.ev.on('messages.upsert', (u) => this.onUpsert(asRecord(u) ?? {}));
  }

  private onConnectionUpdate(sock: BaileysSocket, u: Record<string, unknown>): void {
    if (sock !== this.sock) return;
    if (typeof u.qr === 'string' && u.qr.length > 0) {
      this.latestQr = { value: u.qr, at: Date.now() };
      const waiters = this.qrWaiters;
      this.qrWaiters = [];
      for (const w of waiters) w(u.qr);
    }
    if (u.connection === 'open') {
      this.backoff = 2000;
      this.latestQr = null;
      this.setState('connected');
      return;
    }
    if (u.connection !== 'close') return;
    this.sock = null;
    const code = statusCodeOf(u);
    if (code === 403) {
      this.setState('banned');
      return;
    }
    if (code === 401) {
      this.setState('logged_out');
      return;
    }
    this.setState('disconnected');
    if (this.stopped) return;
    const delay = code === 515 ? 0 : this.backoff;
    this.backoff = Math.min(60_000, this.backoff * 2);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect().catch((err: unknown) => this.o.log.error('reconnect WhatsApp gagal', { err: errMsg(err) }));
    }, delay);
    this.reconnectTimer.unref();
  }

  private onUpsert(u: Record<string, unknown>): void {
    if (u.type !== 'notify' || !Array.isArray(u.messages)) return;
    for (const raw of u.messages) {
      const m = asRecord(raw);
      const key = asRecord(m?.key);
      if (!m || !key || key.fromMe === true) continue;
      const content = asRecord(m.message);
      const text = content?.conversation ?? asRecord(content?.extendedTextMessage)?.text;
      if (typeof text !== 'string') continue;
      const from = senderE164(key);
      for (const h of this.inbound) h(from, text);
    }
  }
}
