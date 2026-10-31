/** @odoo-module **/
// G35: tampilan hasil Laporan Keuangan EFN + drill-down. Klik angka bertanda .efn-drill memanggil
// efn.financial.report.action_drill (cek akses + filter PT/periode di server), lalu membuka Journal Items.
import { Component, markup } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class EfnReportHtmlField extends Component {
    static template = "efn_account_reports.EfnReportHtmlField";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
    }

    get html() {
        return markup(String(this.props.record.data[this.props.name] || ""));
    }

    async onClick(ev) {
        const link = ev.target.closest("a.efn-drill");
        if (!link) {
            return;
        }
        ev.preventDefault();
        const accountIds = (link.dataset.efnAccounts || "")
            .split(",")
            .map((v) => parseInt(v, 10))
            .filter((v) => Number.isInteger(v));
        if (!accountIds.length) {
            return;
        }
        const record = this.props.record;
        if (!(await record.save())) {
            return;
        }
        const action = await this.orm.call(record.resModel, "action_drill", [
            [record.resId],
            accountIds,
            link.dataset.efnPeriod === "compare" ? "compare" : "current",
        ]);
        await this.action.doAction(action);
    }
}

registry.category("fields").add("efn_report_html", {
    component: EfnReportHtmlField,
    displayName: "Laporan Keuangan EFN",
    supportedTypes: ["html"],
});
