/**
 * Tour POS restoran -> KDS (efn_pos_kds, tag efn_tour). Dijalankan tests/test_kds_tour.py.
 * Printer dapur di-mock (`printOrderChanges` dicatat di `window.efnKdsPrints`); tiket KDS diperiksa
 * di Python setelah tour (dibuat server dari sinkron order).
 */
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as FloorScreen from "@pos_restaurant/../tests/tours/utils/floor_screen_util";
import * as ProductScreenPos from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ProductScreenResto from "@pos_restaurant/../tests/tours/utils/product_screen_util";
import { registry } from "@web/core/registry";

const ProductScreen = { ...ProductScreenPos, ...ProductScreenResto };

function mockPrinter() {
    return {
        content: "mock printer dapur",
        trigger: "body",
        run: () => {
            window.efnKdsPrints = [];
            posmodel.printOrderChanges = async function (data, printer) {
                window.efnKdsPrints.push(printer.config.name);
                return { successful: true, warningCode: false };
            };
        },
    };
}

function sendOrderToKitchen() {
    return [
        Chrome.startPoS(),
        Dialog.confirm("Open Register"),
        mockPrinter(),
        FloorScreen.clickTable("5"),
        ProductScreen.clickDisplayedProduct("KDS Nasi Tour"),
        ProductScreen.clickDisplayedProduct("Coca-Cola"),
        ProductScreen.clickOrderButton(),
        FloorScreen.isShown(),
        Chrome.isSynced(),
    ].flat();
}

function printedCount(expected) {
    return {
        content: `printer dapur dipanggil ${expected} kali`,
        trigger: "body",
        run: () => {
            const count = (window.efnKdsPrints || []).length;
            if (expected === "some" ? count < 1 : count !== expected) {
                throw new Error(`efnKdsPrints=${JSON.stringify(window.efnKdsPrints)} (harap ${expected})`);
            }
        },
    };
}

function waitSynced() {
    return {
        content: "order tersinkron (tiket dibuat server)",
        trigger: ".floor-map .table:not(.syncing):has(.label:contains('5'))",
    };
}

// (1)+(2) Stasiun dapur & bar menerima tiket; bar mode kds_printer -> printer standar dipanggil.
registry.category("web_tour.tours").add("EfnKdsPosOrderTour", {
    steps: () => [sendOrderToKitchen(), waitSynced(), printedCount("some"), Chrome.endTour()].flat(),
});

// (3) Stasiun bar mode kds, layar mati (heartbeat basi) -> printer cadangan mencetak.
registry.category("web_tour.tours").add("EfnKdsPosFallbackTour", {
    steps: () => [sendOrderToKitchen(), waitSynced(), printedCount("some"), Chrome.endTour()].flat(),
});

// (3b) Stasiun bar mode kds, layar hidup -> printer cadangan TIDAK dipanggil, tiket tetap dibuat.
registry.category("web_tour.tours").add("EfnKdsPosSuppressTour", {
    steps: () => [sendOrderToKitchen(), waitSynced(), printedCount(0), Chrome.endTour()].flat(),
});
