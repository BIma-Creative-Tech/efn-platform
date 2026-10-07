/**
 * Tour UI POS (OWL) metode terminal `efn_gateway` (QRIS lewat BFF) — Gelombang 12.
 * Dijalankan oleh tests/test_gateway_tour.py (tag `efn_tour`, Chrome headless: image target
 * `test`). BFF di-mock server-side: create -> pending + QR; status -> paid (tour bayar) atau
 * tetap pending (tour batal); cancel -> cancelled. Nilai QR/payment_id fiktif.
 */
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import { negate } from "@point_of_sale/../tests/generic_helpers/utils";
import { registry } from "@web/core/registry";

const PRODUCT = "Produk Tour QRIS";
const METHOD = "QRIS EFN";

function startQris() {
    return [
        Chrome.startPoS(),
        Dialog.confirm("Open Register"),
        ProductScreen.addOrderline(PRODUCT, "1"),
        ProductScreen.clickPayButton(),
        PaymentScreen.clickPaymentMethod(METHOD),
        {
            content: "kirim permintaan QRIS",
            trigger: ".paymentline .send_payment_request",
            run: "click",
        },
        {
            content: "QR tampil di popup kasir",
            trigger: '.modal img[alt="QR Code"][src^="data:image/png;base64,"]',
        },
    ].flat();
}

// (c) QR tampil -> BFF (mock) paid -> baris pembayaran selesai -> order selesai.
registry.category("web_tour.tours").add("EfnPosQrisPaidTour", {
    steps: () =>
        [
            startQris(),
            { content: "notifikasi lunas", trigger: '.o_notification:contains("Pembayaran QRIS diterima")' },
            { content: "popup QR tertutup", trigger: negate('.modal img[alt="QR Code"]') },
            { content: "baris pembayaran selesai", trigger: ".paymentline .electronic_status:contains(\"Payment Successful\")" },
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            Chrome.isSynced(),
            Chrome.endTour(),
        ].flat(),
});

// (c) Batal dari popup QR -> intent cancelled -> baris bisa dicoba ulang/dihapus -> bayar Bank.
registry.category("web_tour.tours").add("EfnPosQrisCancelTour", {
    steps: () =>
        [
            startQris(),
            {
                content: "batalkan QRIS dari popup",
                trigger: '.modal .modal-footer button:contains("Batalkan Pembayaran")',
                run: "click",
            },
            { content: "popup QR tertutup", trigger: negate('.modal img[alt="QR Code"]') },
            { content: "baris siap dicoba ulang", trigger: ".paymentline .send_payment_request:contains(\"Retry\")" },
            {
                content: "hapus baris QRIS",
                trigger: ".paymentline .delete-button",
                run: "click",
            },
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            Chrome.isSynced(),
            Chrome.endTour(),
        ].flat(),
});
