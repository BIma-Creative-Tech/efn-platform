{
    "name": "EFN PII + HR (K-a work contact, E-38a)",
    "summary": "ADR-036 §0.2 K-a: work contact karyawan (identitas kerja) dibuat tanpa penanda terlindungi lewat "
    "context server. Data pribadi karyawan (hr.employee/hr.version) = E-38b.",
    "version": "19.0.1.0.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031, CLAUDE.md).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["efn_pii", "hr"],
    "data": [],
    "auto_install": True,
    "installable": True,
    "application": False,
}
