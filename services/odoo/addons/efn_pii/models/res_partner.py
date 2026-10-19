"""`res.partner`: kelas subjek terlindungi + enkripsi kolom PII (E-38a, ADR-036 §0.2-§0.6).

- Protect-by-default: penanda `efn_pii_protected` dihitung SAAT create (precompute, dalam panggilan yang
  sama) dari aturan §0.2. Pengecualian K-a hanya lewat context server `efn_pii_skip_default`
  (`pii_model.SKIP_TOKEN`; nilai string biasa dari klien RPC tidak berlaku).
- Field: name, complete_name, street, street2, city, zip, phone, email, email_normalized, vat.
- Format `efnpii2:` terikat id (SEC-138), token nama `efn_name_tokens` (§0.5), SEC-136, SEC-137.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import SQL, split_every

from . import keyring as kr
from . import pii_model as pm
from .fields_pii import (
    NORMALIZERS,
    PROTECTED_FIELD,
    TOKENS_FIELD,
    EfnPiiChar,
    bidx_field_name,
    complete_term,
    keyring_for,
    raise_locked,
    record_name_tokens,
)

_logger = logging.getLogger(__name__)

PII_FIELDS = (
    "name",
    "complete_name",
    "street",
    "street2",
    "city",
    "zip",
    "phone",
    "email",
    "email_normalized",
    "vat",
)
BIDX_SOURCES = ("phone", "email", "vat")
# Field turunan non-stored yang di-tracking `mail` dan memuat alamat plaintext.
TRACKING_EXCLUDED = frozenset(PII_FIELDS) | {"contact_address_inline"}
GIN_INDEX = "res_partner_efn_name_tokens_gin"
POSITIVE_PHONE_OPERATORS = ("ilike", "like", "=ilike", "=like", "=")


class ResPartner(models.Model):
    _inherit = "res.partner"

    # atribut yang dibaca EfnPiiChar / pii_model (modul jembatan, mis. efn_pii_l10n_id, memperluas daftar ini)
    _efn_pii_fields = PII_FIELDS
    _efn_pii_bidx_sources = BIDX_SOURCES
    _efn_pii_bidx = {
        "phone": ("efn_phone_bidx", "phone"),
        "email": ("efn_email_bidx", "email"),
        # email_normalized = normalize(email) -> blind index email yang sama (subkunci "email")
        "email_normalized": ("efn_email_bidx", "email"),
        "vat": ("efn_vat_bidx", "vat"),
    }
    _efn_pii_token_fields = ("name", "complete_name")

    efn_pii_protected = fields.Boolean(
        string="EFN: Data Pribadi Terlindungi",
        compute="_compute_efn_pii_protected",
        store=True,
        precompute=True,
        index=True,
        copy=False,
        readonly=True,
        groups="base.group_system",
        help="Satu arah (ADR-036 §0.2): ditetapkan saat partner dibuat (individu, kontak di bawah perusahaan, "
        "anak dari partner terlindungi). Nama, alamat, kota, kode pos, HP, email, NPWP disimpan terenkripsi.",
    )

    # Definisi ulang dengan kelas EfnPiiChar. Atribut definisi sebelumnya (base: index/compute/store;
    # mail: tracking, compute email_normalized) tetap digabung oleh Field._get_attrs.
    name = EfnPiiChar()
    complete_name = EfnPiiChar()
    street = EfnPiiChar()
    street2 = EfnPiiChar()
    city = EfnPiiChar()
    zip = EfnPiiChar()
    phone = EfnPiiChar()
    email = EfnPiiChar()
    email_normalized = EfnPiiChar()
    vat = EfnPiiChar()

    efn_phone_bidx = fields.Char(
        compute="_compute_efn_pii_bidx", store=True, index=True, readonly=True, copy=False, groups="base.group_system"
    )
    efn_email_bidx = fields.Char(
        compute="_compute_efn_pii_bidx", store=True, index=True, readonly=True, copy=False, groups="base.group_system"
    )
    efn_vat_bidx = fields.Char(
        compute="_compute_efn_pii_bidx", store=True, index=True, readonly=True, copy=False, groups="base.group_system"
    )
    efn_name_tokens = fields.Char(
        string="EFN: Token Nama",
        compute="_compute_efn_name_tokens",
        store=True,
        readonly=True,
        copy=False,
        prefetch=False,
        groups="base.group_system",
        help="Token HMAC-128 nama (ADR-036 §0.5) untuk pencarian nama partner terlindungi. Indeks GIN ekspresi.",
    )

    # ------------------------------------------------------------------ registry
    def _auto_init(self):
        res = super()._auto_init()
        # ADR-036 §0.5 T2: indeks GIN ekspresi; kueri memakai ekspresi yang sama persis.
        self.env.cr.execute(
            SQL(
                "CREATE INDEX IF NOT EXISTS %s ON res_partner USING gin (string_to_array(efn_name_tokens, ' '))",
                SQL.identifier(GIN_INDEX),
            )
        )
        return res

    def _register_hook(self):
        """R-T18: deteksi field cakupan yang didefinisikan ulang tanpa EfnPiiChar (tulis ditolak, SEC-137)."""
        res = super()._register_hook()
        pm.check_registry(self)
        self._efn_pii_startup_check()
        return res

    def _efn_pii_startup_check(self):
        """Satu baris EFN_PII_KEYRING saat registry dimuat (lihat README: level CRITICAL/WARNING)."""
        dbname = self.env.cr.dbname
        ring = kr.get_keyring(dbname, log=False)
        if ring.can_encrypt:
            kr.log_keyring(ring, "startup_check", dbname)
            return
        self.env.cr.execute(SQL("SELECT EXISTS (SELECT 1 FROM res_partner WHERE efn_pii_protected)"))
        has_protected = self.env.cr.fetchone()[0]
        level = logging.CRITICAL if (has_protected or kr.required()) else logging.WARNING
        kr.log_keyring(ring, "startup_check", dbname, level=level)

    # ------------------------------------------------------------------ aturan kelas subjek (§0.2)
    @api.model
    def _efn_pii_rule(self, is_company, parent, partner_type):
        """True bila partner baru termasuk kelas P/V/A (atau turunan). Tanpa pengecualian K-a."""
        if parent and parent.sudo()[PROTECTED_FIELD]:
            return True
        if is_company:
            return False
        if parent:
            # kontak orang di bawah perusahaan -> terlindungi; alamat tagih/kirim perusahaan -> tidak
            return (partner_type or "contact") == "contact"
        return True

    @api.model
    def _efn_pii_rule_vals(self, vals):
        ctx = self.env.context
        is_company = vals.get("is_company", ctx.get("default_is_company", False))
        parent_id = vals.get("parent_id", ctx.get("default_parent_id"))
        partner_type = vals.get("type", ctx.get("default_type") or "contact")
        parent = self.browse(parent_id) if isinstance(parent_id, int) and parent_id else self.browse()
        return self._efn_pii_rule(bool(is_company), parent, partner_type)

    @api.depends()  # sengaja kosong: hanya dihitung saat create (precompute), tidak pernah dihitung ulang
    def _compute_efn_pii_protected(self):
        existing_ids = []
        for partner in self:
            rid = partner.id if isinstance(partner.id, int) else partner._origin.id
            if rid:
                existing_ids.append(rid)
        current = set()
        for chunk in split_every(500, existing_ids):
            # record lama (install modul / onchange form): nilai DB dipertahankan, tidak pernah dari aturan
            self.env.cr.execute(SQL("SELECT id FROM res_partner WHERE id IN %s AND efn_pii_protected", tuple(chunk)))
            current.update(row[0] for row in self.env.cr.fetchall())
        skip = pm.skip_default(self.env)
        for partner in self:
            rid = partner.id if isinstance(partner.id, int) else partner._origin.id
            if rid:
                partner.efn_pii_protected = rid in current
            else:
                partner.efn_pii_protected = (not skip) and self._efn_pii_rule(
                    partner.is_company, partner.parent_id, partner.type
                )

    # ------------------------------------------------------------------ compute
    @api.depends("phone", "email", "vat", PROTECTED_FIELD)
    def _compute_efn_pii_bidx(self):
        self._efn_pii_fill_bidx(BIDX_SOURCES)

    def _efn_pii_fill_bidx(self, sources):
        """Isi kolom `efn_<field>_bidx` untuk ``sources`` (dipakai juga modul jembatan, mis. NIK)."""
        ring = None
        for partner in self:
            protected = partner.sudo()[PROTECTED_FIELD]
            for fname in sources:
                value = False
                raw = partner[fname] if protected else False
                if raw == kr.PLACEHOLDER:
                    # dekripsi gagal / kunci tidak ada: jangan hitung blind index dari pengganti
                    ring = ring or keyring_for(self.env)
                    raise_locked(self.env, ring)
                normalized = NORMALIZERS[fname](raw) if raw else None
                if normalized:
                    ring = ring or keyring_for(self.env)
                    if not ring.can_encrypt:
                        raise_locked(self.env, ring)
                    value = ring.blind_index(fname, normalized)
                partner[bidx_field_name(fname)] = value

    @api.depends("name", "complete_name", PROTECTED_FIELD)
    def _compute_efn_name_tokens(self):
        ring = None
        for partner in self:
            if not partner.sudo()[PROTECTED_FIELD]:
                partner[TOKENS_FIELD] = False
                continue
            name, complete_name = partner.name, partner.complete_name
            if kr.PLACEHOLDER in (name, complete_name):
                ring = ring or keyring_for(self.env)
                raise_locked(self.env, ring)
            if not (name or complete_name):
                partner[TOKENS_FIELD] = False
                continue
            ring = ring or pm.require_keys(self.env)
            # SEC-149: padding deterministik per (tabel, id, token asli) -> hitung ulang nama sama = kolom identik
            rid = partner.id if isinstance(partner.id, int) else partner._origin.id
            partner[TOKENS_FIELD] = record_name_tokens(ring, name or "", complete_name or "", self._table, rid)

    @api.depends(lambda self: [*(self._phone_get_sanitize_triggers() or []), PROTECTED_FIELD])
    def _compute_phone_sanitized(self):
        """`phone_sanitized` = salinan plaintext ber-index (phone_validation) -> kosong (perlakuan K)."""
        protected = self.filtered(lambda p: p.sudo()[PROTECTED_FIELD])
        for partner in protected:
            partner.phone_sanitized = False
        super(ResPartner, self - protected)._compute_phone_sanitized()

    # ------------------------------------------------------------------ baca / cari
    def _fetch_query(self, query, fields):
        return pm.scan_fetch(self, lambda: super(ResPartner, self)._fetch_query(query, fields))

    def _search_phone_mobile_search(self, operator, value):
        """POS 19 mencari angka lewat `phone_mobile_search` (SQL REGEXP_REPLACE mentah, phone_validation).

        - Record terlindungi dikeluarkan dari hasil SQL mentah: teks base64 ciphertext yang dibuang huruf-
          hurufnya bisa cocok kebetulan dengan potongan angka pendek.
        - HP lengkap (>= 9 digit) -> + blind index `phone in [nilai]` (menemukan partner terlindungi).
        """
        domain = super()._search_phone_mobile_search(operator, value)
        if operator not in POSITIVE_PHONE_OPERATORS or not isinstance(value, str) or not value.strip():
            return domain
        matched = self.sudo().with_context(active_test=False).search(Domain(domain), order="id").ids
        plain_ids = []
        for chunk in split_every(1000, matched):
            self.env.cr.execute(
                SQL(
                    "SELECT id FROM res_partner WHERE id IN %s AND NOT COALESCE(efn_pii_protected, FALSE)",
                    tuple(chunk),
                )
            )
            plain_ids.extend(row[0] for row in self.env.cr.fetchall())
        result = Domain("id", "in", plain_ids)
        term = complete_term("phone", value)
        if term:
            result = Domain.OR([result, Domain("phone", "in", [term])])
        return result

    # ------------------------------------------------------------------ chatter
    def _mail_track(self, tracked_fields, initial_values):
        """Nilai field cakupan partner terlindungi tidak pernah ditulis ke mail.tracking.value."""
        if any(self.sudo().mapped(PROTECTED_FIELD)):
            excluded = self._efn_pii_tracking_excluded()
            tracked_fields = {k: v for k, v in tracked_fields.items() if k not in excluded}
        return super()._mail_track(tracked_fields, initial_values)

    @api.model
    def _efn_pii_tracking_excluded(self):
        return TRACKING_EXCLUDED | frozenset(self._efn_pii_fields)

    # ------------------------------------------------------------------ create / write / copy
    def copy_data(self, default=None):
        """Duplikat partner terlindungi tetap terlindungi. `default` dari pemanggil (RPC) tidak bisa
        mematikan penanda. Bila aturan protect-by-default sudah memberi True, kunci tidak ditulis eksplisit
        (user non-admin tetap bisa menduplikasi tanpa AccessError field admin)."""
        vals_list = super().copy_data(default=default)
        skip = pm.skip_default(self.env)
        for partner, vals in zip(self, vals_list, strict=True):
            if partner.sudo()[PROTECTED_FIELD]:
                if skip or not self._efn_pii_rule_vals(vals):
                    vals[PROTECTED_FIELD] = True
                else:
                    vals.pop(PROTECTED_FIELD, None)
        return vals_list

    @api.model
    def _create(self, data_list):
        # SEC-148: id baru dicatat agar efnpii1 hanya sah sampai diikat oleh create ini
        return pm.created(super()._create(data_list))

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        skip = pm.skip_default(self.env)
        may_protect = False
        for vals in vals_list:
            pm.check_markers(self, vals)
            rule = (not skip) and self._efn_pii_rule_vals(vals)
            if PROTECTED_FIELD in vals and not vals[PROTECTED_FIELD] and rule:
                # protect-by-default tidak bisa dimatikan lewat nilai eksplisit (impor/admin)
                vals[PROTECTED_FIELD] = True
            may_protect = may_protect or bool(vals.get(PROTECTED_FIELD)) or bool(rule)
        if may_protect:
            broken = pm.broken_fields(self)
            if broken:
                pm.refuse_broken(self, broken)
            pm.require_keys(self.env)
        records = super().create(vals_list)
        pm.bind_new_records(records)
        return records

    def write(self, vals):
        if not self or pm.internal(self.env):
            return super().write(vals)
        pm.check_markers(self, vals)
        protected_before = self.browse(self.sudo().filtered(PROTECTED_FIELD).ids)
        if PROTECTED_FIELD in vals and not vals[PROTECTED_FIELD] and protected_before:
            raise UserError(_("Penanda data pribadi terlindungi bersifat satu arah dan tidak bisa dicabut."))
        pii_touch = any(fname in vals for fname in self._efn_pii_fields)
        becoming = self.browse()
        if vals.get(PROTECTED_FIELD):
            becoming = self - protected_before
        elif pii_touch or "parent_id" in vals:
            # SEC-136: kontak yang (dipindah) di bawah partner terlindungi ikut terlindungi sebelum menerima
            # nilai (mis. NPWP dari `_commercial_sync_to_descendants`)
            new_parent = self.browse(vals["parent_id"]) if vals.get("parent_id") else self.browse()
            for partner in self - protected_before:
                parent = new_parent if "parent_id" in vals else partner.parent_id
                if parent and parent.sudo()[PROTECTED_FIELD]:
                    becoming |= partner
        if protected_before or becoming:
            broken = pm.broken_fields(self)
            if broken and (becoming or pm.vals_touch_broken(self, broken, vals)):
                pm.refuse_broken(self, broken)
            if becoming or pii_touch:
                pm.require_keys(self.env)
        if becoming:
            becoming._efn_pii_protect()
        return super().write(vals)

    def _efn_pii_protect(self):
        """Jadikan terlindungi (satu arah) beserta seluruh turunannya (SEC-136).

        Urutan: nilai lama dienkripsi dulu di SQL (efnpii2), riwayat tracking diredaksi, baru penanda
        ditulis. Dengan begitu tidak pernah ada plaintext di kolom record yang sudah bertanda.
        """
        if not self:
            return
        family = self.sudo().with_context(active_test=False).search([("id", "child_of", self.ids)])
        todo = (family | self.sudo()).filtered(lambda p: not p[PROTECTED_FIELD])
        if not todo:
            return
        pm.require_keys(self.env)
        pm.encrypt_existing(todo)
        pm.redact_tracking(self.env, self._name, todo.ids, sorted(self._efn_pii_tracking_excluded()))
        todo.with_context(**{pm.INTERNAL_CONTEXT_KEY: pm.INTERNAL_TOKEN}).write({PROTECTED_FIELD: True})
        todo._efn_pii_after_protect()

    def _efn_pii_after_protect(self):
        """Hook setelah partner ``self`` menjadi terlindungi: salinan data pribadi di model lain ikut dienkripsi.

        efn_pii: rekening bank (`res.partner.bank`, Q-PII9) dan email antrean (`mail.mail` penerima, SEC-153).
        Modul jembatan memperluas (efn_pii_account: `account.move.invoice_partner_display_name`).
        """
        self.env["res.partner.bank"]._efn_pii_protect_for_partners(self)
        self.env["mail.mail"]._efn_pii_protect_for_partners(self)

    # ------------------------------------------------------------------ migrasi (shell/Superadmin)
    @api.model
    def _efn_pii_migrate(self, batch=500, ids=None):
        """Migrasi DB lama (PoC G19 / data sebelum E-38a) & rotasi `odoo_pii_name_key` untuk partner yang SUDAH
        bertanda: plaintext & `efnpii1:` -> `efnpii2:`, lalu hitung ulang token nama, blind index, email_normalized.

        Dipanggil manual: `env['res.partner'].sudo()._efn_pii_migrate(); env.cr.commit()`.
        ``ids`` membatasi ke partner tertentu (test / batch manual).
        :returns: dict jumlah (tanpa id).
        """
        pm.require_keys(self.env)
        self.env.flush_all()
        if ids is None:
            self.env.cr.execute(SQL("SELECT id FROM res_partner WHERE efn_pii_protected ORDER BY id"))
        else:
            self.env.cr.execute(
                SQL("SELECT id FROM res_partner WHERE efn_pii_protected AND id IN %s ORDER BY id", tuple(ids) or (0,))
            )
        full_run = ids is None
        ids = [row[0] for row in self.env.cr.fetchall()]
        totals = {"partners": len(ids), "bound": 0, "encrypted": 0, "failed": 0, "unbound": 0}
        names = (TOKENS_FIELD, "email_normalized", *map(bidx_field_name, self._efn_pii_bidx_sources))
        recompute = [self._fields[name] for name in names if name in self._fields]
        # SEC-148: efnpii1 hanya dikonversi dalam mode migrasi (env EFN_ODOO_PII_ACCEPT_V1=true, kait DB kosong)
        allow_v1 = pm.migration_allows_v1(self.env)
        if not allow_v1:
            # record ber-efnpii1 dilewati seluruhnya (baca = pengganti; blind index/token tidak boleh dihitung dari
            # nilai pengganti). Jalankan ulang dalam mode migrasi.
            skipped = set(pm.unbound_ids(self, ids))
            if skipped:
                totals["unbound"] += len(skipped)
                kr.log_alarm(
                    "migrate_skipped", self.env.cr.dbname, "unbound_ciphertext", model=self._name, count=len(skipped)
                )
                ids = [rid for rid in ids if rid not in skipped]
        for chunk in split_every(batch, ids):
            records = self.sudo().browse(chunk)
            counts = pm.rewrite_rows(records, list(chunk), encrypt_plain=True, allow_v1=allow_v1)
            for key in ("bound", "encrypted", "failed", "unbound"):
                totals[key] += counts[key]
            records.invalidate_recordset()
            for field in recompute:
                self.env.add_to_compute(field, records)
            records.flush_recordset()
        _logger.info(
            "%s migrate res.partner partners=%d bound=%d encrypted=%d failed=%d unbound=%d",
            kr.LOG_TAG,
            totals["partners"],
            totals["bound"],
            totals["encrypted"],
            totals["failed"],
            totals["unbound"],
        )
        if full_run and not totals["failed"] and not totals["unbound"]:
            # kait "migrasi efnpii1 selesai" hanya bila seluruh DB (semua model PII) bersih dari efnpii1
            totals["v1_latched"] = pm.latch_v1_migrated(self.env)
        return totals
