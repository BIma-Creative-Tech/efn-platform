"""Mesin laporan keuangan dari ``account_move_line`` posted (SQL, satu query per laporan).

Klasifikasi memakai ``account_type`` CE 19 (bukan kode akun), sehingga berlaku untuk semua COA ``l10n_id``:

* Neraca: Aset (lancar, tidak lancar, kas, piutang, prepaid, tetap) = Liabilitas + Ekuitas + Laba berjalan.
  Saldo s.d. tanggal akhir, termasuk saldo awal. Laba tahun berjalan = Σ pendapatan - beban sejak awal tahun fiskal;
  laba tahun-tahun sebelumnya (belum ditutup) masuk "Laba ditahan (belum ditutup)".
* Laba rugi: pendapatan, HPP, beban operasional, pendapatan/beban lain, penyusutan pada periode.
* Neraca saldo: saldo awal, mutasi debit/kredit periode, saldo akhir per akun.
* Arus kas (G34, metode tidak langsung): laba bersih + penyusutan (non-kas) + perubahan modal kerja = aktivitas
  operasi; perubahan aset tetap/tidak lancar = investasi; liabilitas jangka panjang & ekuitas = pendanaan. Akun bisa
  dipindah kategori dengan tag CE ``account.account_tag_operating|investing|financing``. Kas & setara kas = akun
  ``asset_cash`` + akun *outstanding receipts/payments* jurnal bank/kas (CE 18+: pembayaran diposting ke akun itu
  sampai direkonsiliasi dengan mutasi bank) + akun transfer likuiditas PT. Kenaikan kas bersih direkonsiliasi ke
  perubahan saldo akun-akun tersebut (baris *Selisih*, harus 0).
* Konsolidasi (beberapa PT): baris akun digabung per (tipe, kode akun). **Eliminasi**: baris jurnal yang partner-nya
  = partner company lain DALAM cakupan laporan dianggap transaksi antar-PT dan dikeluarkan (piutang/utang dan
  penjualan/pembelian antar-PT). Daftar eliminasi ditampilkan terpisah agar bisa diaudit.
Mata uang: semua PT IDR; laporan memakai ``balance`` (mata uang company).
"""

from collections import OrderedDict, defaultdict
from datetime import date

from odoo import _, api, models

BS_SECTIONS = OrderedDict(
    [
        ("asset_cash", ("asset", _("Kas dan bank"))),
        ("asset_receivable", ("asset", _("Piutang usaha"))),
        ("asset_current", ("asset", _("Aset lancar lainnya"))),
        ("asset_prepayments", ("asset", _("Biaya dibayar di muka"))),
        ("asset_non_current", ("asset", _("Aset tidak lancar"))),
        ("asset_fixed", ("asset", _("Aset tetap"))),
        ("liability_payable", ("liability", _("Utang usaha"))),
        ("liability_credit_card", ("liability", _("Kartu kredit"))),
        ("liability_current", ("liability", _("Liabilitas jangka pendek"))),
        ("liability_non_current", ("liability", _("Liabilitas jangka panjang"))),
        ("equity", ("equity", _("Ekuitas"))),
        ("equity_unaffected", ("equity", _("Laba ditahan (belum ditutup)"))),
    ]
)
PL_SECTIONS = OrderedDict(
    [
        ("income", ("income", _("Pendapatan"))),
        ("expense_direct_cost", ("cogs", _("Harga pokok penjualan"))),
        ("expense", ("opex", _("Beban operasional"))),
        ("expense_depreciation", ("opex", _("Penyusutan"))),
        ("income_other", ("other", _("Pendapatan lain"))),
        ("expense_other", ("other", _("Beban lain"))),
    ]
)
PL_TYPES = tuple(PL_SECTIONS)
# Arus kas: kategori default per account_type neraca (asset_cash = kas itu sendiri).
CF_DEFAULT = {
    "asset_receivable": "operating",
    "asset_current": "operating",
    "asset_prepayments": "operating",
    "liability_payable": "operating",
    "liability_credit_card": "operating",
    "liability_current": "operating",
    "asset_fixed": "investing",
    "asset_non_current": "investing",
    "liability_non_current": "financing",
    "equity": "financing",
    "equity_unaffected": "financing",
}
CF_TAGS = {
    "account.account_tag_operating": "operating",
    "account.account_tag_investing": "investing",
    "account.account_tag_financing": "financing",
}
CF_SECTIONS = OrderedDict(
    [
        ("operating", _("Arus kas dari aktivitas operasi")),
        ("investing", _("Arus kas dari aktivitas investasi")),
        ("financing", _("Arus kas dari aktivitas pendanaan")),
    ]
)


class EfnFinancialReportEngine(models.AbstractModel):
    _name = "efn.financial.report.engine"
    _description = "EFN mesin laporan keuangan"

    # ------------------------------------------------------------------ query dasar
    @api.model
    def _efn_lines(self, companies, date_from, date_to, eliminate=True, with_account=False):
        """[(account_type, code, name, company_id, opening, debit, credit, eliminated)] per akun x company.

        ``opening`` = saldo sebelum ``date_from``; ``debit/credit`` = mutasi [date_from, date_to].
        Baris dengan partner = company lain dalam cakupan -> dihitung ke ``eliminated`` (bukan ke saldo) bila
        ``eliminate`` dan lebih dari satu company. ``with_account`` menambah record ``account.account`` di akhir tuple.
        """
        self.env["account.move.line"].flush_model()
        self.env["account.account"].flush_model()
        elim = eliminate and len(companies) > 1
        partner_ids = tuple(companies.mapped("partner_id").ids) or (0,)
        self.env.cr.execute(
            """
            SELECT aml.account_id, aml.company_id,
                   SUM(CASE WHEN aml.date < %(d0)s AND (NOT %(elim_flag)s OR p.id IS NULL) THEN aml.balance ELSE 0 END),
                   SUM(CASE WHEN aml.date >= %(d0)s AND (NOT %(elim_flag)s OR p.id IS NULL) THEN aml.debit ELSE 0 END),
                   SUM(CASE WHEN aml.date >= %(d0)s AND (NOT %(elim_flag)s OR p.id IS NULL) THEN aml.credit ELSE 0 END),
                   SUM(CASE WHEN %(elim_flag)s AND p.id IS NOT NULL THEN aml.balance ELSE 0 END)
              FROM account_move_line aml
              JOIN res_company own ON own.id = aml.company_id
              LEFT JOIN res_partner rp ON rp.id = aml.partner_id
              LEFT JOIN res_partner p ON p.id = rp.commercial_partner_id
                                     AND p.id IN %(partners)s
                                     AND p.id != own.partner_id
                                     -- PPN/pajak antar-PT TIDAK dieliminasi: kewajiban/hak terhadap kantor pajak.
                                     AND aml.tax_line_id IS NULL
             WHERE aml.parent_state = 'posted'
               AND aml.company_id IN %(companies)s
               AND aml.date <= %(d1)s
          GROUP BY 1, 2
            """,
            {
                "d0": date_from,
                "d1": date_to,
                "companies": tuple(companies.ids),
                "partners": partner_ids,
                "elim_flag": elim,
            },
        )
        rows = self.env.cr.fetchall()
        Account = self.env["account.account"].sudo()
        result = []
        for account_id, company_id, opening, debit, credit, eliminated in rows:
            account = Account.with_company(company_id).browse(account_id)
            if account.account_type == "off_balance":
                continue
            row = (
                account.account_type,
                account.code or "",
                account.name,
                company_id,
                opening,
                debit,
                credit,
                eliminated,
            )
            result.append(row + (account,) if with_account else row)
        return result

    @api.model
    def _efn_fy_start(self, company, day):
        """Awal tahun fiskal yang memuat ``day`` (CE: fiscalyear_last_month/day)."""
        dates = company.compute_fiscalyear_dates(day)
        return dates["date_from"]

    # ------------------------------------------------------------------ laporan
    @api.model
    def _efn_balance_sheet(self, companies, date_to, eliminate=True):
        fy_start = self._efn_fy_start(companies[:1], date_to)
        rows = self._efn_lines(companies, date(1900, 1, 1), date_to, eliminate)
        sections = OrderedDict(
            (key, {"label": label, "group": grp, "lines": defaultdict(float)})
            for key, (grp, label) in BS_SECTIONS.items()
        )
        current_profit, prior_profit, eliminations = 0.0, 0.0, defaultdict(float)
        # Laba: pendapatan & beban; dipisah tahun berjalan vs sebelumnya.
        pl_rows = self._efn_lines(companies, fy_start, date_to, eliminate)
        for acc_type, _code, _name, _company, opening, debit, credit, _elim in pl_rows:
            if acc_type in PL_TYPES:
                prior_profit += -opening
                current_profit += -(debit - credit)
        for acc_type, code, name, _company, opening, debit, credit, elim in rows:
            balance = opening + debit - credit
            if elim:
                eliminations[(code, name)] += elim
            if acc_type not in BS_SECTIONS:
                continue
            sign = 1 if BS_SECTIONS[acc_type][0] == "asset" else -1
            sections[acc_type]["lines"][(code, name)] += sign * balance
        sections["equity_unaffected"]["lines"][("", _("Laba (rugi) tahun-tahun lalu belum ditutup"))] += prior_profit
        totals = defaultdict(float)
        out = []
        for key, sec in sections.items():
            lines = [
                {"code": c, "name": n, "amount": round(v, 2)}
                for (c, n), v in sorted(sec["lines"].items())
                if round(v, 2)
            ]
            amount = round(sum(line["amount"] for line in lines), 2)
            totals[sec["group"]] += amount
            if lines:
                out.append({"key": key, "label": sec["label"], "group": sec["group"], "lines": lines, "total": amount})
        totals["current_profit"] = round(current_profit, 2)
        totals["equity_total"] = round(totals["equity"] + current_profit, 2)
        totals["liability_equity"] = round(totals["liability"] + totals["equity_total"], 2)
        totals["difference"] = round(totals["asset"] - totals["liability_equity"], 2)
        return {
            "sections": out,
            "totals": dict(totals),
            "eliminations": [
                {"code": c, "name": n, "amount": round(v, 2)}
                for (c, n), v in sorted(eliminations.items())
                if round(v, 2)
            ],
        }

    @api.model
    def _efn_profit_loss(self, companies, date_from, date_to, eliminate=True):
        rows = self._efn_lines(companies, date_from, date_to, eliminate)
        sections = OrderedDict(
            (key, {"label": label, "group": grp, "lines": defaultdict(float)})
            for key, (grp, label) in PL_SECTIONS.items()
        )
        eliminations = defaultdict(float)
        for acc_type, code, name, _company, _opening, debit, credit, elim in rows:
            if acc_type not in PL_SECTIONS:
                continue
            if elim:
                eliminations[(code, name)] += elim
            sign = 1 if acc_type in ("income", "income_other") else -1
            sections[acc_type]["lines"][(code, name)] += sign * (credit - debit)
        out, totals = [], defaultdict(float)
        for key, sec in sections.items():
            lines = [
                {"code": c, "name": n, "amount": round(v, 2)}
                for (c, n), v in sorted(sec["lines"].items())
                if round(v, 2)
            ]
            amount = round(sum(line["amount"] for line in lines), 2)
            totals[key] = amount
            if lines:
                out.append({"key": key, "label": sec["label"], "group": sec["group"], "lines": lines, "total": amount})
        gross = totals["income"] - totals["expense_direct_cost"]
        operating = gross - totals["expense"] - totals["expense_depreciation"]
        net = operating + totals["income_other"] - totals["expense_other"]
        return {
            "sections": out,
            "totals": {
                "revenue": totals["income"],
                "gross_profit": round(gross, 2),
                "operating_profit": round(operating, 2),
                "net_profit": round(net, 2),
            },
            "eliminations": [
                {"code": c, "name": n, "amount": round(v, 2)}
                for (c, n), v in sorted(eliminations.items())
                if round(v, 2)
            ],
        }

    @api.model
    def _efn_trial_balance(self, companies, date_from, date_to, eliminate=True):
        rows = self._efn_lines(companies, date_from, date_to, eliminate)
        merged = defaultdict(lambda: [0.0, 0.0, 0.0])
        types = {}
        for acc_type, code, name, _company, opening, debit, credit, _elim in rows:
            key = (code or "", name)
            merged[key][0] += opening
            merged[key][1] += debit
            merged[key][2] += credit
            types[key] = acc_type
        lines, tot = [], [0.0, 0.0, 0.0, 0.0]
        for (code, name), (opening, debit, credit) in sorted(merged.items()):
            closing = opening + debit - credit
            if not any(round(x, 2) for x in (opening, debit, credit)):
                continue
            lines.append(
                {
                    "code": code,
                    "name": name,
                    "type": types[(code, name)],
                    "opening": round(opening, 2),
                    "debit": round(debit, 2),
                    "credit": round(credit, 2),
                    "closing": round(closing, 2),
                }
            )
            for i, v in enumerate((opening, debit, credit, closing)):
                tot[i] += v
        return {
            "lines": lines,
            "totals": {
                "opening": round(tot[0], 2),
                "debit": round(tot[1], 2),
                "credit": round(tot[2], 2),
                "closing": round(tot[3], 2),
            },
        }

    @api.model
    def _efn_cf_category(self, account):
        """Kategori arus kas akun neraca: tag CE (operating/investing/financing) mengalahkan default per tipe."""
        for xmlid, category in CF_TAGS.items():
            tag = self.env.ref(xmlid, raise_if_not_found=False)
            if tag and tag in account.tag_ids:
                return category
        return CF_DEFAULT.get(account.account_type)

    @api.model
    def _efn_cash_equivalent_ids(self, companies):
        """Akun aset (lancar/kas) outstanding pembayaran jurnal bank/kas (eksplisit atau bawaan PT) + akun transfer PT.

        Akun kliring berjenis liabilitas (mis. jurnal Poin EFN) TIDAK dihitung sebagai kas.
        """
        lines = (
            self.env["account.payment.method.line"]
            .sudo()
            .search([("journal_id.type", "in", ("bank", "cash")), ("journal_id.company_id", "in", companies.ids)])
        )
        accounts = lines.mapped("payment_account_id") | companies.sudo().mapped("transfer_account_id")
        # Metode tanpa akun eksplisit memakai akun outstanding bawaan chart template PT (CE 19
        # `account_journal_payment_debit/credit_account_id`, mis. "Outstanding Receipts/Payments").
        Template = self.env["account.chart.template"].sudo()
        for company in companies:
            for xmlid in ("account_journal_payment_debit_account_id", "account_journal_payment_credit_account_id"):
                account = Template.with_company(company).ref(xmlid, raise_if_not_found=False)
                if account:
                    accounts |= account
        return set(accounts.filtered(lambda a: a.account_type in ("asset_cash", "asset_current")).ids)

    @api.model
    def _efn_cash_flow(self, companies, date_from, date_to, eliminate=True):
        """Arus kas metode tidak langsung untuk periode [date_from, date_to].

        Pengaruh kas akun neraca non-kas = -(debit - credit) periode. Penyusutan (``expense_depreciation``) ditambahkan
        kembali di operasi dan dikurangkan dari investasi (akumulasi penyusutan ada di akun ``asset_fixed``), sehingga
        total tidak berubah. ``difference`` = (kas awal + kenaikan bersih) - kas akhir; 0 bila semua jurnal seimbang
        dan (konsolidasi) eliminasi simetris.
        """
        rows = self._efn_lines(companies, date_from, date_to, eliminate, with_account=True)
        cash_ids = self._efn_cash_equivalent_ids(companies)
        lines = {key: defaultdict(float) for key in CF_SECTIONS}
        net_profit = depreciation = cash_opening = cash_move = 0.0
        for acc_type, code, name, _company, opening, debit, credit, _elim, account in rows:
            move = debit - credit
            if acc_type in PL_TYPES:
                net_profit -= move
                if acc_type == "expense_depreciation":
                    depreciation += move
                continue
            if acc_type == "asset_cash" or account.id in cash_ids:
                cash_opening += opening
                cash_move += move
                continue
            category = self._efn_cf_category(account)
            if category and round(move, 2):
                lines[category][(code, name)] -= move
        out, totals = [], {}
        for key, label in CF_SECTIONS.items():
            sec_lines = []
            if key == "operating":
                sec_lines.append({"code": "", "name": _("Laba (rugi) bersih"), "amount": round(net_profit, 2)})
                if round(depreciation, 2):
                    sec_lines.append(
                        {"code": "", "name": _("Penyusutan (beban non-kas)"), "amount": round(depreciation, 2)}
                    )
            elif key == "investing" and round(depreciation, 2):
                sec_lines.append(
                    {"code": "", "name": _("Penyusutan tercatat di aset tetap"), "amount": round(-depreciation, 2)}
                )
            sec_lines += [
                {"code": c, "name": n, "amount": round(v, 2)} for (c, n), v in sorted(lines[key].items()) if round(v, 2)
            ]
            total = round(sum(line["amount"] for line in sec_lines), 2)
            totals[key] = total
            if sec_lines:
                out.append({"key": key, "label": label, "group": key, "lines": sec_lines, "total": total})
        net_change = round(totals["operating"] + totals["investing"] + totals["financing"], 2)
        cash_closing = round(cash_opening + cash_move, 2)
        totals.update(
            {
                "net_change": net_change,
                "cash_opening": round(cash_opening, 2),
                "cash_closing": cash_closing,
                "difference": round(cash_opening + net_change - cash_closing, 2),
            }
        )
        return {"sections": out, "totals": totals, "eliminations": []}
