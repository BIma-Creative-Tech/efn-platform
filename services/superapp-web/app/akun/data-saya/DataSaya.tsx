'use client';

/**
 * UI DSAR: unduh data, riwayat permintaan, hapus akun (re-OTP step-up -> konfirmasi -> DELETE), batalkan.
 * Semua panggilan ke Route Handler origin sendiri (`/api/me/*`); token sesi & step-up hanya di cookie HttpOnly.
 */
import { useEffect, useRef, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  DELETE_CONFIRM_WORD,
  daysUntil,
  dsarStatusLabel,
  dsarTypeLabel,
  findPendingDeletion,
  formatDateId,
  safeDownloadUrl,
  type DeleteAcceptedParsed,
  type DsarRequest,
  type ExportAccepted,
} from '@/lib/dsar';
import { formatNumber } from '@/lib/format';

type Msg = { kind: 'ok' | 'error'; text: string } | null;

type DeleteStep =
  | { kind: 'idle' }
  | { kind: 'otp'; challengeId: string }
  | { kind: 'confirm' }
  | { kind: 'done'; result: DeleteAcceptedParsed };

async function errorMessage(res: Response, fallback: string): Promise<string> {
  const data = (await res.json().catch(() => null)) as { error?: { message?: string } } | null;
  return data?.error?.message ?? fallback;
}

export function DataSaya({
  pointsBalance,
  requests,
  graceDays,
}: {
  pointsBalance: number | null;
  requests: DsarRequest[] | null;
  graceDays: number;
}) {
  const router = useRouter();
  const pending = requests ? findPendingDeletion(requests) : null;

  // ---------- Unduh data ----------
  const [exportBusy, setExportBusy] = useState(false);
  const [exportMsg, setExportMsg] = useState<Msg>(null);
  const [exportAsync, setExportAsync] = useState<ExportAccepted | null>(null);

  async function downloadData() {
    setExportBusy(true);
    setExportMsg(null);
    setExportAsync(null);
    try {
      const res = await fetch('/api/me/data', { method: 'GET', cache: 'no-store' });
      if (res.status === 200) {
        const blob = await res.blob();
        const cd = res.headers.get('content-disposition') ?? '';
        const name = /filename="([^"]+)"/.exec(cd)?.[1] ?? 'data-saya-efn.json';
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 10_000);
        setExportMsg({ kind: 'ok', text: 'Berkas data Anda telah diunduh. Simpan di tempat yang aman.' });
      } else if (res.status === 202) {
        const data = (await res.json().catch(() => null)) as ExportAccepted | null;
        if (data) setExportAsync({ ...data, download_url: safeDownloadUrl(data.download_url) });
        setExportMsg({
          kind: 'ok',
          text: 'Data Anda sedang disiapkan. Tautan unduh akan tersedia di riwayat permintaan dan berlaku 24 jam.',
        });
        router.refresh();
      } else if (res.status === 401) {
        window.location.assign('/masuk?next=/akun/data-saya&expired=1');
      } else if (res.status === 403) {
        // docs/04 §8.9: export wajib token step-up -> kirim OTP ke nomor akun, lalu ulangi unduhan (G6 QA)
        await requestExportReauth();
      } else if (res.status === 429) {
        setExportMsg({ kind: 'error', text: 'Terlalu banyak permintaan. Coba lagi nanti.' });
      } else {
        setExportMsg({ kind: 'error', text: await errorMessage(res, 'Data belum dapat diunduh. Silakan coba lagi.') });
      }
    } catch {
      setExportMsg({ kind: 'error', text: 'Koneksi bermasalah. Periksa internet Anda lalu coba lagi.' });
    } finally {
      setExportBusy(false);
    }
  }

  // Step-up untuk export (token step-up berlaku 10 menit, dipakai bersama dengan hapus akun; cookie HttpOnly).
  const [exportOtp, setExportOtp] = useState<{ challengeId: string } | null>(null);
  const [exportCode, setExportCode] = useState('');

  async function requestExportReauth() {
    const res = await fetch('/api/me/reauth', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}',
    });
    if (res.status === 202) {
      const data = (await res.json()) as { challenge_id: string };
      setExportCode('');
      setExportOtp({ challengeId: data.challenge_id });
      setExportMsg({ kind: 'ok', text: 'Untuk keamanan, masukkan kode yang dikirim lewat WhatsApp ke nomor akun Anda.' });
    } else if (res.status === 401) {
      window.location.assign('/masuk?next=/akun/data-saya&expired=1');
    } else if (res.status === 429) {
      setExportMsg({ kind: 'error', text: 'Terlalu banyak permintaan. Coba lagi beberapa menit lagi.' });
    } else {
      setExportMsg({ kind: 'error', text: await errorMessage(res, 'Kode verifikasi belum dapat dikirim.') });
    }
  }

  async function verifyExportReauth(e: FormEvent) {
    e.preventDefault();
    if (!exportOtp) return;
    if (!/^\d{6}$/.test(exportCode)) {
      setExportMsg({ kind: 'error', text: 'Masukkan 6 digit kode.' });
      return;
    }
    setExportBusy(true);
    try {
      const res = await fetch('/api/me/reauth', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ challenge_id: exportOtp.challengeId, code: exportCode }),
      });
      if (res.ok) {
        setExportOtp(null);
        setExportCode('');
        setExportBusy(false);
        await downloadData();
        return;
      }
      if (res.status === 400) {
        const data = (await res.json().catch(() => ({}))) as { reason?: string; attempts_left?: number };
        if (data.reason === 'expired' || data.reason === 'locked') {
          setExportOtp(null);
          setExportMsg({ kind: 'error', text: 'Kode kedaluwarsa atau terlalu banyak percobaan. Ulangi unduh data.' });
        } else {
          setExportMsg({
            kind: 'error',
            text:
              typeof data.attempts_left === 'number'
                ? `Kode tidak cocok. Sisa percobaan: ${data.attempts_left}.`
                : 'Kode tidak cocok.',
          });
        }
      } else if (res.status === 401) {
        window.location.assign('/masuk?next=/akun/data-saya&expired=1');
      } else {
        setExportMsg({ kind: 'error', text: await errorMessage(res, 'Verifikasi belum dapat diproses.') });
      }
    } catch {
      setExportMsg({ kind: 'error', text: 'Koneksi bermasalah. Periksa internet Anda lalu coba lagi.' });
    } finally {
      setExportBusy(false);
    }
  }

  // ---------- Hapus akun ----------
  const [step, setStep] = useState<DeleteStep>({ kind: 'idle' });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [code, setCode] = useState('');
  const [ack, setAck] = useState(false);
  const [confirmWord, setConfirmWord] = useState('');
  const otpRef = useRef<HTMLInputElement>(null);
  const confirmHeadingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (step.kind === 'otp') otpRef.current?.focus();
    if (step.kind === 'confirm' || step.kind === 'done') confirmHeadingRef.current?.focus();
  }, [step.kind]);

  function resetDelete(message: Msg = null) {
    setStep({ kind: 'idle' });
    setCode('');
    setAck(false);
    setConfirmWord('');
    setMsg(message);
  }

  async function requestReauth() {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch('/api/me/reauth', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{}',
      });
      if (res.status === 202) {
        const data = (await res.json()) as { challenge_id: string };
        setCode('');
        setStep({ kind: 'otp', challengeId: data.challenge_id });
        setMsg({ kind: 'ok', text: 'Kode verifikasi telah dikirim lewat WhatsApp ke nomor akun Anda.' });
      } else if (res.status === 401) {
        window.location.assign('/masuk?next=/akun/data-saya&expired=1');
      } else if (res.status === 429) {
        setMsg({ kind: 'error', text: 'Terlalu banyak permintaan. Coba lagi beberapa menit lagi.' });
      } else {
        setMsg({ kind: 'error', text: await errorMessage(res, 'Kode verifikasi belum dapat dikirim.') });
      }
    } catch {
      setMsg({ kind: 'error', text: 'Koneksi bermasalah. Periksa internet Anda lalu coba lagi.' });
    } finally {
      setBusy(false);
    }
  }

  async function verifyReauth(e: FormEvent) {
    e.preventDefault();
    if (step.kind !== 'otp') return;
    setMsg(null);
    if (!/^\d{6}$/.test(code)) {
      setMsg({ kind: 'error', text: 'Masukkan 6 digit kode.' });
      return;
    }
    setBusy(true);
    try {
      const res = await fetch('/api/me/reauth', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ challenge_id: step.challengeId, code }),
      });
      if (res.ok) {
        setStep({ kind: 'confirm' });
        setMsg(null);
        return;
      }
      if (res.status === 400) {
        const data = (await res.json().catch(() => ({}))) as { reason?: string; attempts_left?: number };
        if (data.reason === 'expired' || data.reason === 'locked') {
          resetDelete({ kind: 'error', text: 'Kode kedaluwarsa atau terlalu banyak percobaan. Minta kode baru.' });
        } else {
          setMsg({
            kind: 'error',
            text:
              typeof data.attempts_left === 'number'
                ? `Kode tidak cocok. Sisa percobaan: ${data.attempts_left}.`
                : 'Kode tidak cocok.',
          });
        }
      } else if (res.status === 401) {
        window.location.assign('/masuk?next=/akun/data-saya&expired=1');
      } else if (res.status === 429) {
        setMsg({ kind: 'error', text: 'Terlalu banyak percobaan. Coba lagi beberapa menit lagi.' });
      } else {
        setMsg({ kind: 'error', text: await errorMessage(res, 'Verifikasi belum dapat diproses.') });
      }
    } catch {
      setMsg({ kind: 'error', text: 'Koneksi bermasalah. Periksa internet Anda lalu coba lagi.' });
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    if (!ack || confirmWord.trim() !== DELETE_CONFIRM_WORD) {
      setMsg({ kind: 'error', text: `Centang pernyataan dan ketik ${DELETE_CONFIRM_WORD} untuk melanjutkan.` });
      return;
    }
    setBusy(true);
    try {
      const res = await fetch('/api/me', {
        method: 'DELETE',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ confirm: DELETE_CONFIRM_WORD, acknowledge_points: true }),
      });
      if (res.status === 202) {
        const result = (await res.json()) as DeleteAcceptedParsed;
        setStep({ kind: 'done', result });
        return;
      }
      if (res.status === 403) {
        resetDelete({ kind: 'error', text: await errorMessage(res, 'Verifikasi ulang diperlukan.') });
      } else if (res.status === 401) {
        window.location.assign('/masuk?next=/akun/data-saya&expired=1');
      } else {
        setMsg({ kind: 'error', text: await errorMessage(res, 'Permintaan hapus akun belum dapat diproses.') });
      }
    } catch {
      setMsg({ kind: 'error', text: 'Koneksi bermasalah. Periksa internet Anda lalu coba lagi.' });
    } finally {
      setBusy(false);
    }
  }

  // ---------- Batalkan penghapusan ----------
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelMsg, setCancelMsg] = useState<Msg>(null);

  async function cancelDeletion() {
    setCancelBusy(true);
    setCancelMsg(null);
    try {
      const res = await fetch('/api/me/delete/cancel', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{}',
      });
      if (res.ok) {
        setCancelMsg({ kind: 'ok', text: 'Permintaan hapus akun dibatalkan. Akun Anda tetap aktif.' });
        router.refresh();
      } else if (res.status === 401) {
        window.location.assign('/masuk?next=/akun/data-saya&expired=1');
      } else {
        setCancelMsg({ kind: 'error', text: await errorMessage(res, 'Pembatalan belum dapat diproses.') });
      }
    } catch {
      setCancelMsg({ kind: 'error', text: 'Koneksi bermasalah. Periksa internet Anda lalu coba lagi.' });
    } finally {
      setCancelBusy(false);
    }
  }

  const points = pointsBalance ?? 0;

  return (
    <div className="stack">
      {/* ---------- Unduh data ---------- */}
      <section className="card stack" aria-labelledby="unduh-judul">
        <h2 id="unduh-judul" style={{ margin: 0 }}>
          Unduh data saya
        </h2>
        <p style={{ margin: 0 }}>
          Salinan berformat JSON: profil (nomor HP disamarkan), riwayat poin, laporan Pantau Harga, persetujuan, dan
          daftar pemroses data. Data besar disiapkan terlebih dahulu dan tautannya berlaku 24 jam.
        </p>
        <div aria-live="polite">
          {exportMsg && (
            <p className={`alert ${exportMsg.kind === 'ok' ? 'alert-success' : 'alert-error'}`} role="status">
              {exportMsg.text}
            </p>
          )}
          {exportAsync?.download_url && (
            <p>
              <a href={exportAsync.download_url} rel="noopener noreferrer" target="_blank">
                Unduh data (tautan sementara)
              </a>
              {exportAsync.expires_at ? ` — berlaku sampai ${formatDateId(exportAsync.expires_at)}` : ''}
            </p>
          )}
        </div>
        {exportOtp ? (
          <form onSubmit={verifyExportReauth} className="stack" noValidate>
            <div className="field">
              <label htmlFor="export-otp">Kode verifikasi (6 digit)</label>
              <input
                id="export-otp"
                name="export-otp"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="\d{6}"
                maxLength={6}
                value={exportCode}
                onChange={(e) => setExportCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                required
              />
            </div>
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
              <button type="submit" className="btn" disabled={exportBusy}>
                {exportBusy ? 'Memeriksa…' : 'Verifikasi & unduh'}
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => {
                  setExportOtp(null);
                  setExportCode('');
                  setExportMsg(null);
                }}
                disabled={exportBusy}
              >
                Batal
              </button>
            </div>
          </form>
        ) : (
          <p style={{ margin: 0 }}>
            <button type="button" className="btn" onClick={downloadData} disabled={exportBusy}>
              {exportBusy ? 'Menyiapkan…' : 'Unduh data saya'}
            </button>
          </p>
        )}
      </section>

      {/* ---------- Riwayat ---------- */}
      <section className="card stack" aria-labelledby="riwayat-judul">
        <h2 id="riwayat-judul" style={{ margin: 0 }}>
          Riwayat permintaan
        </h2>
        {requests === null ? (
          <p className="alert alert-error" role="alert">
            Riwayat permintaan belum dapat dimuat.
          </p>
        ) : requests.length === 0 ? (
          <p className="muted" style={{ margin: 0 }}>
            Belum ada permintaan.
          </p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="cart-table">
              <caption className="visually-hidden">Riwayat permintaan data pribadi</caption>
              <thead>
                <tr>
                  <th scope="col">Jenis</th>
                  <th scope="col">Status</th>
                  <th scope="col">Diajukan</th>
                  <th scope="col">Keterangan</th>
                </tr>
              </thead>
              <tbody>
                {requests.map((r) => (
                  <tr key={r.id}>
                    <td>{dsarTypeLabel(r.type)}</td>
                    <td>{dsarStatusLabel(r.status)}</td>
                    <td>{formatDateId(r.requested_at)}</td>
                    <td>
                      {r.type === 'delete' && r.scheduled_at && !r.completed_at
                        ? `Dijadwalkan ${formatDateId(r.scheduled_at)}`
                        : r.completed_at
                          ? `Selesai ${formatDateId(r.completed_at)}`
                          : ''}
                      {r.download_url && (
                        <>
                          {' '}
                          <a href={r.download_url} rel="noopener noreferrer" target="_blank">
                            Unduh
                          </a>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ---------- Hapus akun ---------- */}
      <section className="card stack" aria-labelledby="hapus-judul">
        <h2 id="hapus-judul" style={{ margin: 0 }}>
          Hapus akun
        </h2>

        {pending && step.kind !== 'done' ? (
          <div className="stack">
            <p className="notice" role="note" style={{ margin: 0 }}>
              Akun Anda dijadwalkan dihapus pada <strong>{formatDateId(pending.scheduled_at)}</strong>
              {daysUntil(pending.scheduled_at) !== null ? ` (${daysUntil(pending.scheduled_at)} hari lagi)` : ''}. Anda
              masih dapat membatalkannya sebelum tanggal tersebut.
            </p>
            <div aria-live="polite">
              {cancelMsg && (
                <p className={`alert ${cancelMsg.kind === 'ok' ? 'alert-success' : 'alert-error'}`} role="status">
                  {cancelMsg.text}
                </p>
              )}
            </div>
            <p style={{ margin: 0 }}>
              <button type="button" className="btn" onClick={cancelDeletion} disabled={cancelBusy}>
                {cancelBusy ? 'Membatalkan…' : 'Batalkan penghapusan'}
              </button>
            </p>
          </div>
        ) : (
          <div className="stack">
            <div aria-live="polite">
              {msg && (
                <p className={`alert ${msg.kind === 'ok' ? 'alert-success' : 'alert-error'}`} role="alert">
                  {msg.text}
                </p>
              )}
            </div>

            {step.kind === 'idle' && (
              <>
                <ul style={{ margin: 0 }}>
                  <li>
                    Akun dihapus setelah <strong>masa tenggang {graceDays} hari</strong>; selama itu Anda dapat
                    membatalkan dengan masuk kembali.
                  </li>
                  <li>
                    <strong>Sisa poin ({pointsBalance === null ? 'tidak dapat dimuat' : formatNumber(points)}) hangus</strong>{' '}
                    dan tidak dapat diuangkan.
                  </li>
                  <li>
                    Data pribadi dihapus atau dianonimkan. Catatan transaksi yang wajib disimpan menurut peraturan
                    perpajakan tetap ada tanpa identitas Anda.
                  </li>
                  <li>Anda akan keluar dari semua perangkat.</li>
                </ul>
                <p style={{ margin: 0 }}>
                  Untuk keamanan, kami akan mengirim kode verifikasi ke nomor akun Anda terlebih dahulu.
                </p>
                <p style={{ margin: 0 }}>
                  <button type="button" className="btn btn-danger" onClick={requestReauth} disabled={busy}>
                    {busy ? 'Mengirim kode…' : 'Mulai hapus akun'}
                  </button>
                </p>
              </>
            )}

            {step.kind === 'otp' && (
              <form onSubmit={verifyReauth} className="stack" noValidate>
                <div className="field">
                  <label htmlFor="reauth-otp">Kode verifikasi (6 digit)</label>
                  <input
                    ref={otpRef}
                    id="reauth-otp"
                    name="reauth-otp"
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    pattern="\d{6}"
                    maxLength={6}
                    value={code}
                    onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                    required
                  />
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                  <button type="submit" className="btn" disabled={busy}>
                    {busy ? 'Memeriksa…' : 'Verifikasi'}
                  </button>
                  <button type="button" className="btn btn-secondary" onClick={() => resetDelete()} disabled={busy}>
                    Batal
                  </button>
                </div>
              </form>
            )}

            {step.kind === 'confirm' && (
              <form onSubmit={confirmDelete} className="stack" noValidate>
                <h3 ref={confirmHeadingRef} tabIndex={-1} style={{ margin: 0 }}>
                  Konfirmasi hapus akun
                </h3>
                <p style={{ margin: 0 }}>
                  Verifikasi berhasil dan berlaku 10 menit. Penghapusan dijadwalkan setelah masa tenggang {graceDays}{' '}
                  hari.
                </p>
                <div className="checkbox">
                  <input id="ack-points" type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
                  <label htmlFor="ack-points">
                    Saya mengerti bahwa sisa poin saya
                    {pointsBalance === null ? '' : ` (${formatNumber(points)} poin)`} akan <strong>hangus</strong> dan
                    tidak dapat diuangkan, serta akun saya akan dihapus.
                  </label>
                </div>
                <div className="field">
                  <label htmlFor="confirm-word">
                    Ketik <strong>{DELETE_CONFIRM_WORD}</strong> untuk konfirmasi
                  </label>
                  <input
                    id="confirm-word"
                    name="confirm-word"
                    type="text"
                    autoComplete="off"
                    value={confirmWord}
                    onChange={(e) => setConfirmWord(e.target.value)}
                  />
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                  <button
                    type="submit"
                    className="btn btn-danger"
                    disabled={busy || !ack || confirmWord.trim() !== DELETE_CONFIRM_WORD}
                  >
                    {busy ? 'Memproses…' : 'Hapus akun saya'}
                  </button>
                  <button type="button" className="btn btn-secondary" onClick={() => resetDelete()} disabled={busy}>
                    Batal
                  </button>
                </div>
              </form>
            )}

            {step.kind === 'done' && (
              <div className="stack" role="status">
                <h3 ref={confirmHeadingRef} tabIndex={-1} style={{ margin: 0 }}>
                  Permintaan hapus akun diterima
                </h3>
                <p className="alert alert-success" style={{ margin: 0 }}>
                  Akun dijadwalkan dihapus pada <strong>{formatDateId(step.result.scheduled_at)}</strong>.{' '}
                  {formatNumber(step.result.points_forfeited)} poin hangus. Nomor permintaan: {step.result.request_id}.
                </p>
                <p style={{ margin: 0 }}>
                  Anda telah keluar. Berubah pikiran? Masuk kembali sebelum tanggal tersebut dan buka{' '}
                  <strong>Akun &rarr; Data Saya</strong> untuk membatalkan.
                </p>
                <p style={{ margin: 0 }}>
                  <Link href="/" className="btn btn-secondary">
                    Ke beranda
                  </Link>
                </p>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
