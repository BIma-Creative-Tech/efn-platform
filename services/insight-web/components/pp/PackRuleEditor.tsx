'use client';
/**
 * Master kemasan utuh (`pack-rules`, docs/04 §30.8): daftar + tambah/ubah/nonaktifkan. Hanya pp_planner yang bisa mengubah
 * (`editable`); kepala gudang melihat saja. Satuan harus sama dengan satuan dasar produk di ESB (diperiksa prodplan).
 */
import { useRouter } from 'next/navigation';
import { useId, useRef, useState, useTransition } from 'react';
import { announce } from '@/lib/client-nav';
import { useBusy } from '@/lib/loading-store';
import { callAction } from '@/lib/prodplan-call';
import { fmtPpQty, fmtShelfLife, toNum } from '@/lib/prodplan-format';
import type { PackRule } from '@/lib/prodplan-types';
import { Term } from '@/components/Term';

interface Form {
  product_code: string;
  base_uom: string;
  pack_qty: string;
  opened_shelf_life_hours: string;
  notes: string;
  active: boolean;
  row_version: number | null;
}

const EMPTY: Form = { product_code: '', base_uom: '', pack_qty: '', opened_shelf_life_hours: '', notes: '', active: true, row_version: null };

const fromRule = (r: PackRule): Form => ({
  product_code: r.product_code,
  base_uom: r.base_uom,
  pack_qty: String(toNum(r.pack_qty) ?? ''),
  opened_shelf_life_hours: r.opened_shelf_life_hours === null ? '' : String(r.opened_shelf_life_hours),
  notes: r.notes ?? '',
  active: r.active,
  row_version: r.row_version,
});

export function PackRuleEditor({ brand, rules, editable }: { brand: string; rules: readonly PackRule[]; editable: boolean }): React.ReactNode {
  const router = useRouter();
  const uid = useId();
  const [form, setForm] = useState<Form | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const codeRef = useRef<HTMLInputElement | null>(null);
  useBusy(pending);

  const send = (f: Form, okText: string): void => {
    setError(null);
    setOk(null);
    start(async () => {
      const res = await callAction('pack-rule-put', {
        brand,
        product_code: f.product_code.trim(),
        base_uom: f.base_uom.trim(),
        pack_qty: f.pack_qty.trim().replace(',', '.'),
        opened_shelf_life_hours: f.opened_shelf_life_hours.trim(),
        notes: f.notes,
        active: f.active,
        ...(f.row_version === null ? {} : { row_version: f.row_version }),
      });
      if (!res.ok) {
        const names = res.fields.length > 0 ? ` (isian: ${res.fields.join(', ')})` : '';
        setError(`${res.message}${names}`);
        announce(res.message);
        return;
      }
      setOk(okText);
      announce(okText);
      setForm(null);
      router.refresh();
    });
  };

  const set = (patch: Partial<Form>): void => setForm((f) => (f ? { ...f, ...patch } : f));

  return (
    <div className="pp-packrules">
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel kemasan utuh">
        <table className="table">
          <caption className="sr-only">Master kemasan utuh brand {brand}</caption>
          <thead>
            <tr>
              <th scope="col">Produk</th>
              <th scope="col">Satuan</th>
              <th scope="col" className="num">
                Isi per kemasan
              </th>
              <th scope="col">Umur setelah dibuka</th>
              <th scope="col">Status</th>
              <th scope="col">Catatan</th>
              {editable ? <th scope="col">Aksi</th> : null}
            </tr>
          </thead>
          <tbody>
            {rules.length === 0 ? (
              <tr>
                <td colSpan={editable ? 7 : 6} className="muted">
                  Belum ada aturan kemasan utuh untuk brand ini. Tanpa aturan, kebutuhan bahan tidak dibulatkan ke kemasan.
                </td>
              </tr>
            ) : null}
            {rules.map((r) => (
              <tr key={r.product_code}>
                <th scope="row">
                  {r.product_name || r.product_code}
                  {r.product_name ? <span className="muted small"> ({r.product_code})</span> : null}
                </th>
                <td>{r.base_uom}</td>
                <td className="num">{fmtPpQty(r.pack_qty)}</td>
                <td>{fmtShelfLife(r.opened_shelf_life_hours)}</td>
                <td>
                  <span className={`pill ${r.active ? 'pill-good' : 'pill-neutral'}`}>{r.active ? 'Aktif' : 'Nonaktif'}</span>
                </td>
                <td className="wrap">{r.notes ?? '-'}</td>
                {editable ? (
                  <td>
                    <div className="pp-row-actions">
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={pending}
                        onClick={() => {
                          setOk(null);
                          setError(null);
                          setForm(fromRule(r));
                          window.setTimeout(() => codeRef.current?.focus(), 0);
                        }}
                      >
                        Ubah<span className="sr-only"> {r.product_code}</span>
                      </button>
                      <button type="button" className="btn btn-sm" disabled={pending} onClick={() => send({ ...fromRule(r), active: !r.active }, r.active ? 'Aturan dinonaktifkan.' : 'Aturan diaktifkan.')}>
                        {r.active ? 'Nonaktifkan' : 'Aktifkan'}
                        <span className="sr-only"> {r.product_code}</span>
                      </button>
                    </div>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editable && form === null ? (
        <p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              setOk(null);
              setError(null);
              setForm({ ...EMPTY });
              window.setTimeout(() => codeRef.current?.focus(), 0);
            }}
          >
            Tambah aturan kemasan
          </button>
        </p>
      ) : null}

      {editable && form !== null ? (
        <form
          className="pp-form card"
          aria-label={form.row_version === null ? 'Tambah aturan kemasan utuh' : `Ubah aturan kemasan ${form.product_code}`}
          onSubmit={(e) => {
            e.preventDefault();
            send(form, form.row_version === null ? 'Aturan kemasan ditambahkan.' : 'Aturan kemasan disimpan.');
          }}
        >
          <h3 className="card-title">{form.row_version === null ? 'Tambah aturan kemasan utuh' : `Ubah aturan: ${form.product_code}`}</h3>
          <p className="muted small">
            Kemasan utuh <Term id="kemasan-utuh" />: bahan dibulatkan ke atas ke kelipatan isi kemasan, sisa kemasan terbuka dicatat.
          </p>
          <div className="pp-field">
            <label htmlFor={`${uid}-code`}>Kode produk *</label>
            <input
              id={`${uid}-code`}
              ref={codeRef}
              className="input"
              required
              maxLength={40}
              readOnly={form.row_version !== null}
              value={form.product_code}
              onChange={(e) => set({ product_code: e.target.value })}
            />
          </div>
          <div className="pp-field">
            <label htmlFor={`${uid}-uom`}>Satuan dasar * (mis. ML, KG)</label>
            <input id={`${uid}-uom`} className="input" required maxLength={20} value={form.base_uom} onChange={(e) => set({ base_uom: e.target.value })} />
          </div>
          <div className="pp-field">
            <label htmlFor={`${uid}-qty`}>Isi per kemasan * (dalam satuan dasar)</label>
            <input id={`${uid}-qty`} className="input" required inputMode="decimal" value={form.pack_qty} onChange={(e) => set({ pack_qty: e.target.value })} />
          </div>
          <div className="pp-field">
            <label htmlFor={`${uid}-life`}>Umur setelah dibuka (jam, 1 sampai 720; boleh kosong)</label>
            <input id={`${uid}-life`} className="input" inputMode="numeric" value={form.opened_shelf_life_hours} onChange={(e) => set({ opened_shelf_life_hours: e.target.value })} />
          </div>
          <div className="pp-field">
            <label htmlFor={`${uid}-notes`}>Catatan (maks 500 karakter)</label>
            <textarea id={`${uid}-notes`} className="input" rows={2} maxLength={500} value={form.notes} onChange={(e) => set({ notes: e.target.value })} />
          </div>
          <div className="pp-check">
            <input id={`${uid}-active`} type="checkbox" checked={form.active} onChange={(e) => set({ active: e.target.checked })} />
            <label htmlFor={`${uid}-active`}>Aktif</label>
          </div>
          <div className="pp-form-actions">
            <button type="submit" className="btn btn-primary" disabled={pending}>
              {pending ? 'Menyimpan...' : 'Simpan'}
            </button>
            <button type="button" className="btn" disabled={pending} onClick={() => setForm(null)}>
              Batal
            </button>
          </div>
        </form>
      ) : null}

      {error ? (
        <p role="alert" className="pp-msg pp-msg-error">
          <strong>Gagal:</strong> {error}
        </p>
      ) : null}
      {ok ? (
        <p role="status" className="pp-msg pp-msg-ok">
          {ok}
        </p>
      ) : null}
    </div>
  );
}
