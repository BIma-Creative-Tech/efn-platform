import { coverageNotices, type CoverageRow } from '@/lib/coverage';
import { Icon } from './ui';

/**
 * Banner cakupan data (review 21 Nov 2026): bila rentang filter melebihi data yang tersedia untuk sebuah entitas, jelaskan
 * bahwa nol = data belum masuk, bukan "tidak ada penjualan". `rows` null (view cakupan gagal dimuat) = tanpa banner.
 */
export function CoverageNotice({
  rows,
  entities,
  range,
}: {
  rows: readonly CoverageRow[] | null;
  entities: readonly string[];
  range: { from: string; to: string };
}): React.ReactNode {
  if (rows === null) return null;
  const notes = coverageNotices(rows, entities, range);
  if (notes.length === 0) return null;
  return (
    <div className="notice notice-cov" role="status">
      <Icon name="warn" size={16} />
      <ul>
        {notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </div>
  );
}
