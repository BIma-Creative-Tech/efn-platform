"""Terbilang (nominal dalam kata, Bahasa Indonesia) untuk Payment Voucher/Receipt.

Port apa adanya dari odoo-platform `_tenants/custom_levis_localization/models/terbilang.py` (ADR-049, fungsi murni).
Python murni agar tidak bergantung pada locale num2words/babel di sandbox QWeb (id_ID biasanya tidak aktif sehingga
`amount_to_text` jatuh ke bahasa Inggris). Pecahan dibulatkan (voucher IDR = rupiah penuh).
"""

_SATUAN = [
    "",
    "satu",
    "dua",
    "tiga",
    "empat",
    "lima",
    "enam",
    "tujuh",
    "delapan",
    "sembilan",
    "sepuluh",
    "sebelas",
]


def _spell(x):
    if x < 12:
        return _SATUAN[x]
    if x < 20:
        return _spell(x - 10) + " belas"
    if x < 100:
        return _spell(x // 10) + " puluh" + ((" " + _spell(x % 10)) if x % 10 else "")
    if x < 200:
        return "seratus" + ((" " + _spell(x - 100)) if x - 100 else "")
    if x < 1000:
        return _spell(x // 100) + " ratus" + ((" " + _spell(x % 100)) if x % 100 else "")
    if x < 2000:
        return "seribu" + ((" " + _spell(x - 1000)) if x - 1000 else "")
    if x < 1_000_000:
        return _spell(x // 1000) + " ribu" + ((" " + _spell(x % 1000)) if x % 1000 else "")
    if x < 1_000_000_000:
        return _spell(x // 1_000_000) + " juta" + ((" " + _spell(x % 1_000_000)) if x % 1_000_000 else "")
    if x < 1_000_000_000_000:
        rest = x % 1_000_000_000
        return _spell(x // 1_000_000_000) + " miliar" + ((" " + _spell(rest)) if rest else "")
    rest = x % 1_000_000_000_000
    return _spell(x // 1_000_000_000_000) + " triliun" + ((" " + _spell(rest)) if rest else "")


def terbilang_id(amount, suffix="Rupiah"):
    """``270045`` -> ``"Dua Ratus Tujuh Puluh Ribu Empat Puluh Lima Rupiah"`` (Title Case)."""
    n = int(round(abs(amount or 0.0)))
    words = "nol" if n == 0 else " ".join(_spell(n).split())
    text = words.title()
    return f"{text} {suffix}" if suffix else text
