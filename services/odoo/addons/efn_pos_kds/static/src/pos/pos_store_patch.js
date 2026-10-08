/**
 * efn_pos_kds — integrasi POS restoran (docs/08 §7.3 F-52/F-53).
 *
 * - Kategori stasiun KDS ikut `preparationCategories` (tombol "Order" muncul walau tanpa printer).
 * - Cadangan printer: printer standar (pos.printer + product_categories_ids, TIDAK diubah) yang
 *   menjadi `fallback_printer_id` stasiun mode `kds` dilewati selama POS online DAN layar stasiun
 *   hidup (status dicek ke server tepat sebelum mencetak; gagal cek = dianggap mati -> cetak).
 *   Mode `kds_printer` / `printer` = perilaku standar.
 * - POS offline: tombol "Order" mencetak ke printer (standar Odoo menolak saat offline) dan
 *   menandai order `efn_kds_printed_offline` -> tiket dibuat server saat sinkron, langsung
 *   "dimasak" berlabel "sudah dicetak" (dapur tidak memasak ganda).
 * - Badge "Siap" di denah meja dari notifikasi bus `EFN_KDS_READY` di kanal PARTNER kasir
 *   (SEC-77; bukan kanal token config yang ikut ke QR self-order/kiosk), disaring per `config_id`.
 * - SEC-76: bila server gagal membuat tiket (`order.efn_kds_ticket_failed` di respons sinkron),
 *   perubahan yang tadi DITAHAN untuk stasiun mode `kds` dicetak ke printer cadangan + notifikasi
 *   kasir (sticky). Mode `kds_printer` sudah mencetak; kasir tetap diberi tahu layar tidak lengkap.
 * `updateLastOrderChange`/`last_order_preparation_change` tidak diubah.
 */
import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosConfig } from "@point_of_sale/app/models/pos_config";
import { ConnectionLostError } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";

function kdsStations(models) {
    return models["efn.kds.station"]?.getAll?.() || [];
}

patch(PosConfig.prototype, {
    get preparationCategories() {
        const categories = super.preparationCategories;
        const stations = kdsStations(this.models).filter((s) => s.mode !== "printer");
        if (!stations.length) {
            return categories;
        }
        const result = new Set(categories);
        for (const station of stations) {
            const ids = (station.category_ids || []).map((c) => (typeof c === "object" ? c.id : c));
            if (!ids.length) {
                // Stasiun "semua kategori" (expo): seluruh kategori POS.
                for (const categ of this.models["pos.category"].getAll()) {
                    result.add(categ.id);
                }
            }
            for (const id of ids) {
                result.add(id);
            }
        }
        return result;
    },
});

patch(PosStore.prototype, {
    async afterProcessServerData() {
        const result = await super.afterProcessServerData(...arguments);
        this.efnKdsReadyTables = {};
        this.efnKdsSuppressed = {};
        // Kanal partner user (dilanggani otomatis oleh sesi websocket ter-autentikasi).
        this.bus.subscribe("EFN_KDS_READY", (payload) => this.efnKdsOnReady(payload));
        return result;
    },

    efnKdsOnReady(payload) {
        if (!payload || !payload.table_id || payload.config_id !== this.config.id) {
            return;
        }
        const ready = { ...(this.efnKdsReadyTables || {}) };
        if (payload.state === "ready") {
            ready[payload.table_id] = true;
            const table = this.models["restaurant.table"]?.get(payload.table_id);
            this.notification.add(
                _t("Pesanan siap diantar: meja %s", table?.table_number ?? payload.table_id),
                { type: "success" }
            );
        } else {
            delete ready[payload.table_id];
        }
        this.efnKdsReadyTables = ready;
    },

    efnKdsTableReady(table) {
        return Boolean(table && this.efnKdsReadyTables?.[table.id]);
    },

    async efnKdsStationStatus() {
        const stations = kdsStations(this.models);
        if (!stations.length) {
            return {};
        }
        const status = {};
        if (!this.data.network.offline) {
            try {
                const rows = await this.data.call("pos.config", "efn_kds_station_status", [
                    this.config.id,
                ]);
                for (const row of rows || []) {
                    status[row.id] = row.is_online;
                }
            } catch {
                // Gagal cek = anggap layar mati -> printer cadangan mencetak (F-53).
            }
        }
        return status;
    },

    async printChanges(order, orderChange, reprint = false, printers = this.unwatched.printers) {
        const stations = kdsStations(this.models).filter((s) => s.mode === "kds");
        if (reprint || !stations.length) {
            return super.printChanges(order, orderChange, reprint, printers);
        }
        const online = !this.data.network.offline;
        const status = await this.efnKdsStationStatus();
        const suppressed = new Set();
        const printedFallback = new Set();
        for (const printer of printers) {
            const printerId = printer.config?.id;
            const users = kdsStations(this.models).filter(
                (s) => (s.fallback_printer_id?.id ?? s.fallback_printer_id) === printerId
            );
            if (!users.length) {
                continue;
            }
            const allKdsAlive = users.every((s) => s.mode === "kds" && online && status[s.id]);
            if (allKdsAlive) {
                suppressed.add(printer);
            } else if (users.some((s) => s.mode === "kds")) {
                printedFallback.add(printer);
            }
        }
        const toPrint = printers.filter((p) => !suppressed.has(p));
        let printed = false;
        if (toPrint.length) {
            printed = await super.printChanges(order, orderChange, reprint, toPrint);
        }
        if (printed && printedFallback.size) {
            order.efn_kds_printed_offline = true;
        }
        if (suppressed.size) {
            // Disimpan sampai respons sinkron: dicetak bila server gagal membuat tiket (SEC-76).
            this.efnKdsSuppressed = this.efnKdsSuppressed || {};
            this.efnKdsSuppressed[order.uuid] = { orderChange, printers: [...suppressed] };
        }
        // Perubahan diterima layar KDS (server) -> dianggap terkirim ke alat persiapan.
        return printed || suppressed.size > 0;
    },

    async sendOrderInPreparation(order, opts = {}) {
        const result = await super.sendOrderInPreparation(order, opts);
        await this.efnKdsAfterSend(order);
        return result;
    },

    async efnKdsAfterSend(order) {
        const pending = this.efnKdsSuppressed?.[order.uuid];
        if (pending) {
            delete this.efnKdsSuppressed[order.uuid];
        }
        if (!order.efn_kds_ticket_failed || !kdsStations(this.models).length) {
            return;
        }
        let printed = false;
        if (pending && pending.printers.length) {
            try {
                printed = await super.printChanges(order, pending.orderChange, false, pending.printers);
            } catch {
                printed = false;
            }
        }
        this.notification.add(
            printed
                ? _t("Layar dapur gagal menerima pesanan: pesanan dicetak ke printer dapur cadangan.")
                : _t("Layar dapur gagal menerima pesanan. Pastikan dapur menerima cetakan / cetak ulang."),
            { type: "danger", sticky: true }
        );
    },

    async sendOrderInPreparationUpdateLastChange(order, opts = {}) {
        try {
            return await super.sendOrderInPreparationUpdateLastChange(order, opts);
        } catch (error) {
            if (!(error instanceof ConnectionLostError) || !kdsStations(this.models).length) {
                throw error;
            }
            // POS offline: cetak ke printer dapur (cadangan) lalu antre sinkron.
            await this.sendOrderInPreparation(order, opts);
            this.notification.add(
                _t("Offline: pesanan dicetak ke printer dapur; layar KDS diperbarui saat online."),
                { type: "warning" }
            );
        }
    },
});
