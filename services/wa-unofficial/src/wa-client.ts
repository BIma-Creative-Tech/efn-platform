/**
 * Antarmuka klien WhatsApp. Implementasi produksi = `baileys-client.ts` (library tidak resmi, ADR-044); test memakai
 * fake. Logika kuota/allowlist/idempotensi ada di `processor.ts` (tidak bergantung library), sehingga mengganti
 * library tidak menyentuh pagar keamanan.
 */
export type SessionState = 'idle' | 'connecting' | 'connected' | 'disconnected' | 'logged_out' | 'banned';

/** Status untuk kontrak admin (§28.9: `connected|disconnected|banned`). */
export function contractStatus(s: SessionState): 'connected' | 'disconnected' | 'banned' {
  if (s === 'connected') return 'connected';
  if (s === 'banned') return 'banned';
  return 'disconnected';
}

export interface PairResult {
  qr?: string;
  code?: string;
}

export interface WaClient {
  state(): SessionState;
  /** Nomor akun sendiri yang sudah di-mask (`62812****789`), `null` bila belum terhubung. */
  selfMasked(): string | null;
  start(): Promise<void>;
  stop(): Promise<void>;
  isOnWhatsApp(e164: string): Promise<boolean>;
  /** Kirim teks; mengembalikan id pesan WA (hanya di-hash bila disimpan). */
  sendText(e164: string, text: string): Promise<string | undefined>;
  pair(method: 'qr' | 'code', phoneE164?: string): Promise<PairResult>;
  logout(): Promise<void>;
  /** `fromE164` = null bila pengirim tidak bisa dipetakan ke nomor (mis. alamat LID). */
  onInboundText(handler: (fromE164: string | null, text: string) => void): void;
  onStateChange(handler: (s: SessionState) => void): void;
}
