#!/usr/bin/env python
"""Scraper katalog resmi Paris Baguette Indonesia (https://parisbaguette.co.id) -> data/brand-catalog/parisbaguette/.

Sumber: WordPress REST API publik (``/wp-json/wp/v2/product`` + ``products_category`` + media). robots.txt
mengizinkan semua; skrip tetap menjeda >= 1 s antar request. Situs TIDAK mencantumkan harga -> ``price_idr`` null.
Perusahaan: PT Paris Baguette (kode PT ``PB``, F&B PB1). Pemakaian konten: izin brand owner via Erajaya (README).

Pakai:  python scripts/brand-catalog/scrape_parisbaguette.py [--delay 1.0] [--limit N] [--no-images]
"""

from __future__ import annotations

import argparse
import html
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
    nice_category,
    product_record,
    slugify,
    write_catalog,
)

BASE = "https://parisbaguette.co.id"
API = f"{BASE}/wp-json/wp/v2"
BRAND = "parisbaguette"
COMPANY_CODE = "PB"
# Kategori musiman/promosi: dipakai hanya bila produk tidak punya kategori menu lain.
SEASONAL = {"summer splash"}


def fetch_all(fetcher: Fetcher, path: str, fields: str) -> list[dict]:
    items, page = [], 1
    while True:
        batch = fetcher.get_json(f"{API}/{path}?per_page=100&page={page}&_fields={fields}")
        if not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def pick_image(media: dict) -> str | None:
    """Ukuran asli bila <= 1600 px, selain itu 'large'/'1536x1536' (import akan resize ke maks 1024 px)."""
    if not media:
        return None
    details = media.get("media_details") or {}
    sizes = details.get("sizes") or {}
    width = details.get("width") or 0
    if width and width <= 1600:
        return media.get("source_url")
    for key in ("1536x1536", "large", "full"):
        if key in sizes:
            return sizes[key].get("source_url")
    return media.get("source_url")


OG_IMAGE = re.compile(r'<meta\s+property="og:image"\s+content="([^"]+)"', re.I)


def og_image(fetcher: Fetcher, page_url: str) -> str | None:
    try:
        match = OG_IMAGE.search(fetcher.get_text(page_url))
    except Exception as exc:  # noqa: BLE001
        log(f"  halaman {page_url} gagal: {exc}")
        return None
    url = html.unescape(match.group(1)) if match else None
    return url if url and url.startswith(BASE) else None


def main() -> int:
    configure_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--delay", type=float, default=1.0, help="jeda minimum antar request (detik)")
    ap.add_argument("--limit", type=int, default=0, help="batasi jumlah produk (uji)")
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()

    fetcher = Fetcher(min_delay=args.delay)
    out_dir = brand_dir(BRAND)
    categories = {c["id"]: nice_category(c["name"]) for c in fetch_all(fetcher, "products_category", "id,name")}
    log(f"kategori: {len(categories)}")
    raw = fetch_all(fetcher, "product", "id,slug,title,content,featured_media,products_category,link")
    log(f"produk API: {len(raw)}")
    if args.limit:
        raw = raw[: args.limit]

    products, images_ok = [], 0
    for item in raw:
        slug = slugify(item.get("slug") or clean_title(item["title"]["rendered"]))
        cats = [categories.get(cid) for cid in item.get("products_category") or [] if categories.get(cid)]
        main_cats = [c for c in cats if c.lower() not in SEASONAL]
        category = (main_cats or cats or [None])[0]
        image_file = None
        if not args.no_images and item.get("featured_media"):
            existing = sorted((out_dir / "images").glob(f"{slug}.*"))
            if existing:
                image_file = f"images/{existing[0].name}"
            else:
                try:
                    media = fetcher.get_json(f"{API}/media/{item['featured_media']}?_fields=id,source_url,media_details")
                except Exception as exc:  # noqa: BLE001 - satu media gagal tidak menghentikan katalog
                    log(f"  media {item['featured_media']} gagal: {exc}")
                    media = None
                url = pick_image(media) if media else None
                if not url and item.get("link"):
                    # Sebagian attachment tidak publik di REST (401) -> og:image halaman produk publik.
                    url = og_image(fetcher, item["link"])
                if url:
                    image_file = download_image(fetcher, url, out_dir / "images", slug)
        images_ok += bool(image_file)
        products.append(
            product_record(
                source_id=item["id"],
                slug=slug,
                name=clean_title(item["title"]["rendered"]),
                description_text=html_to_text((item.get("content") or {}).get("rendered")),
                category=category,
                image_file=image_file,
                source_url=item.get("link"),
                company_code=COMPANY_CODE,
                extra={"all_categories": cats},
            )
        )
        log(f"  {item['id']} {products[-1]['name']} [{category}] img={'ya' if image_file else '-'}")

    path = write_catalog(BRAND, BASE, products, extra={"company_code": COMPANY_CODE})
    log(f"selesai: {len(products)} produk, {images_ok} gambar, {fetcher.requests_made} request -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
