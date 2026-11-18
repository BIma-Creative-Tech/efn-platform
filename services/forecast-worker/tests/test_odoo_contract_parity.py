"""Paritas statis forecast-worker <-> modul Odoo `efn_demand_forecast` <-> gateway HAProxy (docs/04 §28.2).

Tanpa Odoo/HAProxy di runtime: nilai dibaca dari SOURCE kedua sisi (pola test_access_sql). Bila salah satu sisi
berubah tanpa sisi lain, test ini gagal -> ketidakcocokan kontrak tertangkap di CI sebelum deploy."""

import math
import re
from pathlib import Path

import pytest

from efn_forecast import MODEL_VERSION, contract
from efn_forecast.odoo import METHODS

ROOT = Path(__file__).resolve().parents[3]
MODELS_DIR = ROOT / "services" / "odoo" / "addons" / "efn_demand_forecast" / "models"
API = (MODELS_DIR / "forecast_api.py").read_text(encoding="utf-8")
RUN = (MODELS_DIR / "efn_forecast_run.py").read_text(encoding="utf-8")
DAILY = (MODELS_DIR / "efn_forecast_daily.py").read_text(encoding="utf-8")
CALENDAR = (MODELS_DIR / "efn_forecast_calendar.py").read_text(encoding="utf-8")
MINMAX = (MODELS_DIR / "efn_demand_forecast.py").read_text(encoding="utf-8")
HAPROXY = (ROOT / "infra" / "odoo-rpc-gateway" / "haproxy.cfg").read_text(encoding="utf-8")


def _int_const(src: str, name: str) -> int:
    match = re.search(rf"^{name} = ([0-9 *]+)$", src, re.MULTILINE)
    assert match, name
    return math.prod(int(part) for part in match.group(1).split("*"))


def _quoted(block: str) -> set[str]:
    return set(re.findall(r'"([^"]+)"', block))


def _between(src: str, start: str, end: str) -> str:
    i = src.index(start)
    return src[i : src.index(end, i)]


def test_limits_match() -> None:
    assert _int_const(API, "MAX_ITEMS") == contract.MAX_ITEMS == 500
    assert _int_const(API, "MAX_PAYLOAD_BYTES") == contract.MAX_BODY_BYTES == 512 * 1024
    assert _int_const(API, "SCHEMA_VERSION") == contract.SCHEMA_VERSION
    assert _int_const(API, "MAX_CHUNKS") == 10_000
    horizon = _int_const(API, "MAX_HORIZON_DAYS")
    assert horizon == 120
    base = {
        "schema_version": 1,
        "run_id": "6f1c2b8e-0d3a-4c8e-9d61-1b5a2f4e7c90",
        "company_code": "SMD",
        "model_version": MODEL_VERSION,
        "kind": "daily",
        "generated_at": "2026-11-18T16:30:00Z",
        "chunk_total": 1,
    }
    contract.validate_run_open({**base, "horizon_days": horizon})
    with pytest.raises(contract.ContractError) as err:  # lokal menolak lebih dulu daripada Odoo (tanpa round-trip)
        contract.validate_run_open({**base, "horizon_days": horizon + 1})
    assert err.value.code == "horizon_days"


def test_enums_and_keys_match() -> None:
    models = set(re.findall(r'\("(\w+)", ', _between(DAILY, "FORECAST_MODELS = [", "]")))
    assert models == set(contract.MODELS)
    kinds = set(re.findall(r'\("(\w+)", ', _between(CALENDAR, "CALENDAR_KINDS = [", "]")))
    assert kinds == set(contract.CALENDAR_KINDS)
    runs = set(re.findall(r'\("(\w+)", ', _between(RUN, "RUN_KINDS = [", "]")))
    assert runs == set(contract.RUN_KINDS)
    assert _quoted(_between(DAILY, "LINE_KEYS = frozenset(", ")")) == set(contract.FORECAST_LINE_KEYS)
    assert _quoted(_between(MINMAX, "keys = {", "}")) == set(contract.MINMAX_LINE_KEYS)
    assert _quoted(_between(RUN, "METRIC_KEYS = (", ")")) == set(contract.METRIC_KEYS)
    open_block = _between(RUN, "def efn_forecast_run_open", "fa.check_company_code")
    assert _quoted(_between(open_block, "(\n", "),\n        )")) == set(contract.RUN_OPEN_KEYS)
    entry_keys = re.search(r"set\(entry\) != \{([^}]+)\}", CALENDAR)
    assert entry_keys and _quoted(entry_keys.group(1)) == set(contract.CALENDAR_ENTRY_KEYS)


def test_error_codes_used_by_worker_exist_in_odoo() -> None:
    codes = _quoted(_between(API, "ERROR_CODES = frozenset(", ")\n"))
    # kode yang diperlakukan khusus / diharapkan worker (runner & test_odoo)
    assert {"incomplete_run", "unknown_run", "run_conflict", "run_closed", "invalid_chunk", "too_many_items"} <= codes


def test_model_version_and_company_patterns_accepted_by_odoo() -> None:
    odoo_version = re.search(r'^MODEL_VERSION_RE = re\.compile\(r"([^"]+)"\)$', API, re.MULTILINE)
    assert odoo_version and re.fullmatch(odoo_version.group(1), MODEL_VERSION)
    assert re.fullmatch(odoo_version.group(1), "g44-1.0.0")
    odoo_company = re.search(r'^COMPANY_CODE_RE = re\.compile\(r"([^"]+)"\)$', API, re.MULTILINE)
    assert odoo_company and odoo_company.group(1) == contract.COMPANY_RE.pattern
    odoo_uuid = re.search(r'^UUID4_RE = re\.compile\(r"([^"]+)"\)$', API, re.MULTILINE)
    assert odoo_uuid and odoo_uuid.group(1) == contract.UUID4_RE.pattern


def test_service_level_range_matches_odoo() -> None:
    bounds = re.search(r'if not ([0-9.]+) <= float\(line\["service_level"\]\) < ([0-9.]+):', MINMAX)
    assert bounds, "pemeriksaan service_level Odoo berubah; sesuaikan test ini"
    assert float(bounds.group(1)) == contract.SERVICE_LEVEL_MIN
    assert float(bounds.group(2)) == contract.SERVICE_LEVEL_MAX


def test_value_cap_matches_odoo_when_present() -> None:
    sources = "\n".join(p.read_text(encoding="utf-8") for p in sorted(MODELS_DIR.glob("*.py")))
    if "max_qty_per_line" not in sources:
        pytest.skip("Odoo belum memuat efn_forecast.max_qty_per_line (rencana efn-odoo-engineer)")
    assert "value_too_large" in sources
    # Default Odoo ada di dua tempat: konstanta kode (fallback) dan data ir.config_parameter; keduanya = worker.
    default = re.search(r"^DEFAULT_MAX_QTY\s*=\s*([0-9][0-9_]*(?:\.[0-9]+)?)", sources, re.MULTILINE)
    assert default, "konstanta DEFAULT_MAX_QTY Odoo tidak ditemukan; sesuaikan test ini"
    assert float(default.group(1).replace("_", "")) == contract.MAX_QTY_PER_LINE
    param_xml = (MODELS_DIR.parent / "data" / "ir_config_parameter.xml").read_text(encoding="utf-8")
    xml_default = re.search(
        r"<field name=\"key\">efn_forecast\.max_qty_per_line</field>\s*<field name=\"value\">([0-9.]+)</field>",
        param_xml,
    )
    assert xml_default and float(xml_default.group(1)) == contract.MAX_QTY_PER_LINE


def test_gateway_allowlist_equals_client_methods() -> None:
    gateway = set(re.findall(r"acl p_forecast\s+path -m str (\S+)", HAPROXY))
    assert gateway == {f"/json/2/{model}/{method}" for model, method in METHODS.values()}
    assert len(gateway) == 5
