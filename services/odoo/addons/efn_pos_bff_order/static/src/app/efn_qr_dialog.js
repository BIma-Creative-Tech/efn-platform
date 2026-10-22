/**
 * E-27: dialog QR sesi meja. Gambar QR (PNG base64) dirender server; URL bertoken tidak pernah ada di browser sebagai
 * teks dan tidak disimpan di order/localStorage. Cetak lewat printer struk (`pos.printer.print`, webPrintFallback).
 */
import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { EfnQrSlip } from "@efn_pos_bff_order/app/efn_qr_slip";

export class EfnQrDialog extends Component {
    static template = "efn_pos_bff_order.EfnQrDialog";
    static components = { Dialog };
    static props = {
        qr_png_b64: String,
        expires_at: String,
        table_label: String,
        close: Function,
        onCloseSession: { type: Function, optional: true },
    };

    setup() {
        this.pos = usePos();
    }

    get imageSrc() {
        return `data:image/png;base64,${this.props.qr_png_b64}`;
    }

    async print() {
        await this.pos.printer.print(
            EfnQrSlip,
            {
                qr_png_b64: this.props.qr_png_b64,
                expires_at: this.props.expires_at,
                table_label: this.props.table_label,
                outlet: this.pos.config.name,
            },
            this.pos.printOptions
        );
    }

    async closeSession() {
        if (this.props.onCloseSession) {
            await this.props.onCloseSession();
        }
        this.props.close();
    }
}
