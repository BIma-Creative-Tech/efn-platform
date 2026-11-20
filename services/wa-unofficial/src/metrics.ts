import type { SessionState } from './wa-client';

/**
 * Metrik Prometheus (format teks) tanpa dependensi. Label hanya enum (tanpa nomor/to_hash).
 * Dasar alert (devsecops): `EfnWaUnofficialDown` = `efn_wa_unofficial_session_up == 0` selama 15 m saat aktif;
 * `EfnWaUnofficialBanned` = `efn_wa_unofficial_session_banned == 1`.
 */
const STATES: readonly SessionState[] = ['idle', 'connecting', 'connected', 'disconnected', 'logged_out', 'banned'];

export class Metrics {
  private readonly counters = new Map<string, number>();
  session: SessionState = 'idle';
  dayCount = 0;
  enabled = false;

  inc(name: 'sent' | 'failed' | 'rate_limited' | 'dlq' | 'stop_received' | 'stop_unmapped' | 'duplicate', reason = ''): void {
    const k = reason ? `${name}|${reason}` : name;
    this.counters.set(k, (this.counters.get(k) ?? 0) + 1);
  }

  get(name: string, reason = ''): number {
    return this.counters.get(reason ? `${name}|${reason}` : name) ?? 0;
  }

  render(): string {
    const out: string[] = [];
    out.push('# TYPE efn_wa_unofficial_enabled gauge', `efn_wa_unofficial_enabled ${this.enabled ? 1 : 0}`);
    out.push('# TYPE efn_wa_unofficial_session_up gauge', `efn_wa_unofficial_session_up ${this.session === 'connected' ? 1 : 0}`);
    out.push('# TYPE efn_wa_unofficial_session_banned gauge', `efn_wa_unofficial_session_banned ${this.session === 'banned' ? 1 : 0}`);
    out.push('# TYPE efn_wa_unofficial_session_state gauge');
    for (const s of STATES) out.push(`efn_wa_unofficial_session_state{state="${s}"} ${this.session === s ? 1 : 0}`);
    out.push('# TYPE efn_wa_unofficial_sent_today gauge', `efn_wa_unofficial_sent_today ${this.dayCount}`);
    out.push('# TYPE efn_wa_unofficial_messages_total counter');
    for (const [k, v] of [...this.counters.entries()].sort()) {
      const [name, reason] = k.split('|');
      out.push(`efn_wa_unofficial_messages_total{outcome="${name}"${reason ? `,reason="${reason}"` : ''}} ${v}`);
    }
    return `${out.join('\n')}\n`;
  }
}
