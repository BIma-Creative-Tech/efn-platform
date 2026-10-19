"""Gelombang 21 (ADR-036 §0.7, docs/04 §13.2, E-38b): kode wilayah kab/kota domisili pembeli/member untuk laporan
penjualan per wilayah. Logika murni + definisi job; tanpa I/O.

Sumber (DB Odoo, dibuat `sql/odoo-readonly-grants.sql` bagian G21, pemilik `efn_odoo`):
- view `efn_etl.partner_region(partner_id, company_id, region_code)` — satu-satunya pengecualian K-36-5 (ETL tanpa grant
  `res_partner`). Hanya membaca `res_partner.id`, `res_partner.company_id`, `res_partner.efn_region_id` (plaintext
  menurut ADR-036 §0.2 tabel field) dan `efn_region.code`. TIDAK membaca nama, alamat, kota, kode pos, email, HP, NPWP,
  NIK, blind index, maupun token nama (semua terenkripsi/terlindungi ADR-036).
  Dipakai sebagai LEFT JOIN di job `sales_fact_pos` -> kolom `fact_sales_line.customer_region_code` (wilayah domisili
  saat baris dimuat; tidak ada tabel partner -> wilayah di ClickHouse, minimisasi data).
- view `efn_etl.region(region_code, name, state_code, state_name, active, write_date)` — master `efn.region` (data
  Kemendagri, bukan data pribadi) -> `dim_region`.

Kebocoran yang diterima (ADR-036 §0.7, Q-PII10): kab/kota domisili (±500 nilai) per baris penjualan ber-member.
`customer_id` di baris yang sama tetap dikosongkan setelah `CH_PSEUDONYM_RETENTION_YEARS` (TTL kolom + AgeOut).
"""

import re
from typing import TYPE_CHECKING, Any

from efn_etl.watermark import to_utc

if TYPE_CHECKING:
    from efn_etl.jobs import Job

# Kode kab/kota Kemendagri `PP.KK` (mis. 31.71 = Kota Jakarta Selatan). Granularitas lebih halus (kecamatan
# `PP.KK.CC`) sengaja DITOLAK: menambah kebocoran domisili (Q-PII10).
REGION_CODE_RE = re.compile(r"^\d{2}\.\d{2}$")
STATE_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{0,16}$")
_MAX_NAME = 128


def region_code(value: Any) -> str:
    """Kode wilayah untuk baris fakta. Kosong/tidak valid -> '' (baris tetap dimuat tanpa wilayah).

    Lenient di fakta: kode rusak di master tidak boleh menahan seluruh mart penjualan. Nilai yang tidak berbentuk kode
    (mis. teks bebas yang salah masuk) TIDAK pernah disalin.
    """
    text = "" if value is None else str(value).strip()
    return text if REGION_CODE_RE.fullmatch(text) else ""


def strict_region_code(value: Any) -> str:
    """Kode wilayah untuk `dim_region` (master). Tidak valid -> ValueError (halaman batal, heartbeat error)."""
    text = "" if value is None else str(value).strip()
    if not REGION_CODE_RE.fullmatch(text):
        raise ValueError("efn_region.code bukan kode kab/kota Kemendagri PP.KK")
    return text


def _label(value: Any) -> str:
    return ("" if value is None else str(value).strip())[:_MAX_NAME]


def _state_code(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return text if STATE_CODE_RE.fullmatch(text) else ""


# ---------------------------------------------------------------- efn_etl.region -> dim_region
SQL_REGIONS = """
SELECT r.region_code, r.name, r.state_code, r.state_name, r.active, r.write_date,
       r.write_date AS _cursor_ts, r.region_code AS _cursor_key
FROM efn_etl.region r
WHERE r.write_date >= %(cursor_ts)s
  AND (r.write_date, r.region_code) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY r.write_date, r.region_code
LIMIT %(limit)s
"""
REGION_COLUMNS = ("region_code", "name", "state_code", "state_name", "active", "write_date")


def transform_region(row: dict[str, Any]) -> list[Any]:
    return [
        strict_region_code(row["region_code"]),
        _label(row["name"]),
        _state_code(row.get("state_code")),
        _label(row.get("state_name")),
        1 if row.get("active", True) else 0,
        to_utc(row["write_date"]),
    ]


def regions_job() -> "Job":
    # Import lokal: jobs.py mengimpor `region_code` dari modul ini.
    from efn_etl.jobs import Job

    return Job("regions", "odoo", SQL_REGIONS, "dim_region", REGION_COLUMNS, transform_region)


# ---------------------------------------------------------------- join wilayah di SQL_SALES_FACT (jobs.py)
# Aktif hanya bila ETL_PARTNER_REGION_ENABLED=true (view ada setelah efn_region terpasang + grant G21). Mati = kolom
# konstan '' tanpa menyentuh skema efn_etl (job penjualan tidak gagal sebelum efn_region dipasang).
REGION_SELECT_ON = "pr.region_code AS customer_region_code"
REGION_SELECT_OFF = "''::text AS customer_region_code"
REGION_JOIN_ON = "LEFT JOIN efn_etl.partner_region pr ON pr.partner_id = o.partner_id"
REGION_JOIN_OFF = ""
