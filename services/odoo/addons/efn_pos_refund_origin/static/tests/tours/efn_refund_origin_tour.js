/* global posmodel */
/**
 * Tour E-31 (refund ke metode asal). Dijalankan tests/test_refund_origin_tour.py (tag `efn_tour`).
 * Order dibayar "Bank" -> refund: layar bayar hanya menampilkan "Bank" (tanpa "Cash") dan nominal terisi
 * otomatis -> validasi.
 */
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as TicketScreen from "@point_of_sale/../tests/pos/tours/utils/ticket_screen_util";
import * as Order from "@point_of_sale/../tests/generic_helpers/order_widget_util";
import * as Numpad from "@point_of_sale/../tests/generic_helpers/numpad_util";
import { inLeftSide } from "@point_of_sale/../tests/pos/tours/utils/common";
import { negate } from "@point_of_sale/../tests/generic_helpers/utils";
import { registry } from "@web/core/registry";

const PRODUCT = "Produk Tour Refund";

registry.category("web_tour.tours").add("EfnPosRefundOriginTour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.addOrderline(PRODUCT, "2"),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            Chrome.isSynced(),
            ReceiptScreen.clickNextOrder(),
            Chrome.clickOrders(),
            TicketScreen.selectFilter("Paid"),
            TicketScreen.selectOrder("001"),
            inLeftSide([
                ...Order.hasLine({ productName: PRODUCT, withClass: ".selected" }),
                Numpad.click("1"),
            ]),
            TicketScreen.confirmRefund(),
            PaymentScreen.isShown(),
            {
                content: "metode Cash tidak ditawarkan (bukan metode asal)",
                trigger: negate(`.paymentmethods .button.paymentmethod .payment-name:contains("Cash")`),
            },
            {
                content: "metode Bank (asal) ditawarkan",
                trigger: `.paymentmethods .button.paymentmethod .payment-name:contains("Bank")`,
            },
            {
                content: "baris Bank terisi otomatis",
                trigger: `.paymentlines .paymentline .payment-name:contains("Bank")`,
            },
            {
                content: "sisa tagihan 0",
                trigger: "body",
                run: () => {
                    const order = posmodel.getOrder();
                    if (Math.abs(order.remainingDue) > 0.001) {
                        throw new Error(`sisa ${order.remainingDue} != 0`);
                    }
                },
            },
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            Chrome.isSynced(),
            Chrome.endTour(),
        ].flat(),
});
