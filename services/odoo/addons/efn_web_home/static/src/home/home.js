/** @odoo-module **/
// G56 (ADR-049): port dari core/custom_home_console/static/src/home_console/home_console.js (19.0.1.0.0).
// Perubahan: tanpa 309 ikon PNG (banyak ikon Enterprise) -> ikon menu CE (webIconData) atau tile huruf awal;
// pengelompokan dihitung server (efn_home_bootstrap), bukan peta slug modul di klien; tanpa kartu sintetis.

import { Component, markup, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { EfnSpotlightSearch } from "./spotlight_search";

const DEFAULT_ACCENT = "#0b5d4b";
const EFN_LOGO = "/efn_web_home/static/src/img/efn_logo.png";
const DISMISS_TTL_MS = 24 * 60 * 60 * 1000;

function dismissKey(companyId) {
    return `efn_web_home.ann_until.${companyId || 0}`;
}

function isDismissed(companyId) {
    try {
        return parseInt(localStorage.getItem(dismissKey(companyId)) || "0", 10) > Date.now();
    } catch {
        return false;
    }
}

export class EfnHome extends Component {
    static template = "efn_web_home.Home";
    static components = { EfnSpotlightSearch };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.menu = useService("menu");
        this.ui = useState(useService("ui"));
        this.recent = useService("efn_web_home_recent");

        this.state = useState({
            bootstrap: null,
            grouped: [],
            dismissed: false,
        });

        onWillStart(async () => {
            const data = await this.orm.call("res.users", "efn_home_bootstrap", []);
            this.state.bootstrap = data;
            this.state.grouped = this._groupApps(data);
            this.state.dismissed = isDismissed(data.company.id);
        });
    }

    // --- Aplikasi ----------------------------------------------------------

    _iconSrc(menu) {
        const data = menu && menu.webIconData;
        if (typeof data === "string" && data && !data.includes("default_icon_app")) {
            return data;
        }
        return null;
    }

    _groupApps(data) {
        const buckets = {};
        for (const app of data.apps || []) {
            const menu = this.menu.getMenu(app.id);
            if (!menu) {
                continue;
            }
            (buckets[app.group] = buckets[app.group] || []).push({
                id: app.id,
                name: app.name,
                icon: this._iconSrc(menu),
                initial: (app.name || "?").trim().charAt(0).toUpperCase() || "?",
            });
        }
        return (data.groups || [])
            .filter((g) => buckets[g.key] && buckets[g.key].length)
            .map((g) => ({ key: g.key, label: g.label, apps: buckets[g.key] }));
    }

    get visibleAppIds() {
        const ids = new Set();
        for (const group of this.state.grouped) {
            for (const app of group.apps) {
                ids.add(app.id);
            }
        }
        return ids;
    }

    get recentApps() {
        // Hanya aplikasi yang masih terlihat untuk user ini (daftar lokal bisa basi setelah hak akses berubah).
        const visible = this.visibleAppIds;
        return this.recent.list().filter((a) => visible.has(a.id));
    }

    // --- Branding ----------------------------------------------------------

    get company() {
        return (this.state.bootstrap && this.state.bootstrap.company) || null;
    }

    get rootStyle() {
        const accent = (this.company && this.company.accent) || DEFAULT_ACCENT;
        return `--efn-home-accent: ${accent};`;
    }

    get logoUrl() {
        const co = this.company;
        if (co && co.has_home_logo) {
            return `/web/image/res.company/${co.id}/efn_brand_logo_home`;
        }
        if (co && co.has_logo) {
            return `/web/image/res.company/${co.id}/logo`;
        }
        return EFN_LOGO;
    }

    get greeting() {
        const h = new Date().getHours();
        if (h < 11) {
            return "Selamat pagi";
        }
        if (h < 15) {
            return "Selamat siang";
        }
        if (h < 19) {
            return "Selamat sore";
        }
        return "Selamat malam";
    }

    get isCompact() {
        const user = this.state.bootstrap && this.state.bootstrap.user;
        return (user && user.density === "compact") || this.ui.isSmall;
    }

    get announcement() {
        const co = this.company;
        if (!co || !co.announcement_html || this.state.dismissed) {
            return null;
        }
        // Sudah disanitasi server (fields.Html sanitize=True).
        return markup(co.announcement_html);
    }

    get pinned() {
        return (this.state.bootstrap && this.state.bootstrap.pinned) || [];
    }

    get activities() {
        return (this.state.bootstrap && this.state.bootstrap.recent_activities) || [];
    }

    // --- Handler -----------------------------------------------------------

    onDismissAnnouncement() {
        try {
            localStorage.setItem(dismissKey(this.company && this.company.id), String(Date.now() + DISMISS_TTL_MS));
        } catch {
            // abaikan
        }
        this.state.dismissed = true;
    }

    onIconError(ev) {
        // Ikon gagal dimuat -> sembunyikan img, tampilkan tile huruf di sebelahnya.
        const img = ev.target;
        img.classList.add("d-none");
        const tile = img.nextElementSibling;
        if (tile) {
            tile.classList.remove("d-none");
        }
    }

    onClickApp(app) {
        const menu = this.menu.getMenu(app.id);
        if (!menu) {
            return;
        }
        this.recent.push(app);
        // selectMenu (bukan doAction) agar app aktif + submenu navbar ikut diperbarui.
        this.menu.selectMenu(menu);
    }

    onClickShortcut(shortcut) {
        if (shortcut.id) {
            this.action.doAction(shortcut.id, { clearBreadcrumbs: true });
        }
    }

    onClickActivity(act) {
        if (!act.res_model || !act.res_id) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: act.res_model,
            res_id: act.res_id,
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("efn_web_home.home", EfnHome);
