/**
 * Alur OTP kasir (SEC-07): pilih pelanggan order -> request (server Odoo membaca nomor HP
 * dari res.partner lalu memanggil BFF) -> input kode -> verify.
 * Kasir TIDAK mengetik nomor HP; OTP dikirim ke nomor yang tercatat pada pelanggan order.
 * Browser TIDAK pernah menerima challenge_id maupun discount_token; keduanya disimpan
 * server-side (efn.pos.otp.challenge). Browser hanya menerima ok + nomor ter-mask.
 *
 * E-23: `purpose` = "pos_promo" (default, gate promo) | "pos_redeem" (tukar Poin EFN,
 * modul efn_pos_loyalty_ledger) | "pos_enroll" (E-34: kasir mendaftarkan member; wajib centang pernyataan usia
 * 17+ -> `age_confirmed: true`, Odoo tidak menyimpan usia). Selain `pos_promo` penanda promo order TIDAK disentuh.
 */
import { _t } from "@web/core/l10n/translation";
import { NumberPopup } from "@point_of_sale/app/components/popups/number_popup/number_popup";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { EfnAgeConfirmDialog } from "@efn_pos_otp_gate/app/components/efn_age_confirm_dialog/efn_age_confirm_dialog";

const MAX_CODE_ATTEMPTS = 3;

export function efnIsOffline(pos) {
    return Boolean(pos?.data?.network?.offline);
}

async function callServer(pos, method, args) {
    return await pos.data.call("pos.config", method, [[pos.config.id], ...args]);
}

/**
 * @param {Object} [options]
 * @param {"pos_promo"|"pos_redeem"|"pos_enroll"} [options.purpose="pos_promo"]
 * @param {boolean} [options.ageConfirmed] wajib true untuk "pos_enroll" (server menolak selain true)
 * @returns {Promise<boolean>} true bila OTP terverifikasi (untuk pos_promo order sudah ditandai).
 */
export async function efnRunOtpFlow({ pos, dialog, notification }, order, options = {}) {
    const purpose = ["pos_redeem", "pos_enroll"].includes(options.purpose) ? options.purpose : "pos_promo";
    const isPromo = purpose === "pos_promo";
    const ageConfirmed = purpose === "pos_enroll" && options.ageConfirmed === true;
    const featureLabel = {
        pos_promo: _t("promo"),
        pos_redeem: _t("tukar poin"),
        pos_enroll: _t("pendaftaran member"),
    }[purpose];
    if (efnIsOffline(pos)) {
        notification.add(_t("Fitur %s tidak tersedia saat POS offline.", featureLabel), { type: "warning" });
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
            options.amount ?? order.priceIncl,
            partner.id,
            purpose,
            ageConfirmed,
        ]);
    } catch {
        notification.add(_t("Layanan OTP tidak dapat dihubungi. Lanjutkan tanpa %s.", featureLabel), {
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
            notification.add(_t("POS offline. Verifikasi OTP %s dibatalkan.", featureLabel), {
                type: "warning",
            });
            return false;
        }
        let verifyResult;
        try {
            verifyResult = await callServer(pos, "efn_otp_verify", [
                order.uuid,
                String(code),
                purpose,
                ageConfirmed,
            ]);
        } catch {
            notification.add(_t("Layanan OTP tidak dapat dihubungi. Lanjutkan tanpa %s.", featureLabel), {
                type: "danger",
            });
            return false;
        }
        if (verifyResult?.ok && !isPromo) {
            notification.add(
                purpose === "pos_enroll" ? _t("Member terdaftar (OTP terverifikasi).") : _t("OTP terverifikasi."),
                { type: "success" }
            );
            return true;
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

/**
 * E-34 (K-18-8): kasir mendaftarkan pelanggan order sebagai member. Centang wajib pernyataan usia 17+ (teks
 * menunggu Legal) -> OTP `pos_enroll` dengan `age_confirmed: true`. Offline: tidak tersedia.
 * @returns {Promise<boolean>}
 */
export async function efnRunEnrollFlow({ pos, dialog, notification }, order) {
    if (efnIsOffline(pos)) {
        notification.add(_t("Pendaftaran member tidak tersedia saat POS offline."), { type: "warning" });
        return false;
    }
    if (!order.getPartner()) {
        notification.add(_t("Pilih pelanggan yang akan didaftarkan sebagai member."), { type: "info" });
        await pos.selectPartner(order);
        if (!order.getPartner()) {
            return false;
        }
    }
    const confirmed = await makeAwaitable(dialog, EfnAgeConfirmDialog, {
        title: _t("Daftarkan member"),
        body: _t(
            "Tanyakan kepada pelanggan dan centang bila pelanggan menyatakan berusia minimal 17 tahun. " +
                "Usia tidak disimpan di POS."
        ),
    });
    if (confirmed !== true) {
        return false;
    }
    return await efnRunOtpFlow({ pos, dialog, notification }, order, {
        purpose: "pos_enroll",
        ageConfirmed: true,
        amount: 0,
    });
}
