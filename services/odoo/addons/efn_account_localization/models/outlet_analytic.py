"""G60-C: helper analitik Outlet (fungsi murni + cap baris laba-rugi).

Ditulis ulang dari ide `_levis_merge_ou_distribution` (odoo-platform
`custom_levis_localization/models/purchase_order.py`), dengan perbedaan: kunci yang SUDAH memuat akun dari plan Outlet tidak ditambah lagi (plan-aware), dan hanya baris
laba-rugi (``internal_group`` income/expense) yang dicap (Q-G60-8: cukup laba-rugi).
"""

PNL_GROUPS = ("income", "expense")


def merge_outlet_distribution(distribution, outlet_id, plan_account_ids):
    """Tambahkan ``outlet_id`` ke setiap kunci ``analytic_distribution``.

    Kunci distribusi = id akun analitik dipisah koma (satu per plan) -> persen. Kunci yang sudah memuat akun plan
    Outlet (``plan_account_ids``) dibiarkan; distribusi kosong -> ``{outlet: 100}``.
    """
    outlet = str(outlet_id)
    if not distribution:
        return {outlet: 100.0}
    merged = {}
    for key, percent in distribution.items():
        ids = [part.strip() for part in str(key).split(",") if part.strip()]
        if not plan_account_ids.intersection(int(part) for part in ids):
            ids.append(outlet)
        new_key = ",".join(ids)
        merged[new_key] = merged.get(new_key, 0.0) + percent
    return merged


def stamp_outlet(lines, outlet):
    """Cap ``outlet`` (account.analytic.account) ke baris laba-rugi ``lines`` (account.move.line, move draft).

    Distribusi bawaan (distribution model produk/akun, plan lain) dibaca dulu (compute CE) lalu digabung, bukan
    ditimpa. :returns: baris yang dicap.
    """
    pnl = lines.filtered(
        lambda line: line.account_id.internal_group in PNL_GROUPS
        and line.display_type not in ("line_section", "line_note")
    )
    if not outlet or not pnl:
        return pnl.browse()
    Account = lines.env["account.analytic.account"].with_context(active_test=False)
    plan_account_ids = set(Account.search([("root_plan_id", "=", outlet.root_plan_id.id)]).ids)
    for line in pnl:
        line.analytic_distribution = merge_outlet_distribution(line.analytic_distribution, outlet.id, plan_account_ids)
    return pnl
