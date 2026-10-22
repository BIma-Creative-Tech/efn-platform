/**
 * Atribut produk F&B (modifier `no_variant`) — docs/04 §14.A.6 (format A.6), §14.B B-2/B-4. Modul MURNI
 * (client, Route Handler, Server Component, test).
 *
 * - Dipakai di dua tempat: pesan dari meja (`/meja`, QR) dan belanja F&B online (keranjang `attribute_value_ids`).
 * - Frontend TIDAK pernah menghitung harga final: `price_extra_excl` hanya ditampilkan sebagai informasi (belum termasuk
 *   pajak/layanan). Harga, pajak, dan validasi kepemilikan nilai (PTAV) ada di Odoo/BFF.
 * - Validasi di sini hanya untuk UX; server tetap sumber validasi (docs/04 §14.A.5 `invalid_input`).
 *
 * ASUMSI (belum diverifikasi): `display_type` `radio|select|pills` = pilih satu; `multi` = pilih banyak. Kontrak §14.A.5
 * hanya menyebut "satu nilai per atribut `radio/select`"; `pills` diperlakukan satu pilihan (lebih ketat dari server,
 * aman — server tidak menolak satu pilihan).
 */

/** Batas jumlah nilai atribut per baris (docs/04 §14.A.3, §14.B B-4). */
export const MAX_ATTRIBUTE_IDS = 10;
const MAX_ATTRIBUTES = 10;
const MAX_VALUES_PER_ATTRIBUTE = 40;

export type AttributeDisplay = 'radio' | 'select' | 'pills' | 'multi';

export interface AttributeValue {
  id: number;
  name: string;
  /** Tambahan harga SEBELUM pajak/layanan (indikatif). */
  price_extra_excl: number;
}

export interface ProductAttribute {
  attribute_id: number;
  name: string;
  display_type: AttributeDisplay;
  required: boolean;
  values: AttributeValue[];
}

/** Pilihan pelanggan: `attribute_id` -> daftar id nilai (PTAV). */
export type Selection = Record<number, number[]>;

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function posInt(v: unknown): number | null {
  return typeof v === 'number' && Number.isSafeInteger(v) && v > 0 ? v : null;
}

function str(v: unknown, max: number): string | null {
  return typeof v === 'string' && v.trim() ? v.trim().slice(0, max) : null;
}

export function isMultiAttribute(a: Pick<ProductAttribute, 'display_type'>): boolean {
  return a.display_type === 'multi';
}

/**
 * Allowlist `attributes` dari BFF. Entri rusak dibuang; `display_type` tak dikenal -> `radio`.
 * Nilai tanpa id/nama dibuang; atribut tanpa nilai dibuang (tidak ada yang bisa dipilih).
 */
export function parseAttributes(v: unknown): ProductAttribute[] {
  if (!Array.isArray(v)) return [];
  const out: ProductAttribute[] = [];
  const seenAttr = new Set<number>();
  for (const a of v.slice(0, MAX_ATTRIBUTES)) {
    if (!isObj(a)) continue;
    const id = posInt(a.attribute_id);
    const name = str(a.name, 80);
    if (id === null || !name || seenAttr.has(id) || !Array.isArray(a.values)) continue;
    const display: AttributeDisplay =
      a.display_type === 'select' || a.display_type === 'pills' || a.display_type === 'multi' ? a.display_type : 'radio';
    const values: AttributeValue[] = [];
    const seenVal = new Set<number>();
    for (const x of a.values.slice(0, MAX_VALUES_PER_ATTRIBUTE)) {
      if (!isObj(x)) continue;
      const vid = posInt(x.id);
      const vname = str(x.name, 80);
      if (vid === null || !vname || seenVal.has(vid)) continue;
      seenVal.add(vid);
      const extra = typeof x.price_extra_excl === 'number' && Number.isFinite(x.price_extra_excl) && x.price_extra_excl >= 0 ? x.price_extra_excl : 0;
      values.push({ id: vid, name: vname, price_extra_excl: extra });
    }
    if (values.length === 0) continue;
    seenAttr.add(id);
    out.push({ attribute_id: id, name, display_type: display, required: a.required === true, values });
  }
  return out;
}

/**
 * Normalisasi `attribute_value_ids` (cart/submit): undefined/null -> []; bukan array, isi bukan integer positif, atau
 * lebih dari 10 -> null (tolak). Hasil unik dan terurut naik (identitas baris = produk + id terurut, docs/04 §14.B B-4).
 */
export function normalizeAttributeIds(v: unknown): number[] | null {
  if (v === undefined || v === null) return [];
  if (!Array.isArray(v) || v.length > MAX_ATTRIBUTE_IDS) return null;
  const out: number[] = [];
  for (const x of v) {
    const id = posInt(x);
    if (id === null) return null;
    if (!out.includes(id)) out.push(id);
  }
  return out.sort((a, b) => a - b);
}

export function sameIds(a: readonly number[], b: readonly number[]): boolean {
  if (a.length !== b.length) return false;
  const sa = [...a].sort((x, y) => x - y);
  const sb = [...b].sort((x, y) => x - y);
  return sa.every((x, i) => x === sb[i]);
}

/** Ubah pilihan satu atribut. Atribut `multi` -> toggle; selain itu -> ganti (atau kosongkan bila `valueId` null). */
export function selectValue(attrs: readonly ProductAttribute[], selection: Selection, attributeId: number, valueId: number | null, checked = true): Selection {
  const attr = attrs.find((a) => a.attribute_id === attributeId);
  if (!attr) return selection;
  if (valueId === null) {
    const { [attributeId]: _removed, ...rest } = selection;
    void _removed;
    return rest;
  }
  if (!attr.values.some((x) => x.id === valueId)) return selection;
  if (isMultiAttribute(attr)) {
    const current = selection[attributeId] ?? [];
    const next = checked ? (current.includes(valueId) ? current : [...current, valueId]) : current.filter((x) => x !== valueId);
    if (next.length === 0) {
      const { [attributeId]: _removed, ...rest } = selection;
      void _removed;
      return rest;
    }
    return { ...selection, [attributeId]: next };
  }
  return { ...selection, [attributeId]: [valueId] };
}

/** Id nilai terpilih (hanya yang valid untuk atribut yang ada), unik, terurut naik. */
export function selectedIds(attrs: readonly ProductAttribute[], selection: Selection): number[] {
  const out: number[] = [];
  for (const a of attrs) {
    const chosen = selection[a.attribute_id] ?? [];
    const picks = isMultiAttribute(a) ? chosen : chosen.slice(0, 1);
    for (const id of picks) {
      if (a.values.some((x) => x.id === id) && !out.includes(id)) out.push(id);
    }
  }
  return out.sort((x, y) => x - y);
}

export type SelectionCheck =
  | { ok: true; ids: number[] }
  | { ok: false; reason: 'missing_required'; missing: number[] }
  | { ok: false; reason: 'too_many' };

/** Validasi UX: atribut wajib terisi, total ≤ 10 id. */
export function validateSelection(attrs: readonly ProductAttribute[], selection: Selection): SelectionCheck {
  const missing = attrs.filter((a) => a.required && (selection[a.attribute_id]?.length ?? 0) === 0).map((a) => a.attribute_id);
  if (missing.length > 0) return { ok: false, reason: 'missing_required', missing };
  const ids = selectedIds(attrs, selection);
  if (ids.length > MAX_ATTRIBUTE_IDS) return { ok: false, reason: 'too_many' };
  return { ok: true, ids };
}

/** Jumlah tambahan harga (sebelum pajak) dari nilai terpilih — INDIKATIF, hanya untuk tampilan. */
export function extraExcl(attrs: readonly ProductAttribute[], ids: readonly number[]): number {
  let total = 0;
  for (const a of attrs) {
    for (const v of a.values) if (ids.includes(v.id)) total += v.price_extra_excl;
  }
  return total;
}

/** Ringkasan teks pilihan, mis. "Gula: Kurang manis; Topping: Boba, Keju". */
export function describeSelection(attrs: readonly ProductAttribute[], ids: readonly number[]): string {
  const parts: string[] = [];
  for (const a of attrs) {
    const names = a.values.filter((v) => ids.includes(v.id)).map((v) => v.name);
    if (names.length > 0) parts.push(`${a.name}: ${names.join(', ')}`);
  }
  return parts.join('; ');
}

/** Pilihan awal: atribut wajib bernilai tunggal -> nilai pertama TIDAK dipilih otomatis (pelanggan harus memilih sadar). */
export const EMPTY_SELECTION: Selection = {};
