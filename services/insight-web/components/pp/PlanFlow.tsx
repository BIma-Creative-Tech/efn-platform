/**
 * Alur status rencana (draf -> diajukan -> disetujui -> diposting / dibatalkan), tombol sesuai peran + pemisahan tugas (SoD),
 * pratinjau posting dengan konfirmasi `digest`, outbox/dokumen ESB, dan riwayat. Server components; aksi = <PpAction>.
 * Tombol yang tidak boleh dipakai tampil nonaktif DENGAN alasan terlihat (bukan disembunyikan diam-diam).
 */
import Link from 'next/link';
import type { ReactNode } from 'react';
import { PpAction } from '@/components/pp/PpAction';
import { Term } from '@/components/Term';
import { EmptyState, StatusPill } from '@/components/ui';
import type { PpCaps } from '@/lib/prodplan-access';
import {
  CALC_STATUS,
  DOC_KIND_LABEL,
  OUTBOX_STATUS,
  PLAN_STATUS,
  canResend,
  defaultPhase,
  describeEndpoint,
  describeLine,
  envLabel,
  canRefreshStock,
  fmtPlace,
  fmtPpQty,
  fmtWibDateTime,
  hasNeedsLogin,
  hasOpenShortage,
  outboxErrorLabel,
  placeHint,
  postingBadge,
  productionViews,
  sodBlock,
  submitBlock,
  undecidedTransfers,
} from '@/lib/prodplan-format';
import type { OutboxItem, Plan, PlanStatus, PostPhase, PostPreview, PpMeta } from '@/lib/prodplan-types';
import type { PpLoad } from '@/lib/prodplan-data';
import { fmtDate } from '@/lib/format';

const STEPS: readonly PlanStatus[] = ['draft', 'submitted', 'approved', 'posted'];

/** Indikator tahap (teks + penanda "tahap sekarang"; tidak hanya warna). */
export function StatusSteps({ status }: { status: PlanStatus }): ReactNode {
  if (status === 'cancelled') {
    return (
      <p>
        <StatusPill status="bad" label="Dibatalkan" />
      </p>
    );
  }
  const cur = STEPS.indexOf(status);
  return (
    <ol className="pp-steps" aria-label="Tahap rencana">
      {STEPS.map((s, i) => (
        <li key={s} aria-current={i === cur ? 'step' : undefined} className={i < cur ? 'pp-step-done' : i === cur ? 'pp-step-cur' : undefined}>
          <span className="pp-step-no" aria-hidden="true">
            {i < cur ? '✓' : i + 1}
          </span>
          <span>
            {PLAN_STATUS[s].label}
            {i < cur ? <span className="sr-only"> (selesai)</span> : null}
            {i === cur ? <span className="sr-only"> (tahap sekarang)</span> : null}
          </span>
        </li>
      ))}
    </ol>
  );
}

export function PlanHeaderInfo({ plan }: { plan: Plan }): ReactNode {
  return (
    <dl className="pp-meta">
      <div>
        <dt>Tanggal produksi</dt>
        <dd>{plan.production_date ? fmtDate(plan.production_date) : '-'}</dd>
      </div>
      <div>
        <dt>Cabang dan lokasi produksi</dt>
        <dd>
          {fmtPlace(plan.branch_name, plan.branch_id, plan.production_location_name, plan.production_location_id)}
          {placeHint(plan.branch_name, plan.branch_id, plan.production_location_name, plan.production_location_id) ? (
            <span className="muted small"> ({placeHint(plan.branch_name, plan.branch_id, plan.production_location_name, plan.production_location_id)})</span>
          ) : null}
        </dd>
      </div>
      <div>
        <dt>Status</dt>
        <dd>
          <StatusPill status={PLAN_STATUS[plan.status].tone} label={PLAN_STATUS[plan.status].label} />
        </dd>
      </div>
      <div>
        <dt>Hasil hitung</dt>
        <dd>
          <StatusPill status={CALC_STATUS[plan.calc_status].tone} label={CALC_STATUS[plan.calc_status].label} />
          {plan.calculated_at ? <span className="muted small"> ({fmtWibDateTime(plan.calculated_at)})</span> : null}
        </dd>
      </div>
      {plan.stock_fetched_at ? (
        <div>
          <dt>Stok ESB</dt>
          <dd>Stok ESB diambil {fmtWibDateTime(plan.stock_fetched_at)}</dd>
        </div>
      ) : null}
      {postingBadge(plan) ? (
        <div>
          <dt>Posting</dt>
          <dd>
            <StatusPill status={postingBadge(plan)?.tone ?? 'unknown'} label={postingBadge(plan)?.label} />
          </dd>
        </div>
      ) : null}
      <div>
        <dt>Dibuat oleh</dt>
        <dd>{plan.created_by_name ?? '-'}</dd>
      </div>
      {plan.notes ? (
        <div>
          <dt>Catatan</dt>
          <dd>{plan.notes}</dd>
        </div>
      ) : null}
    </dl>
  );
}

/** Tombol alur sesuai status + peran + SoD. */
/**
 * Posting berhenti karena sesi ESB pemicu berakhir (§30.17.9): tidak ada panggilan ke ESB sampai ada pengguna yang masuk dan
 * memilih "Lanjutkan posting" (`resume-posting`). Bila dokumen mungkin sudah tercipta oleh pengguna sebelumnya, server hanya
 * mengizinkan pengguna yang sama (403 `resume_requires_original_user` ditampilkan apa adanya).
 */
export function ResumePosting({ plan, caps }: { plan: Plan; caps: PpCaps }): ReactNode {
  if (!hasNeedsLogin(plan.outbox)) return null;
  const may = caps.planner || caps.warehouse;
  return (
    <div className="notice" role="note">
      <p>
        <strong>Posting berhenti karena sesi ESB berakhir.</strong> Dokumen yang belum selesai tidak dikirim ke ESB sampai Anda melanjutkannya. Pastikan Anda sudah masuk dengan akun ESB yang berhak, lalu lanjutkan.
      </p>
      {may ? (
        <PpAction
          action="plan-resume-posting"
          payload={{ id: plan.id, row_version: plan.row_version }}
          label="Lanjutkan posting"
          variant="primary"
          successMessage="Posting dilanjutkan. Pantau status dokumen di tab Posting atau Riwayat."
        />
      ) : (
        <p className="muted small">Peran Anda tidak boleh melanjutkan posting.</p>
      )}
    </div>
  );
}

export function WorkflowBar({ plan, caps, sub }: { plan: Plan; caps: PpCaps; sub: number | null }): ReactNode {
  const base = { id: plan.id, row_version: plan.row_version };
  const undecided = undecidedTransfers(plan.transfers);
  const sod = sodBlock(plan, sub);

  if (plan.status === 'cancelled') {
    return (
      <>
        <p className="muted">
          Rencana dibatalkan{plan.cancelled_at ? ` pada ${fmtWibDateTime(plan.cancelled_at)}` : ''}.{plan.cancel_reason ? ` Alasan: ${plan.cancel_reason}` : ''}
        </p>
        <ResumePosting plan={plan} caps={caps} />
      </>
    );
  }

  const cancel = caps.planner ? (
    <PpAction
      action="plan-cancel"
      payload={base}
      label="Batalkan rencana"
      fields={[
        { name: 'reason', label: 'Alasan pembatalan', kind: 'textarea', required: true, maxLength: 500, help: plan.outbox.some((o) => o.status === 'confirmed') ? 'Dokumen ESB yang sudah terkonfirmasi akan dibatalkan (dihapus) di ESB.' : undefined },
      ]}
      successMessage="Rencana dibatalkan."
    />
  ) : null;

  return (
    <div className="pp-workflow" role="group" aria-label="Tindakan rencana">
      {plan.status === 'draft' && caps.planner ? (
        <>
          <PpAction
            action="plan-calculate"
            payload={{ id: plan.id, row_version: plan.row_version }}
            label={plan.calc_status === 'none' ? 'Hitung kebutuhan' : 'Hitung ulang'}
            variant={plan.calc_status === 'fresh' ? 'default' : 'primary'}
            successMessage="Perhitungan dimulai."
          />
          <PpAction
            action="plan-submit"
            payload={base}
            label="Ajukan untuk persetujuan"
            variant={plan.calc_status === 'fresh' ? 'primary' : 'default'}
            disabledReason={submitBlock(plan)}
            successMessage="Rencana diajukan."
          />
        </>
      ) : null}
      {plan.status === 'draft' && !caps.planner ? <p className="muted">Menunggu Planner CK menyelesaikan draf dan mengajukannya.</p> : null}

      {plan.status === 'submitted' ? (
        <>
          {caps.warehouse ? (
            <PpAction
              action="plan-approve"
              payload={base}
              label="Setujui rencana"
              variant="primary"
              disabledReason={sod ?? (undecided > 0 ? `Masih ${undecided} usulan transfer belum disetujui atau ditolak (tab Transfer).` : null)}
              successMessage="Rencana disetujui."
            />
          ) : (
            <p className="muted">Menunggu persetujuan Kepala gudang.</p>
          )}
          <PpAction
            action="plan-return"
            payload={base}
            label="Kembalikan ke draf"
            fields={[{ name: 'reason', label: 'Alasan dikembalikan', kind: 'textarea', required: true, maxLength: 500, help: 'Keputusan transfer akan direset.' }]}
            successMessage="Rencana dikembalikan ke draf."
          />
        </>
      ) : null}

      {plan.status === 'approved' ? (
        <>
          <p>
            Rencana disetujui. Lanjutkan di tab{' '}
            <Link href={`/produksi/${encodeURIComponent(plan.id)}?tab=posting`}>Posting</Link> untuk melihat pratinjau dan membuat dokumen di ESB.
          </p>
          {canRefreshStock(plan.status, caps) ? (
            <div>
              <PpAction
                action="plan-calculate"
                payload={{ id: plan.id, row_version: plan.row_version }}
                label="Segarkan stok & hitung ulang"
                refresh
                successMessage="Penyegaran stok dimulai."
              />
              <p className="muted small">Bila stok berubah sehingga hasil hitung berubah, rencana kembali ke Draf dan harus diajukan serta disetujui ulang.</p>
            </div>
          ) : null}
        </>
      ) : null}
      {plan.status === 'posted' ? <p>Rencana sudah diposting. Pantau dokumen di tab Riwayat.</p> : null}
      <ResumePosting plan={plan} caps={caps} />
      {cancel}
    </div>
  );
}

// ---------------------------------------------------------------- outbox

export function OutboxTable({ items, caps, showResend }: { items: readonly OutboxItem[]; caps: PpCaps; showResend: boolean }): ReactNode {
  if (items.length === 0) {
    return <EmptyState title="Belum ada dokumen ESB." reason="Dokumen dibuat setelah posting dikonfirmasi." icon="info" />;
  }
  const sorted = [...items].sort((a, b) => a.seq - b.seq);
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel dokumen ESB (outbox)">
      <table className="table">
        <caption className="sr-only">Status dokumen ESB hasil posting</caption>
        <thead>
          <tr>
            <th scope="col" className="num">
              Urutan
            </th>
            <th scope="col">Fase</th>
            <th scope="col">Dokumen</th>
            <th scope="col">Status</th>
            <th scope="col">Nomor dokumen ESB</th>
            <th scope="col" className="num">
              Percobaan
            </th>
            <th scope="col">Keterangan</th>
            {showResend ? <th scope="col">Aksi</th> : null}
          </tr>
        </thead>
        <tbody>
          {sorted.map((o) => {
            const st = OUTBOX_STATUS[o.status] ?? { label: o.status, tone: 'unknown' as const };
            return (
              <tr key={o.id}>
                <td className="num">{o.seq}</td>
                <td>{o.phase === 'transfers' ? 'Transfer' : 'Produksi'}</td>
                <th scope="row">{DOC_KIND_LABEL[o.doc_kind]}</th>
                <td>
                  <StatusPill status={st.tone} label={st.label} />
                </td>
                <td>{o.esb_doc_number ? <code>{o.esb_doc_number}</code> : '-'}</td>
                <td className="num">{o.attempts ?? '-'}</td>
                <td className="wrap">
                  {outboxErrorLabel(o.last_error_code) || (o.status === 'delete_failed' ? 'Perlu ditangani langsung di ESB.' : o.confirmed_at ? `Terkonfirmasi ${fmtWibDateTime(o.confirmed_at)}` : '-')}
                </td>
                {showResend ? (
                  <td>
                    {caps.planner && canResend(o) ? (
                      <PpAction
                        action="outbox-resend"
                        payload={{ oid: o.id }}
                        label="Kirim ulang"
                        small
                        fields={[{ name: 'ack', kind: 'checkbox', required: true, send: false, label: 'Saya memahami dokumen ini akan dikirim ulang ke ESB dengan penanda yang sama' }]}
                        successMessage="Dokumen dijadwalkan kirim ulang."
                      />
                    ) : (
                      <span className="muted">-</span>
                    )}
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- posting

function PhaseLinks({ plan, phase }: { plan: Plan; phase: PostPhase }): ReactNode {
  const href = (p: PostPhase): string => `/produksi/${encodeURIComponent(plan.id)}?tab=posting&phase=${p}`;
  return (
    <nav className="tabs-links" aria-label="Fase posting">
      {(['transfers', 'production'] as const).map((p) => (
        <Link key={p} className="btn btn-sm" href={href(p)} aria-current={phase === p ? 'true' : undefined}>
          {p === 'transfers' ? '1. Transfer bahan' : '2. Produksi'}
        </Link>
      ))}
    </nav>
  );
}

export function PostingPanel({
  plan,
  meta,
  caps,
  phase,
  preview,
}: {
  plan: Plan;
  meta: PpMeta | null;
  caps: PpCaps;
  phase: PostPhase;
  preview: PpLoad<PostPreview> | null;
}): ReactNode {
  if (plan.status !== 'approved' && plan.status !== 'posted') {
    return <EmptyState title="Posting tersedia setelah rencana disetujui." reason={`Status sekarang: ${PLAN_STATUS[plan.status].label}.`} icon="info" />;
  }
  const writeOn = meta?.write_enabled === true;
  const env = meta ? envLabel(meta.esb_env) : 'ESB (lingkungan tidak diketahui)';
  const shortage = hasOpenShortage(plan.requirements);
  const noTransfers = !plan.transfers.some((t) => t.status === 'approved');
  const prodViews = preview !== null && preview.ok ? preview.data.docs.flatMap((d) => productionViews(d)) : [];

  let blockReason: string | null = null;
  if (plan.status === 'posted') blockReason = 'Rencana sudah diposting.';
  else if (!meta) blockReason = 'Lingkungan ESB tidak diketahui; posting dinonaktifkan sampai layanan dapat dihubungi.';
  else if (!writeOn) blockReason = `Penulisan ke ${env} sedang dimatikan oleh administrator; posting belum tersedia.`;
  else if (plan.outbox.some((o) => o.phase === phase)) blockReason = 'Dokumen fase ini sudah dibuat; pantau statusnya di tab Riwayat (server menolak posting ganda).';
  else if (phase === 'transfers' && noTransfers) blockReason = 'Tidak ada transfer yang disetujui untuk diposting.';
  else if (!caps.planner && !caps.warehouse) blockReason = 'Peran Anda tidak boleh memposting.';

  return (
    <div className="stack">
      <PhaseLinks plan={plan} phase={phase} />
      <p className="muted">
        Posting <Term id="posting" /> dilakukan dua fase: transfer bahan dulu (harus sudah di-authorize di ESB), lalu produksi. Rencana tanpa transfer langsung ke fase produksi. Fase transfer tidak mengubah status
        rencana; fase produksi menjadikannya &quot;Sudah diposting&quot;.
      </p>
      {phase === 'production' ? (
        <div className="notice" role="note">
          Dokumen produksi langsung mengubah stok di ESB (status terotorisasi). Status pengiriman barang pada permintaan transfer antar cabang tidak diperiksa sebelum produksi.
        </div>
      ) : null}

      {preview === null ? null : !preview.ok ? (
        <EmptyState title="Pratinjau posting belum bisa dimuat." reason={preview.error.message} icon="warn" />
      ) : (
        <>
          <h3 className="card-title">Dokumen ESB yang akan dibuat ({preview.data.docs.length})</h3>
          {preview.data.docs.length === 0 ? (
            <EmptyState title="Tidak ada dokumen untuk fase ini." icon="info" />
          ) : (
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel dokumen yang akan dibuat">
              <table className="table">
                <caption className="sr-only">Dokumen ESB yang akan dibuat pada fase ini</caption>
                <thead>
                  <tr>
                    <th scope="col" className="num">
                      No.
                    </th>
                    <th scope="col">Jenis dokumen</th>
                    <th scope="col">Asal</th>
                    <th scope="col">Tujuan</th>
                    <th scope="col">Isi</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.data.docs.map((d, i) => {
                    const lines = (d.lines ?? []).map(describeLine).filter((x): x is string => x !== null);
                    return (
                      <tr key={i}>
                        <td className="num">{i + 1}</td>
                        <th scope="row">{DOC_KIND_LABEL[d.doc_kind] ?? d.doc_kind}</th>
                        <td>{describeEndpoint(d.origin)}</td>
                        <td>{describeEndpoint(d.dest)}</td>
                        <td className="wrap">
                          {lines.length === 0 ? `${(d.lines ?? []).length} baris` : (
                            <details>
                              <summary>{lines.length} baris</summary>
                              <ul>
                                {lines.map((l, j) => (
                                  <li key={j}>{l}</li>
                                ))}
                              </ul>
                            </details>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {phase === 'production' && prodViews.length > 0 ? (
            <>
              <h3 className="card-title">Produk yang akan diproduksi ({prodViews.length})</h3>
              <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel produk hasil dan bahannya">
                <table className="table">
                  <caption className="sr-only">Produk hasil produksi menurut urutan posting, dengan bahan yang dipakai</caption>
                  <thead>
                    <tr>
                      <th scope="col" className="num">
                        Urutan
                      </th>
                      <th scope="col" className="num">
                        Level
                      </th>
                      <th scope="col">Produk hasil</th>
                      <th scope="col" className="num">
                        Jumlah
                      </th>
                      <th scope="col">Bahan</th>
                    </tr>
                  </thead>
                  <tbody>
                    {prodViews.map((v, i) => (
                      <tr key={i}>
                        <td className="num">{i + 1}</td>
                        <td className="num">{v.level ?? '-'}</td>
                        <th scope="row">{v.product}</th>
                        <td className="num">{v.qty}</td>
                        <td className="wrap">
                          {v.materials.length === 0 ? (
                            '-'
                          ) : (
                            <details>
                              <summary>{v.materials.length} bahan</summary>
                              <ul>
                                {v.materials.map((m, j) => (
                                  <li key={j}>{m}</li>
                                ))}
                              </ul>
                            </details>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}

          <h3 className="card-title">Dampak stok</h3>
          {preview.data.stock_impact.length === 0 ? (
            <EmptyState title="Tidak ada perubahan stok pada fase ini." icon="info" />
          ) : (
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel dampak stok">
              <table className="table">
                <caption className="sr-only">Dampak stok per lokasi dan produk</caption>
                <thead>
                  <tr>
                    <th scope="col">Lokasi</th>
                    <th scope="col">Produk</th>
                    <th scope="col" className="num">
                      Keluar
                    </th>
                    <th scope="col" className="num">
                      Masuk
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {preview.data.stock_impact.map((s, i) => (
                    <tr key={i}>
                      <td>
                        {s.location_name || `Lokasi ${s.location_id}`}
                        {s.location_name ? <span className="muted small"> (ID {s.location_id})</span> : null}
                      </td>
                      <th scope="row">{s.product_name ? `${s.product_code} - ${s.product_name}` : s.product_code}</th>
                      <td className="num">{fmtPpQty(s.qty_out)}</td>
                      <td className="num">{fmtPpQty(s.qty_in)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <p className="muted small">
            Digest pratinjau: <code>{preview.data.digest}</code>. Konfirmasi membawa digest ini; bila rencana atau stok berubah sebelum Anda konfirmasi, posting ditolak dan pratinjau harus dimuat ulang.
          </p>
          <section className="card pp-confirm" aria-label="Konfirmasi posting">
            <h3 className="card-title">Konfirmasi posting ke {env}</h3>
            {shortage && phase === 'production' ? <p className="pp-msg pp-msg-error">Masih ada kekurangan bahan yang belum tertutup transfer (lihat tab Kebutuhan).</p> : null}
            <PpAction
              action="plan-post"
              payload={{ id: plan.id, row_version: plan.row_version, phase, digest: preview.data.digest }}
              label={`Posting ${phase === 'transfers' ? 'transfer' : 'produksi'} ke ${env}`}
              variant="primary"
              defaultOpen
              disabledReason={blockReason}
              successMessage="Posting diterima. Dokumen dikirim ke ESB; pantau di tab Riwayat."
              fields={[
                {
                  name: 'ack',
                  kind: 'checkbox',
                  required: true,
                  send: false,
                  label: phase === 'production' ? 'Saya memahami dokumen produksi langsung mengubah stok ESB' : 'Saya memahami dokumen transfer akan dibuat di ESB dan perlu di-authorize di sana',
                },
                ...(shortage && phase === 'production'
                  ? [{ name: 'accept_shortage', kind: 'checkbox' as const, label: 'Saya menerima kekurangan bahan yang belum tertutup dan tetap ingin memposting', help: 'Penerimaan ini dicatat di jejak audit.' }]
                  : []),
              ]}
            />
          </section>
        </>
      )}
    </div>
  );
}

export function initialPhase(plan: Plan, requested: PostPhase | null): PostPhase {
  return requested ?? defaultPhase(plan);
}

// ---------------------------------------------------------------- riwayat

export function HistoryPanel({ plan, caps }: { plan: Plan; caps: PpCaps }): ReactNode {
  const events: { who: string | null | undefined; label: string; at: string | null | undefined }[] = [
    { who: plan.created_by_name, label: 'Dibuat', at: plan.created_at },
    { who: plan.submitted_by_name, label: 'Diajukan', at: plan.submitted_at },
    { who: plan.approved_by_name, label: 'Disetujui', at: plan.approved_at },
    { who: plan.posted_by_name, label: 'Diposting', at: plan.posted_at },
    { who: undefined, label: plan.cancel_reason ? `Dibatalkan (${plan.cancel_reason})` : 'Dibatalkan', at: plan.cancelled_at },
  ];
  const done = events.filter((e) => e.at);
  return (
    <div className="stack">
      <h3 className="card-title">Linimasa</h3>
      {done.length === 0 ? (
        <p className="muted">Belum ada peristiwa.</p>
      ) : (
        <ol className="pp-timeline">
          {done.map((e) => (
            <li key={e.label}>
              <strong>{e.label}</strong>
              {e.who ? ` oleh ${e.who}` : ''} <span className="muted">- {fmtWibDateTime(e.at)}</span>
            </li>
          ))}
        </ol>
      )}
      <h3 className="card-title">
        Dokumen ESB (outbox) <Term id="outbox" />
      </h3>
      <OutboxTable items={plan.outbox} caps={caps} showResend />
      <p className="muted small">
        Status &quot;Hasil belum pasti&quot; berarti ESB tidak menjawab setelah dokumen terkirim; sistem mencocokkan ulang ke ESB dan tidak mengirim ulang otomatis. Kirim ulang manual hanya tersedia (untuk Planner CK) bagi dokumen gagal yang belum
        punya nomor ESB dan kegagalannya aman diulang.
      </p>
    </div>
  );
}
