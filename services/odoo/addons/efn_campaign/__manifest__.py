{
    "name": "EFN Kampanye Tertarget (segmen + markdown, voucher, outbox ke BFF)",
    "summary": "G50 (K18-01, ADR-047 §3.4, docs/04 §28.7): usulan segmen dari segment-worker (tanpa data "
    "member), approval Marketing dengan pemisahan tugas, voucher loyalty CE per mode "
    "(anonymous_code/public_code/member_bound), outbox + klaim/ack untuk BFF lewat /json/2.",
    "version": "19.0.1.0.0",
    "category": "EFN/Marketing",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031): hanya CE + efn_*. Tidak bergantung pada modul migrasi
    # brand lama (efn_legacy_history, efn_mig_*, ESB) maupun efn_expiry_monitor (G45, belum ada di main).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["mail", "loyalty", "stock", "efn_core", "efn_audit"],
    "data": [
        "security/efn_campaign_security.xml",
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "data/efn_audit_rule_data.xml",
        "views/efn_campaign_views.xml",
    ],
    "installable": True,
    "application": False,
}
