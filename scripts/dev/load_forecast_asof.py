# ruff: noqa: F821
# G51 A6 (docs/design/G51_SMD_E2E_LEAD_SPEC.md §A6; runbook docs/runbooks/demo-supermarket-seed.md §8).
# HANYA DB demo. Jalankan di odoo shell (env tersedia sebagai global `env`):
#   docker cp var/forecast-asof efn-odoo-1:/tmp/forecast-asof
#   docker exec -i -e EFN_ASOF_DIR=/tmp/forecast-asof -e EFN_ASOF_CONFIRM=demo efn-odoo-1 \
#     odoo shell -c /etc/odoo/odoo.conf -d efn_smd_demo --no-http < scripts/dev/load_forecast_asof.py
"""Muat file ekspor `python -m efn_forecast run --as-of ... --out ...` (forecast-worker G51) sebagai run forecast
historis di Odoo.

Per file: satu `efn.forecast.run` kind `daily`, state `ok`, `is_active = False`, `generated_at` = Minggu 23:00 WIB dari
file (dicek = as_of − 1 hari 16:00 UTC), `opened_at` = generated_at, `closed_at` = sekarang; baris `efn.forecast.daily`
divalidasi ULANG dengan validator Odoo (`_efn_validate_line`, cakupan gudang/produk PT, plafon nilai) relatif terhadap
tanggal batch, bukan hari ini. TIDAK memanggil `efn_forecast_run_close` / `_efn_on_run_closed_ok` -> run aktif tidak
berubah dan usulan replenishment tidak dipicu. Idempoten: run_id (deterministik per PT x tanggal) yang sudah ada
dilewati. Retensi Odoo (`efn_forecast.daily_retention_days`, 35 hari setelah closed_at) menghapus baris run nonaktif;
salinan ClickHouse (fact_forecast_daily) tetap untuk akurasi.
"""

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from odoo import fields
from odoo.addons.efn_demand_forecast.models import forecast_api as fa

FORMAT = "efn_forecast_asof/1"
BATCH = 5000
TAG = "[efn-asof]"

if os.environ.get("EFN_ASOF_CONFIRM") != "demo":
    raise SystemExit(f"{TAG} set EFN_ASOF_CONFIRM=demo (skrip ini hanya untuk DB demo)")
_dir = os.environ.get("EFN_ASOF_DIR")
if not _dir:
    raise SystemExit(f"{TAG} set EFN_ASOF_DIR ke folder berisi file JSON keluaran --as-of")
folder = Path(_dir)
files = sorted(folder.glob("*.json"))
if not files:
    raise SystemExit(f"{TAG} tidak ada file *.json di {folder}")

Run = env["efn.forecast.run"].sudo().with_context(tracking_disable=True, mail_create_nolog=True, mail_notrack=True)
Daily = env["efn.forecast.daily"].sudo()
Chunk = env["efn.forecast.chunk"].sudo()
cap = fa.max_qty(env)
now = fields.Datetime.now()
summary = {"created": 0, "skipped": 0, "lines": 0, "rejected": 0}

for path in files:
    doc = json.loads(path.read_text(encoding="utf-8"))
    run_open, chunks = doc.get("run_open") or {}, doc.get("chunks") or []
    run_id = run_open.get("run_id")
    if (
        doc.get("format") != FORMAT
        or (doc.get("run_close") or {}).get("status") != "ok"
        or run_open.get("kind") != "daily"
        or not isinstance(run_id, str)
        or not fa.UUID4_RE.match(run_id)
        or not chunks
        or any(c.get("run_id") != run_id for c in chunks)
    ):
        raise SystemExit(f"{TAG} {path.name}: format/run tidak valid")
    as_of = datetime.strptime(str(doc.get("as_of")), "%Y-%m-%d").date()
    generated = fa.parse_rfc3339_utc(run_open.get("generated_at"))
    expected = datetime.combine(as_of - timedelta(days=1), datetime.min.time()) + timedelta(hours=16)  # 23:00 WIB
    if generated != expected or generated > now - timedelta(days=1):
        raise SystemExit(f"{TAG} {path.name}: generated_at harus Minggu 23:00 WIB sebelum as_of dan di masa lalu")
    code = run_open.get("company_code")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z]{2,8}", code):
        raise SystemExit(f"{TAG} {path.name}: company_code tidak valid")
    company = env["res.company"].sudo().search([("efn_brand_code", "=", code)], limit=1)
    if not company:
        raise SystemExit(f"{TAG} {path.name}: PT {code} tidak ada")
    if Run.search_count([("run_id", "=", run_id)]):
        summary["skipped"] += 1
        print(f"{TAG} {path.name}: run {run_id} sudah ada -> dilewati")
        continue
    horizon = run_open.get("horizon_days")
    if not fa.is_int(horizon) or not 1 <= horizon <= fa.MAX_HORIZON_DAYS:
        raise SystemExit(f"{TAG} {path.name}: horizon_days tidak valid")
    lines = [line for c in sorted(chunks, key=lambda c: c.get("chunk_no", 0)) for line in (c.get("lines") or [])]
    scoped = Daily.with_company(company)
    warehouses, products = Chunk.with_company(company)._efn_scope(env, company, lines)
    batch_day = as_of - timedelta(days=1)  # tanggal batch WIB; baris valid = [batch_day, batch_day + horizon]
    values, seen, rejected = [], set(), 0
    run = Run.create(
        {
            "run_id": run_id,
            "company_id": company.id,
            "model_version": str(run_open.get("model_version") or "")[:32],
            "kind": "daily",
            "generated_at": generated,
            "horizon_days": horizon,
            "chunk_total": len(chunks),
            "state": "ok",
            "is_active": False,
            "opened_at": generated,
            "closed_at": now,
            "metric_series": int((doc["run_close"].get("metrics") or {}).get("series") or 0),
        }
    )
    last_day = batch_day + timedelta(days=horizon)
    for line in lines:
        if scoped._efn_validate_line(line, warehouses, products, batch_day, last_day, cap=cap):
            rejected += 1
            continue
        wh = warehouses[line["warehouse_code"]]
        day = fa.parse_date(line["date"])
        key = (wh.id, line["product_id"], day)
        if key in seen:
            rejected += 1
            continue
        seen.add(key)
        values.append(
            {
                "run_id": run.id,
                "company_id": company.id,
                "warehouse_id": wh.id,
                "product_id": line["product_id"],
                "date": day,
                "yhat": float(line["yhat"]),
                "p10": float(line["p10"]),
                "p90": float(line["p90"]),
                "sigma": float(line["sigma"]),
                "model": line["model"],
            }
        )
    for i in range(0, len(values), BATCH):
        Daily.create(values[i : i + BATCH])
    env.cr.commit()
    summary["created"] += 1
    summary["lines"] += len(values)
    summary["rejected"] += rejected
    print(f"{TAG} {path.name}: run {run_id} as_of {as_of} -> {len(values)} baris, {rejected} ditolak")

print(f"{TAG} selesai: {summary}")
