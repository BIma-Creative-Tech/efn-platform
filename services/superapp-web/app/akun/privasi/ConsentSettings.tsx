'use client';

/**
 * Toggle persetujuan per tujuan. Setiap perubahan langsung dicatat server (PUT /api/me/consents ->
 * BFF PUT /v1/me/consents/:purpose, append-only). Tidak ada optimistic update: status di layar hanya
 * berubah setelah server mengonfirmasi, agar tampilan = bukti yang tersimpan.
 */
import { useState } from 'react';
import { isConsentOutdated, type ConsentRecord, type ConsentText } from '@/lib/consent';

interface Item {
  record: ConsentRecord;
  text: ConsentText;
}

function formatDateTime(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat('id-ID', { dateStyle: 'medium', timeStyle: 'short' }).format(d);
}

export function ConsentSettings({ initial }: { initial: Item[] }) {
  const [items, setItems] = useState<Item[]>(initial);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);

  async function update(item: Item, granted: boolean) {
    setBusy(item.text.purpose);
    setMessage(null);
    try {
      const res = await fetch('/api/me/consents', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ purpose: item.text.purpose, granted, version: item.text.version }),
      });
      if (res.ok) {
        setItems((prev) =>
          prev.map((it) =>
            it.text.purpose === item.text.purpose
              ? {
                  ...it,
                  record: { ...it.record, granted, version: it.text.version, updated_at: new Date().toISOString() },
                }
              : it,
          ),
        );
        setMessage({
          kind: 'ok',
          text: granted ? `Persetujuan "${item.text.title}" disimpan.` : `Persetujuan "${item.text.title}" ditarik.`,
        });
        return;
      }
      if (res.status === 401) {
        window.location.assign('/masuk?next=/akun/privasi&expired=1');
        return;
      }
      const data = (await res.json().catch(() => null)) as { error?: { message?: string } } | null;
      setMessage({ kind: 'error', text: data?.error?.message ?? 'Perubahan belum tersimpan. Silakan coba lagi.' });
    } catch {
      setMessage({ kind: 'error', text: 'Koneksi bermasalah. Periksa internet Anda lalu coba lagi.' });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="stack">
      <div aria-live="polite">
        {message && (
          <p className={`alert ${message.kind === 'ok' ? 'alert-success' : 'alert-error'}`} role="status">
            {message.text}
          </p>
        )}
      </div>
      {items.map((item) => {
        const id = `consent-${item.text.purpose}`;
        const outdated = isConsentOutdated(item.record);
        const when = formatDateTime(item.record.updated_at);
        return (
          <section key={item.text.purpose} className="card stack" aria-labelledby={`${id}-title`}>
            <h2 id={`${id}-title`} style={{ margin: 0, fontSize: '1.125rem' }}>
              {item.text.title}
            </h2>
            <p style={{ margin: 0 }} id={`${id}-body`}>
              {item.text.body}
            </p>
            <div className="checkbox">
              <input
                id={id}
                type="checkbox"
                checked={item.record.granted}
                disabled={busy !== null}
                aria-describedby={`${id}-body ${id}-status`}
                onChange={(e) => update(item, e.target.checked)}
              />
              <label htmlFor={id}>Saya setuju</label>
            </div>
            <p id={`${id}-status`} className="hint" style={{ margin: 0 }}>
              {item.record.granted ? 'Aktif' : 'Tidak aktif'}
              {when ? ` · diubah ${when}` : ''} · versi teks {item.text.version}. {!item.record.granted && item.text.whenOff}
            </p>
            {outdated && (
              <div className="notice small" role="note">
                Teks persetujuan ini telah diperbarui sejak Anda menyetujuinya (versi {item.record.version ?? '-'}).{' '}
                <button
                  type="button"
                  className="btn btn-secondary btn-small"
                  disabled={busy !== null}
                  onClick={() => update(item, true)}
                >
                  Setujui versi terbaru
                </button>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
