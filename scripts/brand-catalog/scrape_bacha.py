#!/usr/bin/env python
"""Scraper katalog butik Bacha Coffee (situs global https://bachacoffee.com) -> data/brand-catalog/bacha/.

Sumber: endpoint JSON yang dipakai halaman listing situs sendiri (``bacha-product-listing.min.js``):
``POST <siteurl>/api/BachaProductApi/GetBachaProductsByCategory`` dengan body
``{category, pagenumber, pagesize, lang, cur, locale, store}``. ``siteurl`` + ``store`` dibaca dari atribut
``<html siteurl=".." store="..">`` halaman listing publik (bukan nilai tertanam), sehingga skrip ikut berubah bila
situs berubah. robots.txt: ``Crawl-delay: 5`` -> jeda minimum 5 s antar request (juga untuk gambar di
media-brand.bachacoffee.com yang robots.txt-nya 403 -> dianggap tanpa aturan, jeda tetap 5 s).

Batas penting:
- Harga situs = USD (toko global), BUKAN harga Indonesia -> ``price_idr`` null; USD disimpan di ``extra`` hanya
  sebagai referensi, tidak diimpor.
- Ini katalog BUTIK (biji kopi, drip bag, aksesori, gourmet, gift set) -> PT Bacha Coffee Retail (``BCR``).
  Menu kafe (minuman, pastry) tidak dipublikasikan sebagai katalog di situs -> tidak ada data ``BCF``.
- Satu SKU per varian (mis. Ground Beans vs Whole Beans) = satu produk Odoo (sederhana, cocok dengan POS butik).

Pakai:  python scripts/brand-catalog/scrape_bacha.py [--categories loose-coffee,packed-coffee,...] [--limit N]
"""

from __future__ import annotations

import argparse
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

BASE = "https://bachacoffee.com"
LISTING_PAGE = f"{BASE}/en/coffee/shop-all-single-serve-drip-coffee-bags?category=packed-coffee"
BRAND = "bacha"
COMPANY_CODE = "BCR"
PAGE_SIZE = 100
MAX_PAGES = 10
# Nilai `Category`/`Definition` dari bundle JS situs (7048: LOOSE_COFFEE .. GIFT_SET). Hamper = konfigurator, dilewati.
CATEGORIES = {
    "loose-coffee": "Coffee / Loose Coffee",
    "packed-coffee": "Coffee / Packed Coffee",
    "accessories": "Accessories",
    "gourmet": "Gourmet",
    "giftset": "Gift Sets",
}
HTML_ATTR = re.compile(r'<html\b[^>]*>', re.I)


def site_context(fetcher: Fetcher) -> tuple[str, str, str]:
    page = fetcher.get_text(LISTING_PAGE)
    tag = HTML_ATTR.search(page)
    attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', tag.group(0))) if tag else {}
    siteurl = attrs.get("siteurl") or f"{BASE}/en"
    if not siteurl.startswith(BASE):
        raise SystemExit(f"siteurl tak terduga: {siteurl}")
    return siteurl, attrs.get("store", ""), attrs.get("cur", "usd")


def category_label(p: dict, cat: str) -> str:
    base = CATEGORIES[cat]
    sub = p.get("CoffeeCategory") if cat in ("loose-coffee", "packed-coffee") else None
    if cat == "packed-coffee" and "coffee bags" in (p.get("Name") or "").lower():
        sub = "Coffee Bags"
    return f"{base} / {sub}" if sub else base


def main() -> int:
    configure_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--categories", default=",".join(CATEGORIES))
    ap.add_argument("--delay", type=float, default=5.0, help="jeda minimum (detik); robots Crawl-delay tetap menang")
    ap.add_argument("--limit", type=int, default=0, help="batasi produk per kategori (uji)")
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()

    fetcher = Fetcher(min_delay=max(args.delay, 5.0))
    out_dir = brand_dir(BRAND)
    siteurl, store, cur = site_context(fetcher)
    api = f"{siteurl.rstrip('/')}/api/BachaProductApi/GetBachaProductsByCategory"
    log(f"API {api} (store={'ada' if store else 'kosong'}, cur={cur})")

    products, seen, images_ok = [], set(), 0
    for cat in [c.strip() for c in args.categories.split(",") if c.strip()]:
        if cat not in CATEGORIES:
            log(f"kategori tidak dikenal, lewati: {cat}")
            continue
        # Paging API tidak konsisten (total/countLeft bisa meleset dan halaman lewat-batas mengulang item
        # terakhir) -> lanjut selama halaman membawa SKU baru, maksimal MAX_PAGES.
        page, items, cat_skus, data = 1, [], set(), {}
        while page <= MAX_PAGES:
            body = {"category": cat, "pagenumber": str(page), "pagesize": str(PAGE_SIZE), "lang": "en",
                    "cur": cur, "locale": "", "store": store}
            data = fetcher.post_json(api, body)
            if data.get("message") != "OK":
                log(f"  {cat}: {data.get('message')}")
                break
            fresh = [p for p in data.get("products") or [] if (p.get("Sku") or p.get("SKU")) not in cat_skus]
            if not fresh:
                break
            cat_skus.update(p.get("Sku") or p.get("SKU") for p in fresh)
            items.extend(fresh)
            page += 1
        log(f"kategori {cat}: {len(items)} produk (total API {data.get('total')})")
        if args.limit:
            items = items[: args.limit]
        for p in items:
            sku = (p.get("Sku") or p.get("SKU") or "").strip()
            if not sku or sku in seen:
                continue
            seen.add(sku)
            name = clean_title(p.get("Name") or p.get("BachaName"))
            slug = slugify(f"{name}-{sku}")
            image_file = None
            imgs = [u for u in (p.get("ProductImages") or []) if u and u.startswith("https://")]
            if not args.no_images and imgs:
                image_file = download_image(fetcher, imgs[0], out_dir / "images", slug)
            images_ok += bool(image_file)
            desc = html_to_text(p.get("Description") or p.get("ProductDesciption") or p.get("CoffeeCategoryDesciption"))
            weight = p.get("WeightVolumeOrUnits") or None
            products.append(
                product_record(
                    source_id=sku,
                    slug=slug,
                    name=name,
                    description_text=desc,
                    category=category_label(p, cat),
                    image_file=image_file,
                    source_url=p.get("Url"),
                    company_code=COMPANY_CODE,
                    extra={
                        # Kategori Super-App/POS (pos.category) = kategori situs tingkat-1 (Loose Coffee, Gourmet, ...).
                        "online_category": CATEGORIES[cat].split(" / ")[-1],
                        "group_name": p.get("GroupName"),
                        "collection": p.get("Collection"),
                        "weight_volume": weight,
                        "site_category": cat,
                        "coffee_category": p.get("CoffeeCategory"),
                        "country": p.get("Country"),
                        "roast": p.get("Roast"),
                        "reference_price_usd_global": p.get("Price"),
                    },
                )
            )
            log(f"  {sku} {name} img={'ya' if image_file else '-'}")

    path = write_catalog(BRAND, BASE, products, extra={
        "company_code": COMPANY_CODE,
        "note": "Situs global (USD). Harga Indonesia & ketersediaan butik ID wajib dari principal/master data.",
    })
    log(f"selesai: {len(products)} produk, {images_ok} gambar, {fetcher.requests_made} request -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
