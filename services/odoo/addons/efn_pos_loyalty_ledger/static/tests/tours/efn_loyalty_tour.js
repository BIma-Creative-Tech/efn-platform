/* global posmodel */
/**
 * Tour UI POS Poin EFN (E-22). Dijalankan tests/test_loyalty_tour.py (tag `efn_tour`, Chrome
 * headless). BFF di-mock server-side: saldo 500 poin, 1 poin = Rp100, kode OTP benar = 123456.
 * Nomor HP data uji fiktif.
 */
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as NumberPopup from "@point_of_sale/../tests/generic_helpers/number_popup_util";
import * as OfflineUtil from "@point_of_sale/../tests/generic_helpers/offline_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import { negate } from "@point_of_sale/../tests/generic_helpers/utils";
import { registry } from "@web/core/registry";

const PRODUCT = "Produk Tour Poin";
const MEMBER = "Member Tour Poin";
const MEMBER_MASKED = "62812****890";
const REDEEM_BUTTON = ".payment-screen .efn-redeem-points";

function notificationContains(text) {
    return { content: `notifikasi: ${text}`, trigger: `.o_notification:contains("${text}")` };
}

function waitNetwork(offline) {
    return {
        content: `tunggu POS ${offline ? "offline" : "online"}`,
        trigger: "body",
        run: async () => {
            for (let i = 0; i < 40; i++) {
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

function pointsLines(expected) {
    return {
        content: `order punya ${expected} baris Poin EFN`,
        trigger: "body",
        run: () => {
            const lines = posmodel.getOrder().efnPointsLines();
            if (lines.length !== expected) {
                throw new Error(`baris poin ${lines.length} != ${expected}`);
            }
            if (expected) {
                const total = lines.reduce((sum, line) => sum + line.priceIncl, 0);
                if (Math.abs(total + 10000) > 0.01) {
                    throw new Error(`total baris poin ${total} != -10000`);
                }
                if (lines.some((line) => line.price_type !== "automatic")) {
                    throw new Error("price_type baris poin harus automatic");
                }
            }
        },
    };
}

function startWithMemberOnPayment() {
    return [
        Chrome.startPoS(),
        Dialog.confirm("Open Register"),
        ProductScreen.addOrderline(PRODUCT, "1"),
        ProductScreen.clickPartnerButton(),
        ProductScreen.clickCustomer(MEMBER),
        ProductScreen.clickPayButton(),
        PaymentScreen.isShown(),
    ].flat();
}

function submitOtp(code) {
    return [
        { content: "popup OTP dengan nomor ter-mask", trigger: `.modal .subtitle:contains("${MEMBER_MASKED}")` },
        ...NumberPopup.enterValue(code),
        ...NumberPopup.isShown(code),
        {
            content: `kirim kode OTP ${code}`,
            trigger: `.modal .modal-footer .btn-primary:not([disabled]):contains("Verifikasi")`,
            run: "click",
        },
    ];
}

function redeemPoints(points) {
    return [
        { content: "klik Tukar Poin EFN", trigger: REDEEM_BUTTON, run: "click" },
        { content: "popup jumlah poin", trigger: `.modal .modal-header:contains("Tukar Poin EFN")` },
        ...NumberPopup.enterValue(points),
        ...NumberPopup.isShown(points),
        {
            content: "lanjut OTP",
            trigger: `.modal .modal-footer .btn-primary:not([disabled]):contains("Lanjut OTP")`,
            run: "click",
        },
    ];
}

function payBankAndSync() {
    return [
        PaymentScreen.clickPaymentMethod("Bank"),
        PaymentScreen.clickValidate(),
        ReceiptScreen.isShown(),
        { content: "struk mencetak poin ditukar", trigger: `.pos-receipt .efn-loyalty-receipt:contains("100 poin")` },
        Chrome.isSynced(),
    ].flat();
}

// (1) Sukses: Cek Poin -> Tukar Poin -> OTP benar -> baris poin -> bayar -> sync.
registry.category("web_tour.tours").add("EfnPosLoyaltyRedeemTour", {
    steps: () =>
        [
            startWithMemberOnPayment(),
            { content: "klik Cek Poin", trigger: ".payment-screen .efn-check-points", run: "click" },
            notificationContains("500 poin"),
            redeemPoints("100"),
            submitOtp("123456"),
            notificationContains("100 poin ditukar"),
            pointsLines(1),
            { content: "tombol tukar hilang setelah redeem", trigger: negate(REDEEM_BUTTON) },
            payBankAndSync(),
            Chrome.endTour(),
        ].flat(),
});

// (2) OTP salah: tiga kali salah -> tidak ada reservasi, tidak ada baris poin; bayar normal.
registry.category("web_tour.tours").add("EfnPosLoyaltyWrongOtpTour", {
    steps: () =>
        [
            startWithMemberOnPayment(),
            redeemPoints("100"),
            submitOtp("111111"),
            notificationContains("Kode OTP salah"),
            submitOtp("222222"),
            notificationContains("Kode OTP salah"),
            submitOtp("333333"),
            { content: "popup OTP tertutup", trigger: negate(".modal .subtitle") },
            pointsLines(0),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            Chrome.isSynced(),
            Chrome.endTour(),
        ].flat(),
});

// (3) Batal: redeem lalu kembali ke layar produk -> dialog -> release, baris hilang.
registry.category("web_tour.tours").add("EfnPosLoyaltyCancelTour", {
    steps: () =>
        [
            startWithMemberOnPayment(),
            redeemPoints("100"),
            submitOtp("123456"),
            pointsLines(1),
            PaymentScreen.clickBackToProductScreen(),
            Dialog.is({ title: "Batalkan tukar poin?" }),
            Dialog.confirm("Batalkan tukar poin"),
            ProductScreen.isShown(),
            pointsLines(0),
            ProductScreen.clickPayButton(),
            { content: "tombol tukar muncul lagi", trigger: REDEEM_BUTTON },
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            Chrome.isSynced(),
            Chrome.endTour(),
        ].flat(),
});

// (4) Offline: tombol disembunyikan; redeem lalu offline -> validasi diblok sampai online.
registry.category("web_tour.tours").add("EfnPosLoyaltyOfflineTour", {
    steps: () =>
        [
            startWithMemberOnPayment(),
            { content: "tombol tukar tampil saat online", trigger: REDEEM_BUTTON },
            OfflineUtil.setOfflineMode(),
            waitNetwork(true),
            { content: "tombol tukar hilang saat offline", trigger: negate(REDEEM_BUTTON) },
            OfflineUtil.setOnlineMode(),
            waitNetwork(false),
            redeemPoints("100"),
            submitOtp("123456"),
            pointsLines(1),
            OfflineUtil.setOfflineMode(),
            waitNetwork(true),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            { content: "validasi diblok saat offline", trigger: `.modal-body:contains("POS sedang offline")` },
            Dialog.confirm("Tunggu online", ".btn-secondary"),
            PaymentScreen.isShown(),
            pointsLines(1),
            OfflineUtil.setOnlineMode(),
            waitNetwork(false),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            Chrome.isSynced(),
            Chrome.endTour(),
        ].flat(),
});
