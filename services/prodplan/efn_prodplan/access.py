"""Pemetaan izin ESB -> peran prodplan + akses dokumen + cabang (docs/04 §30.17.6, ADR-051 §8 amandemen A1). Murni.

Sumber data saat login (token pengguna itu sendiri):
- `GET /user/access` -> `result` = list grup `{accessID, accessName, accessDetails:[{accessID: "B.4", viewAccess,
  insertAccess, updateAccess, deleteAccess, authorizeAccess, ...}]}`. Kode seperti `B.4` ada di
  `accessDetails[].accessID` (uji staging lead 25 Nov 2026). Parser toleran: grup tanpa `accessDetails` yang sendirinya
  berbentuk detail ikut dibaca.
- `GET /branch/list/user` -> `result: {page, pageSize, dataCount, data:[{branchID, branchCode, branchName}]}`.

Tata bahasa aturan (env non-rahasia): `KODE:hak[+hak];KODE:hak...`, `hak` ∈ {view, insert, update, delete}; semua
klausa wajib terpenuhi (AND). `authorize` sengaja TIDAK diterima. Hak terpenuhi bila field `<hak>Access` bernilai
boolean `true` persis (bukan 1/"true").
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any

from efn_prodplan.config import ConfigError

RIGHTS = ("view", "insert", "update", "delete")
DOC_KINDS = ("simple_transfer", "goods_transfer_request", "simple_manufacturing")
PLANNER = "pp_planner"
WAREHOUSE = "pp_warehouse"
DEFAULT_PLANNER_RULES = "B.4:view+insert;K.5:view;D.16:view;C.1:view"
DEFAULT_WAREHOUSE_RULES = "D.9:view+insert;D.4:view+insert;D.16:view"
DEFAULT_DOC_ACCESS = "simple_transfer=D.9;goods_transfer_request=D.4;simple_manufacturing=B.4"
MAX_BRANCHES = 200
_CODE_RE = re.compile(r"^[A-Za-z0-9.]{1,16}$")

Rule = tuple[str, frozenset[str]]


def _rule_error(env_name: str) -> ConfigError:
    return ConfigError("esb_role_rule", f"{env_name}: aturan izin ESB tidak valid")


def parse_rules(text: str, env_name: str) -> tuple[Rule, ...]:
    """`B.4:view+insert;K.5:view` -> ((B.4, {view, insert}), (K.5, {view})). Kosong/tak sah -> ConfigError."""
    rules: list[Rule] = []
    for raw in text.split(";"):
        item = raw.strip()
        if not item:
            continue
        code, sep, rights_text = item.partition(":")
        code = code.strip()
        rights = [r.strip() for r in rights_text.split("+")]
        if not sep or not _CODE_RE.fullmatch(code) or not rights or any(r not in RIGHTS for r in rights):
            raise _rule_error(env_name)
        rules.append((code, frozenset(rights)))
    if not rules:
        raise _rule_error(env_name)  # aturan kosong = semua orang lolos -> ditolak
    return tuple(rules)


def parse_doc_access(text: str, env_name: str = "PRODPLAN_ESB_DOC_ACCESS") -> dict[str, str]:
    """`simple_transfer=D.9;...` -> {doc_kind: kode}. Ketiga doc_kind wajib ada, masing-masing sekali."""
    out: dict[str, str] = {}
    for raw in text.split(";"):
        item = raw.strip()
        if not item:
            continue
        kind, sep, code = (part.strip() for part in item.partition("="))
        if not sep or kind not in DOC_KINDS or kind in out or not _CODE_RE.fullmatch(code):
            raise _rule_error(env_name)
        out[kind] = code
    if set(out) != set(DOC_KINDS):
        raise _rule_error(env_name)
    return out


def _is_detail(item: Mapping[str, Any]) -> bool:
    return "accessID" in item and any(f"{r}Access" in item for r in RIGHTS)


def access_items(result: Any) -> list[dict[str, Any]]:
    """Ratakan `result` `/user/access` menjadi daftar item detail (toleran terhadap bentuk)."""
    groups = result if isinstance(result, list) else []
    items: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        details = group.get("accessDetails")
        if isinstance(details, list):
            items.extend(d for d in details if isinstance(d, dict))
        elif _is_detail(group):
            items.append(group)
    return items


def granted(items: Iterable[Mapping[str, Any]], code: str, right: str) -> bool:
    for item in items:
        value = item.get("accessID")
        if isinstance(value, str) and value.strip() == code and item.get(f"{right}Access") is True:
            return True
    return False


def rules_satisfied(items: list[dict[str, Any]], rules: Iterable[Rule]) -> bool:
    return all(granted(items, code, right) for code, rights in rules for right in rights)


def compute_roles(
    items: list[dict[str, Any]], planner_rules: Iterable[Rule], warehouse_rules: Iterable[Rule]
) -> frozenset[str]:
    roles = set()
    if rules_satisfied(items, planner_rules):
        roles.add(PLANNER)
    if rules_satisfied(items, warehouse_rules):
        roles.add(WAREHOUSE)
    return frozenset(roles)


def doc_access(items: list[dict[str, Any]], doc_map: Mapping[str, str]) -> tuple[frozenset[str], frozenset[str]]:
    """(doc_insert, doc_delete): doc_kind yang kodenya punya `insertAccess` / `deleteAccess` true."""
    insert = frozenset(kind for kind, code in doc_map.items() if granted(items, code, "insert"))
    delete = frozenset(kind for kind, code in doc_map.items() if granted(items, code, "delete"))
    return insert, delete


def _pos_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def parse_branch_page(result: Any) -> tuple[dict[int, str], int | None]:
    """Satu halaman `/branch/list/user` -> ({branchID: nama}, dataCount|None). Nama kosong -> kode cabang -> ""."""
    if isinstance(result, list):
        rows: Any = result
        total = None
    elif isinstance(result, Mapping):
        rows = result.get("data") or []
        raw_total = result.get("dataCount", result.get("count"))
        total = raw_total if isinstance(raw_total, int) and not isinstance(raw_total, bool) else None
    else:
        return {}, None
    out: dict[int, str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        branch_id = _pos_int(row.get("branchID"))
        if branch_id <= 0:
            continue
        name = row.get("branchName") or row.get("branchCode") or ""
        out[branch_id] = str(name).strip()[:120]
    return out, total
