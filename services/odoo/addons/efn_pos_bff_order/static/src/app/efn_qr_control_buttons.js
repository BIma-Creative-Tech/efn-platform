/**
 * E-27 (docs/04 §14.A.4): tombol "QR Meja". Tampil bila `pos.config.efn_qr_enabled` dan order punya meja;
 * DINONAKTIFKAN saat POS offline (butuh server, CLAUDE.md). Alur: sinkron order meja -> RPC
 * `pos.config.efn_qr_session_open` -> dialog QR (gambar dari server) -> cetak slip. QR aktif -> konfirmasi cetak ulang
 * (QR lama dimatikan). Token tidak pernah disimpan di order/localStorage.
 */
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { AlertDialog, ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { EfnQrDialog } from "@efn_pos_bff_order/app/efn_qr_dialog";

function efnIsOffline(pos) {
    return Boolean(pos?.data?.network?.offline);
}

function efnRecordId(value) {
    return value && typeof value === "object" ? value.id : value;
}

patch(ControlButtons.prototype, {
    get efnShowQr() {
        const order = this.pos.getOrder();
        return Boolean(this.pos.config.efn_qr_enabled && order && efnRecordId(order.table_id));
    },

    get efnQrDisabled() {
        return efnIsOffline(this.pos);
    },

    efnQrAlert(body) {
        this.dialog.add(AlertDialog, { title: _t("QR Meja"), body });
    },

    async efnQrCall(method, args) {
        return await this.pos.data.call("pos.config", method, [[this.pos.config.id], ...args]);
    },

    async efnOpenTableQr(reissue = false) {
        this.props.close?.();
        if (efnIsOffline(this.pos)) {
            this.efnQrAlert(_t("POS sedang offline. QR meja membutuhkan koneksi ke server."));
            return;
        }
        const order = this.pos.getOrder();
        const tableId = efnRecordId(order?.table_id);
        if (!order || !tableId) {
            return;
        }
        // Order meja harus tersimpan di server (uuid dipakai server untuk mengikat sesi meja).
        await this.pos.syncAllOrders({ orders: [order] });
        const result = await this.efnQrCall("efn_qr_session_open", [tableId, order.uuid, reissue]);
        if (result?.error_code === "session_exists" && !reissue) {
            this.dialog.add(ConfirmationDialog, {
                title: _t("Cetak ulang QR?"),
                body: _t("Meja ini sudah punya QR aktif. Cetak ulang akan mematikan QR lama."),
                confirm: () => this.efnOpenTableQr(true),
            });
            return;
        }
        if (result?.error_code) {
            this.efnQrAlert(result.message || _t("QR tidak tersedia."));
            return;
        }
        if (!result?.qr_png_b64) {
            return;
        }
        this.dialog.add(EfnQrDialog, {
            qr_png_b64: result.qr_png_b64,
            expires_at: result.expires_at,
            table_label: result.table_label,
            onCloseSession: async () => {
                if (efnIsOffline(this.pos)) {
                    this.efnQrAlert(_t("POS sedang offline."));
                    return;
                }
                await this.efnQrCall("efn_qr_session_close", [tableId]);
            },
        });
    },
});
