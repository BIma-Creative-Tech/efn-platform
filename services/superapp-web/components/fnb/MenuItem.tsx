'use client';

import { useId, useRef, useState } from 'react';
import { describeSelection, validateSelection, type Selection } from '@/lib/attributes';
import { formatRupiah } from '@/lib/format';
import { NOTE_ERROR_MESSAGES, NOTE_MAX, validateNote, type FnbProduct, type TableAddInput } from '@/lib/fnb';
import { ProductImage } from '@/components/online/ProductImage';
import { AttributePicker } from './AttributePicker';
import styles from './fnb.module.css';

interface Props {
  product: FnbProduct;
  open: boolean;
  onToggle: () => void;
  maxQty: number;
  /** Mengembalikan pesan galat, atau null bila berhasil ditambahkan. */
  onAdd: (input: TableAddInput) => string | null;
}

/**
 * Satu menu: harga SEBELUM pajak & layanan + label pajak dari Odoo; panel "Pilih" berisi atribut (modifier), jumlah, dan
 * catatan (≤ 140 karakter, tanpa nomor telepon/email). Validasi di sini hanya UX; BFF/Odoo tetap sumber validasi.
 */
export function MenuItem({ product, open, onToggle, maxQty, onAdd }: Props) {
  const uid = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState<Selection>({});
  const [qty, setQty] = useState(1);
  const [note, setNote] = useState('');
  const [missing, setMissing] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);

  const panelId = `${uid}-panel`;
  const noteId = `${uid}-note`;
  const noteHintId = `${uid}-note-hint`;

  function focusFirstProblem() {
    setTimeout(() => {
      panelRef.current?.querySelector<HTMLElement>('select[aria-invalid="true"], fieldset[aria-describedby] input, [aria-invalid="true"]')?.focus();
    }, 0);
  }

  function add() {
    setError(null);
    const check = validateSelection(product.attributes, selection);
    if (!check.ok) {
      if (check.reason === 'missing_required') {
        setMissing(check.missing);
        setError('Lengkapi pilihan yang wajib (ditandai merah).');
        focusFirstProblem();
      } else {
        setError('Terlalu banyak pilihan tambahan untuk satu menu.');
      }
      return;
    }
    setMissing([]);
    const noteCheck = validateNote(note);
    if (!noteCheck.ok) {
      setError(NOTE_ERROR_MESSAGES[noteCheck.reason]);
      document.getElementById(noteId)?.focus();
      return;
    }
    const problem = onAdd({
      product,
      qty,
      attribute_value_ids: check.ids,
      options_text: describeSelection(product.attributes, check.ids),
      note: noteCheck.value,
    });
    if (problem) {
      setError(problem);
      return;
    }
    setSelection({});
    setQty(1);
    setNote('');
    onToggle();
  }

  const labels = product.tax_labels.length > 0 ? ` (${product.tax_labels.join(', ')})` : '';

  return (
    <div className={styles.item}>
      <div className={styles.itemRow}>
        <div className={styles.thumb}>
          <ProductImage sha={product.image_sha} alt="" />
        </div>
        <div>
          <div className={styles.itemName}>{product.name}</div>
          <div>
            <span className={styles.itemPrice}>{formatRupiah(product.price_excl)}</span>
            <span className="small muted">
              {' '}
              {product.price_note}
              {labels}
            </span>
          </div>
          {!product.available && <div className={styles.soldOut}>Habis</div>}
        </div>
        <button
          type="button"
          className={open ? 'btn btn-secondary btn-small' : 'btn btn-small'}
          disabled={!product.available}
          aria-expanded={open}
          aria-controls={panelId}
          aria-label={`${open ? 'Tutup pilihan' : 'Pilih'} ${product.name}`}
          onClick={onToggle}
        >
          {open ? 'Tutup' : 'Pilih'}
        </button>
      </div>

      {open && product.available && (
        <div id={panelId} ref={panelRef} className={styles.panel}>
          <AttributePicker
            attributes={product.attributes}
            selection={selection}
            onChange={(next) => {
              setSelection(next);
              if (missing.length > 0) setMissing([]);
            }}
            idPrefix={uid}
            missing={missing}
          />
          <div className="field">
            <label htmlFor={noteId}>Catatan (opsional)</label>
            <input
              id={noteId}
              className={styles.noteInput}
              type="text"
              value={note}
              maxLength={NOTE_MAX}
              autoComplete="off"
              enterKeyHint="done"
              aria-describedby={noteHintId}
              onChange={(e) => setNote(e.target.value)}
            />
            <span id={noteHintId} className="hint">
              Mis. &quot;tanpa es&quot;. Maksimal {NOTE_MAX} karakter. Jangan tulis nomor telepon atau email.
            </span>
          </div>
          <div className="field">
            <span id={`${uid}-qty`} style={{ fontWeight: 600 }}>
              Jumlah
            </span>
            <div className={styles.qty} role="group" aria-labelledby={`${uid}-qty`}>
              <button type="button" disabled={qty <= 1} aria-label="Kurangi jumlah" onClick={() => setQty((q) => Math.max(1, q - 1))}>
                −
              </button>
              <output aria-live="polite">{qty}</output>
              <button type="button" disabled={qty >= maxQty} aria-label="Tambah jumlah" onClick={() => setQty((q) => Math.min(maxQty, q + 1))}>
                +
              </button>
            </div>
          </div>
          <div role="alert">{error && <p className="alert alert-error">{error}</p>}</div>
          <button type="button" className="btn" onClick={add}>
            Tambah ke pesanan
          </button>
        </div>
      )}
    </div>
  );
}
