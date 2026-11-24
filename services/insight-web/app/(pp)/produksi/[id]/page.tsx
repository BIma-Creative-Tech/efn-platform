import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { DemandEditor } from '@/components/pp/DemandEditor';
import { HistoryPanel, OutboxTable, PlanHeaderInfo, PostingPanel, StatusSteps, WorkflowBar, initialPhase } from '@/components/pp/PlanFlow';
import {
  Limitations,
  ProductionLinesTable,
  RequirementsTable,
  ShortageNotice,
  SuggestionsTable,
  TransfersPanel,
} from '@/components/pp/PlanParts';
import { PpAction } from '@/components/pp/PpAction';
import { EmptyState, PageHeader, Section } from '@/components/ui';
import { fmtDate } from '@/lib/format';
import { firstParam, redirectIfExpired, requirePpSession, type SearchParams } from '@/lib/pp-page';
import { loadMeta, loadPackRules, loadPlan, loadPreview, type PpLoad } from '@/lib/prodplan-data';
import {
  PLAN_TABS,
  PLAN_TAB_LABEL,
  UNMAPPED_REASON_LABEL,
  fmtPpQty,
  parsePhase,
  parseTab,
  undecidedTransfers,
  type PlanTab,
} from '@/lib/prodplan-format';
import type { PackRule, PostPreview } from '@/lib/prodplan-types';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Detail rencana produksi' };

/**
 * `/produksi/[id]` = dashboard Production Order (docs/04 §30.12): tab Permintaan, Kebutuhan, Usulan batch, Transfer, Posting,
 * Riwayat. Semua data dari API prodplan; tombol sesuai peran dan pemisahan tugas; tidak ada akses langsung ke ESB/DB.
 */
export default async function PlanDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: SearchParams;
}) {
  const { sid, view, caps } = await requirePpSession();
  const { id } = await params;
  const raw = await searchParams;
  const tab = parseTab(firstParam(raw, 'tab'));

  const [metaRes, planRes] = await Promise.all([loadMeta(sid), loadPlan(sid, id)]);
  redirectIfExpired(planRes);
  if (!planRes.ok) {
    if (planRes.error.code === 'not_found') notFound();
    return (
      <>
        <PageHeader title="Rencana produksi" question="Rencana ini belum bisa dimuat." />
        <EmptyState title="Rencana belum bisa dimuat." reason={planRes.error.message} icon="warn">
          <p>
            <Link href="/produksi">Kembali ke daftar rencana</Link>
          </p>
        </EmptyState>
      </>
    );
  }
  const plan = planRes.data;
  const meta = metaRes.ok ? metaRes.data : null;
  const brand = plan.esb_brand;

  // Pemuatan tambahan hanya untuk tab yang memerlukannya.
  let packRules: ReadonlyMap<string, PackRule> = new Map();
  if (tab === 'kebutuhan' && brand) {
    const pr = await loadPackRules(sid, brand);
    if (pr.ok) packRules = new Map(pr.data.map((r) => [r.product_code, r]));
  }
  const phase = initialPhase(plan, parsePhase(firstParam(raw, 'phase')));
  let preview: PpLoad<PostPreview> | null = null;
  if (tab === 'posting' && (plan.status === 'approved' || plan.status === 'posted')) {
    preview = await loadPreview(sid, plan.id, phase);
    redirectIfExpired(preview);
  }

  const href = (t: PlanTab): string => `/produksi/${encodeURIComponent(plan.id)}${t === 'permintaan' ? '' : `?tab=${t}`}`;
  const counts: Partial<Record<PlanTab, number>> = {
    permintaan: plan.demand.length,
    usulan: plan.suggestions.filter((s) => s.status === 'open').length,
    transfer: plan.transfers.length,
    posting: plan.outbox.length,
  };
  const undecided = undecidedTransfers(plan.transfers);
  const draftPlanner = plan.status === 'draft' && caps.planner;

  return (
    <>
      <PageHeader
        title={plan.plan_no || 'Rencana produksi'}
        question="Apa yang harus diproduksi, bahan apa yang kurang, dan sudah sampai tahap mana?"
        actions={
          <Link className="btn" href="/produksi">
            Kembali ke daftar
          </Link>
        }
      />
      <Section>
        <StatusSteps status={plan.status} />
        <PlanHeaderInfo plan={plan} />
        <WorkflowBar plan={plan} caps={caps} sub={view.actor_id} />
        {plan.status === 'submitted' && undecided > 0 ? (
          <p className="muted small" role="status">
            {undecided} usulan transfer menunggu keputusan Kepala gudang (tab Transfer).
          </p>
        ) : null}
      </Section>

      <nav className="tabs-links" aria-label="Bagian rencana">
        {PLAN_TABS.map((t) => (
          <Link key={t} className="btn btn-sm" href={href(t)} aria-current={t === tab ? 'true' : undefined}>
            {PLAN_TAB_LABEL[t]}
            {counts[t] !== undefined ? ` (${counts[t]})` : ''}
          </Link>
        ))}
      </nav>

      {tab === 'permintaan' ? (
        <Section title="Permintaan produksi" id="pp-demand">
          <p className="muted">
            Produk hasil dan jumlah yang akan diproduksi. Impor dari purchase request (PR) outlet atau tambah manual. Produk tanpa BOM Assembly tidak bisa dihitung dan tampil terpisah di bawah.
          </p>
          {draftPlanner ? (
            <PpAction
              action="plan-import-pr"
              payload={{ id: plan.id }}
              label="Impor dari PR"
              fields={[
                { name: 'required_date_from', label: 'Tanggal dibutuhkan dari', kind: 'date', required: true, defaultValue: plan.production_date },
                { name: 'required_date_to', label: 'Tanggal dibutuhkan sampai', kind: 'date', required: true, defaultValue: plan.production_date },
              ]}
              successMessage="Impor PR dimulai."
            />
          ) : null}
          <DemandEditor key={`${plan.id}:${plan.row_version}`} planId={plan.id} brand={plan.esb_brand ?? meta?.brands[0]?.esb_brand ?? ''} rowVersion={plan.row_version} lines={plan.demand} editable={draftPlanner} />
          {plan.unmapped.length > 0 ? (
            <>
              <h3 className="card-title">Baris PR yang tidak bisa dihitung ({plan.unmapped.length})</h3>
              <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel baris PR tidak terpetakan">
                <table className="table">
                  <caption className="sr-only">Baris PR yang tidak punya BOM Assembly atau tidak dikenal</caption>
                  <thead>
                    <tr>
                      <th scope="col">Nomor PR</th>
                      <th scope="col">Produk</th>
                      <th scope="col" className="num">
                        Jumlah
                      </th>
                      <th scope="col">Alasan</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plan.unmapped.map((u, i) => (
                      <tr key={i}>
                        <th scope="row">{u.pr_num}</th>
                        <td>
                          {u.product_name || u.product_code}
                          {u.product_name ? <span className="muted small"> ({u.product_code})</span> : null}
                        </td>
                        <td className="num">{fmtPpQty(u.qty)}</td>
                        <td>{UNMAPPED_REASON_LABEL[u.reason] ?? u.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </Section>
      ) : null}

      {tab === 'kebutuhan' ? (
        <>
          <Section title="Kebutuhan bahan" id="pp-req">
            <Limitations items={plan.limitations} />
            {plan.calc_status !== 'fresh' ? (
              <p className="pp-msg pp-msg-error" role="status">
                Hasil hitung {plan.calc_status === 'none' ? 'belum ada' : plan.calc_status === 'failed' ? 'gagal' : 'kedaluwarsa'}. Jalankan {plan.calc_status === 'none' ? 'Hitung kebutuhan' : 'Hitung ulang'} sebelum
                mengajukan.
              </p>
            ) : null}
            <ShortageNotice reqs={plan.requirements} />
            <RequirementsTable reqs={plan.requirements} packRules={packRules} />
          </Section>
          <Section title="Baris produksi" id="pp-lines">
            <p className="muted">Urutan posting: bahan setengah jadi (WIP) dulu, lalu produk jadi. Stok WIP yang sudah ada tidak dikurangkan (keterbatasan v1).</p>
            <ProductionLinesTable lines={plan.production_lines} />
          </Section>
        </>
      ) : null}

      {tab === 'usulan' ? (
        <Section title="Usulan batch tambahan" id="pp-sugg">
          <SuggestionsTable plan={plan} caps={caps} />
        </Section>
      ) : null}

      {tab === 'transfer' ? (
        <Section title="Usulan transfer bahan" id="pp-transfer">
          <p className="muted">
            Sumber dipilih dari stok terbesar (bukan FEFO) di lokasi yang diizinkan. Kepala gudang menyetujui atau menolak tiap transfer; pembuat dan pengaju rencana tidak boleh menyetujui.
          </p>
          <ShortageNotice reqs={plan.requirements} />
          <TransfersPanel plan={plan} caps={caps} sub={view.actor_id} />
        </Section>
      ) : null}

      {tab === 'posting' ? (
        <>
          <Section title="Baris produksi yang akan diposting" id="pp-post-lines">
            <ProductionLinesTable lines={plan.production_lines} />
          </Section>
          <Section title="Pratinjau dan posting" id="pp-post">
            <PostingPanel plan={plan} meta={meta} caps={caps} phase={phase} preview={preview} />
          </Section>
          <Section title="Status dokumen ESB" id="pp-outbox">
            <OutboxTable items={plan.outbox} caps={caps} showResend />
          </Section>
        </>
      ) : null}

      {tab === 'riwayat' ? (
        <Section title="Riwayat dan dokumen ESB" id="pp-history">
          <HistoryPanel plan={plan} caps={caps} />
        </Section>
      ) : null}

      <p className="muted small">
        Rencana dibuat untuk {plan.production_date ? fmtDate(plan.production_date) : '-'}. Data stok dan resep berasal dari ESB saat terakhir dihitung.
      </p>
    </>
  );
}
