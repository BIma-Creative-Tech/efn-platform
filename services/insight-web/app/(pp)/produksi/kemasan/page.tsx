import type { Metadata } from 'next';
import { PackRuleEditor } from '@/components/pp/PackRuleEditor';
import { EmptyState, PageHeader, Section } from '@/components/ui';
import { firstParam, requirePpSession, type SearchParams } from '@/lib/pp-page';
import { loadMeta, loadPackRules } from '@/lib/prodplan-data';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Kemasan utuh' };

/**
 * `/produksi/kemasan` (docs/04 §30.12): master kemasan utuh per brand. Planner CK dapat menambah, mengubah, dan menonaktifkan;
 * kepala gudang hanya melihat.
 */
export default async function PackRulesPage({ searchParams }: { searchParams: SearchParams }) {
  const { session, caps } = await requirePpSession();
  const raw = await searchParams;
  const metaRes = await loadMeta(session);
  const brands = metaRes.ok ? metaRes.data.brands : [];
  const brandParam = firstParam(raw, 'brand');
  const brand = brands.find((b) => b.esb_brand === brandParam)?.esb_brand ?? brands[0]?.esb_brand;
  const rules = brand ? await loadPackRules(session, brand) : null;

  return (
    <>
      <PageHeader
        title="Kemasan utuh"
        question="Bahan apa yang hanya bisa dipakai per kemasan utuh, berapa isinya, dan berapa lama bertahan setelah dibuka?"
      />
      {brands.length > 1 ? (
        <Section title="Brand" id="pp-pack-brand">
          <form className="pp-filter" method="get" action="/produksi/kemasan">
            <div className="pp-field">
              <label htmlFor="pp-pack-brand-select">Brand</label>
              <select id="pp-pack-brand-select" name="brand" className="input" defaultValue={brand}>
                {brands.map((b) => (
                  <option key={b.esb_brand} value={b.esb_brand}>
                    {b.company_code} ({b.esb_brand})
                  </option>
                ))}
              </select>
            </div>
            <div className="pp-field pp-field-btn">
              <button type="submit" className="btn">
                Tampilkan
              </button>
            </div>
          </form>
        </Section>
      ) : null}
      <Section title="Aturan kemasan utuh" id="pp-pack-rules">
        {!metaRes.ok ? (
          <EmptyState title="Data belum bisa dimuat." reason={metaRes.error.message} icon="warn" />
        ) : !brand ? (
          <EmptyState title="Belum ada brand yang diizinkan untuk Anda di modul produksi." reason="Hubungi administrator." icon="info" />
        ) : rules === null ? null : !rules.ok ? (
          <EmptyState title="Daftar kemasan belum bisa dimuat." reason={rules.error.message} icon="warn" />
        ) : (
          <PackRuleEditor key={brand} brand={brand} rules={rules.data} editable={caps.planner} />
        )}
      </Section>
    </>
  );
}
