/**
 * E-45 (docs/04 §12.2): helper bersama bayar pesanan online di kasir. Server = penentu akhir (validasi sync); kunci
 * baris di sini hanya UX.
 */

const lockState = { unlocked: 0 };

/** Jalankan `fn` dengan kunci baris pelunasan dibuka (hanya saat baris dibuat dari hasil lookup server). */
export async function efnWithCounterUnlocked(fn) {
    lockState.unlocked += 1;
    try {
        return await fn();
    } finally {
        lockState.unlocked -= 1;
    }
}

export function efnCounterUnlocked() {
    return lockState.unlocked > 0;
}

export function efnIsOffline(pos) {
    return Boolean(pos?.data?.network?.offline);
}

/** Baris pelunasan (jual) atau refund atas baris pelunasan. */
export function efnIsCounterLine(line) {
    return Boolean(line?.efn_online_order_label || line?.refunded_orderline_id?.efn_online_order_label);
}

export function efnOrderHasCounterLine(order) {
    return (order?.lines || []).some((line) => efnIsCounterLine(line));
}

export function efnCounterProduct(pos) {
    const value = pos?.config?.efn_online_counter_product_id;
    if (!value) {
        return null;
    }
    if (typeof value === "number") {
        return pos.models["product.product"].get(value) || null;
    }
    return value;
}

/** Normalisasi input kasir (huruf besar, tanpa spasi/strip) — sama dengan server. */
export function efnNormalizeCounterCode(value) {
    return String(value || "")
        .replace(/[\s-]/g, "")
        .toUpperCase();
}

export const EFN_COUNTER_CODE_RE = /^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$/;
