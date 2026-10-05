/**
 * Cegah validasi order yang membawa reward ber-OTP bila belum terverifikasi atau POS offline
 * (token harus di-consume server saat sync; offline berarti tidak bisa).
 */
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { efnIsOffline } from "@efn_pos_otp_gate/app/utils/efn_otp_flow";

patch(PaymentScreen.prototype, {
    async validateOrder(isForceValidate) {
        const order = this.currentOrder;
        if (order && order.efnGatedRewardLines().length) {
            let body = null;
            if (!order.efn_otp_verified) {
                body = _t("Promo pada order ini memerlukan verifikasi OTP. Hapus promo atau verifikasi OTP.");
            } else if (efnIsOffline(this.pos) && this.pos.config.efn_promo_disabled_when_offline) {
                body = _t("POS sedang offline. Hapus promo ber-OTP untuk melanjutkan transaksi.");
            }
            if (body) {
                this.env.services.dialog.add(AlertDialog, { title: _t("Promo ber-OTP"), body });
                return;
            }
        }
        return super.validateOrder(...arguments);
    },
});
