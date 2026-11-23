# G56 — Beranda EFN (home console) + gaya ringan halaman login.
# ADR-049 (port kode odoo-platform milik pemilik proyek): diport dari `core/custom_home_console` 19.0.1.0.0,
# lihat README.md bagian Provenance. ADR-031: tanpa dependency modul pihak ketiga; tanpa aset Enterprise.
{
    "name": "EFN Beranda (Home Console)",
    "summary": "Halaman beranda pasca-login: kartu aplikasi per kelompok, pencarian, pintasan, branding per PT; "
    "gaya ringan halaman login.",
    "version": "19.0.1.0.0",
    "category": "EFN",
    "author": "EFN",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["web", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "views/home_action.xml",
        "views/res_company_views.xml",
        "views/res_users_views.xml",
        "views/menu_views.xml",
        "views/login_templates.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "efn_web_home/static/src/home/home.scss",
            "efn_web_home/static/src/home/recent_service.js",
            "efn_web_home/static/src/home/spotlight_search.js",
            "efn_web_home/static/src/home/spotlight_search.xml",
            "efn_web_home/static/src/home/home.js",
            "efn_web_home/static/src/home/home.xml",
            "efn_web_home/static/src/home/navbar_patch.js",
            "efn_web_home/static/src/home/navbar_patch.xml",
        ],
        "web.assets_frontend": [
            "efn_web_home/static/src/login/login.scss",
        ],
    },
    "post_init_hook": "_efn_post_init_set_default_home",
    "uninstall_hook": "_efn_uninstall_clear_default_home",
    "installable": True,
    "application": False,
}
