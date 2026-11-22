"""Importer bundel migrasi ESB -> Odoo CE 19 (G43 W2b; ADR-042 Accepted; docs/04 §27.2/§27.4/§27.5/§27.8a v19.1).

Dipanggil `scripts/migration/odoo_import_esb.py` (di dalam `odoo shell`) dan test modul. Bukan model -> tanpa RPC.

Alur `run_import(env, bundle, options, commit)` (bundel SUDAH diverifikasi `efn_mig.bundle.open_bundle`):
1. Gerbang (exit 4, sebelum DB ditulis): company manifest ⊆ target & ada di Odoo; laporan validator (SHA-256 manifest
   sama, `errors = 0`); production + run nyata -> `confirm_brand`; histori -> `cutover_date`, segmen valid, company
   tidak di `EFN_MIG_SALDO_BLOCKED`; prasyarat modul (mrp untuk BoM). Saldo awal (`stock`/`opening`) = stub mati.
2. Integritas baris (exit 3, sebelum DB ditulis): amplop/xmlid dihitung ulang/referensi satu brand × company (master),
   kunci persis + rentang tanggal (histori).
3. Master berurutan §27.2 (cabang -> UoM -> kategori -> produk -> menu -> partner -> BoM -> HPP) sebagai user teknis
   `efn_migration` (`env(user=…, su=True)`, `write_uid` = user migrasi); upsert xmlid `efn_mig_esb.*` §27.4: hanya field
   milik migrasi; record yang terakhir diubah user lain + nilai beda = `conflict` (tidak ditimpa); tidak ada hapus/arsip
   otomatis (`missing_in_source` dilaporkan); xmlid ke record terhapus = `dangling_xmlid` (gagal).
4. Histori bulk (`efn_legacy_history.bulk`), SEC-197: untuk SETIAP gudang `target_warehouses` manifest, ganti irisan
   gudang × range berkas (flow/sales.daily: baris digabung per kunci grain; sales.line: dipotong `sales_line_window`,
   di luar = dilewati + dilaporkan); gudang target tanpa baris = irisan dikosongkan; gudang bukan milik PT = tolak.
5. `odoo_totals` dihitung dari TABEL Odoo setelah tulis (sebelum rollback dry-run) + `table_bytes`.
Dry-run = satu savepoint di-rollback di akhir (tanpa commit). Run nyata = `commit()` per entitas / irisan.
Keamanan tambahan (review G43): laporan validator bertanda tangan (SEC-201), tolak replay bundel lebih tua dari run
sukses terakhir per brand × PT × grup (SEC-198, `allow_older` eksplisit), xmlid standar divalidasi modelnya (SEC-203),
`confirm_brand` wajib di production DAN staging. Mode `totals` (`run_totals`) = total dari tabel tanpa impor.
"""

import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from odoo import SUPERUSER_ID, Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL

from .. import bulk
from . import mapping as m
from .efn_mig.bundle import (
    Bundle,
    IntegrityError,
    Keys,
    build_bundle,
    canonical_json,
    file_entry,
    jsonl_bytes,
    sha256_hex,
)
from .efn_mig.xmlid import MODULE, xmlid

_logger = logging.getLogger(__name__)

EXIT_OK, EXIT_DATA, EXIT_CONFIG, EXIT_INTEGRITY, EXIT_GATE = 0, 1, 2, 3, 4
STATUSES = ("created", "updated", "skipped", "conflict", "failed", "missing_in_source", "dangling_xmlid")
# Fail closed (sama dengan scripts/migration/efn_mig/settings.py): hanya nilai non-produksi yang DIKENAL melonggarkan
# gerbang production; kosong / typo ("prodution") / nilai asing = production. Staging (K25-06) dianggap non-production
# untuk gerbang --confirm-brand (docs/04 §27.5 butir 3 hanya menyebut `APP_ENV=production`).
NON_PRODUCTION_ENVS = frozenset({"dev", "development", "local", "test", "ci", "staging", "stg"})
# Keputusan efn-lead (review G43): --confirm-brand juga wajib di staging (konsisten dengan efn_mig/settings.py).
CONFIRM_BRAND_ENVS = frozenset({"staging", "stg"})
# SEC-201: laporan validator ditandatangani HMAC-SHA256 dengan kunci HKDF(migration_bundle_key, info di bawah).
VAL_INFO = b"efn:mig-esb:val:v1"
# SEC-198: run sukses terakhir per (brand, PT, grup) -> ir.config_parameter (bukan RPC; hanya admin yang membaca).
PARAM_LAST_RUN_PREFIX = "efn_mig_esb.last_run."
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAX_ERRORS = 200
# Pesan error entitas ini tanpa teks exception (nama partner bisa nama orang -> data pribadi, UU PDP).
QUIET_ENTITIES = frozenset({"customer", "supplier"})
ROW_ERRORS = (ValidationError, UserError, AccessError, ValueError, KeyError, psycopg2.Error)
HISTORY_ORDER = ("legacy_flow_daily", "legacy_sales_daily", "legacy_sales_line")
XMLID_ENTITIES = {"branch": ("branch_wh", "branch_loc", "branch_pos")}
# Target default per brand ESB (docs/04 §25/§27.1): bundel brand X yang memuat company lain ditolak (exit 4).
BRAND_COMPANIES = {
    "pb": frozenset({"PB"}),
    "wtz": frozenset({"WTZ"}),
    "bacha": frozenset({"BCF", "BCR"}),
    "imd": frozenset({"IMD"}),
    "cup": frozenset({"CUP"}),
}


def default_targets(brand: str) -> frozenset[str]:
    return BRAND_COMPANIES.get(brand, frozenset())


class GateError(Exception):
    """Gerbang §27.5 / prasyarat ditolak (exit 4)."""


class ConfigError(Exception):
    """Argumen / konfigurasi / langkah belum dibangun (exit 2)."""


@dataclass
class Options:
    companies: frozenset[str]
    dry_run: bool = True
    steps: tuple[str, ...] = ()
    confirm_brand: str | None = None
    app_env: str = "production"
    saldo_blocked: frozenset[str] = frozenset({"IMD", "CUP"})
    enable_opening_balances: bool = False
    migration_login: str = "efn_migration"
    validation: dict | None = None
    allow_missing_validation: bool = False
    validation_key: bytes | None = field(default=None, repr=False)
    allow_older: bool = False


@dataclass
class ImportResult:
    report: dict
    totals: dict
    map_rows: list = field(default_factory=list)
    exit_code: int = EXIT_OK


def is_production(app_env: str | None) -> bool:
    return (app_env or "").strip().lower() not in NON_PRODUCTION_ENVS


def requires_confirm_brand(app_env: str | None) -> bool:
    """Run nyata di production (fail closed) DAN staging wajib `confirm_brand` (§27.5 gerbang 3 + keputusan lead)."""
    return is_production(app_env) or (app_env or "").strip().lower() in CONFIRM_BRAND_ENVS


# ======================================================================================= tanda tangan validator
def derive_validation_key(secret: str) -> bytes:
    """`migration_bundle_key` (hex) -> kunci HMAC laporan validator (HKDF-SHA256, salt kosong, info `val:v1`).

    Didefinisikan di sini karena `efn_mig.bundle` (salinan terkendali) belum memuatnya; bila efn-data-ai-engineer
    menambahkan fungsi setara ke `efn_mig/bundle.py`, ganti dengan fungsi itu (kontrak sama, README importer)."""
    value = (secret or "").strip()
    if len(value) < 64 or len(value) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", value):
        raise ConfigError("migration_bundle_key wajib hex >= 64 karakter")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=VAL_INFO).derive(bytes.fromhex(value))


def sign_validation_report(report: dict, key: bytes) -> dict:
    """Pasangan `verify_validation_report` (dipakai test; validator W1 menandatangani dengan skema sama)."""
    body = {k: v for k, v in report.items() if k != "sig"}
    return {**body, "sig": hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest()}


def verify_validation_report(report: dict, key: bytes) -> None:
    """`sig` = HMAC-SHA256(kunci val, canonical JSON laporan tanpa `sig`); tidak ada / salah -> IntegrityError
    (exit 3)."""
    sig = report.get("sig")
    if not isinstance(sig, str):
        raise IntegrityError("laporan validator tanpa tanda tangan (sig)")
    body = {k: v for k, v in report.items() if k != "sig"}
    expected = hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise IntegrityError("tanda tangan laporan validator tidak cocok (laporan diubah / kunci lain)")


# ================================================================================================ langkah & gerbang
def expand_steps(raw):
    out = []
    for step in raw:
        if step == "master":
            out += m.MASTER_STEPS
        elif step == "history":
            out += list(m.HISTORY_STEPS)
        elif step == "cutover":
            out += [*m.HISTORY_STEPS, "cutover-param"]
        elif step in m.MASTER_STEPS or step in m.HISTORY_STEPS or step in m.OPENING_STEPS or step == "cutover-param":
            out.append(step)
        else:
            raise ConfigError(f"langkah tidak dikenal: {step}")
    return list(dict.fromkeys(out))


def import_opening_balances(*_args, **_kwargs):
    """Langkah 11-12 §27.2 (`stock` = stock.quant mode inventaris, `opening` = jurnal pembukaan draft) — STUB.

    Sengaja belum diimplementasikan (G43 W2b): data E6 (stock opname) dan E7–E10 (AP/AR/COA/TB) belum diverifikasi
    (Q-ESB21), sehingga format baris `quant.jsonl` / `opening.jsonl` belum ada di kontrak bundel."""
    raise ConfigError(
        "saldo awal (stock/opening) belum diimplementasikan di importer: data E6-E10 belum diverifikasi (Q-ESB21). "
        "Tidak ada data yang ditulis."
    )


def _plan(bundle: Bundle, opts: Options):
    """-> (entitas master [(entity, nama berkas)], berkas histori [entri manifest], cutover_param?, catatan)."""
    files = {str(e.get("entity")): e for e in bundle.manifest.get("files") or []}
    steps = expand_steps(opts.steps) if opts.steps else None
    notes = []
    wanted_opening = [s for s in (steps or []) if s in m.OPENING_STEPS]
    if wanted_opening:
        if not opts.enable_opening_balances:
            raise ConfigError(
                f"langkah {', '.join(wanted_opening)} default MATI (data E6+ belum diverifikasi); "
                "flag --enable-opening-balances baru boleh dipakai setelah kontrak bundel saldo awal tersedia"
            )
        import_opening_balances()
    unknown = sorted(set(files) - set(m.MASTER_ORDER) - set(m.HISTORY_KEYS))
    if unknown:
        raise ConfigError(f"entitas bundel belum didukung importer: {', '.join(unknown)}")
    wanted_entities = None
    if steps is not None:
        wanted_entities = {e for s in steps for e in m.STEP_ENTITIES.get(s, ())}
        wanted_entities |= {m.HISTORY_STEPS[s] for s in steps if s in m.HISTORY_STEPS}
        for step in steps:
            if step in m.STEP_ENTITIES and not any(e in files for e in m.STEP_ENTITIES[step]):
                notes.append(f"langkah {step}: tidak ada berkas di bundel (dilewati)")

    def wanted(entity):
        return entity in files and (wanted_entities is None or entity in wanted_entities)

    master = [(e, str(files[e]["name"])) for e in m.MASTER_ORDER if wanted(e)]
    history = [files[e] for e in HISTORY_ORDER if wanted(e)]
    cutover_param = bool(steps and "cutover-param" in steps)
    return master, history, cutover_param, notes


def check_gates(env, bundle: Bundle, opts: Options):
    """Gerbang §27.5 sebelum DB disentuh. -> {kode PT: res.company}."""
    man = bundle.manifest
    brand = str(man.get("brand"))
    codes = [str(c) for c in man.get("company_codes") or []]
    if not codes:
        raise IntegrityError("manifest tanpa company_codes")
    foreign = sorted(set(codes) - set(opts.companies))
    if foreign:
        raise GateError(f"bundel memuat company yang bukan target impor: {', '.join(foreign)}")
    companies = {}
    for code in codes:
        company = env["res.company"].sudo().search([("efn_brand_code", "=", code)], limit=1)
        if not company:
            raise GateError(f"company {code} belum ada di Odoo target (§12.1/§27.1)")
        companies[code] = company
    validation = opts.validation
    dev_lenient = opts.allow_missing_validation and opts.dry_run and not is_production(opts.app_env)
    if validation is None:
        if not dev_lenient:
            raise GateError("laporan validator (validation_<run_id>.json) wajib untuk impor (§27.5 gerbang 1)")
    else:
        # SEC-201: tanda tangan dulu (exit 3), baru isi (exit 4). Tanpa sig hanya boleh pada dry-run dev longgar.
        if not (dev_lenient and "sig" not in validation):
            if opts.validation_key is None:
                raise ConfigError("kunci verifikasi laporan validator tidak tersedia")
            verify_validation_report(validation, opts.validation_key)
        if validation.get("manifest_sha256") != bundle.manifest_sha256 or validation.get("errors") != 0:
            raise GateError("laporan validator bukan untuk manifest ini atau errors != 0 (§27.5 gerbang 1)")
        # Pengikatan tambahan (setara efn_mig.bundle.verify_validation): laporan untuk run & berkas bundel ini.
        if "run_id" in validation and str(validation["run_id"]) != str(bundle.manifest.get("run_id")):
            raise IntegrityError("laporan validator untuk run_id lain")
        blob_sha = getattr(bundle, "blob_sha256", None)
        if blob_sha and "bundle_sha256" in validation and str(validation["bundle_sha256"]) != blob_sha:
            raise IntegrityError("laporan validator untuk berkas bundel lain (bundle_sha256)")
    if requires_confirm_brand(opts.app_env) and not opts.dry_run and opts.confirm_brand != brand:
        raise GateError(
            "production/staging + run nyata wajib --confirm-brand sama dengan brand manifest (§27.5 gerbang 3)"
        )
    return companies


# ================================================================================================ replay (SEC-198)
def _parse_created_at(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise IntegrityError("manifest.created_at tidak valid (ISO-8601)") from None
    if stamp.tzinfo is None:
        raise IntegrityError("manifest.created_at wajib ber-zona waktu")
    return stamp.astimezone(UTC)


def replay_keys(bundle: Bundle):
    """Kunci parameter run terakhir: `<prefix><brand>.<PT>.master` atau `.history.<YYYY-MM>` (per segmen bulan)."""
    man = bundle.manifest
    segment = man.get("segment")
    group = f"history.{segment.get('month')}" if isinstance(segment, dict) else "master"
    return [f"{PARAM_LAST_RUN_PREFIX}{man['brand']}.{code}.{group}" for code in man.get("company_codes") or []]


def check_replay(env, bundle: Bundle, opts: Options):
    """Tolak bundel yang dibuat SEBELUM bundel run sukses terakhir grup yang sama (replay bundel lama). Bundel yang
    sama / lebih baru = boleh (impor ulang idempoten). `allow_older` = pengecualian eksplisit (dicatat di laporan)."""
    created = _parse_created_at(bundle.manifest.get("created_at"))
    ICP = env["ir.config_parameter"].sudo()
    older = []
    for key in replay_keys(bundle):
        raw = ICP.get_param(key)
        if not raw:
            continue
        try:
            last = _parse_created_at(json.loads(raw).get("created_at"))
        except (ValueError, AttributeError, IntegrityError):
            _logger.warning("efn_mig_esb: parameter %s rusak; dianggap tidak ada", key)
            continue
        if created < last:
            older.append(key)
    if older and not opts.allow_older:
        raise GateError(
            "replay ditolak: bundel lebih tua dari run sukses terakhir untuk grup yang sama "
            "(pakai allow_older hanya dengan persetujuan tertulis, catat di berita acara)"
        )
    return created, older


def record_replay(env, bundle: Bundle, created):
    ICP = env["ir.config_parameter"].sudo()
    for key in replay_keys(bundle):
        raw = ICP.get_param(key)
        try:
            last = _parse_created_at(json.loads(raw).get("created_at")) if raw else None
        except (ValueError, AttributeError, IntegrityError):
            last = None
        if last is None or created >= last:
            value = {"created_at": created.isoformat(), "run_id": bundle.manifest["run_id"]}
            ICP.set_param(key, json.dumps(value, sort_keys=True))


def _check_history_gates(bundle: Bundle, opts: Options, history, cutover_param):
    man = bundle.manifest
    if not history and not cutover_param:
        return None
    raw = man.get("cutover_date")
    try:
        cutover = date.fromisoformat(str(raw))
    except ValueError:
        raise GateError("histori/cutover-param wajib cutover_date di manifest (§27.5 gerbang 4)") from None
    codes = [str(c) for c in man.get("company_codes") or []]
    blocked = sorted(set(codes) & set(opts.saldo_blocked))
    if blocked:
        raise GateError(f"histori/cutover ditolak untuk company {', '.join(blocked)} (EFN_MIG_SALDO_BLOCKED, Q-ESB5)")
    if history:
        segment = man.get("segment")
        if not isinstance(segment, dict) or not man.get("parent_run_id"):
            raise IntegrityError("bundel histori tanpa segment/parent_run_id (§27.3a)")
        if codes != [segment.get("company_code")]:
            raise IntegrityError("segment.company_code tidak sama dengan company_codes manifest")
        if not m.segment_month_ok(str(segment.get("month")), cutover):
            raise GateError("bulan segmen tidak sebelum cutover (§27.5 gerbang 5)")
        slm = man.get("sales_line_months")
        if not isinstance(slm, int) or isinstance(slm, bool) or not 0 <= slm <= bulk.MAX_SALES_LINE_MONTHS:
            raise IntegrityError("sales_line_months manifest di luar 0..24")
        # SEC-197: semua gudang tujuan segmen (termasuk tanpa baris) -> irisan setiap gudang diganti utuh.
        targets = man.get("target_warehouses")
        prefix = f"{m.company_prefix(str(man['brand']), codes[0])}branch_wh_"
        if (
            not isinstance(targets, list)
            or not targets
            or len(set(targets)) != len(targets)
            or not all(isinstance(t, str) and t.startswith(prefix) and len(t) > len(prefix) for t in targets)
        ):
            raise IntegrityError("bundel histori tanpa target_warehouses valid (xmlid branch_wh company segmen)")
        for entry in history:
            start, end = m.parse_range(entry)
            month = segment.get("month")
            if end > cutover or m.month_label(start) != month or m.month_label(end - timedelta(days=1)) != month:
                raise IntegrityError(f"{entry.get('name')}: range di luar bulan segmen / >= cutover")
    return cutover


# ================================================================================================ util nilai
def _digits_tol(field_obj, env):
    getter = getattr(field_obj, "get_digits", None)
    if field_obj is not None and field_obj.type == "float" and getter:
        digits = getter(env)
        if digits and digits[1] is not None:
            return 0.5 * 10 ** -digits[1]
    return 1e-6


def _current(rec, fname):
    if fname == "bom_line_ids":
        return tuple(sorted((ln.product_id.id, ln.product_qty, ln.product_uom_id.id) for ln in rec.bom_line_ids))
    if fname == "_nb_days":
        lines = rec.line_ids
        if len(lines) == 1 and lines.value == "percent" and abs(lines.value_amount - 100) < 1e-6:
            return lines.nb_days
        return -1
    field_obj = rec._fields[fname]
    value = rec[fname]
    if field_obj.type == "many2one":
        return value.id or False
    if field_obj.type in ("many2many", "one2many"):
        return tuple(sorted(value.ids))
    if field_obj.type in ("char", "text", "selection", "html"):
        return value or False
    return value


def _same(rec, fname, desired):
    current = _current(rec, fname)
    if fname == "bom_line_ids":
        tol = _digits_tol(rec.env["mrp.bom.line"]._fields["product_qty"], rec.env)
        if len(current) != len(desired):
            return False
        cur = sorted(current, key=lambda t: (t[0], t[2], t[1]))
        want = sorted(desired, key=lambda t: (t[0], t[2], t[1]))
        return all(a[0] == b[0] and a[2] == b[2] and abs(a[1] - b[1]) <= tol for a, b in zip(cur, want, strict=True))
    field_obj = rec._fields.get(fname)
    if isinstance(desired, float) or (field_obj is not None and field_obj.type in ("float", "monetary")):
        return abs(float(current or 0.0) - float(desired or 0.0)) <= _digits_tol(field_obj, rec.env)
    if desired in (None, ""):
        desired = False
    return current == desired


def _to_write(vals, create=False):
    out = {}
    for fname, value in vals.items():
        if fname == "bom_line_ids":
            cmds = [] if create else [Command.clear()]
            cmds += [Command.create({"product_id": p, "product_qty": q, "product_uom_id": u}) for p, q, u in value]
            out[fname] = cmds
        elif fname == "_nb_days":
            line = {"value": "percent", "value_amount": 100.0, "delay_type": "days_after", "nb_days": value}
            out["line_ids"] = ([] if create else [Command.clear()]) + [Command.create(line)]
        elif isinstance(value, tuple):
            out[fname] = [Command.set(list(value))]
        else:
            out[fname] = value
    return out


def _num(value):
    if value is None:
        return 0.0
    return float(round(Decimal(str(value)), 6))


# ================================================================================================ run
class _Run:
    def __init__(self, env, bundle: Bundle, opts: Options, companies, commit):
        self.env = env
        self.cr = env.cr
        self.bundle = bundle
        self.man = bundle.manifest
        self.brand = str(self.man["brand"])
        self.codes = [str(c) for c in self.man["company_codes"]]
        self.opts = opts
        self.companies = companies
        self.commit = commit
        self.uid = env.uid
        # Presisi qty BoM (mrp.bom / mrp.bom.line memakai digits "Product Unit"; CE default 2) -> m.bom_scale.
        self.qty_digits = int(env["decimal.precision"].precision_get("Product Unit"))
        self.report = {
            "dry_run": bool(opts.dry_run),
            "run_id": self.man["run_id"],
            "parent_run_id": self.man.get("parent_run_id"),
            "brand": self.brand,
            "company_codes": self.codes,
            "steps": [],
            "entities": {},
            "slices": [],
            "table_bytes": {},
            "notes": [],
            "errors": [],
            "skipped_by_param": {},
        }
        self.map_rows = []
        self.failed = 0
        self.seen = set()
        self.xmlids = self._load_xmlids()
        self.uoms = m.uom_index(bundle.rows("uom.jsonl"))
        self.products = m.product_info(bundle.rows("product.jsonl"))
        self._exists_cache = {}
        self._std_cache = {}
        self._variant_cache = {}
        self._tag_cache = {}
        self._dropped = set()
        self.wh_by_key = {}
        self.target_wh_ids = []
        self.menu_code_key = {}
        self.history_scope = {}
        self._std_problems = {}

    # ------------------------------------------------------------------------------------------ util
    def _load_xmlids(self):
        prefix = f"{self.brand}_"
        self.cr.execute(
            SQL(
                "SELECT name, id, model, res_id FROM ir_model_data WHERE module = %s AND left(name, %s) = %s",
                MODULE,
                len(prefix),
                prefix,
            )
        )
        return {name: [imd_id, model, res_id] for name, imd_id, model, res_id in self.cr.fetchall()}

    def _checkpoint(self):
        if not self.opts.dry_run and self.commit is not None:
            self.commit()

    def _cenv(self, company):
        return self.env(context=dict(self.env.context, allowed_company_ids=[company.id]))

    def _entity(self, entity):
        return self.report["entities"].setdefault(entity, dict.fromkeys(STATUSES, 0))

    def _count(self, entity, status, n=1):
        if entity:
            self._entity(entity)[status] += n

    def _error(self, entity, esb_key, exc):
        self.failed += 1
        if len(self.report["errors"]) >= MAX_ERRORS:
            return
        if isinstance(exc, str):
            text = exc
        elif entity in QUIET_ENTITIES:
            text = type(exc).__name__
        else:
            # Beberapa baris pertama: pesan Odoo (mis. inkonsistensi company) menaruh detail record/field di baris 2+.
            lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
            text = f"{type(exc).__name__}: {' | '.join(lines[:4])[:400]}"
        self.report["errors"].append({"entity": entity, "esb_key": str(esb_key)[:128], "error": text})

    def _note(self, text):
        if text not in self.report["notes"]:
            self.report["notes"].append(text)

    @staticmethod
    def _name(full):
        return full.split(".", 1)[1] if full and full.startswith(MODULE + ".") else None

    def _exists(self, model, res_id):
        key = (model, res_id)
        if key not in self._exists_cache:
            self._exists_cache[key] = bool(self.env[model].browse(res_id).exists())
        return self._exists_cache[key]

    # Resolver (mapping.Resolver) ------------------------------------------------------------------
    def res_id(self, full):
        name = self._name(full)
        entry = self.xmlids.get(name) if name else None
        if not entry or not self._exists(entry[1], entry[2]):
            return None
        return entry[2]

    def record(self, model, full):
        name = self._name(full)
        entry = self.xmlids.get(name) if name else None
        if not entry or entry[1] != model or not self._exists(model, entry[2]):
            return self.env[model]
        return self.env[model].browse(entry[2])

    def std_ref(self, full, model=None):
        """xmlid standar -> id. `model` diisi (SEC-203): xmlid yang menunjuk model lain = None + catatan jelas
        (mis. `uom_map.csv` memetakan teks ESB ke xmlid produk alih-alih `uom.uom`)."""
        if not full or not isinstance(full, str):
            return None
        if full.startswith(MODULE + "."):
            entry = self.xmlids.get(self._name(full))
            if model and entry and entry[1] != model:
                return self._wrong_model(full, entry[1], model)
            return self.res_id(full)
        if full not in self._std_cache:
            res_model, res_id = (None, None)
            if "." in full:
                found = self.env["ir.model.data"]._xmlid_to_res_model_res_id(full, raise_if_not_found=False)
                if found and found[0] and found[1] and self._exists(found[0], found[1]):
                    res_model, res_id = found
            self._std_cache[full] = (res_model, res_id)
        res_model, res_id = self._std_cache[full]
        if res_id and model and res_model != model:
            return self._wrong_model(full, res_model, model)
        if not res_id:
            self._std_problems.setdefault(full, f"xmlid {full} tidak ada di Odoo target")
        return res_id or None

    def _wrong_model(self, full, actual, expected):
        message = f"xmlid {full} menunjuk model {actual}, bukan {expected}"
        self._std_problems[full] = message
        self._note(message)
        return None

    def variant_id(self, full):
        tmpl_id = self.res_id(full)
        if not tmpl_id:
            return None
        if tmpl_id not in self._variant_cache:
            variants = self.env["product.template"].browse(tmpl_id).with_context(active_test=False).product_variant_ids
            self._variant_cache[tmpl_id] = variants[:1].id or None
        return self._variant_cache[tmpl_id]

    def uom_of(self, full):
        tmpl = self.record("product.template", full)
        return tmpl.uom_id.id if tmpl else None

    def tag_id(self, name):
        if name not in self._tag_cache:
            Tag = self.env["res.partner.category"]
            tag = Tag.search([("name", "=", name)], limit=1) or Tag.create({"name": name})
            self._tag_cache[name] = tag.id
        return self._tag_cache[name]

    # ------------------------------------------------------------------------------------------ xmlid & upsert
    def _bind(self, name, model, res_id):
        entry = self.xmlids.get(name)
        if entry:
            self.env["ir.model.data"].browse(entry[0]).write({"model": model, "res_id": res_id})
            entry[1:] = [model, res_id]
            return
        imd = self.env["ir.model.data"].create(
            {"module": MODULE, "name": name, "model": model, "res_id": res_id, "noupdate": True}
        )
        self.xmlids[name] = [imd.id, model, res_id]

    def _supported(self, Model, vals):
        out = {}
        for fname, value in vals.items():
            real = "line_ids" if fname == "_nb_days" else fname
            if real in Model._fields:
                out[fname] = value
            elif (Model._name, fname) not in self._dropped:
                self._dropped.add((Model._name, fname))
                self._note(f"{Model._name}.{fname} tidak ada di Odoo target (modul belum terpasang) -> tidak ditulis")
        return out

    def _map(self, entity, company_code, esb_key, model, full, odoo_id):
        self.map_rows.append(
            {
                "entity": entity,
                "company_code": company_code,
                "esb_key": str(esb_key),
                "odoo_model": model,
                "xmlid": full,
                "odoo_id": int(odoo_id),
            }
        )

    def upsert(self, count_entity, map_entity, company_code, esb_key, full, plan, cenv):
        """-> (status, record|None). Aturan §27.4 butir 1-5."""
        name = self._name(full)
        self.seen.add(name)
        if plan.missing:
            self._count(count_entity, "failed")
            self._error(count_entity or map_entity, esb_key, "prasyarat tidak ada: " + ", ".join(plan.missing))
            return "failed", None
        Model = cenv[plan.model]
        owned = self._supported(Model, plan.owned)
        entry = self.xmlids.get(name)
        status, rec, imd = None, Model, None
        try:
            with self.cr.savepoint():
                if entry:
                    rec = Model.browse(entry[2]).exists() if entry[1] == plan.model else Model
                    if not rec:
                        status = "dangling"
                    else:
                        diff = {f: v for f, v in owned.items() if not _same(rec, f, v)}
                        if not diff:
                            status = "skipped"
                        elif rec.write_uid.id in (self.uid, SUPERUSER_ID):
                            # L-167: tulisan __system__ (upgrade modul -u, recompute, cron) bukan edit pengguna;
                            # tanpa ini setiap upgrade membuat semua record master "konflik" saat impor ulang.
                            rec.write(_to_write(diff))
                            status = "updated"
                        else:
                            status = "conflict"
                else:
                    vals = _to_write({**self._supported(Model, plan.create_only), **owned}, create=True)
                    rec = Model.create(vals)
                    imd = self.env["ir.model.data"].create(
                        {"module": MODULE, "name": name, "model": plan.model, "res_id": rec.id, "noupdate": True}
                    )
                    status = "created"
        except ROW_ERRORS as exc:
            self._count(count_entity, "failed")
            self._error(count_entity or map_entity, esb_key, exc)
            return "failed", None
        if status == "dangling":
            self._count(count_entity, "failed")
            self._count(count_entity, "dangling_xmlid")
            self._error(count_entity or map_entity, esb_key, "dangling_xmlid: xmlid menunjuk record yang sudah dihapus")
            return "failed", None
        if status == "created":
            # Disimpan setelah savepoint sukses (rollback baris = indeks memori tetap konsisten).
            self.xmlids[name] = [imd.id, plan.model, rec.id]
            self._exists_cache[(plan.model, rec.id)] = True
        if status == "conflict":
            self._error(
                count_entity or map_entity, esb_key, "conflict_user_edited: field milik migrasi diubah pengguna"
            )
            self.failed -= 1  # konflik = peringatan, bukan kegagalan
        self._count(count_entity, status)
        self._map(map_entity, company_code, esb_key, plan.model, full, rec.id)
        return status, rec

    def alias(self, count_entity, company_code, esb_key, full, target_full, model):
        """xmlid kedua ke record yang sudah ada (menu takeaway -> menu dasar; UoM -> UoM standar)."""
        name = self._name(full)
        self.seen.add(name)
        if target_full.startswith(MODULE + "."):
            entry = self.xmlids.get(self._name(target_full))
            target = self.res_id(target_full) if entry and entry[1] == model else None
            if entry and entry[1] != model:
                self._wrong_model(target_full, entry[1], model)
        else:
            target = self.std_ref(target_full, model)
        if not target:
            self._count(count_entity, "failed")
            problem = self._std_problems.get(target_full) or "record tujuan alias belum ada"
            self._error(count_entity, esb_key, f"prasyarat tidak ada: {problem}")
            return "failed"
        entry = self.xmlids.get(name)
        try:
            with self.cr.savepoint():
                if entry and entry[1] == model and entry[2] == target:
                    status = "skipped"
                else:
                    self._bind(name, model, target)
                    status = "updated" if entry else "created"
        except ROW_ERRORS as exc:
            self._count(count_entity, "failed")
            self._error(count_entity, esb_key, exc)
            return "failed"
        self._count(count_entity, status)
        self._map(count_entity, company_code, esb_key, model, full, target)
        return status

    def _missing_in_source(self, entity):
        for code in self.codes:
            for xe in XMLID_ENTITIES.get(entity, (m.row_xmlid_entity(entity, {}),)):
                if not xe:
                    continue
                prefix = f"{self.brand}_{code.lower()}_{xe}_"
                n = sum(1 for name in self.xmlids if name.startswith(prefix) and name not in self.seen)
                self._count(entity, "missing_in_source", n)

    # ------------------------------------------------------------------------------------------ master
    def import_master(self, master):
        rows_by_entity = {entity: list(self.bundle.rows(name)) for entity, name in master}
        for entity, _name in master:
            self.report["steps"].append(entity)
            self._entity(entity)
            handler = getattr(self, f"_do_{entity}")
            handler(rows_by_entity[entity])
            if entity != "cost":
                self._missing_in_source(entity)
            self._checkpoint()
            _logger.info("efn_mig_esb: entitas %s %s", entity, self.report["entities"].get(entity))

    def _company(self, row):
        company = self.companies[row["company_code"]]
        return row["company_code"], company, self._cenv(company)

    def _do_branch(self, rows):
        for row in sorted(rows, key=lambda r: r["data"].get("type") == "location"):
            code, company, cenv = self._company(row)
            data = row["data"]
            if data.get("type") == "location":
                parent = self.record("stock.warehouse", data.get("parent_key"))
                plan = m.location_plan(data, company.id, parent.lot_stock_id.id if parent else None)
                self.upsert("branch", "branch", code, row["esb_key"], row["xmlid"], plan, cenv)
                continue
            status, wh = self.upsert(
                "branch", "branch", code, row["esb_key"], row["xmlid"], m.warehouse_plan(data, company.id), cenv
            )
            pos_full = data.get("pos_xmlid")
            if not pos_full:
                continue
            self._entity("branch_pos")
            if not wh:
                self.seen.add(self._name(pos_full))
                self._count("branch_pos", "failed")
                continue
            plan = m.pos_config_plan(data, company.id, wh.pos_type_id.id if "pos_type_id" in wh._fields else None)
            self.upsert("branch_pos", "branch_pos", code, row["esb_key"], pos_full, plan, cenv)

    def _do_uom(self, rows):
        for row in rows:
            data = row["data"]
            if not data.get("uom_xmlid"):
                self.seen.add(self._name(row["xmlid"]))
                self._count("uom", "failed")
                self._error("uom", row["esb_key"], "uom_xmlid kosong (uom_map.csv)")
                continue
            self.alias("uom", row["company_code"], row["esb_key"], row["xmlid"], data["uom_xmlid"], "uom.uom")

    def _category_root(self):
        """Kategori induk per brand (`All / <BRAND> / <kategori ESB>`, fit-gap §2.2); xmlid `<brand>_category_root`."""
        name = f"{self.brand}_category_root"
        entry = self.xmlids.get(name)
        if entry and self._exists("product.category", entry[2]):
            return entry[2]
        # Odoo 19 tidak lagi memasang kategori "All" (product.product_category_all) -> kategori brand = tingkat atas.
        parent = self.env.ref("product.product_category_all", raise_if_not_found=False)
        rec = self.env["product.category"].create(
            {"name": self.brand.upper(), "parent_id": parent.id if parent else False}
        )
        self._bind(name, "product.category", rec.id)
        self._exists_cache[("product.category", rec.id)] = True
        self._note(f"kategori induk brand dibuat/ditautkan: {self.brand.upper()}")
        return rec.id

    def _do_category(self, rows):
        # G61 (docs/fit-gap/G61_KATEGORI_PRODUK_PB.md): bundel membawa pohon peran (`Persediaan / ...`, urut kedalaman)
        # -> tingkat atas TANPA induk brand. product.category tidak ber-company: pohon dipakai bersama PT/brand, jadi
        # kategori yang sudah ada dengan nama + induk sama (mis. dibuat scripts/migration/recategorize_pb.py) ditautkan
        # ke xmlid bundel, bukan digandakan. Urutan stabil: induk (lebih dangkal) selalu lebih dulu.
        for row in sorted(rows, key=lambda r: r["data"].get("parent_key") is not None):
            code = row["company_code"]
            plan = m.category_plan(row["data"], self, None)
            if not plan.missing:
                self._adopt_category(row["xmlid"], plan.owned)
            self.upsert("category", "category", code, row["esb_key"], row["xmlid"], plan, self.env)

    def _adopt_category(self, full, owned):
        name = self._name(full)
        if not name or self.res_id(full):
            return
        found = self.env["product.category"].search(
            [("name", "=", owned["name"]), ("parent_id", "=", owned["parent_id"] or False)], limit=1
        )
        if found:
            self._bind(name, "product.category", found.id)
            self._exists_cache[("product.category", found.id)] = True

    def _do_pos_category(self, rows):
        for row in sorted(rows, key=lambda r: r["data"].get("parent_key") is not None):
            plan = m.pos_category_plan(row["data"], self)
            code = row["company_code"]
            self.upsert("pos_category", "pos_category", code, row["esb_key"], row["xmlid"], plan, self.env)

    def _do_product(self, rows):
        for row in rows:
            code, company, cenv = self._company(row)
            root = None if row["data"].get("categ_key") else self._category_root()
            plan = m.product_plan(row["data"], company.id, self, root)
            problem = self._std_problems.get(row["data"].get("uom_xmlid"))
            if problem:  # SEC-203: pesan jelas (xmlid tidak ada / model salah), bukan sekadar "uom_xmlid"
                plan.missing = [problem if item == "uom_xmlid" else item for item in plan.missing]
            status, rec = self.upsert("product", "product", code, row["esb_key"], row["xmlid"], plan, cenv)
            _reactivate_single_variant(status, rec)
            self._variant_cache.pop(self.res_id(row["xmlid"]), None)

    def _do_menu(self, rows):
        # Menu dasar dulu; takeaway `alias_of` kemudian (xmlid kedua ke record dasar, tanpa produk baru).
        for row in sorted(rows, key=lambda r: bool(m.menu_alias_target(r["data"]))):
            code, company, cenv = self._company(row)
            target = m.menu_alias_target(row["data"])
            if target:
                self.alias("menu", code, row["esb_key"], row["xmlid"], target, "product.template")
                continue
            plan = m.menu_plan(row["data"], company.id, self)
            status, rec = self.upsert("menu", "menu", code, row["esb_key"], row["xmlid"], plan, cenv)
            _reactivate_single_variant(status, rec)

    def _do_payment_term(self, rows):
        for row in rows:
            code, company, cenv = self._company(row)
            plan = m.payment_term_plan(row["data"], company.id)
            self.upsert("payment_term", "payment_term", code, row["esb_key"], row["xmlid"], plan, cenv)

    def _do_supplier(self, rows):
        for row in rows:
            code, company, cenv = self._company(row)
            plan = m.supplier_plan(row["data"], company.id)
            self.upsert("supplier", "supplier", code, row["esb_key"], row["xmlid"], plan, cenv)

    def _do_customer(self, rows):
        for row in rows:
            code, company, cenv = self._company(row)
            plan = m.customer_plan(row["data"], company.id, self)
            self.upsert("customer", "customer", code, row["esb_key"], row["xmlid"], plan, cenv)

    def _do_bom(self, rows):
        order = ("failed", "conflict", "created", "updated", "skipped")
        for row in rows:
            code, company, cenv = self._company(row)
            statuses = []
            # Satu baris ESB bisa = beberapa kit (satu per menu); status baris = status terburuk (rekonsiliasi butir 1).
            for target in m.bom_targets(row, company.id, self, self.products, self.uoms, self.qty_digits):
                status, _rec = self.upsert(None, "bom", code, target.esb_key, target.xmlid, target.plan, cenv)
                statuses.append(status)
            self._count("bom", next(s for s in order if s in statuses))

    def _do_cost(self, rows):
        for row in rows:
            code, company, cenv = self._company(row)
            data = row["data"]
            tmpl = self.record("product.template", data.get("product_key"))
            if not tmpl:
                self._count("cost", "failed")
                self._error("cost", row["esb_key"], "prasyarat tidak ada: produk belum ada di Odoo")
                continue
            info = self.products.get(data.get("product_key"))
            price, source_xmlid = m.convert_price(data.get("standard_price"), data.get("source_uom"), info, self.uoms)
            try:
                with self.cr.savepoint():
                    product = tmpl.with_env(cenv)
                    value = float(price)
                    if source_xmlid:
                        source_uom = self.env["uom.uom"].browse(self.std_ref(source_xmlid, "uom.uom") or 0).exists()
                        if source_uom:
                            value = source_uom._compute_price(value, product.uom_id)
                    tol = _digits_tol(product._fields["standard_price"], cenv)
                    if abs(product.standard_price - value) <= tol:
                        status = "skipped"
                    elif product.qty_available > 0:
                        status = "conflict"  # sudah berstok: mengubah HPP = revaluasi, diputuskan Finance
                    else:
                        product.standard_price = value
                        status = "updated"
            except ROW_ERRORS as exc:
                self._count("cost", "failed")
                self._error("cost", row["esb_key"], exc)
                continue
            if status == "conflict":
                self._error("cost", row["esb_key"], "conflict_has_stock: produk sudah berstok, HPP tidak diubah")
                self.failed -= 1
            self._count("cost", status)

    # ------------------------------------------------------------------------------------------ histori
    def prepass_history(self, history):
        """Integritas baris histori sebelum DB ditulis: kunci persis, referensi milik company segmen, tanggal di
        range berkas."""
        company = self.codes[0]
        prefix = m.company_prefix(self.brand, company)
        refs = {"branch_key": "branch_wh_", "product_key": "product_", "menu_key": "menu_", "pos_key": "branch_pos_"}
        targets = set(self.man.get("target_warehouses") or ())
        scope = defaultdict(set)
        for entry in history:
            file = str(entry["entity"])
            start, end = m.parse_range(entry)
            date_field = m.HISTORY_DATE_FIELD[file]
            for row in self.bundle.rows(str(entry["name"])):
                m.check_history_row(file, row)
                for key, ent in refs.items():
                    value = row.get(key)
                    if key in row and value is not None and not str(value).startswith(prefix + ent):
                        raise IntegrityError(f"{file}: {key} bukan milik {self.brand}/{company}")
                try:
                    day = date.fromisoformat(str(row[date_field]))
                except ValueError:
                    raise IntegrityError(f"{file}: tanggal tidak valid") from None
                if not start <= day < end:
                    raise IntegrityError(f"{file}: baris di luar range berkas/segmen")
                if row["branch_key"] not in targets:
                    raise IntegrityError(f"{file}: branch_key baris tidak ada di target_warehouses manifest")
                scope[file].add(row["branch_key"])
        self.history_scope = scope

    def _resolve_warehouses(self, history):
        """SEC-197: setiap gudang `target_warehouses` wajib sudah diimpor DAN milik company segmen (selain itu bundel
        ditolak utuh sebelum histori ditulis)."""
        company = self.companies[self.codes[0]]
        missing, foreign = [], []
        for key in self.man["target_warehouses"]:
            name = self._name(key)
            entry = self.xmlids.get(name) if name else None
            wh = self.record("stock.warehouse", key)
            if not wh:
                missing.append(key)
            elif wh.company_id != company:
                foreign.append(key)
            else:
                self.wh_by_key[key] = wh.id
            if entry and entry[1] != "stock.warehouse":
                foreign.append(key)
        if foreign:
            raise GateError(f"target_warehouses memuat {len(foreign)} gudang yang bukan milik company target")
        if missing:
            raise GateError(
                f"prasyarat histori: {len(missing)} gudang target belum diimpor (jalankan langkah branch dulu, §27.2)"
            )
        self.target_wh_ids = sorted(set(self.wh_by_key.values()))

    def import_history(self, history, cutover):
        if not history:
            return
        self._resolve_warehouses(history)
        for entry in history:
            file = str(entry["entity"])
            self.report["steps"].append(file)
            getattr(self, f"_history_{file}")(entry, cutover)

    def _hist_model(self, file):
        return self.env[m.HISTORY_MODEL[file]].with_context(efn_legacy_import=True)

    def _slice(self, file, wh_id, started, stats, skipped=0, error=None, **extra):
        key = next((k for k, v in self.wh_by_key.items() if v == wh_id), None)
        item = {
            "model": m.HISTORY_MODEL[file],
            "company_code": self.codes[0],
            "branch_key": key,
            "month": self.man["segment"]["month"],
            "rows_deleted": int(stats.get("rows_deleted", 0)),
            "rows_inserted": int(stats.get("rows_inserted", stats.get("inserted", 0))),
            "rows_skipped_unmapped": int(skipped),
            "seconds": round(time.monotonic() - started, 3),
        }
        if "updated" in stats:
            item["rows_updated"] = int(stats["updated"])
            item["rows_unchanged"] = int(stats["unchanged"])
        item.update(extra)
        if error is not None:
            item["error"] = error
            self.failed += 1
        self.report["slices"].append(item)

    def _replace_slices(self, entry, rows_by_wh, skipped, key, sums, cutover):
        """SEC-197: ganti irisan gudang × range berkas (bulan segmen) untuk SETIAP gudang target — baris digabung per
        kunci grain dulu (cabang `location` -> gudang induk), baris lama yang tidak ada di bundel ikut terhapus,
        gudang target tanpa baris = irisan dikosongkan. Bukan upsert murni (upsert tidak menghapus baris basi)."""
        file = str(entry["entity"])
        start, end = m.parse_range(entry)
        Model = self._hist_model(file)
        company = self.companies[self.codes[0]]
        for wh_id in self.target_wh_ids:
            started = time.monotonic()
            vals = m.aggregate(rows_by_wh.get(wh_id, []), key, sums)
            try:
                with self.cr.savepoint():
                    stats = bulk.bulk_replace_slice(
                        Model, company.id, wh_id, start, end, vals, self.man["run_id"], max_day=cutover
                    )
            except ROW_ERRORS as exc:
                self._slice(file, wh_id, started, {}, skipped.get(wh_id, 0), error=_exc_text(exc))
                continue
            self._slice(file, wh_id, started, stats, skipped.get(wh_id, 0))
            self._checkpoint()

    def _history_legacy_flow_daily(self, entry, cutover):
        company = self.companies[self.codes[0]]
        rows_by_wh, skipped = defaultdict(list), defaultdict(int)
        for row in self.bundle.rows(str(entry["name"])):
            wh_id = self.wh_by_key[row["branch_key"]]
            product_id = self.variant_id(row["product_key"])
            if not product_id:
                skipped[wh_id] += 1  # produk wajib di flow.daily (forecast) -> dilewati + dilaporkan
                continue
            rows_by_wh[wh_id].append(
                m.flow_vals(row, company_id=company.id, warehouse_id=wh_id, product_id=product_id, brand=self.brand)
            )
        key = ("day", "company_id", "warehouse_id", "product_id")
        self._replace_slices(entry, rows_by_wh, skipped, key, m.FLOW_COLUMNS, cutover)

    def _history_legacy_sales_daily(self, entry, cutover):
        company = self.companies[self.codes[0]]
        rows_by_wh = defaultdict(list)
        for row in self.bundle.rows(str(entry["name"])):
            wh_id = self.wh_by_key[row["branch_key"]]
            self.menu_code_key.setdefault(str(row["esb_menu_code"]), row.get("menu_key"))
            pos_id = self.res_id(row["pos_key"]) if row.get("pos_key") else None
            product_id = self.variant_id(row["menu_key"]) if row.get("menu_key") else None
            rows_by_wh[wh_id].append(
                m.sales_daily_vals(
                    row,
                    company_id=company.id,
                    warehouse_id=wh_id,
                    pos_config_id=pos_id,
                    product_id=product_id,
                    brand=self.brand,
                )
            )
        key = ("day", "company_id", "warehouse_id", "esb_menu_code")
        self._replace_slices(entry, rows_by_wh, {}, key, m.SALES_DAILY_SUMS, cutover)

    def _history_legacy_sales_line(self, entry, cutover):
        company = self.companies[self.codes[0]]
        name = str(entry["name"])
        slm = int(self.man.get("sales_line_months") or 0)
        window = bulk.sales_line_window(self.env, company, anchor=cutover, months=slm)
        if window is None:
            self.report["skipped_by_param"]["legacy_sales_line"] = int(entry.get("rows") or 0)
            self._note("legacy_sales_line dilewati: efn_mig_esb.sales_line_months = 0 / manifest 0 (skipped_by_param)")
            return
        start, end = m.parse_range(entry)
        clipped = m.clip_range(start, end, window)
        Model = self._hist_model("legacy_sales_line")
        for wh_id in self.target_wh_ids:  # SEC-197: setiap gudang target, juga yang tanpa baris (irisan dihapus)
            started = time.monotonic()
            counters = {"window": 0}
            if clipped is None:
                total = sum(1 for r in self.bundle.rows(name) if self.wh_by_key.get(r["branch_key"]) == wh_id)
                if total:
                    self._slice("legacy_sales_line", wh_id, started, {}, 0, rows_skipped_window=total)
                continue
            lo, hi = clipped

            def rows(wh_id=wh_id, lo=lo, hi=hi, counters=counters):
                for row in self.bundle.rows(name):
                    if self.wh_by_key.get(row["branch_key"]) != wh_id:
                        continue
                    if not lo <= date.fromisoformat(row["date"]) < hi:
                        counters["window"] += 1
                        continue
                    product_id = self.variant_id(row["menu_key"]) if row.get("menu_key") else None
                    yield m.sales_line_vals(row, product_id=product_id)

            try:
                with self.cr.savepoint():
                    stats = bulk.bulk_replace_slice(
                        Model, company.id, wh_id, lo, hi, rows(), None, max_day=cutover, sales_line_months=slm
                    )
            except ROW_ERRORS as exc:
                self._slice("legacy_sales_line", wh_id, started, {}, 0, error=_exc_text(exc))
                continue
            extra = {"rows_skipped_window": counters["window"]} if counters["window"] else {}
            self._slice("legacy_sales_line", wh_id, started, stats, 0, **extra)
            self._checkpoint()

    # ------------------------------------------------------------------------------------------ cutover-param
    def write_cutover_param(self, cutover):
        if self.opts.dry_run:
            self._note("cutover-param tidak ditulis pada dry-run (§27.2 langkah 13: hanya run nyata)")
            return
        ICP = self.env["ir.config_parameter"].sudo()
        for code in self.codes:
            ICP.set_param(bulk.PARAM_CUTOVER_PREFIX + code, cutover.isoformat())
        self.report["steps"].append("cutover-param")
        self._checkpoint()

    # ------------------------------------------------------------------------------------------ total Odoo
    def table_bytes(self):
        out = {}
        for model in m.HISTORY_MODEL.values():
            table = self.env[model]._table
            self.cr.execute(SQL("SELECT pg_total_relation_size(%s::regclass)", table))
            out[table] = int(self.cr.fetchone()[0] or 0)
        return out

    def odoo_totals(self, history):
        if not history:
            return _Totals.empty()
        calc = _Totals(self.env, self.brand, self.codes[0], self.companies[self.codes[0]], self.xmlids)
        month = self.man["segment"]["month"]
        specs = [(str(e["entity"]), *m.parse_range(e), month) for e in history]
        return calc.compute(specs, wh_ids=self.target_wh_ids, code_key=self.menu_code_key)


# ================================================================================================ total dari tabel
def load_xmlids(cr, brand):
    """{name: [imd_id, model, res_id]} xmlid `efn_mig_esb.<brand>_*` (tanpa ormcache; aman setelah rollback)."""
    prefix = f"{brand}_"
    cr.execute(
        SQL(
            "SELECT name, id, model, res_id FROM ir_model_data WHERE module = %s AND left(name, %s) = %s",
            MODULE,
            len(prefix),
            prefix,
        )
    )
    return {name: [imd_id, model, res_id] for name, imd_id, model, res_id in cr.fetchall()}


class _Totals:
    """`odoo_totals.json` dari TABEL Odoo (bukan bundel): Σ per bulan × gudang × produk/menu + pemeriksa bulk 5d.

    Dipakai setelah impor segmen (gudang target segmen) dan mode `totals` (`reconcile --final`, semua gudang PT).
    Label kunci = xmlid migrasi: gudang `branch_wh`, produk `product`, menu = xmlid menu DASAR (xmlid pertama yang
    dibuat untuk record itu; takeaway `alias_of` = xmlid kedua, sama dengan `menu_key` export)."""

    def __init__(self, env, brand, company_code, company, xmlids):
        self.env, self.cr, self.brand, self.code, self.company = env, env.cr, brand, company_code, company
        self.xmlids = xmlids
        prefix = f"{brand}_{company_code.lower()}_"
        self.wh_key, self.product_key, menu_base = {}, {}, {}
        for name in sorted(xmlids):
            imd_id, model, res_id = xmlids[name]
            full = f"{MODULE}.{name}"
            if name.startswith(prefix + "branch_wh_") and model == "stock.warehouse":
                self.wh_key.setdefault(res_id, full)
            elif name.startswith(prefix + "product_") and model == "product.template":
                self.product_key.setdefault(res_id, full)
            elif name.startswith(prefix + "menu_") and model == "product.template":
                if res_id not in menu_base or imd_id < menu_base[res_id][0]:
                    menu_base[res_id] = (imd_id, full)
        self.menu_base = {res_id: full for res_id, (_imd, full) in menu_base.items()}
        self._tmpl = {}

    @staticmethod
    def empty():
        return {
            "legacy_flow_daily": [],
            "legacy_sales_daily": [],
            "legacy_sales_line": [],
            "bulk_check": {"wrong_company_rows": 0, "foreign_product_rows": 0},
        }

    def _tmpl_of(self, product_id):
        if product_id and product_id not in self._tmpl:
            self._tmpl[product_id] = self.env["product.product"].browse(product_id).product_tmpl_id.id
        return self._tmpl.get(product_id)

    def menu_key(self, code, product_id, code_key=None):
        if code_key and code in code_key:
            return code_key[code]
        try:
            own = xmlid(self.brand, self.code, "menu", code) if code else None
        except ValueError:
            own = None
        entry = self.xmlids.get(own.split(".", 1)[1]) if own else None
        if entry and entry[1] == "product.template":
            return self.menu_base.get(entry[2])
        return self.menu_base.get(self._tmpl_of(product_id)) if product_id else None

    def compute(self, specs, wh_ids=None, code_key=None):
        """`specs` = [(berkas, mulai, akhir_eksklusif, label_bulan)]; `wh_ids` None = semua gudang PT."""
        self.env.flush_all()
        totals = self.empty()
        wh_filter = SQL("AND warehouse_id = ANY(%s)", list(wh_ids)) if wh_ids is not None else SQL()
        bounds = []
        for file, start, end, month in specs:
            bounds.append((start, end))
            table = SQL.identifier(self.env[m.HISTORY_MODEL[file]]._table)
            if file == "legacy_flow_daily":
                cols = (*m.FLOW_COLUMNS, "outflow")
                sums = SQL(", ").join(SQL("SUM(%s)", SQL.identifier(c)) for c in cols)
                self.cr.execute(
                    SQL(
                        "SELECT warehouse_id, product_id, %s FROM %s WHERE company_id = %s %s "
                        "AND day >= %s AND day < %s GROUP BY warehouse_id, product_id ORDER BY 1, 2",
                        sums, table, self.company.id, wh_filter, start, end,
                    )
                )  # fmt: skip
                for wh_id, product_id, *values in self.cr.fetchall():
                    row = {
                        "month": month,
                        "branch_key": self.wh_key.get(wh_id),
                        "product_key": self.product_key.get(self._tmpl_of(product_id)),
                    }
                    row.update({c: _num(v) for c, v in zip(cols, values, strict=True)})
                    totals[file].append(row)
            elif file == "legacy_sales_daily":
                cols = ("qty", "gross", "discount", "net", "cogs", "trx_count")
                sums = SQL(", ").join(SQL("SUM(%s)", SQL.identifier(c)) for c in cols)
                self.cr.execute(
                    SQL(
                        "SELECT warehouse_id, esb_menu_code, is_takeaway, product_id, %s FROM %s WHERE company_id = %s "
                        "%s AND day >= %s AND day < %s "
                        "GROUP BY warehouse_id, esb_menu_code, is_takeaway, product_id ORDER BY 1, 2, 3",
                        sums, table, self.company.id, wh_filter, start, end,
                    )
                )  # fmt: skip
                for wh_id, code, takeaway, product_id, *values in self.cr.fetchall():
                    row = {
                        "month": month,
                        "branch_key": self.wh_key.get(wh_id),
                        "menu_key": self.menu_key(code, product_id, code_key),
                        "is_takeaway": bool(takeaway),
                    }
                    for col, value in zip(cols, values, strict=True):
                        row[col] = int(value or 0) if col == "trx_count" else _num(value)
                    totals[file].append(row)
            else:
                self.cr.execute(
                    SQL(
                        "SELECT warehouse_id, COUNT(*), SUM(qty), SUM(total), SUM(discount), SUM(cogs) FROM %s "
                        "WHERE company_id = %s %s AND date >= %s AND date < %s GROUP BY warehouse_id ORDER BY 1",
                        table, self.company.id, wh_filter, start, end,
                    )
                )  # fmt: skip
                for wh_id, count, qty, total, discount, cogs in self.cr.fetchall():
                    totals[file].append(
                        {
                            "month": month,
                            "branch_key": self.wh_key.get(wh_id),
                            "rows": int(count),
                            "qty": _num(qty),
                            "total": _num(total),
                            "discount": _num(discount),
                            "cogs": _num(cogs),
                        }
                    )
        if bounds:
            totals["bulk_check"] = self.bulk_check(min(b[0] for b in bounds), max(b[1] for b in bounds))
        return totals

    def bulk_check(self, lo, hi):
        """Rekonsiliasi 5d: baris company ≠ company gudang, atau produk milik company lain (rentang)."""
        wrong = foreign = 0
        for model in m.HISTORY_MODEL.values():
            Model = self.env[model]
            table = SQL.identifier(Model._table)
            day = SQL.identifier(Model._efn_date_field)
            self.cr.execute(
                SQL(
                    "SELECT COUNT(*) FROM %s t JOIN stock_warehouse w ON w.id = t.warehouse_id "
                    "WHERE t.company_id = %s AND t.%s >= %s AND t.%s < %s AND w.company_id <> t.company_id",
                    table, self.company.id, day, lo, day, hi,
                )
            )  # fmt: skip
            wrong += int(self.cr.fetchone()[0])
            self.cr.execute(
                SQL(
                    "SELECT COUNT(*) FROM %s t JOIN product_product pp ON pp.id = t.product_id "
                    "JOIN product_template pt ON pt.id = pp.product_tmpl_id WHERE t.company_id = %s "
                    "AND t.%s >= %s AND t.%s < %s AND pt.company_id IS NOT NULL AND pt.company_id <> t.company_id",
                    table, self.company.id, day, lo, day, hi,
                )
            )  # fmt: skip
            foreign += int(self.cr.fetchone()[0])
        return {"wrong_company_rows": wrong, "foreign_product_rows": foreign}


def _exc_text(exc):
    text = str(exc).splitlines()[0] if str(exc) else ""
    return f"{type(exc).__name__}: {text[:300]}"


# ================================================================================================ API
def _reactivate_single_variant(status, rec):
    """L-166: template diaktifkan kembali (menu arsip -> aktif) -> varian tunggalnya ikut aktif.

    Odoo CE hanya mengarsip varian saat template diarsip; saat template diaktifkan, varian tetap arsip sehingga POS dan
    forecast (product.product) tidak melihatnya. Hanya template dengan TEPAT satu varian (menu/produk ESB tanpa
    atribut); varian template beratribut yang diarsip bisa disengaja.
    """
    if status not in ("created", "updated", "skipped") or not rec or rec._name != "product.template" or not rec.active:
        return
    variants = rec.with_context(active_test=False).product_variant_ids
    if len(variants) == 1 and not variants.active:
        variants.write({"active": True})


def find_migration_user(env, login):
    user = env["res.users"].sudo().with_context(active_test=False).search([("login", "=", login)], limit=1)
    if not user:
        raise ConfigError(f"user teknis migrasi '{login}' tidak ada (docs/04 §27.9; dibuat runbook)")
    return user


def run_import(env, bundle: Bundle, opts: Options, commit=None) -> ImportResult:
    """Impor satu bundel terverifikasi. `commit` = `env.cr.commit` (run nyata, `odoo shell`) atau None (test).

    Exception: `IntegrityError` (exit 3), `GateError` (4), `ConfigError` (2) — dilempar SEBELUM DB ditulis kecuali
    prasyarat gudang histori (setelah langkah master pada bundel campuran). Kegagalan per baris/irisan -> laporan +
    `exit_code = 1`."""
    started = time.monotonic()
    if bundle.header.get("kind") != "bundle":
        raise IntegrityError("importer hanya menerima bundel kind=bundle")
    companies = check_gates(env, bundle, opts)
    master, history, cutover_param, notes = _plan(bundle, opts)
    cutover = _check_history_gates(bundle, opts, history, cutover_param)
    if any(e == "bom" for e, _n in master) and "mrp.bom" not in env:
        raise GateError("prasyarat BoM: modul mrp belum terpasang")
    if history and any(model not in env for model in m.HISTORY_MODEL.values()):
        raise GateError("prasyarat histori: modul efn_legacy_history belum terpasang")
    user = find_migration_user(env, opts.migration_login)
    created_at, older = check_replay(env, bundle, opts)
    brand = str(bundle.manifest["brand"])
    codes = [str(c) for c in bundle.manifest["company_codes"]]
    for entity, name in master:  # integritas semua baris master sebelum DB ditulis (§27.3: xmlid beda -> exit 3)
        for row in bundle.rows(name):
            m.check_master_row(brand, entity, row, codes)
    context = dict(
        env.context,
        tracking_disable=True,
        mail_create_nolog=True,
        mail_create_nosubscribe=True,
        mail_notrack=True,
        active_test=False,
    )
    menv = env(user=user.id, su=True, context=context)
    run = _Run(menv, bundle, opts, companies, commit)
    for note in notes:
        run._note(note)
    run.report["created_at"] = created_at.isoformat()
    if older:
        run.report["allow_older_used"] = True
        run._note("allow_older: bundel LEBIH TUA dari run sukses terakhir diimpor atas izin eksplisit (SEC-198)")
    if history:
        run.prepass_history(history)
    savepoint = env.cr.savepoint() if opts.dry_run else None
    try:
        run.import_master(master)
        run.import_history(history, cutover)
        if cutover_param:
            run.write_cutover_param(cutover)
        menv.flush_all()
        totals = run.odoo_totals(history)
        run.report["table_bytes"] = run.table_bytes()
    finally:
        if savepoint is not None:
            savepoint.close(rollback=True)
            env.invalidate_all()
            env.registry.clear_cache()
    run.report["seconds"] = round(time.monotonic() - started, 3)
    exit_code = EXIT_DATA if run.failed > 0 else EXIT_OK
    run.report["exit_code"] = exit_code
    if exit_code == EXIT_OK and not opts.dry_run:
        record_replay(env, bundle, created_at)  # hanya run nyata yang sukses penuh (SEC-198)
        run._checkpoint()
    _logger.info(
        "efn_mig_esb: run=%s brand=%s dry_run=%s gagal=%s irisan=%s",
        bundle.manifest["run_id"],
        brand,
        opts.dry_run,
        run.failed,
        len(run.report["slices"]),
    )
    map_rows = [] if bundle.manifest.get("segment") else run.map_rows
    return ImportResult(run.report, totals, map_rows, exit_code)


def build_result(bundle: Bundle, result: ImportResult, keys: Keys, now: datetime | None = None) -> bytes:
    """Bundel hasil `kind=result` (kontrak scripts/migration/README.md): import_report.json, odoo_totals.json, dan
    map.jsonl (bundel master saja; segmen histori tanpa map, §27.3a)."""
    man = bundle.manifest
    report_bytes = canonical_json(result.report)
    totals_bytes = canonical_json(result.totals)
    members = {"import_report.json": report_bytes, "odoo_totals.json": totals_bytes}
    files = [
        {"name": "import_report.json", "entity": "import_report", "sha256": sha256_hex(report_bytes)},
        {"name": "odoo_totals.json", "entity": "odoo_totals", "sha256": sha256_hex(totals_bytes)},
    ]
    if not man.get("segment"):
        blob, count = jsonl_bytes(result.map_rows)
        members["map.jsonl"] = blob
        files.append(file_entry("map.jsonl", "map", blob, count))
    stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    manifest = {
        "run_id": man["run_id"],
        "brand": man["brand"],
        "company_codes": list(man.get("company_codes") or []),
        "parent_run_id": man.get("parent_run_id"),
        "segment": man.get("segment"),
        "source_manifest_sha256": bundle.manifest_sha256,
        "dry_run": bool(result.report.get("dry_run")),
        "created_at": stamp,
        "files": files,
        "tool": {"importer": "efn_legacy_history.esb_import", "schema": 1},
    }
    return build_bundle(manifest, members, keys, kind="result")


def brand_for_companies(codes) -> str:
    """Brand ESB unik untuk daftar PT (mode `totals` tanpa bundel); tidak unik / tidak dikenal -> ConfigError."""
    brands = {b for b, cs in BRAND_COMPANIES.items() if set(codes) <= cs}
    if len(brands) != 1:
        raise ConfigError("mode totals: company harus milik satu brand ESB (sertakan 'brand' bila perlu)")
    return brands.pop()


def run_totals(env, brand: str, codes, months, parent_run_id: str | None = None) -> ImportResult:
    """Mode `totals` (tanpa impor, tanpa tulis): `odoo_totals` dari tabel histori Odoo untuk PT × bulan penuh,
    semua gudang PT — dipakai `reconcile --final` setelah semua segmen diimpor. `parent_run_id` = run export histori
    yang direkonsiliasi (manifest hasil; `reconcile --final` menolak hasil totals tanpa parent yang sama)."""
    codes = sorted(set(codes or ()))
    months = sorted(set(months or ()))
    if not codes or not months or any(not isinstance(mo, str) or not _MONTH_RE.match(mo) for mo in months):
        raise ConfigError("mode totals wajib companies dan months (YYYY-MM)")
    if not set(codes) <= default_targets(brand):
        raise GateError(f"mode totals: company {', '.join(codes)} bukan milik brand {brand}")
    if any(model not in env for model in m.HISTORY_MODEL.values()):
        raise GateError("prasyarat: modul efn_legacy_history belum terpasang")
    xmlids = load_xmlids(env.cr, brand)
    totals = _Totals.empty()
    for code in codes:
        company = env["res.company"].sudo().search([("efn_brand_code", "=", code)], limit=1)
        if not company:
            raise GateError(f"company {code} belum ada di Odoo target")
        specs = []
        for month in months:
            year, mon = (int(p) for p in month.split("-"))
            start = date(year, mon, 1)
            end = date(year + mon // 12, mon % 12 + 1, 1)
            specs += [(file, start, end, month) for file in HISTORY_ORDER]
        part = _Totals(env(su=True), brand, code, company, xmlids).compute(specs)  # Environment tidak punya .sudo()
        for file in HISTORY_ORDER:
            totals[file] += part[file]
        for key, value in part["bulk_check"].items():
            totals["bulk_check"][key] += value
    report = {
        "mode": "totals",
        "dry_run": False,  # tidak ada yang ditulis; tanpa map.jsonl -> writeback tidak berefek
        "run_id": str(uuid.uuid4()),
        "parent_run_id": parent_run_id,
        "brand": brand,
        "company_codes": codes,
        "months": months,
        "steps": ["totals"],
        "entities": {},
        "slices": [],
        "table_bytes": {},
        "notes": [],
        "errors": [],
        "skipped_by_param": {},
        "exit_code": EXIT_OK,
    }
    return ImportResult(report, totals, [], EXIT_OK)


def build_totals_result(result: ImportResult, keys: Keys, now: datetime | None = None) -> bytes:
    """Bundel hasil `kind=result` mode totals: import_report.json (`mode=totals`) + odoo_totals.json."""
    report_bytes = canonical_json(result.report)
    totals_bytes = canonical_json(result.totals)
    stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    manifest = {
        "run_id": result.report["run_id"],
        "brand": result.report["brand"],
        "company_codes": result.report["company_codes"],
        "parent_run_id": result.report.get("parent_run_id"),
        "segment": None,
        "mode": "totals",
        "steps": ["totals"],  # kontrak reconcile_final: kind=result + steps == ["totals"] + parent_run_id
        "months": result.report["months"],
        "dry_run": False,
        "created_at": stamp,
        "files": [
            {"name": "import_report.json", "entity": "import_report", "sha256": sha256_hex(report_bytes)},
            {"name": "odoo_totals.json", "entity": "odoo_totals", "sha256": sha256_hex(totals_bytes)},
        ],
        "tool": {"importer": "efn_legacy_history.esb_import", "schema": 1},
    }
    members = {"import_report.json": report_bytes, "odoo_totals.json": totals_bytes}
    return build_bundle(manifest, members, keys, kind="result")


def result_filename(bundle: Bundle, dry_run: bool) -> str:
    """`<run_id>.result.efnmig` (run nyata) / `<run_id>.dryrun.result.efnmig` (dry-run; tidak menimpa hasil nyata)."""
    return f"{bundle.manifest['run_id']}{'.dryrun' if dry_run else ''}.result.efnmig"
