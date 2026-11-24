'use client';
/**
 * Editor baris permintaan produksi (docs/04 §30.8 `PUT /v1/plans/{id}/demand`, semantik "ganti seluruh baris non-suggestion").
 * Baris sumber `suggestion` (usulan batch yang diterima) tampil tetapi tidak ikut diedit/dikirim: dikelola server.
 * Baris baru dipilih lewat pencarian BOM (`GET /v1/boms`, BomPicker); `product_detail_id` + `bom_id` tersimpan tersembunyi.
 */
import { useRouter } from 'next/navigation';
import { useId, useRef, useState, useTransition } from 'react';
import { announce } from '@/lib/client-nav';
import { useBusy } from '@/lib/loading-store';
import { callAction } from '@/lib/prodplan-call';
import { DEMAND_SOURCE_LABEL, fmtPpQty, toNum } from '@/lib/prodplan-format';
import type { BomItem, DemandLine } from '@/lib/prodplan-types';
import { BomPicker } from './BomPicker';

interface Row {
  key: number;
  product_detail_id: string;
  bom_id: string;
  qty: string;
  source: 'manual' | 'pr';
  pr_nums: string[];
  code: string;
  name: string;
  uom: string;
  bomName: string;
}

const toRow = (d: DemandLine, key: number): Row => ({
  key,
  product_detail_id: String(d.product_detail_id),
  bom_id: String(d.bom_id),
  qty: String(toNum(d.qty) ?? ''),
  source: d.source === 'pr' ? 'pr' : 'manual',
  pr_nums: d.pr_nums ?? [],
  code: d.product_code ?? '',
  name: d.product_name ?? '',
  uom: d.uom_name ?? '',
  bomName: '',
});

export function DemandEditor({
  planId,
  brand,
  rowVersion,
  lines,
  editable,
}: {
  planId: string;
  /** esb_brand rencana (untuk pencarian BOM). */
  brand: string;
  rowVersion: number;
  lines: readonly DemandLine[];
  editable: boolean;
}): React.ReactNode {
  const router = useRouter();
  const uid = useId();
  const nextKey = useRef(lines.length + 1);
  const editableLines = lines.filter((l) => l.source !== 'suggestion');
  const fixedLines = lines.filter((l) => l.source === 'suggestion');
  const [rows, setRows] = useState<Row[]>(() => editableLines.map((l, i) => toRow(l, i + 1)));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [pending, start] = useTransition();
  useBusy(pending);

  const update = (key: number, patch: Partial<Row>): void => {
    setSaved(false);
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  };

  const addRow = (): void => {
    setSaved(false);
    nextKey.current += 1;
    const key = nextKey.current;
    setRows((rs) => [...rs, { key, product_detail_id: '', bom_id: '', qty: '', source: 'manual', pr_nums: [], code: '', name: '', uom: '', bomName: '' }]);
  };

  const pick = (key: number, b: BomItem): void =>
    update(key, {
      product_detail_id: String(b.product_detail_id),
      bom_id: String(b.bom_id),
      name: b.product_name,
      code: b.product_code,
      uom: b.uom_name,
      bomName: b.bom_name || b.bom_code,
    });

  const save = (): void => {
    setError(null);
    setSaved(false);
    if (rows.some((r) => r.product_detail_id === '')) {
      setError('Pilih BOM untuk setiap baris (cari lalu pilih dari daftar), atau hapus baris yang kosong.');
      return;
    }
    const payloadLines = rows.map((r) => ({
      product_detail_id: r.product_detail_id.trim(),
      bom_id: r.bom_id.trim(),
      qty: r.qty.trim().replace(',', '.'),
      source: r.source,
      ...(r.source === 'pr' && r.pr_nums.length > 0 ? { pr_nums: r.pr_nums } : {}),
    }));
    start(async () => {
      const res = await callAction('plan-demand', { id: planId, row_version: rowVersion, lines: payloadLines });
      if (!res.ok) {
        const where = res.fields.includes('lines') ? ' Periksa setiap baris: jumlah harus angka positif (maks 4 desimal) dan produk tidak boleh ganda dengan sumber yang sama.' : '';
        setError(`${res.message}${where}`);
        announce(res.message);
        return;
      }
      setSaved(true);
      announce('Permintaan disimpan. Hasil hitung perlu diperbarui.');
      router.refresh();
    });
  };

  return (
    <div className="pp-demand">
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel baris permintaan produksi">
        <table className="table">
          <caption className="sr-only">Baris permintaan produksi</caption>
          <thead>
            <tr>
              <th scope="col">Produk hasil (BOM)</th>
              <th scope="col" className="num">
                Jumlah
              </th>
              <th scope="col">Sumber</th>
              {editable ? <th scope="col">Aksi</th> : null}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && fixedLines.length === 0 ? (
              <tr>
                <td colSpan={editable ? 4 : 3} className="muted">
                  Belum ada baris permintaan. Impor dari PR atau tambah manual.
                </td>
              </tr>
            ) : null}
            {rows.map((r, i) => {
              const base = `${uid}-${r.key}`;
              const isNew = r.product_detail_id === '';
              return (
                <tr key={r.key}>
                  <th scope="row">
                    {isNew ? (
                      <BomPicker brand={brand} label={`Cari BOM, baris ${i + 1}`} onPick={(b) => pick(r.key, b)} />
                    ) : (
                      <>
                        {r.name || r.code || 'Produk tanpa nama'}
                        {r.code && r.name ? <span className="muted small"> ({r.code})</span> : null}
                        {r.bomName ? <span className="muted small"> - BOM: {r.bomName}</span> : null}
                        {editable && r.source === 'manual' ? (
                          <button type="button" className="link-btn small" onClick={() => update(r.key, { product_detail_id: '', bom_id: '', name: '', code: '', bomName: '', uom: '' })}>
                            {' '}
                            Ganti<span className="sr-only"> BOM baris {i + 1}</span>
                          </button>
                        ) : null}
                      </>
                    )}
                  </th>
                  <td className="num">
                    {editable ? (
                      <>
                        <label className="sr-only" htmlFor={`${base}-qty`}>
                          Jumlah{r.uom ? ` (${r.uom})` : ''}, baris {i + 1}
                        </label>
                        <input id={`${base}-qty`} className="input pp-num" inputMode="decimal" value={r.qty} onChange={(e) => update(r.key, { qty: e.target.value })} />
                        {r.uom ? <span className="muted small"> {r.uom}</span> : null}
                      </>
                    ) : (
                      `${fmtPpQty(r.qty)}${r.uom ? ` ${r.uom}` : ''}`
                    )}
                  </td>
                  <td>
                    {DEMAND_SOURCE_LABEL[r.source]}
                    {r.pr_nums.length > 0 ? <span className="muted small"> ({r.pr_nums.join(', ')})</span> : null}
                  </td>
                  {editable ? (
                    <td>
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() => {
                          setSaved(false);
                          setRows((rs) => rs.filter((x) => x.key !== r.key));
                        }}
                      >
                        Hapus<span className="sr-only"> baris {i + 1}</span>
                      </button>
                    </td>
                  ) : null}
                </tr>
              );
            })}
            {fixedLines.map((d, i) => (
              <tr key={`s-${i}`}>
                <th scope="row">
                  {d.product_name || d.product_code || 'Produk tanpa nama'}
                  {d.product_code && d.product_name ? <span className="muted small"> ({d.product_code})</span> : null}
                </th>
                <td className="num">{`${fmtPpQty(d.qty)}${d.uom_name ? ` ${d.uom_name}` : ''}`}</td>
                <td>{DEMAND_SOURCE_LABEL.suggestion}</td>
                {editable ? <td className="muted small">Dikelola lewat usulan batch</td> : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editable ? (
        <div className="pp-form-actions">
          <button type="button" className="btn" onClick={addRow} disabled={pending}>
            Tambah baris
          </button>
          <button type="button" className="btn btn-primary" onClick={save} disabled={pending}>
            {pending ? 'Menyimpan...' : 'Simpan permintaan'}
          </button>
          <p className="muted small">
            Menyimpan mengganti seluruh baris impor/manual dan menandai hasil hitung kedaluwarsa. ID produk dan ID BOM diambil dari ESB (BOM harus bertipe Assembly).
          </p>
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="pp-msg pp-msg-error">
          <strong>Gagal:</strong> {error}
        </p>
      ) : null}
      {saved ? (
        <p role="status" className="pp-msg pp-msg-ok">
          Permintaan disimpan. Jalankan Hitung ulang untuk memperbarui kebutuhan bahan.
        </p>
      ) : null}
    </div>
  );
}
