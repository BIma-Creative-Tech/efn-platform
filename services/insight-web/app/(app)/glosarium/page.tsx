import type { Metadata } from 'next';
import { PageHeader, Section } from '@/components/ui';
import { GLOSSARY } from '@/lib/glossary';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Glosarium' };

/** Daftar istilah (lib/glossary.ts): sumber yang sama dengan popover "i" di label KPI, judul kartu, dan header tabel. */
export default function GlossaryPage() {
  const sorted = [...GLOSSARY].sort((a, b) => a.term.localeCompare(b.term, 'id'));
  return (
    <>
      <PageHeader title="Glosarium" question="Apa arti istilah dan singkatan yang dipakai di Insight, dan bagaimana membaca angkanya?" />
      <Section>
        <ul className="glossary-index" aria-label="Loncat ke istilah">
          {sorted.map((e) => (
            <li key={e.id}>
              <a href={`#t-${e.id}`}>{e.term}</a>
            </li>
          ))}
        </ul>
        <dl className="glossary">
          {sorted.map((e) => (
            <div key={e.id} className="glossary-item" id={`t-${e.id}`}>
              <dt>
                {e.term}
                {e.full ? <span className="muted"> - {e.full}</span> : null}
              </dt>
              <dd>
                <p>{e.def}</p>
                {e.read ? (
                  <p className="muted">
                    <strong>Cara membaca:</strong> {e.read}
                  </p>
                ) : null}
              </dd>
            </div>
          ))}
        </dl>
      </Section>
    </>
  );
}
