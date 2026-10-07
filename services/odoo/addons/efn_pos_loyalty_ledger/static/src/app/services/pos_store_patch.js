/**
 * Poin EFN: mencegah order berubah setelah reservasi (docs/08 §6.3 G). Kembali ke layar produk,
 * mengganti pelanggan, atau menghapus order dengan tukar poin aktif -> dialog "Batalkan tukar
 * poin?" -> release (server Odoo -> BFF; gagal = diantre outbox) + hapus baris poin.
 */
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { ask } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { efnIsOffline } from "@efn_pos_otp_gate/app/utils/efn_otp_flow";

patch(PosStore.prototype, {
    async efnConfirmCancelRedeem() {
        return await ask(this.dialog, {
            title: _t("Batalkan tukar poin?"),
            body: _t(
                "Order ini memakai Poin EFN. Untuk mengubah order atau pelanggan, penukaran poin " +
                    "dibatalkan dan poin dikembalikan ke pelanggan."
            ),
            confirmLabel: _t("Batalkan tukar poin"),
            cancelLabel: _t("Kembali"),
        });
    },

    async efnCancelRedeem(order) {
        if (!efnIsOffline(this)) {
            try {
                await this.data.call("pos.config", "efn_loyalty_redeem_release", [[this.config.id], order.uuid]);
            } catch {
                // Server melepas reservasi saat order tersinkron tanpa baris poin.
            }
        }
        order.efnRemovePointsLines();
    },

    async onClickBackButton() {
        const order = this.getOrder();
        if (this.router.state.current === "PaymentScreen" && order?.efnHasActiveRedeem()) {
            if (!(await this.efnConfirmCancelRedeem())) {
                return;
            }
            await this.efnCancelRedeem(order);
        }
        return await super.onClickBackButton(...arguments);
    },

    async selectPartner(currentOrder = this.getOrder()) {
        if (currentOrder?.efnHasActiveRedeem()) {
            if (!(await this.efnConfirmCancelRedeem())) {
                return currentOrder.getPartner();
            }
            await this.efnCancelRedeem(currentOrder);
        }
        return await super.selectPartner(...arguments);
    },

    async _onBeforeDeleteOrder(order) {
        if (order?.efnHasActiveRedeem()) {
            await this.efnCancelRedeem(order);
        }
        return await super._onBeforeDeleteOrder(...arguments);
    },
});
