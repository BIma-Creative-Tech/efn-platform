'use client';

import { useId, type InputHTMLAttributes } from 'react';
import {
  BUYER_TYPE_LABEL,
  INVOICE_PRIVACY_NOTICE,
  TAX_ID_LABEL,
  type InvoiceBuyerType,
  type InvoiceErrors,
  type InvoiceRequestInput,
} from '@/lib/invoice-request';

/**
 * Opsi "Minta faktur pajak (B2B / atas nama)" (docs/04 §13.5, L-63). Komponen terkendali: nilai hidup HANYA di state
 * induk (memori), tidak ada storage/URL/analytics, dan atribut `name` sengaja tidak dipasang agar tidak ikut
 * serialisasi form/autofill yang menyimpan. Validasi klien hanya UX; BFF tetap sumber validasi.
 */
export function InvoiceRequestForm({
  enabled,
  onToggle,
  value,
  errors,
  onChange,
  disabled,
}: {
  enabled: boolean;
  onToggle: (on: boolean) => void;
  value: InvoiceRequestInput;
  errors: InvoiceErrors;
  onChange: (next: InvoiceRequestInput) => void;
  disabled: boolean;
}) {
  const uid = useId();
  const id = (k: string) => `${uid}-${k}`;
  const set = <K extends keyof InvoiceRequestInput>(k: K, v: InvoiceRequestInput[K]) => onChange({ ...value, [k]: v });
  const err = (k: keyof InvoiceRequestInput) => (errors[k] ? `${id(k)}-err` : undefined);

  return (
    <section className="stack" aria-labelledby={id('title')} style={{ margin: '0.75rem 0' }}>
      <div className="checkbox">
        <input
          type="checkbox"
          id={id('toggle')}
          checked={enabled}
          disabled={disabled}
          aria-controls={id('fields')}
          aria-expanded={enabled}
          onChange={(e) => onToggle(e.target.checked)}
        />
        <label htmlFor={id('toggle')} id={id('title')}>
          Minta faktur pajak (B2B / atas nama)
        </label>
      </div>
      {enabled && (
        <div id={id('fields')} className="stack">
          <p className="hint" style={{ margin: 0 }}>
            {INVOICE_PRIVACY_NOTICE}
          </p>
          <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0 }} disabled={disabled}>
            <legend style={{ fontWeight: 600 }}>Jenis identitas</legend>
            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
              {(Object.keys(BUYER_TYPE_LABEL) as InvoiceBuyerType[]).map((t) => (
                <span key={t}>
                  <input
                    type="radio"
                    id={id(`type-${t}`)}
                    name={`${uid}-buyer-type`}
                    checked={value.buyer_type === t}
                    onChange={() => set('buyer_type', t)}
                  />{' '}
                  <label htmlFor={id(`type-${t}`)}>{BUYER_TYPE_LABEL[t]}</label>
                </span>
              ))}
            </div>
          </fieldset>
          <Field
            fid={id('tax_id')}
            label={TAX_ID_LABEL[value.buyer_type]}
            error={errors.tax_id}
            errId={err('tax_id')}
            inputProps={{
              value: value.tax_id,
              maxLength: 32,
              inputMode: value.buyer_type === 'passport' ? 'text' : 'numeric',
              autoComplete: 'off',
              disabled,
              onChange: (e) => set('tax_id', e.target.value),
            }}
          />
          <Field
            fid={id('name')}
            label="Nama sesuai identitas"
            error={errors.name}
            errId={err('name')}
            inputProps={{ value: value.name, maxLength: 100, autoComplete: 'off', disabled, onChange: (e) => set('name', e.target.value) }}
          />
          <Field
            fid={id('address')}
            label="Alamat"
            error={errors.address}
            errId={err('address')}
            inputProps={{ value: value.address, maxLength: 255, autoComplete: 'off', disabled, onChange: (e) => set('address', e.target.value) }}
          />
          <Field
            fid={id('email')}
            label="Email penerima faktur"
            error={errors.email}
            errId={err('email')}
            inputProps={{
              value: value.email,
              maxLength: 254,
              inputMode: 'email',
              autoComplete: 'off',
              autoCapitalize: 'none',
              spellCheck: false,
              disabled,
              onChange: (e) => set('email', e.target.value),
            }}
          />
        </div>
      )}
    </section>
  );
}

function Field({
  fid,
  label,
  error,
  errId,
  inputProps,
}: {
  fid: string;
  label: string;
  error: string | undefined;
  errId: string | undefined;
  inputProps: InputHTMLAttributes<HTMLInputElement>;
}) {
  return (
    <div className="field">
      <label htmlFor={fid}>{label}</label>
      <input
        {...inputProps}
        id={fid}
        type="text"
        required
        aria-required="true"
        aria-invalid={error ? true : undefined}
        aria-describedby={errId}
      />
      {error && (
        <p id={errId} className="small" role="alert" style={{ margin: 0, color: 'var(--color-danger)' }}>
          {error}
        </p>
      )}
    </div>
  );
}
