/**
 * efn_expiry_monitor — aset POS (docs/04 §28.5a, fit-gap G45 §2-§3). Aktif hanya bila
 * `efn_expiry_monitor.enabled` (data preload `pos.config.efn_expiry_pos_data.enabled`).
 *
 * (a) FEFO otomatis: produk ber-lot yang dipindai TANPA lot (bukan GS1 AI 10) -> lot kedaluwarsa terdekat yang belum
 *     kedaluwarsa/lewat removal date, bukan lot markdown aktif, masih ber-stok; tanpa popup. Tidak ada kandidat ->
 *     popup lot CE (pilihan kasir = "manual").
 * (b) Blokir lot kedaluwarsa/removal date lewat (dari GS1 atau pilihan manual) kecuali override: kasir dengan grup
 *     `group_expiry_override` (konfirmasi, juga offline) atau supervisor + PIN yang diverifikasi server (online saja).
 *     Baris ditandai `efn_expiry_override`; server mencatat jejak audit saat sync.
 * (c) Harga markdown hanya bila lot dari GS1 (01)(10) atau dipilih eksplisit; data lot & markdown dimuat saat sesi
 *     dibuka (berlaku offline) dan diperbarui per produk saat online (`efn_expiry_lot_candidates`). `qty_cap`
 *     dihormati lokal (qty baris markdown dibatasi sisa kuota; kuota habis -> harga normal); server memvalidasi ulang.
 *
 * API POS 19.0 yang dipakai (diverifikasi dari source 19.0, lihat README): `PosStore.editLots(product,
 * packLotLinesToEdit)`, `PosStore.addLineToOrder(vals, order, opts, configure)` dengan `opts.code.type === "lot"`
 * dari `_barcodeGS1Action`, `getOrder()`, `data.call`, `dialog`, `notification`, `PosOrderline.setUnitPrice/
 * setDiscount/setQuantity`, `price_type = "manual"`.
 */
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { ask, makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { EfnExpiryOverrideDialog } from "@efn_expiry_monitor/app/components/efn_expiry_override_dialog/efn_expiry_override_dialog";
import {
    efnActiveMarkdown,
    efnExpiryData,
    efnIsOffline,
    efnLocalToday,
    efnLotBlocked,
    efnMarkdownRemaining,
    efnNormalizeLots,
    efnPickFefo,
} from "@efn_expiry_monitor/app/utils/efn_expiry";

function lotTracked(product) {
    const tmpl = product?.product_tmpl_id;
    return (tmpl?.tracking || product?.tracking) === "lot";
}

/**
 * Produk baris dari `vals` addLineToOrder. Kartu produk CE 19 (`ProductScreen.addProductToOrder`) hanya mengirim
 * `{ product_tmpl_id }`; core lalu memakai varian pertama. Pindaian barcode/GS1 mengirim `product_id` juga.
 */
function lineProduct(pos, vals) {
    if (vals?.product_id && typeof vals.product_id === "object") {
        return vals.product_id;
    }
    let tmpl = vals?.product_tmpl_id;
    if (typeof tmpl === "number") {
        tmpl = pos.data?.models?.["product.template"]?.get(tmpl);
    }
    return (tmpl && typeof tmpl === "object" && tmpl.product_variant_ids?.[0]) || null;
}

function packLotNames(result) {
    const names = (result?.newPackLotLines || []).map((line) => line?.lot_name).filter(Boolean);
    const modified = result?.modifiedPackLotLines;
    if (Array.isArray(modified)) {
        names.push(...modified.map((item) => (Array.isArray(item) ? item[1] : item)).filter(Boolean));
    } else if (modified && typeof modified === "object") {
        names.push(...Object.values(modified).filter(Boolean));
    }
    return names.map(String);
}

/** Order lokal yang belum tersinkron (heuristik: id belum numerik atau masih draft). */
function unsyncedOrders(pos) {
    return (pos.models["pos.order"]?.getAll?.() || []).filter(
        (order) => typeof order.id !== "number" || order.state === "draft"
    );
}

patch(PosStore.prototype, {
    efnExpiryEnabled() {
        return Boolean(efnExpiryData(this).enabled);
    },

    efnAlert(body) {
        this.dialog.add(AlertDialog, { title: _t("Kedaluwarsa & markdown"), body });
    },

    /** Lot & markdown satu produk: snapshot sesi, diperbarui dari server bila online. */
    async efnLotsForProduct(product, lotName = null) {
        const data = efnExpiryData(this);
        const key = String(product.id);
        let lots = efnNormalizeLots(data.lots[key]);
        let markdowns = data.markdowns.filter((md) => md.product_id === product.id);
        if (!efnIsOffline(this)) {
            try {
                const args = [[this.config.id], product.id];
                if (lotName) {
                    args.push(lotName);
                }
                const res = await this.data.call("pos.config", "efn_expiry_lot_candidates", args);
                if (res?.ok && res.enabled) {
                    lots = efnNormalizeLots(res.lots);
                    markdowns = res.markdowns || [];
                    // Simpan ke cache sesi: data terbaru tetap dipakai bila POS kemudian offline.
                    data.lots[key] = res.lots;
                    data.markdowns = [
                        ...data.markdowns.filter((md) => md.product_id !== product.id),
                        ...markdowns,
                    ];
                }
            } catch {
                // Jaringan gagal: pakai snapshot sesi (berlaku offline).
            }
        }
        return { lots, markdowns };
    },

    /** {lot: qty} pemakaian produk di order lokal yang belum tersinkron. */
    efnLocalUsedQty(productId) {
        const used = {};
        for (const order of unsyncedOrders(this)) {
            for (const line of order.lines || []) {
                if (line.product_id?.id !== productId) {
                    continue;
                }
                for (const lot of line.pack_lot_ids || []) {
                    if (lot.lot_name) {
                        used[lot.lot_name] = (used[lot.lot_name] || 0) + (line.qty || 0);
                    }
                }
            }
        }
        return used;
    },

    efnMarkdownLocalUsed(markdownId, exceptLine = null) {
        let used = 0;
        for (const order of unsyncedOrders(this)) {
            for (const line of order.lines || []) {
                if (line !== exceptLine && line.efn_markdown_ref === markdownId) {
                    used += line.qty || 0;
                }
            }
        }
        return used;
    },

    /** Override lot kedaluwarsa. true = boleh dijual (baris ditandai). */
    async efnRequestOverride(order, product, lotName) {
        const data = efnExpiryData(this);
        if (data.override) {
            return Boolean(
                await ask(this.dialog, {
                    title: _t("Lot kedaluwarsa"),
                    body: _t(
                        "Lot %s sudah kedaluwarsa / melewati batas jual. Anda berwenang override; penjualan ini " +
                            "dicatat di jejak audit. Lanjutkan?",
                        lotName
                    ),
                })
            );
        }
        if (efnIsOffline(this)) {
            this.efnAlert(
                _t(
                    "Lot %s kedaluwarsa dan diblokir. Persetujuan supervisor membutuhkan koneksi server (POS offline).",
                    lotName
                )
            );
            return false;
        }
        const orderRef = order?.uuid || this.getOrder()?.uuid;
        if (!orderRef) {
            return false;
        }
        let approvers;
        try {
            approvers = await this.data.call("pos.config", "efn_expiry_override_approvers", [[this.config.id]]);
        } catch {
            this.efnAlert(_t("Server tidak dapat dihubungi. Lot kedaluwarsa tidak dapat disetujui sekarang."));
            return false;
        }
        if (!approvers?.ok || !approvers.approvers?.length) {
            this.efnAlert(
                approvers?.message ||
                    _t("Tidak ada supervisor override dengan PIN persetujuan untuk POS ini. Lot kedaluwarsa diblokir.")
            );
            return false;
        }
        let granted = false;
        const confirmed = await makeAwaitable(this.dialog, EfnExpiryOverrideDialog, {
            title: _t("Persetujuan supervisor"),
            lotName,
            approvers: approvers.approvers,
            submit: async ({ approverId, pin, reason }) => {
                if (efnIsOffline(this)) {
                    return { ok: false, message: _t("POS offline. Persetujuan tidak dapat diverifikasi.") };
                }
                const result = await this.data.call("pos.config", "efn_expiry_override_request", [
                    [this.config.id],
                    orderRef,
                    product.id,
                    lotName,
                    approverId,
                    pin,
                    reason,
                ]);
                if (result?.ok) {
                    granted = true;
                }
                return result;
            },
        });
        return confirmed === true && granted;
    },

    async editLots(product, packLotLinesToEdit) {
        if (!this.efnExpiryEnabled() || !lotTracked(product)) {
            return super.editLots(...arguments);
        }
        const { lots, markdowns } = await this.efnLotsForProduct(product);
        const nowMs = Date.now();
        const today = efnLocalToday();
        const editing = Boolean(packLotLinesToEdit?.length);
        if (!editing) {
            const name = efnPickFefo(lots, markdowns, product.id, nowMs, today, this.efnLocalUsedQty(product.id));
            if (name) {
                this._efnLotPick = { source: "fefo", name, lots, markdowns, override: false, productId: product.id };
                return { newPackLotLines: [{ lot_name: name }] };
            }
        }
        const result = await super.editLots(...arguments);
        if (!result) {
            return result;
        }
        const names = packLotNames(result);
        const byName = Object.fromEntries(lots.map((lot) => [lot.name, lot]));
        const blocked = names.find((name) => efnLotBlocked(byName[name], nowMs));
        if (editing) {
            // Ubah lot baris yang sudah ada: tidak membuka override/markdown di jalur ini.
            if (blocked) {
                this.efnAlert(
                    _t("Lot %s kedaluwarsa. Hapus baris lalu pindai ulang untuk meminta override.", blocked)
                );
                return null;
            }
            const line = this.getOrder()?.getSelectedOrderline?.();
            if (line?.efn_markdown_ref) {
                this.efnAlert(_t("Lot baris markdown tidak dapat diganti. Hapus baris lalu pindai ulang."));
                return null;
            }
            return result;
        }
        const pick = {
            source: "modifiedPackLotLines" in result ? "manual" : "auto",
            name: names[0] || null,
            lots,
            markdowns,
            override: false,
            productId: product.id,
        };
        if (blocked) {
            const allowed = await this.efnRequestOverride(this._efnPendingOrder || this.getOrder(), product, blocked);
            if (!allowed) {
                return null;
            }
            pick.override = true;
        }
        this._efnLotPick = pick;
        return result;
    },

    async addLineToOrder(vals, order, opts = {}, configure = true) {
        // G48: kartu produk hanya mengirim product_tmpl_id -> produk diturunkan dari template (sebelumnya patch ini
        // dilewati sehingga efn_lot_source/markdown/override tidak pernah dipasang dari kartu produk).
        const product = lineProduct(this, vals);
        if (!this.efnExpiryEnabled() || !product || !lotTracked(product)) {
            return super.addLineToOrder(...arguments);
        }
        this._efnLotPick = null;
        const code = opts?.code;
        if (code && code.type === "lot" && code.code) {
            // GS1 (01)(10): lot eksplisit dari stiker.
            const lotName = String(code.code);
            const { lots, markdowns } = await this.efnLotsForProduct(product, lotName);
            const lot = lots.find((item) => item.name === lotName);
            let override = false;
            if (efnLotBlocked(lot, Date.now())) {
                override = await this.efnRequestOverride(order, product, lotName);
                if (!override) {
                    return;
                }
            }
            this._efnLotPick = { source: "gs1", name: lotName, lots, markdowns, override, productId: product.id };
        }
        this._efnPendingOrder = order;
        let line;
        try {
            line = await super.addLineToOrder(...arguments);
        } finally {
            this._efnPendingOrder = null;
        }
        const pick = this._efnLotPick;
        this._efnLotPick = null;
        // Produk aktual baris (varian bisa diganti konfigurator core) harus sama dengan produk saat lot dipilih.
        const picked = line?.product_id;
        if (
            line &&
            pick?.name &&
            !line.efn_lot_source &&
            picked &&
            picked.id === pick.productId &&
            (line.pack_lot_ids || []).some((lot) => lot.lot_name === pick.name)
        ) {
            this.efnApplyLotPick(line, picked, pick);
        }
        return line;
    },

    efnApplyLotPick(line, product, pick) {
        line.efn_lot_source = pick.source;
        if (pick.override) {
            line.efn_expiry_override = true;
        }
        if (pick.source !== "gs1" && pick.source !== "manual") {
            return;
        }
        const md = efnActiveMarkdown(pick.markdowns, product.id, pick.name, efnLocalToday());
        if (!md) {
            return;
        }
        const remaining = efnMarkdownRemaining(md, this.efnMarkdownLocalUsed(md.id, line));
        if (!(remaining > 0)) {
            this.notification.add(_t("Kuota markdown lot %s habis: harga normal.", pick.name), { type: "warning" });
            return;
        }
        if (remaining !== Infinity && line.qty > remaining) {
            line.setQuantity(remaining, true);
            this.notification.add(
                _t("Sisa kuota markdown %s; kelebihan dijual sebagai baris harga normal.", remaining),
                { type: "warning" }
            );
        }
        line.efn_markdown_ref = md.id;
        // Properti klien (bukan field): batas qty baris markdown untuk setQuantity.
        line._efnMarkdownMax = remaining === Infinity ? null : remaining;
        if (md.price !== null && md.price !== undefined) {
            line.setUnitPrice(md.price);
            line.price_type = "manual";
        } else if (md.discount_pct) {
            line.setDiscount(md.discount_pct);
        }
        this.notification.add(_t("Markdown lot %s diterapkan.", pick.name), { type: "success" });
    },
});
