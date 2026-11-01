"""Tanya Data: pertanyaan staf -> service text2sql -> tabel hasil (docs/04 §23, G38).

- Cakupan PT = PT yang dipilih di form ∩ PT yang boleh diakses user (`env.user.company_ids`). Odoo mengirim
  company_id + kode PT; ClickHouse menegakkan cakupan lewat row policy bertanda tangan (lapis kedua).
- Odoo tidak punya kredensial ClickHouse dan tidak memakai sudo untuk membaca data bisnis. Hasil disimpan sebagai log
  per user (record rule: hanya pemilik; auditor membaca semua di PT-nya).
- Kill switch env `TEXT2SQL_ENABLED` (default false). Pertanyaan/SQL/hasil tidak di-log server.
"""

import json
import logging
import os
import uuid

import requests
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.efn_core.models.efn_bff_client import plaintext_allowed

_logger = logging.getLogger(__name__)

URL_ENV = "EFN_TEXT2SQL_URL"
DEFAULT_URL = "https://text2sql:8443"
KEY_FILE_ENV = "EFN_TEXT2SQL_API_KEY_FILE"
DEFAULT_KEY_FILE = "/run/secrets/text2sql_api_key"
CA_FILE_ENV = "EFN_CA_FILE"
DEFAULT_CA_FILE = "/run/secrets/efn_ca_crt"
ENABLED_ENV = "TEXT2SQL_ENABLED"
TIMEOUT_SECONDS = 60
MAX_QUESTION = 500
COMPANY_CODES = frozenset({"EFN", "GL", "PB", "BCR", "BCF", "WTZ", "FARM"})
RESULT_FIELDS = frozenset({"state", "sql", "result_json", "row_count", "truncated", "engine", "error_code", "asked_at"})
ERROR_MESSAGES = {
    "text2sql_disabled": "Tanya Data belum aktif.",
    "llm_unavailable": "Layanan AI sedang tidak tersedia. Coba lagi beberapa saat lagi.",
    "clickhouse_unavailable": "Gudang data sedang tidak tersedia. Coba lagi beberapa saat lagi.",
    "llm_no_sql": "AI tidak menghasilkan query untuk pertanyaan ini. Coba tulis ulang pertanyaannya.",
    # parse_error = jawaban AI bukan SQL (biasanya AI menolak karena data itu tidak tersedia, mis. GL / tabel sistem)
    "parse_error": "Data untuk pertanyaan ini tidak tersedia di Tanya Data (mis. laporan keuangan GL), "
    "atau AI tidak bisa membuat query. Coba pertanyaan lain.",
    "no_table": "Pertanyaan tidak memakai data yang tersedia di Tanya Data.",
    "query_failed": "Query hasil AI gagal dijalankan. Coba tulis ulang pertanyaannya dengan lebih spesifik.",
    "invalid_request": "Pertanyaan tidak valid (3-500 karakter).",
    "unauthorized": "Konfigurasi kunci layanan Tanya Data salah. Hubungi admin.",
    "connection": "Layanan Tanya Data tidak dapat dihubungi. Hubungi admin.",
}
GUARD_MESSAGE = "Pertanyaan tidak bisa dijawab dengan aman dari data yang tersedia (kode: %s)."


def _enabled():
    return (os.environ.get(ENABLED_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


class EfnDataAsk(models.Model):
    _name = "efn.data.ask"
    _description = "EFN Tanya Data (Text-to-SQL)"
    _order = "id desc"
    _rec_name = "question"

    user_id = fields.Many2one("res.users", required=True, readonly=True, default=lambda self: self.env.user, index=True)
    company_id = fields.Many2one("res.company", required=True, readonly=True, default=lambda self: self.env.company)
    company_ids = fields.Many2many(
        "res.company",
        "efn_data_ask_res_company_rel",
        "ask_id",
        "company_id",
        string="PT",
        # Default = PT yang aktif di pemilih perusahaan (switcher) Odoo; pilihan dibatasi PT aktif (record rule).
        default=lambda self: self.env.companies,
        help="Data hanya dari PT ini. Aktifkan PT lain lewat pemilih perusahaan di kanan atas untuk menambahkannya.",
    )
    question = fields.Text(string="Pertanyaan", required=True)
    state = fields.Selection(
        [("draft", "Draft"), ("done", "Terjawab"), ("rejected", "Tidak bisa dijawab"), ("error", "Gagal")],
        default="draft",
        required=True,
        readonly=True,
    )
    asked_at = fields.Datetime(readonly=True)
    sql = fields.Text(string="SQL (AI-generated)", readonly=True)
    result_json = fields.Text(readonly=True)
    row_count = fields.Integer(readonly=True)
    truncated = fields.Boolean(readonly=True)
    engine = fields.Char(string="Model AI", readonly=True)
    error_code = fields.Char(readonly=True)
    message = fields.Char(compute="_compute_result_html")
    result_html = fields.Html(string="Hasil", compute="_compute_result_html", sanitize=True)

    # ------------------------------------------------------------------ tampilan
    @api.depends("result_json", "state", "error_code")
    def _compute_result_html(self):
        for rec in self:
            rec.message = rec._efn_message()
            rec.result_html = rec._efn_render_table()

    def _efn_message(self):
        if self.state == "rejected":
            code = self.error_code or "-"
            return ERROR_MESSAGES.get(code) or GUARD_MESSAGE % code
        if self.state == "error":
            return ERROR_MESSAGES.get(self.error_code or "", ERROR_MESSAGES["connection"])
        if self.state == "done":
            note = " (dipotong, maks 1000 baris)" if self.truncated else ""
            return f"{self.row_count} baris{note}. Jawaban dibuat AI: periksa SQL sebelum dipakai untuk keputusan."
        return ""

    def _efn_render_table(self):
        if self.state != "done" or not self.result_json:
            return False
        try:
            data = json.loads(self.result_json)
        except ValueError:
            return False
        columns = data.get("columns") or []
        rows = data.get("rows") or []
        head = Markup("").join(Markup("<th>%s</th>") % escape(str(c)) for c in columns)
        body = Markup("").join(
            Markup("<tr>%s</tr>") % Markup("").join(Markup("<td>%s</td>") % escape(self._efn_cell(v)) for v in row)
            for row in rows
        )
        return Markup(
            '<table class="table table-sm table-striped o_efn_data_ask"><thead><tr>%s</tr></thead>'
            "<tbody>%s</tbody></table>"
        ) % (head, body)

    @staticmethod
    def _efn_cell(value):
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:,.2f}".rstrip("0").rstrip(".") if value % 1 else f"{value:,.0f}"
        if isinstance(value, int) and not isinstance(value, bool):
            return f"{value:,}"
        return str(value)

    # ------------------------------------------------------------------ integritas
    def write(self, vals):
        # Hasil hanya ditulis action_ask (sudo); user tidak bisa memalsukan jawaban lewat RPC write.
        if not self.env.su and RESULT_FIELDS & set(vals):
            raise AccessError(_("Hasil Tanya Data hanya diisi sistem."))
        if not self.env.su and ({"question", "company_ids"} & set(vals)) and any(r.state != "draft" for r in self):
            raise UserError(_("Pertanyaan yang sudah dijawab tidak bisa diubah; buat pertanyaan baru."))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                if RESULT_FIELDS & set(vals) or vals.get("user_id", self.env.uid) != self.env.uid:
                    raise AccessError(_("Hasil Tanya Data hanya diisi sistem."))
        return super().create(vals_list)

    # ------------------------------------------------------------------ aksi
    def action_ask(self):
        """Tombol "Tanya" (RPC publik; allowlist efn_core/tests/test_rpc_surface.py).

        Cek: grup Tanya Data, pemilik record, state draft, PT terpilih ⊆ PT user, kill switch. Lalu POST ke text2sql.
        """
        self.ensure_one()
        user = self.env.user
        if not user.has_group("efn_data_ask.group_data_ask"):
            raise AccessError(_("Anda tidak punya akses Tanya Data."))
        if self.user_id != user:
            raise AccessError(_("Hanya penanya yang bisa menjalankan pertanyaan ini."))
        if self.state != "draft":
            raise UserError(_("Pertanyaan ini sudah dijalankan; buat pertanyaan baru."))
        question = " ".join((self.question or "").split())
        if not 3 <= len(question) <= MAX_QUESTION:
            raise UserError(_("Pertanyaan harus 3-500 karakter."))
        # PT dibaca dari tabel relasi (SQL): membaca company_ids sebagai user menyaring PT yang tidak bisa ia lihat
        # (record rule res.company), sehingga PT di luar akses "hilang" dan lolos pemeriksaan.
        self.flush_recordset(["company_ids"])
        self.env.cr.execute("SELECT company_id FROM efn_data_ask_res_company_rel WHERE ask_id = %s", (self.id,))
        company_ids = {row[0] for row in self.env.cr.fetchall()} or {self.env.company.id}
        if company_ids - set(user.company_ids.ids):
            raise AccessError(_("Ada PT yang dipilih di luar akses Anda."))
        companies = self.env["res.company"].sudo().browse(sorted(company_ids))
        if not _enabled():
            raise UserError(_(ERROR_MESSAGES["text2sql_disabled"]))
        payload = {
            "question": question,
            "company_ids": sorted(companies.ids),
            "company_codes": sorted({c.efn_brand_code for c in companies if c.efn_brand_code in COMPANY_CODES}),
            "request_id": f"odoo-{self.id}-{uuid.uuid4().hex[:8]}",
        }
        status, body = self._efn_text2sql_post(payload)
        vals = {"asked_at": fields.Datetime.now()}
        result = body if isinstance(body, dict) else {}
        if status == 200 and result.get("status") == "ok":
            columns = result.get("columns") if isinstance(result.get("columns"), list) else []
            rows = result.get("rows") if isinstance(result.get("rows"), list) else []
            vals.update(
                state="done",
                sql=str(result.get("sql") or ""),
                result_json=json.dumps({"columns": columns, "rows": rows[:1000]}, ensure_ascii=False, default=str),
                row_count=len(rows[:1000]),
                truncated=bool(result.get("truncated")),
                engine=str(result.get("engine") or "")[:128],
                error_code=False,
            )
        elif status == 200 and result.get("status") == "rejected":
            vals.update(state="rejected", error_code=str(result.get("code") or "rejected")[:64])
        else:
            code = str(result.get("code") or ("unauthorized" if status == 401 else "connection"))[:64]
            vals.update(state="error", error_code=code)
        self.sudo().write(vals)
        _logger.info(
            "EFN_DATA_ASK id=%s user=%s state=%s code=%s", self.id, user.id, vals["state"], vals.get("error_code")
        )
        return True

    def action_new_question(self):
        """Buat pertanyaan baru dengan PT yang sama (RPC publik; hanya membuat record milik user sendiri)."""
        self.ensure_one()
        if self.user_id != self.env.user:
            raise AccessError(_("Hanya penanya yang bisa menyalin pertanyaan ini."))
        new = self.create({"question": self.question, "company_ids": [(6, 0, self.company_ids.ids)]})
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": new.id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------------ HTTP
    @api.model
    def _efn_text2sql_post(self, payload):
        """POST /v1/ask. Mengembalikan (status, body). Tidak pernah me-log payload/hasil."""
        url = (os.environ.get(URL_ENV) or DEFAULT_URL).strip().rstrip("/")
        lowered = url.lower()
        verify = True
        if lowered.startswith("https://"):
            verify = os.environ.get(CA_FILE_ENV) or DEFAULT_CA_FILE
            if not os.path.isfile(verify):
                _logger.error("EFN Tanya Data: CA internal tidak terbaca (cek %s)", CA_FILE_ENV)
                return 0, {"code": "connection"}
        elif not (lowered.startswith("http://") and plaintext_allowed()):
            _logger.error("EFN Tanya Data: %s wajib https", URL_ENV)
            return 0, {"code": "connection"}
        try:
            with open(os.environ.get(KEY_FILE_ENV) or DEFAULT_KEY_FILE, encoding="utf-8") as handle:
                key = handle.read().strip()
        except OSError:
            _logger.error("EFN Tanya Data: file service key tidak terbaca (cek %s)", KEY_FILE_ENV)
            return 0, {"code": "unauthorized"}
        try:
            resp = requests.post(
                f"{url}/v1/ask",
                json=payload,
                headers={"X-EFN-Service-Key": key, "Accept": "application/json"},
                timeout=TIMEOUT_SECONDS,
                allow_redirects=False,
                verify=verify,
            )
        except requests.RequestException as exc:
            _logger.warning("EFN Tanya Data: text2sql tidak terjangkau (%s)", type(exc).__name__)
            return 0, {"code": "connection"}
        try:
            body = resp.json()
        except ValueError:
            body = {}
        return resp.status_code, body
