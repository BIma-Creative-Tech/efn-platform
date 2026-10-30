"""Helper Poin EFN untuk pesanan online (bukan model) — G22 W-12, docs/04 §14.C.

Ledger BFF tetap satu-satunya saldo (ADR-020). Odoo hanya:
1. memvalidasi porsi Poin EFN saat place terhadap kebijakan PT yang **dipublikasikan** (``efn.loyalty.policy``,
   modul ``efn_loyalty_accounting``) + nilai tukar global (company ``EFN``, L-62) — pertahanan berlapis di atas BFF;
2. mencatat porsi itu sebagai pembayaran faktur SO di jurnal metode "Poin EFN" PT (``pos.payment.method`` ber-
   ``efn_is_loyalty_points``, modul ``efn_pos_loyalty_ledger``) — jurnal & akun yang sama dengan POS (K-18-6 RECON);
3. menghitung dasar earn ``eligible_amount`` (K-1/K-18-4) dan alokasi proporsional refund (Q-F18-4).

Kedua modul di atas **tidak** di ``depends`` (ritel online tanpa POS loyalty tetap bisa dipasang); dicek saat runtime.
Fail-closed: tanpa kebijakan PT/global yang dipublikasikan, atau tanpa metode/jurnal "Poin EFN" PT yang berakun
lengkap (L-79) -> porsi poin ditolak saat place (``points_invalid``). Modul ini tidak menulis konfigurasi akun.
Tidak ada angka bisnis di kode (ADR-027).
"""

import logging

from odoo import fields

_logger = logging.getLogger(__name__)

POLICY_MODEL = "efn.loyalty.policy"
GLOBAL_COMPANY_CODE = "EFN"
# Fallback flag earn K-18-4 bila kebijakan PT belum ada (sama dengan efn_pos_loyalty_ledger, default True).
EARN_EXCLUDE_POINTS_PARAM = "efn.pos.loyalty.earn_exclude_point_payment"
_TRUE = {"1", "true", "yes", "y", "on"}


def active_policy(env, company):
    """Kebijakan PT ``company`` yang berlaku sekarang: versi tertinggi ``published`` dengan ``effective_from <= now``
    (aturan BFF §13.1 no. 4). ``None`` bila modul kebijakan tidak terpasang / belum ada."""
    if not company or POLICY_MODEL not in env:
        return None
    now = fields.Datetime.now()
    domain = [("company_id", "=", company.id), ("state", "=", "published"), ("effective_from", "<=", now)]
    policy = env[POLICY_MODEL].sudo().search(domain, order="version desc", limit=1)
    return policy or None


def global_point_value(env):
    """``point_value_idr`` global (kebijakan company EFN yang berlaku, L-62) atau ``None``."""
    company = env["res.company"].sudo().search([("efn_brand_code", "=", GLOBAL_COMPANY_CODE)], limit=1)
    policy = active_policy(env, company) if company else None
    if not policy:
        return None
    value = int(policy.point_value_idr or 0)
    return value if value > 0 else None


def redeem_limits(env, company):
    """``{min_points, max_pct, point_value_idr}`` dari kebijakan PT + global, atau ``None`` (fail-closed)."""
    policy = active_policy(env, company)
    point_value = global_point_value(env)
    if not policy or not point_value:
        return None
    max_pct = int(policy.redeem_max_pct or 0)
    min_points = int(policy.redeem_min_points or 0)
    if not 1 <= max_pct <= 100 or min_points < 1:
        return None
    return {"min_points": min_points, "max_pct": max_pct, "point_value_idr": point_value}


def earn_on_money_only(env, company):
    """K-18-4 / 18-10: porsi Poin EFN tidak menghasilkan poin. Kebijakan PT bila ada; selain itu parameter POS
    (``efn.pos.loyalty.earn_exclude_point_payment``, default True)."""
    policy = active_policy(env, company)
    if policy is not None:
        return bool(policy.earn_on_money_only)
    raw = env["ir.config_parameter"].sudo().get_param(EARN_EXCLUDE_POINTS_PARAM, "True")
    return str(raw).strip().lower() in _TRUE


def excluded_category_ids(env):
    """K-2: kategori produk tanpa Poin EFN (``product.category.efn_loyalty_earn_excluded`` + turunannya). Kosong bila
    field tidak ada (efn_pos_loyalty_ledger tidak terpasang)."""
    Category = env["product.category"].sudo().with_context(active_test=False)
    if "efn_loyalty_earn_excluded" not in Category._fields:
        return set()
    flagged = Category.search([("efn_loyalty_earn_excluded", "=", True)])
    if not flagged:
        return set()
    return set(Category.search([("id", "child_of", flagged.ids)]).ids)


def mgm_excluded_category_ids(env, company, at):
    """G31b (docs/04 §20.10): kategori pengecualian kebijakan MGM PT pada ``at`` (efn_mgm_policy opsional, tanpa
    depends). ``None`` = tidak ada kebijakan/pengecualian -> dasar MGM tidak dikirim (BFF memakai dasar earn)."""
    if "efn.mgm.policy" not in env:
        return None
    return env["efn.mgm.policy"]._efn_mgm_excluded_category_ids(company, at)


def eligible_subtotal(lines, excluded):
    """Σ subtotal TANPA pajak baris produk yang berhak poin (baris reward promo ikut, mengurangi dasar; K-1)."""
    total = 0.0
    for line in lines:
        product = line.product_id
        if not product or product.categ_id.id in excluded:
            continue
        total += line.price_subtotal
    return total


def points_method(env, company):
    """Metode POS "Poin EFN" aktif milik ``company`` (sumber jurnal & akun liabilitas) atau ``None``."""
    if "pos.payment.method" not in env:
        return None
    Method = env["pos.payment.method"].sudo()
    if "efn_is_loyalty_points" not in Method._fields:
        return None
    return Method.search(
        [("efn_is_loyalty_points", "=", True), ("company_id", "=", company.id), ("active", "=", True)], limit=1
    )


def points_journal_raw(env, company):
    """Jurnal metode "Poin EFN" PT ``company`` (tanpa cek akun) atau jurnal kosong."""
    method = points_method(env, company)
    if not method:
        return env["account.journal"].browse()
    journal = method.journal_id.sudo()
    if not journal or journal.company_id != company:
        return env["account.journal"].browse()
    return journal


def points_journal(env, company):
    """Jurnal pembayaran Poin EFN PT ``company`` yang SIAP dipakai, atau jurnal kosong.

    Siap = metode punya akun liabilitas (``outstanding_account_id``) DAN semua baris metode masuk & keluar jurnal punya
    ``payment_account_id``: Odoo 19 tidak membuat jurnal akuntansi untuk ``account.payment`` tanpa akun outstanding
    (``_generate_journal_entry``, [V] source 19.0). L-79: modul ini TIDAK mengisi akun saat runtime; akun diisi data
    ``efn_setup_pos`` (``_efn_setup_points_journal_accounts``) atau Finance."""
    method = points_method(env, company)
    journal = points_journal_raw(env, company)
    if not journal or not method.outstanding_account_id:
        return env["account.journal"].browse()
    inbound = journal.inbound_payment_method_line_ids
    outbound = journal.outbound_payment_method_line_ids
    if not inbound or not outbound or (inbound | outbound).filtered(lambda line: not line.payment_account_id):
        return env["account.journal"].browse()
    return journal
