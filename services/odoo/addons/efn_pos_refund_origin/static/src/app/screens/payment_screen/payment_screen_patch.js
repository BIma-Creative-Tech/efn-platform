/**
 * E-31 (docs/08 §9.3, L-23): layar bayar refund hanya menampilkan metode bayar order ASAL dan mengisi nominal
 * otomatis (alokasi proporsional dari server, `pos.config.efn_refund_origin_allocation`). Retur tanpa struk:
 * hanya metode tunai. Server tetap penentu akhir saat sync (metode di luar asal / melebihi sisa -> ditolak).
 * Offline: daftar metode disaring dari pembayaran order asal yang termuat (bila ada), tanpa isi otomatis.
 */
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { patch } from "@web/core/utils/patch";
import { onWillStart } from "@odoo/owl";

function refundOrigin(order) {
    return order?.lines?.find((line) => line.refunded_orderline_id)?.refunded_orderline_id?.order_id || null;
}

patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.efnRefundAllocation = null;
        onWillStart(async () => {
            await this.efnLoadRefundOrigin();
        });
    },

    async efnLoadRefundOrigin() {
        const order = this.currentOrder;
        if (!order) {
            return;
        }
        const origin = refundOrigin(order);
        if (!origin) {
            if (order.priceIncl < 0 && order.lines.some((line) => line.qty < 0)) {
                // Retur tanpa struk: tunai saja (L-23, Q-F18-5).
                this.payment_methods_from_config = this.payment_methods_from_config.filter((m) => m.is_cash_count);
            }
            return;
        }
        let result = null;
        if (typeof origin.id === "number" && !this.pos.data.network?.offline) {
            try {
                result = await this.pos.data.call("pos.config", "efn_refund_origin_allocation", [
                    [this.pos.config.id],
                    origin.id,
                    Math.abs(order.priceIncl),
                ]);
            } catch {
                result = null;
            }
        }
        let allowed = null;
        if (result?.ok) {
            this.efnRefundAllocation = result.allocation;
            allowed = new Set(result.allocation.map((row) => row.payment_method_id));
        } else if (origin.payment_ids?.length) {
            allowed = new Set(
                origin.payment_ids.filter((p) => p.amount > 0).map((p) => p.payment_method_id?.id)
            );
        }
        if (allowed) {
            this.payment_methods_from_config = this.payment_methods_from_config.filter((m) => allowed.has(m.id));
        }
    },

    onMounted() {
        super.onMounted(...arguments);
        this.efnApplyRefundAllocation();
    },

    efnApplyRefundAllocation() {
        const order = this.currentOrder;
        const allocation = this.efnRefundAllocation;
        if (!order || !allocation?.length) {
            return;
        }
        // Metode yang sudah punya baris (mis. pengembalian Poin EFN E-30, atau baris otomatis core bila hanya
        // satu metode) tidak ditimpa.
        const used = new Set(order.payment_ids.map((line) => line.payment_method_id?.id));
        for (const row of allocation) {
            if (!(row.amount > 0) || used.has(row.payment_method_id)) {
                continue;
            }
            const method = this.pos.models["pos.payment.method"].get(row.payment_method_id);
            if (!method) {
                continue;
            }
            const result = order.addPaymentline(method);
            if (result?.status) {
                result.data.setAmount(-row.amount);
            }
        }
        this.numberBuffer.reset();
    },
});
