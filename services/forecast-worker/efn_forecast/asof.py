"""G51 A6 (docs/design/G51_SMD_E2E_LEAD_SPEC.md §A6, keputusan lead G51): run forecast "as-of" (histori akurasi demo).

    python -m efn_forecast run --company SMD --as-of 2026-09-28 --out /out/asof-SMD-2026-09-28.json

Arti `--as-of D` (Senin): batch `daily` dihitung seolah-olah dijalankan Minggu D−1 pukul 23:00 WIB (sebelum jadwal
default 23:30): histori penjualan/stok s.d. D−1 (data setelahnya TIDAK dibaca: kueri ClickHouse dibatasi `end`),
proyeksi mulai D. Hanya HITUNG: tidak ada panggilan Odoo (Odoo menolak baris bertanggal lampau, `invalid_date`); payload
kontrak §28.2 yang sudah divalidasi ditulis ke file JSON lalu dimuat ke Odoo oleh skrip odoo-shell terpisah
`scripts/dev/load_forecast_asof.py` (run `ok`, `is_active = False`, tanpa memicu usulan replenishment). ETL lalu
menyalinnya (fc_runs/fc_daily) dan `fact_forecast_accuracy` terisi karena generated_at < tanggal proyeksi.

Tidak mengubah perilaku produksi: jalur ini hanya aktif dengan `--as-of` + `--out` dan tidak butuh FORECAST_ENABLED
(tidak ada RPC). run_id deterministik (company, tanggal, kind) -> ekspor ulang menghasilkan run_id sama dan loader
melewatinya (idempoten). File hanya berisi id teknis, kode gudang, tanggal, angka (tanpa PII).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from efn_forecast.config import Params
from efn_forecast.contract import (
    INGEST_KEYS,
    RUN_CLOSE_KEYS,
    RUN_OPEN_KEYS,
    UUID4_RE,
    ContractError,
)
from efn_forecast.schedule import WIB

EXPORT_FORMAT = "efn_forecast_asof/1"
GENERATED_AT_WIB = time(23, 0)
MAX_BACK_DAYS = 400


def parse_as_of(text: str, today: date) -> date:
    """Tanggal YYYY-MM-DD, wajib Senin, di masa lalu (<= hari ini − 1) dan tidak lebih dari 400 hari ke belakang."""
    try:
        value = date.fromisoformat(text.strip())
    except ValueError:
        raise ValueError("--as-of harus YYYY-MM-DD") from None
    if value.weekday() != 0:
        raise ValueError("--as-of harus hari Senin (minggu akurasi = Senin WIB)")
    if value >= today:
        raise ValueError("--as-of harus di masa lalu")
    if (today - value).days > MAX_BACK_DAYS:
        raise ValueError(f"--as-of maksimal {MAX_BACK_DAYS} hari ke belakang")
    return value


def as_of_now(as_of: date) -> datetime:
    """Waktu batch = Minggu (as_of − 1) 23:00 WIB, dalam UTC."""
    return datetime.combine(as_of - timedelta(days=1), GENERATED_AT_WIB, WIB).astimezone(UTC)


def as_of_params(params: Params) -> Params:
    """Histori sampai hari batch (Minggu) -> proyeksi mulai Senin; tanpa seed kalender (tidak ada RPC)."""
    return replace(params, include_today=True, seed_calendar=False)


def as_of_run_id(company: str, as_of: date, kind: str = "daily") -> str:
    """UUID berformat v4 (lolos UUID4_RE kontrak) yang deterministik per (PT, tanggal, kind)."""
    digest = hashlib.sha256(f"efn-forecast-asof|{company}|{as_of.isoformat()}|{kind}".encode()).digest()
    return str(uuid.UUID(bytes=digest[:16], version=4))


class RecordingOdoo:
    """Pengganti klien Odoo untuk mode as-of: merekam payload yang SUDAH divalidasi run_batch, tanpa jaringan."""

    def __init__(self) -> None:
        self.run_open_payload: dict[str, Any] | None = None
        self.ingest_payloads: list[dict[str, Any]] = []
        self.run_close_payload: dict[str, Any] | None = None

    def calendar_upsert(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("as-of tidak menulis kalender")

    def run_open(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.run_open_payload = payload
        return {"ok": True, "run_id": payload["run_id"], "state": "open", "is_active": False, "created": True}

    def ingest(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.ingest_payloads.append(payload)
        return {"ok": True, "run_id": payload["run_id"], "chunk_no": payload["chunk_no"], "rejected": []}

    def minmax_ingest(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("as-of hanya kind daily")

    def run_close(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.run_close_payload = payload
        return {"ok": True, "run_id": payload["run_id"], "state": payload["status"]}


def export_document(company: str, as_of: date, rec: RecordingOdoo) -> dict[str, Any]:
    """Dokumen ekspor dari rekaman run `ok`. Gagal bila run tidak lengkap/ditutup failed."""
    if rec.run_open_payload is None or rec.run_close_payload is None or not rec.ingest_payloads:
        raise ContractError("asof_incomplete")
    if rec.run_close_payload.get("status") != "ok":
        raise ContractError("asof_not_ok")
    if rec.run_open_payload.get("kind") != "daily":
        raise ContractError("asof_kind")
    if rec.run_open_payload.get("chunk_total") != len(rec.ingest_payloads):
        raise ContractError("asof_chunks")
    return {
        "format": EXPORT_FORMAT,
        "company_code": company,
        "as_of": as_of.isoformat(),
        "run_open": rec.run_open_payload,
        "chunks": rec.ingest_payloads,
        "run_close": rec.run_close_payload,
    }


def validate_document(doc: Any) -> dict[str, Any]:
    """Validasi struktur (dipakai test; loader Odoo memvalidasi ulang setiap baris dengan validator Odoo)."""
    if not isinstance(doc, dict) or doc.get("format") != EXPORT_FORMAT:
        raise ContractError("asof_format")
    run_open, run_close, chunks = doc.get("run_open"), doc.get("run_close"), doc.get("chunks")
    if not isinstance(run_open, dict) or set(run_open) != RUN_OPEN_KEYS:
        raise ContractError("asof_run_open")
    if not isinstance(run_close, dict) or set(run_close) != RUN_CLOSE_KEYS or run_close.get("status") != "ok":
        raise ContractError("asof_run_close")
    if not isinstance(chunks, list) or not chunks:
        raise ContractError("asof_chunks")
    if any(not isinstance(c, dict) or set(c) != INGEST_KEYS for c in chunks):
        raise ContractError("asof_chunks")
    run_id = run_open["run_id"]
    if not UUID4_RE.fullmatch(str(run_id)) or run_close["run_id"] != run_id:
        raise ContractError("asof_run_id")
    if any(c["run_id"] != run_id for c in chunks):
        raise ContractError("asof_run_id")
    if run_open["company_code"] != doc.get("company_code") or run_open["kind"] != "daily":
        raise ContractError("asof_company")
    as_of = date.fromisoformat(str(doc.get("as_of")))
    if run_open["generated_at"] != as_of_now(as_of).strftime("%Y-%m-%dT%H:%M:%SZ"):
        raise ContractError("asof_generated_at")
    return doc


def write_document(path: Path, doc: dict[str, Any]) -> None:
    """SEC-G51-04: hanya file .json baru (mode "x": tolak bila sudah ada, tidak menimpa)."""
    if path.suffix != ".json":
        raise ContractError("asof_out_suffix")
    with path.open("x", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
