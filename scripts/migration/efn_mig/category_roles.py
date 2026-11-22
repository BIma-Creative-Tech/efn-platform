"""G61: kategori produk ESB -> pohon kategori Odoo per peran akuntansi. Murni, stdlib saja.

Sumber tunggal = `category_roles.csv` di folder ini (docs/fit-gap/G61_KATEGORI_PRODUK_PB.md, spesifikasi
docs/fit-gap/G54_G55_G61_ODOO_SETUP.md §G61). Dipakai `mapping.build_categories`/`build_products` (export bundel) dan
`scripts/migration/recategorize_pb.py` (odoo shell, efn-prod). Ambiguitas A1-A11 = kolom `note`; ubah barisnya, bukan
kode. Kategori ESB yang tidak ada di tabel = error (fail closed).
"""

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

CSV_PATH = Path(__file__).with_name("category_roles.csv")
SEP = " / "
COLUMNS = (
    "esb_category", "target_path", "role", "type", "is_storable", "sale_ok", "purchase_ok", "available_in_pos", "note",
)  # fmt: skip
# Peran -> (akar path, type, is_storable). Peran "menu" tidak ada di CSV (produk menu POS, recategorize_pb.py).
ROLES = {
    "persediaan": ("Persediaan", "consu", True),
    "perlengkapan": ("Perlengkapan", "consu", False),
    "jasa": ("Beban / Jasa", "service", False),
    "aset_tetap": ("Aset Tetap", "consu", False),
}
PURCHASE_RULES = frozenset({"true", "false", "supplierinfo"})
POS_RULES = frozenset({"false", "keep"})
MENU_ROOT = "Menu"
MENU_OTHER = "Menu / Lainnya"


class CategoryRoleError(ValueError):
    """Tabel peran tidak valid atau kategori ESB tidak terpetakan."""


@dataclass(frozen=True)
class CategoryRole:
    esb_category: str
    target_path: str
    role: str
    type: str
    is_storable: bool
    sale_ok: bool
    purchase_ok: str  # true | false | supplierinfo (A2: True bila ada product.supplierinfo / PO ESB)
    available_in_pos: str  # false | keep
    note: str

    def purchase(self, has_supplier: bool, is_fin: bool = False) -> bool:
        """purchase_ok produk. FIN (`is_fin`) selalu False (diproduksi CK)."""
        if is_fin:
            return False
        if self.purchase_ok == "supplierinfo":
            return has_supplier
        return self.purchase_ok == "true"


def key(name: object) -> str:
    """Kunci pencocokan nama kategori ESB (spasi dirapikan, tanpa beda huruf besar/kecil)."""
    return re.sub(r"\s+", " ", str(name or "").strip()).casefold()


def path_chain(path: str) -> list[str]:
    """`A / B / C` -> [`A`, `A / B`, `A / B / C`] (induk dulu)."""
    parts = path.split(SEP)
    return [SEP.join(parts[: i + 1]) for i in range(len(parts))]


def _bool(value: str, line_no: int, column: str) -> bool:
    if value not in ("true", "false"):
        raise CategoryRoleError(f"category_roles.csv baris {line_no}: {column} wajib true/false")
    return value == "true"


def parse_roles(text: str) -> dict[str, CategoryRole]:
    """CSV -> {key(esb_category): CategoryRole}. Validasi konsistensi peran (type/is_storable/akar path)."""
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise CategoryRoleError(f"category_roles.csv: kolom wajib persis {', '.join(COLUMNS)}")
    out: dict[str, CategoryRole] = {}
    for line_no, raw in enumerate(reader, start=2):
        row = {k: (v or "").strip() for k, v in raw.items()}
        name, path, role = row["esb_category"], row["target_path"], row["role"]
        if not name or key(name) in out:
            raise CategoryRoleError(f"category_roles.csv baris {line_no}: esb_category kosong atau ganda")
        if role not in ROLES:
            raise CategoryRoleError(f"category_roles.csv baris {line_no}: role wajib salah satu {sorted(ROLES)}")
        root, ptype, storable = ROLES[role]
        parts = path.split(SEP)
        if not path.startswith(root + SEP) or any(not p or p != p.strip() for p in parts):
            raise CategoryRoleError(f"category_roles.csv baris {line_no}: target_path wajib di bawah '{root}'")
        if row["type"] != ptype or _bool(row["is_storable"], line_no, "is_storable") != storable:
            raise CategoryRoleError(
                f"category_roles.csv baris {line_no}: role {role} wajib type={ptype} "
                f"is_storable={str(storable).lower()}"
            )
        if row["purchase_ok"] not in PURCHASE_RULES or row["available_in_pos"] not in POS_RULES:
            raise CategoryRoleError(
                f"category_roles.csv baris {line_no}: purchase_ok {sorted(PURCHASE_RULES)}, "
                f"available_in_pos {sorted(POS_RULES)}"
            )
        out[key(name)] = CategoryRole(
            esb_category=name,
            target_path=path,
            role=role,
            type=ptype,
            is_storable=storable,
            sale_ok=_bool(row["sale_ok"], line_no, "sale_ok"),
            purchase_ok=row["purchase_ok"],
            available_in_pos=row["available_in_pos"],
            note=row["note"],
        )
    if not out:
        raise CategoryRoleError("category_roles.csv kosong")
    return out


def load_roles(path: Path = CSV_PATH) -> dict[str, CategoryRole]:
    return parse_roles(path.read_text(encoding="utf-8-sig"))


def role_for(roles: dict[str, CategoryRole], esb_category: object) -> CategoryRole:
    """Peran kategori ESB; kosong / tidak ada di tabel -> CategoryRoleError (fail closed)."""
    found = roles.get(key(esb_category))
    if found is None:
        shown = str(esb_category or "").strip()[:80] or "(kosong)"
        raise CategoryRoleError(
            f"kategori ESB tidak terpetakan: {shown!r} -> tambahkan baris di "
            "scripts/migration/efn_mig/category_roles.csv"
        )
    return found
