"""Cakupan PT untuk row policy `efn_t2s_scope` (docs/04 §23.3). Logika murni.

Service mengirim setting query ClickHouse:
  SQL_efn_cids  = company_id terurut, dipisah koma
  SQL_efn_codes = kode PT terurut, dipisah koma
  SQL_efn_sig   = hex(SHA256(scope_key | cids | codes))
Row policy menghitung ulang tanda tangan dengan kunci yang sama (dibuat clickhouse-init.sh). Query yang mengganti
setting di subquery (guard sudah menolak SETTINGS) membuat tanda tangan tidak cocok -> 0 baris.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

COMPANY_CODES = frozenset({"EFN", "GL", "PB", "BCR", "BCF", "WTZ", "FARM"})
MAX_COMPANIES = 32
_CODE_RE = re.compile(r"^[A-Z]{2,8}$")


class ScopeError(ValueError):
    pass


@dataclass(frozen=True)
class Scope:
    company_ids: tuple[int, ...]
    company_codes: tuple[str, ...]

    @classmethod
    def build(cls, company_ids: object, company_codes: object) -> Scope:
        if not isinstance(company_ids, list) or not 1 <= len(company_ids) <= MAX_COMPANIES:
            raise ScopeError("company_ids harus daftar 1..32")
        if not all(isinstance(i, int) and not isinstance(i, bool) and 0 < i < 2**31 for i in company_ids):
            raise ScopeError("company_ids harus bilangan bulat positif")
        if not isinstance(company_codes, list) or len(company_codes) > MAX_COMPANIES:
            raise ScopeError("company_codes harus daftar 0..32")
        if not all(isinstance(c, str) and _CODE_RE.match(c) and c in COMPANY_CODES for c in company_codes):
            raise ScopeError("company_codes tidak dikenal")
        return cls(tuple(sorted(set(company_ids))), tuple(sorted(set(company_codes))))

    def settings(self, scope_key: str) -> dict[str, str]:
        cids = ",".join(str(i) for i in self.company_ids)
        codes = ",".join(self.company_codes)
        sig = hashlib.sha256(f"{scope_key}|{cids}|{codes}".encode()).hexdigest()
        return {"SQL_efn_cids": cids, "SQL_efn_codes": codes, "SQL_efn_sig": sig}
