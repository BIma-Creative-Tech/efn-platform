/**
 * Patch tombol Reward pos_loyalty: reward ber-OTP memicu alur OTP lebih dulu,
 * dan disembunyikan saat POS offline (ADR-017 opsi a).
 */
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { patch } from "@web/core/utils/patch";
import { efnIsOffline, efnRunOtpFlow } from "@efn_pos_otp_gate/app/utils/efn_otp_flow";

patch(ControlButtons.prototype, {
    getPotentialRewards() {
        const rewards = super.getPotentialRewards(...arguments);
        if (!efnIsOffline(this.pos)) {
            return rewards;
        }
        const order = this.pos.getOrder();
        return rewards.filter(({ reward }) => !order?.efnRewardRequiresOtp(reward));
    },

    async _applyReward(reward, coupon_id, potentialQty) {
        const order = this.pos.getOrder();
        if (order && order.efnRewardRequiresOtp(reward) && !order.efn_otp_verified) {
            const verified = await efnRunOtpFlow(
                { pos: this.pos, dialog: this.dialog, notification: this.notification },
                order
            );
            if (!verified) {
                return false;
            }
        }
        return super._applyReward(...arguments);
    },
});
