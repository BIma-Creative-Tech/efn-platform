"""`efn.esb.identity`: identitas pengguna ESB + shadow user Odoo yang diarsipkan.

G66b; ADR-051 A1 (Proposed); docs/04 §30.17.11; fit-gap efn-odoo-functional 25 Nov 2026 "G66b".

Alur: prodplan (setelah login ESB sukses) memanggil
`POST /json/2/efn.esb.identity/efn_esb_provision` body `{"payload": {...}}` dengan API key user teknis
`efn_esb_provisioner`. Metode ini:

1. Cek pemanggil: grup `group_esb_provisioner`, Internal User, aktif, TANPA grup lain (fail closed).
   Context klien dibuang (klien /json/2 tidak bisa menyetel `active_test`, `allowed_company_ids`, dsb.).
2. Payload tertutup: kunci persis `PAYLOAD_KEYS`; kunci lain (termasuk `password`/`token`) ditolak.
3. PT dari cabang ESB terdaftar (`efn.insight.esb.branch`, aktif), bukan dari teks `company_code`.
4. Upsert per `(esb_account, esb_username)` di bawah advisory lock. User baru: `active=False`, grup
   eksplisit hanya `base.group_user` (+ `base.group_multi_company` bila >1 PT, sama dengan CE), tanpa
   password, tanpa email, login `esb:<akun>:<username>`, context `no_reset_password`. Login yang sudah
   dipakai user mana pun ditolak (tidak pernah menaut ke user yang ada). Tidak pernah menghapus.
5. Peran PP = cermin data (`esb_pp_roles`), ditimpa setiap login; TIDAK menjadi res.groups (SEC-228).

Log tanpa PII: hanya id identity/user, flag, jumlah peran/cabang (tanpa username, nama, login).
Galat: `UserError("efn_esb_identity:<kode>")` (json2 -> HTTP 422) atau
`AccessError("efn_esb_identity:access_denied")` (HTTP 403); pesan tanpa detail internal.
"""

import json
import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tools import frozendict

from odoo.addons.efn_insight.lib.insight_token import ESB_BRANDS, MAX_ID, PP_ROLES
from odoo.addons.efn_insight.models.efn_insight_esb_branch import ESB_BRAND_SELECTION

_logger = logging.getLogger(__name__)

GROUP_PROVISIONER = "efn_esb_identity.group_esb_provisioner"
PAYLOAD_KEYS = frozenset(
    {"esb_account", "username", "full_name", "company_code", "role_id", "pp_roles", "branch_ids"}
)
MAX_PAYLOAD_BYTES = 16 * 1024  # sama dengan batas body haproxy (docs/04 §30.17.11)
MAX_BRANCHES = 200
MAX_FULL_NAME = 128
MAX_COMPANY_CODE = 64
USERNAME_RE = re.compile(r"^[a-z0-9._@-]{1,64}$")
LOGIN_PREFIX = "esb:"
STALE_LOGIN_DAYS = 90
ERROR_CODES = frozenset(
    {"invalid_input", "payload_too_large", "esb_provision_no_company", "esb_provision_login_conflict"}
)
STATE_SELECTION = [
    ("shadow", "Shadow (arsip)"),
    ("revoked", "Dicabut (tidak login > 90 hari)"),
    ("activated", "Diaktifkan (migrasi)"),
]


def provision_error(code):
    assert code in ERROR_CODES, code
    return UserError(f"efn_esb_identity:{code}")


def shadow_login(account, username):
    return f"{LOGIN_PREFIX}{account}:{username}"


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_clean_text(value, max_len):
    """String <= max_len tanpa karakter kontrol."""
    if not isinstance(value, str) or len(value) > max_len:
        return False
    return not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _payload_size(payload):
    try:
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        raise provision_error("invalid_input") from None
    return len(text.encode("utf-8"))


def validate_payload(payload):
    """Payload tertutup -> dict ternormalisasi. Galat apa pun -> `invalid_input` / `payload_too_large`.

    Nilai payload tidak pernah dimasukkan ke pesan galat atau log."""
    if not isinstance(payload, dict):
        raise provision_error("invalid_input")
    if _payload_size(payload) > MAX_PAYLOAD_BYTES:
        raise provision_error("payload_too_large")
    if set(payload) != PAYLOAD_KEYS:
        raise provision_error("invalid_input")

    account = payload["esb_account"]
    if not isinstance(account, str) or account not in ESB_BRANDS:
        raise provision_error("invalid_input")

    username = payload["username"]
    if not isinstance(username, str):
        raise provision_error("invalid_input")
    username = username.strip().lower()
    if not USERNAME_RE.match(username):
        raise provision_error("invalid_input")

    full_name = payload["full_name"]
    if not isinstance(full_name, str):
        raise provision_error("invalid_input")
    full_name = full_name.strip()
    if not _is_clean_text(full_name, MAX_FULL_NAME):
        raise provision_error("invalid_input")

    company_code = payload["company_code"]
    if company_code is not None and not _is_clean_text(company_code, MAX_COMPANY_CODE):
        raise provision_error("invalid_input")

    role_id = payload["role_id"]
    if role_id is not None and (not _is_int(role_id) or not 0 <= role_id <= MAX_ID):
        raise provision_error("invalid_input")

    pp_roles = payload["pp_roles"]
    if not isinstance(pp_roles, list) or len(set(map(str, pp_roles))) != len(pp_roles):
        raise provision_error("invalid_input")
    if not all(isinstance(role, str) and role in PP_ROLES for role in pp_roles):
        raise provision_error("invalid_input")

    branch_ids = payload["branch_ids"]
    if not isinstance(branch_ids, list) or len(branch_ids) > MAX_BRANCHES:
        raise provision_error("invalid_input")
    if not all(_is_int(bid) and 1 <= bid <= MAX_ID for bid in branch_ids):
        raise provision_error("invalid_input")

    return {
        "account": account,
        "username": username,
        "full_name": full_name or username,
        "company_code": (company_code or "").strip().upper(),
        "role_id": role_id or 0,
        "pp_roles": sorted(pp_roles),
        "branch_ids": sorted(set(branch_ids)),
    }


def check_caller(env):
    """User teknis provisioner (Internal User + group_esb_provisioner saja, aktif).

    Kembalikan env sudo dengan context bersih (context klien dibuang)."""
    bare = env(context=frozendict())
    denied = AccessError("efn_esb_identity:access_denied")
    user = bare.user
    if not user.has_group(GROUP_PROVISIONER) or user.share or not user.active:
        raise denied
    su = bare(su=True)
    allowed = su.ref("base.group_user") | su.ref(GROUP_PROVISIONER)
    allowed |= allowed.all_implied_ids
    if user.sudo().all_group_ids - allowed:
        _logger.warning("EFN esb_provision: user teknis #%s punya grup tambahan; RPC ditolak", user.id)
        raise denied
    return env(context=frozendict(active_test=True, no_reset_password=True), su=True)


class EfnEsbIdentity(models.Model):
    _name = "efn.esb.identity"
    _description = "Identitas pengguna ESB (shadow user Odoo)"
    _order = "company_id, esb_account, esb_username"
    _check_company_auto = True

    user_id = fields.Many2one(
        "res.users",
        string="User Odoo",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        context={"active_test": False},
        help="Shadow user internal yang diarsipkan. Tidak pernah ditautkan otomatis ke user yang ada.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="PT",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        help="PT pertama dari cabang ESB pengguna (efn.insight.esb.branch).",
    )
    esb_account = fields.Selection(ESB_BRAND_SELECTION, string="Akun ESB", required=True, readonly=True)
    esb_username = fields.Char(string="Username ESB", required=True, readonly=True)
    esb_role_id = fields.Integer(string="userRoleID ESB", readonly=True)
    esb_pp_roles = fields.Char(
        string="Peran PP (cermin ESB)",
        readonly=True,
        help="Cermin izin ESB saat login terakhir (pp_planner / pp_warehouse). BUKAN grup Odoo: "
        "saat migrasi peran ditetapkan manual lewat Penetapan Peran efn_role (SEC-228).",
    )
    esb_branch_ids = fields.Many2many(
        "efn.insight.esb.branch",
        "efn_esb_identity_branch_rel",
        "identity_id",
        "branch_id",
        string="Cabang ESB (cermin)",
        readonly=True,
    )
    first_login_at = fields.Datetime(string="Login ESB pertama", readonly=True)
    last_login_at = fields.Datetime(string="Login ESB terakhir", readonly=True, index=True)
    state = fields.Selection(STATE_SELECTION, string="Status", required=True, default="shadow", readonly=True)
    activated_at = fields.Datetime(string="Diaktifkan pada", readonly=True)

    _efn_identity_unique = models.Constraint(
        "UNIQUE(esb_account, esb_username)",
        "efn_esb_identity:duplicate — satu identitas per (akun ESB, username).",
    )
    _efn_identity_user_unique = models.Constraint(
        "UNIQUE(user_id)",
        "efn_esb_identity:user_duplicate — satu user Odoo hanya untuk satu identitas ESB.",
    )

    @api.depends("esb_account")
    def _compute_display_name(self):
        # Tanpa username (log audit res_name = display_name; minimisasi PDP).
        for rec in self:
            rec.display_name = f"ESB {rec.esb_account or '?'} #{rec.id or '-'}"

    # ================================================================== RPC prodplan
    @api.model
    def efn_esb_provision(self, payload):
        """docs/04 §30.17.11: upsert identitas + shadow user.

        Respons `{"identity_id": int, "created": bool}` (tanpa PII)."""
        env = check_caller(self.env)
        data = validate_payload(payload)
        account, username = data["account"], data["username"]

        branches = env["efn.insight.esb.branch"]
        if data["branch_ids"]:
            domain = [("esb_brand", "=", account), ("esb_branch_id", "in", data["branch_ids"])]
            branches = branches.search(domain)
        companies = branches.company_id.sorted("id")
        if not companies:
            raise provision_error("esb_provision_no_company")
        codes = set(companies.mapped("efn_brand_code"))
        if data["company_code"] and data["company_code"] not in codes:
            # Pengecekan silang saja (format companyCode ESB belum stabil, fit-gap §2), bukan penolakan.
            _logger.warning("EFN esb_provision: company_code tidak cocok dengan PT cabang (akun %s)", account)

        # Serialisasi per (akun, username): dua login bersamaan tidak membuat dua user.
        lock_key = f"efn_esb_identity:{account}:{username}"
        env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [lock_key])
        Identity = env["efn.esb.identity"]
        key_domain = [("esb_account", "=", account), ("esb_username", "=", username)]
        identity = Identity.search(key_domain, limit=1)
        now = fields.Datetime.now()
        mirror = {
            "esb_role_id": data["role_id"],
            "esb_pp_roles": ",".join(data["pp_roles"]) or False,
            "esb_branch_ids": [Command.set(branches.ids)],
            "company_id": companies[0].id,
            "last_login_at": now,
        }
        created = False
        if identity:
            if identity.state == "revoked":
                mirror["state"] = "shadow"
            identity.write(mirror)
            if identity.state != "activated":
                identity._efn_enforce_shadow_user(data["full_name"], companies)
        else:
            login = shadow_login(account, username)
            env["res.users"].flush_model(["login"])
            env.cr.execute("SELECT 1 FROM res_users WHERE lower(login) = %s LIMIT 1", [login])
            if env.cr.fetchone():
                _logger.warning("EFN esb_provision: login shadow sudah dipakai (akun %s); ditolak", account)
                raise provision_error("esb_provision_login_conflict")
            user_vals = {
                "name": data["full_name"],
                "login": login,
                "active": False,
                "company_id": companies[0].id,
                "company_ids": [Command.set(companies.ids)],
                "group_ids": [Command.set(self._efn_shadow_group_ids(env, companies))],
            }
            user = env["res.users"].create(user_vals)
            mirror.update(user_id=user.id, esb_account=account, esb_username=username, first_login_at=now)
            identity = Identity.create(mirror)
            created = True
        _logger.info(
            "esb_provision identity=%s uid=%s created=%s state=%s roles=%s branches=%s",
            identity.id,
            identity.user_id.id,
            created,
            identity.state,
            len(data["pp_roles"]),
            len(branches),
        )
        return {"identity_id": identity.id, "created": created}

    @api.model
    def _efn_shadow_group_ids(self, env, companies):
        """Grup eksplisit shadow user: Internal User saja (+ multi-company bila >1 PT, perilaku CE)."""
        groups = env.ref("base.group_user")
        if len(companies) > 1:
            groups |= env.ref("base.group_multi_company")
        return groups.ids

    def _efn_enforce_shadow_user(self, name, companies):
        """Shadow user (state != activated) dikembalikan ke invariannya: arsip, grup minimal, PT dari cabang.

        Hanya menulis bila berbeda (tidak membanjiri log audit res.users)."""
        self.ensure_one()
        user = self.user_id.with_context(active_test=False)
        vals = {}
        if user.active:
            # Unarchive manual sebelum `action_efn_activate` dibatalkan pada login berikutnya (fail safe).
            vals["active"] = False
        if user.name != name:
            vals["name"] = name
        if set(user.company_ids.ids) != set(companies.ids) or user.company_id not in companies:
            vals["company_ids"] = [Command.set(companies.ids)]
            vals["company_id"] = companies[0].id
        expected = self._efn_shadow_group_ids(self.env, companies)
        if set(user.group_ids.ids) != set(expected):
            vals["group_ids"] = [Command.set(expected)]
        if vals:
            user.write(vals)

    # ================================================================== cron
    @api.model
    def _cron_efn_esb_mark_stale(self, now=None):
        """Harian: shadow tanpa login ESB > STALE_LOGIN_DAYS -> `revoked`, cermin dikosongkan.

        User TIDAK dihapus (jejak audit + peta migrasi); login ESB berikutnya mengembalikan ke `shadow`."""
        now = now or fields.Datetime.now()
        cutoff = now - timedelta(days=STALE_LOGIN_DAYS)
        stale = self.sudo().search([("state", "=", "shadow"), ("last_login_at", "<", cutoff)])
        if stale:
            stale.write({"state": "revoked", "esb_pp_roles": False, "esb_branch_ids": [Command.clear()]})
        _logger.info("esb_identity stale: %s identitas ditandai revoked", len(stale))
        return len(stale)

    # ================================================================== aktivasi (migrasi)
    def action_efn_activate(self):
        """Superadmin saja. Prasyarat: login sudah diganti dari format `esb:` dan email kerja terisi.

        Hanya memindah state; unarchive, peran (efn_role), reset password, TOTP = UI CE standar (README)."""
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("efn_esb_identity:activate_superadmin_only — aktivasi hanya oleh Superadmin."))
        self.check_access("write")
        for rec in self:
            user = rec.user_id.sudo().with_context(active_test=False)
            if rec.state == "activated":
                raise UserError(_("efn_esb_identity:already_activated — identitas sudah diaktifkan."))
            if not user.login or user.login.lower().startswith(LOGIN_PREFIX) or not user.email:
                raise UserError(
                    _(
                        "efn_esb_identity:activate_prerequisite — ganti login dari format esb:<akun>:<username> "
                        "dan isi email kerja di user terlebih dahulu."
                    )
                )
        self.sudo().write({"state": "activated", "activated_at": fields.Datetime.now()})
        for rec in self:
            _logger.info("esb_identity activated identity=%s uid=%s by=%s", rec.id, rec.user_id.id, self.env.uid)
        return True
