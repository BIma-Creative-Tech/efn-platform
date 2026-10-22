/** E-27: slip QR untuk printer struk (gambar dari server; tanpa teks URL/token). */
import { Component } from "@odoo/owl";

export class EfnQrSlip extends Component {
    static template = "efn_pos_bff_order.EfnQrSlip";
    static props = {
        qr_png_b64: String,
        expires_at: String,
        table_label: String,
        outlet: { type: String, optional: true },
    };

    get imageSrc() {
        return `data:image/png;base64,${this.props.qr_png_b64}`;
    }
}
