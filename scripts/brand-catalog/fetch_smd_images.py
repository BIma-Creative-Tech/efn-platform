#!/usr/bin/env python3
"""Foto produk DEMO supermarket SMD dari sumber berlisensi bebas (CC0 / Public Domain Mark) -> JPEG lokal + manifest.

Permintaan user 23 Nov 2026: "gambar free realistis" untuk situs supermarket. Produk SMD = katalog generik fiktif
(scripts/dev/seed_demo_supermarket.py CATALOG), jadi foto = foto bahan/produk generik yang cocok dengan nama (bukan
merek). Sumber (urutan):
  0. Pexels API bila env PEXELS_API_KEY ada (pilihan user 23 Nov 2026; lisensi Pexels: komersial tanpa atribusi).
  0b. Pixabay API bila env PIXABAY_API_KEY ada (Pexels API dijeda; Content License Pixabay: komersial tanpa atribusi).
  1. Openverse API (api.openverse.org, WordPress Foundation) filter `license=cc0,pdm` — anonim 20/menit, 200/hari.
  2. Cadangan: Wikimedia Commons, berkas ber-structured-data CC0 (`haswbstatement:P275=Q6938433`).
Cadangan hanya CC0/PDM: bebas dipakai komersial tanpa kewajiban atribusi; kredit tetap dicatat di manifest (praktik baik).
Data yang dikirim ke luar hanya kata kunci produk generik (tanpa data pribadi). Gambar tidak di-hotlink (CSP
`img-src 'self'`): dimuat ke Odoo `image_1920` oleh smd_images_odoo_shell.py lalu tampil lewat proxy BFF.

Output (default `data/brand-catalog/smd/`, sudah di .gitignore): `images/<slug>.jpg` (maks 1024 px) + `manifest.json`.
Idempoten: gambar yang sudah ada dilewati. Pakai:
    PIXABAY_API_KEY=... python scripts/brand-catalog/fetch_smd_images.py [--out DIR] [--limit N] [--only "Bayam,Wortel" [--refresh]]
Prasyarat: Python 3.10+, Pillow.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

UA = "efn-catalog-demo/1.0 (demo catalog image fetch; contact via repository owner)"
OPENVERSE = "https://api.openverse.org/v1/images/"
COMMONS = "https://commons.wikimedia.org/w/api.php"
MAX_PX = 1024

# Nama produk (persis basis nama di CATALOG seed) -> kata kunci pencarian (Inggris). Alkohol & produk non-halal TIDAK
# dipublikasikan online (SEC-01) sehingga tidak dicari.
QUERIES: dict[str, str] = {
    # Sayur
    "Bayam": "fresh spinach leaves", "Kangkung": "water spinach vegetable", "Sawi Hijau": "mustard greens vegetable",
    "Wortel": "fresh carrots", "Kentang": "potatoes", "Tomat": "fresh tomatoes", "Cabai Merah Keriting": "red chili peppers",
    "Cabai Rawit": "bird's eye chili", "Bawang Merah": "shallots", "Bawang Putih": "garlic bulbs",
    "Bawang Bombay": "onions", "Kol": "cabbage", "Brokoli": "broccoli", "Buncis": "green beans",
    "Terong Ungu": "eggplant", "Timun": "cucumbers", "Labu Siam": "chayote", "Jagung Manis": "sweet corn cob",
    "Selada": "lettuce", "Daun Bawang": "green onions chopped", "Seledri": "celery stalks", "Jahe": "ginger root",
    "Kunyit": "turmeric root", "Lengkuas": "galangal root", "Serai": "lemongrass stalks", "Tauge": "bean sprouts",
    "Pakcoy": "bok choy cabbage", "Kacang Panjang": "yardlong beans", "Paprika Merah": "red bell pepper",
    "Jamur Tiram": "oyster mushrooms",
    # Buah
    "Pisang Cavendish": "bananas", "Apel Merah": "red apples", "Jeruk Manis": "oranges fruit", "Mangga Harum": "ripe mango",
    "Semangka": "watermelon", "Melon": "cantaloupe melon", "Pepaya": "papaya halved", "Nanas": "pineapple",
    "Anggur Hijau": "green grapes", "Pir": "pears fruit", "Salak": "salak snake fruit", "Kelengkeng": "longan",
    "Jambu Kristal": "guava fruit", "Alpukat": "avocado", "Buah Naga": "dragon fruit", "Stroberi": "strawberries bowl",
    "Kurma Segar": "fresh dates fruit", "Lemon": "lemons", "Kiwi": "kiwi sliced", "Manggis": "mangosteen",
    "Rambutan": "rambutan", "Sirsak": "soursop", "Markisa": "passion fruit halved", "Jeruk Nipis": "limes",
    "Belimbing": "star fruit carambola",
    # Daging
    "Daging Sapi Has Dalam": "beef tenderloin raw", "Daging Sapi Sengkel": "beef shank raw", "Daging Sapi Giling": "ground beef",
    "Iga Sapi": "beef ribs raw", "Ayam Utuh": "whole raw chicken", "Dada Ayam Fillet": "chicken breast fillet raw",
    "Paha Ayam": "chicken thighs raw", "Sayap Ayam": "chicken wings raw", "Ati Ampela Ayam": "raw chicken liver",
    "Daging Kambing": "raw lamb meat", "Tetelan Sapi": "beef trimmings raw", "Buntut Sapi": "oxtail",
    "Ayam Kampung": "raw chicken meat", "Bebek Utuh": "whole raw duck", "Ceker Ayam": "chicken feet raw",
    # Ikan & seafood
    "Ikan Kembung": "mackerel", "Ikan Tongkol": "tuna fish market", "Ikan Bandeng": "milkfish",
    "Ikan Nila": "tilapia fish", "Ikan Lele": "catfish", "Udang Vaname": "raw prawns", "Cumi-cumi": "raw squid",
    "Ikan Gurame": "fresh fish ice", "Ikan Kakap Fillet": "snapper fillet", "Ikan Tenggiri": "mackerel fish raw",
    "Kerang Hijau": "green mussels", "Ikan Patin Fillet": "fish fillet raw",
    # Bakery
    "Roti Tawar": "white bread loaf", "Roti Gandum": "whole grain bread", "Roti Sobek Cokelat": "chocolate brioche",
    "Roti Isi Keju": "cheese bread", "Donat Gula": "donuts", "Croissant Mentega": "croissant",
    "Bolu Pandan": "green sponge cake", "Roti Kismis": "raisin bread", "Roti Burger": "burger buns",
    "Roti Hotdog": "hot dog buns", "Brownies Panggang": "brownies", "Bika Ambon": "bika ambon cake",
    "Lapis Legit": "layered cake slice", "Roti Pisang": "banana bread loaf", "Kue Lumpur": "coconut custard tart",
    "Bolu Gulung": "swiss roll cake", "Roti Abon": "floss bread bun", "Muffin Cokelat": "chocolate muffins",
    # Dairy & chilled
    "Susu Segar Pasteurisasi": "milk bottle", "Yoghurt Plain": "plain yogurt", "Yoghurt Buah": "fruit yogurt",
    "Minuman Yoghurt": "yogurt drink", "Keju Cheddar": "cheddar cheese", "Keju Mozzarella": "mozzarella cheese",
    "Mentega Tawar": "butter block", "Krim Kental": "whipped cream", "Telur Ayam": "chicken eggs", "Telur Bebek": "duck eggs",
    "Tahu Putih": "tofu cubes", "Tahu Kuning": "yellow tofu", "Tempe": "tempeh", "Sosis Ayam": "chicken sausages",
    "Bakso Sapi": "beef meatballs", "Puding Susu": "milk pudding", "Susu Kedelai": "soy milk", "Mi Basah": "fresh yellow noodles",
    "Kulit Lumpia": "spring roll wrappers", "Santan Segar": "coconut milk",
    # Frozen
    "Nugget Ayam": "chicken nuggets", "Sosis Sapi Beku": "sausages", "Kentang Goreng Beku": "french fries",
    "Dimsum Ayam": "dim sum dumplings", "Siomay Ikan": "siomay", "Es Krim Vanila": "vanilla ice cream scoop",
    "Es Krim Cokelat": "chocolate ice cream", "Bakso Ikan Beku": "fish balls", "Udang Kupas Beku": "peeled shrimp",
    "Daging Burger Beku": "burger patties raw", "Pizza Beku": "pizza", "Lumpia Beku": "spring rolls",
    "Edamame Beku": "edamame", "Fillet Ikan Dori Beku": "white fish fillet", "Roti Paratha Beku": "paratha flatbread",
    # Bahan pokok
    "Beras Premium": "white rice grains", "Beras Medium": "rice grains bowl", "Gula Pasir": "white sugar",
    "Tepung Terigu": "flour", "Minyak Goreng": "cooking oil bottle", "Kecap Manis": "soy sauce",
    "Saus Sambal": "red chili sauce", "Mi Instan Goreng": "fried instant noodles", "Mi Instan Kuah": "instant noodle soup",
    "Garam Beryodium": "salt", "Kopi Bubuk": "ground coffee", "Teh Celup": "tea bags", "Susu Kental Manis": "condensed milk",
    "Ikan Sarden Kaleng": "canned sardines", "Kornet Sapi": "canned meat", "Santan Kemasan": "coconut milk carton",
    "Bumbu Instan Rendang": "rendang spices", "Bihun Jagung": "rice vermicelli", "Oat Instan": "rolled oats",
    "Sereal Jagung": "corn flakes cereal", "Selai Kacang": "peanut butter", "Madu Murni": "honey jar",
    "Tepung Beras": "rice flour powder", "Tepung Tapioka": "starch powder", "Gula Merah": "brown sugar",
    "Kacang Hijau": "mung beans", "Susu Bubuk": "powdered milk", "Kaldu Bubuk": "spice powder", "Saus Tomat": "tomato sauce",
    "Mayones": "mayonnaise", "Pasta Spageti": "spaghetti", "Agar-agar Bubuk": "jelly dessert",
    "Cokelat Bubuk": "cocoa powder", "Kurma Kemasan": "dates fruit", "Kacang Tanah Kupas": "peanuts shelled",
    # Minuman
    "Air Mineral": "bottled water", "Air Mineral Galon": "water gallon", "Teh Kemasan": "iced tea bottle",
    "Kopi Siap Minum": "iced coffee", "Minuman Isotonik": "sports drink", "Jus Jeruk": "orange juice glass",
    "Jus Apel": "apple juice", "Susu UHT Cokelat": "chocolate milk glass", "Susu UHT Full Cream": "glass of milk",
    "Minuman Soda Cola": "cola glass ice", "Minuman Soda Jeruk": "orange soda glass", "Sirup Rasa Mawar": "rose syrup drink",
    "Sirup Rasa Jeruk": "orange syrup drink", "Minuman Kelapa": "coconut water", "Minuman Energi": "energy drink",
    "Teh Hijau Kemasan": "green tea drink", "Sari Kacang Hijau": "mung beans", "Minuman Lidah Buaya": "aloe vera drink",
    "Cokelat Bubuk Sachet": "hot chocolate", "Kopi Instan Sachet": "instant coffee", "Es Batu Kristal": "ice cubes",
    "Minuman Jahe Instan": "ginger tea", "Minuman Yoghurt Ambient": "yogurt drink bottle",
    # Camilan
    "Keripik Kentang": "potato chips", "Keripik Singkong": "cassava chips", "Kacang Atom": "coated peanuts",
    "Biskuit Kelapa": "coconut biscuits", "Biskuit Cokelat": "chocolate cookies", "Wafer Vanila": "wafers",
    "Kue Kering Nastar": "pineapple jam cookies", "Kue Kering Kastengel": "cheese sticks", "Astor Cokelat": "wafer rolls",
    "Permen Mint": "mint candy", "Cokelat Batang": "chocolate bar", "Kerupuk Udang": "prawn crackers",
    "Rengginang": "rice crackers", "Popcorn Karamel": "caramel popcorn", "Kacang Mete": "cashew nuts",
    "Emping Melinjo": "crackers snack", "Biskuit Marie": "biscuits", "Pilus Pedas": "spicy snack",
    "Makaroni Pedas": "macaroni", "Rumput Laut Panggang": "nori seaweed",
    # Perawatan diri
    "Sabun Mandi Batang": "bar soap", "Sabun Mandi Cair": "liquid soap bottle", "Sampo": "shampoo bottle",
    "Kondisioner": "conditioner", "Pasta Gigi": "toothpaste", "Sikat Gigi": "toothbrush",
    "Deodoran": "deodorant", "Losion Tubuh": "body lotion", "Sabun Cuci Muka": "facial cleanser",
    "Popok Bayi": "baby diapers", "Pembalut": "cotton pads", "Tisu Wajah": "facial tissues box",
    "Minyak Kayu Putih": "eucalyptus oil", "Bedak Bayi": "talcum powder", "Cotton Bud": "cotton swabs",
    # Rumah tangga
    "Deterjen Bubuk": "laundry detergent powder", "Deterjen Cair": "liquid laundry detergent",
    "Pewangi Pakaian": "laundry clothes fresh", "Sabun Cuci Piring": "dish soap", "Pembersih Lantai": "floor cleaner",
    "Pembersih Kaca": "glass cleaner spray", "Pemutih Pakaian": "white laundry", "Kantong Sampah": "garbage bags",
    "Tisu Dapur": "paper towel roll", "Obat Nyamuk Semprot": "spray bottle", "Spons Cuci": "kitchen sponge",
    "Kapur Barus": "white balls", "Pengharum Ruangan": "air freshener",
    # Impor premium (tanpa produk non-halal)
    "Keju Brie Impor": "brie cheese", "Daging Sapi Wagyu Impor Beku": "wagyu beef", "Minyak Zaitun Extra Virgin": "olive oil bottle",
    "Keju Parmesan Impor": "parmesan", "Salmon Atlantik Fillet Beku": "salmon fillet", "Cokelat Batang Gaya Eropa": "dark chocolate",
    "Selai Buah Impor": "fruit jam jar", "Pasta Gandum Impor": "penne", "Teh Earl Grey Impor": "black tea cup",
    "Biskuit Mentega Impor": "butter cookies tin",
}

# Lorong non-pangan (perawatan diri & rumah tangga) dicari tanpa `category=food` Pixabay.
_names = list(QUERIES)
NONFOOD = set(_names[_names.index("Sabun Mandi Batang"):_names.index("Keju Brie Impor")])
# Merek dagang & alkohol tidak boleh tampil di katalog demo (SEC-01; foto generik, bukan merek).
BLOCKED_TAG = re.compile(r"\b(whisk(e)?y|beer|wine|alcohol|liquor|vodka|rum|cocktail|coca|pepsi|red ?bull|heinz|"
                         r"nutella|oreo|l'?oreal|twinings|logo|brand)\b", re.I)
BAD_TITLE = re.compile(r"\b(logo|icon|vector|clipart|clip art|illustration|drawing|cartoon|diagram|map|painting|poster)\b", re.I)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


STOP = {"fresh", "raw", "whole", "of", "a", "the", "bottle", "jar", "box", "can", "market", "drink", "fruit", "vegetable", "glass", "bowl"}


def relevant(query: str, text: str) -> bool:
    """Kata kunci utama (kata non-umum terakhir dari query, mis. 'bananas' -> 'banana') wajib ada di judul/tag."""
    words = [w for w in re.findall(r"[a-z']+", query.lower()) if w not in STOP] or query.lower().split()
    head = words[-1].rstrip("s") if len(words[-1]) > 4 else words[-1]
    return head in text.lower()


# Sumber Openverse diutamakan: foto stok CC0 realistis (StockSnap) & Wikimedia; Flickr CC0 terlalu acak (uji 23 Nov).
OV_SOURCES = "stocksnap,wikimedia,rawpixel,flickr"


def openverse(query: str) -> list[dict]:
    q = urllib.parse.urlencode({"q": query, "license": "cc0,pdm", "page_size": 20, "mature": "false",
                                "category": "photograph", "source": OV_SOURCES})
    out = []
    rank = {s: i for i, s in enumerate(OV_SOURCES.split(","))}
    results = get_json(f"{OPENVERSE}?{q}").get("results", [])
    results.sort(key=lambda r: rank.get(r.get("source") or r.get("provider") or "", 9))
    for r in results:
        text = " ".join([r.get("title") or ""] + [t.get("name", "") for t in (r.get("tags") or [])])
        if BAD_TITLE.search(r.get("title") or "") or (r.get("width") or 0) < 500 or not relevant(query, text):
            continue
        out.append({"url": r["url"], "source": r.get("foreign_landing_url"), "creator": r.get("creator"),
                    "license": r.get("license"), "provider": r.get("provider"), "title": r.get("title")})
    return out


def pexels(query: str, key: str) -> list[dict]:
    """Pexels API (pilihan user 23 Nov 2026): foto stok realistis; lisensi Pexels = boleh komersial tanpa atribusi
    (tidak boleh dijual ulang apa adanya / menyiratkan endorsement). Key dari env, tidak pernah dicetak/disimpan."""
    q = urllib.parse.urlencode({"query": query, "per_page": 8, "orientation": "square", "size": "medium"})
    req = urllib.request.Request(f"https://api.pexels.com/v1/search?{q}", headers={"User-Agent": UA, "Authorization": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    return [
        {"url": p["src"].get("large") or p["src"]["original"], "source": p.get("url"), "creator": p.get("photographer"),
         "license": "pexels", "provider": "pexels", "title": (p.get("alt") or "")[:160]}
        for p in data.get("photos", [])
    ]


def pixabay(query: str, key: str, food: bool) -> list[dict]:
    """Pixabay API (alternatif saat Pexels API dijeda, 23 Nov 2026): Content License Pixabay = komersial tanpa atribusi
    (tidak boleh dijual ulang apa adanya). Batas 100 permintaan/menit. Key dari env, tidak dicetak/disimpan.
    Pixabay melarang hotlink permanen -> gambar diunduh & disimpan sendiri (memang pola skrip ini).
    Review visual run pertama: `order=popular` tanpa kategori memberi foto kucing/kota/kereta -> produk pangan dibatasi
    `category=food`, kandidat bertag merek/alkohol dibuang, kandidat yang tag-nya memuat kata kunci didahulukan."""
    params = {"key": key, "q": query, "image_type": "photo", "per_page": 12, "safesearch": "true", "min_width": 640}
    if food:
        params["category"] = "food"
    req = urllib.request.Request(f"https://pixabay.com/api/?{urllib.parse.urlencode(params)}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    hits = [
        {"url": h.get("largeImageURL") or h["webformatURL"], "source": h.get("pageURL"), "creator": h.get("user"),
         "license": "pixabay", "provider": "pixabay", "title": (h.get("tags") or "")[:160]}
        for h in data.get("hits", [])
        if not BLOCKED_TAG.search(h.get("tags") or "")
    ]
    return sorted(hits, key=lambda c: not relevant(query, c["title"]))


def commons(query: str) -> list[dict]:
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 10,
        "gsrsearch": f"{query} filetype:bitmap haswbstatement:P275=Q6938433", "prop": "imageinfo",
        "iiprop": "url|size|extmetadata", "iiurlwidth": MAX_PX,
    })
    pages = get_json(f"{COMMONS}?{q}").get("query", {}).get("pages", {})
    out = []
    for p in sorted(pages.values(), key=lambda v: v.get("index", 99)):
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        lic = (meta.get("LicenseShortName", {}).get("value") or "").lower()
        if "cc0" not in lic and "public domain" not in lic:
            continue
        if BAD_TITLE.search(p.get("title", "")) or (ii.get("width") or 0) < 500 or not relevant(query, p.get("title", "")):
            continue
        out.append({"url": ii.get("thumburl") or ii.get("url"), "source": ii.get("descriptionurl"),
                    "creator": re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value") or "")[:120],
                    "license": lic, "provider": "wikimedia", "title": p.get("title")})
    return out


def download_jpeg(url: str, dest: Path) -> tuple[int, int]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read(25 * 1024 * 1024)
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 2:
                raise
            time.sleep(15 * (attempt + 1))  # server gambar Wikimedia membatasi laju unduh
    time.sleep(1.5)
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    img.thumbnail((MAX_PX, MAX_PX))
    # Persegi tengah (kartu katalog 1:1): potong sisi panjang, foto tidak terdistorsi.
    w, h = img.size
    s = min(w, h)
    img = img.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    img.save(dest, "JPEG", quality=85, optimize=True, progressive=True)
    return img.size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/brand-catalog/smd")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--refresh", action="store_true", help="unduh ulang nama di --only walau sudah ada (ganti foto yang salah)")
    args = ap.parse_args()
    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    mpath = out / "manifest.json"
    manifest: dict = json.loads(mpath.read_text("utf-8")) if mpath.exists() else {}
    names = [n.strip() for n in args.only.split(",") if n.strip()] or list(QUERIES)
    ov_budget = True
    pexels_key = os.environ.get("PEXELS_API_KEY", "").strip()
    pixabay_key = os.environ.get("PIXABAY_API_KEY", "").strip()
    print("sumber utama:", "Pexels" if pexels_key else "Pixabay" if pixabay_key else "Openverse/Wikimedia (CC0/PDM)")
    done = 0
    for name in names:
        if args.limit and done >= args.limit:
            break
        dest = out / "images" / f"{slug(name)}.jpg"
        if dest.exists() and name in manifest and not (args.refresh and args.only):
            continue
        query = QUERIES[name]
        candidates: list[dict] = []
        if pexels_key:
            try:
                candidates = pexels(query, pexels_key)
                time.sleep(0.4)  # batas Pexels 200/jam per key; 251 produk muat dalam satu jam lebih sedikit
            except urllib.error.HTTPError as e:
                print(f"Pexels {name}: HTTP {e.code}", file=sys.stderr)
                if e.code in (401, 403):
                    return 2
                if e.code == 429:
                    print("Kuota Pexels (200/jam) habis: jalankan ulang perintah yang sama ±1 jam lagi (yang sudah ada dilewati).")
                    break
        if not candidates and pixabay_key:
            try:
                candidates = pixabay(query, pixabay_key, name not in NONFOOD)
                time.sleep(0.7)  # <= 100 permintaan/menit
            except urllib.error.HTTPError as e:
                print(f"Pixabay {name}: HTTP {e.code}", file=sys.stderr)
                if e.code in (400, 401, 403):
                    print("Key Pixabay ditolak (cek PIXABAY_API_KEY).")
                    return 2
                if e.code == 429:
                    print("Batas Pixabay tercapai: tunggu 1 menit lalu jalankan ulang (yang sudah ada dilewati).")
                    break
        if not candidates and ov_budget:
            try:
                candidates = openverse(query)
                time.sleep(3.2)  # <= 20 permintaan/menit (batas anonim)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    print("Openverse: kuota harian habis -> Wikimedia Commons", file=sys.stderr)
                    ov_budget = False
                else:
                    print(f"Openverse {name}: HTTP {e.code}", file=sys.stderr)
        if not candidates:
            try:
                candidates = commons(query)
                time.sleep(1.0)
            except Exception as e:  # noqa: BLE001 - lanjut ke produk berikutnya
                print(f"Commons {name}: {e}", file=sys.stderr)
        ok = False
        for c in candidates[:4]:
            try:
                size = download_jpeg(c["url"], dest)
                manifest[name] = {**c, "query": query, "file": dest.name, "size": list(size)}
                ok = True
                break
            except Exception as e:  # noqa: BLE001 - coba kandidat berikutnya
                print(f"unduh gagal {name}: {e}", file=sys.stderr)
        print(("OK  " if ok else "MISS"), name, "<-", (manifest.get(name) or {}).get("provider", "-"), flush=True)
        done += 1
        mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    print(f"selesai: {len(manifest)}/{len(QUERIES)} produk bergambar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
