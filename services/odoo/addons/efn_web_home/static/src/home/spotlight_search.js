/** @odoo-module **/
// G56 (ADR-049): port dari core/custom_home_console/static/src/home_console/spotlight_search.js.
// Kolom cari di header beranda membuka command palette bawaan (Ctrl+K); tidak ada logika cari sendiri.

import { Component } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class EfnSpotlightSearch extends Component {
    static template = "efn_web_home.SpotlightSearch";
    static props = {};

    setup() {
        this.command = useService("command");
    }

    _openPalette(searchValue) {
        this.command.openMainPalette({ searchValue: searchValue || "" });
    }

    // Klik (bukan focus) membuka palette: focus memicu loop saat palette ditutup dan fokus kembali ke input.
    onClick(ev) {
        this._openPalette("");
        ev.currentTarget.blur();
    }

    onInput(ev) {
        const query = ev.target.value;
        ev.target.value = "";
        ev.target.blur();
        this._openPalette(query);
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            const query = ev.target.value || "";
            ev.target.blur();
            this._openPalette(query);
        }
    }
}
