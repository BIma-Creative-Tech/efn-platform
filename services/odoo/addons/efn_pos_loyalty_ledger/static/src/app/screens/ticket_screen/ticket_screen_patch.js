/**
 * Refund order berpoin (docs/08 §6.3 H): baris "Poin EFN" asal tidak bisa dipilih manual; layar
 * refund menambahkan baris penyesuaian = round(rasio x nilai poin asal) (nilai positif, sehingga
 * uang yang dikembalikan hanya porsi yang dibayar uang). Rasio & nilai dihitung server
 * (`efn_loyalty_refund_preview`); server memvalidasi lagi saat sync (selisih -> review).
 */
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { efnIsOffline } from "@efn_pos_otp_gate/app/utils/efn_otp_flow";
import { efnIsPointsLine, efnPointsProductId } from "@efn_pos_loyalty_ledger/app/models/pos_order_patch";

patch(TicketScreen.prototype, {
    _getRefundableDetails(partner, order) {
        return super._getRefundableDetails(...arguments).filter((detail) => !efnIsPointsLine(detail.line));
    },

    async onDoRefund() {
        await super.onDoRefund(...arguments);
        const destination = this.pos.getOrder();
        if (!destination?.isRefund) {
            return;
        }
        const refundLines = destination.lines.filter((line) => line.refunded_orderline_id && !efnIsPointsLine(line));
        const original = refundLines[0]?.refunded_orderline_id?.order_id;
        if (!original || destination.efnPointsLines().length) {
            return;
        }
        const productId = efnPointsProductId(this.pos.config);
        const originalPoints = original.lines.filter((line) => productId && line.product_id?.id === productId);
        if (!originalPoints.length) {
            return;
        }
        if (efnIsOffline(this.pos) || typeof original.id !== "number") {
            this.dialog.add(AlertDialog, {
                title: _t("Refund transaksi berpoin"),
                body: _t(
                    "Order asal memakai Poin EFN. Penyesuaian poin memerlukan koneksi ke server; " +
                        "refund ini akan ditandai untuk review supervisor."
                ),
            });
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
        if (!preview?.ok || !(preview.ratio > 0)) {
            if (!preview?.ok) {
                this.pos.notification.add(_t("Gagal menghitung penyesuaian Poin EFN."), { type: "danger" });
            }
            return;
        }
        const product = this.pos.models["product.product"].get(productId);
        for (const pointsLine of originalPoints) {
            this.pos.models["pos.order.line"].create({
                order_id: destination,
                product_id: product,
                qty: 1,
                price_unit: -pointsLine.price_unit * preview.ratio,
                tax_ids: pointsLine.tax_ids.map((tax) => ["link", tax]),
                price_type: "automatic",
            });
        }
        destination.triggerRecomputeAllPrices?.();
        this.pos.notification.add(
            _t("Penyesuaian Poin EFN %s ditambahkan.", this.env.utils.formatCurrency(preview.value_idr)),
            { type: "info" }
        );
    },
});
