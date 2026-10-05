import { Errors } from '../../common/errors/api-error';

/** Field form `POST /v1/market-scout/reports` (kontrak §3.3), sudah tervalidasi. */
export interface ReportFields {
  productName: string;
  price: number;
  competitorName: string | null;
  lat: number | null;
  lng: number | null;
}

export interface RawReportForm {
  fields: Record<string, string>;
  photo: Buffer | null;
}

// eslint-disable-next-line no-control-regex
const CONTROL_CHARS_RE = /[\u0000-\u001f\u007f]/;
const PRICE_RE = /^\d{1,12}(\.\d{1,2})?$/;
const COORD_RE = /^-?\d{1,3}(\.\d{1,10})?$/;

function text(fields: Record<string, string>, key: string, max: number, required: boolean): string | null {
  const raw = fields[key];
  const v = typeof raw === 'string' ? raw.trim() : '';
  if (v.length === 0) {
    if (required) throw Errors.invalidRequest();
    return null;
  }
  if (v.length > max || CONTROL_CHARS_RE.test(v)) throw Errors.invalidRequest();
  return v;
}

/** SEC-17: presisi lokasi yang disimpan/diteruskan = 3 desimal (±110 m), dibulatkan DI SERVER. */
export const COORD_DECIMALS = 3;

export function roundCoord(n: number): number {
  const f = 10 ** COORD_DECIMALS;
  const r = Math.round(n * f) / f;
  return Object.is(r, -0) ? 0 : r;
}

function coord(fields: Record<string, string>, key: string, limit: number): number | null {
  const raw = (fields[key] ?? '').trim();
  if (raw.length === 0) return null;
  if (!COORD_RE.test(raw)) throw Errors.invalidRequest();
  const n = Number(raw);
  if (!Number.isFinite(n) || Math.abs(n) > limit) throw Errors.invalidRequest();
  return roundCoord(n);
}

export function parseReportFields(fields: Record<string, string>): ReportFields {
  const productName = text(fields, 'product_name', 200, true) as string;
  const priceRaw = (fields.price ?? '').trim();
  if (!PRICE_RE.test(priceRaw)) throw Errors.invalidRequest('Harga tidak valid.');
  const price = Number(priceRaw);
  if (!(price > 0)) throw Errors.invalidRequest('Harga tidak valid.');
  const lat = coord(fields, 'lat', 90);
  const lng = coord(fields, 'lng', 180);
  if ((lat === null) !== (lng === null)) throw Errors.invalidRequest();
  return {
    productName,
    price,
    competitorName: text(fields, 'competitor_name', 200, false),
    lat,
    lng,
  };
}
