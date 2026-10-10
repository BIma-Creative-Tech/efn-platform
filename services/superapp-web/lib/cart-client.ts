/**
 * Helper BROWSER untuk belanja online: panggilan ke Route Handler origin sendiri (`/api/cart|orders|catalog`) dan
 * keranjang tamu di `localStorage`. Tidak ada token/secret di sini (sesi = cookie HttpOnly yang dikirim otomatis).
 *
 * - `localStorage` bisa tidak tersedia (mode privat, diblokir) -> setiap akses dibungkus try/catch.
 * - Keranjang tamu hanya `product_id` + `qty` (+ nama untuk tampilan) — TANPA harga (docs/04 §10.3.3).
 */
import {
  addItem,
  GUEST_CART_KEY,
  mergeGroups,
  parseGuestCart,
  sanitizeCart,
  serializeGuestCart,
  type AddItemInput,
  type Cart,
  type CartGroup,
  type CartPutResult,
  type GuestCart,
} from './cart';
import { onlineErrorMessage } from './orders';

export type ApiResult<T> =
  | { ok: true; status: number; data: T; date: string | null }
  | { ok: false; status: number; code: string; message: string };

/** fetch JSON same-origin. Error -> pesan Indonesia dari peta K-ON5 (lib/orders.ts). */
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<ApiResult<T>> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { accept: 'application/json', ...(init.body ? { 'content-type': 'application/json' } : {}), ...init.headers },
    });
  } catch {
    return { ok: false, status: 0, code: 'network_error', message: 'Koneksi terputus. Periksa internet Anda lalu coba lagi.' };
  }
  let body: unknown = null;
  if (res.status !== 204) {
    try {
      body = await res.json();
    } catch {
      body = null;
    }
  }
  if (res.ok) return { ok: true, status: res.status, data: body as T, date: res.headers.get('date') };
  const err = (body as { error?: { code?: unknown; message?: unknown } } | null)?.error;
  const code = typeof err?.code === 'string' ? err.code : `http_${res.status}`;
  const message = typeof err?.message === 'string' && err.message ? err.message : onlineErrorMessage(code);
  return { ok: false, status: res.status, code, message };
}

/** Arahkan ke halaman masuk lalu kembali ke halaman ini (path relatif saja; divalidasi lagi oleh safeNextPath). */
export function goToLogin(nextPath: string): void {
  window.location.assign(`/masuk?next=${encodeURIComponent(nextPath)}&expired=1`);
}

// ---------- keranjang tamu ----------

export function readGuestCart(): GuestCart {
  try {
    return parseGuestCart(window.localStorage.getItem(GUEST_CART_KEY));
  } catch {
    return { groups: [], names: {} };
  }
}

export function writeGuestCart(cart: GuestCart): boolean {
  try {
    if (cart.groups.length === 0) window.localStorage.removeItem(GUEST_CART_KEY);
    else window.localStorage.setItem(GUEST_CART_KEY, serializeGuestCart(cart));
    return true;
  } catch {
    return false;
  }
}

export function clearGuestCart(): void {
  try {
    window.localStorage.removeItem(GUEST_CART_KEY);
  } catch {
    // diabaikan
  }
}

// ---------- keranjang server ----------

export async function getServerCart(): Promise<ApiResult<Cart>> {
  const r = await apiFetch<unknown>('/api/cart');
  if (!r.ok) return r;
  const cart = sanitizeCart(r.data);
  return cart ? { ...r, data: cart } : { ok: false, status: 502, code: 'upstream_error', message: onlineErrorMessage('upstream_error') };
}

export async function putServerCart(version: number, groups: CartGroup[]): Promise<ApiResult<CartPutResult>> {
  return apiFetch<CartPutResult>('/api/cart', { method: 'PUT', body: JSON.stringify({ version, groups }) });
}

/**
 * Ubah keranjang server dengan optimistic concurrency: GET -> ubah -> PUT; `409 cart_version_conflict`
 * -> ambil versi terbaru dan ulang sekali.
 */
export async function updateServerCart(mutate: (groups: CartGroup[]) => CartGroup[] | null): Promise<ApiResult<CartPutResult>> {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const current = await getServerCart();
    if (!current.ok) return current;
    const next = mutate(current.data.groups);
    if (next === null) return { ok: false, status: 400, code: 'invalid_request', message: 'Barang tidak dapat ditambahkan.' };
    const put = await putServerCart(current.data.version, next);
    if (put.ok || put.code !== 'cart_version_conflict') return put;
  }
  return { ok: false, status: 409, code: 'cart_version_conflict', message: onlineErrorMessage('cart_version_conflict') };
}

export async function addToServerCart(input: AddItemInput): Promise<ApiResult<CartPutResult> & { outletChanged?: boolean }> {
  let outletChanged = false;
  const r = await updateServerCart((groups) => {
    const res = addItem(groups, input);
    if (!res.ok) return null;
    outletChanged = res.outletChanged;
    return res.groups;
  });
  return { ...r, outletChanged };
}

/** Setelah login: gabungkan keranjang tamu ke server lalu kosongkan `localStorage` bila berhasil. */
export async function mergeGuestCartIntoServer(): Promise<ApiResult<CartPutResult> | null> {
  const guest = readGuestCart();
  if (guest.groups.length === 0) return null;
  const r = await updateServerCart((groups) => mergeGroups(groups, guest.groups));
  if (r.ok) clearGuestCart();
  return r;
}
