{
    "name": "EFN Core",
    "summary": "Fondasi addons EFN: atribut multi-company, parameter sistem, klien HTTP ke BFF.",
    # 19.0.1.7.0 (G20 W-32/E-43): kode company EFN/GL/PB/BCR/BCF/FARM (docs/04 §12.1).
    "version": "19.0.1.7.0",
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
