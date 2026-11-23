"""G54: wizard "Lengkapi konfigurasi POS" (docs/fit-gap/G54_G55_G61_ODOO_SETUP.md §1-§7).

Masalah: ``pos.config`` hasil importer ESB (``efn_legacy_history``, ``payment_method_ids=()``) tidak bisa membuka sesi
("Open Register" ditolak ``_check_payment_method_ids``). Wizard ini mengotomasi langkah UI standar CE 19, tanpa model
akuntansi/POS baru:

- mode ``check``: laporan kesiapan P01..P19 per config + peringatan W-xx; TIDAK menulis apa pun selain baris hasil wizard
  (cek CE dipanggil langsung, tanpa savepoint karena tidak ada yang ditulis);
- mode ``apply``: langkah C-01..C-09 (cari dulu, baru buat -> idempoten), lalu cek ulang (C-10). Setiap config diproses
  dalam savepoint sendiri; gagal = baris ``blocked``, config lain lanjut.

Hak: cek = ``point_of_sale.group_pos_manager``; apply = + ``account.group_account_manager`` (membuat jurnal/akun), dicek
di server lalu operasi memakai ``sudo().with_company(company)``. ``pos.config`` CE 19 tidak punya chatter (bukan
``mail.thread``), jadi jejak apply = baris hasil wizard + ``_logger.info`` (id saja, tanpa PII).

Multi-company: semua pencarian dibatasi ``company_id = wizard.company_id``. Data baru (EDC/QRIS/Poin per PT) tidak
dibuat untuk SMD; wizard tetap generik (SMD tanpa EDC/QRIS -> ``warning``).
ADR: ADR-001, ADR-027, ADR-031, ADR-042 (Proposed).
"""

import logging

from odoo import SUPERUSER_ID, Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

JOURNAL_CODE_SIZE = 5  # account.journal.code size=5 (account_journal.py 19.0)
CODE_SUFFIX_CHARS = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Kode brand -> sufiks xmlid data efn_setup_company/efn_setup_pos (xmlid lama GL/PB dipertahankan, L-48).
BRAND_XMLID_SUFFIX = {"GL": "rtl", "PB": "fnb"}
SEQUENCE_FIELDS = ("order_seq_id", "order_backend_seq_id", "order_line_seq_id", "device_seq_id")
EDC_PREFIX = "pos_payment_method_edc_"
QRIS_PREFIX = "pos_payment_method_qris_manual_"
STATUS_SELECTION = [
    ("ok", "OK"),
    ("fixed", "Diperbaiki"),
    ("warning", "Peringatan"),
    ("blocked", "Terblokir"),
]


def cash_journal_code(brand_code, taken):
    """Kode jurnal kas per outlet (§3.1): ``<brand>C`` + nomor bebas terkecil yang mengisi sisa 5 karakter.

    2 huruf (PB) -> PBC01..PBC99; 3 huruf (IMD) -> IMDC1..IMDC9, IMDCA..IMDCZ; >= 4 huruf / tanpa kode -> False.
    """
    prefix = f"{brand_code or ''}C"
    room = JOURNAL_CODE_SIZE - len(prefix)
    if not brand_code or room < 1:
        return False
    if room == 1:
        candidates = (prefix + char for char in CODE_SUFFIX_CHARS)
    else:
        candidates = (f"{prefix}{number:0{room}d}" for number in range(1, 10**room))
    return next((code for code in candidates if code not in taken), False)


def _exc_text(exc):
    # SEC-G54-02: hanya pesan fungsional (UserError/ValidationError/AccessError) yang ditampilkan; error lain
    # (mis. psycopg2) bisa memuat detail SQL -> pesan generik, detail lengkap hanya di log server.
    if isinstance(exc, (UserError, ValidationError, AccessError)) and getattr(exc, "args", None):
        return str(exc.args[0])[:300]
    _logger.warning("EFN G54: kesalahan internal %s", type(exc).__name__, exc_info=exc)
    return _("Kesalahan internal (detail di log server).")


class EfnPosSetupWizard(models.TransientModel):
    _name = "efn.pos.setup.wizard"
    _description = "EFN: lengkapi konfigurasi POS (G54)"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        domain=lambda self: [("id", "in", self.env.user.company_ids.ids)],
    )
    config_ids = fields.Many2many(
        "pos.config",
        string="Point of Sale",
        check_company=True,
        domain="[('company_id', '=', company_id)]",
        help="Kosong = semua Point of Sale aktif milik company.",
    )
    mode = fields.Selection(
        [("check", "Cek saja (tidak menulis)"), ("apply", "Lengkapi (apply)")],
        required=True,
        default="check",
    )
    opt_cash = fields.Boolean(string="Tunai per POS (jurnal + metode)", default=True)
    opt_edc = fields.Boolean(string="EDC manual", default=True)
    opt_qris_manual = fields.Boolean(string="QRIS manual (cek mutasi)", default=True)
    opt_company_accounts = fields.Boolean(string="Akun POS company (piutang PoS, selisih kas)", default=True)
    opt_menu_taxes = fields.Boolean(string="Pajak penjualan default di produk menu tanpa pajak", default=True)
    line_ids = fields.One2many("efn.pos.setup.wizard.line", "wizard_id", string="Hasil", readonly=True)
    summary = fields.Char(string="Ringkasan", readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        if ctx.get("active_model") == "pos.config" and ctx.get("active_ids"):
            configs = self.env["pos.config"].browse(ctx["active_ids"]).exists()
            if configs:
                company = configs[0].company_id
                res["company_id"] = company.id
                res["config_ids"] = [Command.set(configs.filtered(lambda c: c.company_id == company).ids)]
        return res

    # ------------------------------------------------------------------------------------------------------ entri
    def action_run(self):
        self.ensure_one()
        company = self.company_id
        apply = self.mode == "apply"
        self._efn_check_access(company, apply)
        configs = self._efn_target_configs()
        lines = []
        if apply:
            lines += self._efn_apply_company(company, configs)
            for config in configs:
                lines += self._efn_apply_config(config)
        check_lines = self._efn_check_company(company)
        for config in configs:
            check_lines += self._efn_check_config(config)
        self.line_ids.unlink()
        self.env["efn.pos.setup.wizard.line"].create([{**line, "wizard_id": self.id} for line in lines + check_lines])
        blocked = {line["config_id"] for line in check_lines if line["status"] == "blocked" and line["config_id"]}
        self.summary = _(
            "%(mode)s: %(ready)s dari %(total)s Point of Sale siap dibuka, %(blocked)s terblokir.",
            mode=_("Apply") if apply else _("Cek"),
            ready=len(configs) - len(blocked),
            total=len(configs),
            blocked=len(blocked),
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Lengkapi konfigurasi POS"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _efn_check_access(self, company, apply):
        env = self.env
        if env.is_superuser():
            return
        user = env.user
        if company not in user.company_ids:
            raise AccessError(_("Anda tidak punya akses ke company %s.", company.name))
        if not user.has_group("point_of_sale.group_pos_manager"):
            raise AccessError(_("Wizard kesiapan POS hanya untuk Point of Sale / Administrator."))
        if apply and not user.has_group("account.group_account_manager"):
            raise AccessError(
                _(
                    "Mode apply membuat jurnal dan akun: butuh hak Point of Sale / Administrator "
                    "dan Akuntansi / Administrator."
                )
            )

    def _efn_target_configs(self):
        Config = self.env["pos.config"].sudo()
        if self.config_ids:
            if self.config_ids.filtered(lambda c: c.company_id != self.company_id):
                raise UserError(_("Semua Point of Sale yang dipilih harus milik company wizard."))
            return Config.browse(self.config_ids.ids)
        return Config.search([("company_id", "=", self.company_id.id), ("active", "=", True)], order="id")

    # --------------------------------------------------------------------------------------------------- helper
    def _efn_env(self, company):
        return self.sudo().with_company(company).env

    @api.model
    def _line(self, config, code, status, message):
        return {"config_id": config.id if config else False, "code": code, "status": status, "message": message}

    @api.model
    def _efn_try(self, check):
        """Jalankan cek CE (raise UserError/ValidationError); kembalikan pesan bila gagal, else False."""
        try:
            check()
        except UserError as exc:
            return _exc_text(exc)
        return False

    def _efn_chart_ref(self, company, xmlid):
        # Pola CE pos_config.py `_create_journal_and_payment_methods` (allowed_company_ids = root company).
        chart = self.env["account.chart.template"].sudo().with_context(allowed_company_ids=company.root_id.ids)
        return chart.ref(xmlid, raise_if_not_found=False)

    def _efn_xmlid_methods(self, company, prefix):
        domain = [("module", "=", "efn_setup_pos"), ("model", "=", "pos.payment.method"), ("name", "=like", prefix + "%")]
        data = self.env["ir.model.data"].sudo().search(domain)
        methods = self._efn_env(company)["pos.payment.method"].browse(data.mapped("res_id")).exists()
        return methods.filtered(lambda m: m.active and m.company_id == company)

    def _efn_sfx(self, company):
        code = company.efn_brand_code or ""
        return BRAND_XMLID_SUFFIX.get(code, code.lower())

    def _efn_ref(self, xmlid):
        record = self.env.ref(xmlid, raise_if_not_found=False)
        return record.sudo() if record else record

    def _efn_menu_products_without_tax(self, company):
        env = self._efn_env(company)
        templates = env["product.template"].search(
            [("company_id", "=", company.id), ("sale_ok", "=", True), ("available_in_pos", "=", True)]
        )
        return templates.filtered(
            lambda t: not t.taxes_id.filtered(lambda tax: tax.company_id == company and tax.type_tax_use == "sale")
        )

    # ---------------------------------------------------------------------------------------- apply: company
    def _efn_apply_company(self, company, configs):
        company = company.with_env(self._efn_env(company))
        steps = []
        if self.opt_company_accounts:
            steps.append(("C-01", lambda: self._efn_fix_company_accounts(company)))
        steps.append(("C-07", lambda: self._efn_fix_outstanding_accounts(company, configs)))
        if self.opt_menu_taxes:
            steps.append(("C-09", lambda: self._efn_fix_menu_taxes(company)))
        lines = []
        for code, step in steps:
            try:
                with self.env.cr.savepoint():
                    status, message = step()
            except Exception as exc:  # noqa: BLE001 - savepoint di-rollback; dilaporkan sebagai baris blocked
                self.env.invalidate_all(flush=False)
                _logger.warning("EFN G54: langkah %s company %s gagal (%s)", code, company.id, type(exc).__name__)
                status, message = "blocked", _("Gagal: %s", _exc_text(exc))
            if status == "fixed":
                _logger.info("EFN G54 apply: company %s langkah %s", company.id, code)
            lines.append(self._line(None, code, status, message))
        return lines

    def _efn_fix_company_accounts(self, company):
        wanted = (
            ("account_default_pos_receivable_account_id", "l10n_id_11210011"),
            ("default_cash_difference_income_account_id", "l10n_id_99900002"),
            ("default_cash_difference_expense_account_id", "l10n_id_99900001"),
        )
        vals, missing = {}, []
        for fname, xmlid in wanted:
            if company[fname]:
                continue
            account = self._efn_chart_ref(company, xmlid)
            if account:
                vals[fname] = account.id
            else:
                missing.append(fname)
        if vals:
            company.write(vals)
        if missing:
            return "blocked", _("Akun company kosong dan tidak ada di CoA (isi manual): %s", ", ".join(missing))
        if vals:
            return "fixed", _("Diisi dari CoA l10n_id: %s", ", ".join(vals))
        return "ok", _("Akun piutang PoS dan selisih kas company sudah terisi.")

    def _efn_fix_outstanding_accounts(self, company, configs):
        methods = (
            self._efn_xmlid_methods(company, EDC_PREFIX)
            | self._efn_xmlid_methods(company, QRIS_PREFIX)
            | configs.with_env(company.env).payment_method_ids
        )
        methods = methods.filtered(
            lambda m: m.company_id == company
            and m.journal_id.type == "bank"
            and not m.efn_is_loyalty_points
            and not m.outstanding_account_id
        )
        if not methods:
            return "ok", _("Metode bank (EDC/QRIS) sudah punya akun outstanding.")
        account = self._efn_chart_ref(company, "account_journal_payment_debit_account_id") or company.transfer_account_id
        if not account:
            return "blocked", _("Tidak ada akun outstanding/transfer di CoA company; isi manual di metode bayar.")
        busy = methods.filtered("open_session_ids")
        (methods - busy).write({"outstanding_account_id": account.id})
        if busy:
            return "warning", _(
                "Akun outstanding diisi untuk %(done)s metode; dilewati (sesi terbuka): %(busy)s",
                done=len(methods - busy),
                busy=", ".join(busy.mapped("name")),
            )
        return "fixed", _("Akun outstanding diisi: %s", ", ".join(methods.mapped("name")))

    def _efn_fix_menu_taxes(self, company):
        products = self._efn_menu_products_without_tax(company)
        if not products:
            return "ok", _("Semua produk menu POS company sudah berpajak penjualan.")
        tax = company.account_sale_tax_id
        if not tax:
            message = _("%s produk menu tanpa pajak, tetapi company tidak punya pajak penjualan default.", len(products))
            return "warning", message
        products.write({"taxes_id": [Command.link(tax.id)]})
        return "fixed", _("%(count)s produk menu diberi pajak default %(tax)s.", count=len(products), tax=tax.name)

    # ----------------------------------------------------------------------------------------- apply: config
    def _efn_apply_config(self, config):
        company = self.company_id
        config = config.with_env(self._efn_env(company))
        if config.session_ids.filtered(lambda s: s.state != "closed"):
            message = _("Sesi belum ditutup: config dilewati, jalankan lagi setelah sesi ditutup.")
            return [self._line(config, "P15", "warning", message)]
        try:
            with self.env.cr.savepoint():
                lines = self._efn_fix_journals(config, company)
                lines += self._efn_fix_sequences(config)
                commands = []
                lines += self._efn_fix_cash(config, company, commands)
                lines += self._efn_fix_bank_methods(config, company, commands)
                if commands:
                    config.write({"payment_method_ids": commands})
                lines += self._efn_fix_points(config)
        except Exception as exc:  # noqa: BLE001 - savepoint di-rollback; config lain tetap diproses
            self.env.invalidate_all(flush=False)
            _logger.warning("EFN G54: apply config %s gagal (%s)", config.id, type(exc).__name__)
            return [self._line(config, "C-00", "blocked", _("Apply dibatalkan untuk POS ini: %s", _exc_text(exc)))]
        fixed = [line["code"] for line in lines if line["status"] == "fixed"]
        if fixed:
            _logger.info("EFN G54 apply: company %s config %s langkah %s", company.id, config.id, ",".join(fixed))
        return lines

    def _efn_fix_journals(self, config, company):
        Journal = config.env["account.journal"]
        vals, lines = {}, []
        journal = config.journal_id
        if not journal or journal.company_id != company or journal.type not in ("general", "sale"):
            vals["journal_id"] = Journal._ensure_company_account_journal().id
        invoice = config.invoice_journal_id
        if not invoice or invoice.company_id != company or invoice.type != "sale":
            invoice = Journal.search([("company_id", "=", company.id), ("type", "=", "sale")], limit=1)
            if invoice:
                vals["invoice_journal_id"] = invoice.id
            else:
                lines.append(self._line(config, "C-02", "blocked", _("Company tidak punya jurnal penjualan (invoice).")))
        if vals:
            config.write(vals)
            lines.append(self._line(config, "C-02", "fixed", _("Jurnal diisi: %s", ", ".join(vals))))
        elif not lines:
            lines.append(self._line(config, "C-02", "ok", _("Jurnal sesi dan invoice sudah benar.")))
        return lines

    def _efn_fix_sequences(self, config):
        if all(config[fname] for fname in SEQUENCE_FIELDS):
            return [self._line(config, "C-03", "ok", _("Sequence order lengkap."))]
        if config.env["pos.order"].search_count([("session_id.config_id", "=", config.id)], limit=1):
            return [
                self._line(
                    config, "C-03", "blocked", _("Sequence order hilang tetapi POS sudah punya order: perbaiki manual.")
                )
            ]
        config._create_sequences()
        return [self._line(config, "C-03", "fixed", _("Sequence order dibuat."))]

    def _efn_fix_cash(self, config, company, commands):
        if not self.opt_cash:
            return []
        cash = config.payment_method_ids.filtered("is_cash_count")
        exclusive = cash.filtered(lambda m: m.config_ids == config)
        shared = cash - exclusive
        commands += [Command.unlink(method.id) for method in shared]
        shared_note = _(" Metode tunai bersama dilepas: %s.", ", ".join(shared.mapped("name"))) if shared else ""
        if exclusive:
            status = "fixed" if shared else "ok"
            return [self._line(config, "C-04", status, _("Metode tunai eksklusif sudah ada.") + shared_note)]
        Journal = config.env["account.journal"]
        name = f"Kas {config.name}"
        domain = [("type", "=", "cash"), ("company_id", "=", company.id), ("name", "=", name)]
        journal = Journal.search(domain + [("pos_payment_method_ids", "=", False)], limit=1)
        if not journal:
            taken = set(Journal.with_context(active_test=False).search([("company_id", "=", company.id)]).mapped("code"))
            code = cash_journal_code(company.efn_brand_code, taken)
            if not code:
                return [
                    self._line(
                        config,
                        "C-04",
                        "blocked",
                        _("Kode jurnal kas tidak bisa dibuat otomatis (kode brand kosong/>= 4 huruf/habis): buat manual."),
                    )
                ]
            # default_account_id TIDAK diisi: CE membuat akun kas per jurnal (prefix cash l10n_id) dan mengisi
            # loss/profit dari akun selisih kas company (account_journal.py _fill_missing_values).
            journal = Journal.create({"name": name, "code": code, "type": "cash", "company_id": company.id})
        method = config.env["pos.payment.method"].create(
            {
                "name": f"Tunai {config.name}",
                "journal_id": journal.id,
                "company_id": company.id,
                "payment_method_type": "none",
            }
        )
        commands.append(Command.link(method.id))
        return [self._line(config, "C-04", "fixed", _("Tunai dibuat: jurnal %s.", journal.code) + shared_note)]

    def _efn_fix_bank_methods(self, config, company, commands):
        lines = []
        for enabled, prefix, code, label in (
            (self.opt_edc, EDC_PREFIX, "C-05", _("EDC manual")),
            (self.opt_qris_manual, QRIS_PREFIX, "C-06", _("QRIS manual")),
        ):
            if not enabled:
                continue
            method = self._efn_xmlid_methods(company, prefix)[:1]
            if not method:
                message = _("Company tidak punya metode %s (data efn_setup_pos).", label)
                lines.append(self._line(config, code, "warning", message))
            elif method in config.payment_method_ids:
                lines.append(self._line(config, code, "ok", _("%s sudah ditautkan.", label)))
            else:
                commands.append(Command.link(method.id))
                lines.append(self._line(config, code, "fixed", _("%s ditautkan.", label)))
        return lines

    def _efn_fix_points(self, config):
        if not config.efn_loyalty_redeem_enabled:
            return [self._line(config, "C-08", "ok", _("Tukar poin nonaktif: Poin EFN tidak ditautkan."))]
        if config._efn_loyalty_points_method_record():
            return [self._line(config, "C-08", "ok", _("Poin EFN sudah ditautkan."))]
        if config._efn_setup_link_points_method():
            return [self._line(config, "C-08", "fixed", _("Poin EFN ditautkan (tukar poin aktif)."))]
        return [self._line(config, "C-08", "warning", _("Tukar poin aktif tetapi company tidak punya metode Poin EFN."))]

    # ------------------------------------------------------------------------------------------------ cek
    def _efn_check_company(self, company):
        company = company.with_env(self._efn_env(company))
        lines = []
        missing = self._efn_menu_products_without_tax(company)
        if missing:
            message = _("%s produk menu POS tanpa pajak penjualan company.", len(missing))
            lines.append(self._line(None, "C-09", "warning", message))
        pb1 = self._efn_ref(f"efn_setup_company.tax_pb1_10_{self._efn_sfx(company)}")
        if pb1 and pb1.company_id == company:
            unaccounted = pb1.invoice_repartition_line_ids.filtered(
                lambda r: r.repartition_type == "tax" and not r.account_id
            )
            if unaccounted:
                lines.append(
                    self._line(
                        None,
                        "W-04",
                        "warning",
                        _("Repartisi pajak %s tanpa akun liabilitas (GO-LIVE blocker Finance).", pb1.name),
                    )
                )
        return lines

    def _efn_check_config(self, config):
        company = self.company_id
        env = self._efn_env(company)
        config = config.with_env(env)
        lines = []

        def add(code, problem, ok_message, bad_status="blocked"):
            lines.append(self._line(config, code, bad_status if problem else "ok", problem or ok_message))

        add("P01", not config.company_has_template and _("Company belum punya CoA."), _("CoA terpasang."))
        add(
            "P02",
            not company.account_fiscal_country_id and _("Company tanpa negara fiskal (isi manual)."),
            _("Negara fiskal terisi."),
        )
        add("P03", self._efn_try(config._check_pricelists), _("Pricelist valid."))
        add("P04", self._efn_try(config._check_company_payment), _("Metode bayar milik company POS."))
        add("P05", self._efn_try(config._check_currencies), _("Mata uang konsisten."))
        add("P06", self._efn_try(config._check_profit_loss_cash_journal), _("Jurnal kas punya akun laba/rugi selisih."))
        add("P07", self._efn_try(config._check_payment_method_ids), _("Ada metode bayar."))
        shared = config.payment_method_ids.filtered(lambda m: m.is_cash_count and len(m.config_ids) > 1)
        add(
            "P08",
            shared and _("Metode tunai dipakai lebih dari satu POS: %s", ", ".join(shared.mapped("name"))),
            _("Metode tunai tidak dipakai bersama."),
        )
        add("P09", self._efn_try(config._check_payment_method_ids_journal), _("Satu jurnal kas per metode tunai."))
        picking_type = config.picking_type_id
        add(
            "P10",
            (not picking_type or picking_type.code != "outgoing" or picking_type.warehouse_id.company_id != company)
            and _("Operation type POS kosong/bukan pengiriman gudang company (isi manual)."),
            _("Operation type POS benar."),
        )
        journal = config.journal_id
        add(
            "P11",
            (not journal or journal.company_id != company or journal.type not in ("general", "sale"))
            and _("Jurnal sesi POS kosong/salah company."),
            _("Jurnal sesi POS benar."),
        )
        invoice = config.invoice_journal_id
        add(
            "P12",
            (not invoice or invoice.company_id != company or invoice.type != "sale")
            and _("Jurnal invoice kosong/salah company."),
            _("Jurnal invoice benar."),
        )
        add(
            "P13",
            not all(config[fname] for fname in SEQUENCE_FIELDS) and _("Sequence order POS tidak lengkap."),
            _("Sequence order lengkap."),
        )
        session_seq = env["ir.sequence"].search_count(
            [("code", "=", "pos.session"), ("company_id", "in", [company.id, False])], limit=1
        )
        add("P14", not session_seq and _("Sequence sesi POS (pos.session) tidak ada."), _("Sequence sesi ada."))
        add(
            "P15",
            config.session_ids.filtered(lambda s: s.state != "closed") and _("Ada sesi yang belum ditutup."),
            _("Tidak ada sesi terbuka."),
            bad_status="warning",
        )
        violated = journal and company._get_violated_lock_dates(fields.Date.context_today(config), True, journal)
        add(
            "P16",
            violated and _("Tanggal hari ini melanggar lock date: %s", env["res.company"]._format_lock_dates(violated)),
            _("Tidak melanggar lock date."),
        )
        add(
            "P17",
            not company.account_default_pos_receivable_account_id and _("Akun piutang PoS company kosong (C-01)."),
            _("Akun piutang PoS terisi."),
        )
        cash_journals = config.payment_method_ids.filtered("is_cash_count").journal_id
        add(
            "P18",
            cash_journals.filtered(lambda j: not j.loss_account_id or not j.profit_account_id)
            and _("Jurnal kas tanpa akun selisih: penutupan sesi berselisih gagal."),
            _("Akun selisih kas penutupan tersedia."),
        )
        users = env["res.users"].search(
            [("company_ids", "in", company.ids), ("share", "=", False), ("id", "!=", SUPERUSER_ID)]
        )
        add(
            "P19",
            not any(user.has_group("point_of_sale.group_pos_user") for user in users)
            and _("Belum ada user POS (non-superuser) di company ini."),
            _("Ada user POS di company."),
            bad_status="warning",
        )
        lines += self._efn_check_warnings(config, company)
        return lines

    def _efn_check_warnings(self, config, company):
        lines = []
        if company.efn_business_type == "fnb" and "use_presets" in config._fields:
            sfx = self._efn_sfx(company)
            fp_dine_in = self._efn_ref(f"efn_setup_company.fiscal_position_{sfx}_dine_in")
            has_tables = "floor_ids" in config._fields and bool(config.floor_ids.table_ids)
            preset = self._efn_ref(f"efn_setup_pos.preset_{sfx}_{'dine_in' if has_tables else 'takeaway'}")
            problems = []
            if not config.use_presets or not config.default_preset_id:
                problems.append(_("preset belum aktif/default kosong"))
            if fp_dine_in and config.default_fiscal_position_id != fp_dine_in:
                problems.append(_("default fiscal position bukan %s", fp_dine_in.name))
            if problems:
                advice = _(" Usulan default preset: %s (Q-18).", preset.name) if preset else ""
                lines.append(self._line(config, "W-01", "warning", "; ".join(problems) + "." + advice))
        if not config.receipt_header:
            lines.append(self._line(config, "W-02", "warning", _("Header struk kosong (nama PT, NPWP, alamat; Q-1).")))
        employees = config.env["hr.employee"].search_count([("company_id", "=", company.id)], limit=1)
        if config.module_pos_hr and not employees:
            lines.append(self._line(config, "W-03", "warning", _("pos_hr aktif tetapi company tanpa karyawan aktif.")))
        return lines


class EfnPosSetupWizardLine(models.TransientModel):
    _name = "efn.pos.setup.wizard.line"
    _description = "EFN: hasil cek/perbaikan konfigurasi POS (G54)"
    _order = "id"
    _check_company_auto = True

    wizard_id = fields.Many2one("efn.pos.setup.wizard", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="wizard_id.company_id", store=True)
    config_id = fields.Many2one("pos.config", string="Point of Sale", check_company=True, ondelete="cascade")
    code = fields.Char(string="Kode", required=True)
    status = fields.Selection(STATUS_SELECTION, required=True)
    message = fields.Char(string="Pesan")
