"""Gelombang 44 (ADR-046 Proposed §3.4/§3.6, docs/04 §28.1/§28.2, R-T21): mart forecast supermarket.

Arah data (L-145): forecast-worker menulis hasil ke Odoo; ETL ini menurunkannya ke ClickHouse (worker tidak pernah
menulis ClickHouse). Job incremental (cursor `write_date, id`, pola efn_etl/waste.py) -> ReplacingMergeTree:

- `fc_warehouses` : `stock_warehouse` (+ kode PT)        -> `dim_fc_warehouse` (kode gudang = warehouse_code §28.2)
- `fc_calendar`   : `efn_forecast_calendar`              -> `dim_calendar`     (entri lib + manual, docs/04 §28.3)
- `fc_runs`       : `efn_forecast_run`                   -> `dim_forecast_run` (status run; akurasi hanya run `ok`)
- `fc_daily`      : `efn_forecast_daily`                 -> `fact_forecast_daily`

Lalu (harian) `fact_forecast_accuracy` = MAPE/WAPE/bias per SKU x gudang x minggu (Senin, WIB) dari forecast run `ok`
TERAKHIR yang dibuat sebelum tanggalnya (lead >= 1 hari) vs penjualan aktual (`v_fc_sales_daily`). Logika murni di
`accuracy_rows` / `accuracy_weeks` / `accuracy_due`; I/O di `ClickHouseForecastStore`.

Skema Odoo v2 (`efn_demand_forecast/models/efn_forecast_{run,daily,calendar}.py`, G44, dicocokkan dari source 18 Nov
2026, belum dijalankan ke DB): `efn_forecast_run` (`run_id` Char = UUID worker, `company_id`, `kind`, `model_version`,
`state` open|ok|failed, `generated_at`), `efn_forecast_daily` (`run_id` many2one, `company_id`, `warehouse_id`,
`product_id`, `date`, `yhat`, `p10`, `p90`, `sigma`, `model`), `efn_forecast_calendar` (`company_id`, `warehouse_id`,
`date`, `kind`, `name` Char, `source`, `active`). Kolom mart `dim_forecast_run.run_uuid` = `efn_forecast_run.run_id`;
`dim_forecast_run.run_id` = id baris Odoo (= `efn_forecast_daily.run_id`).

PDP: tidak ada partner, member, user pelaku, atau lot. Hanya id teknis, kode, tanggal, angka statistik.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from efn_etl.entities import normalize_code
from efn_etl.jobs import Job
from efn_etl.stock import JAKARTA
from efn_etl.watermark import to_utc

_MAX_LABEL = 128
Q3 = Decimal("0.001")
CALENDAR_KINDS = frozenset({"weekend", "payday", "holiday", "cuti_bersama", "ramadan", "lebaran_window", "custom"})
CALENDAR_SOURCES = frozenset({"lib", "manual"})
FORECAST_MODELS = frozenset({"AutoETS", "AutoARIMA", "CrostonOptimized", "ADIDA", "category_mean"})
ACCURACY_JOB = "forecast_accuracy"


def _label(value: Any) -> str:
    if isinstance(value, dict):  # field terjemahan (jsonb) -> id_ID lalu en_US
        value = value.get("id_ID") or value.get("en_US") or next(iter(value.values()), "")
    return ("" if value is None else str(value).strip())[:_MAX_LABEL]


def _dec(value: Any) -> Decimal:
    number = Decimal(str(value if value is not None else 0))
    if not number.is_finite() or number < 0:
        raise ValueError("nilai forecast harus angka >= 0")
    return number.quantize(Q3)


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return to_utc(value).astimezone(JAKARTA).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


# ---------------------------------------------------------------- stock_warehouse -> dim_fc_warehouse
SQL_FC_WAREHOUSES = """
SELECT w.id AS warehouse_id, COALESCE(w.code, '') AS code, COALESCE(w.name, '') AS name, w.company_id,
       COALESCE(c.efn_brand_code, '') AS company_code, COALESCE(w.active, FALSE) AS active, w.write_date,
       w.write_date AS _cursor_ts, w.id::text AS _cursor_key
FROM stock_warehouse w
JOIN res_company c ON c.id = w.company_id
WHERE w.write_date >= %(cursor_ts)s
  AND (w.write_date, w.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY w.write_date, w.id::text
LIMIT %(limit)s
"""
FC_WAREHOUSE_COLUMNS = ("warehouse_id", "code", "name", "company_id", "company_code", "active", "write_date")


def transform_fc_warehouse(row: dict[str, Any]) -> list[Any]:
    # Kode PT hanya dinormalisasi (bukan divalidasi): gudang PT lain dengan kode baru (mis. IMD/CUP G43) tidak boleh
    # membatalkan halaman; worker memfilter company_code sendiri. Kode gudang disalin PERSIS (tanpa strip/upper):
    # worker mengirimnya kembali sebagai warehouse_code dan Odoo mencocokkannya apa adanya.
    return [
        int(row["warehouse_id"]),
        str(row.get("code") or ""),
        _label(row.get("name")),
        int(row["company_id"]),
        normalize_code(row.get("company_code")),
        1 if row.get("active") else 0,
        to_utc(row["write_date"]),
    ]


# ---------------------------------------------------------------- efn_forecast_calendar -> dim_calendar
SQL_FC_CALENDAR = """
SELECT k.id AS calendar_id, k.company_id, COALESCE(k.warehouse_id, 0) AS warehouse_id, k.date, k.kind,
       k.name, k.source, COALESCE(k.active, FALSE) AS active, k.write_date,
       k.write_date AS _cursor_ts, k.id::text AS _cursor_key
FROM efn_forecast_calendar k
WHERE k.write_date >= %(cursor_ts)s
  AND (k.write_date, k.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY k.write_date, k.id::text
LIMIT %(limit)s
"""
CALENDAR_COLUMNS = (
    "calendar_id",
    "company_id",
    "warehouse_id",
    "date",
    "kind",
    "name",
    "source",
    "active",
    "write_date",
)


def transform_calendar(row: dict[str, Any]) -> list[Any]:
    kind = str(row.get("kind") or "").strip()
    source = str(row.get("source") or "").strip()
    if kind not in CALENDAR_KINDS or source not in CALENDAR_SOURCES:
        # Fail closed: kind/source di luar kontrak §28.3 = skema Odoo berubah -> halaman tidak dimuat (heartbeat error).
        raise ValueError("efn_forecast_calendar: kind/source di luar kontrak")
    return [
        int(row["calendar_id"]),
        int(row["company_id"]),
        int(row.get("warehouse_id") or 0),
        _date(row["date"]),
        kind,
        _label(row.get("name"))[:64],
        source,
        1 if row.get("active") else 0,
        to_utc(row["write_date"]),
    ]


# ---------------------------------------------------------------- efn_forecast_run -> dim_forecast_run
SQL_FC_RUNS = """
SELECT r.id AS run_id, COALESCE(r.run_id, '') AS run_uuid, r.company_id, COALESCE(r.kind, '') AS kind,
       COALESCE(r.model_version, '') AS model_version, COALESCE(r.state, '') AS state, r.generated_at, r.write_date,
       r.write_date AS _cursor_ts, r.id::text AS _cursor_key
FROM efn_forecast_run r
WHERE r.write_date >= %(cursor_ts)s
  AND (r.write_date, r.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY r.write_date, r.id::text
LIMIT %(limit)s
"""
RUN_COLUMNS = (
    "run_id",
    "run_uuid",
    "company_id",
    "kind",
    "model_version",
    "state",
    "generated_at",
    "write_date",
)


def transform_run(row: dict[str, Any]) -> list[Any]:
    generated = row.get("generated_at")
    return [
        int(row["run_id"]),
        str(row.get("run_uuid") or "")[:36],
        int(row["company_id"]),
        str(row.get("kind") or "")[:32],
        str(row.get("model_version") or "")[:32],
        str(row.get("state") or "")[:16],
        to_utc(generated) if isinstance(generated, datetime) else to_utc(row["write_date"]),
        to_utc(row["write_date"]),
    ]


# ---------------------------------------------------------------- efn_forecast_daily -> fact_forecast_daily
SQL_FC_DAILY = """
SELECT d.id AS line_id, d.run_id, d.company_id, d.warehouse_id, d.product_id, d.date AS forecast_date,
       d.yhat, d.p10, d.p90, d.sigma, COALESCE(d.model, '') AS model, d.write_date,
       d.write_date AS _cursor_ts, d.id::text AS _cursor_key
FROM efn_forecast_daily d
WHERE d.write_date >= %(cursor_ts)s
  AND (d.write_date, d.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY d.write_date, d.id::text
LIMIT %(limit)s
"""
DAILY_COLUMNS = (
    "line_id",
    "run_id",
    "company_id",
    "warehouse_id",
    "product_id",
    "forecast_date",
    "yhat",
    "p10",
    "p90",
    "sigma",
    "model",
    "write_date",
)


def transform_daily(row: dict[str, Any]) -> list[Any]:
    model = str(row.get("model") or "")
    if model not in FORECAST_MODELS:
        raise ValueError("efn_forecast_daily.model di luar kontrak §28.2")
    yhat, p10, p90 = _dec(row.get("yhat")), _dec(row.get("p10")), _dec(row.get("p90"))
    if not p10 <= yhat <= p90:
        raise ValueError("efn_forecast_daily: p10 <= yhat <= p90 dilanggar")
    return [
        int(row["line_id"]),
        int(row["run_id"]),
        int(row["company_id"]),
        int(row["warehouse_id"]),
        int(row["product_id"]),
        _date(row["forecast_date"]),
        yhat,
        p10,
        p90,
        _dec(row.get("sigma")),
        model,
        to_utc(row["write_date"]),
    ]


# ---------------------------------------------------------------- efn_legacy_sales_daily -> fact_legacy_sales_daily
# G46 (L-165, docs/04 §28.2a): histori penjualan ESB sebelum cutover (G43, ADR-042) sebagai input forecast-worker lewat
# v_fc_sales_daily. Pemetaan menu -> product.product dan batas cutover sudah ditegakkan Odoo; ETL hanya menyalin kolom
# untuk forecast (tanpa nama menu/cabang, tanpa rupiah, tanpa run_id). Bulk SQL G43 mengisi write_date -> cursor biasa.
SQL_FC_LEGACY_SALES = """
SELECT l.id, l.company_id, l.warehouse_id, l.day, l.esb_menu_code, l.product_id, l.qty, l.write_date,
       l.write_date AS _cursor_ts, l.id::text AS _cursor_key
FROM efn_legacy_sales_daily l
WHERE l.write_date >= %(cursor_ts)s
  AND (l.write_date, l.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY l.write_date, l.id::text
LIMIT %(limit)s
"""
LEGACY_SALES_COLUMNS = ("company_id", "warehouse_id", "day", "esb_menu_code", "product_id", "qty", "write_date")
_MAX_MENU_CODE = 64  # = size Char efn_legacy_sales_daily.esb_menu_code


def transform_legacy_sales(row: dict[str, Any]) -> list[Any]:
    code = str(row.get("esb_menu_code") or "").strip()
    if not code or len(code) > _MAX_MENU_CODE:
        raise ValueError("efn_legacy_sales_daily.esb_menu_code kosong/terlalu panjang")
    qty = Decimal(str(row.get("qty") if row.get("qty") is not None else 0))
    if not qty.is_finite():
        raise ValueError("efn_legacy_sales_daily.qty bukan angka")
    # Qty negatif (koreksi/retur di ESB) disalin apa adanya; view v_fc_sales_daily hanya memakai qty > 0.
    return [
        int(row["company_id"]),
        int(row["warehouse_id"]),
        _date(row["day"]),
        code,
        int(row.get("product_id") or 0),
        qty.quantize(Q3),
        to_utc(row["write_date"]),
    ]


def legacy_sales_jobs() -> tuple[Job, ...]:
    """Job incremental G46 (`ETL_ENABLE_FORECAST_LEGACY`, butuh `efn_legacy_history` + grant G46)."""
    return (
        Job(
            "fc_legacy_sales",
            "odoo",
            SQL_FC_LEGACY_SALES,
            "fact_legacy_sales_daily",
            LEGACY_SALES_COLUMNS,
            transform_legacy_sales,
        ),
    )


def forecast_jobs() -> tuple[Job, ...]:
    """Job incremental G44 (`ETL_ENABLE_FORECAST`, butuh model v2 efn_demand_forecast + grant G44)."""
    return (
        Job(
            "fc_warehouses", "odoo", SQL_FC_WAREHOUSES, "dim_fc_warehouse", FC_WAREHOUSE_COLUMNS, transform_fc_warehouse
        ),
        Job("fc_calendar", "odoo", SQL_FC_CALENDAR, "dim_calendar", CALENDAR_COLUMNS, transform_calendar),
        Job("fc_runs", "odoo", SQL_FC_RUNS, "dim_forecast_run", RUN_COLUMNS, transform_run),
        Job("fc_daily", "odoo", SQL_FC_DAILY, "fact_forecast_daily", DAILY_COLUMNS, transform_daily),
    )


# ---------------------------------------------------------------- fact_forecast_accuracy (logika murni)
ACCURACY_COLUMNS = (
    "company_id",
    "warehouse_id",
    "product_id",
    "week_start",
    "days",
    "days_with_sales",
    "forecast_qty",
    "actual_qty",
    "abs_error",
    "ape_sum",
    "wape",
    "mape",
    "bias",
    "computed_at",
)


def week_start(day: date) -> date:
    """Senin minggu itu (ISO)."""
    return day - timedelta(days=day.weekday())


def business_date(now: datetime) -> date:
    return to_utc(now).astimezone(JAKARTA).date()


def accuracy_weeks(now: datetime, lookback_weeks: int) -> tuple[date, date]:
    """Rentang [awal, akhir) minggu yang SUDAH selesai: `lookback_weeks` minggu sebelum minggu berjalan (WIB).

    Dihitung ulang setiap run (penjualan terlambat/koreksi dalam beberapa hari) -> idempoten lewat versi computed_at.
    """
    if lookback_weeks < 1:
        raise ValueError("lookback_weeks >= 1")
    end = week_start(business_date(now))
    return end - timedelta(days=7 * lookback_weeks), end


def accuracy_due(last_computed: datetime | None, now: datetime, interval: timedelta) -> bool:
    """Sekali per hari bisnis (WIB) atau setelah `interval`."""
    if last_computed is None:
        return True
    if business_date(last_computed) != business_date(now):
        return True
    return to_utc(now) - to_utc(last_computed) >= interval


@dataclass
class _Acc:
    days: int = 0
    days_with_sales: int = 0
    forecast: float = 0.0
    actual: float = 0.0
    abs_error: float = 0.0
    ape_sum: float = 0.0


def accuracy_rows(
    forecasts: Iterable[tuple[int, int, int, date, float]],
    actuals: Mapping[tuple[int, int, date], float],
    computed_at: datetime,
) -> list[list[Any]]:
    """Agregasi per (company, gudang, produk, minggu) hanya pada hari yang PUNYA forecast.

    `forecasts`: (company_id, warehouse_id, product_id, tanggal, yhat); `actuals`: (warehouse_id, product_id, tanggal)
    -> qty terjual (tanpa entri = 0). WAPE = Σ|F−A|/ΣA, bias = Σ(F−A)/ΣA (None bila ΣA = 0), MAPE = rata-rata |F−A|/A
    hanya hari dengan A > 0 (None bila tidak ada).
    """
    groups: dict[tuple[int, int, int, date], _Acc] = defaultdict(_Acc)
    seen: set[tuple[int, int, date]] = set()
    for company_id, warehouse_id, product_id, day, yhat in forecasts:
        key_day = (int(warehouse_id), int(product_id), day)
        if key_day in seen:  # satu forecast per hari (dedup defensif)
            continue
        seen.add(key_day)
        f = max(float(yhat), 0.0)
        a = max(float(actuals.get(key_day, 0.0)), 0.0)
        acc = groups[(int(company_id), int(warehouse_id), int(product_id), week_start(day))]
        acc.days += 1
        acc.forecast += f
        acc.actual += a
        acc.abs_error += abs(f - a)
        if a > 0:
            acc.days_with_sales += 1
            acc.ape_sum += abs(f - a) / a
    ts = to_utc(computed_at)
    out: list[list[Any]] = []
    for (company_id, warehouse_id, product_id, week), acc in sorted(groups.items()):
        wape = acc.abs_error / acc.actual if acc.actual > 0 else None
        bias = (acc.forecast - acc.actual) / acc.actual if acc.actual > 0 else None
        mape = acc.ape_sum / acc.days_with_sales if acc.days_with_sales else None
        out.append(
            [
                company_id,
                warehouse_id,
                product_id,
                week,
                acc.days,
                acc.days_with_sales,
                Decimal(str(acc.forecast)).quantize(Q3),
                Decimal(str(acc.actual)).quantize(Q3),
                Decimal(str(acc.abs_error)).quantize(Q3),
                round(acc.ape_sum, 6),
                None if wape is None else round(wape, 6),
                None if mape is None else round(mape, 6),
                None if bias is None else round(bias, 6),
                ts,
            ]
        )
    return out


# ---------------------------------------------------------------- fact_forecast_accuracy (I/O)
SQL_LAST_ACCURACY = "SELECT max(computed_at), count() FROM fact_forecast_accuracy"
# Forecast run `ok` (kind daily) TERAKHIR yang dibuat sebelum tanggalnya (tanggal WIB generated_at < forecast_date).
SQL_ACCURACY_FORECASTS = """
SELECT f.company_id, f.warehouse_id, f.product_id, f.forecast_date, toFloat64(argMax(f.yhat, r.generated_at)) AS yhat
FROM
(
    SELECT run_id, company_id, warehouse_id, product_id, forecast_date, yhat
    FROM fact_forecast_daily FINAL
    WHERE forecast_date >= {start:Date} AND forecast_date < {end:Date}
) AS f
INNER JOIN
(
    SELECT run_id, generated_at FROM dim_forecast_run FINAL WHERE state = 'ok' AND kind = 'daily'
) AS r ON r.run_id = f.run_id
WHERE toDate(r.generated_at, 'Asia/Jakarta') < f.forecast_date
GROUP BY f.company_id, f.warehouse_id, f.product_id, f.forecast_date
"""
SQL_ACCURACY_ACTUALS = """
SELECT warehouse_id, product_id, day, qty
FROM v_fc_sales_daily
WHERE day >= {start:Date} AND day < {end:Date}
"""


class QueryClient(Protocol):
    def query(self, sql: str, parameters: dict[str, Any] | None = None) -> Any: ...


class Sink(Protocol):
    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None: ...


class ClickHouseForecastStore:
    """Baca mart forecast + aktual di ClickHouse (user efn_etl). Semua query berparameter."""

    def __init__(self, client: QueryClient) -> None:
        self._client = client

    def last_accuracy_at(self) -> datetime | None:
        rows = self._client.query(SQL_LAST_ACCURACY).result_rows
        if not rows or not rows[0][1]:
            return None
        return to_utc(rows[0][0])

    def forecasts(self, start: date, end: date) -> list[tuple[int, int, int, date, float]]:
        rows = self._client.query(SQL_ACCURACY_FORECASTS, parameters={"start": start, "end": end}).result_rows
        return [(int(c), int(w), int(p), _date(d), float(y)) for c, w, p, d, y in rows]

    def actuals(self, start: date, end: date) -> dict[tuple[int, int, date], float]:
        rows = self._client.query(SQL_ACCURACY_ACTUALS, parameters={"start": start, "end": end}).result_rows
        out: dict[tuple[int, int, date], float] = {}
        for w, p, d, q in rows:
            key = (int(w), int(p), _date(d))
            out[key] = out.get(key, 0.0) + float(q or 0)
        return out


@dataclass(frozen=True)
class AccuracyResult:
    start: date
    end: date
    rows: int


def run_forecast_accuracy(
    store: ClickHouseForecastStore, sink: Sink, now: datetime, lookback_weeks: int
) -> AccuracyResult:
    start, end = accuracy_weeks(now, lookback_weeks)
    rows = accuracy_rows(store.forecasts(start, end), store.actuals(start, end), now)
    sink.insert("fact_forecast_accuracy", ACCURACY_COLUMNS, rows)
    return AccuracyResult(start=start, end=end, rows=len(rows))
