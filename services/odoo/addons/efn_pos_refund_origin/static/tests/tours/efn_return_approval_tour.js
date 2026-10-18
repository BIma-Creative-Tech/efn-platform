/**
 * Tour E-41 (retur tanpa struk + persetujuan Store Manager). Dijalankan tests/test_return_approval_tour.py
 * (tag `efn_tour`). Produk qty -1 -> bayar tunai -> Validate -> dialog persetujuan -> PIN salah (pesan galat)
 * -> PIN benar + alasan -> struk tersinkron.
 */
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import { registry } from "@web/core/registry";

const PRODUCT = "Produk Tour Retur";

registry.category("web_tour.tours").add("EfnPosReturnApprovalTour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.clickDisplayedProduct(PRODUCT),
            ProductScreen.clickNumpad("+/-"),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Cash"),
            PaymentScreen.clickValidate(),
            {
                content: "dialog persetujuan Store Manager tampil",
                trigger: ".modal select.efn-return-approver",
            },
            {
                content: "PIN salah",
                trigger: ".modal input.efn-return-pin",
                run: "edit 246801",
            },
            {
                content: "alasan",
                trigger: ".modal input.efn-return-reason",
                run: "edit Barang rusak tour",
            },
            {
                content: "setujui (PIN salah)",
                trigger: ".modal .efn-return-approve:enabled",
                run: "click",
            },
            {
                content: "pesan PIN salah dari server",
                trigger: ".modal .efn-return-error:contains('PIN salah')",
            },
            {
                content: "PIN benar",
                trigger: ".modal input.efn-return-pin",
                run: "edit 135790",
            },
            {
                content: "setujui (PIN benar)",
                trigger: ".modal .efn-return-approve:enabled",
                run: "click",
            },
            ReceiptScreen.isShown(),
            Chrome.isSynced(),
            Chrome.endTour(),
        ].flat(),
});
