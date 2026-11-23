"""Register aset tetap EFN (G57). Port `custom.fixed.asset` dari `odoo-platform` (ADR-049, README "Provenance").

Perubahan terhadap sumber:
- `pdp.audited.mixin` -> aturan `efn.audit.rule` (data); tanpa dependency `custom_*`.
- Default kelompok diterapkan di server (`_efn_group_default_vals`: create / write group_id / onchange), bukan
  hanya onchange (gotcha sumber).
- Kontrol status di server: tombol manager memanggil `_efn_check_manager` (grup + check_access + company); aset
  non-draft hanya boleh diubah pada `name`, `location_id`, `custodian_id`, `note` (selebihnya lewat wizard, yang
  menulis lewat sudo() setelah cek manager).
- Baru: `source_move_line_id` (tagihan vendor -> draf aset, models/account_move.py).
- Guard serial/rental `_merge_assets_into_pool` dibuang (field tidak ada di EFN).
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare

from .efn_account_asset_group import efn_check_asset_manager

_logger = logging.getLogger(__name__)

NEW_CODE = "New"
GROUP_PARAM = "efn_account_asset.group_depreciation_moves"
# Field yang boleh diubah langsung pada aset non-draft (G57 §1.6).
NON_DRAFT_WRITABLE = frozenset({"name", "location_id", "custodian_id", "note"})
# SEC-G57-02: field sistem (status, disposal, revaluasi, pelepasan, gabung, asal tagihan). Hanya boleh ditulis lewat
# `_efn_system()` (sudo + context privat) oleh kode modul ini sesudah cek akses; readonly di view = UI saja.
SYSTEM_FIELDS = frozenset(
    {
        "state",
        "merged_into_id",
        "source_move_line_id",
        "disposal_date",
        "disposal_value",
        "disposal_gain_loss",
        "disposal_move_id",
        "revaluation_value",
        "revaluation_surplus_balance",
        "revaluation_loss_recognized",
        "retired_cost",
        "retired_accumulated_depreciation",
        "opening_accumulated_depreciation",
        "original_quantity",
    }
)
SYSTEM_WRITE_KEY = "efn_asset_system_write"
# Pemetaan default kelompok -> field aset (dipakai create/write/onchange).
GROUP_DEFAULTS = (
    ("default_asset_account_id", "asset_account_id"),
    ("default_depreciation_account_id", "depreciation_account_id"),
    ("default_expense_account_id", "expense_account_id"),
    ("default_journal_id", "journal_id"),
)


class EfnAccountAsset(models.Model):
    _name = "efn.account.asset"
    _description = "EFN Fixed Asset"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "code, id"
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(required=True, copy=False, default=NEW_CODE, tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    group_id = fields.Many2one("efn.account.asset.group", string="Kelompok", tracking=True, check_company=True)
    location_id = fields.Many2one("efn.account.asset.location", string="Lokasi", tracking=True, check_company=True)
    custodian_id = fields.Many2one("res.users", string="Penanggung Jawab", tracking=True)
    note = fields.Html()

    # ---------------------------------------------------------------- perolehan
    acquisition_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    posting_date = fields.Date(
        string="Tanggal Acuan Posting",
        tracking=True,
        copy=False,
        help="Tanggal acuan jadwal penyusutan. Kosong = tanggal perolehan.",
    )
    depreciation_date_mode = fields.Selection(
        [
            ("specific", "Tanggal tertentu (sama dengan tanggal acuan)"),
            ("next_month", "Tanggal tertentu, bulan berikutnya"),
            ("end_following_month", "Akhir bulan berikutnya"),
        ],
        string="Aturan Tanggal Penyusutan",
        default="next_month",
        required=True,
    )
    acquisition_value = fields.Monetary(required=True, currency_field="currency_id", tracking=True)
    salvage_value = fields.Monetary(default=0.0, currency_field="currency_id")
    quantity = fields.Float(
        default=1.0,
        required=True,
        digits="Product Unit of Measure",
        tracking=True,
        help="Jumlah unit fisik di bawah satu nomor aset (aset pool). Pelepasan sebagian mengurangi jumlah dan nilai.",
    )
    original_quantity = fields.Float(
        default=1.0,
        digits="Product Unit of Measure",
        readonly=True,
        copy=False,
        help="Jumlah saat perolehan; tidak berubah oleh pelepasan sebagian.",
    )
    retired_quantity = fields.Float(compute="_compute_retired_quantity", digits="Product Unit of Measure")
    is_quantity_asset = fields.Boolean(string="Aset Pool", compute="_compute_quantity_flags", store=True)
    unit_acquisition_value = fields.Monetary(
        string="Nilai per Unit", compute="_compute_quantity_figures", currency_field="currency_id"
    )
    unit_net_book_value = fields.Monetary(
        string="Nilai Buku per Unit", compute="_compute_quantity_figures", currency_field="currency_id"
    )
    retired_cost = fields.Monetary(default=0.0, currency_field="currency_id", readonly=True, copy=False, tracking=True)
    retired_accumulated_depreciation = fields.Monetary(
        default=0.0, currency_field="currency_id", readonly=True, copy=False
    )
    opening_accumulated_depreciation = fields.Monetary(
        string="Akumulasi Awal / Bawaan",
        default=0.0,
        currency_field="currency_id",
        readonly=True,
        copy=False,
        tracking=True,
        help="Akumulasi penyusutan tanpa baris jadwal (saldo awal cutover atau dari aset yang digabung).",
    )
    merged_into_id = fields.Many2one(
        "efn.account.asset", string="Digabung ke", readonly=True, copy=False, index=True, check_company=True
    )
    merged_asset_ids = fields.One2many("efn.account.asset", "merged_into_id", string="Aset Tergabung", readonly=True)
    merged_count = fields.Integer(compute="_compute_merged_count")
    partial_disposal_ids = fields.One2many(
        "efn.account.asset.partial.disposal", "asset_id", string="Pelepasan Sebagian", copy=False
    )
    partial_disposal_count = fields.Integer(compute="_compute_partial_disposal_count")
    revaluation_value = fields.Monetary(
        string="Revaluasi Kumulatif",
        default=0.0,
        currency_field="currency_id",
        readonly=True,
        copy=False,
        tracking=True,
    )
    revaluation_surplus_balance = fields.Monetary(
        string="Saldo Surplus Revaluasi",
        default=0.0,
        currency_field="currency_id",
        readonly=True,
        copy=False,
        tracking=True,
    )
    revaluation_loss_recognized = fields.Monetary(
        string="Rugi Revaluasi Diakui",
        default=0.0,
        currency_field="currency_id",
        readonly=True,
        copy=False,
        tracking=True,
    )
    useful_life_months = fields.Integer(string="Umur Ekonomis (bulan)", required=True, default=60)
    depreciation_method = fields.Selection(
        [("straight_line", "Garis lurus"), ("declining", "Saldo menurun"), ("none", "Tanpa penyusutan")],
        default="straight_line",
        required=True,
    )
    declining_factor = fields.Float(default=2.0)

    # ---------------------------------------------------------------- akun
    asset_account_id = fields.Many2one("account.account", string="Akun Aset", check_company=True)
    depreciation_account_id = fields.Many2one("account.account", string="Akun Akumulasi Penyusutan", check_company=True)
    expense_account_id = fields.Many2one("account.account", string="Akun Beban Penyusutan", check_company=True)
    journal_id = fields.Many2one(
        "account.journal", string="Jurnal Penyusutan", check_company=True, domain="[('type', '=', 'general')]"
    )

    # ---------------------------------------------------------------- jadwal
    depreciation_line_ids = fields.One2many(
        "efn.account.asset.depreciation.line", "asset_id", string="Jadwal Penyusutan", copy=False
    )
    accumulated_depreciation = fields.Monetary(compute="_compute_depreciation_totals", currency_field="currency_id")
    net_book_value = fields.Monetary(compute="_compute_depreciation_totals", currency_field="currency_id")
    revaluation_ids = fields.One2many("efn.account.asset.revaluation", "asset_id", string="Revaluasi", copy=False)
    revaluation_count = fields.Integer(compute="_compute_revaluation_count")

    # ---------------------------------------------------------------- status / disposal
    state = fields.Selection(
        [("draft", "Draft"), ("running", "Berjalan"), ("disposed", "Dilepas"), ("cancelled", "Dibatalkan")],
        default="draft",
        tracking=True,
        copy=False,
    )
    disposal_date = fields.Date(readonly=True, copy=False)
    disposal_value = fields.Monetary(currency_field="currency_id", readonly=True, copy=False)
    disposal_gain_loss = fields.Monetary(currency_field="currency_id", readonly=True, copy=False)
    disposal_move_id = fields.Many2one("account.move", string="Jurnal Disposal", readonly=True, copy=False)

    # ---------------------------------------------------------------- asal tagihan (G57 §1.3)
    source_move_line_id = fields.Many2one(
        "account.move.line",
        string="Baris Tagihan Asal",
        readonly=True,
        copy=False,
        index=True,
        ondelete="set null",
    )
    source_move_id = fields.Many2one(
        "account.move",
        related="source_move_line_id.move_id",
        store=True,
        index=True,
        string="Tagihan Asal",
    )
    product_id = fields.Many2one("product.product", related="source_move_line_id.product_id", string="Produk")

    _code_company_unique = models.Constraint(
        "UNIQUE(code, company_id)",
        "Kode aset harus unik per PT.",
    )

    # ---------------------------------------------------------------- constraint
    @api.constrains("useful_life_months", "depreciation_method")
    def _check_useful_life(self):
        for asset in self:
            if asset.depreciation_method != "none" and asset.useful_life_months < 1:
                raise ValidationError(_('Aset "%(name)s": umur ekonomis minimal 1 bulan.', name=asset.name))

    @api.constrains("salvage_value", "acquisition_value")
    def _check_salvage(self):
        for asset in self:
            if asset.salvage_value < 0:
                raise ValidationError(_('Aset "%(name)s": nilai sisa tidak boleh negatif.', name=asset.name))
            if asset.salvage_value > asset.acquisition_value:
                raise ValidationError(
                    _('Aset "%(name)s": nilai sisa tidak boleh melebihi nilai perolehan.', name=asset.name)
                )

    @api.constrains("quantity", "original_quantity")
    def _check_quantity(self):
        for asset in self:
            if asset.quantity <= 0:
                raise ValidationError(
                    _('Aset "%(name)s": jumlah harus positif. Lepas seluruh pool lewat Disposal.', name=asset.name)
                )
            if asset.original_quantity and asset.quantity > asset.original_quantity:
                raise ValidationError(
                    _(
                        'Aset "%(name)s": jumlah (%(qty)s) tidak boleh melebihi jumlah awal (%(orig)s). '
                        "Catat penambahan sebagai aset terpisah.",
                        name=asset.name,
                        qty=asset.quantity,
                        orig=asset.original_quantity,
                    )
                )

    @api.constrains("declining_factor", "depreciation_method")
    def _check_declining_factor(self):
        for asset in self:
            if asset.depreciation_method == "declining" and asset.declining_factor <= 0:
                raise ValidationError(_("Faktor saldo menurun harus positif."))

    # ---------------------------------------------------------------- default kelompok (server)
    @api.model
    def _efn_group_default_vals(self, group, current):
        """Nilai default kelompok untuk field yang masih kosong di dict `current` (vals create / nilai record)."""
        vals = {}
        for group_field, asset_field in GROUP_DEFAULTS:
            if group[group_field] and not current.get(asset_field):
                vals[asset_field] = group[group_field].id
        return vals

    @api.onchange("acquisition_date")
    def _onchange_acquisition_date(self):
        for asset in self:
            if asset.acquisition_date and not asset.posting_date:
                asset.posting_date = asset.acquisition_date

    @api.onchange("group_id")
    def _onchange_group_id(self):
        for asset in self.filtered("group_id"):
            current = {name: asset[name] for _g, name in GROUP_DEFAULTS}
            for name, value in self._efn_group_default_vals(asset.group_id, current).items():
                asset[name] = value
            if asset.group_id.default_useful_life_months:
                asset.useful_life_months = asset.group_id.default_useful_life_months

    # ---------------------------------------------------------------- compute
    @api.depends(
        "depreciation_line_ids.amount",
        "depreciation_line_ids.posted",
        "depreciation_line_ids.reversed",
        "acquisition_value",
        "revaluation_value",
        "retired_accumulated_depreciation",
        "opening_accumulated_depreciation",
    )
    def _compute_depreciation_totals(self):
        for asset in self:
            posted = asset.depreciation_line_ids.filtered("posted")
            accum = (
                sum(posted.mapped("amount"))
                + (asset.opening_accumulated_depreciation or 0.0)
                - (asset.retired_accumulated_depreciation or 0.0)
            )
            asset.accumulated_depreciation = accum
            asset.net_book_value = (asset.acquisition_value or 0.0) + (asset.revaluation_value or 0.0) - accum

    @api.depends("quantity", "original_quantity")
    def _compute_quantity_flags(self):
        for asset in self:
            qty = asset.quantity or 0.0
            original = asset.original_quantity or qty
            asset.is_quantity_asset = original > 1.0 or qty > 1.0 or original > qty

    @api.depends("quantity", "original_quantity")
    def _compute_retired_quantity(self):
        for asset in self:
            qty = asset.quantity or 0.0
            original = asset.original_quantity or qty
            asset.retired_quantity = max(0.0, original - qty)

    @api.depends("quantity", "acquisition_value", "revaluation_value", "accumulated_depreciation")
    def _compute_quantity_figures(self):
        for asset in self:
            qty = asset.quantity or 0.0
            gross = (asset.acquisition_value or 0.0) + (asset.revaluation_value or 0.0)
            asset.unit_acquisition_value = gross / qty if qty else 0.0
            asset.unit_net_book_value = (asset.net_book_value or 0.0) / qty if qty else 0.0

    def _compute_merged_count(self):
        for asset in self:
            asset.merged_count = len(asset.merged_asset_ids)

    def _compute_partial_disposal_count(self):
        for asset in self:
            asset.partial_disposal_count = len(asset.partial_disposal_ids)

    def _compute_revaluation_count(self):
        for asset in self:
            asset.revaluation_count = len(asset.revaluation_ids)

    # ---------------------------------------------------------------- akses
    def _efn_check_manager(self):
        """Grup Aset / Manager + check_access('write') + semua aset di company aktif user (G57 §1.6)."""
        efn_check_asset_manager(self.env)
        if self.env.su:
            return
        self.check_access("write")
        if self.company_id - self.env.companies:
            raise AccessError(_("Aset milik PT yang tidak aktif untuk Anda."))

    def _efn_system(self):
        """Recordset untuk menulis SYSTEM_FIELDS (sudo + context privat). Panggil HANYA sesudah cek akses."""
        return self.sudo().with_context(**{SYSTEM_WRITE_KEY: True})

    def _efn_check_system_fields(self, fields_set):
        """SEC-G57-02: tolak SYSTEM_FIELDS kecuali dari `_efn_system()` (RPC tidak bisa menjadi su)."""
        blocked = SYSTEM_FIELDS.intersection(fields_set)
        if blocked and not (self.env.su and self.env.context.get(SYSTEM_WRITE_KEY)):
            raise AccessError(
                _(
                    "Field sistem aset (%(fields)s) hanya diubah lewat tombol/wizard Aset / Manager.",
                    fields=", ".join(sorted(blocked)),
                )
            )

    # ---------------------------------------------------------------- jadwal
    def _depreciable_base(self):
        self.ensure_one()
        return max(0.0, self.acquisition_value + (self.revaluation_value or 0.0) - (self.salvage_value or 0.0))

    def _depreciation_date_for(self, seq_number):
        """Tanggal baris ke-`seq_number` menurut `depreciation_date_mode`, berjangkar `posting_date`."""
        self.ensure_one()
        start = self.posting_date or self.acquisition_date or fields.Date.context_today(self)
        mode = self.depreciation_date_mode or "next_month"
        if mode == "specific":
            return start + relativedelta(months=seq_number - 1)
        if mode == "end_following_month":
            return start + relativedelta(months=seq_number, day=31)
        return start + relativedelta(months=seq_number)

    def _build_schedule(self):
        """Bangun ulang baris belum diposting dari parameter aset saat ini; baris posted/reversed dipertahankan."""
        self.ensure_one()
        if self.depreciation_method == "none":
            return
        base = self._depreciable_base()
        months = self.useful_life_months
        if months <= 0 or base <= 0:
            return
        self.depreciation_line_ids.filtered(lambda line: not line.posted and not line.reversed).unlink()
        remaining = max(0.0, base - self.accumulated_depreciation)
        if remaining <= 0:
            return
        first_seq = max(self.depreciation_line_ids.mapped("sequence")) + 1 if self.depreciation_line_ids else 1
        months_left = months - len(self.depreciation_line_ids.filtered("posted"))
        if months_left <= 0:
            return
        vals_list = []
        running = 0.0
        if self.depreciation_method == "straight_line":
            monthly = round(remaining / months_left, 2)
            for i in range(months_left):
                if i == months_left - 1:
                    amount = round(remaining - running, 2)  # sisa pembulatan di baris terakhir
                else:
                    amount = monthly
                    running += amount
                vals_list.append(self._efn_line_vals(first_seq + i, amount))
        elif self.depreciation_method == "declining":
            rate = self.declining_factor / float(months)
            nbv = remaining
            for i in range(months_left):
                if i == months_left - 1:
                    amount = round(remaining - running, 2)
                else:
                    amount = round(nbv * rate, 2)
                    if running + amount > remaining:
                        amount = round(remaining - running, 2)
                    nbv -= amount
                    running += amount
                vals_list.append(self._efn_line_vals(first_seq + i, amount))
        if vals_list:
            self.env["efn.account.asset.depreciation.line"].create(vals_list)

    def _efn_line_vals(self, seq, amount):
        return {"asset_id": self.id, "sequence": seq, "date": self._depreciation_date_for(seq), "amount": amount}

    # ---------------------------------------------------------------- transisi status
    def action_confirm(self):
        self._efn_check_manager()
        for asset in self:
            if asset.state != "draft":
                raise UserError(_("Hanya aset draft yang dapat dikonfirmasi."))
            if asset.depreciation_method != "none":
                if not asset.asset_account_id or not asset.expense_account_id or not asset.depreciation_account_id:
                    raise UserError(
                        _(
                            'Aset "%(name)s": akun aset, akun beban penyusutan, dan akun akumulasi penyusutan wajib '
                            "diisi sebelum konfirmasi (kelompok tanpa akun, mis. Perangkat Lunak, menunggu Finance).",
                            name=asset.name,
                        )
                    )
                if not asset.journal_id:
                    raise UserError(_('Aset "%(name)s": jurnal penyusutan wajib diisi.', name=asset.name))
                asset._build_schedule()
            asset._efn_system().write({"state": "running"})

    def action_cancel(self):
        self._efn_check_manager()
        for asset in self:
            if asset.depreciation_line_ids.filtered("posted"):
                raise UserError(
                    _(
                        'Aset "%(name)s" tidak bisa dibatalkan: penyusutan sudah diposting. Balik dulu.',
                        name=asset.name,
                    )
                )
        self.filtered(lambda a: a.state in ("draft", "running"))._efn_system().write({"state": "cancelled"})

    def action_reset_draft(self):
        self._efn_check_manager()
        for asset in self:
            if asset.depreciation_line_ids.filtered("posted"):
                raise UserError(
                    _('Aset "%(name)s" sudah punya penyusutan terposting; tidak bisa di-reset.', name=asset.name)
                )
        self.depreciation_line_ids.unlink()
        self._efn_system().write({"state": "draft"})

    def _efn_wizard_action(self, model, name):
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "view_mode": "form",
            "target": "new",
            "context": {"default_asset_id": self.id},
        }

    def action_open_dispose_wizard(self):
        self.ensure_one()
        self._efn_check_manager()
        if self.state != "running":
            raise UserError(_("Hanya aset berjalan yang dapat dilepas."))
        return self._efn_wizard_action("efn.account.asset.disposal.wizard", _("Disposal Aset"))

    def action_open_revaluation_wizard(self):
        self.ensure_one()
        self._efn_check_manager()
        if self.state != "running":
            raise UserError(_("Hanya aset berjalan yang dapat direvaluasi."))
        return self._efn_wizard_action("efn.account.asset.revaluation.wizard", _("Revaluasi Aset"))

    def action_open_partial_disposal_wizard(self):
        self.ensure_one()
        self._efn_check_manager()
        if self.state != "running":
            raise UserError(_("Hanya aset berjalan yang dapat dilepas sebagian."))
        if self.quantity <= 1:
            raise UserError(_('Aset "%(name)s" hanya satu unit. Gunakan Disposal.', name=self.name))
        return self._efn_wizard_action("efn.account.asset.partial.disposal.wizard", _("Pelepasan Sebagian"))

    def _efn_list_action(self, model, name, domain):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "view_mode": "list,form",
            "domain": domain,
        }

    def action_view_partial_disposals(self):
        return self._efn_list_action(
            "efn.account.asset.partial.disposal", _("Pelepasan Sebagian"), [("asset_id", "=", self.id)]
        )

    def action_view_merged_assets(self):
        return self._efn_list_action("efn.account.asset", _("Aset Tergabung"), [("merged_into_id", "=", self.id)])

    def action_view_revaluations(self):
        return self._efn_list_action("efn.account.asset.revaluation", _("Revaluasi"), [("asset_id", "=", self.id)])

    # ---------------------------------------------------------------- pelepasan sebagian (aset pool)
    def _partial_retirement_split(self, quantity):
        """Bagi nilai tercatat secara pro-rata atas `quantity` unit yang keluar (dibulatkan mata uang PT)."""
        self.ensure_one()
        rounding = self.currency_id.round
        qty = self.quantity or 0.0
        if quantity <= 0:
            raise UserError(_("Jumlah unit yang dilepas harus positif."))
        if quantity > qty:
            raise UserError(_("Tidak bisa melepas %(out)s unit: aset hanya membawa %(qty)s.", out=quantity, qty=qty))
        full = float_compare(quantity, qty, precision_rounding=0.000001) == 0
        ratio = 1.0 if full else quantity / qty

        def part(value):
            value = value or 0.0
            return value if full else rounding(value * ratio)

        acquisition = part(self.acquisition_value)
        revaluation = part(self.revaluation_value)
        accum = part(self.accumulated_depreciation)
        cost = acquisition + revaluation
        return {
            "full": full,
            "quantity": quantity,
            "acquisition_value": acquisition,
            "revaluation_value": revaluation,
            "cost": cost,
            "accumulated_depreciation": accum,
            "salvage_value": part(self.salvage_value),
            "surplus": part(self.revaluation_surplus_balance),
            "loss_recognized": part(self.revaluation_loss_recognized),
            "net_book_value": rounding(cost - accum),
        }

    def _apply_partial_retirement(self, split, disposal_date, proceeds, gain_loss, move):
        """Kecilkan aset ke unit tersisa lalu jadwalkan ulang; baris posted tetap (sejarah)."""
        self.ensure_one()
        asset = self._efn_system()
        if split["full"]:
            asset.write(
                {
                    "state": "disposed",
                    "disposal_date": disposal_date,
                    "disposal_value": proceeds,
                    "disposal_gain_loss": gain_loss,
                    "disposal_move_id": move.id if move else False,
                    "revaluation_surplus_balance": 0.0,
                }
            )
            asset.depreciation_line_ids.filtered(lambda line: not line.posted and not line.reversed).unlink()
            return
        asset.write(
            {
                "acquisition_value": asset.acquisition_value - split["acquisition_value"],
                "revaluation_value": (asset.revaluation_value or 0.0) - split["revaluation_value"],
                "salvage_value": (asset.salvage_value or 0.0) - split["salvage_value"],
                "revaluation_surplus_balance": (asset.revaluation_surplus_balance or 0.0) - split["surplus"],
                "revaluation_loss_recognized": (asset.revaluation_loss_recognized or 0.0) - split["loss_recognized"],
                "retired_cost": (asset.retired_cost or 0.0) + split["cost"],
                "retired_accumulated_depreciation": (asset.retired_accumulated_depreciation or 0.0)
                + split["accumulated_depreciation"],
                "quantity": asset.quantity - split["quantity"],
            }
        )
        asset._build_schedule()

    def _merge_assets_into_pool(self, others):
        """Gabungkan `others` ke `self` (aset pool). Tanpa jurnal: hanya subledger (nilai dijumlah, akumulasi
        bawaan masuk `opening_accumulated_depreciation`, aset terserap dibatalkan + `merged_into_id`).
        Privat: hanya untuk skrip `odoo shell` (G57 §1.7)."""
        self.ensure_one()
        others = others - self
        if not others:
            raise UserError(_("Tidak ada aset untuk digabung ke %(code)s.", code=self.code))
        for other in others:
            if other.company_id != self.company_id:
                raise UserError(_("Aset lintas PT tidak dapat digabung."))
            if other.currency_id != self.currency_id:
                raise UserError(_("Aset beda mata uang tidak dapat digabung."))
            if other.state not in ("draft", "running"):
                raise UserError(
                    _('Aset "%(code)s" berstatus %(state)s; tidak dapat digabung.', code=other.code, state=other.state)
                )
            if other.merged_into_id:
                raise UserError(_('Aset "%(code)s" sudah digabung ke aset lain.', code=other.code))
            if other.partial_disposal_ids:
                raise UserError(_('Aset "%(code)s" punya pelepasan sebagian; gabungkan manual.', code=other.code))

        absorbed_accum = sum(others.mapped("accumulated_depreciation"))
        quantity = self.quantity + sum(others.mapped("quantity"))
        self._efn_system().write(
            {
                "acquisition_value": self.acquisition_value + sum(others.mapped("acquisition_value")),
                "salvage_value": (self.salvage_value or 0.0) + sum(others.mapped("salvage_value")),
                "revaluation_value": (self.revaluation_value or 0.0) + sum(others.mapped("revaluation_value")),
                "revaluation_surplus_balance": (self.revaluation_surplus_balance or 0.0)
                + sum(others.mapped("revaluation_surplus_balance")),
                "revaluation_loss_recognized": (self.revaluation_loss_recognized or 0.0)
                + sum(others.mapped("revaluation_loss_recognized")),
                "opening_accumulated_depreciation": (self.opening_accumulated_depreciation or 0.0) + absorbed_accum,
                "quantity": quantity,
                "original_quantity": quantity,
            }
        )
        others.depreciation_line_ids.filtered(lambda line: not line.posted and not line.reversed).unlink()
        others._efn_system().write({"merged_into_id": self.id, "state": "cancelled"})
        for other in others:
            other.message_post(
                body=_(
                    "Digabung ke aset pool %(code)s. Nilai %(cost)s dan akumulasi %(accum)s dipindahkan; aset ini "
                    "dibatalkan dan tidak lagi disusutkan.",
                    code=self.code,
                    cost=other.acquisition_value,
                    accum=other.accumulated_depreciation,
                )
            )
        self.message_post(
            body=_(
                "Menyerap %(count)s aset: %(codes)s. Jumlah kini %(qty)s, nilai perolehan %(value)s, akumulasi "
                "bawaan %(accum)s.",
                count=len(others),
                codes=", ".join(others.mapped("code")),
                qty=self.quantity,
                value=self.acquisition_value,
                accum=absorbed_accum,
            )
        )
        if self.state == "running":
            self._build_schedule()
        return self

    # ---------------------------------------------------------------- posting penyusutan
    def _group_depreciation_moves(self):
        """Satu jurnal per (PT, jurnal, akun beban, akun akumulasi, tanggal) — default ON. Parameter
        `efn_account_asset.group_depreciation_moves` = 0 untuk satu jurnal per baris."""
        param = self.env["ir.config_parameter"].sudo().get_param(GROUP_PARAM, "1")
        return str(param).strip().lower() in ("1", "true", "yes")

    def _post_due_depreciation(self, as_of=None):
        """Posting semua baris belum posted bertanggal <= `as_of` (DR beban / CR akumulasi)."""
        as_of = as_of or fields.Date.context_today(self)
        AccountMove = self.env["account.move"]
        Line = self.env["efn.account.asset.depreciation.line"]
        grouped = self._group_depreciation_moves()
        buckets = {}
        for asset in self:
            if asset.state != "running":
                continue
            due = asset.depreciation_line_ids.filtered(
                lambda line: not line.posted and not line.reversed and line.date <= as_of
            )
            for line in due:
                key = (
                    asset.company_id.id,
                    asset.journal_id.id,
                    asset.expense_account_id.id,
                    asset.depreciation_account_id.id,
                    line.date,
                    None if grouped else line.id,
                )
                buckets[key] = buckets.get(key, Line) | line

        posted_count = 0
        for key in sorted(buckets, key=lambda k: (k[4], k[0], k[1], k[5] or 0)):
            company_id, journal_id, expense_id, accum_id, line_date, _single = key
            lines = buckets[key]
            total = sum(lines.mapped("amount"))
            if not total:
                continue
            if len(lines) == 1:
                asset = lines.asset_id
                ref = _("Penyusutan %(code)s #%(seq)s", code=asset.code, seq=lines.sequence)
                expense_label = _("Penyusutan %(name)s", name=asset.name)
                accum_label = _("Akumulasi penyusutan %(name)s", name=asset.name)
            else:
                ref = _("Penyusutan %(date)s (%(count)s aset)", date=line_date, count=len(lines.asset_id))
                expense_label = _("Penyusutan %(date)s", date=line_date)
                accum_label = _("Akumulasi penyusutan %(date)s", date=line_date)
            move = AccountMove.with_company(company_id).create(
                {
                    "move_type": "entry",
                    "date": line_date,
                    "journal_id": journal_id,
                    "company_id": company_id,
                    "ref": ref,
                    "line_ids": [
                        fields.Command.create(
                            {"name": expense_label, "account_id": expense_id, "debit": total, "credit": 0.0}
                        ),
                        fields.Command.create(
                            {"name": accum_label, "account_id": accum_id, "debit": 0.0, "credit": total}
                        ),
                    ],
                }
            )
            move.action_post()
            lines.write({"posted": True, "move_id": move.id})
            posted_count += len(lines)
        return posted_count

    @api.model
    def _cron_post_due_depreciation(self):
        """Cron harian (OdooBot): posting baris jatuh tempo semua aset berjalan."""
        running = self.search([("state", "=", "running")])
        count = running._post_due_depreciation()
        _logger.info("efn.account.asset: %s baris penyusutan diposting", count)
        return count

    def action_post_selected(self):
        """Server action list: posting baris jatuh tempo (per hari ini) untuk aset terpilih."""
        self._efn_check_manager()
        count = self._post_due_depreciation()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if count else "warning",
                "title": _("Posting Penyusutan"),
                "message": _("%(count)s baris penyusutan diposting.", count=count),
                "sticky": False,
            },
        }

    # ---------------------------------------------------------------- ORM
    @api.model_create_multi
    def create(self, vals_list):
        Group = self.env["efn.account.asset.group"]
        for vals in vals_list:
            # Nilai kosong / state draft = default (klien web bisa mengirimnya); selain itu field sistem ditolak.
            self._efn_check_system_fields(
                name
                for name in SYSTEM_FIELDS & set(vals)
                if vals[name] and not (name == "state" and vals[name] == "draft")
            )
            if vals.get("code", NEW_CODE) == NEW_CODE:
                vals["code"] = self.env["ir.sequence"].next_by_code("efn.account.asset") or NEW_CODE
            if not vals.get("posting_date") and vals.get("acquisition_date"):
                vals["posting_date"] = vals["acquisition_date"]
            if not vals.get("original_quantity"):
                vals["original_quantity"] = vals.get("quantity", 1.0)
            if vals.get("group_id"):
                group = Group.browse(vals["group_id"])
                vals.update(self._efn_group_default_vals(group, vals))
                if "useful_life_months" not in vals and group.default_useful_life_months:
                    vals["useful_life_months"] = group.default_useful_life_months
        return super().create(vals_list)

    def write(self, vals):
        self._efn_check_system_fields(vals)
        if not self.env.su:
            locked = set(vals) - NON_DRAFT_WRITABLE
            locked = {name for name in locked if not name.startswith(("message_", "activity_"))}
            if locked and any(asset.state != "draft" for asset in self):
                raise UserError(
                    _(
                        "Aset yang sudah dikonfirmasi hanya dapat diubah pada nama, lokasi, penanggung jawab, dan "
                        "catatan. Gunakan wizard revaluasi / pelepasan / disposal untuk perubahan nilai."
                    )
                )
        res = super().write(vals)
        if "quantity" in vals and "original_quantity" not in vals:
            draft = self.filtered(lambda a: a.state == "draft")
            if draft:
                super(EfnAccountAsset, draft).write({"original_quantity": vals["quantity"]})
        if vals.get("group_id"):
            for asset in self.filtered(lambda a: a.state == "draft"):
                current = {name: asset[name] for _g, name in GROUP_DEFAULTS}
                defaults = self._efn_group_default_vals(asset.group_id, current)
                if defaults:
                    super(EfnAccountAsset, asset).write(defaults)
        return res

    @api.ondelete(at_uninstall=False)
    def _unlink_except_booked(self):
        if self.filtered(lambda a: a.state in ("running", "disposed") or a.depreciation_line_ids.filtered("posted")):
            raise UserError(_("Aset berjalan/dilepas atau yang punya penyusutan terposting tidak dapat dihapus."))
