"""Helper impor bulk & parameter efn_legacy_history (G43 W2, ADR-042, docs/04 §27.8/§27.8a/§27.9 v19.1).

Semua fungsi di sini = Python server-side (bukan metode model publik) sehingga tidak dapat dipanggil lewat
``/web/dataset/call_kw`` / ``/json/2`` (SEC-58, ``efn_core/tests/test_rpc_surface.py``). Pemanggil satu-satunya yang
sah = importer migrasi di ``odoo shell`` (``scripts/migration``) dengan ``env.su`` **dan** konteks
``efn_legacy_import=True``; selain itu ditolak (``AccessError``).

Dua mode tulis (docs/04 §27.8a):

* :func:`bulk_upsert` — ``INSERT ... ON CONFLICT (<kunci grain>) DO UPDATE`` untuk model berkunci unik
  (``efn.legacy.flow.daily``, ``efn.legacy.sales.daily``). Baris yang nilainya sama persis tidak ditulis ulang
  (``WHERE ... IS DISTINCT FROM``) → run kedua dengan data sama = 0 insert, 0 update.
* :func:`bulk_replace_slice` — ganti irisan company × gudang × rentang tanggal: ``DELETE`` irisan lalu ``INSERT``
  multi-baris, dalam transaksi pemanggil (dipakai ``efn.legacy.sales.line`` yang sengaja tanpa constraint unik; boleh
  juga untuk dua model lain).

SQL disusun dengan ``odoo.tools.SQL`` (identifier di-quote, nilai = parameter terikat psycopg2; tidak pernah format
string nilai). Constraint Python (``check_company``) dan compute ORM tidak berjalan pada SQL langsung → helper
memvalidasi company/gudang/produk/tanggal sendiri sebelum menulis dan mengisi kolom turunan (``outflow``, ``net``).
"""

import logging
import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL

_logger = logging.getLogger(__name__)

IMPORT_CONTEXT_KEY = "efn_legacy_import"
PARAM_CUTOVER_PREFIX = "efn_mig_esb.cutover_date."
PARAM_SALES_LINE_MONTHS = "efn_mig_esb.sales_line_months"
PARAM_LEGACY_DAYS = "efn_mig_esb.legacy_days"
DEFAULT_SALES_LINE_MONTHS = 24
MAX_SALES_LINE_MONTHS = 24  # docs/04 §27.9 v19.1: 0..24
DEFAULT_BATCH = 5000
MAX_BATCH = 10000

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RUN_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_LOG_COLUMNS = ("create_uid", "create_date", "write_uid", "write_date")


# ---------------------------------------------------------------------------------------------- akses
def import_allowed(env):
    """True hanya untuk superuser (``sudo``) DAN konteks impor migrasi eksplisit."""
    return bool(env.su and env.context.get(IMPORT_CONTEXT_KEY))


def check_import_allowed(env, model_name):
    if not import_allowed(env):
        raise AccessError(
            f"efn_legacy_history:read_only — {model_name} adalah arsip sistem lama ESB (read-only). Tulis hanya oleh "
            "skrip migrasi (sudo + konteks efn_legacy_import=True)."
        )


# ---------------------------------------------------------------------------------------------- parameter
def _parse_iso_date(value):
    if isinstance(value, datetime):
        raise ValueError("datetime")
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _ISO_DATE_RE.match(value.strip()):
        return date.fromisoformat(value.strip())
    raise ValueError("format")


def get_cutover_date(env, company):
    """Tanggal cutover ESB -> Odoo company (``efn_mig_esb.cutover_date.<CODE>``) atau None.

    Kosong / company tanpa kode / format bukan ``YYYY-MM-DD`` -> None (format salah dicatat sebagai peringatan;
    fail safe = forecast memakai perilaku lama).
    """
    code = company.sudo().efn_brand_code if company else False
    if not code:
        return None
    key = PARAM_CUTOVER_PREFIX + code
    raw = env["ir.config_parameter"].sudo().get_param(key)
    if not raw:
        return None
    try:
        return _parse_iso_date(str(raw))
    except ValueError:
        _logger.warning("efn_legacy_history: parameter %s bukan tanggal YYYY-MM-DD; histori legacy diabaikan.", key)
        return None


def get_sales_line_months(env):
    """Rentang baris penjualan legacy dalam bulan (``efn_mig_esb.sales_line_months``; default 24, 0 = nonaktif)."""
    raw = env["ir.config_parameter"].sudo().get_param(PARAM_SALES_LINE_MONTHS)
    if raw in (None, False, ""):
        return DEFAULT_SALES_LINE_MONTHS
    try:
        value = int(str(raw).strip())
        if value < 0:
            raise ValueError(raw)
    except ValueError:
        _logger.warning(
            "efn_legacy_history: parameter %s tidak valid; memakai default %s.",
            PARAM_SALES_LINE_MONTHS,
            DEFAULT_SALES_LINE_MONTHS,
        )
        return DEFAULT_SALES_LINE_MONTHS
    return min(value, MAX_SALES_LINE_MONTHS)


def sales_line_window(env, company, anchor=None, months=None):
    """``(hari_pertama, batas_eksklusif)`` baris penjualan yang boleh dimuat untuk company, atau None bila nonaktif.

    docs/04 §27.8: ``[awal bulan(cutover) − N bulan, cutover − 1]``. ``batas`` = cutover company, jika tidak ada
    ``anchor`` (mis. cutover di manifest), jika tidak ada hari ini. ``months`` (nilai manifest) → efektif
    ``min(parameter, months)``.
    """
    n = get_sales_line_months(env)
    if months is not None:
        n = min(n, max(int(months), 0))
    if not n:
        return None
    end = get_cutover_date(env, company) or anchor or fields.Date.context_today(env["res.company"])
    return end.replace(day=1) - relativedelta(months=n), end


# ---------------------------------------------------------------------------------------------- normalisasi
def _err(index, field, reason):
    # Tanpa nilai baris di pesan (data bisnis klien; log/laporan cukup indeks + kolom).
    return ValidationError(f"efn_legacy_history:row — baris #{index} kolom '{field}': {reason}.")


def _to_decimal(index, field, value):
    if value is None or value is False:
        return Decimal(0)
    if isinstance(value, bool):
        raise _err(index, field, "boolean bukan angka")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _err(index, field, "NaN/inf")
        value = repr(value)
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise _err(index, field, "bukan angka") from exc
    if not dec.is_finite():
        raise _err(index, field, "NaN/inf")
    if abs(dec) >= Decimal("1e13"):
        raise _err(index, field, "di luar rentang")
    return dec


def _normalize_value(index, field, kind, required, value):
    empty = value is None or value is False or value == ""
    if kind == "date":
        if empty:
            raise _err(index, field, "wajib")
        try:
            return _parse_iso_date(value)
        except ValueError as exc:
            raise _err(index, field, "format YYYY-MM-DD") from exc
    if kind == "num":
        return _to_decimal(index, field, value)
    if kind == "bool":
        if value in (True, False, 0, 1, None):
            return bool(value)
        raise _err(index, field, "boolean")
    if empty:
        if required:
            raise _err(index, field, "wajib")
        return 0 if kind == "int" else None
    if kind == "m2o":
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise _err(index, field, "id record (int > 0)")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int) or not (-(2**31) <= value < 2**31):
            raise _err(index, field, "bilangan bulat 32-bit")
        return value
    if kind.startswith("char:"):
        size = int(kind.split(":", 1)[1])
        if not isinstance(value, str) or "\x00" in value:
            raise _err(index, field, "teks")
        if len(value) > size:
            raise _err(index, field, f"maks {size} karakter")
        return value
    if kind.startswith("sel:"):
        allowed = kind.split(":", 1)[1].split("|")
        if value not in allowed:
            raise _err(index, field, "nilai di luar pilihan " + "/".join(allowed))
        return value
    raise ValueError(f"jenis kolom tidak dikenal: {kind}")  # bug kode, bukan data


def _normalize_row(model, index, row, run_id):
    if not isinstance(row, dict):
        raise _err(index, "-", "baris harus objek")
    spec = model._efn_upsert_spec
    known = {name for name, _kind, _req in spec}
    unknown = set(row) - known
    if unknown:
        # Fail closed: kolom salah ketik / turunan yang tidak boleh diisi / kunci asing tidak diam-diam diabaikan.
        raise _err(index, ",".join(sorted(unknown)), "kolom tidak dikenal")
    vals = {name: _normalize_value(index, name, kind, req, row.get(name)) for name, kind, req in spec}
    vals = model._efn_upsert_derive(vals, row)
    if "run_id" in model._fields:
        vals["run_id"] = run_id
    return vals


def _clean_run_id(run_id, required):
    if not run_id and not required:
        return None
    run_id = str(run_id or "").strip().lower()
    if not _RUN_ID_RE.match(run_id):
        raise UserError("efn_legacy_history:run_id — run_id wajib UUID (manifest bundel migrasi).")
    return run_id


def _clean_company_ids(company_ids):
    ids = (
        {company_ids} if isinstance(company_ids, int) and not isinstance(company_ids, bool) else set(company_ids or ())
    )
    if not ids or any(isinstance(c, bool) or not isinstance(c, int) for c in ids):
        raise UserError("efn_legacy_history:company_ids — daftar id company target migrasi wajib (int).")
    return ids


# ---------------------------------------------------------------------------------------------- validasi batch
def _check_references(env, model, batch, company_ids, max_day):
    """Pengganti check_company ORM (dilewati SQL langsung): company ∈ target, gudang & produk milik company baris,
    tanggal < cutover company (parameter) dan < ``max_day``."""
    cr = env.cr
    date_field = model._efn_date_field
    for index, vals in batch:
        if vals["company_id"] not in company_ids:
            raise _err(index, "company_id", "company di luar target migrasi")
    used = sorted({v["company_id"] for _i, v in batch})
    upper = {}
    for company in env["res.company"].browse(used).exists():
        bound = get_cutover_date(env, company)
        if max_day and (bound is None or max_day < bound):
            bound = max_day
        upper[company.id] = bound

    wh_ids = sorted({v["warehouse_id"] for _i, v in batch})
    cr.execute(SQL("SELECT id, company_id FROM stock_warehouse WHERE id = ANY(%s)", wh_ids))
    wh_company = dict(cr.fetchall())

    prod_ids = sorted({v["product_id"] for _i, v in batch if v.get("product_id")})
    prod_company = {}
    if prod_ids:
        cr.execute(
            SQL(
                """SELECT pp.id, pt.company_id FROM product_product pp
                     JOIN product_template pt ON pt.id = pp.product_tmpl_id
                    WHERE pp.id = ANY(%s)""",
                prod_ids,
            )
        )
        prod_company = dict(cr.fetchall())

    pos_company = {}
    if "pos_config_id" in model._fields:
        pos_ids = sorted({v["pos_config_id"] for _i, v in batch if v.get("pos_config_id")})
        if pos_ids:
            cr.execute(SQL("SELECT id, company_id FROM pos_config WHERE id = ANY(%s)", pos_ids))
            pos_company = dict(cr.fetchall())

    for index, vals in batch:
        company_id = vals["company_id"]
        if company_id not in upper:
            raise _err(index, "company_id", "company tidak ada")
        if wh_company.get(vals["warehouse_id"], -1) != company_id:
            raise _err(index, "warehouse_id", "gudang tidak ada atau bukan milik company baris")
        product_id = vals.get("product_id")
        if product_id:
            if product_id not in prod_company:
                raise _err(index, "product_id", "produk tidak ada")
            if prod_company[product_id] not in (None, company_id):
                raise _err(index, "product_id", "produk milik company lain")
        pos_id = vals.get("pos_config_id")
        if pos_id and pos_company.get(pos_id, -1) != company_id:
            raise _err(index, "pos_config_id", "kasir tidak ada atau bukan milik company baris")
        bound = upper[company_id]
        if bound and vals[date_field] >= bound:
            raise _err(index, date_field, "tanggal >= cutover/batas; histori legacy hanya untuk hari sebelum cutover")


# ---------------------------------------------------------------------------------------------- SQL
def _columns(model, sample):
    columns = list(sample)
    if model._log_access:
        columns += list(_LOG_COLUMNS)
    return columns


def _row_values(model, vals, columns, uid, now):
    log = {"create_uid": uid, "create_date": now, "write_uid": uid, "write_date": now}
    return [log[c] if c in log else vals[c] for c in columns]


def _values_sql(model, batch, columns, uid, now):
    placeholders = "(" + ", ".join(["%s"] * len(columns)) + ")"
    return SQL(", ").join(SQL(placeholders, *_row_values(model, vals, columns, uid, now)) for _i, vals in batch)


def _execute_upsert(env, model, batch, stats):
    if not batch:
        return
    key = tuple(model._efn_upsert_key)
    seen = set()
    for index, vals in batch:
        k = tuple(vals[c] for c in key)
        if k in seen:
            raise _err(index, ",".join(key), "kunci unik ganda dalam satu batch (agregasikan di export)")
        seen.add(k)
    columns = _columns(model, batch[0][1])
    data_cols = [c for c in columns if c not in key and c != "run_id" and c not in _LOG_COLUMNS]
    update_cols = [c for c in columns if c not in key and c not in ("create_uid", "create_date")]
    ident = SQL.identifier
    query = SQL(
        "INSERT INTO %(table)s AS tgt (%(cols)s) VALUES %(values)s "
        "ON CONFLICT (%(key)s) DO UPDATE SET %(sets)s "
        "WHERE (%(old)s) IS DISTINCT FROM (%(new)s) RETURNING (tgt.xmax = 0)",
        table=ident(model._table),
        cols=SQL(", ").join(ident(c) for c in columns),
        values=_values_sql(model, batch, columns, env.uid, fields.Datetime.now()),
        key=SQL(", ").join(ident(c) for c in key),
        sets=SQL(", ").join(SQL("%s = EXCLUDED.%s", ident(c), ident(c)) for c in update_cols),
        old=SQL(", ").join(ident("tgt", c) for c in data_cols),
        new=SQL(", ").join(ident("excluded", c) for c in data_cols),
    )
    env.cr.execute(query)
    written = env.cr.fetchall()
    inserted = sum(1 for (is_insert,) in written if is_insert)
    stats["inserted"] += inserted
    stats["updated"] += len(written) - inserted
    stats["unchanged"] += len(batch) - len(written)


def _execute_insert(env, model, batch):
    if not batch:
        return 0
    columns = _columns(model, batch[0][1])
    query = SQL(
        "INSERT INTO %(table)s (%(cols)s) VALUES %(values)s",
        table=SQL.identifier(model._table),
        cols=SQL(", ").join(SQL.identifier(c) for c in columns),
        values=_values_sql(model, batch, columns, env.uid, fields.Datetime.now()),
    )
    env.cr.execute(query)
    return len(batch)


def _start(model, run_id, batch_size, run_id_required):
    env = model.env
    check_import_allowed(env, model._name)
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_BATCH:
        raise UserError(f"efn_legacy_history:batch_size — 1..{MAX_BATCH}.")
    return _clean_run_id(run_id, run_id_required)


# ---------------------------------------------------------------------------------------------- API importer
def bulk_upsert(model, rows, run_id, *, company_ids, batch_size=DEFAULT_BATCH, max_day=None):
    """Upsert bulk baris histori ke model berkunci grain (``efn.legacy.flow.daily`` / ``efn.legacy.sales.daily``).

    :param model: recordset kosong model dengan ``env.su`` dan konteks ``efn_legacy_import=True``.
    :param rows: iterable ``dict`` (boleh generator) berkunci nama field (``model._efn_upsert_spec``); Many2one = id
        int hasil resolve xmlid; tanggal ``date``/``'YYYY-MM-DD'``; angka int/str/Decimal (float diterima).
    :param run_id: UUID run migrasi (manifest bundel) → kolom ``run_id``.
    :param company_ids: id company target migrasi (int atau iterable); baris di luar → error.
    :param batch_size: baris per ``INSERT`` (1..10000, default 5000).
    :param max_day: batas eksklusif opsional (cutover di manifest bila parameter belum ditulis).
    :return: ``{"rows", "inserted", "updated", "unchanged"}``.
    Error data → ``ValidationError`` (tanpa nilai baris di pesan). Transaksi milik pemanggil (dry-run = rollback).
    """
    env = model.env
    run_id = _start(model, run_id, batch_size, run_id_required=True)
    if not getattr(model, "_efn_upsert_key", None):
        raise UserError(f"efn_legacy_history:no_key — {model._name} tanpa kunci unik; pakai bulk_replace_slice.")
    company_ids = _clean_company_ids(company_ids)
    if isinstance(max_day, str):
        max_day = _parse_iso_date(max_day)
    stats = {"rows": 0, "inserted": 0, "updated": 0, "unchanged": 0}
    model.flush_model()
    batch = []
    for index, row in enumerate(rows):
        batch.append((index, _normalize_row(model, index, row, run_id)))
        stats["rows"] += 1
        if len(batch) >= batch_size:
            _check_references(env, model, batch, company_ids, max_day)
            _execute_upsert(env, model, batch, stats)
            batch = []
    if batch:
        _check_references(env, model, batch, company_ids, max_day)
        _execute_upsert(env, model, batch, stats)
    env.invalidate_all()
    _logger.info(
        "efn_legacy_history: upsert %s run=%s baris=%s insert=%s update=%s sama=%s",
        model._name,
        run_id,
        stats["rows"],
        stats["inserted"],
        stats["updated"],
        stats["unchanged"],
    )
    return stats


def bulk_replace_slice(
    model,
    company_id,
    warehouse_id,
    date_from,
    date_to,
    rows,
    run_id=None,
    *,
    batch_size=DEFAULT_BATCH,
    max_day=None,
    sales_line_months=None,
):
    """Ganti irisan company × gudang × ``[date_from, date_to)`` (docs/04 §27.8a): validasi → DELETE → INSERT.

    Seluruh baris divalidasi lebih dulu (irisan ditolak utuh bila ada satu baris salah: di luar irisan/rentang, gudang/
    produk bukan milik company, tanggal >= cutover). Untuk ``efn.legacy.sales.line`` irisan wajib berada di dalam
    rentang ``sales_line_window`` (``sales_line_months`` = nilai manifest; efektif ``min(parameter, manifest)``;
    0 → ``UserError``). Idempoten: memuat irisan yang sama 2× = jumlah baris sama. Commit per irisan = tugas importer.

    :param rows: iterable ``dict`` (lihat :func:`bulk_upsert`); ``company_id``/``warehouse_id`` baris boleh diisi
        (wajib sama dengan irisan) atau dihilangkan (diisi dari argumen).
    :return: ``{"rows_deleted", "rows_inserted"}``.
    """
    env = model.env
    run_id = _start(model, run_id, batch_size, run_id_required="run_id" in model._fields)
    for name, value in (("company_id", company_id), ("warehouse_id", warehouse_id)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise UserError(f"efn_legacy_history:slice — {name} wajib id int.")
    try:
        date_from, date_to = _parse_iso_date(date_from), _parse_iso_date(date_to)
        if isinstance(max_day, str):
            max_day = _parse_iso_date(max_day)
    except ValueError as exc:
        raise UserError("efn_legacy_history:slice — tanggal irisan wajib YYYY-MM-DD.") from exc
    if date_from >= date_to:
        raise UserError("efn_legacy_history:slice — date_from harus < date_to (batas akhir eksklusif).")
    date_field = model._efn_date_field
    if model._efn_months_window:
        window = sales_line_window(env, env["res.company"].browse(company_id), anchor=max_day, months=sales_line_months)
        if window is None:
            raise UserError(
                "efn_legacy_history:sales_line_disabled — baris penjualan legacy nonaktif "
                f"({PARAM_SALES_LINE_MONTHS} = 0)."
            )
        if date_from < window[0] or date_to > window[1]:
            raise UserError("efn_legacy_history:slice_window — irisan di luar rentang sales_line_months/cutover.")

    batch = []
    for index, row in enumerate(rows):
        if isinstance(row, dict):
            row = dict(row)
            row.setdefault("company_id", company_id)
            row.setdefault("warehouse_id", warehouse_id)
        vals = _normalize_row(model, index, row, run_id)
        if vals["company_id"] != company_id or vals["warehouse_id"] != warehouse_id:
            raise _err(index, "company_id/warehouse_id", "di luar irisan")
        if not date_from <= vals[date_field] < date_to:
            raise _err(index, date_field, "di luar irisan tanggal")
        batch.append((index, vals))
    if batch:
        _check_references(env, model, batch, {company_id}, max_day)
    else:
        # Irisan kosong tetap divalidasi (gudang milik company) agar DELETE tidak mengenai irisan PT lain.
        env.cr.execute(SQL("SELECT company_id FROM stock_warehouse WHERE id = %s", warehouse_id))
        found = env.cr.fetchone()
        if not found or found[0] != company_id:
            raise UserError("efn_legacy_history:slice — gudang tidak ada atau bukan milik company irisan.")

    model.flush_model()
    env.cr.execute(
        SQL(
            "DELETE FROM %s WHERE company_id = %s AND warehouse_id = %s AND %s >= %s AND %s < %s",
            SQL.identifier(model._table),
            company_id,
            warehouse_id,
            SQL.identifier(date_field),
            date_from,
            SQL.identifier(date_field),
            date_to,
        )
    )
    deleted = env.cr.rowcount
    inserted = 0
    for start in range(0, len(batch), batch_size):
        inserted += _execute_insert(env, model, batch[start : start + batch_size])
    env.invalidate_all()
    _logger.info(
        "efn_legacy_history: ganti irisan %s company=%s gudang=%s %s..%s hapus=%s sisip=%s",
        model._name,
        company_id,
        warehouse_id,
        date_from,
        date_to,
        deleted,
        inserted,
    )
    return {"rows_deleted": deleted, "rows_inserted": inserted}
