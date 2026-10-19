"""Akrual harian liabilitas Poin EFN per PT + rekonsiliasi (docs/04 §13.1 no. 6-7, L-61; docs/08 §11.4 no. 4-6).

L-61: **Odoo menarik** dari BFF (``GET /v1/pos/loyalty/accruals?company_code=&date=``) lewat klien Odoo->BFF yang ada
-- tanpa user teknis / API key Odoo baru. Cron harian 02:00 Asia/Jakarta, per company berkode, untuk tanggal bisnis
kemarin + ``efn.loyalty.accrual_lookback_days`` (3) hari ke belakang yang belum selesai (mengejar BFF yang mati).

Satu ``efn.loyalty.accrual`` per (company, tanggal) (UNIQUE, baris dikunci saat diproses) dan paling banyak satu
``account.move`` dengan ``ref = EFN-LOY-<company_code>-<YYYY-MM-DD>``.

Idempotensi (SEC-156, r14): jurnal dianggap sudah ada HANYA bila ``account.move`` ditautkan sistem lewat
``account.move.efn_loyalty_accrual_id`` (tidak bisa diisi pengguna) dan berstatus ``posted`` -> ``done``. Jurnal lain
ber-ref sama (manual, draft, batal) atau jurnal akrual sendiri yang di-reset ke draft/dibatalkan/dihapus -> status
``conflict`` + log ERROR + aktivitas Finance; **tidak** membuat jurnal ganda dan tidak diam.

Baris jurnal di PT yang diminta (``company_code`` = PT tempat transaksi):

====================================================  =======================================  ====================
Komponen BFF                                          Debit                                    Kredit
====================================================  =======================================  ====================
``earned_value_idr`` (poin diterbitkan PT ini)        Beban Program Poin                       Liabilitas Poin
``earn_reversed_value_idr`` (clawback refund)         Liabilitas Poin                          Beban Program Poin
``redeemed_value_idr_by_issuer[I]``, I != PT ini      Kliring Poin Antar-PT (penerbit I)       Liabilitas Poin
``redeemed_value_idr_by_issuer[PT ini]``              -- (sudah dijurnal POS, metode Poin EFN §9.2)
``restored_value_idr_by_issuer[I]``, I != PT ini L-68 Liabilitas Poin                          Kliring Poin Antar-PT
``redeemed_value_idr_as_issuer_by_redeemer[R]`` L-66  Liabilitas Poin                          Utang Poin Antar-PT
``restored_value_idr_as_issuer_by_redeemer[R]`` L-66  Utang Poin Antar-PT                      Liabilitas Poin
``restored_value_idr`` (bagian PT ini)                -- (refund POS pembayaran Poin EFN negatif sudah menjurnal)
``expired_value_idr`` (kedaluwarsa) L-67              Liabilitas Poin                          Pendapatan kedaluwarsa
``forfeited_value_idr`` (erasure DSAR dll.) L-67      Liabilitas Poin                          Pendapatan kedaluwarsa
====================================================  =======================================  ====================

Akun per PT di ``res.company`` (Settings, PLACEHOLDER). Akun/jurnal yang dibutuhkan tidak ada -> status
``missing_config``, log ERROR (galat, tanpa angka/PII), aktivitas Finance, **tanpa jurnal**. Semua komponen 0 ->
``no_entry`` (tanpa akun pun OK).

Rekonsiliasi (no. 7): saldo akun liabilitas PT (jurnal posted s.d. tanggal itu) vs ``closing_liability_idr`` BFF ->
satu baris ``EFN_LOYALTY_RECON {json 5 kunci}`` per PT per tanggal SETIAP run (termasuk selisih 0 dan akrual yang
sudah final -- dead-man; format mengikat lead, lihat ``_efn_reconcile``) + aktivitas Finance bila selisih. Tidak ada
koreksi otomatis.
"""

import json
import logging
from datetime import date as date_cls
from datetime import datetime, timedelta

import psycopg2
import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command

from odoo.addons.efn_core.models.efn_bff_client import EfnBffError
from odoo.addons.efn_core.models.res_company import EFN_BRAND_CODES

from .account_move import ACCRUAL_LINK_CONTEXT

_logger = logging.getLogger(__name__)
_recon_logger = logging.getLogger("odoo.addons.efn_loyalty_accounting.recon")

ACCRUALS_PATH = "/v1/pos/loyalty/accruals"
BUSINESS_TZ = "Asia/Jakarta"
DEFAULT_LOOKBACK_DAYS = 3
MAX_LOOKBACK_DAYS = 31
KNOWN_CODES = frozenset(code for code, _label in EFN_BRAND_CODES)
VALUE_KEYS = ("earned_value_idr", "earn_reversed_value_idr", "restored_value_idr", "expired_value_idr")
AS_ISSUER_KEYS = ("redeemed_value_idr_as_issuer_by_redeemer", "restored_value_idr_as_issuer_by_redeemer")
# Field aditif opsional (BFF lama tanpa field -> 0): L-67 forfeit erasure DSAR/penyesuaian.
OPTIONAL_VALUE_KEYS = ("forfeited_value_idr",)
# L-68: restore di PT ini per PT penerbit (opsional; BFF lama -> {}); kunci PT sendiri = bagian POS, tidak dijurnal.
RESTORED_BY_ISSUER_KEY = "restored_value_idr_by_issuer"
MAX_IDR = 10**15  # pagar nilai tidak masuk akal (bukan batas bisnis)

STATES = [
    ("pending", "Menunggu"),
    ("done", "Dijurnal"),
    ("no_entry", "Tanpa jurnal (nilai 0)"),
    ("missing_config", "Akun belum diatur"),
    ("conflict", "Konflik jurnal ber-ref sama"),
    ("error", "Galat"),
]
FINAL_STATES = ("done", "no_entry")
RECON_STATES = [("not_run", "Belum"), ("ok", "Cocok"), ("mismatch", "Selisih")]
_CODE_SAFE = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_:")


def _safe_code(value, fallback):
    if isinstance(value, str) and 0 < len(value) <= 64 and set(value) <= _CODE_SAFE:
        return value
    return fallback


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def parse_accrual_response(data, company_code, date_str):
    """Validasi respons BFF (integer IDR, kode PT kontrak). :returns: dict ternormalisasi atau ``None``."""
    if not isinstance(data, dict):
        return None
    if data.get("company_code") != company_code or data.get("date") != date_str:
        return None
    result = {}
    for key in VALUE_KEYS:
        value = data.get(key)
        if not _is_int(value) or not 0 <= value <= MAX_IDR:
            return None
        result[key] = value
    closing = data.get("closing_liability_idr")
    if not _is_int(closing) or abs(closing) > MAX_IDR:
        return None
    result["closing_liability_idr"] = closing
    by_issuer = data.get("redeemed_value_idr_by_issuer")
    if not isinstance(by_issuer, dict):
        return None
    issuers = {}
    for issuer, value in by_issuer.items():
        if issuer not in KNOWN_CODES or not _is_int(value) or not 0 <= value <= MAX_IDR:
            return None
        issuers[issuer] = value
    result["redeemed_value_idr_by_issuer"] = issuers
    for key in OPTIONAL_VALUE_KEYS:
        value = data.get(key, 0)
        if not _is_int(value) or not 0 <= value <= MAX_IDR:
            return None
        result[key] = value
    raw_restored = data.get(RESTORED_BY_ISSUER_KEY)
    restored_by_issuer = {}
    if raw_restored is not None:
        if not isinstance(raw_restored, dict):
            return None
        for issuer, value in raw_restored.items():
            if issuer not in KNOWN_CODES or not _is_int(value) or not 0 <= value <= MAX_IDR:
                return None
            restored_by_issuer[issuer] = value
        # Rincian per penerbit harus menjumlah ke total restore di PT ini (kontrak BFF: Σ = restored_value_idr).
        if sum(restored_by_issuer.values()) != result["restored_value_idr"]:
            return None
    result[RESTORED_BY_ISSUER_KEY] = restored_by_issuer
    # L-66 (opsional; BFF lama tanpa field -> map kosong): sisi PT PENERBIT, kunci = PT penebus, tanpa PT sendiri.
    for key in AS_ISSUER_KEYS:
        raw = data.get(key)
        if raw is None:
            result[key] = {}
            continue
        if not isinstance(raw, dict):
            return None
        mapped = {}
        for redeemer, value in raw.items():
            if redeemer not in KNOWN_CODES or redeemer == company_code:
                return None
            if not _is_int(value) or not 0 <= value <= MAX_IDR:
                return None
            mapped[redeemer] = value
        result[key] = mapped
    generated_at = data.get("generated_at")
    result["generated_at"] = generated_at[:40] if isinstance(generated_at, str) else False
    return result


class EfnLoyaltyAccrual(models.Model):
    _name = "efn.loyalty.accrual"
    _description = "EFN Loyalty Daily Accrual (per PT)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, company_id"
    _check_company_auto = True
    _rec_name = "ref"

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    company_code = fields.Char(readonly=True)
    date = fields.Date(string="Tanggal bisnis", required=True, readonly=True, index=True)
    ref = fields.Char(string="Referensi jurnal", readonly=True)
    state = fields.Selection(STATES, default="pending", required=True, readonly=True, index=True, tracking=True)
    move_id = fields.Many2one("account.move", string="Jurnal", readonly=True, check_company=True)
    earned_value_idr = fields.Integer(string="Earn (Rp)", readonly=True)
    earn_reversed_value_idr = fields.Integer(string="Clawback (Rp)", readonly=True)
    redeemed_same_value_idr = fields.Integer(string="Tukar poin PT sendiri (Rp)", readonly=True)
    redeemed_cross_value_idr = fields.Integer(string="Tukar poin PT lain (Rp)", readonly=True)
    redeemed_by_issuer = fields.Char(string="Tukar per penerbit", readonly=True, help="JSON {kode PT: Rp}.")
    issuer_redeemed_value_idr = fields.Integer(
        string="Poin PT ini ditukar di PT lain (Rp)",
        readonly=True,
        help="L-66: Σ redeemed_value_idr_as_issuer_by_redeemer.",
    )
    issuer_restored_value_idr = fields.Integer(
        string="Penukaran di PT lain dikembalikan (Rp)",
        readonly=True,
        help="L-66: Σ restored_value_idr_as_issuer_by_redeemer.",
    )
    issuer_by_redeemer = fields.Char(
        string="Sisi penerbit per PT penebus", readonly=True, help="JSON {redeemed: {PT: Rp}, restored: {PT: Rp}}."
    )
    restored_value_idr = fields.Integer(string="Poin dikembalikan (Rp)", readonly=True)
    restored_cross_value_idr = fields.Integer(
        string="Restore poin PT lain (Rp)", readonly=True, help="L-68: Σ restored_value_idr_by_issuer[I], I != PT ini."
    )
    restored_by_issuer = fields.Char(string="Restore per penerbit", readonly=True, help="JSON {kode PT: Rp}.")
    expired_value_idr = fields.Integer(string="Kedaluwarsa (Rp)", readonly=True)
    forfeited_value_idr = fields.Integer(
        string="Hangus erasure/penyesuaian (Rp)", readonly=True, help="L-67: forfeit DSAR & penyesuaian ledger BFF."
    )
    closing_liability_idr = fields.Integer(string="Liabilitas penutup BFF (Rp)", readonly=True)
    ledger_liability_idr = fields.Integer(string="Saldo akun liabilitas Odoo (Rp)", readonly=True)
    recon_diff_idr = fields.Integer(string="Selisih (Odoo - BFF)", readonly=True)
    recon_status = fields.Selection(RECON_STATES, default="not_run", readonly=True, tracking=True)
    generated_at = fields.Char(string="Dibuat BFF", readonly=True)
    attempt_count = fields.Integer(readonly=True)
    last_error = fields.Char(readonly=True, help="Kode galat saja.")
    activity_flagged = fields.Char(readonly=True, copy=False)

    _company_date_unique = models.Constraint(
        "UNIQUE (company_id, date)", "Akrual poin hanya satu per company per tanggal."
    )

    @api.ondelete(at_uninstall=False)
    def _unlink_except_linked_move(self):
        """SEC-156: akrual yang sudah menghasilkan jurnal tidak bisa dihapus (termasuk sudo) -- tautan sistem jurnal
        akrual adalah dasar idempotensi; menghapusnya akan membuat jurnal lama tampak "manual" (konflik)."""
        linked = self.env["account.move"].sudo().search_count([("efn_loyalty_accrual_id", "in", self.ids)], limit=1)
        if linked or any(record.move_id for record in self):
            raise UserError(_("Akrual Poin EFN yang sudah memiliki jurnal tidak dapat dihapus (jejak audit)."))

    # ------------------------------------------------------------- tanggal
    @api.model
    def _efn_business_today(self):
        return datetime.now(pytz.timezone(BUSINESS_TZ)).date()

    @api.model
    def _efn_lookback_days(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("efn.loyalty.accrual_lookback_days")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = DEFAULT_LOOKBACK_DAYS
        return max(1, min(value, MAX_LOOKBACK_DAYS))

    @api.model
    def _efn_accrual_dates(self, today=None, lookback=None):
        """Tanggal yang diproses: kemarin dan ``lookback - 1`` hari sebelumnya, terlama dulu."""
        today = today or self._efn_business_today()
        lookback = lookback or self._efn_lookback_days()
        return [today - timedelta(days=offset) for offset in range(lookback, 0, -1)]

    @staticmethod
    def _efn_ref(company_code, day):
        return f"EFN-LOY-{company_code}-{day.isoformat()}"

    # ----------------------------------------------------------------- cron
    @api.model
    def _cron_efn_loyalty_accrual_run(self):
        self._efn_run_all(commit=True)

    @api.model
    def _efn_run_all(self, today=None, commit=False):
        companies = self.env["res.company"].sudo().search([("efn_brand_code", "!=", False)], order="id")
        for day in self._efn_accrual_dates(today=today):
            for company in companies:
                self._efn_run(company, day)
                if commit:
                    self.env["ir.cron"]._commit_progress(1)

    @api.model
    def _efn_get_or_create(self, company, day):
        Accrual = self.sudo()
        record = Accrual.search([("company_id", "=", company.id), ("date", "=", day)], limit=1)
        if record:
            return record
        try:
            with self.env.cr.savepoint():
                return Accrual.create(
                    {
                        "company_id": company.id,
                        "company_code": company.efn_brand_code,
                        "date": day,
                        "ref": self._efn_ref(company.efn_brand_code, day),
                    }
                )
        except psycopg2.IntegrityError:
            return Accrual.search([("company_id", "=", company.id), ("date", "=", day)], limit=1)

    @api.model
    def _efn_run(self, company, day):
        """Proses satu (company, tanggal). Aman dipanggil ulang (final -> dilewati)."""
        if isinstance(day, str):
            day = date_cls.fromisoformat(day)
        company = company.sudo()
        if not company.efn_brand_code:
            return self.browse()
        record = self._efn_get_or_create(company, day)
        if not record:
            return record
        self.flush_model()
        self.env.cr.execute("SELECT id FROM efn_loyalty_accrual WHERE id = %s FOR UPDATE SKIP LOCKED", (record.id,))
        if not self.env.cr.fetchone():
            return record  # sedang diproses worker lain
        record.invalidate_recordset()
        if record.state == "done" and record.move_id.state != "posted":
            # SEC-156: jurnal akrual di-reset ke draft / dibatalkan / dihapus -> tidak lagi dianggap selesai.
            record._efn_fail("conflict", "ref_conflict:move_not_posted", level=logging.ERROR)
            record._efn_reconcile()
            return record
        if record.state in FINAL_STATES:
            # Sudah dijurnal: BFF tidak dipanggil lagi, tetapi baris RECON tetap dikirim setiap run (dead-man) memakai
            # closing BFF tersimpan vs saldo akun terkini.
            record._efn_reconcile()
            return record
        record._efn_process()
        return record

    # -------------------------------------------------------------- proses
    def _efn_process(self):
        self.ensure_one()
        company = self.company_id.sudo()
        code = company.efn_brand_code
        date_str = self.date.isoformat()
        try:
            response = self.env["efn.bff.client"]._efn_loyalty_request(
                "GET",
                ACCRUALS_PATH,
                params={"company_code": code, "date": date_str},
                log_context={"company": code, "date": date_str},
            )
        except EfnBffError as exc:
            self._efn_fail("error", f"bff:{exc.code}", level=logging.WARNING)
            return
        if response.status != 200:
            error = response.data.get("error")
            reason = error.get("code") if isinstance(error, dict) else response.data.get("reason")
            self._efn_fail("error", f"bff:{_safe_code(reason, f'http_{response.status}')}", level=logging.WARNING)
            return
        totals = parse_accrual_response(response.data, code, date_str)
        if totals is None:
            self._efn_fail("error", "bad_response", level=logging.WARNING)
            return
        by_issuer = totals["redeemed_value_idr_by_issuer"]
        cross = {issuer: value for issuer, value in sorted(by_issuer.items()) if issuer != code and value}
        as_issuer_redeemed = totals["redeemed_value_idr_as_issuer_by_redeemer"]
        as_issuer_restored = totals["restored_value_idr_as_issuer_by_redeemer"]
        restored_by_issuer = totals[RESTORED_BY_ISSUER_KEY]
        restored_cross = {issuer: value for issuer, value in restored_by_issuer.items() if issuer != code and value}
        self.write(
            {
                "issuer_redeemed_value_idr": sum(as_issuer_redeemed.values()),
                "issuer_restored_value_idr": sum(as_issuer_restored.values()),
                "issuer_by_redeemer": json.dumps(
                    {"redeemed": as_issuer_redeemed, "restored": as_issuer_restored},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "earned_value_idr": totals["earned_value_idr"],
                "earn_reversed_value_idr": totals["earn_reversed_value_idr"],
                "redeemed_same_value_idr": by_issuer.get(code, 0),
                "redeemed_cross_value_idr": sum(cross.values()),
                "redeemed_by_issuer": json.dumps(by_issuer, sort_keys=True, separators=(",", ":")),
                "restored_value_idr": totals["restored_value_idr"],
                "restored_cross_value_idr": sum(restored_cross.values()),
                "restored_by_issuer": json.dumps(restored_by_issuer, sort_keys=True, separators=(",", ":")),
                "expired_value_idr": totals["expired_value_idr"],
                "forfeited_value_idr": totals["forfeited_value_idr"],
                "closing_liability_idr": totals["closing_liability_idr"],
                "generated_at": totals["generated_at"],
            }
        )
        if self._efn_check_existing_moves(company):
            return
        specs = self._efn_line_specs(totals, cross, restored_cross)
        if not specs:
            self.write({"state": "no_entry", "last_error": False})
            self._efn_reconcile()
            return
        missing = self._efn_missing_config(specs)
        if missing:
            self._efn_fail("missing_config", "missing_accounts:" + ",".join(missing), level=logging.ERROR)
            return
        move = self._efn_create_move(specs)
        if not move:
            return
        self.write({"state": "done", "move_id": move.id, "last_error": False, "activity_flagged": False})
        _logger.info("EFN loyalty accrual %s: jurnal %s diposting (%d baris)", self.ref, move.id, len(specs) * 2)
        self._efn_reconcile()

    def _efn_check_existing_moves(self, company):
        """SEC-156: ``True`` bila tidak boleh membuat jurnal (sudah ``done`` atau ``conflict``), ``False`` bila aman.

        - Jurnal tertaut sistem (``efn_loyalty_accrual_id`` = record ini) berstatus posted -> ``done`` (idempoten).
        - Selain itu, jurnal mana pun ber-ref sama di company ini (manual/draft/batal) atau jurnal tertaut yang tidak
          posted -> ``conflict`` + ERROR + aktivitas Finance. Tidak membuat jurnal ganda."""
        Move = self.env["account.move"].sudo()
        ours = Move.search([("efn_loyalty_accrual_id", "=", self.id)], order="id")
        posted = ours.filtered(lambda move: move.state == "posted" and move.company_id == company)[:1]
        if posted:
            _logger.info("EFN loyalty accrual %s: jurnal akrual %s sudah posted (idempoten)", self.ref, posted.id)
            self.write({"state": "done", "move_id": posted.id, "last_error": False, "activity_flagged": False})
            self._efn_reconcile()
            return True
        same_ref = Move.search([("company_id", "=", company.id), ("ref", "=", self.ref)], order="id")
        if not (same_ref or ours):
            return False
        kind = "foreign_move" if (same_ref - ours) else "move_not_posted"
        self.write({"move_id": ours[:1].id or False})
        self._efn_fail("conflict", f"ref_conflict:{kind}", level=logging.ERROR)
        self._efn_reconcile()
        return True

    def _efn_line_specs(self, totals, cross, restored_cross=None):
        """[(nilai, akun debit, akun kredit, label)] -- nama field akun ``res.company`` (belum di-resolve)."""
        specs = []
        if totals["earned_value_idr"]:
            specs.append(
                (
                    totals["earned_value_idr"],
                    "efn_loyalty_expense_account_id",
                    "efn_loyalty_liability_account_id",
                    _("Earn Poin EFN"),
                )
            )
        if totals["earn_reversed_value_idr"]:
            specs.append(
                (
                    totals["earn_reversed_value_idr"],
                    "efn_loyalty_liability_account_id",
                    "efn_loyalty_expense_account_id",
                    _("Clawback Poin EFN (refund)"),
                )
            )
        for issuer, value in cross.items():
            specs.append(
                (
                    value,
                    "efn_loyalty_clearing_account_id",
                    "efn_loyalty_liability_account_id",
                    _("Tukar Poin EFN terbitan %(issuer)s (kliring antar-PT)", issuer=issuer),
                )
            )
        # L-68, sisi PT PENEBUS: refund POS di PT ini mengembalikan poin terbitan PT lain -> kebalikan jurnal penukaran
        # lintas PT (POS sudah mengkredit liabilitas lewat pembayaran Poin EFN negatif).
        for issuer, value in sorted((restored_cross or {}).items()):
            if value:
                specs.append(
                    (
                        value,
                        "efn_loyalty_liability_account_id",
                        "efn_loyalty_clearing_account_id",
                        _("Restore Poin EFN terbitan %(issuer)s (kliring antar-PT)", issuer=issuer),
                    )
                )
        # L-66, sisi PT PENERBIT: poin terbitan PT ini ditukar di PT lain -> liabilitas berpindah menjadi utang ke PT
        # penebus (pasangan Dr Piutang/Kliring di PT penebus); restore penukaran itu -> kebalikannya.
        for redeemer, value in sorted(totals["redeemed_value_idr_as_issuer_by_redeemer"].items()):
            if value:
                specs.append(
                    (
                        value,
                        "efn_loyalty_liability_account_id",
                        "efn_loyalty_interco_payable_account_id",
                        _("Poin EFN terbitan PT ini ditukar di %(redeemer)s (utang antar-PT)", redeemer=redeemer),
                    )
                )
        for redeemer, value in sorted(totals["restored_value_idr_as_issuer_by_redeemer"].items()):
            if value:
                specs.append(
                    (
                        value,
                        "efn_loyalty_interco_payable_account_id",
                        "efn_loyalty_liability_account_id",
                        _("Restore penukaran Poin EFN di %(redeemer)s (utang antar-PT)", redeemer=redeemer),
                    )
                )
        # L-67 (SEC-160): poin kedaluwarsa & hangus karena erasure DSAR/penyesuaian di PT penerbit. Tanpa baris ini
        # ``closing_liability_idr`` BFF (sudah dikurangi keduanya) menyimpang permanen dari saldo Odoo.
        if totals["expired_value_idr"]:
            specs.append(
                (
                    totals["expired_value_idr"],
                    "efn_loyalty_liability_account_id",
                    "efn_loyalty_breakage_account_id",
                    _("Poin EFN kedaluwarsa (breakage)"),
                )
            )
        if totals.get("forfeited_value_idr"):
            specs.append(
                (
                    totals["forfeited_value_idr"],
                    "efn_loyalty_liability_account_id",
                    "efn_loyalty_breakage_account_id",
                    _("Poin EFN hangus (penghapusan data/penyesuaian)"),
                )
            )
        return specs

    def _efn_missing_config(self, specs):
        company = self.company_id.sudo()
        needed = sorted({name for _v, debit, credit, _l in specs for name in (debit, credit)})
        missing = [
            name.removeprefix("efn_loyalty_").removesuffix("_account_id") for name in needed if not company[name]
        ]
        if not company._efn_loyalty_journal():
            missing.append("journal")
        return missing

    def _efn_create_move(self, specs):
        company = self.company_id.sudo()
        line_cmds = []
        for value, debit_field, credit_field, label in specs:
            line_cmds.append(Command.create({"name": label, "account_id": company[debit_field].id, "debit": value}))
            line_cmds.append(Command.create({"name": label, "account_id": company[credit_field].id, "credit": value}))
        Move = self.env["account.move"].sudo().with_company(company).with_context(**{ACCRUAL_LINK_CONTEXT: True})
        try:
            with self.env.cr.savepoint():
                move = Move.create(
                    {
                        "move_type": "entry",
                        "company_id": company.id,
                        "journal_id": company._efn_loyalty_journal().id,
                        "date": self.date,
                        "ref": self.ref,
                        "efn_loyalty_accrual_id": self.id,  # SEC-156: tautan sistem (dasar idempotensi)
                        "line_ids": line_cmds,
                    }
                )
                move.action_post()
        except (UserError, ValidationError) as exc:
            # Mis. periode terkunci (lock date). Pesan Odoo tidak di-log (bisa memuat nama); kelas saja.
            self._efn_fail("error", f"post_failed:{type(exc).__name__.lower()}", level=logging.WARNING)
            return None
        return move

    # ------------------------------------------------------------ rekonsiliasi
    def _efn_ledger_liability(self):
        """Saldo kredit akun liabilitas PT (posted, s.d. tanggal akrual), Rupiah bulat."""
        company = self.company_id.sudo()
        account = company.efn_loyalty_liability_account_id
        if not account:
            return None
        groups = (
            self.env["account.move.line"]
            .sudo()
            ._read_group(
                [
                    ("company_id", "=", company.id),
                    ("account_id", "=", account.id),
                    ("parent_state", "=", "posted"),
                    ("date", "<=", self.date),
                ],
                aggregates=["balance:sum"],
            )
        )
        balance = groups[0][0] if groups and groups[0][0] else 0.0
        return int(round(-balance))

    def _efn_reconcile(self):
        """Kontrak log lead G21 (mengikat, Promtail/Loki data-ai): satu baris per PT per tanggal setiap run,
        termasuk selisih 0 (dead-man): ``EFN_LOYALTY_RECON `` + ``json.dumps(obj, sort_keys=True)`` dengan TEPAT
        5 kunci ``bff_closing_liability_idr, company_code, date, diff_idr, odoo_liability_idr`` (int IDR,
        ``diff_idr = odoo - bff``); INFO bila 0, WARNING bila != 0. Tanpa kunci lain/PII.

        Akun liabilitas belum diatur -> saldo Odoo dianggap 0 (liabilitas memang tidak tercatat) + ``recon_status``
        ``not_run`` dan satu WARNING terpisah (bukan baris RECON) agar penyebabnya terbaca."""
        self.ensure_one()
        ledger = self._efn_ledger_liability()
        if ledger is None:
            _logger.warning(
                "EFN loyalty accrual %s: akun liabilitas belum diatur; rekonsiliasi memakai saldo Odoo 0", self.ref
            )
        odoo_liability = ledger or 0
        diff = odoo_liability - self.closing_liability_idr
        status = "not_run" if ledger is None else ("ok" if diff == 0 else "mismatch")
        self.write({"ledger_liability_idr": odoo_liability, "recon_diff_idr": diff, "recon_status": status})
        payload = {
            "bff_closing_liability_idr": int(self.closing_liability_idr),
            "company_code": self.company_code,
            "date": self.date.isoformat(),
            "diff_idr": int(diff),
            "odoo_liability_idr": int(odoo_liability),
        }
        line = json.dumps(payload, sort_keys=True)
        if diff == 0:
            _recon_logger.info("EFN_LOYALTY_RECON %s", line)
            return
        _recon_logger.warning("EFN_LOYALTY_RECON %s", line)
        self._efn_schedule_activity("recon_mismatch")

    # -------------------------------------------------------------- galat
    def _efn_fail(self, state, code, level):
        self.ensure_one()
        self.write({"state": state, "last_error": code, "attempt_count": self.attempt_count + 1})
        _logger.log(
            level,
            "EFN loyalty accrual %s (%s): %s -- tanpa jurnal; Finance dijadwalkan aktivitas",
            self.ref,
            state,
            code,
        )
        reasons = {"missing_config": "missing_config", "conflict": "ref_conflict"}
        self._efn_schedule_activity(reasons.get(state) or code.split(":", 1)[0])

    def _efn_schedule_activity(self, reason):
        """Aktivitas To-Do sekali per kode alasan ke Accounting Manager pertama company. ``activity_flagged`` =
        himpunan alasan (dipisah koma) agar dua alasan bergantian (mis. ``ref_conflict`` + ``recon_mismatch`` setiap
        run) tidak membuat aktivitas berulang; dikosongkan saat akrual menjadi ``done``."""
        self.ensure_one()
        flagged = [item for item in (self.activity_flagged or "").split(",") if item]
        if reason in flagged:
            return
        user = self._efn_finance_user()
        if user:
            notes = {
                "missing_config": _(
                    "Akrual Poin EFN %(ref)s tidak dijurnal: akun/jurnal belum diatur (%(error)s). "
                    "Atur di Settings > Invoicing > Poin EFN lalu klik 'Proses ulang'."
                ),
                "ref_conflict": _(
                    "Akrual Poin EFN %(ref)s tidak dijurnal: ada jurnal ber-ref sama yang bukan jurnal akrual posted "
                    "(%(error)s; mis. jurnal manual/draft/batal, atau jurnal akrual di-reset ke draft). Posting ulang "
                    "jurnal akrual, atau hapus/ganti ref jurnal lain, lalu klik 'Proses ulang'."
                ),
                "recon_mismatch": _(
                    "Saldo liabilitas Poin EFN %(ref)s berbeda dengan ledger BFF (selisih Rp %(diff)s). "
                    "Tinjau; tidak ada koreksi otomatis."
                ),
            }
            default = _("Akrual Poin EFN %(ref)s gagal (%(error)s). Klik 'Proses ulang' setelah diperiksa.")
            note = notes.get(reason, default)
            self.sudo().activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=user.id,
                summary=_("Akrual Poin EFN"),
                note=note % {"ref": self.ref, "error": self.last_error or reason, "diff": self.recon_diff_idr},
            )
        self.sudo().write({"activity_flagged": ",".join([*flagged, reason])})

    def _efn_finance_user(self):
        group = self.env.ref("account.group_account_manager", raise_if_not_found=False)
        Users = self.env["res.users"].sudo()
        if not group:
            return Users
        group_field = "all_group_ids" if "all_group_ids" in Users._fields else "groups_id"
        return Users.search(
            [
                (group_field, "in", group.ids),
                ("company_ids", "in", self.company_id.ids),
                ("share", "=", False),
                ("id", "!=", self.env.ref("base.user_root").id),
            ],
            order="id",
            limit=1,
        )

    # -------------------------------------------------------------- tombol UI
    def action_efn_retry(self):
        """Proses ulang akrual ``missing_config``/``error`` sekarang (Accounting Manager company record)."""
        user = self.env.user
        if not user.has_group("account.group_account_manager"):
            raise AccessError(_("Hanya Accounting / Manager yang dapat memproses ulang akrual Poin EFN."))
        self.check_access("read")
        for record in self:
            if record.company_id not in user.company_ids:
                raise AccessError(_("Akrual Poin EFN milik company lain."))
            if record.state in FINAL_STATES:
                raise UserError(_("Akrual ini sudah selesai."))
        Accrual = self.env["efn.loyalty.accrual"].sudo()
        for record in self.sudo():
            _logger.info("EFN loyalty accrual %s diproses ulang oleh uid=%s", record.ref, self.env.uid)
            Accrual._efn_run(record.company_id, record.date)
        return True
