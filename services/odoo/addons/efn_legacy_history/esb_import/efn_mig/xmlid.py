"""External ID migrasi (docs/04 §27.4, L-128). Murni, stdlib saja (dipakai export DAN importer Odoo).

`name = <brand>_<company>_<entity>_<key>` di modul `efn_mig_esb`; `company` = kode PT huruf kecil.
`key` dari kode ESB asli `c`: `n = re.sub(r"[^a-z0-9_]", "_", c.lower())[:48]`; bila `c` tidak cocok
`^[A-Z0-9_]{1,48}$` -> `key = n + "_" + sha1(c)[:8]`, selain itu `key = n`.
"""

import hashlib
import re

MODULE = "efn_mig_esb"
_CANONICAL = re.compile(r"^[A-Z0-9_]{1,48}$")
_UNSAFE = re.compile(r"[^a-z0-9_]")
_BRAND = re.compile(r"^[a-z0-9]{2,16}$")
_COMPANY = re.compile(r"^[A-Z0-9]{2,8}$")
ENTITIES = frozenset(
    {
        "branch_wh", "branch_loc", "branch_pos", "category", "pos_category", "uom", "product", "menu", "combo",
        "bom", "supplier", "customer", "payment_term", "supplierinfo",
    }
)  # fmt: skip


def xmlid_key(code: object) -> str:
    """Kunci deterministik dari kode ESB asli (§27.4). Kode kosong -> ValueError."""
    c = "" if code is None else str(code)
    if not c:
        raise ValueError("kode ESB kosong tidak bisa dijadikan xmlid")
    normalized = _UNSAFE.sub("_", c.lower())[:48]
    if _CANONICAL.fullmatch(c):
        return normalized
    digest = hashlib.sha1(c.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"{normalized}_{digest}"


def xmlid_name(brand: str, company_code: str, entity: str, code: object) -> str:
    if not _BRAND.fullmatch(brand):
        raise ValueError("brand tidak valid")
    if not _COMPANY.fullmatch(company_code):
        raise ValueError("company_code tidak valid")
    if entity not in ENTITIES:
        raise ValueError(f"entitas xmlid tidak dikenal: {entity}")
    return f"{brand}_{company_code.lower()}_{entity}_{xmlid_key(code)}"


def xmlid(brand: str, company_code: str, entity: str, code: object) -> str:
    """xmlid lengkap `efn_mig_esb.<name>`."""
    return f"{MODULE}.{xmlid_name(brand, company_code, entity, code)}"
