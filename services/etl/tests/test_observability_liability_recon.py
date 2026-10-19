"""G21 (docs/04 §13.1 no. 7): kontrak log Odoo `EFN_LOYALTY_RECON` -> promtail tahap 2c -> alert Loki + Grafana.

Uji statis tanpa Docker: regex selector promtail (RE2) diterapkan dengan `re` Python atas fixture yang SAMA dengan uji
regresi promtail (`infra/observability/tests/promtail/fixtures/odoo.txt`, scripts/test-promtail-redaction.sh). Pola
yang dipakai hanya subset sintaks bersama RE2/Python (kelas, kuantifier terbatas, alternasi, grup). Uji perilaku
promtail sesungguhnya tetap scripts/test-promtail-redaction.sh (CI job promtail-redaction).
"""

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
OBS = REPO / "infra" / "observability"
PROMTAIL = OBS / "promtail.yml"
RULES = OBS / "loki-rules" / "efn-loyalty.yml"
FIXTURE = OBS / "tests" / "promtail" / "fixtures" / "odoo.txt"
DASHBOARD = OBS / "grafana" / "provisioning" / "dashboards" / "json" / "efn-loyalty-liability-recon.json"
ALERTMANAGER = OBS / "alertmanager.yml"


def _unquote_logql(text: str) -> str:
    """String LogQL ber-kutip ganda (Go strconv.Unquote): hanya escape \\\\ dan \\" yang dipakai di promtail.yml."""
    return re.sub(r"\\(.)", lambda m: m.group(1), text)


def _selector_pattern() -> re.Pattern[str]:
    for line in PROMTAIL.read_text(encoding="utf-8").splitlines():
        if "selector:" in line and "efn_loyalty_accounting" in line:
            assert line.strip().startswith("""selector: '{service="odoo"} |~ \"""")
            raw = line.split('|~ "', 1)[1]
            assert raw.endswith("\"'")
            return re.compile(_unquote_logql(raw[: -len("\"'")]))
    raise AssertionError("selector EFN_LOYALTY_RECON Odoo tidak ditemukan di promtail.yml")


def _fixture_lines() -> list[str]:
    return [line for line in FIXTURE.read_text(encoding="utf-8").splitlines() if "EFN_LOYALTY_RECON" in line]


def _rule_regexp() -> re.Pattern[str]:
    text = RULES.read_text(encoding="utf-8")
    group = text.split("name: efn-loyalty-liability-recon", 1)[1]
    found = set(re.findall(r"\| regexp `([^`]+)`", group))
    assert len(found) == 1, found  # warning & critical memakai ekstraksi yang sama
    return re.compile(found.pop())


def _critical_threshold() -> int:
    group = RULES.read_text(encoding="utf-8").split("alert: EfnLoyaltyLiabilityReconCritical", 1)[1]
    match = re.search(r"\) > (\d+)\n", group)
    assert match
    return int(match.group(1))


def test_fixture_contract_lines_match_and_spoofs_do_not() -> None:
    pattern = _selector_pattern()
    lines = _fixture_lines()
    assert len(lines) == 4
    matched = [bool(pattern.search(line)) for line in lines]
    # (1) sort_keys + nominal 628... (2) urutan kontrak ringkas (3) logger lain (4) kunci tambahan berisi nomor HP
    assert matched == [True, True, False, False]


def _line(payload: str, level: str = "WARNING", logger: str = "odoo.addons.efn_loyalty_accounting.recon") -> str:
    return f"2026-10-20 02:00:05,101 77 {level} efn {logger}: EFN_LOYALTY_RECON {payload}"


VALID_SORTED = (
    '{"bff_closing_liability_idr": 10, "company_code": "BCR", "date": "2026-10-19", "diff_idr": 0, '
    '"odoo_liability_idr": 10}'
)


@pytest.mark.parametrize(
    "line",
    [
        _line(VALID_SORTED, level="ERROR"),
        _line(VALID_SORTED, level="DEBUG"),
        _line(VALID_SORTED, logger="odoo.addons.efn_loyalty_accounting_evil"),
        _line(VALID_SORTED, logger="odoo.addons.efn_pos_loyalty_ledger.models"),
        _line(VALID_SORTED) + " budi 081234567890",
        _line(VALID_SORTED.replace('"BCR"', '"bcr"')),
        _line(VALID_SORTED.replace('"BCR"', '"Budi Santoso"')),
        _line(VALID_SORTED.replace('"2026-10-19"', '"19/10/2026"')),
        _line(VALID_SORTED.replace('"diff_idr": 0', '"diff_idr": 0.5')),
        _line(VALID_SORTED.replace('"diff_idr": 0', '"diff_idr": "0"')),
        _line(VALID_SORTED.replace("}", ', "note": "x"}')),
        _line(VALID_SORTED[:-1] + "}\n" + VALID_SORTED),
        "prefix " + _line(VALID_SORTED),
    ],
)
def test_selector_rejects_off_schema_lines(line: str) -> None:
    assert not _selector_pattern().search(line)


@pytest.mark.parametrize(
    "payload",
    [
        VALID_SORTED,
        VALID_SORTED.replace(": ", ":").replace(", ", ","),
        json.dumps(
            {
                "company_code": "FARM",
                "date": "2026-10-19",
                "odoo_liability_idr": -1,
                "bff_closing_liability_idr": 0,
                "diff_idr": -1,
            }
        ),
        json.dumps(
            {
                "company_code": "EFN",
                "date": "2026-10-19",
                "odoo_liability_idr": 999999999999,
                "bff_closing_liability_idr": 0,
                "diff_idr": 999999999999,
            },
            sort_keys=True,
        ),
    ],
)
def test_selector_accepts_both_key_orders(payload: str) -> None:
    assert _selector_pattern().search(_line(payload, level="INFO", logger="odoo.addons.efn_loyalty_accounting"))


def test_rule_extracts_absolute_diff() -> None:
    regexp = _rule_regexp()
    values = []
    for line in _fixture_lines()[:2]:
        match = regexp.search(line)
        assert match
        values.append(int(match.group("diff_abs")))
    assert values == [5000, 0]


def test_threshold_consistent_with_grafana_and_inhibit() -> None:
    threshold = _critical_threshold()
    assert threshold == 100000  # PLACEHOLDER Finance (laporan data-AI G21); ubah bersama panel Grafana
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    stat = next(p for p in dashboard["panels"] if p["id"] == 1)
    steps = stat["fieldConfig"]["defaults"]["thresholds"]["steps"]
    assert {"color": "red", "value": threshold + 1} in steps
    for panel in dashboard["panels"]:
        assert panel["datasource"] == {"type": "loki", "uid": "loki"}
        for target in panel["targets"]:
            assert 'loyalty_event=\\"liability_recon\\"' in json.dumps(target["expr"])
    am = ALERTMANAGER.read_text(encoding="utf-8")
    assert "source_matchers: ['alertname=\"EfnLoyaltyLiabilityReconCritical\"']" in am


def test_redaction_skips_only_liability_recon_stream() -> None:
    text = PROMTAIL.read_text(encoding="utf-8")
    assert (
        """selector: '{container=~".+", stream!~"efn_audit_anchor|efn_audit_sink", loyalty_event!="liability_recon"}'"""
        in text
    )
    # Tahap 2c dipasang SEBELUM redaksi (label harus sudah ada saat selector redaksi dievaluasi).
    assert text.index("loyalty_event: liability_recon") < text.index('loyalty_event!="liability_recon"')
