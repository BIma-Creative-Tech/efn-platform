"""Entitas grup EFN (G20/W-32, docs/04 §12.1, ADR-001 revisi 18 Okt 2026): kode PT, jenis usaha, grup brand, dan
klasifikasi relasi antar-entitas untuk eliminasi konsolidasi GL.

Logika murni (tanpa I/O). Rumus `ic_relation` dicerminkan di view `v_gl_consolidated` / `v_gl_ic_recon`
(infra/clickhouse/init/08-g20-entities.sql); test memeriksa keduanya tetap selaras.

Kode lama `RTL`/`FNB` dihapus (tidak ada migrasi data: DB dev dibuat ulang, §12.1).
"""

from typing import Final

# Kode `res.company.efn_brand_code` (= `company_code` ledger BFF).
# G24 (docs/04 §16.1, L-91): + WTZ (PT Wetzel's Pretzels, fnb).
COMPANY_CODES: Final = frozenset({"EFN", "GL", "PB", "BCR", "BCF", "WTZ", "FARM"})
HOLDING_CODE: Final = "EFN"

# docs/04 §12.1/§16.1: GL,BCR -> retail; PB,BCF,WTZ -> fnb; EFN -> holding; FARM -> farm.
BUSINESS_TYPES: Final[dict[str, str]] = {
    "EFN": "holding",
    "GL": "retail",
    "PB": "fnb",
    "BCR": "retail",
    "BCF": "fnb",
    "WTZ": "fnb",
    "FARM": "farm",
}

# Laporan per brand: Bacha = gabungan BCR + BCF (ADR-001 revisi §6; brand_code kanal E-37a `BACHA`).
BRAND_GROUPS: Final[dict[str, str]] = {"BCR": "BACHA", "BCF": "BACHA"}

# Pasangan transfer pricing cost-plus (18-3, W-15): hanya BCR <-> BCF dan Holding <-> setiap PT (G24: WTZ <-> PT lain
# = non_tp, brand_group WTZ sendiri).
BACHA_TP_PAIR: Final = frozenset({"BCR", "BCF"})

# Nilai `ic_relation` (view konsolidasi). Semua relasi antar-entitas TETAP dieliminasi saat konsolidasi; label hanya
# membedakan dasar harga untuk rekonsiliasi: TP cost-plus vs jual-beli harga daftar/pasar (Q-AD1-1, belum diputuskan).
IC_NONE: Final = ""
IC_TP_HOLDING: Final = "tp_holding"
IC_TP_BACHA: Final = "tp_bacha"
IC_NON_TP: Final = "non_tp"
IC_UNKNOWN: Final = "unknown"
IC_RELATIONS: Final = (IC_NONE, IC_TP_HOLDING, IC_TP_BACHA, IC_NON_TP, IC_UNKNOWN)


def normalize_code(value: object) -> str:
    return str(value or "").strip().upper()


def company_code(value: object) -> str:
    """Kode PT wajib dikenal (fail closed): kode lama `RTL`/`FNB` atau salah ketik ditolak."""
    code = normalize_code(value)
    if code not in COMPANY_CODES:
        raise ValueError("company_code tidak dikenal")
    return code


def business_type(code: object) -> str:
    """Jenis usaha dari kode PT. Company tanpa kode (mis. company bawaan Odoo) -> '' (tidak diklasifikasi)."""
    normalized = normalize_code(code)
    if not normalized:
        return ""
    return BUSINESS_TYPES[company_code(normalized)]


def brand_group(code: object) -> str:
    """Grup brand laporan: BCR/BCF -> BACHA; kode lain = kodenya sendiri; tanpa kode -> ''."""
    normalized = normalize_code(code)
    if not normalized:
        return ""
    return BRAND_GROUPS.get(company_code(normalized), normalized)


def ic_relation(code: object, counterparty_code: object | None) -> str:
    """Relasi baris GL dengan company lawan (sama dengan multiIf di view; `None` = bukan antar-entitas).

    - lawan kosong/None -> '' (bukan inter-company);
    - salah satu kode tidak dikenal -> 'unknown' (tetap dieliminasi; tanda konfigurasi company belum lengkap);
    - Holding di salah satu sisi -> 'tp_holding';
    - BCR <-> BCF -> 'tp_bacha';
    - pasangan lain (mis. PB membeli dari GL, FARM -> PT) -> 'non_tp' (Q-AD1-1).
    """
    if counterparty_code is None:
        return IC_NONE
    own, other = normalize_code(code), normalize_code(counterparty_code)
    if own not in COMPANY_CODES or other not in COMPANY_CODES:
        return IC_UNKNOWN
    if own == other:
        # Dua company dengan kode sama = data salah (kode unik per PT).
        return IC_UNKNOWN
    if HOLDING_CODE in (own, other):
        return IC_TP_HOLDING
    if {own, other} == BACHA_TP_PAIR:
        return IC_TP_BACHA
    return IC_NON_TP
