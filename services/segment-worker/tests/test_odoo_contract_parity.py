"""Paritas statis worker <-> Odoo `efn_campaign_proposal_upsert` (QA G50; membaca source tanpa menjalankan Odoo).

Sisi Odoo = `services/odoo/addons/efn_campaign/models/{efn_campaign,campaign_api}.py` dan haproxy gateway. Test
ini membaca source-nya (regex) agar perubahan salah satu sisi tertangkap. Anchor regex mengikuti format kode saat ini;
bila `ruff format` mengubahnya, sesuaikan anchor, bukan nilai kontrak.
"""

import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from conftest import key

from efn_segment.contract import PROPOSAL_KEYS, SCHEMA_VERSION, proposal_payload
from efn_segment.odoo import METHOD, MODEL
from efn_segment.segments import SEGMENT_CODES, Segment, SegmentParams

ROOT = Path(__file__).resolve().parents[3]
ODOO_MODEL = ROOT / "services/odoo/addons/efn_campaign/models/efn_campaign.py"
ODOO_API = ROOT / "services/odoo/addons/efn_campaign/models/campaign_api.py"
HAPROXY = ROOT / "infra/odoo-rpc-gateway/haproxy.cfg"
RUN = str(uuid.uuid4())


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _regex_const(src: str, name: str) -> re.Pattern[str]:
    match = re.search(rf'^{name} = re\.compile\(r"(.+?)"\)', src, re.M)
    assert match, f"{name} tidak ditemukan di campaign_api.py"
    return re.compile(match.group(1))


def _int_const(src: str, name: str) -> int:
    match = re.search(rf"^{name} = ([0-9_]+)", src, re.M)
    assert match, f"{name} tidak ditemukan"
    return int(match.group(1).replace("_", ""))


def _segment() -> Segment:
    return Segment(
        code="price_sensitive",
        members=tuple(sorted(key(i) for i in range(25))),
        product_subjects={10: 20, 11: 3, 12: 15},
        reason_counts={"market": 12, "habit": 15},
    )


def _worker_payload() -> dict[str, Any]:
    return proposal_payload("SMD", RUN, _segment(), SegmentParams(), date(2026, 11, 19), 10, 10)


def test_model_method_and_gateway_allowlist() -> None:
    assert (MODEL, METHOD) == ("efn.campaign", "efn_campaign_proposal_upsert")
    assert f"def {METHOD}(" in _read(ODOO_MODEL)
    assert f"/json/2/{MODEL}/{METHOD}" in _read(HAPROXY)


def test_schema_version_and_segment_code_and_run_id_rules_compatible() -> None:
    api = _read(ODOO_API)
    assert _int_const(api, "SCHEMA_VERSION") == SCHEMA_VERSION
    seg_re = _regex_const(api, "SEGMENT_CODE_RE")
    assert all(seg_re.match(code) for code in SEGMENT_CODES)
    assert _regex_const(api, "UUID4_RE").match(RUN)
    assert _regex_const(api, "COMPANY_CODE_RE").match("SMD")


def test_top_level_keys_are_subset_of_odoo_allowlist() -> None:
    src = _read(ODOO_MODEL)
    block = re.search(
        r"def efn_campaign_proposal_upsert.*?ca\.check_payload\(\s*payload,\s*\((.*?)\),\s*\((.*?)\),", src, re.S
    )
    assert block, "blok check_payload proposal_upsert tidak ditemukan"
    allowed = set(re.findall(r'"([a-z_]+)"', block.group(1)))
    required = set(re.findall(r'"([a-z_]+)"', block.group(2)))
    assert set(PROPOSAL_KEYS) <= allowed, "worker mengirim kunci yang ditolak Odoo (invalid_input)"
    assert required <= set(PROPOSAL_KEYS), "Odoo mewajibkan kunci yang tidak dikirim worker"


def test_member_count_bounds_compatible() -> None:
    # Odoo: 0 <= count <= MAX_MEMBER_COUNT; worker mengirim 1..jumlah anggota (<= puluhan ribu)
    assert _int_const(_read(ODOO_MODEL), "MAX_MEMBER_COUNT") >= 1_000_000


def test_worker_metrics_accepted_by_odoo_validator() -> None:
    """QA G50 #1 (diperbaiki): metrics hanya {kunci: angka}; produk teratas di `summary`, waktu di `generated_at`."""
    api = _read(ODOO_API)
    key_re = _regex_const(api, "METRIC_KEY_RE")
    pii_re = _regex_const(api, "PII_RE")
    payload = _worker_payload()
    metrics = payload["metrics"]
    assert len(metrics) <= _int_const(_read(ODOO_MODEL), "MAX_METRICS")
    for name, value in metrics.items():
        assert key_re.match(name) and not pii_re.search(name)
        # ca.is_number: int/float bukan bool (dict/list/str ditolak); Odoo juga menolak |v| >= 1e12
        assert isinstance(value, int | float) and not isinstance(value, bool), f"metrics.{name} bukan angka"
        assert abs(value) < 1e12
    assert {"reason_market", "reason_habit", "product_count", "lookback_days"} <= set(metrics)


def test_summary_and_generated_at_accepted_by_odoo_validator() -> None:
    src = _read(ODOO_MODEL)
    payload = _worker_payload()
    summary = payload["summary"]
    assert isinstance(summary, str) and summary
    assert len(summary) <= _int_const(src, "MAX_SUMMARY")
    assert not _regex_const(_read(ODOO_API), "PII_RE").search(summary)
    rfc = re.search(r'^RFC3339_UTC_RE = re\.compile\(r"(.+?)"\)', src, re.M)
    assert rfc, "RFC3339_UTC_RE tidak ditemukan di efn_campaign.py"
    assert re.compile(rfc.group(1)).match(payload["generated_at"])


def test_worker_constants_mirror_odoo() -> None:
    from efn_segment import contract

    api, src = _read(ODOO_API), _read(ODOO_MODEL)
    assert contract.METRIC_KEY_RE.pattern == _regex_const(api, "METRIC_KEY_RE").pattern
    assert contract.PII_RE.pattern == _regex_const(api, "PII_RE").pattern
    assert contract.MAX_METRICS == _int_const(src, "MAX_METRICS")
    assert contract.MAX_SUMMARY == _int_const(src, "MAX_SUMMARY")
    assert contract.MAX_MEMBER_COUNT <= _int_const(src, "MAX_MEMBER_COUNT")
