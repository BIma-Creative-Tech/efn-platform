/**
 * Poin EFN di model order POS (docs/08 §9.2 E-30, kontrak docs/04 §9.9 K-18-1).
 * Redeem = BARIS PEMBAYARAN metode "Poin EFN" (`efn_is_loyalty_points`) senilai `value_idr`; harga & pajak
 * tidak berubah. Server tetap penentu akhir: pembayaran poin dideteksi dari metode dan divalidasi terhadap
 * reservasi saat sync (models/pos_order.py, toleransi 0). Kode ini hanya UX.
 */
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";

export function efnPointsMethod(pos) {
    const id = pos?.config?.efn_loyalty_points_method || 0;
    return id ? pos.models["pos.payment.method"].get(id) : null;
}

export function efnIsPointsPayment(line) {
    return Boolean(line?.payment_method_id?.efn_is_loyalty_points);
}

patch(PosOrder.prototype, {
    efnPointsPayments() {
        return (this.payment_ids || []).filter((line) => efnIsPointsPayment(line));
    },

    /** Order penjualan (bukan refund) yang sedang memakai tukar poin. */
    efnHasActiveRedeem() {
        return !this.isRefund && this.efnPointsPayments().length > 0;
    },

    /**
     * Tambah baris pembayaran Poin EFN senilai `valueIdr` (terkunci di UI; nominal dari reservasi BFF).
     * @returns {boolean} false bila metode tidak ada atau nilai melebihi sisa tagihan.
     */
    efnAddPointsPayment(method, valueIdr, points) {
        if (!method || !(valueIdr > 0)) {
            return false;
        }
        const due = this.currency.round(this.priceIncl - this.efnPointsPayments().reduce((s, l) => s + l.amount, 0));
        if (valueIdr > due + 0.0001) {
            return false;
        }
        const result = this.addPaymentline(method);
        if (!result?.status) {
            return false;
        }
        result.data.setAmount(valueIdr);
        // Baris poin tidak boleh menjadi baris terpilih (read-only terhadap numpad).
        this.selectPaymentline(undefined);
        this.efn_loyalty_points_redeemed = points || 0;
        this.efn_loyalty_redeem_value = valueIdr;
        return true;
    },

    /** Refund berpoin: pembayaran Poin EFN negatif (alokasi proporsional dihitung server). */
    efnAddPointsRefundPayment(method, valueIdr) {
        if (!method || !(valueIdr > 0) || this.efnPointsPayments().length) {
            return false;
        }
        const result = this.addPaymentline(method);
        if (!result?.status) {
            return false;
        }
        result.data.setAmount(-valueIdr);
        this.selectPaymentline(undefined);
        return true;
    },

    efnRemovePointsPayments() {
        for (const line of this.efnPointsPayments()) {
            this.removePaymentline(line);
        }
        this.efn_loyalty_points_redeemed = 0;
        this.efn_loyalty_redeem_value = 0;
    },
});
