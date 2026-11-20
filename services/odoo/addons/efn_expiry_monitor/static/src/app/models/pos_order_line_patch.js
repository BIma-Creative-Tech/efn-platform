/**
 * efn_expiry_monitor: qty baris markdown dibatasi sisa kuota (`qty_cap`) yang dihitung saat markdown diterapkan.
 * Kelebihan ditolak dengan pesan (kasir menambah sisanya sebagai baris harga normal; pindaian berikutnya otomatis
 * harga normal bila kuota habis). Server tetap memvalidasi saat sync (kelebihan -> event `markdown_cap_exceeded`).
 * `_efnMarkdownMax` adalah properti klien (hilang saat POS dimuat ulang; server tetap penentu).
 */
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";

patch(PosOrderline.prototype, {
    setQuantity(quantity, keep_price) {
        const max = this._efnMarkdownMax;
        if (this.efn_markdown_ref && typeof max === "number" && quantity !== "" && quantity !== "remove") {
            const value = typeof quantity === "number" ? quantity : parseFloat(quantity);
            if (!Number.isNaN(value) && value > max) {
                return {
                    title: _t("Kuota markdown"),
                    body: _t(
                        "Sisa kuota markdown lot ini %s. Tambahkan kelebihannya sebagai baris harga normal.",
                        max
                    ),
                };
            }
        }
        return super.setQuantity(...arguments);
    },
});
