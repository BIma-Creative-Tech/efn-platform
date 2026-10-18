/**
 * E-45: sebelum validasi order pelunasan pesanan online (atau refund-nya) — POS harus online (validasi server saat
 * sync; offline = DIBLOKIR agar uang tidak diterima untuk pesanan yang tidak bisa dicatat), order hanya berisi baris
 * pelunasan, tanpa pelanggan dan tanpa faktur POS (pajak hanya di faktur SO). Server tetap penentu akhir.
 */
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { patch } from "@web/core/utils/patch";
import {
    efnIsCounterLine,
    efnIsOffline,
    efnOrderHasCounterLine,
} from "@efn_online_pos_counter/app/utils/efn_counter";

patch(PaymentScreen.prototype, {
    efnCounterBlockReason(order) {
        if (!efnOrderHasCounterLine(order)) {
            return null;
        }
        if (efnIsOffline(this.pos)) {
            return _t("POS sedang offline. Pelunasan pesanan online hanya bisa divalidasi saat terhubung ke server.");
        }
        if (order.lines.length !== 1 || !order.lines.every((line) => efnIsCounterLine(line))) {
            return _t("Pelunasan pesanan online harus dalam order tersendiri (hanya baris pelunasan).");
        }
        if (order.getPartner?.()) {
            return _t("Hapus pelanggan dari order pelunasan pesanan online.");
        }
        if (order.isToInvoice?.()) {
            return _t("Order pelunasan pesanan online tidak difakturkan di POS (faktur dibuat dari pesanan online).");
        }
        return null;
    },

    async validateOrder(isForceValidate) {
        const reason = this.efnCounterBlockReason(this.currentOrder);
        if (reason) {
            this.dialog.add(AlertDialog, { title: _t("Bayar pesanan online"), body: reason });
            return;
        }
        return await super.validateOrder(...arguments);
    },
});
