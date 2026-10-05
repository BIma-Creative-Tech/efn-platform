/**
 * EFN Payment Gateway (QRIS lewat BFF) — PaymentInterface POS 19.0.
 *
 * - Browser hanya memanggil RPC server Odoo (`pos.payment.method.efn_gateway_*`); server Odoo
 *   yang memanggil BFF (HTTPS + CA internal, service key tidak pernah ke browser).
 * - Offline -> metode ini menolak (CLAUDE.md: fitur yang butuh server nonaktif saat offline).
 * - Polling status tiap 2 s (docs/04 §8.3) sampai final atau `expires_at`.
 * - QR ditampilkan ke kasir dengan `QRPopup` standar dan ke layar pelanggan lewat
 *   `order.qrPaymentData` (bentuk objek BELUM terverifikasi di 19.0 — lihat README).
 * - Status "lunas" hanya dari server; tombol popup TIDAK bisa mengonfirmasi pembayaran.
 * - Tanpa reversal (refund QRIS fase 1 lewat metode lain sesuai SOP).
 */
import { _t } from "@web/core/l10n/translation";
import { PaymentInterface } from "@point_of_sale/app/utils/payment/payment_interface";
import { register_payment_method } from "@point_of_sale/app/services/pos_store";
import { QRPopup } from "@point_of_sale/app/components/popups/qr_code_popup/qr_code_popup";

const POLL_INTERVAL_MS = 2000;
const FALLBACK_TIMEOUT_MS = 15 * 60 * 1000;
const EXPIRY_GRACE_MS = 5000;
const FAILED_STATUSES = ["expired", "failed", "cancelled"];

export class PaymentEfnGateway extends PaymentInterface {
    setup() {
        super.setup(...arguments);
        this.supports_reversals = false;
        this.pending = null;
    }

    get fastPayments() {
        return false;
    }

    // ----------------------------------------------------------------- helpers
    _isOffline() {
        return Boolean(this.pos?.data?.network?.offline);
    }

    _notify(message, type = "warning") {
        this.env.services.notification.add(message, { type });
    }

    _rpc(method, args) {
        return this.pos.data.silentCall("pos.payment.method", method, [
            [this.payment_method_id.id],
            ...args,
        ]);
    }

    _setCustomerDisplayQr(order, data) {
        try {
            order.qrPaymentData = data;
        } catch {
            // Layar pelanggan opsional; kegagalan tidak menghentikan pembayaran.
        }
    }

    _stop(pending) {
        if (pending.timer) {
            clearInterval(pending.timer);
            pending.timer = null;
        }
        if (pending.closeQr) {
            try {
                pending.closeQr();
            } catch {
                // dialog sudah tertutup
            }
            pending.closeQr = null;
        }
        this._setCustomerDisplayQr(pending.order, null);
        if (this.pending === pending) {
            this.pending = null;
        }
    }

    _showQr(pending, line, result) {
        const qrCode = result.qr_image || result.qr_string;
        let amount = String(line.amount);
        try {
            amount = this.env.utils.formatCurrency(line.amount);
        } catch {
            // env.utils tidak tersedia di konteks ini: pakai angka mentah
        }
        this._setCustomerDisplayQr(pending.order, {
            name: this.payment_method_id.name,
            amount,
            qrCode,
        });
        if (!result.qr_image) {
            this._notify(_t("QR tidak dapat ditampilkan di layar kasir; gunakan layar pelanggan."));
            return;
        }
        pending.closeQr = this.env.services.dialog.add(QRPopup, {
            title: _t("QRIS"),
            line,
            order: pending.order,
            qrCode: result.qr_image,
            confirmLabel: _t("Sembunyikan QR"),
            cancelLabel: _t("Batalkan Pembayaran"),
            // Konfirmasi hanya menutup popup: status lunas ditentukan server (polling).
            confirm: () => {},
            cancel: () => this._cancelPending(pending),
            dismiss: () => {},
        });
    }

    // --------------------------------------------------------------- interface
    async sendPaymentRequest(uuid) {
        await super.sendPaymentRequest(uuid);
        const order = this.pos.getOrder();
        const line = order?.payment_ids.find((paymentLine) => paymentLine.uuid === uuid);
        if (!order || !line) {
            return false;
        }
        if (this._isOffline()) {
            this._notify(_t("QRIS tidak tersedia saat POS offline. Gunakan metode lain."));
            return false;
        }
        if (this.pending) {
            this._notify(_t("Selesaikan atau batalkan QRIS yang sedang berjalan terlebih dahulu."));
            return false;
        }
        if (!(line.amount > 0)) {
            this._notify(_t("Nominal QRIS harus lebih dari nol. Refund QRIS lewat metode lain."));
            return false;
        }
        let result;
        try {
            result = await this._rpc("efn_gateway_create", [
                order.uuid,
                line.uuid,
                line.amount,
                this.pos.config.id,
            ]);
        } catch {
            this._notify(_t("Layanan pembayaran tidak dapat dihubungi. Gunakan metode lain."), "danger");
            return false;
        }
        if (!result?.ok) {
            this._notify(result?.message || _t("Gagal membuat QRIS."), "danger");
            return false;
        }
        if (result.status === "paid" && result.transaction_id) {
            line.transaction_id = result.transaction_id;
            return true;
        }
        if (result.status !== "pending") {
            this._notify(_t("QRIS tidak aktif. Coba lagi."), "danger");
            return false;
        }
        line.setPaymentStatus("waitingCard");
        return await this._waitForPayment(order, line, result);
    }

    _waitForPayment(order, line, result) {
        return new Promise((resolve) => {
            const expiresAt = result.expires_at ? Date.parse(result.expires_at) : NaN;
            const deadline = Number.isFinite(expiresAt)
                ? expiresAt + EXPIRY_GRACE_MS
                : Date.now() + FALLBACK_TIMEOUT_MS;
            const pending = {
                order,
                lineUuid: line.uuid,
                intentId: result.intent_id,
                timer: null,
                busy: false,
                closeQr: null,
            };
            pending.finish = (ok) => {
                if (pending.done) {
                    return;
                }
                pending.done = true;
                this._stop(pending);
                resolve(ok);
            };
            this.pending = pending;
            this._showQr(pending, line, result);
            pending.timer = setInterval(async () => {
                if (pending.done || pending.busy) {
                    return;
                }
                if (Date.now() > deadline) {
                    this._notify(_t("QRIS kedaluwarsa. Buat ulang atau gunakan metode lain."));
                    this._rpc("efn_gateway_cancel", [pending.intentId]).catch(() => {});
                    pending.finish(false);
                    return;
                }
                if (this._isOffline()) {
                    return; // status di server tetap otoritatif; coba lagi saat online
                }
                pending.busy = true;
                try {
                    const status = await this._rpc("efn_gateway_status", [pending.intentId]);
                    if (pending.done) {
                        return;
                    }
                    if (status?.status === "paid" && status.transaction_id) {
                        line.transaction_id = status.transaction_id;
                        this._notify(_t("Pembayaran QRIS diterima."), "success");
                        pending.finish(true);
                    } else if (FAILED_STATUSES.includes(status?.status)) {
                        this._notify(_t("Pembayaran QRIS tidak berhasil (%s).", status.status), "danger");
                        pending.finish(false);
                    }
                } catch {
                    // gangguan jaringan sesaat: polling berikutnya mencoba lagi
                } finally {
                    pending.busy = false;
                }
            }, POLL_INTERVAL_MS);
        });
    }

    async _cancelPending(pending) {
        if (!pending || pending.done) {
            return true;
        }
        let result;
        try {
            result = await this._rpc("efn_gateway_cancel", [pending.intentId]);
        } catch {
            this._notify(_t("Gagal membatalkan QRIS. Coba lagi."), "danger");
            return false;
        }
        if (result?.status === "paid") {
            // Pelanggan sudah membayar: jangan batalkan; polling berikutnya menyelesaikan baris.
            this._notify(_t("Pembayaran sudah diterima dan tidak bisa dibatalkan."));
            return false;
        }
        if (result?.ok || FAILED_STATUSES.includes(result?.status)) {
            pending.finish(false);
            return true;
        }
        this._notify(result?.message || _t("Gagal membatalkan QRIS. Coba lagi."), "danger");
        return false;
    }

    async sendPaymentCancel(order, uuid) {
        await super.sendPaymentCancel(order, uuid);
        const pending = this.pending;
        if (!pending || pending.lineUuid !== uuid) {
            return true;
        }
        return await this._cancelPending(pending);
    }

    async sendPaymentReversal(uuid) {
        this._notify(_t("Refund QRIS belum didukung. Gunakan metode lain sesuai SOP."));
        return false;
    }

    close() {
        if (this.pending) {
            // POS ditutup: hentikan polling saja; intent pending dibereskan cron rekonsiliasi.
            const pending = this.pending;
            pending.done = true;
            this._stop(pending);
        }
        super.close();
    }
}

register_payment_method("efn_gateway", PaymentEfnGateway);
