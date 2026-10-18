"""Smoke G18 (K-18-6): rekonsiliasi harian Poin EFN mode `payment` untuk HARI INI (dev saja, read-only).

Dijalankan run-e2e-smoke.sh lewat `docker exec -i efn-etl-1 python - < etl_points_recon_smoke.py` di container ETL
(konfigurasi, secret, dan TLS persis seperti job ETL). Job harian ETL hanya memeriksa hari WIB yang sudah utuh +
`ETL_LOYALTY_RECON_SETTLE_HOURS`, sehingga transaksi smoke hari ini baru diperiksa besok. Skrip ini memanggil fungsi
yang SAMA (`load_inputs` + `run_daily_points`) dengan `now` disimulasikan = besok 07:00 WIB, tanpa menulis temuan ke
ClickHouse dan tanpa log `EFN_LOYALTY_RECON_ETL` (tidak memicu alert). `ETL_LOYALTY_RECON_NOT_BEFORE` dihormati.

Keluaran: satu baris `SMOKE_RESULT {json}` (angka agregat per PT, tanpa data pribadi).
"""

import json
import os
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from efn_etl.adapters import PgSource
from efn_etl.config import Settings
from efn_etl.loyalty_recon import WIB, LoyaltyReconSettings, load_inputs, run_daily_points

settings = Settings.from_env()
cfg = LoyaltyReconSettings(
    lookback=timedelta(hours=settings.loyalty_recon_lookback_hours),
    settle=timedelta(hours=settings.loyalty_recon_settle_hours),
    points_codes=settings.points_product_codes,
    not_before=settings.loyalty_recon_not_before,
    points_mode=settings.loyalty_points_mode,
    points_tolerance_idr=Decimal(settings.loyalty_points_tolerance_idr),
)
today = datetime.now(UTC).astimezone(WIB).date()
now = datetime.combine(today + timedelta(days=1), time(7, 0), tzinfo=WIB).astimezone(UTC)
platform = PgSource(settings.platform_pg, settings.statement_timeout_ms)
odoo = PgSource(settings.odoo_pg, settings.statement_timeout_ms)
inputs, companies = load_inputs(platform, odoo, cfg, now)
legacy = frozenset(
    o.uuid for o in inputs.orders_by_uuid.values() if (pl := inputs.points_lines.get(o.id)) and pl.legacy
)
days = run_daily_points(platform, odoo, cfg, now, companies, legacy) if cfg.payment_mode else []
rows = [
    {
        "day": d.day.isoformat(),
        "company": d.company_code,
        "odoo_idr": str(d.odoo_idr),
        "redeem_idr": str(d.redeem_idr),
        "restored_idr": str(d.restored_idr),
        "diff_idr": str(d.diff),
        "orders": len(d.orders),
        "mismatched": len(d.mismatched),
        "restore_mismatched": len(getattr(d, "restore_mismatched", ())),  # SEC-118
    }
    for d in days
]
today_rows = [r for r in rows if r["day"] == today.isoformat()]
run_refs = {x for x in os.environ.get("SMOKE_REFS", "").split(",") if x}
run_mismatched = sorted(
    {ref for d in days for ref in (d.mismatched | set(getattr(d, "restore_mismatched", ()))) if ref in run_refs}
)
other_mismatched = sorted({ref for d in days for ref in d.mismatched if ref not in run_refs})
print(
    "SMOKE_RESULT "
    + json.dumps(
        {
            "mode": cfg.points_mode,
            "today": today.isoformat(),
            "not_before": cfg.not_before.isoformat() if cfg.not_before else None,
            "days": rows,
            "today_orders": sum(r["orders"] for r in today_rows),
            "today_mismatched": sum(r["mismatched"] for r in today_rows),
            "today_diff_zero": all(Decimal(r["diff_idr"]) == 0 for r in today_rows),
            "today_restored_idr": str(sum(Decimal(r["restored_idr"]) for r in today_rows)),
            "today_restore_mismatched": sum(r["restore_mismatched"] for r in today_rows),
            "run_refs": len(run_refs),
            "run_refs_checked": len(run_refs & {ref for d in days for ref in d.orders}),
            "run_mismatched": len(run_mismatched),
            "other_mismatched": other_mismatched[:5],
        }
    ),
    flush=True,
)
