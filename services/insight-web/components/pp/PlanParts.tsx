/**
 * Bagian tampilan detail rencana (server components): kebutuhan bahan + kemasan utuh, baris produksi, usulan batch,
 * usulan transfer. Status selalu ikon + kata + warna (StatusPill), tidak hanya warna. Tombol aksi = <PpAction> (klien).
 */
import type { ReactNode } from 'react';
import { PpAction } from '@/components/pp/PpAction';
import { Term } from '@/components/Term';
import { EmptyState, StatusPill } from '@/components/ui';
import type { PpCaps } from '@/lib/prodplan-access';
import {
  DOC_KIND_LABEL,
  REQUIREMENT_FLAG_LABEL,
  SUGGESTION_STATUS,
  TRANSFER_STATUS,
  cleanLimitation,
  fmtPlace,
  fmtPpQty,
  fmtPpQtyUom,
  fmtShelfLife,
  fmtWibDateTime,
  hasOpenShortage,
  needToCover,
  packSummary,
  placeHint,
  sodBlock,
  suggestionButtons,
  toNum,
  usesPack,
} from '@/lib/prodplan-format';
import type { PackRule, Plan, ProductionLine, Requirement, Suggestion, Transfer } from '@/lib/prodplan-types';

const nameOf = (code: string | undefined, name: string | undefined, id: number): string => name || code || `Produk #${id}`;

/** Daftar keterbatasan perhitungan dari API (`limitations[]`), selalu tampil agar planner tidak salah mengira. */
export function Limitations({ items }: { items: readonly string[] }): ReactNode {
  if (items.length === 0) return null;
  return (
    <details className="notice pp-limits">
      <summary>Keterbatasan perhitungan ({items.length})</summary>
      <ul>
        {items.map((t, i) => (
          <li key={i}>{cleanLimitation(t)}</li>
        ))}
      </ul>
    </details>
  );
}

export function ProductionLinesTable({ lines }: { lines: readonly ProductionLine[] }): ReactNode {
  if (lines.length === 0) {
    return <EmptyState title="Belum ada baris produksi." reason="Simpan permintaan lalu jalankan hitung." />;
  }
  // Urutan posting = level menurun (WIP dulu), lalu bom_id (§30.9).
  const sorted = [...lines].sort((a, b) => b.level - a.level || a.bom_id - b.bom_id);
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel baris produksi">
      <table className="table">
        <caption className="sr-only">Baris produksi yang akan diposting, diurutkan dari level tertinggi (WIP) ke produk jadi</caption>
        <thead>
          <tr>
            <th scope="col" className="num">
              Urutan posting
            </th>
            <th scope="col" className="num">
              Level
            </th>
            <th scope="col">Produk</th>
            <th scope="col" className="num">
              Jumlah
            </th>
            <th scope="col">Asal baris</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((l, i) => (
            <tr key={l.bom_id}>
              <td className="num">{i + 1}</td>
              <td className="num">{l.level}</td>
              <th scope="row">
                {l.product_name || l.product_code}
                {l.product_name && l.product_code ? <span className="muted small"> ({l.product_code})</span> : null}
              </th>
              <td className="num">{fmtPpQtyUom(l.qty, l.uom_name)}</td>
              <td>{l.is_demand ? 'Permintaan' : 'Turunan (bahan setengah jadi / WIP)'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function RequirementsTable({ reqs, packRules }: { reqs: readonly Requirement[]; packRules: ReadonlyMap<string, PackRule> }): ReactNode {
  if (reqs.length === 0) {
    return <EmptyState title="Belum ada hasil hitung kebutuhan bahan." reason="Simpan permintaan lalu jalankan Hitung kebutuhan." />;
  }
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel kebutuhan bahan">
      <table className="table">
        <caption className="sr-only">Kebutuhan bahan per bahan: kebutuhan, kemasan utuh, stok lokasi produksi, dan kekurangan</caption>
        <thead>
          <tr>
            <th scope="col">Bahan</th>
            <th scope="col" className="num">
              Kebutuhan
            </th>
            <th scope="col">
              Kemasan utuh <Term id="kemasan-utuh" />
            </th>
            <th scope="col" className="num">
              Sisa terbuka <Term id="sisa-terbuka" />
            </th>
            <th scope="col">Umur setelah dibuka</th>
            <th scope="col" className="num">
              Kebutuhan efektif
            </th>
            <th scope="col" className="num">
              Stok lokasi produksi
            </th>
            <th scope="col" className="num">
              Kekurangan (perlu dipenuhi)
            </th>
            <th scope="col" className="num">
              Usulan transfer
            </th>
            <th scope="col">Belum tertutup</th>
            <th scope="col">Catatan</th>
          </tr>
        </thead>
        <tbody>
          {reqs.map((r) => {
            const open = toNum(r.shortage_qty) ?? 0;
            const life = packRules.get(r.product_code)?.opened_shelf_life_hours ?? null;
            const pack = packSummary(r);
            return (
              <tr key={r.product_detail_id} className={open > 0 ? 'pp-row-bad' : undefined}>
                <th scope="row">
                  {r.product_name || r.product_code}
                  {r.product_name ? <span className="muted small"> ({r.product_code})</span> : null}
                </th>
                <td className="num">{fmtPpQtyUom(r.need_qty, r.uom_name)}</td>
                <td>{pack ?? <span className="muted">Tanpa kemasan utuh</span>}</td>
                <td className="num">{usesPack(r) ? fmtPpQtyUom(r.leftover_qty, r.uom_name) : '-'}</td>
                <td>{usesPack(r) ? fmtShelfLife(life) : '-'}</td>
                <td className="num">{fmtPpQtyUom(r.need_eff_qty, r.uom_name)}</td>
                <td className="num">{fmtPpQtyUom(r.stock_prod_qty, r.uom_name)}</td>
                <td className="num">{fmtPpQtyUom(needToCover(r), r.uom_name)}</td>
                <td className="num">{fmtPpQtyUom(r.transfer_qty, r.uom_name)}</td>
                <td>{open > 0 ? <StatusPill status="bad" label={`Kurang ${fmtPpQtyUom(open, r.uom_name)}`} /> : <StatusPill status="good" label="Tertutup" />}</td>
                <td className="wrap">
                  {r.flags.length === 0 ? (
                    '-'
                  ) : (
                    <ul className="pp-flags">
                      {r.flags.map((f) => (
                        <li key={f}>{REQUIREMENT_FLAG_LABEL[f] ?? f}</li>
                      ))}
                    </ul>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ShortageNotice({ reqs }: { reqs: readonly Requirement[] }): ReactNode {
  if (!hasOpenShortage(reqs)) return null;
  const open = reqs.filter((r) => (toNum(r.shortage_qty) ?? 0) > 0);
  return (
    <div className="banner banner-bad pp-banner" role="status">
      <strong>Ada kekurangan bahan yang belum tertutup transfer:</strong>{' '}
      {open.map((r) => `${r.product_name || r.product_code} ${fmtPpQtyUom(r.shortage_qty, r.uom_name)}`).join('; ')}. Menyimpan dan mengajukan tetap bisa; posting produksi diblokir kecuali
      kekurangan diterima secara eksplisit.
    </div>
  );
}

export function SuggestionsTable({ plan, caps }: { plan: Plan; caps: PpCaps }): ReactNode {
  const canDecide = caps.planner && plan.status === 'draft';
  const btn = (st: Suggestion['status']) => suggestionButtons(st, plan.status, caps.planner);
  if (plan.suggestions.length === 0) {
    return (
      <EmptyState
        title="Tidak ada usulan batch tambahan."
        reason="Usulan muncul bila sisa kemasan terbuka cukup untuk membuat sedikitnya satu unit produk tambahan."
        icon="info"
      />
    );
  }
  return (
    <>
      <p className="muted">
        Usulan batch <Term id="usulan-batch" /> adalah alternatif untuk memanfaatkan sisa kemasan terbuka. Menerima satu usulan menggugurkan usulan lain untuk bahan yang sama dan mewajibkan hitung ulang. Permintaan
        asal tidak pernah dikurangi.
      </p>
      {plan.calc_status !== 'fresh' ? (
        <p className="pp-msg pp-msg-error" role="status">
          Hasil hitung belum terbaru; usulan di bawah mungkin tidak berlaku. Hitung ulang dulu.
        </p>
      ) : null}
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel usulan batch tambahan">
        <table className="table">
          <caption className="sr-only">Usulan batch tambahan dari sisa kemasan terbuka</caption>
          <thead>
            <tr>
              <th scope="col">Produk tambahan</th>
              <th scope="col" className="num">
                Tambahan
              </th>
              <th scope="col">Memanfaatkan bahan</th>
              <th scope="col" className="num">
                Dipakai
              </th>
              <th scope="col" className="num">
                Sisa bahan
              </th>
              <th scope="col">Status</th>
              {canDecide ? <th scope="col">Keputusan</th> : null}
            </tr>
          </thead>
          <tbody>
            {plan.suggestions.map((s: Suggestion) => (
              <tr key={s.id}>
                <th scope="row">{nameOf(s.product_code, s.product_name, s.product_detail_id)}</th>
                <td className="num">{fmtPpQty(s.extra_units)} unit</td>
                <td>{nameOf(s.material_code, s.material_name, s.material_detail_id)}</td>
                <td className="num">{fmtPpQty(s.uses_qty)}</td>
                <td className="num">{fmtPpQty(s.leftover_qty)}</td>
                <td>
                  <StatusPill status={SUGGESTION_STATUS[s.status].tone} label={SUGGESTION_STATUS[s.status].label} />
                </td>
                {canDecide ? (
                  <td>
                    {btn(s.status).accept || btn(s.status).cancelAccept ? (
                      <div className="pp-row-actions">
                        {btn(s.status).accept ? (
                          <PpAction action="suggestion-accept" payload={{ id: plan.id, sid: s.id, row_version: plan.row_version }} label="Terima" small successMessage="Usulan diterima. Hitung ulang." />
                        ) : null}
                        {btn(s.status).reject ? (
                          <PpAction action="suggestion-reject" payload={{ id: plan.id, sid: s.id, row_version: plan.row_version }} label="Tolak" small successMessage="Usulan ditolak." />
                        ) : null}
                        {btn(s.status).cancelAccept ? (
                          <PpAction
                            action="suggestion-reject"
                            payload={{ id: plan.id, sid: s.id, row_version: plan.row_version }}
                            label="Batalkan penerimaan"
                            small
                            successMessage="Penerimaan dibatalkan. Permintaan tambahan dihapus; hitung ulang."
                          />
                        ) : null}
                      </div>
                    ) : (
                      <span className="muted">-</span>
                    )}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** Nama cabang + lokasi bila ada; ID hanya sebagai keterangan kecil / cadangan. */
function Place({ bn, bid, ln, lid }: { bn?: string | null; bid: number; ln?: string | null; lid: number }): ReactNode {
  const hint = placeHint(bn, bid, ln, lid);
  return (
    <>
      {fmtPlace(bn, bid, ln, lid)}
      {hint ? <span className="muted small"> ({hint})</span> : null}
    </>
  );
}

export function TransfersPanel({ plan, caps, sub }: { plan: Plan; caps: PpCaps; sub: number }): ReactNode {
  if (plan.transfers.length === 0) {
    return (
      <EmptyState
        title="Tidak ada usulan transfer."
        reason="Stok di lokasi produksi cukup, atau lokasi sumber transfer belum dikonfigurasi untuk brand ini."
        icon="info"
      />
    );
  }
  const decide = caps.warehouse && plan.status === 'submitted';
  const block = sodBlock(plan, sub);
  return (
    <div className="stack">
      {plan.transfers.map((t: Transfer) => (
        <section key={t.id} className="card" aria-label={`Usulan transfer ${t.id}`}>
          <div className="card-head">
            <span className="card-title">
              <Place bn={t.origin_branch_name} bid={t.origin_branch_id} ln={t.origin_location_name} lid={t.origin_location_id} /> ke{' '}
              <Place bn={t.dest_branch_name} bid={t.dest_branch_id} ln={t.dest_location_name} lid={t.dest_location_id} />
            </span>
            <div className="card-tools">
              <span className="muted small">{DOC_KIND_LABEL[t.doc_type]}</span>
              <StatusPill status={TRANSFER_STATUS[t.status].tone} label={TRANSFER_STATUS[t.status].label} />
            </div>
          </div>
          <div className="table-wrap" tabIndex={0} role="region" aria-label={`Bahan transfer ${t.id}`}>
            <table className="table">
              <caption className="sr-only">Bahan pada usulan transfer {t.id}</caption>
              <thead>
                <tr>
                  <th scope="col">Bahan</th>
                  <th scope="col" className="num">
                    Jumlah
                  </th>
                </tr>
              </thead>
              <tbody>
                {t.lines.map((l) => (
                  <tr key={l.product_detail_id}>
                    <th scope="row">
                      {l.product_name || l.product_code}
                      {l.product_name ? <span className="muted small"> ({l.product_code})</span> : null}
                    </th>
                    <td className="num">{fmtPpQtyUom(l.qty, l.uom_name)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {t.decided_by_name ? (
            <p className="muted small">
              Diputuskan oleh {t.decided_by_name} pada {fmtWibDateTime(t.decided_at)}.{t.reject_reason ? ` Alasan penolakan: ${t.reject_reason}` : ''}
            </p>
          ) : null}
          {decide && t.status === 'proposed' ? (
            <div className="pp-row-actions">
              <PpAction
                action="transfer-approve"
                payload={{ id: plan.id, tid: t.id, row_version: plan.row_version }}
                label="Setujui transfer"
                variant="primary"
                small
                disabledReason={block}
                successMessage="Transfer disetujui."
              />
              <PpAction
                action="transfer-reject"
                payload={{ id: plan.id, tid: t.id, row_version: plan.row_version }}
                label="Tolak transfer"
                small
                fields={[{ name: 'reason', label: 'Alasan penolakan', kind: 'textarea', required: true, maxLength: 500 }]}
                disabledReason={block}
                successMessage="Transfer ditolak."
              />
            </div>
          ) : null}
        </section>
      ))}
    </div>
  );
}
