{
    "name": "EFN MGM Policy - kebijakan member-get-member per PT",
    "summary": "K25-10 / ADR-009: L1 & L2 persen, L3+ reward flat, kedalaman maksimum wajib, cap per referrer, "
    "pengecualian kategori, masa tunggu settle; empat mata, read-only setelah disetujui.",
    "version": "19.0.1.2.0",  # G31b: _efn_mgm_excluded_category_ids untuk dasar MGM per baris (§20.10)
    "category": "EFN/Loyalty",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031). Kontrak: docs/04_SERVICE_CONTRACTS.md §19.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["product", "efn_audit", "efn_loyalty_accounting"],
    "data": [
        "security/efn_mgm_security.xml",
        "security/ir.model.access.csv",
        "data/efn_audit_rule_data.xml",
        "data/ir_cron.xml",
        "views/efn_mgm_policy_views.xml",
    ],
    "installable": True,
    "application": False,
}
