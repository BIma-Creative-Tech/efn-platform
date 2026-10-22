/**
 * E-27: notifikasi pesanan QR di kanal PARTNER kasir (SEC-77, sama dengan efn_pos_kds; bukan kanal token config).
 *
 * - `EFN_QR_ORDER {order_id, table_id, auto_send, config_id}`: toast "Pesanan QR baru". Mode `waiter` (default L-71):
 *   pelayan membuka order meja lalu menekan *Order* (standar) atau menghapus order (= tolak).
 * - Mode `auto`: terminal memanggil `pos.order.efn_qr_claim_send`; HANYA pemenang klaim memuat order dari server lalu
 *   `sendOrderInPreparationUpdateLastChange` (standar: printer dapur + tiket KDS lewat sinkron). Offline -> dilewati
 *   (cron server memberi alert bila belum terkirim).
 * - `EFN_QR_STALE`: toast peringatan (pesanan QR belum dikirim ke dapur).
 */
import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { _t } from "@web/core/l10n/translation";

patch(PosStore.prototype, {
    async afterProcessServerData() {
        const result = await super.afterProcessServerData(...arguments);
        this.bus.subscribe("EFN_QR_ORDER", (payload) => this.efnQrOnOrder(payload));
        this.bus.subscribe("EFN_QR_STALE", (payload) => this.efnQrOnStale(payload));
        return result;
    },

    efnQrTableName(tableId) {
        const table = this.models["restaurant.table"]?.get(tableId);
        return table?.table_number ?? tableId;
    },

    async efnQrOnOrder(payload) {
        if (!payload || payload.config_id !== this.config.id) {
            return;
        }
        this.notification.add(_t("Pesanan QR baru: meja %s", this.efnQrTableName(payload.table_id)), {
            type: "info",
        });
        if (!payload.auto_send || this.data.network.offline) {
            return;
        }
        try {
            const won = await this.data.call("pos.order", "efn_qr_claim_send", [[payload.order_id]]);
            if (!won) {
                return;
            }
            const orders = await this.data.loadServerOrders([["id", "=", payload.order_id]]);
            const order = (orders || []).find((o) => o.id === payload.order_id);
            if (order) {
                await this.sendOrderInPreparationUpdateLastChange(order);
            }
        } catch {
            this.notification.add(
                _t("Pesanan QR meja %s belum terkirim ke dapur. Buka order lalu tekan Order.", this.efnQrTableName(payload.table_id)),
                { type: "warning", sticky: true }
            );
        }
    },

    efnQrOnStale(payload) {
        if (!payload || payload.config_id !== this.config.id) {
            return;
        }
        this.notification.add(
            _t("Pesanan QR meja %s belum dikirim ke dapur.", this.efnQrTableName(payload.table_id)),
            { type: "warning", sticky: true }
        );
    },
});
