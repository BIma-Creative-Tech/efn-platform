'use client';

import { selectValue, type ProductAttribute, type Selection } from '@/lib/attributes';
import { formatRupiah } from '@/lib/format';
import styles from './fnb.module.css';

interface Props {
  attributes: ProductAttribute[];
  selection: Selection;
  onChange: (next: Selection) => void;
  /** Awalan id unik per instans (beberapa produk bisa terbuka bersamaan). */
  idPrefix: string;
  disabled?: boolean;
  /** Id atribut wajib yang belum dipilih (ditandai merah + pesan). */
  missing?: number[];
}

/**
 * Pilihan atribut `no_variant` (modifier) F&B — dipakai di `/meja` dan belanja F&B online.
 * `radio`/`pills` -> radio; `select` -> <select>; `multi` -> kotak centang. Atribut opsional diberi pilihan "Tanpa".
 * Tambahan harga ditampilkan SEBELUM pajak (indikatif). Semua kontrol berlabel dan bisa dioperasikan dengan keyboard.
 */
export function AttributePicker({ attributes, selection, onChange, idPrefix, disabled = false, missing = [] }: Props) {
  if (attributes.length === 0) return null;
  const anyExtra = attributes.some((a) => a.values.some((v) => v.price_extra_excl > 0));
  return (
    <div className={styles.attrs}>
      {attributes.map((a) => {
        const gid = `${idPrefix}-a${a.attribute_id}`;
        const invalid = missing.includes(a.attribute_id);
        const chosen = selection[a.attribute_id] ?? [];
        const errId = `${gid}-err`;
        const requiredText = a.required ? 'wajib dipilih' : 'opsional';

        if (a.display_type === 'select') {
          return (
            <div key={a.attribute_id} className="field">
              <label htmlFor={gid}>
                {a.name} <span className="small muted">({requiredText})</span>
              </label>
              <select
                id={gid}
                className={styles.selectInput}
                value={chosen[0] ?? ''}
                disabled={disabled}
                aria-required={a.required}
                aria-invalid={invalid}
                aria-describedby={invalid ? errId : undefined}
                onChange={(e) => onChange(selectValue(attributes, selection, a.attribute_id, e.target.value === '' ? null : Number(e.target.value)))}
              >
                <option value="">{a.required ? 'Pilih…' : 'Tanpa'}</option>
                {a.values.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                    {v.price_extra_excl > 0 ? ` (+${formatRupiah(v.price_extra_excl)})` : ''}
                  </option>
                ))}
              </select>
              {invalid && (
                <p id={errId} role="alert" className={styles.attrError}>
                  Pilih salah satu {a.name}.
                </p>
              )}
            </div>
          );
        }

        const multi = a.display_type === 'multi';
        return (
          <fieldset
            key={a.attribute_id}
            className={`${styles.attr} ${invalid ? styles.attrInvalid : ''}`}
            disabled={disabled}
            aria-describedby={invalid ? errId : undefined}
          >
            <legend>
              {a.name} <span className="small muted">({multi ? `${requiredText}, boleh lebih dari satu` : requiredText})</span>
            </legend>
            {!multi && !a.required && (
              <div className={styles.opt}>
                <input
                  type="radio"
                  id={`${gid}-none`}
                  name={gid}
                  checked={chosen.length === 0}
                  onChange={() => onChange(selectValue(attributes, selection, a.attribute_id, null))}
                />
                <label htmlFor={`${gid}-none`}>Tanpa</label>
              </div>
            )}
            {a.values.map((v) => {
              const id = `${gid}-v${v.id}`;
              return (
                <div key={v.id} className={styles.opt}>
                  <input
                    type={multi ? 'checkbox' : 'radio'}
                    id={id}
                    name={gid}
                    checked={chosen.includes(v.id)}
                    onChange={(e) => onChange(selectValue(attributes, selection, a.attribute_id, v.id, e.target.checked))}
                  />
                  <label htmlFor={id}>{v.name}</label>
                  {v.price_extra_excl > 0 && <span className={styles.optExtra}>+{formatRupiah(v.price_extra_excl)}</span>}
                </div>
              );
            })}
            {invalid && (
              <p id={errId} role="alert" className={styles.attrError}>
                {multi ? `Pilih minimal satu ${a.name}.` : `Pilih salah satu ${a.name}.`}
              </p>
            )}
          </fieldset>
        );
      })}
      {anyExtra && <p className="hint">Tambahan harga belum termasuk pajak.</p>}
    </div>
  );
}
