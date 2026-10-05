{
    "name": "EFN Setup - Keamanan Login",
    "summary": "Konfigurasi data-only: 2FA wajib untuk user internal (auth_totp_mail) dan panjang minimum password.",
    "version": "19.0.1.0.0",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # CLAUDE.md (auth_totp untuk user internal), Q-54 (kebijakan password/SSO), R-S03.
    # depends terverifikasi di odoo/odoo 19.0 (2026-10-05). auth_totp_mail_enforce = 404 di 19.0;
    # kebijakan wajib 2FA ada di auth_totp_mail (param 'auth_totp.policy').
    "depends": [
        "auth_totp",
        "auth_totp_mail",
        "auth_password_policy",
    ],
    "data": [
        "data/ir_config_parameter.xml",
    ],
    "installable": True,
    "application": False,
}
