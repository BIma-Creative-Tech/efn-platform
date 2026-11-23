/** @odoo-module **/
// G56 (ADR-049): port dari core/custom_home_console/static/src/home_console/pinned_shortcut_service.js.
// Daftar "terakhir dibuka" disimpan di localStorage browser (hanya id/nama menu, tanpa data bisnis).

import { registry } from "@web/core/registry";

const STORAGE_KEY = "efn_web_home.recent_apps";
const MAX_RECENT = 6;

export const efnHomeRecentService = {
    start() {
        const read = () => {
            try {
                const list = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
                return Array.isArray(list) ? list : [];
            } catch {
                return [];
            }
        };
        const write = (list) => {
            try {
                localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
            } catch {
                // Kuota penuh / storage dimatikan: abaikan.
            }
        };
        return {
            list() {
                return read();
            },
            push(app) {
                if (!app || !app.id) {
                    return;
                }
                const entry = { id: app.id, name: app.name };
                const rest = read().filter((a) => a.id !== entry.id);
                rest.unshift(entry);
                write(rest.slice(0, MAX_RECENT));
            },
        };
    },
};

registry.category("services").add("efn_web_home_recent", efnHomeRecentService);
