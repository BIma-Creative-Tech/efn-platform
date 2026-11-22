"""G59-B: entri penangguhan + pengakuan bulanan dari baris tagihan/faktur bertanggal mulai-selesai.

Port ADR-049 dari odoo-platform `custom_account_deferred/models/account_move.py`. Perubahan terhadap sumber:
rename `efn_*`, analitik baris asal ikut ke setiap baris turunan (P&L outlet G60-C), pesan Indonesia, pesan jelas
bila entri pengakuan di periode terkunci menghalangi reset-to-draft.
"""

from dateutil.relativedelta import relativedelta

from odoo import Command, api, fields, models
from odoo.exceptions import UserError


def _month_ends(start, end):
    """Tanggal akhir bulan yang mencakup [start, end]; mis. 15 Jan -> 10 Mar = 31 Jan, 28/29 Feb, 31 Mar."""
    ends = []
    cursor = start.replace(day=1)
    while cursor <= end:
        ends.append(cursor + relativedelta(months=1, days=-1))
        cursor += relativedelta(months=1)
    return ends


class AccountMove(models.Model):
    _inherit = "account.move"

    efn_deferred_origin_move_id = fields.Many2one(
        "account.move",
        string="Asal Tangguhan",
        readonly=True,
        copy=False,
        index="btree_not_null",
        help="Tagihan/faktur asal entri penangguhan atau pengakuan ini.",
    )
    efn_deferred_entry_type = fields.Selection(
        [("deferral", "Penangguhan"), ("recognition", "Pengakuan")],
        string="Jenis Entri Tangguhan",
        readonly=True,
        copy=False,
    )
    efn_deferred_generated_ids = fields.One2many(
        "account.move", "efn_deferred_origin_move_id", string="Entri Tangguhan"
    )
    efn_deferred_generated_count = fields.Integer(compute="_compute_efn_deferred_generated_count")

    @api.depends("efn_deferred_generated_ids")
    def _compute_efn_deferred_generated_count(self):
        for move in self:
            move.efn_deferred_generated_count = len(move.efn_deferred_generated_ids)

    def action_efn_open_deferred_entries(self):
        """Smart button. RPC publik: baca move asal (ACL + record rule user), tanpa sudo."""
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Entri Tangguhan"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("efn_deferred_origin_move_id", "=", self.id)],
            "context": {"create": 0},
        }

    # ------------------------------------------------------------------ pembuatan
    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        for move in posted:
            if move.is_invoice(include_receipts=True) and not move.efn_deferred_entry_type:
                move._efn_generate_deferred_entries()
        return posted

    def _efn_deferred_config(self):
        company = self.company_id
        return {
            "expense_account": company.efn_deferred_expense_account_id,
            "revenue_account": company.efn_deferred_revenue_account_id,
            "journal": company.efn_deferred_journal_id,
        }

    @staticmethod
    def _efn_deferred_line_vals(name, account, line, amount):
        """Satu baris jurnal turunan: debit bila amount > 0, kredit bila < 0; partner & analitik dari baris asal."""
        return Command.create(
            {
                "name": name,
                "account_id": account.id,
                "partner_id": line.partner_id.id,
                "analytic_distribution": line.analytic_distribution,
                "debit": amount if amount > 0 else 0.0,
                "credit": -amount if amount < 0 else 0.0,
            }
        )

    def _efn_generate_deferred_entries(self):
        self.ensure_one()
        if self.efn_deferred_generated_ids:
            return  # idempoten: siklus post -> draft -> post
        lines = self.invoice_line_ids.filtered(lambda line: line._efn_is_deferrable())
        if not lines:
            return
        cfg = self._efn_deferred_config()
        if not cfg["journal"]:
            raise UserError(
                self.env._(
                    "Jurnal Tangguhan PT %(company)s belum diatur (Pengaturan > Akuntansi > Entri Tangguhan).",
                    company=self.company_id.name,
                )
            )

        currency = self.company_currency_id
        deferral_lines = []
        recognition = {}  # akhir bulan -> [Command.create]
        today = fields.Date.context_today(self)
        for line in lines:
            is_expense = line.account_id.internal_group == "expense"
            deferred_acc = cfg["expense_account"] if is_expense else cfg["revenue_account"]
            if not deferred_acc:
                raise UserError(
                    self.env._(
                        "Akun %(kind)s tangguhan PT %(company)s belum diatur (dibutuhkan baris '%(line)s').",
                        kind=self.env._("beban") if is_expense else self.env._("pendapatan"),
                        company=self.company_id.name,
                        line=line.name or line.account_id.code,
                    )
                )
            balance = line.balance  # beban: debit > 0; pendapatan: kredit (balance < 0)
            if currency.is_zero(balance):
                continue
            label = line.name or ""

            # 1) Penangguhan: pindahkan SELURUH nilai dari akun laba-rugi ke akun tangguhan.
            name = self.env._("Penangguhan: %(label)s", label=label)
            deferral_lines += [
                self._efn_deferred_line_vals(name, line.account_id, line, -balance),
                self._efn_deferred_line_vals(name, deferred_acc, line, balance),
            ]

            # 2) Pengakuan: prorata jumlah hari per bulan, sisa pembulatan di bulan terakhir.
            start, end = line.efn_deferred_start_date, line.efn_deferred_end_date
            total_days = (end - start).days + 1
            month_ends = _month_ends(start, end)
            allocated = 0.0
            for index, m_end in enumerate(month_ends):
                if index == len(month_ends) - 1:
                    amount = currency.round(balance - allocated)
                else:
                    days = (min(end, m_end) - max(start, m_end.replace(day=1))).days + 1
                    amount = currency.round(balance * days / total_days)
                    allocated += amount
                if currency.is_zero(amount):
                    continue
                name = self.env._("Pengakuan %(month)s: %(label)s", month=m_end.strftime("%m/%Y"), label=label)
                recognition.setdefault(m_end, []).extend(
                    [
                        self._efn_deferred_line_vals(name, deferred_acc, line, -amount),
                        self._efn_deferred_line_vals(name, line.account_id, line, amount),
                    ]
                )

        if not deferral_lines:
            return

        Move = self.env["account.move"].with_context(skip_invoice_sync=True)
        deferral = Move.create(
            {
                "move_type": "entry",
                "journal_id": cfg["journal"].id,
                "date": self.date,
                "ref": self.env._("Penangguhan %(move)s", move=self.name or ""),
                "efn_deferred_origin_move_id": self.id,
                "efn_deferred_entry_type": "deferral",
                "line_ids": deferral_lines,
            }
        )
        deferral._post(soft=False)

        for m_end in sorted(recognition):
            move = Move.create(
                {
                    "move_type": "entry",
                    "journal_id": cfg["journal"].id,
                    "date": m_end,
                    "ref": self.env._(
                        "Pengakuan %(month)s atas %(move)s", month=m_end.strftime("%m/%Y"), move=self.name or ""
                    ),
                    "efn_deferred_origin_move_id": self.id,
                    "efn_deferred_entry_type": "recognition",
                    "line_ids": recognition[m_end],
                }
            )
            if m_end <= today:
                move._post(soft=False)
            else:
                move.auto_post = "at_date"  # diposting cron CE `_autopost_draft_entries`

    # ------------------------------------------------------------------ reset / pembatalan
    def button_draft(self):
        """Override CE: entri turunan ikut di-draft lalu dihapus. Lock date TIDAK pernah dilewati: bila ada entri
        turunan posted di periode terkunci, tolak dengan pesan yang menyebut entrinya (sebelum move asal diubah)."""
        for move in self:
            locked = move.efn_deferred_generated_ids.filtered(
                lambda m: m.state == "posted"
                and m.company_id._get_lock_date_violations(m.date, sale=False, purchase=False, tax=False)
            )
            if locked:
                raise UserError(
                    self.env._(
                        "%(move)s tidak dapat dikembalikan ke draft: entri tangguhan berikut berada di periode "
                        "terkunci: %(entries)s. Buka kunci periode atau buat nota kredit/pembalik.",
                        move=move.display_name,
                        entries=", ".join(locked.mapped("display_name")),
                    )
                )
        res = super().button_draft()
        for move in self:
            generated = move.efn_deferred_generated_ids
            if generated:
                generated.filtered(lambda m: m.state == "posted").button_draft()
                generated.unlink()
        return res
