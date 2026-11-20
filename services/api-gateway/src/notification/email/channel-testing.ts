/**
 * Fake tabel platform untuk spec kanal G50 (email + WA unofficial). BUKAN untuk runtime. Emulasi berbasis fragmen SQL
 * yang dipakai kode kanal; query lain → hasil kosong. Tidak memuat secret (kunci dibangkitkan acak di spec).
 */
import { FakeDb } from '../../common/testing/fakes';
import type { QueryResultLike } from '../../common/db/db';

export interface ChannelMember {
  member_hash: string;
  member_ref_enc: Buffer | null;
  deleted: boolean;
}

export interface ChannelEmailRow {
  email_enc: Buffer;
  verified_at: Date | null;
  status: 'active' | 'invalid';
  status_reason: string | null;
}

export interface ConsentRow {
  member_id: string;
  purpose: string;
  granted: boolean;
  version: string;
  text_sha256: string;
  source: string;
  ip_enc: Buffer | null;
}

type Res = QueryResultLike<Record<string, unknown>>;
const empty = (): Res => ({ rows: [], rowCount: 0 });
const one = (row: Record<string, unknown>): Res => ({ rows: [row], rowCount: 1 });

export class ChannelTables {
  readonly members = new Map<string, ChannelMember>();
  readonly emails = new Map<string, ChannelEmailRow>();
  readonly consents: ConsentRow[] = [];
  readonly emailOutbound = new Map<string, { member_id: string; status: string }>();
  readonly unsubscribeUsed = new Set<string>();
  readonly processed = new Set<string>();
  readonly outbox: { queue: string; payload: Record<string, unknown>; messageId: string }[] = [];
  readonly waRecipients = new Map<string, string>();
  readonly waOutbound = new Map<string, { member_id: string; to_hash: string; status: string; reason: string | null }>();
  readonly db: FakeDb;

  constructor() {
    this.db = new FakeDb((text, params) => this.handle(text, params));
  }

  grantMarketing(memberId: string, granted = true): void {
    this.consents.push({ member_id: memberId, purpose: 'marketing', granted, version: 'm@1', text_sha256: '0'.repeat(64), source: 'superapp', ip_enc: null });
  }

  marketingGranted(memberId: string): boolean {
    const rows = this.consents.filter((c) => c.member_id === memberId && c.purpose === 'marketing');
    return rows.length > 0 && rows[rows.length - 1].granted;
  }

  private memberByHash(hash: string): [string, ChannelMember] | undefined {
    return [...this.members.entries()].find(([, m]) => m.member_hash === hash && !m.deleted);
  }

  private handle(text: string, p: readonly unknown[]): Res | undefined {
    if (text.includes('platform.processed_message')) {
      const k = `${String(p[0])}|${String(p[1])}`;
      if (this.processed.has(k)) return empty();
      this.processed.add(k);
      return one({ consumer: p[0] });
    }
    if (text.includes('INSERT INTO platform.outbox')) {
      this.outbox.push({ queue: String(p[0]), payload: JSON.parse(String(p[1])) as Record<string, unknown>, messageId: String(p[2]) });
      return { rows: [], rowCount: 1 };
    }
    if (text.includes('FROM platform.consent')) {
      return this.marketingGranted(String(p[0])) ? one({ granted: true }) : this.consents.some((c) => c.member_id === p[0]) ? one({ granted: false }) : empty();
    }
    if (text.includes('INSERT INTO platform.consent')) {
      this.consents.push({
        member_id: String(p[0]),
        purpose: 'marketing',
        granted: false,
        version: String(p[1]),
        text_sha256: String(p[2]),
        source: String(p[3]),
        ip_enc: (p[4] as Buffer | null) ?? null,
      });
      return { rows: [], rowCount: 1 };
    }
    // EmailNotifier: member by id + email
    if (text.includes('LEFT JOIN platform.member_email') && text.includes('WHERE m.id = $1')) {
      const m = this.members.get(String(p[0]));
      if (!m || m.deleted) return empty();
      const e = this.emails.get(String(p[0]));
      return one({ member_hash: m.member_hash, verified_at: e?.verified_at ?? null, status: e?.status ?? null });
    }
    // Email sender: member by hash + email
    if (text.includes('LEFT JOIN platform.member_email') && text.includes('WHERE m.member_hash = $1')) {
      const hit = this.memberByHash(String(p[0]));
      if (!hit) return empty();
      const e = this.emails.get(hit[0]);
      return one({ id: hit[0], email_enc: e?.email_enc ?? null, verified_at: e?.verified_at ?? null, status: e?.status ?? null });
    }
    if (text.includes('INSERT INTO platform.email_outbound')) {
      if (this.emailOutbound.has(String(p[0]))) return empty();
      this.emailOutbound.set(String(p[0]), { member_id: String(p[1]), status: 'queued' });
      return one({ idempotency_key: p[0] });
    }
    if (text.includes('UPDATE platform.email_outbound')) {
      const r = this.emailOutbound.get(String(p[0]));
      if (r) r.status = String(p[1]);
      return { rows: [], rowCount: r ? 1 : 0 };
    }
    if (text.includes('UPDATE platform.member_email')) {
      const e = this.emails.get(String(p[0]));
      if (e) {
        e.status = 'invalid';
        e.status_reason = 'smtp_rcpt_5xx';
      }
      return { rows: [], rowCount: e ? 1 : 0 };
    }
    if (text.includes('INSERT INTO platform.email_unsubscribe_used')) {
      if (this.unsubscribeUsed.has(String(p[0]))) return empty();
      this.unsubscribeUsed.add(String(p[0]));
      return one({ jti: p[0] });
    }
    // WA notifier
    if (text.includes('SELECT member_hash, member_ref_enc FROM platform.member')) {
      const m = this.members.get(String(p[0]));
      return m && !m.deleted ? one({ member_hash: m.member_hash, member_ref_enc: m.member_ref_enc }) : empty();
    }
    if (text.includes('INSERT INTO platform.wa_unofficial_recipient')) {
      this.waRecipients.set(String(p[0]), String(p[1]));
      return { rows: [], rowCount: 1 };
    }
    if (text.includes('INSERT INTO platform.wa_unofficial_outbound')) {
      if (this.waOutbound.has(String(p[0]))) return empty();
      this.waOutbound.set(String(p[0]), { member_id: String(p[1]), to_hash: String(p[2]), status: 'queued', reason: null });
      return one({ idempotency_key: p[0] });
    }
    if (text.includes('FROM platform.wa_unofficial_recipient')) {
      const m = this.waRecipients.get(String(p[0]));
      return m ? one({ member_id: m }) : empty();
    }
    if (text.includes('UPDATE platform.wa_unofficial_outbound')) {
      const r = this.waOutbound.get(String(p[0]));
      if (!r || r.to_hash !== p[3] || (r.status !== 'queued' && r.status !== 'disconnected')) return { rows: [], rowCount: 0 };
      r.status = String(p[1]);
      r.reason = (p[2] as string | null) ?? null;
      return { rows: [], rowCount: 1 };
    }
    return undefined;
  }
}
