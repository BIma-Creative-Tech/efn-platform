/**
 * E-45: baris "Pelunasan Pesanan Online" terkunci — qty tetap 1, harga dan diskon tidak bisa diubah. Hapus baris
 * tetap boleh. Baris refund (menunjuk baris asal) mengikuti aturan refund core. Server menolak baris yang diubah.
 */
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { efnCounterUnlocked } from "@efn_online_pos_counter/app/utils/efn_counter";

function locked(line) {
    return Boolean(line.efn_online_order_label) && !line.refunded_orderline_id && !efnCounterUnlocked();
}

patch(PosOrderline.prototype, {
    setQuantity(quantity, keep_price) {
        if (locked(this) && quantity !== "" && quantity !== "remove") {
            const value = typeof quantity === "number" ? quantity : parseFloat(quantity);
            if (value !== 1) {
                return {
                    title: _t("Pelunasan pesanan online"),
                    body: _t("Jumlah baris pelunasan pesanan online tidak dapat diubah. Hapus baris bila salah."),
                };
            }
        }
        return super.setQuantity(...arguments);
    },

    setUnitPrice(price) {
        if (locked(this)) {
            return;
        }
        return super.setUnitPrice(...arguments);
    },

    setDiscount(discount) {
        if (locked(this) && Number(discount || 0) !== 0) {
            return;
        }
        return super.setDiscount(...arguments);
    },
});
