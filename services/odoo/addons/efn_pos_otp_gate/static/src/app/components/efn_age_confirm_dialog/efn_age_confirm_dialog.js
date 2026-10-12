/**
 * E-34: dialog pernyataan usia 17+ (centang wajib) sebelum OTP `pos_enroll`. Tombol lanjut nonaktif sampai
 * dicentang. Hanya mengembalikan `true`; tidak ada data usia yang disimpan/ dikirim selain `age_confirmed`.
 */
import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

export class EfnAgeConfirmDialog extends Component {
    static template = "efn_pos_otp_gate.EfnAgeConfirmDialog";
    static components = { Dialog };
    static props = {
        title: String,
        body: String,
        getPayload: Function,
        close: Function,
    };

    setup() {
        this.state = useState({ confirmed: false });
    }

    confirm() {
        if (!this.state.confirmed) {
            return;
        }
        this.props.getPayload(true);
        this.props.close();
    }
}
