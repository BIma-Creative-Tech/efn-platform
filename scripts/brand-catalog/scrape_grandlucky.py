#!/usr/bin/env python
"""GrandLucky (https://www.grandlucky.co.id): info brand + outlet, dan konversi katalog CSV internal.

Situs GrandLucky TIDAK punya katalog produk publik (hanya halaman about/membership/tenants/location dan PDF
promosi musiman). Skrip ini TIDAK mengarang produk:

1. Mode default (scrape): ambil profil brand (about) dan daftar outlet (location-contact) ->
   ``data/brand-catalog/grandlucky/brand_info.json`` + ``products.json`` dengan ``products: []``.
   Kontak outlet = nomor/email bisnis publik (bukan data pribadi).
2. Mode ``--from-csv <file>``: katalog GL dari master data internal (ekspor POS/ERP lama) memakai template
   ``scripts/brand-catalog/templates/grandlucky_products_template.csv`` -> ``products.json`` skema seragam
   (gambar disalin dari ``--images-dir``), siap untuk ``import_odoo.py --brand grandlucky``.

Pakai:
  python scripts/brand-catalog/scrape_grandlucky.py
  python scripts/brand-catalog/scrape_grandlucky.py --from-csv ekspor_gl.csv --images-dir foto_gl/
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from catalog_common import (  # noqa: E402
    Fetcher,
    brand_dir,
    configure_stdout,
    html_to_text,
    log,
    now_iso,
    product_record,
    slugify,
    write_catalog,
)

BASE = "https://www.grandlucky.co.id"
BRAND = "grandlucky"
COMPANY_CODE = "GL"
CSV_COLUMNS = ["source_id", "name", "category", "description_text", "price_idr", "reference_price_idr", "weight_kg",
               "barcode", "image_file"]
OUTLET_RE = re.compile(
    r"<a class='accordion-toggle'[^>]*>\s*(?P<name>.*?)\s*</a>.*?<div class='panel-body'>(?P<body>.*?)(?:<script|</div>)",
    re.S,
)
REGION_RE = re.compile(r'<h4 class="wpb_toggle[^"]*">(.*?)</h4>', re.S)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_outlets(page: str) -> list[dict]:
    """Outlet per region (accordion WordPress). Region = heading toggle terdekat sebelum outlet."""
    regions = [(m.start(), _text(m.group(1)).replace("(click to view)", "").strip()) for m in REGION_RE.finditer(page)]
    outlets = []
    for m in OUTLET_RE.finditer(page):
        body = _text(m.group("body"))
        region = next((name for pos, name in reversed(regions) if pos < m.start()), None)
        address = re.split(r"\s+Email\s*:", body)[0].strip()
        email = re.search(r"Email\s*:\s*(\S+@\S+)", body)
        hotline = re.search(r"HOT-LINE\s*:\s*([0-9 ()+-]{6,})", body)
        outlets.append({
            "name": re.sub(r"(?i)^grandlucky\b", "GrandLucky", _text(m.group("name"))),
            "region": region,
            "address": address,
            "email": email.group(1) if email else None,
            "hotline": hotline.group(1).strip() if hotline else None,
        })
    return outlets


def scrape(args) -> int:
    fetcher = Fetcher(min_delay=args.delay)
    about = fetcher.get_text(f"{BASE}/about/")
    location = fetcher.get_text(f"{BASE}/location-contact/")
    home = fetcher.get_text(f"{BASE}/")
    body = re.search(r"ABOUT GRAND LUCKY(.*?)(?:CUSTOMERS|TESTIMONIALS)", _text(about), re.S)
    catalog_pdf = re.search(r'href="(https?://doc\.grandlucky\.co\.id/[^"]+\.pdf)"', home)
    info = {
        "brand": BRAND,
        "company_code": COMPANY_CODE,
        "source_url": BASE,
        "scraped_at": now_iso(),
        "description_text": html_to_text(body.group(1)) if body else None,
        "outlets": parse_outlets(location),
        "promo_catalog_pdf": catalog_pdf.group(1) if catalog_pdf else None,
        "product_catalog": None,
        "note": "Situs tidak memuat katalog produk/harga. Katalog GL wajib dari master data internal "
        "(ekspor POS/ERP lama) lewat --from-csv; PDF promosi musiman bukan master data.",
    }
    out = brand_dir(BRAND)
    (out / "brand_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    if not (out / "products.json").exists():
        write_catalog(BRAND, BASE, [], extra={"company_code": COMPANY_CODE, "note": info["note"]})
    log(f"brand_info: {len(info['outlets'])} outlet, deskripsi {'ada' if info['description_text'] else '-'}, "
        f"{fetcher.requests_made} request -> {out / 'brand_info.json'}")
    return 0


def _int_or_none(value: str | None) -> int | None:
    """'89.900' / '89900' -> 89900; kosong/bukan angka -> None."""
    digits = (value or "").replace(".", "").replace(",", "").strip()
    return int(digits) if digits.isdigit() else None


def from_csv(args) -> int:
    src = Path(args.from_csv)
    images_src = Path(args.images_dir) if args.images_dir else src.parent
    out = brand_dir(BRAND)
    products, errors = [], []
    with src.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = {"source_id", "name"} - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"kolom wajib tidak ada: {sorted(missing)} (lihat templates/grandlucky_products_template.csv)")
        seen = set()
        for lineno, row in enumerate(reader, start=2):
            sid = (row.get("source_id") or "").strip()
            name = (row.get("name") or "").strip()
            if not sid or not name:
                errors.append(f"baris {lineno}: source_id/name kosong")
                continue
            if sid in seen:
                errors.append(f"baris {lineno}: source_id duplikat {sid}")
                continue
            seen.add(sid)
            price = (row.get("price_idr") or "").replace(".", "").replace(",", "").strip()
            weight = (row.get("weight_kg") or "").replace(",", ".").strip()
            slug = slugify(f"{name}-{sid}")
            image_file = None
            if (row.get("image_file") or "").strip():
                img = images_src / row["image_file"].strip()
                if img.is_file():
                    target = out / "images" / f"{slug}{img.suffix.lower()}"
                    if not target.exists():
                        shutil.copyfile(img, target)
                    image_file = f"images/{target.name}"
                else:
                    errors.append(f"baris {lineno}: gambar tidak ditemukan {img}")
            rec = product_record(
                source_id=sid, slug=slug, name=name,
                description_text=html_to_text(row.get("description_text")),
                category=(row.get("category") or "").strip() or None,
                image_file=image_file, source_url=None, company_code=COMPANY_CODE,
                extra={
                    "weight_kg": float(weight) if weight else None,
                    "barcode": (row.get("barcode") or "").strip() or None,
                    # Opsional: harga referensi (mis. katalog promo PDF) -> harga DEMO, tetap bertag "DEMO harga".
                    "reference_price_idr": _int_or_none(row.get("reference_price_idr")),
                },
            )
            rec["price_idr"] = int(price) if price.isdigit() else None
            products.append(rec)
    path = write_catalog(BRAND, f"csv:{src.name}", products, extra={"company_code": COMPANY_CODE})
    for err in errors:
        log(f"  PERINGATAN {err}")
    log(f"CSV: {len(products)} produk, {sum(bool(p['image_file']) for p in products)} gambar -> {path}")
    return 1 if errors and args.strict else 0


def main() -> int:
    configure_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--from-csv", help="CSV katalog internal GL (template di templates/)")
    ap.add_argument("--images-dir", help="folder gambar untuk kolom image_file (default: folder CSV)")
    ap.add_argument("--strict", action="store_true", help="exit 1 bila ada baris CSV bermasalah")
    args = ap.parse_args()
    return from_csv(args) if args.from_csv else scrape(args)


if __name__ == "__main__":
    sys.exit(main())
