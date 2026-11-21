/**
 * Preferensi sidebar (ciut jadi ikon + grup yang dilipat), disimpan di localStorage sisi klien. Murni (parse/serialize
 * defensif: nilai rusak -> bawaan). Bukan data sensitif; kegagalan localStorage diabaikan oleh pemanggil.
 */
export const NAV_PREFS_KEY = 'insight-nav';

export interface NavPrefs {
  /** Sidebar diciutkan jadi rail ikon. */
  collapsed: boolean;
  /** id grup yang sedang dilipat. */
  closed: string[];
}

export const DEFAULT_NAV_PREFS: NavPrefs = { collapsed: false, closed: [] };

const ID_RE = /^[a-z][a-z0-9-]{0,23}$/;

export function parseNavPrefs(raw: string | null | undefined): NavPrefs {
  if (!raw) return { ...DEFAULT_NAV_PREFS, closed: [] };
  try {
    const v: unknown = JSON.parse(raw);
    if (typeof v !== 'object' || v === null) return { ...DEFAULT_NAV_PREFS, closed: [] };
    const o = v as Record<string, unknown>;
    const closed = Array.isArray(o.closed) ? o.closed.filter((x): x is string => typeof x === 'string' && ID_RE.test(x)).slice(0, 10) : [];
    return { collapsed: o.collapsed === true, closed: Array.from(new Set(closed)) };
  } catch {
    return { ...DEFAULT_NAV_PREFS, closed: [] };
  }
}

export function serializeNavPrefs(p: NavPrefs): string {
  return JSON.stringify({ collapsed: p.collapsed, closed: p.closed });
}

export function toggleClosed(closed: readonly string[], id: string): string[] {
  return closed.includes(id) ? closed.filter((x) => x !== id) : [...closed, id];
}
