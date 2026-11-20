/**
 * Logger JSON-per-baris ke stdout/stderr. ATURAN (ADR-044 §3.10, UU PDP): nomor HP selalu `maskMsisdn`, isi pesan,
 * QR/kode pairing, token, dan material sesi TIDAK PERNAH di-log. Field hanya tipe primitif.
 */
export type LogLevel = 'debug' | 'info' | 'warn' | 'error';
const ORDER: Record<LogLevel, number> = { debug: 10, info: 20, warn: 30, error: 40 };

export type LogFields = Record<string, string | number | boolean | null | undefined>;

export interface Log {
  debug(msg: string, f?: LogFields): void;
  info(msg: string, f?: LogFields): void;
  warn(msg: string, f?: LogFields): void;
  error(msg: string, f?: LogFields): void;
}

export function maskMsisdn(msisdn: string): string {
  if (typeof msisdn !== 'string' || msisdn.length < 9) return '****';
  return `${msisdn.slice(0, 5)}****${msisdn.slice(-3)}`;
}

export function createLog(minLevel: LogLevel = 'info', write: (line: string, level: LogLevel) => void = defaultWrite): Log {
  const emit = (level: LogLevel, msg: string, f?: LogFields): void => {
    if (ORDER[level] < ORDER[minLevel]) return;
    write(JSON.stringify({ t: new Date().toISOString(), level, svc: 'wa-unofficial', msg, ...(f ?? {}) }), level);
  };
  return {
    debug: (m, f) => emit('debug', m, f),
    info: (m, f) => emit('info', m, f),
    warn: (m, f) => emit('warn', m, f),
    error: (m, f) => emit('error', m, f),
  };
}

function defaultWrite(line: string, level: LogLevel): void {
  if (level === 'error' || level === 'warn') process.stderr.write(`${line}\n`);
  else process.stdout.write(`${line}\n`);
}

export function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
