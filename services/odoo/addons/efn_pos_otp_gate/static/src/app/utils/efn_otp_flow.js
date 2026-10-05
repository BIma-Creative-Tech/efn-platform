/**
 * Alur OTP kasir (SEC-07): pilih pelanggan order -> request (server Odoo membaca nomor HP
 * dari res.partner lalu memanggil BFF) -> input kode -> verify.
 * Kasir TIDAK mengetik nomor HP; OTP dikirim ke nomor yang tercatat pada pelanggan order.
 * Browser TIDAK pernah menerima challenge_id maupun discount_token; keduanya disimpan
 * server-side (efn.pos.otp.challenge). Browser hanya menerima ok + nomor ter-mask.
 */
import { _t } from "@web/core/l10n/translation";
import { NumberPopup } from "@point_of_sale/app/components/popups/number_popup/number_popup";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";

const MAX_CODE_ATTEMPTS = 3;

export function efnIsOffline(pos) {
    return Boolean(pos?.data?.network?.offline);
}

async function callServer(pos, method, args) {
    return await pos.data.call("pos.config", method, [[pos.config.id], ...args]);
}

/**
 * @returns {Promise<boolean>} true bila OTP terverifikasi dan order sudah ditandai.
 */
export async function efnRunOtpFlow({ pos, dialog, notification }, order) {
    if (efnIsOffline(pos)) {
        notification.add(_t("Promo ber-OTP tidak tersedia saat POS offline."), { type: "warning" });
        return false;
    }
    let partner = order.getPartner();
    if (!partner) {
        notification.add(_t("Pilih pelanggan (member) terlebih dahulu untuk promo ber-OTP."), {
            type: "info",
        });
        // Buka daftar pelanggan bawaan POS; partner terpilih dipasang ke order.
        await pos.selectPartner(order);
        partner = order.getPartner();
        if (!partner) {
            return false;
        }
    }
    if (typeof partner.id !== "number") {
        notification.add(_t("Data pelanggan belum tersimpan di server. Coba lagi."), {
            type: "warning",
        });
        return false;
    }

    let requestResult;
    try {
        requestResult = await callServer(pos, "efn_otp_request", [
            order.uuid,
            order.priceIncl,
            partner.id,
        ]);
    } catch {
        notification.add(_t("Layanan OTP tidak dapat dihubungi. Lanjutkan tanpa promo."), {
            type: "danger",
        });
        return false;
    }
    if (!requestResult?.ok) {
        notification.add(requestResult?.message || _t("Gagal meminta OTP."), { type: "danger" });
        return false;
    }

    for (let attempt = 0; attempt < MAX_CODE_ATTEMPTS; attempt++) {
        const code = await makeAwaitable(dialog, NumberPopup, {
            title: _t("Masukkan kode OTP"),
            subtitle: _t("Kode dikirim via WhatsApp ke %s", requestResult.member_masked),
            placeholder: "______",
            confirmButtonLabel: _t("Verifikasi"),
            isValid: (buffer) => /^\d{6}$/.test(String(buffer || "")),
        });
        if (!code) {
            return false;
        }
        if (efnIsOffline(pos)) {
            notification.add(_t("POS offline. Promo ber-OTP dibatalkan."), { type: "warning" });
            return false;
        }
        let verifyResult;
        try {
            verifyResult = await callServer(pos, "efn_otp_verify", [order.uuid, String(code)]);
        } catch {
            notification.add(_t("Layanan OTP tidak dapat dihubungi. Lanjutkan tanpa promo."), {
                type: "danger",
            });
            return false;
        }
        if (verifyResult?.ok) {
            // Penanda UI saja; server memverifikasi ulang dari data challenge miliknya.
            order.efn_otp_verified = true;
            order.efn_otp_member_masked = verifyResult.member_masked || false;
            // Properti klien saja: dipakai untuk mereset penanda bila pelanggan diganti.
            order.efnOtpPartnerId = partner.id;
            notification.add(_t("OTP terverifikasi."), { type: "success" });
            return true;
        }
        const retry = verifyResult?.reason === "invalid" && verifyResult?.attempts_left > 0;
        notification.add(verifyResult?.message || _t("Verifikasi OTP gagal."), {
            type: retry ? "warning" : "danger",
        });
        if (!retry) {
            return false;
        }
    }
    return false;
}
