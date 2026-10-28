"""Settlement bulanan Poin EFN lintas PT (W-15, docs/04 §13.1 L-66/L-68; docs/10 §A.4 "settlement poin bulanan").

Akrual harian (``efn.loyalty.accrual``) sudah memindahkan liabilitas poin yang ditukar di PT lain menjadi:
- di PT **penerbit**: Utang Poin Antar-PT (per PT penebus) -- ``issuer_by_redeemer`` {redeemed, restored};
- di PT **penebus**: Piutang Kliring Poin Antar-PT (per PT penerbit).

Modul ini menutup saldo itu per bulan, per pasangan (penerbit -> penebus):

    nilai = Σ redeemed[penebus] - Σ restored[penebus] dari akrual PENERBIT bulan itu yang final (done/no_entry)

Dokumen ``efn.loyalty.settlement`` (empat mata: disiapkan Finance A, disetujui Finance B, keduanya Accounting Manager
di KEDUA PT) -> dua jurnal ber-ref ``EFN-LOYSET-<PENERBIT>-<PENEBUS>-<YYYY-MM>``:

    PT penerbit : Dr Utang Poin Antar-PT          / Cr Bank (jurnal bank penerbit)
    PT penebus  : Dr Bank (jurnal bank penebus)   / Cr Piutang Kliring Poin Antar-PT

Netting (saling hapus utang dua arah) TIDAK dilakukan otomatis: dua dokumen terpisah (A->B dan B->A) agar jejak audit
per arah utuh; Finance boleh menyetujui keduanya bersamaan. Tidak ada transfer dana otomatis -- jurnal bank mencatat
pembayaran yang dieksekusi Finance di luar sistem (referensi transfer wajib).

Penjaga:
- akrual penerbit bulan itu harus lengkap (setiap tanggal ada & final); ada yang pending/error/conflict/missing_config
  -> ditolak (``efn_loyalty_settlement:accrual_incomplete``);
- satu dokumen per (penerbit, penebus, bulan) (UNIQUE), batal -> boleh dibuat ulang (UNIQUE parsial);
- nilai dihitung ulang saat disetujui; berubah sejak disiapkan (akrual terlambat) -> ditolak, siapkan ulang;
- penyiap != penyetuju (SOD-LOY-SET); akun/jurnal belum diatur -> ditolak dengan daftar yang kurang.
"""

import calendar
import json
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command

from .efn_loyalty_accrual import FINAL_STATES

ERR = "efn_loyalty_settlement"
STATES = [
    ("draft", "Disiapkan"),
    ("approved", "Disetujui & dijurnal"),
    ("cancelled", "Dibatalkan"),
]


def month_bounds(period):
    """``'YYYY-MM'`` -> (tanggal awal, tanggal akhir). ValueError bila format salah."""
    year, month = (int(part) for part in period.split("-"))
    if not (2000 <= year <= 2100 and 1 <= month <= 12) or len(period) != 7:
        raise ValueError(period)
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


class EfnLoyaltySettlement(models.Model):
    _name = "efn.loyalty.settlement"
    _description = "EFN Loyalty Settlement Poin Antar-PT (bulanan)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "period desc, issuer_company_id, redeemer_company_id"
    _rec_name = "ref"

    period = fields.Char(string="Periode (YYYY-MM)", required=True, readonly=True, index=True)
    issuer_company_id = fields.Many2one(
        "res.company", string="PT penerbit (membayar)", required=True, readonly=True, index=True
    )
    redeemer_company_id = fields.Many2one(
        "res.company", string="PT penebus (menerima)", required=True, readonly=True, index=True
    )
    ref = fields.Char(readonly=True, copy=False)
    state = fields.Selection(STATES, default="draft", required=True, readonly=True, tracking=True, copy=False)
    redeemed_value_idr = fields.Integer(string="Ditukar di PT penebus (Rp)", readonly=True)
    restored_value_idr = fields.Integer(string="Dikembalikan (Rp)", readonly=True)
    amount_idr = fields.Integer(string="Nilai settlement (Rp)", readonly=True, tracking=True)
    accrual_days = fields.Integer(string="Hari akrual", readonly=True)
    breakdown = fields.Text(string="Rincian per tanggal", readonly=True, help="JSON {tanggal: [redeemed, restored]}.")
    payment_ref = fields.Char(
        string="Referensi transfer bank",
        tracking=True,
        help="Nomor/referensi transfer antar-rekening PT (wajib saat disetujui).",
    )
    payment_date = fields.Date(string="Tanggal bayar", tracking=True)
    issuer_journal_id = fields.Many2one(
        "account.journal", string="Jurnal bank penerbit", domain="[('type', '=', 'bank')]", tracking=True
    )
    redeemer_journal_id = fields.Many2one(
        "account.journal", string="Jurnal bank penebus", domain="[('type', '=', 'bank')]", tracking=True
    )
    issuer_move_id = fields.Many2one("account.move", string="Jurnal penerbit", readonly=True, copy=False)
    redeemer_move_id = fields.Many2one("account.move", string="Jurnal penebus", readonly=True, copy=False)
    prepared_by_id = fields.Many2one("res.users", string="Disiapkan oleh", readonly=True, copy=False)
    approved_by_id = fields.Many2one("res.users", string="Disetujui oleh", readonly=True, copy=False)
    approved_at = fields.Datetime(readonly=True, copy=False)

    _pair_differs = models.Constraint(
        "CHECK (issuer_company_id <> redeemer_company_id)", "PT penerbit dan penebus harus berbeda."
    )
    _period_format = models.Constraint("CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$')", "Periode harus YYYY-MM.")
    _active_unique = models.UniqueIndex(
        "(issuer_company_id, redeemer_company_id, period) WHERE state != 'cancelled'",
        "Settlement aktif untuk pasangan PT & periode ini sudah ada.",
    )

    # ------------------------------------------------------------ akses
    @api.model
    def _efn_check_finance(self, companies):
        """Accounting Manager di SEMUA PT yang disentuh (jurnal dibuat di kedua PT)."""
        user = self.env.user
        if not user.has_group("account.group_account_manager"):
            raise AccessError(_("%s:not_finance — hanya Accounting Manager.", ERR))
        if companies - user.company_ids:
            raise AccessError(_("%s:company — Anda harus punya akses ke kedua PT.", ERR))

    # --------------------------------------------------------- hitung
    @api.model
    def _efn_compute(self, issuer, redeemer, period):
        """Nilai dari akrual PENERBIT bulan itu. Kembalikan dict atau UserError bila akrual belum lengkap/final."""
        start, end = month_bounds(period)
        Accrual = self.env["efn.loyalty.accrual"].sudo()
        accruals = Accrual.search([("company_id", "=", issuer.id), ("date", ">=", start), ("date", "<=", end)])
        today = Accrual._efn_business_today()
        if end >= today:
            raise UserError(_("%s:period_open — periode %s belum berakhir.", ERR, period))
        not_final = accruals.filtered(lambda a: a.state not in FINAL_STATES)
        missing_days = (end - start).days + 1 - len(accruals)
        if not_final or missing_days:
            raise UserError(
                _(
                    "%(err)s:accrual_incomplete — akrual %(pt)s %(period)s belum lengkap: %(missing)s hari tidak ada, "
                    "%(bad)s hari belum final (%(states)s). Selesaikan akrual harian dulu.",
                    err=ERR,
                    pt=issuer.efn_brand_code,
                    period=period,
                    missing=missing_days,
                    bad=len(not_final),
                    states=", ".join(sorted(set(not_final.mapped("state")))) or "-",
                )
            )
        code = redeemer.efn_brand_code
        redeemed = restored = 0
        breakdown = {}
        for accrual in accruals.sorted("date"):
            data = json.loads(accrual.issuer_by_redeemer or "{}") or {}
            red = int((data.get("redeemed") or {}).get(code, 0))
            res = int((data.get("restored") or {}).get(code, 0))
            if red or res:
                breakdown[accrual.date.isoformat()] = [red, res]
            redeemed += red
            restored += res
        return {
            "redeemed_value_idr": redeemed,
            "restored_value_idr": restored,
            "amount_idr": redeemed - restored,
            "accrual_days": len(accruals),
            "breakdown": json.dumps(breakdown, sort_keys=True, separators=(",", ":")),
        }

    @api.model
    def _efn_ref(self, issuer, redeemer, period):
        return f"EFN-LOYSET-{issuer.efn_brand_code}-{redeemer.efn_brand_code}-{period}"

    # ------------------------------------------------------------ alur
    @api.model
    def efn_prepare(self, period, issuer_company_id=None, redeemer_company_id=None):
        """Siapkan settlement periode untuk satu pasangan, atau semua pasangan yang punya nilai != 0 bila tanpa PT."""
        try:
            month_bounds(period)
        except (ValueError, AttributeError) as exc:
            raise UserError(_("%s:period — format periode YYYY-MM.", ERR)) from exc
        Company = self.env["res.company"].sudo()
        if issuer_company_id and redeemer_company_id:
            pairs = [(Company.browse(issuer_company_id), Company.browse(redeemer_company_id))]
        else:
            coded = Company.search([("efn_brand_code", "!=", False), ("id", "in", self.env.user.company_ids.ids)])
            pairs = [(i, r) for i in coded for r in coded if i != r]
        self._efn_check_finance(Company.browse({c.id for pair in pairs for c in pair}))
        created = self.browse()
        for issuer, redeemer in pairs:
            existing = self.sudo().search(
                [
                    ("issuer_company_id", "=", issuer.id),
                    ("redeemer_company_id", "=", redeemer.id),
                    ("period", "=", period),
                    ("state", "!=", "cancelled"),
                ],
                limit=1,
            )
            if existing:
                created |= existing
                continue
            values = self._efn_compute(issuer, redeemer, period)
            if not values["amount_idr"] and not (issuer_company_id and redeemer_company_id):
                continue
            created |= self.sudo().create(
                {
                    **values,
                    "period": period,
                    "issuer_company_id": issuer.id,
                    "redeemer_company_id": redeemer.id,
                    "ref": self._efn_ref(issuer, redeemer, period),
                    "prepared_by_id": self.env.user.id,
                    "issuer_journal_id": issuer._efn_loyalty_bank_journal().id,
                    "redeemer_journal_id": redeemer._efn_loyalty_bank_journal().id,
                }
            )
        return created

    def action_recompute(self):
        for rec in self:
            rec._efn_check_finance(rec.issuer_company_id | rec.redeemer_company_id)
            if rec.state != "draft":
                raise UserError(_("%s:state — hanya settlement draft yang bisa dihitung ulang.", ERR))
            values = rec._efn_compute(rec.issuer_company_id, rec.redeemer_company_id, rec.period)
            rec.sudo().write({**values, "prepared_by_id": self.env.user.id})
        return True

    def action_approve(self):
        for rec in self:
            rec._efn_approve_one()
        return True

    def _efn_approve_one(self):
        self.ensure_one()
        issuer, redeemer = self.issuer_company_id, self.redeemer_company_id
        self._efn_check_finance(issuer | redeemer)
        if self.state != "draft":
            raise UserError(_("%s:state — settlement sudah diproses.", ERR))
        if self.prepared_by_id == self.env.user:
            raise AccessError(_("%s:sod — penyetuju harus berbeda dari penyiap (empat mata).", ERR))
        current = self._efn_compute(issuer, redeemer, self.period)
        if current["amount_idr"] != self.amount_idr or current["breakdown"] != self.breakdown:
            raise UserError(_("%s:changed — akrual berubah sejak disiapkan. Hitung ulang lalu minta persetujuan.", ERR))
        if self.amount_idr < 0:
            raise UserError(
                _("%s:negative — pengembalian melebihi penukaran; tangani manual oleh Finance (nota koreksi).", ERR)
            )
        if not self.amount_idr:
            self.sudo().write(
                {"state": "approved", "approved_by_id": self.env.user.id, "approved_at": fields.Datetime.now()}
            )
            return
        if not self.payment_ref or not self.payment_date:
            raise UserError(_("%s:payment — isi referensi transfer dan tanggal bayar.", ERR))
        missing = []
        if not issuer.sudo().efn_loyalty_interco_payable_account_id:
            missing.append(f"{issuer.efn_brand_code}: akun Utang Poin Antar-PT")
        if not redeemer.sudo().efn_loyalty_clearing_account_id:
            missing.append(f"{redeemer.efn_brand_code}: akun Piutang Kliring Poin Antar-PT")
        for journal, company in ((self.issuer_journal_id, issuer), (self.redeemer_journal_id, redeemer)):
            if not journal or journal.company_id != company or journal.type != "bank":
                missing.append(f"{company.efn_brand_code}: jurnal bank")
        if missing:
            raise UserError(_("%(err)s:missing_config — %(items)s.", err=ERR, items="; ".join(missing)))
        label = _(
            "Settlement Poin EFN %(i)s -> %(r)s %(p)s",
            i=issuer.efn_brand_code,
            r=redeemer.efn_brand_code,
            p=self.period,
        )
        issuer_move = self._efn_move(
            issuer,
            self.issuer_journal_id,
            issuer.sudo().efn_loyalty_interco_payable_account_id,
            self.issuer_journal_id.default_account_id,
            label,
            redeemer.partner_id,
        )
        redeemer_move = self._efn_move(
            redeemer,
            self.redeemer_journal_id,
            self.redeemer_journal_id.default_account_id,
            redeemer.sudo().efn_loyalty_clearing_account_id,
            label,
            issuer.partner_id,
        )
        self.sudo().write(
            {
                "state": "approved",
                "issuer_move_id": issuer_move.id,
                "redeemer_move_id": redeemer_move.id,
                "approved_by_id": self.env.user.id,
                "approved_at": fields.Datetime.now(),
            }
        )

    def _efn_move(self, company, journal, debit_account, credit_account, label, partner):
        move = (
            self.env["account.move"]
            .sudo()
            .with_company(company)
            .create(
                {
                    "move_type": "entry",
                    "company_id": company.id,
                    "journal_id": journal.id,
                    "date": self.payment_date,
                    "ref": f"{self.ref} {self.payment_ref}",
                    "line_ids": [
                        Command.create(
                            {
                                "name": label,
                                "account_id": debit_account.id,
                                "debit": self.amount_idr,
                                "partner_id": partner.id,
                            }
                        ),
                        Command.create(
                            {
                                "name": label,
                                "account_id": credit_account.id,
                                "credit": self.amount_idr,
                                "partner_id": partner.id,
                            }
                        ),
                    ],
                }
            )
        )
        move.action_post()
        return move

    def action_cancel(self):
        for rec in self:
            rec._efn_check_finance(rec.issuer_company_id | rec.redeemer_company_id)
            if rec.state != "draft":
                raise UserError(_("%s:state — settlement yang sudah dijurnal dibatalkan lewat pembalikan jurnal.", ERR))
            rec.sudo().state = "cancelled"
        return True
