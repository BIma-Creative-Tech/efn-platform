'use client';
/**
 * Tombol aksi Production Order (G66): mengirim satu aksi ke `POST /api/prodplan/<aksi>` (identitas aktor dibuat server dari
 * sesi; tidak ada token di browser). Opsional: form isian inline (alasan, tanggal, centang persetujuan), alasan nonaktif yang
 * TERLIHAT (bukan hanya tombol abu-abu), pemantauan job (impor PR / hitung) dengan polling 2 dtk maks 5 menit, dan
 * penyegaran halaman setelah sukses. Validasi di sini hanya untuk UX; server/prodplan tetap sumber validasi.
 */
import { useRouter } from 'next/navigation';
import { useEffect, useId, useRef, useState, useTransition } from 'react';
import { announce } from '@/lib/client-nav';
import { useBusy } from '@/lib/loading-store';
import { POLL_INTERVAL_MS, callAction, fetchJob, jobDoneText, jobIdOf, jobStep, type JobStep } from '@/lib/prodplan-call';

export interface PpField {
  name: string;
  label: string;
  kind: 'text' | 'textarea' | 'checkbox' | 'date';
  required?: boolean;
  maxLength?: number;
  help?: string;
  /** false = hanya untuk konfirmasi di layar (mis. "Saya memahami ..."), tidak dikirim ke server. */
  send?: boolean;
  defaultValue?: string | boolean;
}

export interface PpActionProps {
  action: string;
  /** Isian tetap yang dikirim (id rencana, row_version, dll). Bukan identitas pengguna. */
  payload: Record<string, unknown>;
  label: string;
  fields?: readonly PpField[];
  /** Form langsung terbuka (mis. konfirmasi posting). */
  defaultOpen?: boolean;
  variant?: 'primary' | 'default';
  small?: boolean;
  /** Alasan tombol nonaktif; ditampilkan sebagai teks. */
  disabledReason?: string | null;
  successMessage?: string;
  /** Setelah sukses: pindah ke path (mis. rencana baru) atau segarkan halaman (bawaan). */
  pushTo?: string;
  /** Aksi "Segarkan stok & hitung ulang" pada rencana disetujui: hasil job menjelaskan apakah rencana kembali ke draf. */
  refresh?: boolean;
}

type Msg = { kind: 'ok' | 'error'; text: string } | null;
type JobUi = { id: string; state: 'running' | 'done' | 'failed'; text: string; reload?: boolean } | null;

function initialValues(fields: readonly PpField[] | undefined): Record<string, string | boolean> {
  const v: Record<string, string | boolean> = {};
  for (const f of fields ?? []) v[f.name] = f.defaultValue ?? (f.kind === 'checkbox' ? false : '');
  return v;
}

export function PpAction(props: PpActionProps): React.ReactNode {
  const { action, payload, label, fields, defaultOpen = false, variant = 'default', small = false, disabledReason, successMessage, pushTo, refresh = false } = props;
  const router = useRouter();
  const uid = useId();
  const [open, setOpen] = useState(defaultOpen);
  const [values, setValues] = useState<Record<string, string | boolean>>(() => initialValues(fields));
  const [msg, setMsg] = useState<Msg>(null);
  const [job, setJob] = useState<JobUi>(null);
  const [pending, start] = useTransition();
  const firstRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  useBusy(pending);

  const hasFields = (fields?.length ?? 0) > 0;
  const blocked = Boolean(disabledReason);
  const reasonId = `${uid}-reason`;

  useEffect(() => {
    if (open && hasFields && !defaultOpen) firstRef.current?.focus();
  }, [open, hasFields, defaultOpen]);

  // Polling job: setState hanya di callback async (bukan sinkron di badan efek).
  const jobId = job?.state === 'running' ? job.id : null;
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const t0 = Date.now();
    const tick = async (): Promise<void> => {
      const r = await fetchJob(jobId);
      if (cancelled) return;
      const elapsed = Date.now() - t0;
      let step: JobStep;
      if (r.ok) step = jobStep(r.job.status, r.job.errorCode, elapsed, r.job.path);
      // Galat otorisasi/tidak ditemukan tidak akan pulih dengan menunggu; galat jaringan sesaat dicoba lagi sampai batas waktu.
      else if (r.status === 401 || r.status === 403 || r.status === 404) step = { kind: 'failed', message: r.message };
      else step = jobStep('running', null, elapsed);
      if (step.kind === 'wait') {
        timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
        return;
      }
      if (step.kind === 'done') {
        const out = r.ok ? jobDoneText(r.job, refresh) : jobDoneText({ planStatus: null, changed: null }, false);
        // Rencana kembali ke draf: tombol ini akan hilang saat halaman dimuat ulang, jadi pesan dibiarkan terbaca dulu.
        setJob({ id: jobId, state: 'done', text: out.text, reload: out.backToDraft });
        announce(out.backToDraft ? out.text : `${out.text} Halaman diperbarui.`);
        if (!out.backToDraft) router.refresh();
      } else {
        setJob({ id: jobId, state: 'failed', text: step.message });
        announce(step.message);
      }
    };
    timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, router, refresh]);

  const submit = (): void => {
    setMsg(null);
    const body: Record<string, unknown> = { ...payload };
    for (const f of fields ?? []) {
      const v = values[f.name];
      if (f.required && (f.kind === 'checkbox' ? v !== true : typeof v !== 'string' || v.trim() === '')) {
        setMsg({ kind: 'error', text: `Isian wajib: ${f.label}.` });
        return;
      }
      if (f.send === false) continue;
      if (f.kind === 'checkbox') body[f.name] = v === true;
      else if (typeof v === 'string' && v.trim() !== '') body[f.name] = v.trim();
    }
    start(async () => {
      const r = await callAction(action, body);
      if (!r.ok) {
        setMsg({ kind: 'error', text: r.message });
        announce(r.message);
        return;
      }
      const text = successMessage ?? 'Berhasil.';
      const jid = jobIdOf(r.data);
      if (jid) {
        setMsg({ kind: 'ok', text: 'Proses dimulai.' });
        setJob({ id: jid, state: 'running', text: 'Sedang diproses. Halaman diperbarui otomatis setelah selesai.' });
        announce('Proses dimulai.');
        return;
      }
      setMsg({ kind: 'ok', text });
      announce(text);
      setOpen(defaultOpen);
      if (pushTo) router.push(pushTo);
      else router.refresh();
    });
  };

  const onButton = (): void => {
    if (hasFields) setOpen((o) => !o);
    else submit();
  };

  return (
    <div className="pp-action">
      {!defaultOpen || blocked ? (
        <button
          type="button"
          className={`btn${variant === 'primary' ? ' btn-primary' : ''}${small ? ' btn-sm' : ''}`}
          disabled={blocked || pending || job?.state === 'running'}
          aria-describedby={blocked ? reasonId : undefined}
          aria-expanded={hasFields && !blocked ? open : undefined}
          onClick={onButton}
        >
          {label}
        </button>
      ) : null}
      {blocked ? (
        <p id={reasonId} className="muted small pp-reason">
          {disabledReason}
        </p>
      ) : null}
      {open && hasFields && !blocked ? (
        <form
          className="pp-form"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          {(fields ?? []).map((f, i) => {
            const id = `${uid}-${f.name}`;
            const helpId = f.help ? `${id}-h` : undefined;
            if (f.kind === 'checkbox') {
              return (
                <div key={f.name} className="pp-check">
                  <input
                    id={id}
                    type="checkbox"
                    checked={values[f.name] === true}
                    aria-describedby={helpId}
                    onChange={(e) => setValues((s) => ({ ...s, [f.name]: e.target.checked }))}
                  />
                  <label htmlFor={id}>{f.label}</label>
                  {f.help ? (
                    <p id={helpId} className="muted small">
                      {f.help}
                    </p>
                  ) : null}
                </div>
              );
            }
            return (
              <div key={f.name} className="pp-field">
                <label htmlFor={id}>
                  {f.label}
                  {f.required ? <span aria-hidden="true"> *</span> : null}
                </label>
                {f.kind === 'textarea' ? (
                  <textarea
                    id={id}
                    className="input"
                    rows={3}
                    maxLength={f.maxLength}
                    required={f.required}
                    aria-describedby={helpId}
                    value={String(values[f.name] ?? '')}
                    ref={i === 0 ? (el) => void (firstRef.current = el) : undefined}
                    onChange={(e) => setValues((s) => ({ ...s, [f.name]: e.target.value }))}
                  />
                ) : (
                  <input
                    id={id}
                    className="input"
                    type={f.kind === 'date' ? 'date' : 'text'}
                    maxLength={f.maxLength}
                    required={f.required}
                    aria-describedby={helpId}
                    value={String(values[f.name] ?? '')}
                    ref={i === 0 ? (el) => void (firstRef.current = el) : undefined}
                    onChange={(e) => setValues((s) => ({ ...s, [f.name]: e.target.value }))}
                  />
                )}
                {f.help ? (
                  <p id={helpId} className="muted small">
                    {f.help}
                  </p>
                ) : null}
              </div>
            );
          })}
          <div className="pp-form-actions">
            <button type="submit" className={`btn${variant === 'primary' ? ' btn-primary' : ''}`} disabled={pending}>
              {pending ? 'Memproses...' : label}
            </button>
            {!defaultOpen ? (
              <button type="button" className="btn" onClick={() => setOpen(false)} disabled={pending}>
                Batal
              </button>
            ) : null}
          </div>
        </form>
      ) : null}
      {msg?.kind === 'error' ? (
        <p role="alert" className="pp-msg pp-msg-error">
          <strong>Gagal:</strong> {msg.text}
        </p>
      ) : null}
      {msg?.kind === 'ok' && !job ? (
        <p role="status" className="pp-msg pp-msg-ok">
          {msg.text}
        </p>
      ) : null}
      {job ? (
        <p role={job.state === 'failed' ? 'alert' : 'status'} className={`pp-msg ${job.state === 'failed' ? 'pp-msg-error' : 'pp-msg-ok'}`}>
          <strong>{job.state === 'running' ? 'Berjalan:' : job.state === 'done' ? 'Selesai:' : 'Gagal:'}</strong> {job.text}
        </p>
      ) : null}
      {job?.reload ? (
        <button type="button" className="btn btn-sm" onClick={() => router.refresh()}>
          Muat ulang halaman
        </button>
      ) : null}
    </div>
  );
}
