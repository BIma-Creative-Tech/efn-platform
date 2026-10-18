/**
 * E-45: dialog kode bayar pesanan online. Kasir mengetik kode dari aplikasi pelanggan; kode dikirim sekali ke server
 * Odoo (`pos.config.efn_online_counter_lookup`) lalu dihapus dari state. Hasil sukses hanya nomor pesanan + nominal.
 */
import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { EFN_COUNTER_CODE_RE, efnNormalizeCounterCode } from "@efn_online_pos_counter/app/utils/efn_counter";

export class EfnCounterCodeDialog extends Component {
    static template = "efn_online_pos_counter.EfnCounterCodeDialog";
    static components = { Dialog };
    static props = {
        title: String,
        submit: Function,
        getPayload: Function,
        close: Function,
    };

    setup() {
        this.state = useState({ code: "", error: "", busy: false });
    }

    get canSubmit() {
        return !this.state.busy && EFN_COUNTER_CODE_RE.test(efnNormalizeCounterCode(this.state.code));
    }

    async confirm() {
        if (!this.canSubmit) {
            return;
        }
        this.state.busy = true;
        this.state.error = "";
        const code = efnNormalizeCounterCode(this.state.code);
        this.state.code = "";
        let result = null;
        try {
            result = await this.props.submit(code);
        } catch {
            result = { error_code: "network", message: _t("Server tidak dapat dihubungi. Coba lagi.") };
        }
        if (result && result.order_label && !result.error_code) {
            this.props.getPayload(result);
            this.props.close();
            return;
        }
        this.state.error = result?.message || _t("Kode bayar tidak valid.");
        this.state.busy = false;
    }
}
