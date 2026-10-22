"""G22 (docs/04 §14.D.9): konsistensi statis dashboard poin hangus dengan kontrak metrik BFF + label promtail.

Tanpa Docker: memastikan nama metrik = kontrak, label Loki (expiry_status/expiry_phase) hanya nilai yang dihasilkan
tahap promtail, dan alert run TIDAK diduplikasi di Prometheus (alert run milik loki-rules/efn-loyalty.yml).
"""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OBS = REPO / "infra" / "observability"
DASHBOARD = OBS / "grafana" / "provisioning" / "dashboards" / "json" / "efn-loyalty-expiry.json"
PROMTAIL = OBS / "promtail.yml"
ALERTS = OBS / "alert-rules.yml"
CONTRACT_METRICS = {
    "efn_loyalty_expiry_total",
    "efn_loyalty_expired_points_total",
    "efn_loyalty_expiry_notice_failed_total",
}


def _dashboard() -> dict:
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def test_dashboard_metrics_match_contract() -> None:
    used: set[str] = set()
    for panel in _dashboard()["panels"]:
        for target in panel["targets"]:
            if panel["datasource"]["type"] == "prometheus":
                used |= set(re.findall(r"\b(efn_[a-z_]+)\{", target["expr"]))
                assert 'job="api-gateway"' in target["expr"]
    assert used == CONTRACT_METRICS


def test_dashboard_loki_labels_exist_in_promtail() -> None:
    text = PROMTAIL.read_text(encoding="utf-8")
    stage = re.search(r'"status":"\(\?P<expiry_status>([a-z_|]+)\)","phase":"\(\?P<expiry_phase>([a-z_|]+)\)"', text)
    assert stage, "tahap promtail EFN_LOYALTY_EXPIRY_RUN tidak ditemukan"
    statuses, phases = set(stage.group(1).split("|")), set(stage.group(2).split("|"))
    for panel in _dashboard()["panels"]:
        if panel["datasource"]["type"] != "loki":
            continue
        for target in panel["targets"]:
            expr = target["expr"]
            assert 'stream="efn_loyalty", loyalty_event="expiry_run"' in expr
            assert set(re.findall(r'expiry_status="([a-z_]+)"', expr)) <= statuses
            assert set(re.findall(r'expiry_phase="([a-z_]+)"', expr)) <= phases


def test_prometheus_does_not_duplicate_log_based_run_alerts() -> None:
    alerts = ALERTS.read_text(encoding="utf-8")
    assert "alert: EfnLoyaltyExpiryNoticeFailed" in alerts
    for name in ("EfnLoyaltyExpiryAborted", "EfnLoyaltyExpiryFailed", "EfnLoyaltyExpiryHeartbeatMissing"):
        assert f"alert: {name}" not in alerts
