"""Helper model untuk semua model ber-field `EfnPiiChar` (res.partner, pos.order, ...). ADR-036 §0.

Sengaja fungsi biasa, bukan AbstractModel `efn.pii.mixin`: AbstractModel `efn.*` tidak boleh punya
metode publik (gerbang SEC-58 `efn_core/tests/test_rpc_surface.py`), sedangkan `create`/`write`
harus di-override. Model pemakai memanggil fungsi di sini dari override-nya sendiri.

Atribut kelas model yang dipakai:
- `_efn_pii_fields`: nama field yang WAJIB `EfnPiiChar` (SEC-137 fail-closed).
- `_efn_pii_always`: True bila semua record terlindungi (tanpa `efn_pii_protected`).
"""

import logging
from collections import defaultdict

from odoo import _
from odoo.exceptions import UserError
from odoo.tools import SQL, split_every

from . import keyring as kr
from .fields_pii import _SCAN, PROTECTED_FIELD, keyring_for, raise_locked, reject_marker

_logger = logging.getLogger(__name__)

BATCH = 500


class _ServerToken(str):
    """Nilai context yang hanya bisa dibuat kode server.

    JSON-RPC klien hanya menghasilkan `str` biasa, sehingga `type(v) is _ServerToken` tidak pernah benar
    untuk context dari klien (tetap bisa diserialisasi JSON bila ada kode yang men-dump context).
    """

    __slots__ = ()


# K-a (ADR-036 §0.2): partner user internal & work contact karyawan dibuat tanpa penanda.
SKIP_CONTEXT_KEY = "efn_pii_skip_default"
SKIP_TOKEN = _ServerToken("efn_pii_skip_default")
# Internal: tulis penanda dari dalam alur efn_pii (mencegah rekursi logika `write`).
INTERNAL_CONTEXT_KEY = "efn_pii_internal"
INTERNAL_TOKEN = _ServerToken("efn_pii_internal")


def has_token(env, key, token):
    value = env.context.get(key)
    return type(value) is _ServerToken and value == token


def skip_default(env):
    return has_token(env, SKIP_CONTEXT_KEY, SKIP_TOKEN)


def internal(env):
    return has_token(env, INTERNAL_CONTEXT_KEY, INTERNAL_TOKEN)


# --------------------------------------------------------------------------- registry / SEC-137
def pii_columns(model):
    """Nama kolom tersimpan bertipe EfnPiiChar pada model (urutan stabil)."""
    return [
        name
        for name, field in model._fields.items()
        if getattr(field, "efn_pii_encrypted", False) and field.store and field.column_type
    ]


def broken_fields(model):
    """Field cakupan yang bukan lagi EfnPiiChar (didefinisikan ulang modul lain, R-T18)."""
    return sorted(
        name
        for name in getattr(model, "_efn_pii_fields", ())
        if not getattr(model._fields.get(name), "efn_pii_encrypted", False)
    )


def check_registry(model):
    broken = broken_fields(model)
    if broken:
        kr.log_alarm("registry_check", model.env.cr.dbname, "field_not_encrypted", model=model._name, fields=broken)
    return broken


def refuse_broken(model, broken):
    kr.log_alarm("write_refused", model.env.cr.dbname, "field_not_encrypted", model=model._name, fields=broken)
    raise UserError(
        _(
            "Field data pribadi %(fields)s pada %(model)s tidak lagi terenkripsi (didefinisikan ulang oleh modul "
            "lain). Tulis data terlindungi ditolak sampai modul penghubung efn_pii_* dipasang.",
            fields=", ".join(broken),
            model=model._name,
        )
    )


def vals_touch_broken(model, broken, vals):
    """True bila `vals` menulis field rusak langsung atau memicu compute stored field rusak."""
    keys = set(vals)
    field_depends = getattr(model.pool, "field_depends", None)
    for name in broken:
        if name in keys:
            return True
        field = model._fields.get(name)
        if field is None or not (field.compute and field.store):
            continue
        if field_depends is None:
            return True
        if any(dep.split(".")[0] in keys for dep in field_depends.get(field, ())):
            return True
    return False


def check_markers(model, vals):
    for name in getattr(model, "_efn_pii_fields", ()):
        reject_marker(vals.get(name))


def require_keys(env):
    ring = keyring_for(env)
    if not ring.can_encrypt:
        raise_locked(env, ring)
    return ring


# --------------------------------------------------------------------------- tulis ulang kolom (SQL)
def rewrite_rows(model, ids, encrypt_plain=False):
    """Tulis ulang kolom PII baris `ids` langsung di SQL (tanpa ORM, tanpa write_date/tracking).

    - `efnpii1:` -> didekripsi dengan AAD v1 lalu dienkripsi `efnpii2:` (AAD + id). Dipakai setelah INSERT
      (`bind_new_records`) dan migrasi data PoC.
    - plaintext -> `efnpii2:` hanya bila ``encrypt_plain`` (record yang baru menjadi terlindungi / migrasi).
    - `efnpii2:` tidak disentuh.

    :returns: dict jumlah {"bound", "encrypted", "failed"} (tanpa id).
    """
    counts = {"bound": 0, "encrypted": 0, "failed": 0}
    columns = pii_columns(model)
    ids = [rid for rid in ids if isinstance(rid, int)]
    if not ids or not columns:
        return counts
    env = model.env
    cr = env.cr
    table = model._table
    ring = None
    select_cols = SQL(", ").join(SQL.identifier(c) for c in columns)
    if encrypt_plain:
        filter_sql = SQL(" OR ").join(
            SQL("(%s IS NOT NULL AND %s NOT LIKE %s)", SQL.identifier(c), SQL.identifier(c), kr.PREFIX_V2 + "%")
            for c in columns
        )
    else:
        filter_sql = SQL(" OR ").join(SQL("%s LIKE %s", SQL.identifier(c), kr.PREFIX + "%") for c in columns)
    for chunk in split_every(BATCH, sorted(set(ids))):
        cr.execute(
            SQL(
                "SELECT id, %s FROM %s WHERE id IN %s AND (%s)",
                select_cols,
                SQL.identifier(table),
                tuple(chunk),
                filter_sql,
            )
        )
        rows = cr.fetchall()
        updates = []
        for row in rows:
            rid = row[0]
            changed = {}
            for column, value in zip(columns, row[1:], strict=True):
                if not value:
                    continue
                fmt = kr.format_of(value)
                if fmt == 2:
                    continue
                ring = ring or keyring_for(env)
                if fmt == 1:
                    try:
                        plain = ring.decrypt_text(value, kr.aad_for(table, column))
                    except kr.PiiError:
                        counts["failed"] += 1
                        continue
                    counts["bound"] += 1
                elif kr.looks_like_marker(value):
                    counts["failed"] += 1
                    continue
                elif encrypt_plain:
                    plain = value
                    counts["encrypted"] += 1
                else:
                    continue
                if not ring.can_encrypt:
                    raise_locked(env, ring)
                changed[column] = ring.encrypt_text(plain, kr.aad_for(table, column, rid), kr.PREFIX_V2)
            if changed:
                updates.append((rid, changed))
        for rid, changed in updates:
            cr.execute(
                SQL(
                    "UPDATE %s SET %s WHERE id = %s",
                    SQL.identifier(table),
                    SQL(", ").join(SQL("%s = %s", SQL.identifier(c), v) for c, v in changed.items()),
                    rid,
                )
            )
    if counts["failed"]:
        kr.log_alarm(
            "rewrite_failed",
            env.cr.dbname,
            "auth_failed",
            model=model._name,
            count=counts["failed"],
            level=logging.ERROR,
        )
    return counts


def bind_new_records(records):
    """SEC-138 (ADR-036 §0.3): setelah INSERT, ikat ciphertext `efnpii1:` record baru ke id-nya (`efnpii2:`).

    Cache ORM berisi plaintext (dari create), jadi tidak di-invalidate.
    """
    if records:
        rewrite_rows(records, records.ids, encrypt_plain=False)


def encrypt_existing(records):
    """Record yang BARU menjadi terlindungi: enkripsi nilai lama di kolom (plaintext + efnpii1 -> efnpii2)."""
    if not records:
        return {}
    records.flush_recordset()
    require_keys(records.env)
    counts = rewrite_rows(records, records.ids, encrypt_plain=True)
    records.invalidate_recordset(pii_columns(records))
    return counts


# --------------------------------------------------------------------------- deteksi plaintext (SEC-137)
def scan_fetch(model, call):
    """Jalankan `_fetch_query` asli sambil mengumpulkan nilai mentah tanpa penanda; lalu laporkan."""
    stack = getattr(_SCAN, "stack", None)
    if stack is None:
        stack = _SCAN.stack = []
    stack.append([])
    try:
        fetched = call()
    finally:
        found = stack.pop()
    if found:
        report_plaintext(model, found)
    return fetched


def _protected_ids(model, ids):
    if getattr(model, "_efn_pii_always", False):
        return set(ids)
    if PROTECTED_FIELD not in model._fields:
        return set()
    field = model._fields[PROTECTED_FIELD]
    protected, missing = set(), []
    try:
        cache = field._get_cache(model.env)
    except AttributeError:
        cache = {}
    for rid in ids:
        if rid in cache:
            if cache[rid]:
                protected.add(rid)
        else:
            missing.append(rid)
    for chunk in split_every(BATCH, missing):
        model.env.cr.execute(
            SQL(
                "SELECT id FROM %s WHERE id IN %s AND %s",
                SQL.identifier(model._table),
                tuple(chunk),
                SQL.identifier(PROTECTED_FIELD),
            )
        )
        protected.update(row[0] for row in model.env.cr.fetchall())
    return protected


def report_plaintext(model, found):
    by_field = defaultdict(set)
    for fname, ids in found:
        by_field[fname].update(ids)
    all_ids = set().union(*by_field.values())
    protected = _protected_ids(model, all_ids)
    if not protected:
        return
    bad = {fname: ids & protected for fname, ids in by_field.items() if ids & protected}
    if bad:
        kr.log_alarm(
            "read_check",
            model.env.cr.dbname,
            "plaintext_in_protected",
            model=model._name,
            fields=list(bad),
            count=len(set().union(*bad.values())),
        )


# --------------------------------------------------------------------------- tracking (SEC-136)
REDACTED_LABEL = "Kontak terlindungi #"


def redact_tracking(env, model_name, ids, fnames):
    """Redaksi `mail.tracking.value` lama: nilai field cakupan record `ids`, dan label many2one ke partner itu."""
    if not ids:
        return
    cr = env.cr
    ids = tuple(ids)
    if fnames:
        cr.execute(
            SQL(
                """UPDATE mail_tracking_value v SET old_value_char = NULL, new_value_char = NULL
                     FROM mail_message m, ir_model_fields f
                    WHERE v.mail_message_id = m.id AND m.model = %s AND m.res_id IN %s
                      AND v.field_id = f.id AND f.model = %s AND f.name IN %s""",
                model_name,
                ids,
                model_name,
                tuple(fnames),
            )
        )
    if model_name == "res.partner":
        for side in ("old", "new"):
            char_col = SQL.identifier(f"{side}_value_char")
            int_col = SQL.identifier(f"{side}_value_integer")
            cr.execute(
                SQL(
                    """UPDATE mail_tracking_value v SET %s = %s || %s::text
                         FROM ir_model_fields f
                        WHERE v.field_id = f.id AND f.ttype = 'many2one' AND f.relation = 'res.partner'
                          AND %s IN %s""",
                    char_col,
                    REDACTED_LABEL,
                    int_col,
                    int_col,
                    ids,
                )
            )
