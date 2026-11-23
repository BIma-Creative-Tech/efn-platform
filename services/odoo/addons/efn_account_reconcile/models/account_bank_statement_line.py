"""Pencocokan baris mutasi bank (CE 19 tidak punya mesin/widget rekonsiliasi bank — Enterprise).

Port ADR-049 dari odoo-platform `custom_account_reconcile/models/account_bank_statement_line.py` + ide generik
`custom_levis_bank_reconcile` (G58 §3.1, §3.3). Perubahan EFN:
- semua method non-UI privat (`_efn_*`); tombol (`action_efn_*`) memeriksa grup Accounting/Bookkeeper,
  `check_access('write')`, dan company aktif sebelum apa pun (sumber tanpa cek grup -> user Billing bisa
  merekonsiliasi lewat RPC);
- kandidat tidak boleh bertanggal setelah tanggal mutasi (parameter `efn_account_reconcile.date_ceiling`, default 1);
- akun bertanda `efn_no_auto_match` (setoran tunai) tidak pernah dicocokkan otomatis;
- write-off berupa DAFTAR baris (bisa dari `account.reconcile.model` CE: true-up MDR G60 D-5), bukan satu dict.
Log: tidak ada isi `payment_ref`/nama partner yang ditulis ke logger.
"""

import re

from odoo import Command, _, models
from odoo.exceptions import AccessError, UserError

RECONCILE_GROUP = "account.group_account_user"
DATE_CEILING_PARAM = "efn_account_reconcile.date_ceiling"
_FALSE_VALUES = ("0", "false", "no", "off")


def _efn_normalize_number(text):
    """'3358,07' -> '3358.07'; '1.234.567,89' -> '1234567.89'; '10.000' / '10,000' -> '10000'."""
    text = re.sub(r"[^\d,.]", "", text or "")
    if "," in text and "." in text:
        dec = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if dec == "," else ","
        return text.replace(thousands, "").replace(dec, ".")
    for sep in (",", "."):
        if sep in text:
            head, _sep, tail = text.rpartition(sep)
            if text.count(sep) == 1 and len(tail) == 2:
                return f"{head}.{tail}"
            return text.replace(sep, "")
    return text


def _efn_amount_from_label(pattern, label):
    """Nominal positif dari narasi mutasi memakai regex baris model rekonsiliasi (amount_type='regex'), atau None.

    Dua grup tangkap = bagian bulat + desimal (sesuai bantuan field CE `amount_string`); satu grup = angka dengan
    pemisah ribuan/desimal (lihat `_efn_normalize_number`)."""
    if not (pattern and label):
        return None
    try:
        match = re.search(pattern, label)
    except re.error:
        return None
    if not match:
        return None
    groups = [g for g in match.groups() if g is not None]
    if len(groups) >= 2:
        integer = re.sub(r"\D", "", groups[0]) or "0"
        decimal = re.sub(r"\D", "", groups[1]) or "0"
        raw = f"{integer}.{decimal}"
    else:
        raw = _efn_normalize_number(groups[0] if groups else match.group(0))
    try:
        return abs(float(raw))
    except ValueError:
        return None


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    # ------------------------------------------------------------------ akses
    def _efn_check_reconcile_access(self):
        """Grup Accounting/Bookkeeper + hak tulis (ACL + record rule) + company baris aktif di sesi user."""
        if not self.env.user.has_group(RECONCILE_GROUP):
            raise AccessError(_("Hanya Accounting / Bookkeeper yang dapat merekonsiliasi mutasi bank."))
        self.check_access("write")
        if self.company_id - self.env.companies:
            raise AccessError(_("Mutasi bank ini milik company yang tidak aktif di sesi Anda."))

    # ------------------------------------------------------------------ kandidat
    def _efn_date_ceiling_enabled(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(DATE_CEILING_PARAM, "1")
        return str(raw).strip().lower() not in _FALSE_VALUES

    def _efn_matching_domain(self, relax=False):
        """Domain kandidat: domain CE (`_get_default_amls_matching_domain`) + batas tanggal mutasi + arah nominal.

        ``relax`` (tombol "Cari lagi") membuang filter arah, tetapi batas tanggal tetap berlaku."""
        self.ensure_one()
        domain = list(self._get_default_amls_matching_domain())
        if self._efn_date_ceiling_enabled():
            domain.append(("date", "<=", self.date))
        if not relax:
            domain.append(("amount_residual", ">", 0) if self.amount > 0 else ("amount_residual", "<", 0))
        return domain

    def _efn_get_match_candidates(self, limit=30, relax=False):
        """Item jurnal terbuka yang bisa dilunasi baris ini, diurutkan skor.

        Skor: residual persis (+100), partner sama (+50), nomor entri ada di narasi (+40), label ada di narasi (+20),
        kedekatan nominal (0..10). Pencarian memakai hak user (record rule company CE berlaku)."""
        self.ensure_one()
        candidates = self.env["account.move.line"].search(
            self._efn_matching_domain(relax=relax), limit=limit * 10, order="date desc, id desc"
        )
        target = abs(self.amount)
        comp_cur = self.company_id.currency_id
        ref = (self.payment_ref or "").strip().lower()

        def score(aml):
            value = 0.0
            if not comp_cur.compare_amounts(abs(aml.amount_residual), target):
                value += 100.0
            if self.partner_id and aml.partner_id == self.partner_id:
                value += 50.0
            if ref and aml.move_id.name and aml.move_id.name.lower() in ref:
                value += 40.0
            label = (aml.name or "").strip().lower()
            if ref and label and label in ref:
                value += 20.0
            if target:
                value += max(0.0, 10.0 - abs(abs(aml.amount_residual) - target) / target * 10.0)
            return value

        return candidates.sorted(key=score, reverse=True)[:limit]

    def _efn_get_auto_match_candidate(self):
        """Satu kandidat tak-ambigu (residual persis, partner cocok bila baris punya partner), atau kosong.

        Kosong juga bila kandidat ada di akun `efn_no_auto_match` (setoran tunai = gabungan beberapa hari)."""
        self.ensure_one()
        comp_cur = self.company_id.currency_id
        target = abs(self.amount)
        exact = self._efn_get_match_candidates(limit=10).filtered(
            lambda aml: (
                not comp_cur.compare_amounts(abs(aml.amount_residual), target)
                and (not self.partner_id or aml.partner_id == self.partner_id)
            )
        )
        if len(exact) != 1 or exact.account_id.efn_no_auto_match:
            return exact.browse()
        return exact

    # ------------------------------------------------------------------ model rekonsiliasi (true-up MDR)
    def _efn_reconcile_model_writeoffs(self, reconcile_model, remainder):
        """Baris write-off dari `account.reconcile.model` CE atas sisa selisih ``remainder`` (saldo mata uang company,
        tanda = arah baris write-off).

        Mesin penerapan model di CE 19 tidak ada (widget Enterprise), jadi diterapkan di sini (G58 §3.3, G60 D-5):
        - `percentage`: persen dari sisa selisih saat baris itu diproses;
        - `percentage_st_line`: persen dari nominal mutasi (mata uang company);
        - `fixed`: nominal tetap (nilai absolut; arah mengikuti sisa selisih);
        - `regex`: nominal diambil dari narasi mutasi (`payment_ref`); tidak cocok -> baris dilewati.
        Tidak pernah melebihi sisa selisih dan tidak pernah berlawanan arah; sisa yang tidak terserap tetap di suspense.
        Baris model dengan pajak ditolak (belum didukung — fail-closed)."""
        self.ensure_one()
        comp_cur = self.company_id.currency_id
        if reconcile_model.company_id != self.company_id:
            raise UserError(_("Model rekonsiliasi milik company lain."))
        if reconcile_model.match_journal_ids and self.journal_id not in reconcile_model.match_journal_ids:
            raise UserError(_("Model rekonsiliasi %s tidak berlaku untuk jurnal ini.", reconcile_model.display_name))
        residual = comp_cur.round(remainder)
        if comp_cur.is_zero(residual):
            return []
        sign = 1.0 if residual > 0 else -1.0
        st_company_amount = abs(sum(self._seek_for_lines()[0].mapped("balance")))
        result = []
        for line in reconcile_model.line_ids:
            if comp_cur.is_zero(residual):
                break
            if not line.account_id:
                continue
            if line.tax_ids:
                raise UserError(
                    _("Baris model rekonsiliasi dengan pajak belum didukung (%s).", reconcile_model.display_name)
                )
            if line.amount_type == "percentage":
                balance = residual * line.amount / 100.0
            elif line.amount_type == "percentage_st_line":
                balance = sign * st_company_amount * abs(line.amount) / 100.0
            elif line.amount_type == "fixed":
                balance = sign * abs(line.amount)
            elif line.amount_type == "regex":
                extracted = _efn_amount_from_label(line.amount_string, self.payment_ref)
                if extracted is None:
                    continue
                balance = sign * extracted
            else:
                continue
            balance = comp_cur.round(balance)
            if comp_cur.compare_amounts(abs(balance), abs(residual)) > 0:
                balance = residual
            if comp_cur.is_zero(balance) or (balance > 0) != (residual > 0):
                continue
            vals = {
                "account_id": line.account_id.id,
                "name": line.label or reconcile_model.name,
                "balance": balance,
                "partner_id": line.partner_id.id or self.partner_id.id,
                "reconcile_model_id": reconcile_model.id,
            }
            if line.analytic_distribution:
                vals["analytic_distribution"] = line.analytic_distribution
            result.append(vals)
            residual = comp_cur.round(residual - balance)
        return result

    # ------------------------------------------------------------------ mekanika rekonsiliasi
    def _efn_reconcile_with_amls(self, amls, writeoff_vals_list=None):
        """Ganti kaki suspense baris ini dengan lawan ``amls`` (residual penuh masing-masing), lalu baris write-off
        ``writeoff_vals_list`` (urut; dict ``account_id``, ``name``, opsional ``balance`` (None = seluruh sisa),
        ``analytic_distribution``, ``partner_id``, ``reconcile_model_id``). Sisa yang belum terserap tetap di
        suspense (cocok sebagian). Lalu rekonsiliasi berpasangan.

        Konteks ``force_delete/skip_readonly_check/skip_account_move_synchronization`` dipakai dengan hak user (tanpa
        sudo); lock date tetap dicek CE saat move ditulis."""
        self.ensure_one()
        if self.is_reconciled:
            raise UserError(_("Baris mutasi %s sudah direkonsiliasi.", self.display_name))
        _liquidity, suspense, _other = self._seek_for_lines()
        if not suspense:
            raise UserError(
                _("Baris mutasi %s tidak punya kaki suspense — batalkan pencocokan sebelumnya dulu.", self.display_name)
            )
        comp_cur = self.company_id.currency_id
        remainder = sum(suspense.mapped("balance"))
        commands = [Command.delete(line.id) for line in suspense]
        matched = []
        for aml in amls:
            if aml.reconciled:
                raise UserError(_("%s sudah direkonsiliasi.", aml.display_name))
            amounts = self._prepare_counterpart_amounts_using_st_line_rate(
                aml.currency_id, -aml.amount_residual, -aml.amount_residual_currency
            )
            commands.append(
                Command.create(
                    {
                        "name": aml.move_id.name or aml.name or self.payment_ref or "/",
                        "account_id": aml.account_id.id,
                        "partner_id": aml.partner_id.id,
                        "currency_id": aml.currency_id.id,
                        "amount_currency": amounts["amount_currency"],
                        "balance": amounts["balance"],
                    }
                )
            )
            matched.append(aml)
            remainder -= amounts["balance"]

        for vals in writeoff_vals_list or []:
            if comp_cur.is_zero(remainder):
                break
            balance = vals.get("balance")
            balance = comp_cur.round(remainder if balance is None else balance)
            if comp_cur.is_zero(balance):
                continue
            line_vals = {
                "name": vals.get("name") or _("Write-Off"),
                "account_id": vals["account_id"],
                "partner_id": vals.get("partner_id", self.partner_id.id),
                "balance": balance,
            }
            if vals.get("analytic_distribution"):
                line_vals["analytic_distribution"] = vals["analytic_distribution"]
            if vals.get("reconcile_model_id"):
                line_vals["reconcile_model_id"] = vals["reconcile_model_id"]
            commands.append(Command.create(line_vals))
            remainder -= balance

        if not comp_cur.is_zero(remainder):
            # Sisa tetap terbuka di suspense (cocok sebagian): kekurangan yang tak terjelaskan harus tetap terlihat.
            commands.append(
                Command.create(
                    {
                        "name": self.payment_ref or _("Saldo terbuka"),
                        "account_id": self.journal_id.suspense_account_id.id,
                        "balance": remainder,
                    }
                )
            )

        before_ids = set(self.move_id.line_ids.ids)
        self.move_id.with_context(
            force_delete=True, skip_readonly_check=True, skip_account_move_synchronization=True
        ).write({"line_ids": commands})
        new_lines = self.move_id.line_ids.filtered(lambda line: line.id not in before_ids).sorted("id")
        # Urutan pembuatan = urutan perintah: len(matched) baris pertama adalah lawan, pasangkan dengan AML-nya.
        for aml, counterpart in zip(matched, new_lines, strict=False):
            if counterpart.account_id.reconcile or counterpart.account_id.account_type in (
                "asset_receivable",
                "liability_payable",
            ):
                (aml + counterpart).reconcile()
        return True

    # ------------------------------------------------------------------ UI
    def action_efn_open_match_wizard(self):
        """Halaman penuh (target=current) pencocokan satu baris mutasi; wizard dibuat dulu agar halaman tidak kosong."""
        self.ensure_one()
        self._efn_check_reconcile_access()
        if self.is_reconciled:
            raise UserError(_("Baris mutasi ini sudah direkonsiliasi."))
        wizard = self.env["efn.bank.reconcile.wizard"].with_context(default_st_line_id=self.id).create({})
        return {
            "type": "ir.actions.act_window",
            "name": _("Cocokkan Mutasi"),
            "res_model": "efn.bank.reconcile.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    def action_efn_auto_match(self):
        """Rekonsiliasi diam-diam setiap baris terpilih yang punya tepat satu kandidat persis; laporkan hitungannya."""
        self._efn_check_reconcile_access()
        matched = skipped = 0
        for line in self.filtered(lambda st_line: not st_line.is_reconciled):
            aml = line._efn_get_auto_match_candidate()
            if aml:
                line._efn_reconcile_with_amls(aml)
                matched += 1
            else:
                skipped += 1
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if matched else "warning",
                "message": _(
                    "%(matched)s baris dicocokkan, %(skipped)s dilewati (tidak ada kandidat tunggal yang pasti).",
                    matched=matched,
                    skipped=skipped,
                ),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
