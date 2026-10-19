/**
 * E-31 (docs/08 §9.3, L-23): layar bayar refund hanya menampilkan metode bayar order ASAL dan mengisi nominal
 * otomatis (alokasi proporsional dari server, `pos.config.efn_refund_origin_allocation`). Retur tanpa struk:
 * hanya metode tunai. Server tetap penentu akhir saat sync (metode di luar asal / melebihi sisa -> ditolak).
 * Offline: daftar metode disaring dari pembayaran order asal yang termuat (bila ada), tanpa isi otomatis.
 *
 * E-41 (docs/04 §12.4, L-51): mode `manager_approval` -> sebelum validasi retur tanpa struk, dialog persetujuan
 * Store Manager (PIN diverifikasi server, `pos.config.efn_return_approval_request`). Offline: DIBLOKIR dengan pesan
 * (persetujuan butuh server). Server tetap penentu akhir saat sync (tanpa persetujuan sah -> ditolak).
 *
 * SEC-147 (19.0.1.4.0): order campuran (refund ber-struk + barang qty < 0 tanpa asal) diblokir sebelum validasi.
 */
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { patch } from "@web/core/utils/patch";
import { onWillStart } from "@odoo/owl";
import { EfnReturnApprovalDialog } from "@efn_pos_refund_origin/app/components/efn_return_approval_dialog/efn_return_approval_dialog";

function refundOrigin(order) {
    return order?.lines?.find((line) => line.refunded_orderline_id)?.refunded_orderline_id?.order_id || null;
}

/** Retur tanpa struk = ada baris qty < 0 dan tidak ada baris yang menunjuk order asal (sama dengan aturan server). */
export function efnIsUnlinkedReturn(order) {
    const lines = order?.lines || [];
    return lines.some((line) => line.qty < 0) && !lines.some((line) => line.refunded_orderline_id);
}

/**
 * SEC-147: order campuran = ada baris yang menunjuk order asal DAN barang qty < 0 tanpa asal. Server menolaknya
 * (semua mode); klien memblokir lebih awal dengan pesan yang sama.
 */
export function efnIsMixedReturn(order) {
    const lines = order?.lines || [];
    return (
        lines.some((line) => line.refunded_orderline_id) &&
        lines.some((line) => line.qty < 0 && !line.refunded_orderline_id)
    );
}

/** Uang keluar order (positif): jumlah pembayaran negatif yang bukan kembalian. */
export function efnOutgoingAmount(order) {
    const total = (order?.payment_ids || [])
        .filter((p) => p.amount < 0 && !p.is_change)
        .reduce((sum, p) => sum - p.amount, 0);
    return Math.round(total * 100) / 100;
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

    async validateOrder(isForceValidate) {
        const order = this.currentOrder;
        if (order && efnIsMixedReturn(order)) {
            this.efnAlert(
                _t(
                    "Refund dari struk dan retur tanpa struk tidak boleh digabung dalam satu order. Selesaikan " +
                        "refund dari struk dulu, lalu buat retur tanpa struk sebagai order terpisah."
                )
            );
            return;
        }
        if (
            order &&
            this.pos.config.efn_unlinked_return_mode === "manager_approval" &&
            efnIsUnlinkedReturn(order)
        ) {
            const approved = await this.efnEnsureReturnApproval(order);
            if (!approved) {
                return;
            }
        }
        return await super.validateOrder(...arguments);
    },

    efnAlert(body) {
        this.dialog.add(AlertDialog, { title: _t("Retur tanpa struk"), body });
    },

    async efnEnsureReturnApproval(order) {
        if (this.pos.data.network?.offline) {
            this.efnAlert(
                _t(
                    "Retur tanpa struk memerlukan persetujuan Store Manager yang diverifikasi server. POS sedang " +
                        "offline: tunggu koneksi pulih atau batalkan retur ini."
                )
            );
            return false;
        }
        const amount = efnOutgoingAmount(order);
        // Properti klien saja (bukan field): persetujuan yang sudah didapat untuk order ini di layar ini.
        const previous = order.efnReturnApproval;
        if (previous && previous.uuid === order.uuid && amount <= previous.maxAmount && Date.now() < previous.until) {
            return true;
        }
        let approvers;
        try {
            approvers = await this.pos.data.call("pos.config", "efn_return_approval_approvers", [[this.pos.config.id]]);
        } catch {
            this.efnAlert(_t("Server tidak dapat dihubungi. Retur tanpa struk tidak dapat disetujui sekarang."));
            return false;
        }
        if (!approvers?.ok) {
            this.efnAlert(approvers?.message || _t("Persetujuan retur tanpa struk tidak tersedia."));
            return false;
        }
        if (!approvers.approvers.length) {
            this.efnAlert(
                _t(
                    "Tidak ada Store Manager lain dengan PIN persetujuan retur untuk POS ini. Minta Store Manager " +
                        "mengatur PIN di Preferensi Odoo."
                )
            );
            return false;
        }
        let grant = null;
        const confirmed = await makeAwaitable(this.dialog, EfnReturnApprovalDialog, {
            title: _t("Persetujuan Store Manager"),
            amountLabel: this.env.utils?.formatCurrency?.(amount) ?? String(amount),
            approvers: approvers.approvers,
            submit: async ({ approverId, pin, reason }) => {
                if (this.pos.data.network?.offline) {
                    return { ok: false, message: _t("POS offline. Persetujuan tidak dapat diverifikasi.") };
                }
                const result = await this.pos.data.call("pos.config", "efn_return_approval_request", [
                    [this.pos.config.id],
                    order.uuid,
                    approverId,
                    pin,
                    amount,
                    reason,
                ]);
                if (result?.ok) {
                    grant = result;
                }
                return result;
            },
        });
        if (confirmed !== true || !grant) {
            return false;
        }
        order.efnReturnApproval = {
            uuid: order.uuid,
            maxAmount: grant.max_amount,
            // Sedikit lebih pendek dari TTL server agar klien tidak memakai persetujuan yang hampir kedaluwarsa.
            until: Date.now() + Math.max((grant.expires_in || 0) - 30, 0) * 1000,
        };
        return true;
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
