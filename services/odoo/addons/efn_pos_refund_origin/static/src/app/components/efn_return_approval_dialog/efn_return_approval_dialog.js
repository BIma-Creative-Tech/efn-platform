/**
 * E-41 (docs/04 §12.4, L-51): dialog persetujuan Store Manager untuk retur tanpa struk.
 * Kasir memilih Store Manager, Store Manager mengetik PIN-nya sendiri, kasir mengisi alasan. PIN dikirim sekali ke
 * server Odoo (`pos.config.efn_return_approval_request`) dan langsung dihapus dari state dialog; tidak ada hash
 * PIN di browser. Dialog hanya tertutup dengan hasil `true` bila server menyetujui.
 */
import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";

const PIN_RE = /^\d{6,8}$/;

export class EfnReturnApprovalDialog extends Component {
    static template = "efn_pos_refund_origin.EfnReturnApprovalDialog";
    static components = { Dialog };
    static props = {
        title: String,
        amountLabel: String,
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
