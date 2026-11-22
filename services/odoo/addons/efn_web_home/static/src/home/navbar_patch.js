/** @odoo-module **/
// G56 (ADR-049): port dari core/custom_home_console/static/src/home_console/navbar_patch.js.
// Tombol apps di navbar membuka Beranda EFN lewat menuService.selectMenu agar app aktif navbar ikut berganti.

import { patch } from "@web/core/utils/patch";
import { NavBar } from "@web/webclient/navbar/navbar";

const HOME_MENU_XMLID = "efn_web_home.menu_home_root";
const HOME_ACTION_XMLID = "efn_web_home.action_home";

patch(NavBar.prototype, {
    openEfnHome() {
        const home = (this.menuService.getApps() || []).find((a) => a.xmlid === HOME_MENU_XMLID);
        if (home && home.actionID) {
            this.menuService.selectMenu(home);
        } else {
            this.actionService.doAction(HOME_ACTION_XMLID, { clearBreadcrumbs: true });
        }
    },
});
