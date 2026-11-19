"""G43 (keputusan user 18 Nov 2026): DB lama yang di-upgrade ke 19.0.1.9.0 - lengkapi company IMD & CUP sekali.

Pola sama dengan ``migrations/19.0.1.8.0/post-migrate.py`` (WTZ, G24). Saat upgrade Odoo melewati ``<function>`` di blok
``noupdate="1"`` dan record ``base.user_admin`` (xmlid noupdate), sehingga label pajak template
(``account_tax_label_data.xml``), pembulatan (``account_tax_rounding_data.xml``), dan akses admin
(``res_company_data.xml``) yang berlaku untuk install baru tidak diterapkan ke ``company_imd``/``company_cup``. Skrip
ini menerapkan nilai yang SAMA hanya untuk kedua company itu. Company lain tidak disentuh.

Idempoten: setiap langkah menulis nilai tetap (bukan menambah), jadi aman dijalankan ulang (diuji
``tests/test_g43_companies.py``). CoA dan PB1 default sudah dipasang blok ``noupdate="0"`` di data XML sebelum
skrip post ini berjalan. Data PT (nama legal, NPWP, alamat) tetap placeholder (Q-ESB5): tidak diisi di sini.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

COMPANY_XMLIDS = ("efn_setup_company.company_imd", "efn_setup_company.company_cup")

# Sama dengan data/account_tax_label_data.xml (install baru) dan migrations/19.0.1.8.0.
TAX_LABELS = {
    ("tax_ST4", "tax_ST5"): "PPN 11%",
    ("tax_ST3", "tax_ST6"): "PPN 12%",
    ("tax_luxury_sales", "tax_luxury_sales_pemungut_ppn"): "PPnBM 20%",
    ("tax_ST0", "tax_ST7"): "PPN 0%",
    ("tax_ST2",): "Bebas PPN",
}
GROUP_NAMES = {
    "l10n_id_tax_group_non_luxury_goods": "PPN 11%",
    "l10n_id_tax_group_luxury_goods": "PPN 12%",
    "l10n_id_tax_group_stlg": "PPnBM",
    "l10n_id_tax_group_0": "PPN 0%",
    "l10n_id_tax_group_exempt": "Bebas PPN",
    "default_tax_group": "Pajak",
}


def apply_g43_company_defaults(env):
    """Terapkan label pajak, pembulatan, dan akses admin ke IMD/CUP. Mengembalikan recordset company yang diproses."""
    companies = env["res.company"]
    for xmlid in COMPANY_XMLIDS:
        company = env.ref(xmlid, raise_if_not_found=False)
        if company:
            companies |= company
    admin = env.ref("base.user_admin", raise_if_not_found=False)
    for company in companies:
        for names, label in TAX_LABELS.items():
            for name in names:
                tax = env.ref(f"account.{company.id}_{name}", raise_if_not_found=False)
                if tax and tax.invoice_label != label:
                    tax.invoice_label = label
        for name, label in GROUP_NAMES.items():
            group = env.ref(f"account.{company.id}_{name}", raise_if_not_found=False)
            if group and group.name != label:
                group.name = label
        if company.tax_calculation_rounding_method != "round_globally":
            company.tax_calculation_rounding_method = "round_globally"
        if admin and company not in admin.company_ids:
            admin.company_ids = [(4, company.id)]
    return companies


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    companies = apply_g43_company_defaults(env)
    _logger.info(
        "EFN setup company G43: %d company (IMD/CUP) dilengkapi (label pajak, pembulatan, akses admin)", len(companies)
    )
