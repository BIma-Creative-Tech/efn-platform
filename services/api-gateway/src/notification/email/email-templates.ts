/**
 * Template email (ADR-048 §3.5): allowlist di kode, Bahasa Indonesia, teks + HTML sederhana. Parameter hanya
 * string/angka, di-escape HTML, baris baru dibuang dari subjek, dipotong. Teks final promo = Marketing + Legal
 * (Permendag 70/2019, UU PDP); isi di sini placeholder yang aman.
 *
 * Sumber → template: template promo tidak bisa dikirim dengan `source=restock` dan sebaliknya.
 * `transactional` belum punya template (ADR-048 "bila diperlukan"); dipakai nanti tanpa footer berhenti berlangganan
 * dan tanpa syarat consent marketing.
 */
export type EmailSource = 'campaign' | 'restock' | 'price_alert' | 'transactional';
export const EMAIL_SOURCES: readonly EmailSource[] = ['campaign', 'restock', 'price_alert', 'transactional'];

/** Sumber yang wajib consent `marketing` (Q-SM25 default) + footer/header berhenti berlangganan. */
export function isMarketingSource(source: EmailSource): boolean {
  return source !== 'transactional';
}

export interface EmailParamSpec {
  required: boolean;
  max: number;
}

export interface EmailTemplateDef {
  sources: readonly EmailSource[];
  params: Readonly<Record<string, EmailParamSpec>>;
  subject: (p: Readonly<Record<string, string>>, brand: string) => string;
  /** Paragraf isi (teks polos, belum di-escape). */
  body: (p: Readonly<Record<string, string>>) => string[];
}

const req = (max: number): EmailParamSpec => ({ required: true, max });
const opt = (max: number): EmailParamSpec => ({ required: false, max });

export const EMAIL_TEMPLATES: Readonly<Record<string, EmailTemplateDef>> = {
  promo_generic: {
    sources: ['campaign'],
    params: { title: req(80), body: req(500), valid_to: opt(32), voucher_code: opt(32) },
    subject: (p, brand) => `${brand}: ${p.title}`,
    body: (p) => [
      p.body,
      ...(p.voucher_code ? [`Kode voucher: ${p.voucher_code}`] : []),
      ...(p.valid_to ? [`Berlaku sampai ${p.valid_to}.`] : []),
    ],
  },
  markdown_near_expiry: {
    sources: ['campaign'],
    params: { product_name: req(100), outlet_name: req(80), valid_to: opt(32) },
    subject: (p, brand) => `${brand}: harga spesial ${p.product_name}`,
    body: (p) => [
      `${p.product_name} sedang diskon di ${p.outlet_name}, stok terbatas.`,
      ...(p.valid_to ? [`Berlaku sampai ${p.valid_to} atau selama persediaan ada.`] : ['Berlaku selama persediaan ada.']),
    ],
  },
  restock_available: {
    sources: ['restock'],
    params: { product_name: req(100), outlet_name: req(80) },
    subject: (p, brand) => `${brand}: ${p.product_name} tersedia lagi`,
    body: (p) => [`Kabar baik! ${p.product_name} yang Anda tunggu sudah tersedia lagi di ${p.outlet_name}.`, 'Stok bisa cepat habis.'],
  },
  price_drop: {
    sources: ['price_alert'],
    params: { product_name: req(100), new_price_idr: req(16) },
    subject: (p, brand) => `${brand}: harga ${p.product_name} turun`,
    body: (p) => [`Harga ${p.product_name} turun menjadi Rp${p.new_price_idr}.`, 'Harga dapat berubah sewaktu-waktu.'],
  },
};

export function isEmailTemplate(name: unknown): name is string {
  return typeof name === 'string' && Object.prototype.hasOwnProperty.call(EMAIL_TEMPLATES, name);
}

export class EmailTemplateError extends Error {}

/** Validasi template ↔ sumber ↔ parameter. Melempar `EmailTemplateError` (kesalahan pemanggil, bukan data member). */
export function normalizeEmailParams(template: string, source: EmailSource, params: Readonly<Record<string, unknown>>): Record<string, string> {
  if (!isEmailTemplate(template)) throw new EmailTemplateError('template email tidak dikenal');
  const def = EMAIL_TEMPLATES[template];
  if (!def.sources.includes(source)) throw new EmailTemplateError('template email tidak sesuai sumber');
  const out: Record<string, string> = {};
  for (const key of Object.keys(params)) {
    if (!Object.prototype.hasOwnProperty.call(def.params, key)) throw new EmailTemplateError(`parameter email tidak dikenal: ${key}`);
  }
  for (const [key, spec] of Object.entries(def.params)) {
    const v = params[key];
    if (v === undefined || v === null || v === '') {
      if (spec.required) throw new EmailTemplateError(`parameter email wajib: ${key}`);
      continue;
    }
    if (typeof v !== 'string' && typeof v !== 'number') throw new EmailTemplateError(`parameter email harus string/angka: ${key}`);
    if (typeof v === 'number' && !Number.isFinite(v)) throw new EmailTemplateError(`parameter email tidak valid: ${key}`);
    // eslint-disable-next-line no-control-regex
    const s = String(v).replace(/[\u0000-\u001f\u007f]+/g, ' ').replace(/ {2,}/g, ' ').trim();
    if (s.length === 0) {
      if (spec.required) throw new EmailTemplateError(`parameter email wajib: ${key}`);
      continue;
    }
    out[key] = s.length > spec.max ? `${s.slice(0, spec.max - 1)}…` : s;
  }
  return out;
}

export function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export interface RenderedEmail {
  subject: string;
  text: string;
  html: string;
}

/**
 * Render email. `unsubscribeLink` wajib untuk sumber marketing (footer + header List-Unsubscribe dibuat pemanggil).
 * Parameter sudah dinormalisasi `normalizeEmailParams`.
 */
export function renderEmail(template: string, params: Readonly<Record<string, string>>, brand: string, unsubscribeLink: string | null): RenderedEmail {
  const def = EMAIL_TEMPLATES[template];
  if (!def) throw new EmailTemplateError('template email tidak dikenal');
  const subject = def.subject(params, brand).replace(/[\r\n]+/g, ' ').slice(0, 150);
  const paragraphs = def.body(params);
  const greeting = `Halo, pelanggan ${brand}.`;
  const footerText = unsubscribeLink
    ? [
        `Anda menerima email ini karena menyetujui menerima info promo dari ${brand}.`,
        `Berhenti berlangganan: ${unsubscribeLink}`,
      ]
    : [];
  const text = [greeting, '', ...paragraphs.flatMap((p) => [p, '']), ...footerText].join('\n').trimEnd() + '\n';
  const htmlParas = paragraphs.map((p) => `<p>${escapeHtml(p)}</p>`).join('');
  const htmlFooter = unsubscribeLink
    ? `<hr><p style="font-size:12px;color:#666">Anda menerima email ini karena menyetujui menerima info promo dari ${escapeHtml(brand)}. ` +
      `<a href="${escapeHtml(unsubscribeLink)}">Berhenti berlangganan</a>.</p>`
    : '';
  const html =
    `<!doctype html><html lang="id"><head><meta charset="utf-8"><title>${escapeHtml(subject)}</title></head>` +
    `<body style="font-family:Arial,sans-serif;line-height:1.5;color:#222"><p>${escapeHtml(greeting)}</p>${htmlParas}${htmlFooter}</body></html>`;
  return { subject, text, html };
}
