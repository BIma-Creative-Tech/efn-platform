'use client';

/**
 * Store wishlist per kanal (satu GET per kanal untuk seluruh kartu di halaman; useSyncExternalStore).
 * Hanya dipakai member login. Status `off` = endpoint 404/501 (flag BFF mati) -> tombol disembunyikan.
 */
import { useSyncExternalStore } from 'react';
import { apiFetch } from '@/lib/cart-client';
import { engageMessage, isFeatureOff, mapEngageCode, type WishlistItem } from '@/lib/engage';

export interface WishlistState {
  status: 'idle' | 'loading' | 'ready' | 'off' | 'error';
  ids: ReadonlySet<number>;
}

const IDLE: WishlistState = { status: 'idle', ids: new Set() };
const states = new Map<string, WishlistState>();
const listeners = new Set<() => void>();

function set(channel: string, s: WishlistState): void {
  states.set(channel, s);
  for (const l of listeners) l();
}

function get(channel: string): WishlistState {
  return states.get(channel) ?? IDLE;
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

export function useWishlistState(channel: string): WishlistState {
  return useSyncExternalStore(subscribe, () => get(channel), () => IDLE);
}

export async function loadWishlist(channel: string): Promise<void> {
  if (get(channel).status !== 'idle') return;
  set(channel, { status: 'loading', ids: new Set() });
  const r = await apiFetch<{ items: WishlistItem[] }>(`/api/me/wishlist?${new URLSearchParams({ channel })}`);
  if (!r.ok) {
    set(channel, { status: isFeatureOff(r.status, r.code) ? 'off' : 'error', ids: new Set() });
    return;
  }
  const ids = new Set((r.data?.items ?? []).filter((i) => i.channel_code === channel).map((i) => i.product_id));
  set(channel, { status: 'ready', ids });
}

/** Paksa muat ulang (mis. setelah sesi berubah). */
export function resetWishlist(): void {
  states.clear();
  for (const l of listeners) l();
}

export type ToggleResult = { ok: true } | { ok: false; code: string; message: string; featureOff: boolean; unauthenticated: boolean };

export async function setWishlisted(channel: string, productId: number, want: boolean): Promise<ToggleResult> {
  const r = want
    ? await apiFetch<null>('/api/me/wishlist/items', { method: 'PUT', body: JSON.stringify({ channel_code: channel, product_id: productId }) })
    : await apiFetch<null>(`/api/me/wishlist/items/${encodeURIComponent(channel)}/${productId}`, { method: 'DELETE' });
  if (!r.ok) {
    let code = r.status === 0 ? 'network_error' : mapEngageCode(r.status, r.code);
    if (want && code === 'limit_reached') code = 'wishlist_limit';
    return {
      ok: false,
      code,
      message: engageMessage(code),
      featureOff: isFeatureOff(r.status, r.code),
      unauthenticated: r.status === 401,
    };
  }
  const cur = get(channel);
  const ids = new Set(cur.ids);
  if (want) ids.add(productId);
  else ids.delete(productId);
  set(channel, { status: cur.status === 'idle' ? 'ready' : cur.status, ids });
  return { ok: true };
}
