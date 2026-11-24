import type { Metadata } from 'next';
import Link from 'next/link';
import { EmptyState, PageHeader, Section, StatusPill } from '@/components/ui';
import { firstParam, requirePpSession, type SearchParams } from '@/lib/pp-page';
import { loadMeta, loadPlans } from '@/lib/prodplan-data';
import { CALC_STATUS, PLAN_STATUS, fmtPlace, isIsoDate, placeHint, postingBadge } from '@/lib/prodplan-format';
import { fmtDate } from '@/lib/format';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Rencana produksi' };

const PAGE = 50;
const STATUS_FILTERS = ['draft', 'submitted', 'approved', 'posted', 'cancelled'] as const;

/** Daftar rencana produksi (docs/04 §30.12 `/produksi`): filter brand, tanggal produksi, status. Data hanya dari API prodplan. */
export default async function PlansPage({ searchParams }: { searchParams: SearchParams }) {
  const { session, caps } = await requirePpSession();
  const raw = await searchParams;
  const metaRes = await loadMeta(session);
  const brands = metaRes.ok ? metaRes.data.brands : [];

  const brandParam = firstParam(raw, 'brand');
  const brand = brands.find((b) => b.esb_brand === brandParam)?.esb_brand ?? brands[0]?.esb_brand;
  const fromP = firstParam(raw, 'from');
  const toP = firstParam(raw, 'to');
  const from = fromP && isIsoDate(fromP) ? fromP : undefined;
  const to = toP && isIsoDate(toP) ? toP : undefined;
  const statusP = firstParam(raw, 'status');
  const status = STATUS_FILTERS.find((s) => s === statusP);
  const offsetN = Number(firstParam(raw, 'offset') ?? '0');
  const offset = Number.isSafeInteger(offsetN) && offsetN >= 0 && offsetN <= 100_000 ? offsetN : 0;
  const rangeBad = Boolean((fromP && !from) || (toP && !to) || (from && to && from > to));

  const plans = brand && !rangeBad ? await loadPlans(session, { brand, from, to, status, limit: PAGE, offset }) : null;

  const qs = (extra: Record<string, string | number>): string => {
    const q = new URLSearchParams();
    if (brand) q.set('brand', brand);
    if (from) q.set('from', from);
    if (to) q.set('to', to);
    if (status) q.set('status', status);
    for (const [k, v] of Object.entries(extra)) q.set(k, String(v));
    return q.toString();
  };

  return (
    <>
      <PageHeader
        title="Rencana produksi"
        question="Rencana produksi Central Kitchen apa yang sedang berjalan, dan sampai tahap persetujuan mana?"
        actions={
          caps.planner ? (
            <Link className="btn btn-primary" href="/produksi/baru">
              Buat rencana
            </Link>
          ) : undefined
        }
      />
      <Section title="Saring daftar" id="pp-filter">
        <form className="pp-filter" method="get" action="/produksi">
          {brands.length > 1 ? (
            <div className="pp-field">
              <label htmlFor="pp-brand">Brand</label>
              <select id="pp-brand" name="brand" className="input" defaultValue={brand}>
                {brands.map((b) => (
                  <option key={b.esb_brand} value={b.esb_brand}>
                    {b.company_code} ({b.esb_brand})
                  </option>
                ))}
              </select>
            </div>
          ) : brand ? (
            <input type="hidden" name="brand" value={brand} />
          ) : null}
          <div className="pp-field">
            <label htmlFor="pp-from">Tanggal produksi dari</label>
            <input id="pp-from" name="from" type="date" className="input" defaultValue={from ?? ''} />
          </div>
          <div className="pp-field">
            <label htmlFor="pp-to">Sampai</label>
            <input id="pp-to" name="to" type="date" className="input" defaultValue={to ?? ''} />
          </div>
          <div className="pp-field">
            <label htmlFor="pp-status">Status</label>
            <select id="pp-status" name="status" className="input" defaultValue={status ?? ''}>
              <option value="">Semua</option>
              {STATUS_FILTERS.map((s) => (
                <option key={s} value={s}>
                  {PLAN_STATUS[s].label}
                </option>
              ))}
            </select>
          </div>
          <div className="pp-field pp-field-btn">
            <button type="submit" className="btn">
              Terapkan
            </button>
          </div>
        </form>
        {rangeBad ? (
          <p role="alert" className="pp-msg pp-msg-error">
            Rentang tanggal tidak valid: tanggal harus berformat benar dan &quot;dari&quot; tidak boleh setelah &quot;sampai&quot;.
          </p>
        ) : null}
      </Section>

      <Section title="Daftar rencana" id="pp-list">
        {!metaRes.ok ? (
          <EmptyState title="Daftar belum bisa dimuat." reason={metaRes.error.message} icon="warn" />
        ) : brands.length === 0 ? (
          <EmptyState title="Belum ada brand yang diizinkan untuk Anda di modul produksi." reason="Hubungi administrator." icon="info" />
        ) : plans === null ? null : !plans.ok ? (
          <EmptyState title="Daftar rencana belum bisa dimuat." reason={plans.error.message} icon="warn" />
        ) : plans.data.items.length === 0 ? (
          <EmptyState title="Belum ada rencana yang cocok dengan penyaring." reason={caps.planner ? 'Buat rencana baru untuk memulai.' : undefined} />
        ) : (
          <>
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel rencana produksi">
              <table className="table">
                <caption className="sr-only">Daftar rencana produksi</caption>
                <thead>
                  <tr>
                    <th scope="col">Nomor rencana</th>
                    <th scope="col">Tanggal produksi</th>
                    <th scope="col">Cabang produksi</th>
                    <th scope="col">Status</th>
                    <th scope="col">Hasil hitung</th>
                    <th scope="col">Dibuat oleh</th>
                  </tr>
                </thead>
                <tbody>
                  {plans.data.items.map((p) => (
                    <tr key={p.id}>
                      <th scope="row">
                        <Link href={`/produksi/${encodeURIComponent(p.id)}`}>{p.plan_no || p.id}</Link>
                      </th>
                      <td>{p.production_date ? fmtDate(p.production_date) : '-'}</td>
                      <td>
                        {fmtPlace(p.branch_name, p.branch_id, p.production_location_name, p.production_location_id)}
                        {placeHint(p.branch_name, p.branch_id, p.production_location_name, p.production_location_id) ? (
                          <span className="muted small"> ({placeHint(p.branch_name, p.branch_id, p.production_location_name, p.production_location_id)})</span>
                        ) : null}
                      </td>
                      <td>
                        <StatusPill status={PLAN_STATUS[p.status].tone} label={PLAN_STATUS[p.status].label} />
                        {postingBadge(p) ? (
                          <>
                            {' '}
                            <StatusPill status={postingBadge(p)?.tone ?? 'unknown'} label={postingBadge(p)?.label} />
                          </>
                        ) : null}
                      </td>
                      <td>
                        <StatusPill status={CALC_STATUS[p.calc_status].tone} label={CALC_STATUS[p.calc_status].label} />
                      </td>
                      <td>{p.created_by_name ?? '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <nav className="pager" aria-label="Halaman daftar rencana">
              {offset > 0 ? (
                <Link className="btn btn-sm" href={`/produksi?${qs({ offset: Math.max(0, offset - PAGE) })}`}>
                  Sebelumnya
                </Link>
              ) : null}
              <span>
                Menampilkan {offset + 1} sampai {offset + plans.data.items.length} dari {plans.data.count} rencana
              </span>
              {offset + PAGE < plans.data.count ? (
                <Link className="btn btn-sm" href={`/produksi?${qs({ offset: offset + PAGE })}`}>
                  Berikutnya
                </Link>
              ) : null}
            </nav>
          </>
        )}
      </Section>
    </>
  );
}
