{
    "name": "EFN Loyalty Accounting",
    "summary": "Kebijakan Poin EFN per PT (empat mata, publikasi bertanda tangan ke BFF) + akrual liabilitas harian.",
    "version": "19.0.1.4.0",  # G31: outbox kebijakan dapat diwarisi (efn.mgm.policy.outbox)
    "category": "EFN/Accounting",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # G21 E-44 / W-29: kontrak docs/04 §13.1 + L-61/L-62 (menang atas docs/08 §11.4). ADR-020 (ledger poin tunggal di
    # BFF; Odoo hanya kebijakan + jurnal), ADR-001 revisi (PT penerbit per lot earn), ADR-031 (tanpa modul pihak
    # ketiga), ADR-027 (tanpa angka bisnis/akun di kode -> PLACEHOLDER configurable).
    "depends": [
        "account",
        "mail",
        "efn_core",
        "efn_audit",
        "efn_pos_loyalty_ledger",
    ],
    "data": [
        "security/efn_loyalty_groups.xml",
        "security/ir.model.access.csv",
        "security/efn_loyalty_security.xml",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "data/efn_audit_rule_data.xml",
        "views/efn_loyalty_policy_views.xml",
        "views/efn_loyalty_outbox_views.xml",
        "views/efn_loyalty_accrual_views.xml",
        "views/efn_loyalty_settlement_views.xml",
        "views/res_config_settings_views.xml",
        "views/efn_loyalty_menus.xml",
    ],
    "installable": True,
    "application": False,
}
