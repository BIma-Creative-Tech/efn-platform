/**
 * efn_expiry_monitor (docs/04 §28.5a, fit-gap G45 §2-§3): helper murni POS — parsing data preload
 * `pos.config.efn_expiry_pos_data`, pemilihan lot FEFO, blokir kedaluwarsa, markdown aktif.
 * Server = penentu akhir (validasi saat sync order); logika di sini UX + berlaku offline.
 */

/** Data preload (di-cache per string JSON). `{ enabled: false }` bila flag mati / data rusak. */
export function efnExpiryData(pos) {
    const raw = pos?.config?.efn_expiry_pos_data;
    if (!raw) {
        return { enabled: false };
    }
    if (pos._efnExpiryCache?.raw === raw) {
        return pos._efnExpiryCache.data;
    }
    let data;
    try {
        data = JSON.parse(raw);
    } catch {
        data = { enabled: false };
    }
    if (!data || typeof data !== "object") {
        data = { enabled: false };
    }
    data.lots = data.lots || {};
    data.markdowns = data.markdowns || [];
    pos._efnExpiryCache = { raw, data };
    return data;
}

/** "YYYY-MM-DD HH:MM:SS" (UTC dari server) -> epoch ms, atau null. */
export function efnParseUtc(value) {
    if (!value) {
        return null;
    }
    const ms = Date.parse(String(value).replace(" ", "T") + "Z");
    return Number.isNaN(ms) ? null : ms;
}

/** Tanggal lokal perangkat "YYYY-MM-DD" (periode markdown dicek dengan jam perangkat, keputusan terkunci). */
export function efnLocalToday(now = new Date()) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** Baris server `[lot, exp, removal, qty]` -> `{ name, exp, removal, qty }`. */
export function efnNormalizeLots(rows) {
    return (rows || [])
        .filter((row) => Array.isArray(row) && row[0])
        .map((row) => ({
            name: String(row[0]),
            exp: efnParseUtc(row[1]),
            removal: efnParseUtc(row[2]),
            qty: typeof row[3] === "number" ? row[3] : null,
        }));
}

export function efnLotBlocked(lot, nowMs) {
    if (!lot) {
        return false;
    }
    return (lot.exp !== null && lot.exp <= nowMs) || (lot.removal !== null && lot.removal <= nowMs);
}

/** Markdown aktif untuk produk × lot pada tanggal lokal `today`, atau null. */
export function efnActiveMarkdown(markdowns, productId, lotName, today) {
    return (
        (markdowns || []).find(
            (md) =>
                md.product_id === productId &&
                md.lot === lotName &&
                md.date_from <= today &&
                today <= md.date_to
        ) || null
    );
}

/**
 * FEFO: lot kedaluwarsa terdekat yang belum kedaluwarsa/lewat removal date, BUKAN lot markdown aktif, dan masih
 * ada stok (qty server − pemakaian lokal). Lot tanpa tanggal di urutan terakhir. Null bila tidak ada kandidat.
 */
export function efnPickFefo(lots, markdowns, productId, nowMs, today, usedQty = {}) {
    const candidates = (lots || []).filter(
        (lot) =>
            !efnLotBlocked(lot, nowMs) &&
            !efnActiveMarkdown(markdowns, productId, lot.name, today) &&
            (lot.qty === null || lot.qty - (usedQty[lot.name] || 0) > 0)
    );
    candidates.sort((a, b) => {
        const ea = a.exp === null ? Infinity : a.exp;
        const eb = b.exp === null ? Infinity : b.exp;
        if (ea !== eb) {
            return ea - eb;
        }
        return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
    });
    return candidates.length ? candidates[0].name : null;
}

/** Sisa kuota markdown (Infinity bila tanpa batas). */
export function efnMarkdownRemaining(md, localUsed = 0) {
    if (!md || !(md.qty_cap > 0)) {
        return Infinity;
    }
    return Math.max(md.qty_cap - (md.qty_sold || 0) - localUsed, 0);
}

export function efnIsOffline(pos) {
    return Boolean(pos?.data?.network?.offline);
}
