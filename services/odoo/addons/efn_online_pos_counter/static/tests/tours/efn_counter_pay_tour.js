/**
 * Tour E-45 (bayar pesanan online di kasir). Dijalankan tests/test_counter_tour.py (tag `efn_tour`).
 * Tombol "Bayar pesanan online" -> kode salah (pesan server) -> kode benar -> baris pelunasan -> tunai -> Validate.
 */
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import { registry } from "@web/core/registry";

// Sama dengan tests/test_counter_tour.py (data uji, bukan rahasia).
const WRONG_CODE = "QQQQ2222";
const TOUR_CODE = "HJKM2345";

registry.category("web_tour.tours").add("EfnOnlineCounterPayTour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.clickControlButton("Bayar pesanan online"),
            {
                content: "dialog kode bayar tampil",
                trigger: ".modal input.efn-counter-code",
                run: `edit ${WRONG_CODE}`,
            },
            {
                content: "cari (kode salah)",
                trigger: ".modal .efn-counter-submit:enabled",
                run: "click",
            },
            {
                content: "pesan kode tidak ditemukan dari server",
                trigger: ".modal .efn-counter-error",
            },
            {
                content: "kode benar",
                trigger: ".modal input.efn-counter-code",
                run: `edit ${TOUR_CODE}`,
            },
            {
                content: "cari (kode benar)",
                trigger: ".modal .efn-counter-submit:enabled",
                run: "click",
            },
            {
                content: "baris pelunasan ada di order",
                trigger: ".orderline:contains('Pelunasan Pesanan Online')",
            },
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Cash"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            Chrome.isSynced(),
            Chrome.endTour(),
        ].flat(),
});
