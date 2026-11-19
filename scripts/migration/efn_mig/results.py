"""Hasil impor (`<run_id>.result.efnmig`, kind=result) -> baris `efn_dwh.map_esb_odoo` (writeback, L-128/L-130). Murni.

Isi bundel hasil (dibuat importer sisi Odoo, kontrak README): `import_report.json`, `odoo_totals.json`, dan
(master saja)
`map.jsonl` -- satu baris per xmlid: `{"entity", "company_code", "esb_key", "odoo_model", "xmlid", "odoo_id"}`.
Manifest hasil: `run_id` = run_id bundel sumber, `parent_run_id` = induk segmen (histori) atau null.
"""

import json
import re
import uuid
from datetime import datetime
from typing import Any

from efn_mig.bundle import Bundle, IntegrityError

MAP_KEYS = frozenset({"entity", "company_code", "esb_key", "odoo_model", "xmlid", "odoo_id"})
_XMLID = re.compile(r"^efn_mig_esb\.[a-z0-9_]{1,200}$")
_MODEL = re.compile(r"^[a-z][a-z0-9_.]{1,63}$")


class DryRunResult(RuntimeError):
    """Hasil dry-run tidak boleh di-writeback (exit 4)."""


def json_member(bundle: Bundle, name: str) -> dict[str, Any]:
    if name not in bundle.files:
        raise IntegrityError(f"{name} tidak ada di bundel hasil")
    try:
        value = json.loads(bundle.files[name].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"{name} rusak") from exc
    if not isinstance(value, dict):
        raise IntegrityError(f"{name} bukan objek")
    return value


def map_rows(result: Bundle, version: datetime) -> list[list[Any]]:
    """Baris map.jsonl -> kolom `source.MAP_COLUMNS`. Menolak dry-run dan baris yang tidak valid (fail closed)."""
    report = json_member(result, "import_report.json")
    if report.get("dry_run") is not False:
        raise DryRunResult("hasil dry_run (atau tanpa flag dry_run) tidak boleh di-writeback")
    man = result.manifest
    brand, run_id = str(man["brand"]), uuid.UUID(str(man["run_id"]))
    companies = set(man.get("company_codes") or [])
    out: list[list[Any]] = []
    for row in result.rows("map.jsonl"):
        if set(row) != MAP_KEYS:
            raise IntegrityError("map.jsonl: kunci baris tidak sesuai kontrak")
        company = str(row["company_code"])
        xml, model, odoo_id = str(row["xmlid"]), str(row["odoo_model"]), row["odoo_id"]
        if company not in companies:
            raise IntegrityError("map.jsonl: company_code di luar manifest")
        if not _XMLID.fullmatch(xml) or not _MODEL.fullmatch(model):
            raise IntegrityError("map.jsonl: xmlid/odoo_model tidak valid")
        if not isinstance(odoo_id, int) or isinstance(odoo_id, bool) or odoo_id <= 0:
            raise IntegrityError("map.jsonl: odoo_id wajib bilangan bulat > 0")
        out.append([brand, company, str(row["entity"]), str(row["esb_key"]), model, xml, odoo_id, run_id, version])
    return out
