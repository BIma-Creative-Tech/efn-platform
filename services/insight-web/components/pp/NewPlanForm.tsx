'use client';
/**
 * Form "Buat rencana produksi": brand, cabang Central Kitchen + lokasi produksi (dari `meta`, hanya yang diizinkan
 * `PRODPLAN_CK_BRANCHES`), tanggal produksi, catatan. Setelah sukses pindah ke halaman rencana.
 */
import { useRouter } from 'next/navigation';
import { useId, useState, useTransition } from 'react';
import { announce } from '@/lib/client-nav';
import { useBusy } from '@/lib/loading-store';
import { callAction } from '@/lib/prodplan-call';

export interface CkOption {
  /** `<brand>|<branch_id>|<location_id>` */
  value: string;
  brand: string;
  branchId: number;
  locationId: number;
  label: string;
}

export function NewPlanForm({ options, defaultDate }: { options: readonly CkOption[]; defaultDate: string }): React.ReactNode {
  const router = useRouter();
  const uid = useId();
  const [ck, setCk] = useState(options[0]?.value ?? '');
  const [date, setDate] = useState(defaultDate);
  const [notes, setNotes] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  useBusy(pending);

  const chosen = options.find((o) => o.value === ck);

  const submit = (): void => {
    setError(null);
    if (!chosen) {
      setError('Pilih cabang produksi.');
      return;
    }
    start(async () => {
      const res = await callAction('plan-create', {
        brand: chosen.brand,
        branch_id: chosen.branchId,
        production_location_id: chosen.locationId,
        production_date: date,
        notes,
      });
      if (!res.ok) {
        const names = res.fields.length > 0 ? ` (isian: ${res.fields.join(', ')})` : '';
        setError(`${res.message}${names}`);
        announce(res.message);
        return;
      }
      const d = res.data;
      const id = typeof d === 'object' && d !== null && typeof (d as { id?: unknown }).id === 'string' ? (d as { id: string }).id : null;
      announce('Rencana dibuat.');
      router.push(id && /^[0-9a-f-]{36}$/.test(id) ? `/produksi/${id}` : '/produksi');
    });
  };

  if (options.length === 0) {
    return <p className="muted">Belum ada cabang Central Kitchen yang dikonfigurasi untuk PT Anda. Hubungi administrator.</p>;
  }

  return (
    <form
      className="pp-form card"
      aria-label="Buat rencana produksi"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="pp-field">
        <label htmlFor={`${uid}-ck`}>Cabang produksi (Central Kitchen) *</label>
        <select id={`${uid}-ck`} className="input" value={ck} onChange={(e) => setCk(e.target.value)} required>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      <div className="pp-field">
        <label htmlFor={`${uid}-date`}>Tanggal produksi *</label>
        <input id={`${uid}-date`} className="input" type="date" required value={date} onChange={(e) => setDate(e.target.value)} />
      </div>
      <div className="pp-field">
        <label htmlFor={`${uid}-notes`}>Catatan (maks 1000 karakter)</label>
        <textarea id={`${uid}-notes`} className="input" rows={3} maxLength={1000} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </div>
      <div className="pp-form-actions">
        <button type="submit" className="btn btn-primary" disabled={pending}>
          {pending ? 'Membuat...' : 'Buat rencana'}
        </button>
      </div>
      {error ? (
        <p role="alert" className="pp-msg pp-msg-error">
          <strong>Gagal:</strong> {error}
        </p>
      ) : null}
    </form>
  );
}
