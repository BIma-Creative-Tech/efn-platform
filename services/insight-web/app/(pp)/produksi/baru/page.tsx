import type { Metadata } from 'next';
import Link from 'next/link';
import { NewPlanForm, type CkOption } from '@/components/pp/NewPlanForm';
import { EmptyState, PageHeader, Section } from '@/components/ui';
import { redirectIfExpired, requirePpSession } from '@/lib/pp-page';
import { loadMeta } from '@/lib/prodplan-data';
import { ckLabel, todayWib } from '@/lib/prodplan-format';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Buat rencana produksi' };

/** `/produksi/baru`: hanya pp_planner (kepala gudang dialihkan ke daftar). Cabang CK dan lokasi produksi dari `meta`. */
export default async function NewPlanPage() {
  const { sid, caps } = await requirePpSession();
  const metaRes = await loadMeta(sid);
  redirectIfExpired(metaRes);

  const options: CkOption[] = metaRes.ok
    ? metaRes.data.brands.flatMap((b) =>
        b.ck.map((c) => ({
          value: `${b.esb_brand}|${c.branch_id}|${c.production_location_id}`,
          brand: b.esb_brand,
          branchId: c.branch_id,
          locationId: c.production_location_id,
          label: ckLabel(b.company_code, c),
        })),
      )
    : [];

  return (
    <>
      <PageHeader title="Buat rencana produksi" question="Untuk cabang produksi dan tanggal mana rencana ini dibuat?" />
      <Section>
        {!caps.planner ? (
          <EmptyState title="Hanya Planner CK yang dapat membuat rencana." reason="Peran Anda hanya dapat melihat dan menyetujui." icon="info">
            <p>
              <Link href="/produksi">Kembali ke daftar rencana</Link>
            </p>
          </EmptyState>
        ) : !metaRes.ok ? (
          <EmptyState title="Form belum bisa dimuat." reason={metaRes.error.message} icon="warn" />
        ) : (
          <NewPlanForm options={options} defaultDate={todayWib(new Date())} />
        )}
      </Section>
    </>
  );
}
