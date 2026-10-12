/**
 * Refund order berpoin (docs/08 §9.2 E-30, kontrak K-18-5, Q-F18-4 proporsional): layar refund menambahkan
 * pembayaran Poin EFN NEGATIF senilai porsi poin barang yang direfund (dihitung server lewat
 * `efn_loyalty_refund_preview`, kumulatif atas refund sebelumnya); sisa uang ke metode asal (E-31). Server
 * memvalidasi lagi saat sync (melebihi poin asal -> ditolak; selisih alokasi -> reverse ditahan + review).
 */
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { efnIsOffline } from "@efn_pos_otp_gate/app/utils/efn_otp_flow";
import { efnPointsMethod } from "@efn_pos_loyalty_ledger/app/models/pos_order_patch";

patch(TicketScreen.prototype, {
    async onDoRefund() {
        await super.onDoRefund(...arguments);
        const destination = this.pos.getOrder();
        if (!destination?.isRefund || destination.efnPointsPayments().length) {
            return;
        }
        const refundLines = destination.lines.filter((line) => line.refunded_orderline_id);
        const original = refundLines[0]?.refunded_orderline_id?.order_id;
        if (!original || !this.pos.config.efn_loyalty_redeem_enabled) {
            return;
        }
        if (efnIsOffline(this.pos) || typeof original.id !== "number") {
            if (original.efn_loyalty_points_redeemed) {
                this.dialog.add(AlertDialog, {
                    title: _t("Refund transaksi berpoin"),
                    body: _t(
                        "Order asal memakai Poin EFN. Pengembalian poin memerlukan koneksi ke server; " +
                            "refund ini akan ditandai untuk review supervisor."
                    ),
                });
            }
            return;
        }
        let preview;
        try {
            preview = await this.pos.data.call("pos.config", "efn_loyalty_refund_preview", [
                [this.pos.config.id],
                original.id,
                refundLines.map((line) => [line.refunded_orderline_id.id, Math.abs(line.qty)]),
            ]);
        } catch {
            preview = null;
        }
        if (!preview?.ok) {
            this.pos.notification.add(_t("Gagal menghitung pengembalian Poin EFN."), { type: "danger" });
            return;
        }
        if (!(preview.value_idr > 0)) {
            return;
        }
        if (destination.efnAddPointsRefundPayment(efnPointsMethod(this.pos), preview.value_idr)) {
            this.pos.notification.add(
                _t("Pengembalian Poin EFN %s ditambahkan.", this.env.utils.formatCurrency(preview.value_idr)),
                { type: "info" }
            );
        }
    },
});
