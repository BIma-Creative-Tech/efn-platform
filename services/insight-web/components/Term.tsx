'use client';
/**
 * Istilah dengan penjelasan: tombol kecil (ikon "i") yang membuka popover berisi definisi + cara membaca (lib/glossary.ts).
 * Bisa dibuka dengan keyboard (Enter/Spasi), sentuh, dan klik; Esc, klik di luar, atau gulir menutupnya. Hover memakai
 * tooltip bawaan browser (atribut title). Popover memakai posisi fixed agar tidak terpotong wadah tabel yang bisa digulir.
 *
 * - <Term id="wape" /> : ikon + popover untuk satu istilah.
 * - <TermHints text="Galat forecast (WAPE)" /> : mendeteksi istilah di teks label dan menampilkan satu ikon per istilah.
 */
import Link from 'next/link';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { getTerm, termsIn, termSummary } from '@/lib/glossary';

const POP_W = 320;

/** Ikon "i" lokal (tidak mengimpor ./ui agar tidak ada impor melingkar: ui.tsx memakai TermHints). */
function InfoGlyph(): React.ReactNode {
  return (
    <svg className="icon" width={13} height={13} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7.5h.01" />
    </svg>
  );
}

export function Term({ id, label }: { id: string; label?: string }): React.ReactNode {
  const entry = getTerm(id);
  const uid = useId();
  const btn = useRef<HTMLButtonElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const open = pos !== null;

  const close = useCallback(() => setPos(null), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        close();
        btn.current?.focus();
      }
    };
    const onDown = (e: PointerEvent): void => {
      const t = e.target as Node | null;
      if (t && btn.current?.parentElement?.contains(t)) return;
      close();
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onDown);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onDown);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [open, close]);

  if (!entry) return null;

  const toggle = (): void => {
    if (open) {
      close();
      return;
    }
    const r = btn.current?.getBoundingClientRect();
    if (!r) return;
    const width = Math.min(POP_W, window.innerWidth - 16);
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
    setPos({ top: r.bottom + 6, left });
  };

  const name = label ?? entry.term;
  return (
    <span className="term">
      <button
        ref={btn}
        type="button"
        className="term-btn"
        aria-expanded={open}
        aria-controls={open ? `${uid}-pop` : undefined}
        aria-label={`Arti istilah ${name}`}
        title={termSummary(entry)}
        onClick={toggle}
      >
        <InfoGlyph />
      </button>
      {pos ? (
        <span id={`${uid}-pop`} role="note" className="term-pop" style={{ top: pos.top, left: pos.left }}>
          <strong className="term-pop-title">
            {entry.term}
            {entry.full ? <span className="term-pop-full"> ({entry.full})</span> : null}
          </strong>
          <span className="term-pop-def">{entry.def}</span>
          {entry.read ? (
            <span className="term-pop-read">
              <em>Cara membaca:</em> {entry.read}
            </span>
          ) : null}
          <Link href="/glosarium" className="term-pop-link">
            Semua istilah
          </Link>
        </span>
      ) : null}
    </span>
  );
}

/** Ikon penjelasan untuk setiap istilah glosarium yang terdeteksi di `text` (tidak merender apa pun bila tidak ada). */
export function TermHints({ text }: { text: string }): React.ReactNode {
  const ids = termsIn(text);
  if (ids.length === 0) return null;
  return (
    <>
      {ids.map((id) => (
        <Term key={id} id={id} />
      ))}
    </>
  );
}
