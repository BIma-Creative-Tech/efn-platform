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
4. Histori bulk (`efn_legacy_history.bulk`): flow/sales.daily = upsert per gudang; sales.line = ganti irisan
   company × gudang × rentang berkas (dipotong ke `sales_line_window`; di luar = dilewati + dilaporkan).
5. `odoo_totals` dihitung dari TABEL Odoo setelah tulis (sebelum rollback dry-run) + `table_bytes`.
Dry-run = satu savepoint di-rollback di akhir (tanpa commit). Run nyata = `commit()` per entitas / irisan.
"""

import csv
import io
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg2

from odoo import Command
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
PRODUCTION_ENVS = frozenset({"", "production", "prod"})
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


@dataclass
class ImportResult:
    report: dict
    totals: dict
    map_rows: list = field(default_factory=list)
    exit_code: int = EXIT_OK


def is_production(app_env: str | None) -> bool:
    return (app_env or "").strip().lower() in PRODUCTION_ENVS


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
    if validation is None:
        if not (opts.allow_missing_validation and opts.dry_run and not is_production(opts.app_env)):
            raise GateError("laporan validator (validation_<run_id>.json) wajib untuk impor (§27.5 gerbang 1)")
    elif validation.get("manifest_sha256") != bundle.manifest_sha256 or validation.get("errors") != 0:
        raise GateError("laporan validator bukan untuk manifest ini atau errors != 0 (§27.5 gerbang 1)")
    if is_production(opts.app_env) and not opts.dry_run and opts.confirm_brand != brand:
        raise GateError("production + run nyata wajib --confirm-brand sama dengan brand manifest (§27.5 gerbang 3)")
    return companies


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
        self.menu_code_key = {}
        self.history_scope = {}

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

    def std_ref(self, full):
        if not full:
            return None
        if full.startswith(MODULE + "."):
            return self.res_id(full)
        if full not in self._std_cache:
            rec = self.env.ref(full, raise_if_not_found=False)
            self._std_cache[full] = rec.id if rec else None
        return self._std_cache[full]

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
                        elif rec.write_uid.id == self.uid:
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
        target = self.res_id(target_full) if target_full.startswith(MODULE + ".") else self.std_ref(target_full)
        if not target:
            self._count(count_entity, "failed")
            self._error(count_entity, esb_key, "prasyarat tidak ada: record tujuan alias belum ada")
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
        for row in sorted(rows, key=lambda r: r["data"].get("parent_key") is not None):
            code = row["company_code"]
            root = None if row["data"].get("parent_key") else self._category_root()
            plan = m.category_plan(row["data"], self, root)
            self.upsert("category", "category", code, row["esb_key"], row["xmlid"], plan, self.env)

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
            self.upsert("product", "product", code, row["esb_key"], row["xmlid"], plan, cenv)
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
            self.upsert("menu", "menu", code, row["esb_key"], row["xmlid"], plan, cenv)

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
            for target in m.bom_targets(row, company.id, self, self.products, self.uoms):
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
                        source_uom = self.env["uom.uom"].browse(self.std_ref(source_xmlid) or 0).exists()
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
                scope[file].add(row["branch_key"])
        self.history_scope = scope

    def _resolve_warehouses(self, history):
        company = self.companies[self.codes[0]]
        keys = set().union(*self.history_scope.values()) if self.history_scope else set()
        keys |= self._segment_branch_keys()
        missing = []
        for key in sorted(keys):
            wh = self.record("stock.warehouse", key)
            if wh and wh.company_id == company:
                self.wh_by_key[key] = wh.id
            elif any(key in s for s in self.history_scope.values()):
                missing.append(key)
        if missing:
            raise GateError(
                f"prasyarat histori: {len(missing)} gudang (branch_key) belum diimpor/bukan milik company "
                "(jalankan langkah branch dulu, §27.2)"
            )

    def _segment_branch_keys(self):
        """Gudang segmen dari `branch_ids` manifest + `maps/branch_map.csv` (irisan kosong tetap diganti)."""
        raw = self.bundle.files.get("maps/branch_map.csv")
        ids = [int(b) for b in self.man.get("branch_ids") or [] if isinstance(b, int) and not isinstance(b, bool)]
        if raw is None or not ids:
            return set()
        company = self.codes[0]
        rows = {}
        for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
            branch_id = (row.get("branch_id") or "").strip()
            if (row.get("brand") or "").strip().lower() == self.brand and branch_id.isdigit():
                rows[int(branch_id)] = row
        out = set()
        for branch_id in ids:
            row = rows.get(branch_id)
            kind = (row.get("type") or "").strip().lower() if row else ""
            if kind in ("store", "ck", "warehouse"):
                out.add(xmlid(self.brand, company, "branch_wh", branch_id))
            elif kind == "location" and (row.get("parent_branch_id") or "").strip().isdigit():
                out.add(xmlid(self.brand, company, "branch_wh", int(row["parent_branch_id"])))
        return out

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

    def _upsert_slices(self, file, rows_by_wh, skipped, key, sums, cutover):
        Model = self._hist_model(file)
        company = self.companies[self.codes[0]]
        for wh_id in sorted(set(rows_by_wh) | set(skipped)):
            started = time.monotonic()
            vals = m.aggregate(rows_by_wh.get(wh_id, []), key, sums)
            try:
                with self.cr.savepoint():
                    stats = bulk.bulk_upsert(Model, vals, self.man["run_id"], company_ids=[company.id], max_day=cutover)
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
        self._upsert_slices("legacy_flow_daily", rows_by_wh, skipped, key, m.FLOW_COLUMNS, cutover)

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
        self._upsert_slices("legacy_sales_daily", rows_by_wh, {}, key, m.SALES_DAILY_SUMS, cutover)

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
        warehouses = sorted({self.wh_by_key[k] for k in self.history_scope.get("legacy_sales_line", ())})
        warehouses = sorted(set(warehouses) | set(self.wh_by_key.values()))
        for wh_id in warehouses:
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

    def _reverse_keys(self, entity):
        """(model, res_id) -> xmlid lengkap (urut nama) untuk entitas xmlid company segmen."""
        prefix = f"{self.brand}_{self.codes[0].lower()}_{entity}_"
        out = {}
        for name in sorted(self.xmlids):
            if name.startswith(prefix):
                _imd, model, res_id = self.xmlids[name]
                out.setdefault((model, res_id), f"{MODULE}.{name}")
        return out

    def odoo_totals(self, history):
        totals = {
            "legacy_flow_daily": [],
            "legacy_sales_daily": [],
            "legacy_sales_line": [],
            "bulk_check": {"wrong_company_rows": 0, "foreign_product_rows": 0},
        }
        if not history:
            return totals
        self.env.flush_all()
        company = self.companies[self.codes[0]]
        month = self.man["segment"]["month"]
        wh_key = {}
        for key, wh_id in sorted(self.wh_by_key.items()):
            wh_key.setdefault(wh_id, key)
        wh_ids = sorted(wh_key)
        variant_tmpl = {}
        products = self._reverse_keys("product")
        menus = self._reverse_keys("menu")
        lo_all, hi_all = None, None
        for entry in history:
            file = str(entry["entity"])
            start, end = m.parse_range(entry)
            lo_all = start if lo_all is None else min(lo_all, start)
            hi_all = end if hi_all is None else max(hi_all, end)
            table = SQL.identifier(self.env[m.HISTORY_MODEL[file]]._table)
            if file == "legacy_flow_daily":
                cols = (*m.FLOW_COLUMNS, "outflow")
                sums = SQL(", ").join(SQL("SUM(%s)", SQL.identifier(c)) for c in cols)
                self.cr.execute(
                    SQL(
                        "SELECT warehouse_id, product_id, %s FROM %s WHERE company_id = %s AND warehouse_id = ANY(%s) "
                        "AND day >= %s AND day < %s GROUP BY warehouse_id, product_id ORDER BY 1, 2",
                        sums, table, company.id, wh_ids, start, end,
                    )
                )  # fmt: skip
                for wh_id, product_id, *values in self.cr.fetchall():
                    if product_id not in variant_tmpl:
                        variant_tmpl[product_id] = self.env["product.product"].browse(product_id).product_tmpl_id.id
                    tmpl_id = variant_tmpl[product_id]
                    row = {
                        "month": month,
                        "branch_key": wh_key.get(wh_id),
                        "product_key": products.get(("product.template", tmpl_id)),
                    }
                    row.update({c: _num(v) for c, v in zip(cols, values, strict=True)})
                    totals[file].append(row)
            elif file == "legacy_sales_daily":
                cols = ("qty", "gross", "discount", "net", "cogs", "trx_count")
                sums = SQL(", ").join(SQL("SUM(%s)", SQL.identifier(c)) for c in cols)
                self.cr.execute(
                    SQL(
                        "SELECT warehouse_id, esb_menu_code, is_takeaway, product_id, %s FROM %s WHERE company_id = %s "
                        "AND warehouse_id = ANY(%s) AND day >= %s AND day < %s "
                        "GROUP BY warehouse_id, esb_menu_code, is_takeaway, product_id ORDER BY 1, 2, 3",
                        sums, table, company.id, wh_ids, start, end,
                    )
                )  # fmt: skip
                for wh_id, code, takeaway, product_id, *values in self.cr.fetchall():
                    menu_key = self.menu_code_key.get(code)
                    if menu_key is None and product_id:
                        tmpl_id = self.env["product.product"].browse(product_id).product_tmpl_id.id
                        menu_key = menus.get(("product.template", tmpl_id))
                    row = {"month": month, "branch_key": wh_key.get(wh_id), "menu_key": menu_key}
                    row["is_takeaway"] = bool(takeaway)
                    for col, value in zip(cols, values, strict=True):
                        row[col] = int(value or 0) if col == "trx_count" else _num(value)
                    totals[file].append(row)
            else:
                self.cr.execute(
                    SQL(
                        "SELECT warehouse_id, COUNT(*), SUM(qty), SUM(total), SUM(discount), SUM(cogs) FROM %s "
                        "WHERE company_id = %s AND warehouse_id = ANY(%s) AND date >= %s AND date < %s "
                        "GROUP BY warehouse_id ORDER BY 1",
                        table, company.id, wh_ids, start, end,
                    )
                )  # fmt: skip
                for wh_id, count, qty, total, discount, cogs in self.cr.fetchall():
                    totals[file].append(
                        {
                            "month": month,
                            "branch_key": wh_key.get(wh_id),
                            "rows": int(count),
                            "qty": _num(qty),
                            "total": _num(total),
                            "discount": _num(discount),
                            "cogs": _num(cogs),
                        }
                    )
        totals["bulk_check"] = self._bulk_check(company, lo_all, hi_all)
        return totals

    def _bulk_check(self, company, lo, hi):
        """Rekonsiliasi 5d: baris company ≠ company gudang, atau produk milik company lain (rentang segmen)."""
        wrong = foreign = 0
        for model in m.HISTORY_MODEL.values():
            Model = self.env[model]
            table = SQL.identifier(Model._table)
            day = SQL.identifier(Model._efn_date_field)
            self.cr.execute(
                SQL(
                    "SELECT COUNT(*) FROM %s t JOIN stock_warehouse w ON w.id = t.warehouse_id "
                    "WHERE t.company_id = %s AND t.%s >= %s AND t.%s < %s AND w.company_id <> t.company_id",
                    table, company.id, day, lo, day, hi,
                )
            )  # fmt: skip
            wrong += int(self.cr.fetchone()[0])
            self.cr.execute(
                SQL(
                    "SELECT COUNT(*) FROM %s t JOIN product_product pp ON pp.id = t.product_id "
                    "JOIN product_template pt ON pt.id = pp.product_tmpl_id WHERE t.company_id = %s "
                    "AND t.%s >= %s AND t.%s < %s AND pt.company_id IS NOT NULL AND pt.company_id <> t.company_id",
                    table, company.id, day, lo, day, hi,
                )
            )  # fmt: skip
            foreign += int(self.cr.fetchone()[0])
        return {"wrong_company_rows": wrong, "foreign_product_rows": foreign}


def _exc_text(exc):
    text = str(exc).splitlines()[0] if str(exc) else ""
    return f"{type(exc).__name__}: {text[:300]}"


# ================================================================================================ API
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


def result_filename(bundle: Bundle, dry_run: bool) -> str:
    """`<run_id>.result.efnmig` (run nyata) / `<run_id>.dryrun.result.efnmig` (dry-run; tidak menimpa hasil nyata)."""
    return f"{bundle.manifest['run_id']}{'.dryrun' if dry_run else ''}.result.efnmig"
