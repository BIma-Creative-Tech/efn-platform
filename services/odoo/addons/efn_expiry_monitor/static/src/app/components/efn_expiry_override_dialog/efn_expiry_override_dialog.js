/**
 * efn_expiry_monitor: dialog persetujuan supervisor untuk menjual lot kedaluwarsa (docs/04 §28.5a).
 * Kasir memilih supervisor (grup override), supervisor mengetik PIN-nya sendiri (PIN E-41, diverifikasi server
 * `pos.config.efn_expiry_override_request`), kasir mengisi alasan. PIN dihapus dari state segera setelah dikirim.
 */
import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";

const PIN_RE = /^\d{6,8}$/;

export class EfnExpiryOverrideDialog extends Component {
    static template = "efn_expiry_monitor.EfnExpiryOverrideDialog";
    static components = { Dialog };
    static props = {
        title: String,
        lotName: String,
        approvers: Array,
        submit: Function,
        getPayload: Function,
        close: Function,
    };

    setup() {
        this.state = useState({
            approverId: String(this.props.approvers[0]?.id || ""),
            pin: "",
            reason: "",
            error: "",
            busy: false,
        });
    }

    get canSubmit() {
        return (
            !this.state.busy &&
            Boolean(this.state.approverId) &&
            PIN_RE.test(this.state.pin) &&
            this.state.reason.trim().length >= 3
        );
    }

    async confirm() {
        if (!this.canSubmit) {
            return;
        }
        this.state.busy = true;
        this.state.error = "";
        const pin = this.state.pin;
        this.state.pin = "";
        let result = null;
        try {
            result = await this.props.submit({
                approverId: Number(this.state.approverId),
                pin,
                reason: this.state.reason.trim(),
            });
        } catch {
            result = { ok: false, message: _t("Server tidak dapat dihubungi. Coba lagi.") };
        }
        if (result?.ok) {
            this.props.getPayload(true);
            this.props.close();
            return;
        }
        this.state.error = result?.message || _t("Persetujuan ditolak.");
        this.state.busy = false;
    }
}
