/* global posmodel */
/**
 * Tour UI POS (OWL) untuk OTP gate — Gelombang 12. Dijalankan oleh
 * tests/test_pos_otp_tour.py (tag `efn_tour`, butuh Chrome headless: image target `test`).
 * BFF di-mock di sisi server (patch `efn.bff.client`), jadi tour deterministik:
 *   - kode OTP benar = 123456, kode lain = salah (attempts_left 2).
 * Nomor HP di data uji fiktif.
 */
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as NumberPopup from "@point_of_sale/../tests/generic_helpers/number_popup_util";
import * as OfflineUtil from "@point_of_sale/../tests/generic_helpers/offline_util";
import * as PartnerList from "@point_of_sale/../tests/pos/tours/utils/partner_list_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as PosLoyalty from "@pos_loyalty/../tests/tours/utils/pos_loyalty_util";
import { negate } from "@point_of_sale/../tests/generic_helpers/utils";
import { registry } from "@web/core/registry";

const PRODUCT = "Produk Tour OTP";
const PROGRAM = "Promo Tour OTP";
const MEMBER = "Member Tour OTP";
const MEMBER_MASKED = "62812****890";
const REWARD_LINE = ".order-container .orderline.fst-italic";

function notificationContains(text) {
    return {
        content: `notifikasi: ${text}`,
        trigger: `.o_notification:contains("${text}")`,
    };
}

function otpPopupShown() {
    return [
        {
            content: "popup OTP tampil dengan nomor ter-mask",
            trigger: `.modal .subtitle:contains("${MEMBER_MASKED}")`,
        },
    ];
}

function submitOtp(code) {
    return [
        ...NumberPopup.enterValue(code),
        ...NumberPopup.isShown(code),
        {
            content: `kirim kode OTP ${code}`,
            trigger: `.modal .modal-footer .btn-primary:not([disabled]):contains("Verifikasi")`,
            run: "click",
        },
    ];
}

function waitNetwork(offline) {
    return {
        content: `tunggu POS ${offline ? "offline" : "online"}`,
        trigger: "body",
        run: async () => {
            for (let i = 0; i < 40; i++) {
                // offline_util memicu event "offline" SEBELUM fetch/XHR diblok, jadi ping POS
                // masih sukses; panggil ulang cek konektivitas (perilaku nyata: ping gagal).
                await posmodel.data.checkConnectivity();
                if (Boolean(posmodel.data.network.offline) === offline) {
                    return;
                }
                await new Promise((resolve) => setTimeout(resolve, 100));
            }
            throw new Error(`POS tidak menjadi ${offline ? "offline" : "online"}`);
        },
    };
}

function noRewardLine() {
    return { content: "tidak ada baris reward", trigger: negate(REWARD_LINE) };
}

function rewardLinePresent() {
    return { content: "baris reward ber-OTP terpasang", trigger: REWARD_LINE };
}

function payWithBankAndValidate() {
    return [
        ...ProductScreen.clickPayButton(),
        ...PaymentScreen.clickPaymentMethod("Bank"),
        ...PaymentScreen.clickValidate(),
    ];
}

// (a) Jalur utama + negatif OTP salah: tanpa pelanggan -> diminta pilih -> OTP salah -> benar.
registry.category("web_tour.tours").add("EfnPosOtpGateTour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.addOrderline(PRODUCT, "1"),
            // Auto-apply pos_loyalty diblok patch PosOrder._applyReward (belum OTP).
            noRewardLine(),
            PosLoyalty.claimReward(PROGRAM),
            // Belum ada pelanggan: kasir diminta memilih (daftar pelanggan bawaan terbuka).
            notificationContains("Pilih pelanggan (member)"),
            PartnerList.clickPartner(MEMBER),
            otpPopupShown(),
            submitOtp("111111"),
            notificationContains("Kode OTP salah"),
            noRewardLine(),
            otpPopupShown(),
            submitOtp("123456"),
            notificationContains("OTP terverifikasi"),
            rewardLinePresent(),
            ProductScreen.customerIsSelected(MEMBER),
            payWithBankAndValidate(),
            ReceiptScreen.isShown(),
            Chrome.isSynced(),
            ReceiptScreen.clickNextOrder(),
            Chrome.endTour(),
        ].flat(),
});

// (a) Negatif: tanpa pelanggan, kasir menutup daftar pelanggan -> tidak ada OTP, tidak ada reward.
registry.category("web_tour.tours").add("EfnPosOtpNoCustomerTour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.addOrderline(PRODUCT, "1"),
            PosLoyalty.claimReward(PROGRAM),
            notificationContains("Pilih pelanggan (member)"),
            { content: "daftar pelanggan terbuka", trigger: PartnerList.partnerListTrigger() },
            Dialog.discard(),
            { content: "popup OTP tidak muncul", trigger: negate(".modal .subtitle") },
            noRewardLine(),
            // Order tanpa promo tetap bisa dibayar.
            payWithBankAndValidate(),
            ReceiptScreen.isShown(),
            Chrome.isSynced(),
            Chrome.endTour(),
        ].flat(),
});

// (b) Offline: reward ber-OTP hilang dari tombol Reward; validasi order ber-promo diblok.
registry.category("web_tour.tours").add("EfnPosOtpOfflineTour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.addOrderline(PRODUCT, "1"),
            ProductScreen.clickPartnerButton(),
            ProductScreen.clickCustomer(MEMBER),
            PosLoyalty.isRewardButtonHighlighted(true),
            OfflineUtil.setOfflineMode(),
            waitNetwork(true),
            // ADR-017 opsi a: reward ber-OTP tidak ditawarkan saat offline.
            PosLoyalty.isRewardButtonHighlighted(false),
            OfflineUtil.setOnlineMode(),
            waitNetwork(false),
            PosLoyalty.claimReward(PROGRAM),
            otpPopupShown(),
            submitOtp("123456"),
            notificationContains("OTP terverifikasi"),
            rewardLinePresent(),
            OfflineUtil.setOfflineMode(),
            waitNetwork(true),
            ...ProductScreen.clickPayButton(),
            ...PaymentScreen.clickPaymentMethod("Bank"),
            ...PaymentScreen.clickValidate(),
            { content: "validasi diblok saat offline", trigger: `.modal-body:contains("POS sedang offline")` },
            Dialog.confirm(),
            PaymentScreen.isShown(),
            OfflineUtil.setOnlineMode(),
            waitNetwork(false),
            ...PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            Chrome.isSynced(),
            Chrome.endTour(),
        ].flat(),
});
