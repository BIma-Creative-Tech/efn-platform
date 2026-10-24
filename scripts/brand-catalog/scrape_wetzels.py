#!/usr/bin/env python
"""Scraper menu resmi Wetzel's Pretzels (https://www.wetzels.com) -> data/brand-catalog/wetzels/.

Sumber: ``/sitemap.xml`` (daftar halaman menu ``/menu/<kategori>/<item>``) + JSON-LD ``MenuItem`` tiap halaman
(nama, deskripsi, gambar ``thumb.png``). robots.txt mengizinkan ``/menu``; skrip menjeda >= 1 s antar request.
Situs global (AS) TIDAK memuat harga IDR -> ``price_idr`` null (import memakai placeholder DEMO per kategori).
Perusahaan: PT Wetzel's Pretzels (kode PT ``WTZ``, F&B PB1; docs/04 §16). Pemakaian konten: hak eksklusif brand
Indonesia dipegang Erajaya (README).

Pakai:  python scripts/brand-catalog/scrape_wetzels.py [--delay 1.0] [--limit N] [--no-images]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from catalog_common import (  # noqa: E402
    Fetcher,
    brand_dir,
    clean_title,
    configure_stdout,
    download_image,
    html_to_text,
    log,
    product_record,
    slugify,
    write_catalog,
)

BASE = "https://www.wetzels.com"
BRAND = "wetzels"
COMPANY_CODE = "WTZ"
# Slug kategori situs -> kategori katalog EFN (nama Odoo/POS, bahasa Inggris seperti brand lain).
CATEGORIES = {
    "pretzels": "Pretzels",
    "bitz": "Bitz",
    "dogs": "Dogs",
    "stickz": "Stickz",
    "catering": "Catering",
    "drinks": "Drinks",
    "dips": "Dips",
}
MENU_URL = re.compile(r"<loc>(https://www\.wetzels\.com/menu/([a-z]+)/([a-z0-9-]+))</loc>")
LD_JSON = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
SERVES = re.compile(r"\bServes\s+(\d+)\b", re.I)


def page_image(page: str, cat: str, item: str) -> str | None:
    """Foto produk yang dipakai halaman (``/assets/menu/<kat>/<item>/thumb.png``).

    JSON-LD ``image`` situs menunjuk ``/menu/.../thumb.png`` tanpa ``/assets`` (404, dicek 24 Okt 2026), jadi yang
    dipakai adalah ``<img src>`` halaman; tidak ada -> pola path ``/assets`` yang sama.
    """
    match = re.search(rf'src="(/assets/menu/{cat}/{item}/[^"]+\.(?:png|jpe?g|webp))"', page)
    return f"{BASE}{match.group(1)}" if match else f"{BASE}/assets/menu/{cat}/{item}/thumb.png"


def menu_item(page: str) -> dict | None:
    """Blok JSON-LD ``@type: MenuItem`` halaman item, atau None."""
    for raw in LD_JSON.findall(page):
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in doc if isinstance(doc, list) else [doc]:
            if isinstance(node, dict) and node.get("@type") == "MenuItem":
                return node
    return None


def main() -> int:
    configure_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--delay", type=float, default=1.0, help="jeda minimum antar request (detik)")
    ap.add_argument("--limit", type=int, default=0, help="batasi jumlah produk (uji)")
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()

    fetcher = Fetcher(min_delay=args.delay)
    out_dir = brand_dir(BRAND)
    urls = []
    for url, cat, item in MENU_URL.findall(fetcher.get_text(f"{BASE}/sitemap.xml")):
        if cat in CATEGORIES and (url, cat, item) not in urls:
            urls.append((url, cat, item))
    log(f"halaman menu di sitemap: {len(urls)}")
    if args.limit:
        urls = urls[: args.limit]

    products, images_ok = [], 0
    for url, cat, item in urls:
        try:
            page = fetcher.get_text(url)
        except Exception as exc:  # noqa: BLE001 - satu halaman gagal tidak menghentikan katalog
            log(f"  {url} gagal: {exc}")
            continue
        node = menu_item(page)
        if not node:
            log(f"  {url}: tanpa JSON-LD MenuItem -> dilewati")
            continue
        name = clean_title(html.unescape(node.get("name") or item))
        desc = html_to_text(html.unescape(node.get("description") or ""))
        slug = slugify(f"{cat}-{item}")
        image_file = None
        img = page_image(page, cat, item) or node.get("image")
        if not args.no_images and isinstance(img, str) and img.startswith(BASE):
            existing = sorted((out_dir / "images").glob(f"{slug}.*"))
            image_file = f"images/{existing[0].name}" if existing else download_image(fetcher, img, out_dir / "images", slug)
        images_ok += bool(image_file)
        serves = SERVES.search(desc)
        products.append(
            product_record(
                source_id=f"{cat}-{item}",
                slug=slug,
                name=name,
                description_text=desc,
                category=CATEGORIES[cat],
                image_file=image_file,
                source_url=url,
                company_code=COMPANY_CODE,
                extra={"catering": cat == "catering", **({"serves": int(serves.group(1))} if serves else {})},
            )
        )
        log(f"  {cat}/{item}: {name} img={'ya' if image_file else '-'}")

    path = write_catalog(BRAND, BASE, products, extra={"company_code": COMPANY_CODE})
    log(f"selesai: {len(products)} produk, {images_ok} gambar, {fetcher.requests_made} request -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
