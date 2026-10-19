"""E-40: selisih PPN Coretax vs jurnal Odoo -> jurnal penyesuaian per faktur (docs/08 §11.2 butir 2-5).

Angka Coretax TIDAK dihitung ulang di sini: setiap baris memanggil
``account.move.line._l10n_id_coretax_build_invoice_line_vals`` (l10n_id_efaktur_coretax 19.0) dan memakai ``VAT``
hasilnya. ``d = Σ VAT Coretax - Σ PPN jurnal Odoo`` (mata uang IDR).

- ``d > 0``: Dr akun Selisih Pembulatan Pajak / Cr akun PPN Keluaran; ``d < 0``: Dr PPN Keluaran / Cr akun selisih
  (lebih). Baris PPN membawa ``tax_tag_ids`` baris repartisi PPN faktur -> laporan pajak berbasis tag = Σ VAT XML.
  Faktur (piutang, total) **tidak** disentuh.
- Pemicu: setelah posting faktur ``out_invoice`` yang ``l10n_id_coretax_efaktur_available`` (best effort: gagal ->
  log, posting faktur tetap jalan) dan sebelum XML e-Faktur dibuat/di-regenerate (``_generate_xml``; ketat: gagal ->
  ``UserError``, XML tidak dibuat).
- Idempoten: selisih sama -> tidak ada jurnal baru; berubah (mis. kode transaksi diubah sebelum ekspor) -> jurnal
  lama di-reverse + jurnal baru. Faktur di-reset ke draft -> jurnal penyesuaian di-reverse.
- Pengaman: ``|d| <= jumlah baris × Rp1``; di luar itu ``UserError`` (data salah, bukan pembulatan).
- Periode terkunci (fiscal/tax/sale/hard lock date) -> ditolak (``UserError``), tidak ada jurnal di tanggal lain.
- Di luar cakupan (lihat README): kode transaksi 07/08 (PPN tidak dipungut/dibebaskan), baris tarif 0/bebas,
  ``out_refund`` (**[B]** perilaku Coretax belum diverifikasi), faktur bukan IDR.

Tidak ada metode publik baru (gerbang RPC SEC-58): hook privat ``_post``/``_efn_*`` + override ORM ``write``.
"""

import logging

from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command

_logger = logging.getLogger(__name__)

LOG_TAG = "EFN_CORETAX_ROUNDING"
# Kode transaksi yang tidak dijembatani (PPN tidak dipungut / dibebaskan: VAT XML bukan PPN terutang di jurnal).
SKIPPED_TRX_CODES = ("07", "08")
LOCK_DATE_FIELDS = ("fiscalyear_lock_date", "tax_lock_date", "sale_lock_date", "hard_lock_date")
RUPIAH = 1.0  # batas pengaman per baris (Rp1)


class _CoretaxSkip(Exception):
    """Faktur tidak dijembatani (bukan galat): kode alasan saja, tanpa data faktur."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class AccountMove(models.Model):
    _inherit = "account.move"

    efn_coretax_adjustment_move_id = fields.Many2one(
        "account.move",
        string="Jurnal Pembulatan PPN Coretax",
        readonly=True,
        copy=False,
        index="btree_not_null",
        check_company=True,
        help="E-40: jurnal penyesuaian selisih PPN Coretax - Odoo untuk faktur ini.",
    )
    efn_coretax_source_move_id = fields.Many2one(
        "account.move",
        string="Faktur Sumber (pembulatan Coretax)",
        readonly=True,
        copy=False,
        index="btree_not_null",
        check_company=True,
    )
    efn_coretax_rounding_diff = fields.Monetary(
        string="Selisih PPN Coretax - Odoo",
        currency_field="company_currency_id",
        readonly=True,
        copy=False,
    )

    # ------------------------------------------------------------------ hitung (tanpa tulis)
    def _efn_coretax_vat_groups(self):
        """(grup PPN terutang, grup tarif 0/bebas) dari chart template company — xmlid sama dengan yang dipakai
        ``_l10n_id_coretax_build_invoice_line_vals`` 19.0 (``default_tax_group``, ``l10n_id_tax_group_*``)."""
        self.ensure_one()
        chart = self.env["account.chart.template"].with_company(self.company_id)
        empty = self.env["account.tax.group"]

        def ref(xmlid):
            return chart.ref(xmlid, raise_if_not_found=False) or empty

        vat_groups = (
            ref("default_tax_group") | ref("l10n_id_tax_group_non_luxury_goods") | ref("l10n_id_tax_group_luxury_goods")
        )
        zero_groups = ref("l10n_id_tax_group_0") | ref("l10n_id_tax_group_exempt")
        return vat_groups, zero_groups

    def _efn_coretax_compute(self):
        """:returns: dict ``diff`` (Coretax - Odoo, IDR), ``lines`` (jumlah baris dijembatani), ``coretax``,
        ``odoo``, ``tax_lines`` (baris PPN jurnal faktur). :raises _CoretaxSkip: faktur tidak dijembatani."""
        self.ensure_one()
        idr = self.env.ref("base.IDR")
        if self.currency_id != idr or self.company_currency_id != idr:
            raise _CoretaxSkip("currency")
        if self.l10n_id_kode_transaksi in SKIPPED_TRX_CODES:
            raise _CoretaxSkip("trx_code")
        vat_groups, zero_groups = self._efn_coretax_vat_groups()
        if not vat_groups:
            raise _CoretaxSkip("tax_groups")
        coretax = 0.0
        count = 0
        # Filter baris = prepare_efaktur_vals 19.0 (invoice_line_ids dengan tax_ids).
        for line in self.invoice_line_ids.filtered(lambda aml: aml.tax_ids):
            groups = line.tax_ids.tax_group_id
            if not (groups & vat_groups) or (groups & zero_groups):
                continue
            vals = {}
            try:
                line._l10n_id_coretax_build_invoice_line_vals(vals)
            except (UserError, ValidationError):
                # mis. subtotal negatif (baris diskon): faktur memang tidak bisa diekspor; Odoo sendiri menolak XML.
                raise _CoretaxSkip("line_not_exportable") from None
            coretax += float(vals["lines"][-1]["VAT"])
            count += 1
        tax_lines = self.line_ids.filtered(
            lambda aml: aml.display_type == "tax" and aml.tax_line_id.tax_group_id & vat_groups
        )
        odoo = -sum(tax_lines.mapped("balance"))  # out_invoice: PPN keluaran di kredit
        return {
            "diff": idr.round(coretax - odoo),
            "lines": count,
            "coretax": idr.round(coretax),
            "odoo": idr.round(odoo),
            "tax_lines": tax_lines,
        }

    # ------------------------------------------------------------------ sinkron
    def _efn_coretax_sync_rounding(self, strict):
        """Buat/perbarui jurnal penyesuaian untuk setiap faktur. ``strict`` (ekspor XML): galat -> ``UserError``.
        Non-strict (posting faktur): galat dicatat (kode saja), faktur tetap terposting; ekspor nanti ketat."""
        for move in self:
            try:
                with self.env.cr.savepoint():
                    move._efn_coretax_sync_one()
            except _CoretaxSkip as skip:
                self.env.invalidate_all()
                _logger.info("%s move=%s skipped (%s)", LOG_TAG, move.id, skip.code)
            except UserError as exc:
                self.env.invalidate_all()
                if strict:
                    raise
                _logger.warning("%s move=%s not adjusted at posting (%s)", LOG_TAG, move.id, type(exc).__name__)

    def _efn_coretax_sync_one(self):
        self.ensure_one()
        if self.state != "posted" or self.move_type != "out_invoice" or not self.l10n_id_coretax_efaktur_available:
            raise _CoretaxSkip("not_applicable")
        existing = self.efn_coretax_adjustment_move_id
        if existing and existing.state != "posted":
            existing = self.env["account.move"]  # dibatalkan/di-draft manual: dianggap tidak ada
        try:
            result = self._efn_coretax_compute()
        except _CoretaxSkip:
            if not existing:
                raise
            # Faktur kini di luar cakupan (mis. kode transaksi diubah ke 07): penyesuaian lama dibatalkan.
            result = {"diff": 0.0, "lines": 0, "tax_lines": self.env["account.move.line"]}
        idr = self.env.ref("base.IDR")
        diff = result["diff"]
        current = existing and self.efn_coretax_rounding_diff
        if existing and idr.compare_amounts(current, diff) == 0:
            return  # idempoten: ekspor ulang tanpa perubahan
        if not existing and idr.is_zero(diff):
            if not idr.is_zero(self.efn_coretax_rounding_diff):
                self.sudo().write({"efn_coretax_rounding_diff": 0.0})
            return
        if abs(diff) > result["lines"] * RUPIAH + idr.rounding / 2:
            raise UserError(
                _(
                    "Selisih PPN Coretax dan jurnal Odoo faktur %(name)s (Rp%(diff)s) melebihi batas pembulatan "
                    "%(lines)s baris × Rp1. Periksa kode transaksi, pajak, dan baris faktur (bukan pembulatan).",
                    name=self.name,
                    diff=diff,
                    lines=result["lines"],
                )
            )
        company = self.company_id
        config = company._efn_tax_rounding_accounts()
        if not config:
            if existing or not idr.is_zero(diff):
                raise UserError(
                    _(
                        "Akun & jurnal Selisih Pembulatan Pajak untuk %(company)s belum diatur "
                        "(Pengaturan > Akuntansi > Pembulatan PPN Coretax).",
                        company=company.name,
                    )
                )
            return
        self._efn_coretax_check_lock(self.date)
        if existing:
            self._efn_coretax_check_lock(existing.date)
        adjustment = self.env["account.move"]
        if not idr.is_zero(diff):
            adjustment = self._efn_coretax_new_adjustment(diff, result["tax_lines"], config)
        if existing:
            self._efn_coretax_reverse(existing)
        self.sudo().write({"efn_coretax_adjustment_move_id": adjustment.id or False, "efn_coretax_rounding_diff": diff})
        _logger.info(
            "%s move=%s diff=%s lines=%s adjustment=%s replaced=%s",
            LOG_TAG,
            self.id,
            diff,
            result["lines"],
            adjustment.id or None,
            existing.id or None,
        )

    def _efn_coretax_check_lock(self, date):
        company = self.company_id
        for fname in LOCK_DATE_FIELDS:
            lock = company[fname] if fname in company._fields else False
            if lock and date and date <= lock:
                raise UserError(
                    _(
                        "Periode %(date)s terkunci untuk %(company)s: jurnal pembulatan PPN Coretax faktur %(name)s "
                        "tidak dapat dibuat/dibatalkan.",
                        date=date,
                        company=company.name,
                        name=self.name,
                    )
                )

    def _efn_coretax_new_adjustment(self, diff, tax_lines, config):
        self.ensure_one()
        expense_account, gain_account, journal = config
        if not tax_lines:
            raise UserError(_("Faktur %s tidak memiliki baris PPN untuk disesuaikan.", self.name))
        # Selisih ditempatkan pada baris PPN terbesar (akun + tag repartisi faktur itu).
        tax_line = max(tax_lines, key=lambda aml: abs(aml.balance))
        amount = abs(diff)
        label = _("Selisih pembulatan PPN Coretax %s", self.name)
        ppn = {
            "name": label,
            "account_id": tax_line.account_id.id,
            "tax_tag_ids": [Command.set(tax_line.tax_tag_ids.ids)],
        }
        if diff > 0:
            lines = [
                {"name": label, "account_id": expense_account.id, "debit": amount, "credit": 0.0},
                {**ppn, "debit": 0.0, "credit": amount},
            ]
        else:
            lines = [
                {**ppn, "debit": amount, "credit": 0.0},
                {"name": label, "account_id": gain_account.id, "debit": 0.0, "credit": amount},
            ]
        company = self.company_id
        adjustment = (
            self.env["account.move"]
            .sudo()
            .with_company(company)
            .create(
                {
                    "move_type": "entry",
                    "company_id": company.id,
                    "journal_id": journal.id,
                    "date": self.date,
                    "ref": f"EFN-CTX-RND {self.name}",
                    "efn_coretax_source_move_id": self.id,
                    "line_ids": [Command.create(vals) for vals in lines],
                }
            )
        )
        adjustment._post(soft=False)
        return adjustment

    def _efn_coretax_reverse(self, adjustment):
        reversal = adjustment.sudo()._reverse_moves(
            [{"date": adjustment.date, "ref": _("Batal %s", adjustment.ref or adjustment.name)}]
        )
        reversal._post(soft=False)
        return reversal

    # ------------------------------------------------------------------ hook
    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        invoices = posted.filtered(lambda m: m.move_type == "out_invoice" and m.l10n_id_coretax_efaktur_available)
        if invoices:
            invoices._efn_coretax_sync_rounding(strict=False)
        return posted

    def write(self, vals):
        """Faktur di-reset ke draft -> jurnal penyesuaian di-reverse (ditolak bila periodenya terkunci)."""
        to_reverse = self.browse()
        if vals.get("state") == "draft":
            to_reverse = self.filtered(
                lambda m: (
                    m.state == "posted"
                    and m.efn_coretax_adjustment_move_id
                    and m.efn_coretax_adjustment_move_id.state == "posted"
                )
            )
            for move in to_reverse:
                move._efn_coretax_check_lock(move.efn_coretax_adjustment_move_id.date)
        result = super().write(vals)
        for move in to_reverse:
            adjustment = move.efn_coretax_adjustment_move_id
            move._efn_coretax_reverse(adjustment)
            move.sudo().write({"efn_coretax_adjustment_move_id": False, "efn_coretax_rounding_diff": 0.0})
            _logger.info("%s move=%s reset to draft, adjustment=%s reversed", LOG_TAG, move.id, adjustment.id)
        return result
