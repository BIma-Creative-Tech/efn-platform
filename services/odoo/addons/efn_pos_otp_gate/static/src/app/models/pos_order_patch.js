/**
 * EFN POS OTP gate — guard di level model order (berlaku untuk SEMUA jalur penerapan
 * reward: tombol Reward, auto-apply pos_loyalty, kode promo).
 * Server tetap menjadi penentu akhir (lihat models/pos_order.py); ini hanya UX.
 */
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

patch(PosOrder.prototype, {
    efnRewardRequiresOtp(reward) {
        const config = this.config_id;
        return Boolean(config?.efn_otp_required_for_promo && reward?.program_id?.efn_requires_otp);
    },

    efnGatedRewardLines() {
        return (this.lines || []).filter(
            (line) => line.is_reward_line && line.reward_id && this.efnRewardRequiresOtp(line.reward_id)
        );
    },

    setPartner(partner) {
        const result = super.setPartner(...arguments);
        // SEC-07: OTP terikat ke pelanggan. Mengganti pelanggan setelah verifikasi membuat
        // penanda UI tidak berlaku (server/BFF tetap memutuskan: member_mismatch -> review).
        if (this.efn_otp_verified && this.efnOtpPartnerId && partner?.id !== this.efnOtpPartnerId) {
            this.efn_otp_verified = false;
            this.efn_otp_member_masked = false;
            this.efnOtpPartnerId = false;
        }
        return result;
    },

    _applyReward(reward, coupon_id, args) {
        if (this.efnRewardRequiresOtp(reward) && !this.efn_otp_verified) {
            // Mengembalikan string = error (konvensi pos_loyalty), reward tidak diterapkan.
            return _t("Promo ini memerlukan verifikasi OTP pelanggan.");
        }
        return super._applyReward(...arguments);
    },
});
