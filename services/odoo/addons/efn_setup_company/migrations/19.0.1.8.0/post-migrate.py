"""G24 W-40 (docs/04 §16.4): DB lama yang di-upgrade ke 19.0.1.8.0 - lengkapi company WTZ sekali.

Saat upgrade Odoo melewati ``<function>`` di blok ``noupdate="1"`` dan record ``base.user_admin`` (xmlid noupdate),
sehingga label pajak template (``account_tax_label_data.xml``), pembulatan (``account_tax_rounding_data.xml``), dan
akses admin (``res_company_data.xml``) yang berlaku untuk install baru tidak diterapkan ke ``company_wtz``. Skrip ini
menerapkan nilai yang SAMA hanya untuk company WTZ, sekali (migrasi berjalan sekali per versi). Company lain tidak
disentuh.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# Sama dengan data/account_tax_label_data.xml (install baru).
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


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    company = env.ref("efn_setup_company.company_wtz", raise_if_not_found=False)
    if not company:
        return
    for names, label in TAX_LABELS.items():
        for name in names:
            tax = env.ref(f"account.{company.id}_{name}", raise_if_not_found=False)
            if tax:
                tax.invoice_label = label
    for name, label in GROUP_NAMES.items():
        group = env.ref(f"account.{company.id}_{name}", raise_if_not_found=False)
        if group:
            group.name = label
    company.tax_calculation_rounding_method = "round_globally"
    admin = env.ref("base.user_admin", raise_if_not_found=False)
    if admin and company not in admin.company_ids:
        admin.company_ids = [(4, company.id)]
    _logger.info("EFN setup company G24: company WTZ dilengkapi (label pajak, pembulatan, akses admin)")
