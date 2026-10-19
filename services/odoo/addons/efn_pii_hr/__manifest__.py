{
    "name": "EFN PII + HR (data pribadi karyawan, E-38b)",
    "summary": "ADR-036 §0.2 kelas K: HP/email/alamat pribadi, NIK (blind index), paspor, kontak darurat, tempat lahir "
    "karyawan (hr.employee/hr.version) terenkripsi; work contact & nama karyawan tetap plaintext (K-a, 18-40).",
    "version": "19.0.2.0.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031, CLAUDE.md).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["efn_pii", "hr"],
    "data": [],
    "post_init_hook": "post_init_hook",
    "auto_install": True,
    "installable": True,
    "application": False,
}
