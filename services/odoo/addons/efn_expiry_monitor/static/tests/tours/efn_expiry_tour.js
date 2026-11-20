/* global posmodel */
/**
 * Tour POS efn_expiry_monitor (G45, docs/04 §28.5a). Dijalankan tests/test_expiry_tour.py (tag `efn_tour`).
 *
 * EfnExpiryFefoMarkdownTour: kartu produk A (3 lot) -> FEFO memilih lot aman terdekat tanpa popup, melewati lot
 *   ber-markdown aktif; kartu produk B (hanya lot markdown + lot kedaluwarsa, tanpa kandidat FEFO) -> popup lot CE ->
 *   pilih lot markdown -> harga baris = harga markdown -> bayar Bank -> tersinkron.
 * EfnExpiryOverrideTour: produk B -> popup lot CE -> pilih lot kedaluwarsa -> dialog persetujuan supervisor -> Batal
 *   (baris tidak ditambahkan) -> ulangi -> PIN salah (pesan server) -> PIN benar + alasan -> baris masuk bertanda
 *   override -> bayar Bank -> tersinkron.
 * Pemeriksaan sisi server (qty_sold, efn_markdown_id, event override, persetujuan consumed) di test Python.
 */
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import { negate } from "@point_of_sale/../tests/generic_helpers/utils";
import { registry } from "@web/core/registry";

// Harus sama dengan tests/test_expiry_tour.py.
const PRODUCT_A = "Produk Tour FEFO";
const PRODUCT_B = "Produk Tour Markdown";
const LOT_A_MD = "TOUR-A-MD";
const LOT_A_SOON = "TOUR-A-SOON";
const LOT_A_LATE = "TOUR-A-LATE";
const LOT_B_MD = "TOUR-B-MD";
const LOT_B_OLD = "TOUR-B-OLD";
const MARKDOWN_PRICE_B = 12000;

/**
 * Pilih lot yang sudah ada di popup lot CE 19 untuk produk ber-tracking lot: ketik nama, klik item dropdown yang cocok,
 * lalu konfirmasi. (Ketik + Enter saja tidak memilih lot: popup mengembalikan newPackLotLines kosong.)
 */
function pickExistingLot(lotName) {
    return [
        { content: `ketik lot ${lotName}`, trigger: ".o-autocomplete input", run: `edit ${lotName}` },
        {
            content: `pilih lot ${lotName} dari dropdown`,
            trigger: `.o-autocomplete--dropdown-item:contains(${lotName})`,
            run: "click",
        },
        Dialog.confirm(),
    ];
}

function linesWithLot(lotName) {
    const order = posmodel.getOrder();
    return (order?.lines || []).filter((line) =>
        (line.pack_lot_ids || []).some((lot) => lot.lot_name === lotName)
    );
}

/**
 * Langkah pemeriksaan state order lokal. `check()` mengembalikan pesan galat (string) atau null; diulang sampai
 * 5 detik karena baris ditambahkan secara async setelah dialog tertutup.
 */
function expectState(content, check) {
    return {
        content,
        trigger: "body",
        run: async () => {
            const deadline = Date.now() + 5000;
            let error = check();
            while (error && Date.now() < deadline) {
                await new Promise((resolve) => setTimeout(resolve, 100));
                error = check();
            }
            if (error) {
                throw new Error(`${content}: ${error}`);
            }
        },
    };
}

registry.category("web_tour.tours").add("EfnExpiryFefoMarkdownTour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            // (1) FEFO otomatis tanpa popup lot.
            ProductScreen.clickDisplayedProduct(PRODUCT_A),
            expectState("FEFO memilih lot aman terdekat (bukan lot markdown)", () => {
                const fefo = linesWithLot(LOT_A_SOON);
                if (fefo.length !== 1) {
                    return `baris lot ${LOT_A_SOON} = ${fefo.length}`;
                }
                if (fefo[0].efn_lot_source !== "fefo") {
                    return `efn_lot_source = ${fefo[0].efn_lot_source}`;
                }
                if (fefo[0].efn_markdown_ref) {
                    return "baris FEFO tidak boleh membawa markdown";
                }
                if (linesWithLot(LOT_A_MD).length || linesWithLot(LOT_A_LATE).length) {
                    return "lot markdown/lot akhir ikut terpilih";
                }
                return null;
            }),
            {
                content: "popup lot CE tidak muncul untuk jalur FEFO",
                trigger: negate(".modal .o-autocomplete input"),
            },
            // (2) Lot markdown dipilih eksplisit lewat popup lot CE (tidak ada kandidat FEFO untuk produk B).
            ProductScreen.clickDisplayedProduct(PRODUCT_B),
            pickExistingLot(LOT_B_MD),
            expectState("harga baris = harga markdown lot", () => {
                const md = linesWithLot(LOT_B_MD);
                if (md.length !== 1) {
                    return `baris lot ${LOT_B_MD} = ${md.length}`;
                }
                if (md[0].efn_lot_source !== "manual") {
                    return `efn_lot_source = ${md[0].efn_lot_source}`;
                }
                if (!md[0].efn_markdown_ref) {
                    return "efn_markdown_ref kosong";
                }
                if (Math.abs(md[0].price_unit - MARKDOWN_PRICE_B) > 0.001) {
                    return `price_unit = ${md[0].price_unit}`;
                }
                return null;
            }),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            Chrome.isSynced(),
            Chrome.endTour(),
        ].flat(),
});

registry.category("web_tour.tours").add("EfnExpiryOverrideTour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            // (3a) Lot kedaluwarsa -> dialog override -> Batal -> baris tidak ditambahkan.
            ProductScreen.clickDisplayedProduct(PRODUCT_B),
            pickExistingLot(LOT_B_OLD),
            {
                content: "dialog persetujuan supervisor tampil",
                trigger: ".modal select.efn-expiry-approver",
            },
            {
                content: "batal override",
                trigger: ".modal .modal-footer button.btn-secondary:contains('Batal')",
                run: "click",
            },
            {
                content: "dialog tertutup",
                trigger: negate(".modal select.efn-expiry-approver"),
            },
            expectState("lot kedaluwarsa tidak ditambahkan setelah batal", () => {
                const count = linesWithLot(LOT_B_OLD).length;
                return count ? `baris lot ${LOT_B_OLD} = ${count}` : null;
            }),
            // (3b) Ulangi -> PIN salah -> PIN benar + alasan -> baris masuk bertanda override.
            ProductScreen.clickDisplayedProduct(PRODUCT_B),
            pickExistingLot(LOT_B_OLD),
            {
                content: "dialog persetujuan supervisor tampil lagi",
                trigger: ".modal select.efn-expiry-approver",
            },
            {
                content: "PIN salah",
                trigger: ".modal input.efn-expiry-pin",
                run: "edit 246801",
            },
            {
                content: "alasan",
                trigger: ".modal input.efn-expiry-reason",
                run: "edit Dijual untuk uji tour",
            },
            {
                content: "setujui (PIN salah)",
                trigger: ".modal .efn-expiry-approve:enabled",
                run: "click",
            },
            {
                content: "pesan PIN salah dari server",
                trigger: ".modal .efn-expiry-error:contains('PIN salah')",
            },
            {
                content: "PIN benar",
                trigger: ".modal input.efn-expiry-pin",
                run: "edit 135790",
            },
            {
                content: "setujui (PIN benar)",
                trigger: ".modal .efn-expiry-approve:enabled",
                run: "click",
            },
            {
                content: "dialog tertutup setelah disetujui",
                trigger: negate(".modal select.efn-expiry-approver"),
            },
            expectState("baris lot kedaluwarsa masuk dengan tanda override", () => {
                const lines = linesWithLot(LOT_B_OLD);
                if (lines.length !== 1) {
                    return `baris lot ${LOT_B_OLD} = ${lines.length}`;
                }
                if (!lines[0].efn_expiry_override) {
                    return "efn_expiry_override tidak terpasang";
                }
                if (lines[0].efn_lot_source !== "manual") {
                    return `efn_lot_source = ${lines[0].efn_lot_source}`;
                }
                return null;
            }),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            Chrome.isSynced(),
            Chrome.endTour(),
        ].flat(),
});
