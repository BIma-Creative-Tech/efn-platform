/**
 * Tombol "Cek Poin" & "Tukar Poin EFN" di layar pembayaran (docs/08 §9.2 E-30).
 * Alur tukar: saldo -> jumlah poin -> OTP `pos_redeem` -> reserve (server Odoo -> BFF) -> baris PEMBAYARAN
 * metode "Poin EFN" senilai `value_idr` (terkunci: tidak bisa diubah nominalnya; menghapus = batal tukar poin).
 * Metode Poin EFN tidak tampil sebagai pilihan manual. Offline: tombol disembunyikan dan validasi order
 * berpoin diblokir (ADR-017 opsi a).
 */
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { NumberPopup } from "@point_of_sale/app/components/popups/number_popup/number_popup";
import { ask, makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { efnIsOffline, efnRunOtpFlow } from "@efn_pos_otp_gate/app/utils/efn_otp_flow";
import { efnIsPointsPayment, efnPointsMethod } from "@efn_pos_loyalty_ledger/app/models/pos_order_patch";

async function callConfig(pos, method, args) {
    return await pos.data.call("pos.config", method, [[pos.config.id], ...args]);
}

patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        // Metode Poin EFN hanya lewat tombol tukar (OTP + reservasi), tidak dipilih manual.
        this.payment_methods_from_config = this.payment_methods_from_config.filter(
            (method) => !method.efn_is_loyalty_points
        );
    },

    get efnLoyaltyOnline() {
        return !efnIsOffline(this.pos);
    },

    get efnShowCheckPoints() {
        const config = this.pos.config;
        const order = this.currentOrder;
        return Boolean(
            (config.efn_loyalty_earn_enabled || config.efn_loyalty_redeem_enabled) &&
                this.efnLoyaltyOnline &&
                order?.getPartner() &&
                !order.isRefund
        );
    },

    get efnShowRedeem() {
        const order = this.currentOrder;
        return Boolean(
            this.pos.config.efn_loyalty_redeem_enabled &&
                efnPointsMethod(this.pos) &&
                this.efnLoyaltyOnline &&
                order?.getPartner() &&
                !order.isRefund &&
                !order.efnHasActiveRedeem()
        );
    },

    updateSelectedPaymentline(amount = false) {
        if (efnIsPointsPayment(this.selectedPaymentLine)) {
            // Nominal Poin EFN terkunci (= reservasi BFF; server menolak selisih).
            return;
        }
        return super.updateSelectedPaymentline(...arguments);
    },

    async deletePaymentLine(uuid) {
        const line = this.paymentLines.find((l) => l.uuid === uuid);
        if (!efnIsPointsPayment(line)) {
            return super.deletePaymentLine(...arguments);
        }
        const order = this.currentOrder;
        if (order.isRefund) {
            this.notification.add(
                _t("Pengembalian Poin EFN dihitung otomatis dan tidak bisa dihapus."),
                { type: "warning" }
            );
            return;
        }
        if (!(await this.pos.efnConfirmCancelRedeem())) {
            return;
        }
        await this.pos.efnCancelRedeem(order);
        this.numberBuffer.reset();
    },

    async efnFetchBalance(partner) {
        let result;
        try {
            result = await callConfig(this.pos, "efn_loyalty_balance", [partner.id]);
        } catch {
            this.notification.add(_t("Layanan poin tidak dapat dihubungi."), { type: "danger" });
            return null;
        }
        if (!result?.ok) {
            this.notification.add(result?.message || _t("Gagal membaca saldo poin."), { type: "danger" });
            return null;
        }
        return result;
    },

    async efnCheckPoints() {
        const partner = this.currentOrder?.getPartner();
        if (!partner || typeof partner.id !== "number") {
            return;
        }
        const result = await this.efnFetchBalance(partner);
        if (result) {
            this.notification.add(
                _t("Saldo Poin EFN %(name)s: %(points)s poin", {
                    name: partner.name,
                    points: result.points_balance,
                }),
                { type: "info" }
            );
        }
    },

    async efnRedeemPoints() {
        const order = this.currentOrder;
        const partner = order?.getPartner();
        const method = efnPointsMethod(this.pos);
        if (!order || !partner || typeof partner.id !== "number") {
            this.notification.add(_t("Pilih pelanggan (member) terlebih dahulu."), { type: "info" });
            return;
        }
        if (!method) {
            return;
        }
        if (efnIsOffline(this.pos)) {
            this.notification.add(_t("Tukar poin tidak tersedia saat POS offline."), { type: "warning" });
            return;
        }
        // K-18-2: total order TERMASUK pajak; nominal yang sama dipakai saat OTP & reserve.
        const amount = order.currency.round(order.priceIncl);
        const balance = await this.efnFetchBalance(partner);
        if (!balance) {
            return;
        }
        const pointValue = balance.point_value_idr || 0;
        const minPoints = balance.redeem_min_points || 1;
        const maxPct = balance.redeem_max_pct_of_order || 100;
        const maxByOrder = pointValue ? Math.floor((amount * maxPct) / 100 / pointValue) : Infinity;
        const maxPoints = Math.min(balance.points_balance, maxByOrder);
        if (!(maxPoints >= minPoints)) {
            this.notification.add(
                _t("Poin tidak dapat ditukar (saldo %s poin).", balance.points_balance),
                { type: "warning" }
            );
            return;
        }
        const raw = await makeAwaitable(this.dialog, NumberPopup, {
            title: _t("Tukar Poin EFN"),
            subtitle: _t("Saldo %(balance)s poin · maks %(max)s poin", {
                balance: balance.points_balance,
                max: maxPoints,
            }),
            feedback: (buffer) => {
                const points = parseInt(buffer || "0", 10) || 0;
                return pointValue ? this.env.utils.formatCurrency(points * pointValue) : "";
            },
            confirmButtonLabel: _t("Lanjut OTP"),
            isValid: (buffer) => {
                const text = String(buffer || "");
                const points = parseInt(text, 10);
                return /^\d+$/.test(text) && points >= minPoints && points <= maxPoints;
            },
        });
        const points = parseInt(raw || "0", 10);
        if (!points) {
            return;
        }
        const verified = await efnRunOtpFlow(
            { pos: this.pos, dialog: this.dialog, notification: this.notification },
            order,
            { purpose: "pos_redeem", amount }
        );
        if (!verified) {
            return;
        }
        let reserve;
        try {
            reserve = await callConfig(this.pos, "efn_loyalty_redeem_reserve", [
                order.uuid,
                partner.id,
                points,
                amount,
            ]);
        } catch {
            this.notification.add(_t("Layanan poin tidak dapat dihubungi."), { type: "danger" });
            return;
        }
        if (!reserve?.ok) {
            this.notification.add(reserve?.message || _t("Penukaran poin ditolak."), { type: "danger" });
            return;
        }
        if (
            order.getPartner()?.id !== partner.id ||
            !order.efnAddPointsPayment(method, reserve.value_idr, reserve.points)
        ) {
            // Order berubah selama proses: lepaskan reservasi agar poin pelanggan tidak tertahan.
            await callConfig(this.pos, "efn_loyalty_redeem_release", [order.uuid]).catch(() => {});
            this.notification.add(_t("Order berubah; penukaran poin dibatalkan."), { type: "warning" });
            return;
        }
        this.numberBuffer.reset();
        this.notification.add(
            _t("%(points)s poin ditukar (%(value)s).", {
                points: reserve.points,
                value: this.env.utils.formatCurrency(reserve.value_idr),
            }),
            { type: "success" }
        );
    },

    async validateOrder(isForceValidate) {
        const order = this.currentOrder;
        if (order?.efnPointsPayments().length && efnIsOffline(this.pos)) {
            const remove = await ask(this.dialog, {
                title: _t("Tukar Poin EFN"),
                body: _t(
                    "POS sedang offline. Penukaran Poin EFN memerlukan koneksi ke server. " +
                        "Tunggu online, atau hapus poin lalu lanjutkan pembayaran."
                ),
                confirmLabel: _t("Hapus poin"),
                cancelLabel: _t("Tunggu online"),
            });
            if (remove && !order.isRefund) {
                // Reservasi dilepas server saat order tersinkron tanpa pembayaran poin.
                order.efnRemovePointsPayments();
            }
            return;
        }
        return super.validateOrder(...arguments);
    },
});
