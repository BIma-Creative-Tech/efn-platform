/**
 * Tour layar KDS (/efn_kds/<id>, user perangkat). Bundle efn_pos_kds.assets_kds_tests.
 * Tiket disiapkan Python (sinkron order POS); tour: tiket tampil -> Mulai -> Siap -> Disajikan.
 */
import { registry } from "@web/core/registry";

const card = (state) => `.efn-kds-column[data-state='${state}'] .efn-kds-ticket:has(.efn-kds-product:contains('KDS Nasi Tour'))`;

registry.category("web_tour.tours").add("EfnKdsScreenTour", {
    steps: () => [
        { content: "nama stasiun", trigger: ".efn-kds-station:contains('Dapur Panas Tour')" },
        { content: "tiket baru tampil", trigger: `${card("new")} .efn-kds-ticket-title:contains('5')` },
        {
            content: "tanpa data pelanggan / tiket stasiun lain",
            trigger: "body",
            run: () => {
                const text = document.body.innerText;
                for (const forbidden of ["Pelanggan Tour KDS", "Coca-Cola"]) {
                    if (text.includes(forbidden)) {
                        throw new Error(`layar KDS memuat '${forbidden}'`);
                    }
                }
            },
        },
        { content: "mulai masak", trigger: `${card("new")} .efn-kds-bump`, run: "click" },
        { content: "siap", trigger: `${card("in_progress")} .efn-kds-bump`, run: "click" },
        { content: "disajikan", trigger: `${card("ready")} .efn-kds-bump`, run: "click" },
        { content: "tiket keluar dari kolom", trigger: ".efn-kds-columns:not(:has(.efn-kds-ticket))" },
        { content: "bisa di-recall", trigger: ".efn-kds-recall-served" },
    ],
});
