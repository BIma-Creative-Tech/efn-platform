"""Kode PT IMD & CUP (docs/04 §27.1, ADR-042/043) + SMD (G44, §12.1 revisi 18 Nov 2026) sama di SEMUA lapisan.

Satu daftar acuan platform (10 entitas); lapisan khusus migrasi ESB memakai 9 kode G43 (tanpa SMD).
Tiap lapisan diurai dari sumber (ast / regex), bukan diimpor, supaya test jalan di container `esb-migrate` tanpa
Odoo/NestJS. Lapisan yang tertinggal = BFF menolak ledger IMD/CUP (CHECK platform), mart ETL salah, Odoo menolak
`efn_brand_code`, importer menolak company, dsb. Dilewati bila checkout repo tidak penuh.
"""

import ast
import re
from pathlib import Path

import pytest

from efn_mig import mapping as export_mapping
from efn_mig.settings import MigSettings

REPO = Path(__file__).resolve().parents[3]
if not (REPO / "services" / "odoo" / "addons").is_dir():
    pytest.skip("checkout repo penuh dibutuhkan", allow_module_level=True)

G43_CODES = frozenset({"EFN", "GL", "PB", "BCR", "BCF", "WTZ", "IMD", "CUP", "FARM"})  # migrasi ESB + PG 18
EXPECTED = G43_CODES | {"SMD"}  # daftar platform setelah merge G43 + G44 (BFF, Odoo, ETL, CH, PG 19, deploy)
ESB_COMPANIES = frozenset({"PB", "WTZ", "BCF", "BCR", "IMD", "CUP"})  # company hasil migrasi brand ESB
QUOTED = re.compile(r"""['"]([A-Z]{2,4})['"]""")


def _read(rel: str) -> str:
    path = REPO / rel
    if not path.is_file():
        pytest.skip(f"{rel} tidak ada di checkout")
    return path.read_text(encoding="utf-8")


def _py_value(rel: str, name: str) -> ast.expr:
    tree = ast.parse(_read(rel))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            assert node.value is not None
            return node.value
    raise AssertionError(f"{name} tidak ditemukan di {rel}")


def _py_set(rel: str, name: str) -> frozenset[str]:
    value = _py_value(rel, name)
    if isinstance(value, ast.Call):  # frozenset({...})
        value = value.args[0]
    return frozenset(ast.literal_eval(value))


def _quoted(text: str) -> frozenset[str]:
    return frozenset(QUOTED.findall(text))


def test_reference_list_is_ten_entities() -> None:
    assert len(G43_CODES) == 9 and len(EXPECTED) == 10 and {"IMD", "CUP", "SMD"} <= EXPECTED


def test_export_mapping_and_settings_know_imd_cup() -> None:
    assert export_mapping.COMPANY_CODES == G43_CODES  # SMD bukan brand ESB
    assert MigSettings.from_env({"APP_ENV": "dev"}).saldo_blocked == frozenset({"IMD", "CUP"})


def test_python_layers() -> None:
    assert _py_set("services/esb-extract/efn_esb/config.py", "ALLOWED_COMPANY_CODES") == G43_CODES
    assert _py_set("services/etl/efn_etl/entities.py", "COMPANY_CODES") == EXPECTED
    codes = [
        t[0]
        for t in ast.literal_eval(_py_value("services/odoo/addons/efn_core/models/res_company.py", "EFN_BRAND_CODES"))
    ]
    assert frozenset(codes) == EXPECTED and len(codes) == len(EXPECTED)
    business = ast.literal_eval(
        _py_value("services/odoo/addons/efn_core/models/res_company.py", "EFN_BRAND_BUSINESS_TYPE")
    )
    assert business["IMD"] == business["CUP"] == "fnb" and frozenset(business) == EXPECTED
    assert _py_set("services/odoo/addons/efn_insight/lib/insight_token.py", "ESB_CODES") == ESB_COMPANIES


def test_esb_extract_default_company_matches_importer_targets() -> None:
    defaults = ast.literal_eval(_py_value("services/esb-extract/efn_esb/config.py", "DEFAULT_COMPANY"))
    assert defaults["imd"] == "IMD" and defaults["cup"] == "CUP"
    text = _read("services/odoo/addons/efn_legacy_history/esb_import/importer.py")
    block = text[text.index("BRAND_COMPANIES = {") :]
    block = block[: block.index("\n}\n")]
    per_brand = {
        brand: frozenset(QUOTED.findall(line.split(":", 1)[1]))
        for line in block.splitlines()[1:]
        if (m := re.match(r'\s*"([a-z]+)":', line)) and (brand := m.group(1))
    }
    assert set(per_brand) == set(defaults) and per_brand["imd"] == {"IMD"} and per_brand["cup"] == {"CUP"}
    for brand, company in defaults.items():
        assert company in per_brand[brand], f"importer: target impor brand {brand} tidak memuat {company}"
    assert frozenset().union(*per_brand.values()) == ESB_COMPANIES


def test_bff_layers() -> None:
    loyalty = _read("services/api-gateway/src/loyalty/loyalty.service.ts")
    match = re.search(r"COMPANY_CODES\s*=\s*\[(.*?)\]\s*as const", loyalty, re.S)
    assert match and _quoted(match.group(1)) == EXPECTED
    metrics = _read("services/api-gateway/src/common/metrics/metrics.service.ts")
    match = re.search(r"const issuer of \[(.*?)\]", metrics, re.S)
    assert match and _quoted(match.group(1)) == EXPECTED


def test_insight_web_claims() -> None:
    claims = _read("services/insight-web/lib/claims.ts")
    match = re.search(r"ESB_CODES\s*=\s*\[(.*?)\]\s*as const", claims, re.S)
    assert match and _quoted(match.group(1)) == ESB_COMPANIES


def test_sql_layers() -> None:
    pg = _read("infra/postgres/init/18-platform-g43.sql")
    match = re.search(r"codes constant text := '(.*?)';\n", pg)
    assert match and _quoted(match.group(1).replace("''", "'")) == G43_CODES
    pg19 = _read("infra/postgres/init/19-platform-g44.sql")  # pagar merge G44: wajib memuat IMD/CUP
    match = re.search(r"code_list constant text\[\] := ARRAY\[(.*?)\];", pg19)
    assert match and _quoted(match.group(1)) == EXPECTED
    deploy = _read("scripts/lib/deploy-validate.sh")
    match = re.search(r"^EFN_COMPANY_CODES=\((.*?)\)$", deploy, re.M)
    assert match and frozenset(match.group(1).split()) == EXPECTED
    ch = _read("infra/clickhouse/init/08-g20-entities.sql")
    match = re.search(r"c\.brand NOT IN \((.*?)\)", ch)
    assert match and _quoted(match.group(1)) == EXPECTED
