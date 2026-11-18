"""Uji lintas-sisi (SEC-201 / SEC-197): laporan validator W1 diverifikasi fungsi importer Odoo dan sebaliknya.

`importer.py` butuh Odoo/psycopg2, jadi fungsi `derive_validation_key`, `sign_validation_report`,
`verify_validation_report` diambil dari SOURCE-nya (ast) lalu dijalankan dengan dependensi minimal (hashlib, hmac,
HKDF, `canonical_json` vendored). Plus vektor tetap: kunci & tanda tangan dihitung ulang secara independen.
"""

import ast
import hashlib
import hmac
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from efn_mig import bundle as b
from efn_mig.xmlid import xmlid

REPO = Path(__file__).resolve().parents[3]
IMPORT_PKG = REPO / "services" / "odoo" / "addons" / "efn_legacy_history" / "esb_import"
IMPORTER = IMPORT_PKG / "importer.py"
FUNCS = ("derive_validation_key", "sign_validation_report", "verify_validation_report")
SECRET = bytes(range(32)).hex()  # kunci uji sintetis (64 hex), bukan secret


class ConfigError(Exception):
    pass


def importer_functions() -> dict[str, Any]:
    tree = ast.parse(IMPORTER.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCS]
    val_info = next(
        n for n in tree.body
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "VAL_INFO" for t in n.targets)
    )  # fmt: skip
    assert {n.name for n in nodes} == set(FUNCS), "fungsi tanda tangan importer berubah nama"
    # Salinan vendored efn_mig/bundle.py sisi importer (canonical_json yang benar-benar dipakai importer).
    spec = importlib.util.spec_from_file_location("_vendored_efn_mig_bundle", IMPORT_PKG / "efn_mig" / "bundle.py")
    assert spec is not None and spec.loader is not None
    vendored = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = vendored  # dataclass butuh modul terdaftar
    spec.loader.exec_module(vendored)
    ns: dict[str, Any] = {
        "hashlib": hashlib, "hmac": hmac, "re": re, "HKDF": HKDF, "hashes": hashes,
        "canonical_json": vendored.canonical_json, "IntegrityError": b.IntegrityError, "ConfigError": ConfigError,
    }  # fmt: skip
    module = ast.Module(body=[val_info, *nodes], type_ignores=[])
    exec(compile(module, str(IMPORTER), "exec"), ns)  # noqa: S102 -- kode repo sendiri, untuk uji kontrak
    return ns


def test_validation_key_identical_and_fixed_vector() -> None:
    ns = importer_functions()
    keys = b.derive_keys(SECRET)
    assert ns["derive_validation_key"](SECRET) == keys.val
    independent = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"efn:mig-esb:val:v1").derive(
        bytes.fromhex(SECRET)
    )
    assert keys.val == independent
    report = {"run_id": "11111111-2222-4333-8444-555555555555", "bundle_sha256": "a" * 64,
              "manifest_sha256": "b" * 64, "errors": 0, "warnings": 0, "findings": [], "catatan": "ü"}  # fmt: skip
    signed = b.sign_validation(report, keys)
    body = {k: v for k, v in signed.items() if k != "sig"}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert signed["sig"] == hmac.new(independent, raw, hashlib.sha256).hexdigest()


def test_w1_signed_report_verified_by_importer_and_back() -> None:
    ns = importer_functions()
    keys = b.derive_keys(SECRET)
    report = {
        "run_id": "11111111-2222-4333-8444-555555555555",
        "bundle_sha256": "c" * 64,
        "manifest_sha256": "d" * 64,
        "errors": 0,
        "warnings": 1,
        "findings": [{"level": "warning", "code": "ROWS_UNMAPPED", "entity": "x", "key": "3", "message": "m"}],
    }
    signed = b.sign_validation(report, keys)
    ns["verify_validation_report"](signed, keys.val)  # laporan W1 diterima importer
    with pytest.raises(b.IntegrityError):
        ns["verify_validation_report"]({**signed, "errors": 1}, keys.val)
    with pytest.raises(b.IntegrityError):
        ns["verify_validation_report"](b.sign_validation(report, b.derive_keys("ff" * 32)), keys.val)
    # Arah sebaliknya: tanda tangan importer == tanda tangan W1 (byte-per-byte).
    from_importer = ns["sign_validation_report"]({**report, "format": b.VALIDATION_FORMAT}, keys.val)
    assert from_importer["sig"] == signed["sig"]
    with pytest.raises(ConfigError):
        ns["derive_validation_key"]("zz")


def test_target_warehouse_xmlid_matches_importer_prefix() -> None:
    """Kontrak 1: target_warehouses = xmlid lengkap `efn_mig_esb.<brand>_<co>_branch_wh_<id>`."""
    key = xmlid("pb", "PB", "branch_wh", 11)
    assert key == "efn_mig_esb.pb_pb_branch_wh_11"
    text = (IMPORT_PKG / "mapping.py").read_text(encoding="utf-8")
    assert 'return f"{MODULE}.{brand}_{company.lower()}_"' in text
    assert "f\"{m.company_prefix(str(man['brand']), codes[0])}branch_wh_\"" in IMPORTER.read_text(encoding="utf-8")
