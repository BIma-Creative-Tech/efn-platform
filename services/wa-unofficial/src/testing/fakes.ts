/** Fake untuk spec (tidak ikut build produksi, lihat tsconfig.build.json). */
import { createLog, Log } from '../log';
import type { WaResult } from '../contract';
import type { ResultSink } from '../processor';
import type { StateBackend } from '../state';
import type { PairResult, SessionState, WaClient } from '../wa-client';

export class MemoryBackend implements StateBackend {
  readonly files = new Map<string, Buffer>();
  writes = 0;
  async read(logical: string): Promise<Buffer | null> {
    return this.files.get(logical) ?? null;
  }
  async write(logical: string, data: Buffer): Promise<void> {
    this.writes += 1;
    this.files.set(logical, Buffer.from(data));
  }
}

export class FakeClient implements WaClient {
  current: SessionState = 'connected';
  readonly sent: { to: string; text: string }[] = [];
  notOnWa = new Set<string>();
  failSend = false;
  async start(): Promise<void> {}
  async stop(): Promise<void> {}
  state(): SessionState {
    return this.current;
  }
  selfMasked(): string | null {
    return this.current === 'connected' ? '62811****999' : null;
  }
  async isOnWhatsApp(e164: string): Promise<boolean> {
    return !this.notOnWa.has(e164);
  }
  async sendText(to: string, text: string): Promise<string | undefined> {
    if (this.failSend) throw new Error('socket closed');
    this.sent.push({ to, text });
    return 'MSGID';
  }
  pairCalls: { method: string; phone?: string }[] = [];
  async pair(method: 'qr' | 'code', phone?: string): Promise<PairResult> {
    this.pairCalls.push({ method, phone });
    return method === 'qr' ? { qr: 'QR-PAYLOAD' } : { code: 'ABCD-EFGH' };
  }
  async logout(): Promise<void> {
    this.current = 'logged_out';
  }
  onInboundText(): void {}
  onStateChange(): void {}
}

export class FakeSink implements ResultSink {
  readonly results: { result: WaResult; id: string }[] = [];
  async publish(result: WaResult, id: string): Promise<void> {
    this.results.push({ result, id });
  }
}

export function captureLog(): { log: Log; lines: string[] } {
  const lines: string[] = [];
  return { log: createLog('debug', (l) => lines.push(l)), lines };
}
