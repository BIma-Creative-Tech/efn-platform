"""Wizard pencocokan satu baris mutasi bank (pengganti widget rekonsiliasi bank Enterprise).

Port ADR-049 dari `custom_account_reconcile/wizard/bank_reconcile_wizard.py` + bagian generik
`custom_levis_bank_reconcile/wizard/bank_reconcile_wizard.py` (halaman penuh, "Isi otomatis", "No. Transaksi").
Perubahan EFN (G58 §3.3/§3.4):
- `reconcile_model_id` (`account.reconcile.model` CE, trigger manual) mengisi baris write-off dari baris model atas
  sisa selisih (true-up MDR G60 D-5). Tidak ada model konfigurasi MDR sendiri, tidak bergantung `efn.mdr.rate`;
- setiap tombol memanggil `_efn_check_reconcile_access` baris mutasi; `action_reconcile` memvalidasi ulang bahwa
  item terpilih memang kandidat sah (company + domain kandidat), karena baris wizard bisa diisi lewat RPC;
- `default_get` membaca dengan hak user (tanpa sudo).
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang


class EfnBankReconcileWizard(models.TransientModel):
    _name = "efn.bank.reconcile.wizard"
    _description = "EFN Pencocokan Mutasi Bank"
    _check_company_auto = True

    st_line_id = fields.Many2one("account.bank.statement.line", required=True, readonly=True)
    company_id = fields.Many2one(related="st_line_id.company_id")
    currency_id = fields.Many2one(related="st_line_id.company_id.currency_id")
    journal_id = fields.Many2one(related="st_line_id.journal_id")
    date = fields.Date(related="st_line_id.date")
    payment_ref = fields.Char(related="st_line_id.payment_ref")
    bank_ref = fields.Char(related="st_line_id.ref", string="No. Transaksi Bank")
    partner_id = fields.Many2one(related="st_line_id.partner_id")
    amount = fields.Monetary(related="st_line_id.amount", currency_field="currency_id")

    candidate_ids = fields.One2many("efn.bank.reconcile.wizard.line", "wizard_id")
    selected_total = fields.Monetary(compute="_compute_amounts", currency_field="currency_id", string="Terpilih")
    remainder = fields.Monetary(
        compute="_compute_amounts",
        currency_field="currency_id",
        string="Sisa",
        help="Nominal mutasi yang belum tertutup item terpilih. Sisa diserap model rekonsiliasi / write-off bila "
        "dipilih; selebihnya tetap terbuka di akun suspense.",
    )
    reconcile_model_id = fields.Many2one(
        "account.reconcile.model",
        string="Model Rekonsiliasi",
        check_company=True,
        domain="[('company_id', '=', company_id), ('trigger', '=', 'manual'), "
        "'|', ('match_journal_ids', '=', False), ('match_journal_ids', 'in', journal_id)]",
        help="Mis. true-up MDR settlement kartu/QRIS: baris model (persentase/tetap/regex + analitik outlet) "
        "membukukan sisa selisih ke akun MDR.",
    )
    writeoff = fields.Boolean(string="Write-off sisa")
    writeoff_account_id = fields.Many2one(
        "account.account",
        string="Akun Write-off",
        check_company=True,
        domain="[('account_type', 'not in', ('asset_receivable', 'liability_payable'))]",
    )
    writeoff_label = fields.Char(string="Label Write-off", default="Write-Off")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        st_line = self.env["account.bank.statement.line"].browse(
            res.get("st_line_id") or self.env.context.get("default_st_line_id")
        )
        if not st_line:
            return res
        st_line._efn_check_reconcile_access()
        if st_line.is_reconciled:
            raise UserError(_("Baris mutasi ini sudah direkonsiliasi."))
        res["candidate_ids"] = self._efn_candidate_commands(st_line, relax=False)
        return res

    @api.model
    def _efn_candidate_commands(self, st_line, relax, keep=None):
        """Perintah baris kandidat; pilih otomatis kandidat persis pertama (atau ``keep`` bila diberikan)."""
        comp_cur = st_line.company_id.currency_id
        target = abs(st_line.amount)
        commands = []
        preselected = False
        for aml in st_line._efn_get_match_candidates(relax=relax):
            if keep is not None:
                select = aml in keep
            else:
                select = not preselected and not comp_cur.compare_amounts(abs(aml.amount_residual), target)
                preselected = preselected or select
            commands.append((0, 0, {"aml_id": aml.id, "selected": select}))
        return commands

    @api.depends("candidate_ids.selected", "candidate_ids.amount_residual", "amount")
    def _compute_amounts(self):
        for wiz in self:
            total = sum(wiz.candidate_ids.filtered("selected").mapped("amount_residual"))
            wiz.selected_total = total
            # Suspense = -nominal; lawan = -residual masing-masing.
            wiz.remainder = -(wiz.amount or 0.0) + total

    # ------------------------------------------------------------------ tombol
    def _efn_check(self):
        self.ensure_one()
        self.st_line_id._efn_check_reconcile_access()
        if self.st_line_id.is_reconciled:
            raise UserError(_("Baris mutasi ini sudah direkonsiliasi."))

    def action_search_more(self):
        """Perlebar kandidat (tanpa filter arah; batas tanggal mutasi tetap). Halaman dimuat ulang di tempat."""
        self._efn_check()
        keep = self.candidate_ids.filtered("selected").aml_id
        self.candidate_ids.unlink()
        commands = self._efn_candidate_commands(self.st_line_id, relax=True, keep=keep)
        known = {cmd[2]["aml_id"] for cmd in commands}
        # Pilihan yang sudah ada tetap dipertahankan walau tidak muncul di pencarian lebar.
        commands += [(0, 0, {"aml_id": aml.id, "selected": True}) for aml in keep if aml.id not in known]
        self.candidate_ids = commands
        return None

    def action_efn_suggest(self):
        """Isi pilihan hingga — tidak pernah melebihi — nominal mutasi: kandidat terbesar dulu, lewati yang tidak muat.

        Untuk setoran gabungan beberapa hari (tunai). Sisa tetap terbuka di suspense dengan sengaja: kekurangan yang
        tak terjelaskan harus tetap terlihat dan diputuskan Finance."""
        self._efn_check()
        currency = self.company_id.currency_id
        direction = 1.0 if self.amount > 0 else -1.0
        budget = abs(self.amount)
        chosen = self.env["efn.bank.reconcile.wizard.line"]
        for line in self.candidate_ids.sorted(lambda c: abs(c.amount_residual), reverse=True):
            residual = line.amount_residual * direction
            if residual <= 0 or currency.compare_amounts(residual, budget) > 0:
                continue
            chosen |= line
            budget -= residual
            if currency.is_zero(budget):
                break
        if not chosen:
            raise UserError(
                _(
                    "Tidak ada item terbuka yang muat dalam %(amount)s. Gunakan 'Cari lagi' atau pilih manual.",
                    amount=formatLang(self.env, abs(self.amount), currency_obj=currency),
                )
            )
        self.candidate_ids.selected = False
        chosen.selected = True
        return None

    def action_reconcile(self):
        self._efn_check()
        st_line = self.st_line_id
        amls = self.candidate_ids.filtered("selected").aml_id
        # Baris wizard bisa diisi lewat RPC: item harus kandidat sah baris ini (company, akun reconcilable, terbuka,
        # posted, tidak setelah tanggal mutasi) dan dapat dibaca user.
        amls.check_access("read")
        if amls and amls != amls.filtered_domain(st_line._efn_matching_domain(relax=True)):
            raise UserError(_("Item terpilih bukan kandidat yang sah untuk baris mutasi ini."))
        if not amls and not (self.writeoff or self.reconcile_model_id):
            raise UserError(_("Pilih minimal satu item jurnal (atau gunakan model rekonsiliasi / write-off)."))
        writeoffs = []
        remainder = -(st_line.amount or 0.0) + sum(amls.mapped("amount_residual"))
        if self.reconcile_model_id:
            writeoffs += st_line._efn_reconcile_model_writeoffs(self.reconcile_model_id, remainder)
        if self.writeoff:
            if not self.writeoff_account_id:
                raise UserError(_("Pilih akun write-off."))
            if st_line.company_id not in self.writeoff_account_id.company_ids:
                raise UserError(_("Akun write-off harus milik company baris mutasi."))
            # Tanpa "balance": menyerap seluruh sisa setelah baris model.
            writeoffs.append({"account_id": self.writeoff_account_id.id, "name": self.writeoff_label or _("Write-Off")})
        st_line._efn_reconcile_with_amls(amls, writeoff_vals_list=writeoffs)
        return self.env["ir.actions.act_window"]._for_xml_id("efn_account_reconcile.action_efn_bank_reconciliation")


class EfnBankReconcileWizardLine(models.TransientModel):
    _name = "efn.bank.reconcile.wizard.line"
    _description = "EFN Kandidat Pencocokan Mutasi"
    _order = "id"

    wizard_id = fields.Many2one("efn.bank.reconcile.wizard", ondelete="cascade", required=True)
    selected = fields.Boolean(string="Pilih")
    aml_id = fields.Many2one("account.move.line", string="Item Jurnal", readonly=True)
    move_name = fields.Char(related="aml_id.move_id.name", string="Entri")
    transaction_ref = fields.Char(related="aml_id.efn_transaction_ref", string="No. Transaksi")
    date = fields.Date(related="aml_id.date")
    account_id = fields.Many2one(related="aml_id.account_id")
    partner_id = fields.Many2one(related="aml_id.partner_id")
    name = fields.Char(related="aml_id.name", string="Label")
    amount_residual = fields.Monetary(related="aml_id.amount_residual", currency_field="company_currency_id")
    company_currency_id = fields.Many2one(related="aml_id.company_currency_id")
