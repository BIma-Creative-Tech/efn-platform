{
    "name": "EFN Core",
    "summary": "Fondasi addons EFN: atribut multi-company, parameter sistem, klien HTTP ke BFF.",
    "version": "19.0.1.6.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031, CLAUDE.md).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Sengaja ramping: modul domain (efn_kitting -> mrp, efn_pos_otp_gate -> point_of_sale)
    # menarik dependensinya sendiri, sehingga efn_core tidak memaksa POS/MRP terpasang.
    "depends": [
        "base",
        "base_setup",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "views/res_company_views.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
    "installable": True,
    "application": False,
}
