/**
 * E-45: tombol "Bayar pesanan online" (menu tombol lain). Hanya tampil bila POS ini menerima bayar di kasir
 * (`pos.config.efn_online_counter_enabled`); dinonaktifkan saat offline (lookup butuh server, CLAUDE.md).
 * Alur: order kosong -> kode bayar -> lookup server -> baris "Pelunasan Pesanan Online" terkunci (qty 1, nominal dari
 * server, tanpa pajak) -> bayar tunai/EDC/QRIS statis.
 */
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { EfnCounterCodeDialog } from "@efn_online_pos_counter/app/components/efn_counter_code_dialog/efn_counter_code_dialog";
import {
    efnCounterProduct,
    efnIsOffline,
    efnWithCounterUnlocked,
} from "@efn_online_pos_counter/app/utils/efn_counter";

patch(ControlButtons.prototype, {
    get efnShowCounterPay() {
        return Boolean(this.pos.config.efn_online_counter_enabled);
    },

    get efnCounterPayDisabled() {
        return efnIsOffline(this.pos);
    },

    efnCounterAlert(body) {
        this.dialog.add(AlertDialog, { title: _t("Bayar pesanan online"), body });
    },

    async efnPayOnlineOrder() {
        this.props.close?.();
        if (efnIsOffline(this.pos)) {
            this.efnCounterAlert(_t("POS sedang offline. Bayar pesanan online membutuhkan koneksi ke server."));
            return;
        }
        const order = this.pos.getOrder();
        if (!order) {
            return;
        }
        if (order.lines.length || order.getPartner?.()) {
            this.efnCounterAlert(
                _t("Gunakan order baru yang kosong (tanpa barang dan tanpa pelanggan) untuk pelunasan pesanan online.")
            );
            return;
        }
        const product = efnCounterProduct(this.pos);
        if (!product) {
            this.efnCounterAlert(_t("Produk pelunasan pesanan online tidak termuat. Muat ulang POS."));
            return;
        }
        const found = await makeAwaitable(this.dialog, EfnCounterCodeDialog, {
            title: _t("Bayar pesanan online"),
            submit: async (code) => {
                if (efnIsOffline(this.pos)) {
                    return { error_code: "offline", message: _t("POS offline. Kode tidak dapat diperiksa.") };
                }
                return await this.pos.data.call("pos.config", "efn_online_counter_lookup", [
                    [this.pos.config.id],
                    code,
                ]);
            },
        });
        if (!found?.order_label) {
            return;
        }
        await efnWithCounterUnlocked(() =>
            this.pos.addLineToCurrentOrder(
                {
                    product_tmpl_id: product.product_tmpl_id,
                    product_id: product,
                    // G22 W-12 (docs/04 §14.C.4): porsi tunai; porsi Poin EFN sudah ditahan BFF.
                    price_unit: found.amount_due,
                    qty: 1,
                    efn_online_order_label: found.order_label,
                },
                { merge: false },
                false
            )
        );
    },
});
