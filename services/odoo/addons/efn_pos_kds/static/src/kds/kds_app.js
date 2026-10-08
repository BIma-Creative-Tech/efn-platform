/**
 * Layar KDS efn_pos_kds (docs/08 §7.3 "Layar KDS"). OWL mandiri (bundle efn_pos_kds.assets_kds).
 *
 * Data: RPC `efn.kds.station.efn_kds_load` (cek perangkat/manager di server). Realtime: kanal bus
 * `<token>-EFN_KDS` (token acak dari halaman ber-autentikasi) hanya sebagai pemicu muat ulang;
 * payload bus tidak berisi isi tiket. Heartbeat 30 dtk -> `last_seen` (deteksi KDS mati di POS)
 * sekaligus polling cadangan bila websocket terputus. Sidik token berubah (rotasi) -> muat ulang.
 * Tidak ada harga, pelanggan, atau pembayaran yang ditampilkan.
 */
import { Component, onMounted, onWillStart, onWillUnmount, useState, whenReady } from "@odoo/owl";
import { MainComponentsContainer } from "@web/core/main_components_container";
import { useService } from "@web/core/utils/hooks";
import { mountComponent } from "@web/env";
import { session } from "@web/session";

const HEARTBEAT_MS = 30000;
const TICK_MS = 1000;
const COLUMNS = [
    { state: "new", title: "Baru" },
    { state: "in_progress", title: "Dimasak" },
    { state: "ready", title: "Siap" },
];
const NEXT_STATE = { new: "in_progress", in_progress: "ready", ready: "served" };
const NEXT_LABEL = { new: "Mulai", in_progress: "Siap", ready: "Disajikan" };

function parseServer(value) {
    // "YYYY-MM-DD HH:MM:SS" UTC dari server -> epoch ms.
    return value ? Date.parse(value.replace(" ", "T") + "Z") : 0;
}

export class EfnKdsApp extends Component {
    static template = "efn_pos_kds.KdsApp";
    static components = { MainComponentsContainer };
    static props = [];

    setup() {
        this.info = session.efn_kds || {};
        this.orm = useService("orm");
        this.bus = useService("bus_service");
        this.state = useState({
            tickets: {},
            now: Date.now(),
            courseFilter: "all",
            dark: true,
            offline: false,
            busy: false,
        });
        this.offset = parseServer(this.info.server_time) - Date.now();
        this.lastSync = false;
        this.onBus = this.onBus.bind(this);
        this.onReconnect = () => this.load(true);

        onWillStart(async () => {
            await this.load(false);
        });
        onMounted(() => {
            if (this.info.access_token) {
                this.bus.addChannel(this.info.access_token);
                this.bus.subscribe(`${this.info.access_token}-EFN_KDS`, this.onBus);
                this.bus.addEventListener("BUS:CONNECT", this.onReconnect);
            }
            this.tick = setInterval(() => (this.state.now = Date.now()), TICK_MS);
            this.heartbeatTimer = setInterval(() => this.heartbeat(), HEARTBEAT_MS);
            this.heartbeat();
        });
        onWillUnmount(() => {
            clearInterval(this.tick);
            clearInterval(this.heartbeatTimer);
            if (this.info.access_token) {
                this.bus.unsubscribe(`${this.info.access_token}-EFN_KDS`, this.onBus);
                this.bus.removeEventListener("BUS:CONNECT", this.onReconnect);
            }
        });
    }

    // ------------------------------------------------------------------ data
    async load(incremental) {
        try {
            const res = await this.orm.call("efn.kds.station", "efn_kds_load", [
                [this.info.station_id],
                incremental ? this.lastSync : false,
            ]);
            this.offset = parseServer(res.server_time) - Date.now();
            const tickets = incremental ? { ...this.state.tickets } : {};
            let hasNew = false;
            for (const ticket of res.tickets) {
                if (!tickets[ticket.id] && ticket.state === "new") {
                    hasNew = true;
                }
                tickets[ticket.id] = ticket;
            }
            this.state.tickets = tickets;
            this.lastSync = res.server_time;
            this.state.offline = false;
            if (hasNew && incremental) {
                this.beep();
            }
        } catch {
            this.state.offline = true;
        }
    }

    async heartbeat() {
        try {
            const res = await this.orm.call("efn.kds.station", "efn_kds_heartbeat", [
                [this.info.station_id],
            ]);
            if (res.token_fp && this.info.token_fp && res.token_fp !== this.info.token_fp) {
                window.location.reload(); // token kanal dirotasi manager
                return;
            }
            this.state.offline = false;
            await this.load(true);
        } catch {
            this.state.offline = true;
        }
    }

    onBus() {
        this.load(true);
    }

    beep() {
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            osc.frequency.value = 880;
            osc.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + 0.15);
        } catch {
            // suara opsional
        }
    }

    // ------------------------------------------------------------------ tampilan
    get courses() {
        const set = new Set(Object.values(this.state.tickets).map((t) => t.course_index));
        return [...set].sort((a, b) => a - b);
    }

    ticketsFor(state) {
        return Object.values(this.state.tickets)
            .filter((t) => t.state === state)
            .filter(
                (t) =>
                    this.state.courseFilter === "all" ||
                    String(t.course_index) === String(this.state.courseFilter)
            )
            .sort((a, b) => parseServer(a.sent_at) - parseServer(b.sent_at) || a.id - b.id);
    }

    get recallable() {
        return Object.values(this.state.tickets)
            .filter((t) => t.state === "served")
            .sort((a, b) => parseServer(b.served_at) - parseServer(a.served_at))
            .slice(0, 5);
    }

    ageSeconds(ticket) {
        const end = ticket.ready_at ? parseServer(ticket.ready_at) : this.state.now + this.offset;
        return Math.max(0, Math.floor((end - parseServer(ticket.sent_at)) / 1000));
    }

    ageLabel(ticket) {
        const s = this.ageSeconds(ticket);
        const mm = String(Math.floor(s / 60)).padStart(2, "0");
        const ss = String(s % 60).padStart(2, "0");
        return `${mm}:${ss}`;
    }

    ageClass(ticket) {
        const minutes = this.ageSeconds(ticket) / 60;
        if (ticket.state === "ready") {
            return "efn-kds-ok";
        }
        if (minutes >= (this.info.late_minutes || 15)) {
            return "efn-kds-late";
        }
        if (minutes >= (this.info.warn_minutes || 10)) {
            return "efn-kds-warn";
        }
        return "efn-kds-ok";
    }

    ticketTitle(ticket) {
        return ticket.table_label || ticket.tracking_number || `#${ticket.id}`;
    }

    nextLabel(ticket) {
        return NEXT_LABEL[ticket.state];
    }

    // ------------------------------------------------------------------ aksi
    applyPayload(rows) {
        const tickets = { ...this.state.tickets };
        for (const row of rows || []) {
            tickets[row.id] = row;
        }
        this.state.tickets = tickets;
    }

    async bump(ticket, toState = NEXT_STATE[ticket.state]) {
        if (!toState || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const rows = await this.orm.call("efn.kds.ticket", "efn_kds_bump", [[ticket.id], toState]);
            this.applyPayload(rows);
        } finally {
            this.state.busy = false;
        }
    }

    async recall(ticket) {
        const back = ticket.state === "served" ? "ready" : "in_progress";
        await this.bump(ticket, back);
    }

    async lineDone(line) {
        if (line.state === "done" || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const rows = await this.orm.call("efn.kds.ticket.line", "efn_kds_line_done", [[line.id]]);
            this.applyPayload(rows);
        } finally {
            this.state.busy = false;
        }
    }

    toggleDark() {
        this.state.dark = !this.state.dark;
    }

    get columns() {
        return COLUMNS;
    }
}

whenReady(() => mountComponent(EfnKdsApp, document.body));
