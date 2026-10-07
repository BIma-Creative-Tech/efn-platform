/**
 * Poin EFN di model order POS (docs/08 §6.3 G).
 * Server tetap penentu akhir: baris poin dideteksi dari produk "Poin EFN" dan divalidasi
 * terhadap reservasi saat sync (models/pos_order.py). Kode ini hanya UX.
 */
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { patch } from "@web/core/utils/patch";

export function efnPointsProductId(config) {
    return config?.efn_loyalty_points_product || 0;
}

export function efnIsPointsLine(line) {
    const productId = efnPointsProductId(line?.order_id?.config_id);
    return Boolean(productId && line?.product_id?.id === productId);
}

function taxKey(taxes) {
    return taxes
        .map((t) => t.id)
        .sort((a, b) => a - b)
        .join(",");
}

patch(PosOrder.prototype, {
    efnPointsLines() {
        return (this.lines || []).filter((line) => efnIsPointsLine(line));
    },

    /** Order penjualan (bukan refund) yang sedang memakai tukar poin. */
    efnHasActiveRedeem() {
        return !this.isRefund && this.efnPointsLines().length > 0;
    },

    /**
     * Pasang baris "Poin EFN" senilai `valueIdr` (total termasuk pajak = -valueIdr), dibagi
     * proporsional per kelompok pajak atas baris non-poin (ide pos_loyalty
     * `_getDiscountableOnOrder`). `price_type: "automatic"` agar tidak tercampur audit harga manual.
     * @returns {boolean} false bila order tidak punya nilai yang bisa ditukar.
     */
    efnAddPointsLines(valueIdr, points) {
        const product = this.models["product.product"].get(efnPointsProductId(this.config_id));
        if (!product || !(valueIdr > 0)) {
            return false;
        }
        const groups = {};
        for (const line of this.getOrderlines()) {
            if (efnIsPointsLine(line) || !line.getQuantity()) {
                continue;
            }
            const taxes = line.tax_ids.filter((t) => t.amount_type !== "fixed");
            const key = taxKey(taxes);
            if (!groups[key]) {
                groups[key] = { taxes, incl: 0, base: 0 };
            }
            groups[key].incl += line.prices.total_included;
            groups[key].base += line.basePrice;
        }
        const entries = Object.values(groups).filter((g) => g.incl > 0 && g.base > 0);
        const total = entries.reduce((sum, g) => sum + g.incl, 0);
        if (!(total > 0) || valueIdr > total + 0.5) {
            return false;
        }
        entries.sort((a, b) => b.incl - a.incl);
        const created = [];
        for (const group of entries) {
            const share = (valueIdr * group.incl) / total;
            const line = this.models["pos.order.line"].create({
                order_id: this,
                product_id: product,
                qty: 1,
                price_unit: -share * (group.base / group.incl),
                tax_ids: group.taxes.map((tax) => ["link", tax]),
                price_type: "automatic",
            });
            created.push({ line, ratio: group.base / group.incl });
        }
        this.triggerRecomputeAllPrices?.();
        // Pembulatan: selisih ke kelompok terbesar agar total baris poin = -valueIdr.
        const actual = created.reduce((sum, c) => sum + c.line.priceIncl, 0);
        const diff = -valueIdr - actual;
        if (created.length && Math.abs(diff) >= 0.005) {
            const first = created[0];
            first.line.price_unit = first.line.price_unit + diff * first.ratio;
            this.triggerRecomputeAllPrices?.();
        }
        this.efn_loyalty_points_redeemed = points || 0;
        this.efn_loyalty_redeem_value = valueIdr;
        return true;
    },

    efnRemovePointsLines() {
        for (const line of this.efnPointsLines()) {
            line.delete();
        }
        this.efn_loyalty_points_redeemed = 0;
        this.efn_loyalty_redeem_value = 0;
    },

    // pos_loyalty: baris poin bukan barang -> tidak menghasilkan/menghitung poin program Odoo.
    isLineValidForLoyaltyPoints(line) {
        if (efnIsPointsLine(line)) {
            return false;
        }
        return super.isLineValidForLoyaltyPoints(...arguments);
    },

    // pos_loyalty: diskon promo tidak dihitung atas bagian yang dibayar poin.
    _getDiscountableOnOrder(reward) {
        const result = super._getDiscountableOnOrder(...arguments);
        for (const line of this.efnPointsLines()) {
            const key = ["ewallet", "gift_card"].includes(reward.program_id.program_type)
                ? line.tax_ids.map((t) => t.id)
                : line.tax_ids.filter((t) => t.amount_type !== "fixed").map((t) => t.id);
            result.discountable -= line.prices.total_included;
            if (result.discountablePerTax[key] !== undefined) {
                result.discountablePerTax[key] -= line.basePrice;
            }
        }
        return result;
    },
});

patch(PosOrderline.prototype, {
    ignoreLoyaltyPoints() {
        if (efnIsPointsLine(this)) {
            return true;
        }
        return super.ignoreLoyaltyPoints(...arguments);
    },
});
