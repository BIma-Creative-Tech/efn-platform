#!/usr/bin/env python
"""Import katalog brand (``data/brand-catalog/<brand>/products.json``) ke Odoo dev sebagai produk DEMO.

Alur: host (Python + Pillow) membaca JSON, me-resize gambar maks 1024 px (JPEG/PNG), menyusun payload ->
``docker cp`` ke container -> ``odoo shell`` menjalankan ``odoo_import_shell.py`` (ORM, upsert idempoten per
``default_code`` ``<PREFIX>-<source_id>``) -> ringkasan + angka verifikasi (per company, per kanal, bergambar).

Tidak ada password di argumen/log: mode ``exec`` memakai ``odoo.conf`` container (secret dari /run/secrets);
mode ``run`` ditujukan untuk DB uji lokal tanpa password (trust) dan menolak opsi password.

Contoh:
  python scripts/brand-catalog/import_odoo.py --brand parisbaguette --dry-run
  python scripts/brand-catalog/import_odoo.py --brand all --container efn-odoo-1 --db efn_odoo
  python scripts/brand-catalog/import_odoo.py --brand bacha --target run --network container:efn-catalog-pg \
      --db efn_catalog --db-host 127.0.0.1 --db-user odoo
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
DATA_ROOT = REPO_ROOT / "data" / "brand-catalog"
SHELL_SCRIPT = HERE / "odoo_import_shell.py"
MAX_IMAGE_PX = 1024

# Konfigurasi brand: prefix default_code, label kategori induk, harga PLACEHOLDER per kategori (IDR, DEMO saja —
# situs brand tidak memuat harga IDR; wajib diganti dari master harga resmi, lihat README §Harga).
BRANDS = {
    "parisbaguette": {
        "prefix": "PB",
        "label": "Paris Baguette",
        "default_price": 35000,
        "prices": {
            "Breads": 25000, "Pastries": 22000, "Cakes": 45000, "Sandwiches": 45000, "Meals": 65000,
            "Salads & Soup": 55000, "Sides": 30000, "Desserts": 35000, "All-Day Brunch": 75000,
            "Coffee by Café Adagio": 35000, "Tea by Teatra": 30000, "Others": 30000, "Summer Splash": 40000,
        },
    },
    "bacha": {
        "prefix": "BACHA",
        "label": "Bacha Coffee",
        "default_price": 350000,
        "prices": {
            "Coffee / Loose Coffee": 450000, "Coffee / Packed Coffee": 350000, "Coffee Bags": 300000,
            "Accessories": 750000, "Gourmet": 250000, "Gift Sets": 1200000,
        },
    },
    "grandlucky": {"prefix": "GL", "label": "GrandLucky", "default_price": 25000, "prices": {}},
}


def log(msg: str) -> None:
    print(f"[import] {msg}", flush=True)


def placeholder_price(cfg: dict, category: str | None) -> int:
    """Kunci terpanjang yang cocok dengan awal/akhir path kategori menang (sub-kategori lebih spesifik)."""
    category = category or ""
    for key, price in sorted(cfg["prices"].items(), key=lambda kv: -len(kv[0])):
        if category == key or category.endswith(key) or category.startswith(key):
            return price
    return cfg["default_price"]


def parse_weight_kg(item: dict) -> float | None:
    extra = item.get("extra") or {}
    if isinstance(extra.get("weight_kg"), (int, float)):
        return float(extra["weight_kg"])
    text = str(extra.get("weight_volume") or "")
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(kg|g)\b", text, re.I)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return round(value / 1000.0 if m.group(2).lower() == "g" else value, 3)


def encode_image(path: Path) -> str | None:
    """Resize maks 1024 px; RGBA -> PNG, lainnya -> JPEG q85 (Odoo membuat image_512/1024 sendiri)."""
    from PIL import Image, ImageOps  # Pillow di host

    if not path.is_file():
        return None
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((MAX_IMAGE_PX, MAX_IMAGE_PX))
        buf = io.BytesIO()
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        if has_alpha:
            img.convert("RGBA").save(buf, "PNG", optimize=True)
        else:
            img.convert("RGB").save(buf, "JPEG", quality=85, optimize=True, progressive=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_payload(brand: str, args) -> dict:
    cfg = BRANDS[brand]
    src = DATA_ROOT / brand / "products.json"
    if not src.is_file():
        raise SystemExit(f"{src} belum ada - jalankan scraper brand dulu")
    doc = json.loads(src.read_text(encoding="utf-8"))
    items = []
    for p in doc.get("products") or []:
        category = p.get("category") or "Lainnya"
        extra = p.get("extra") or {}
        pos_categ = extra.get("online_category") or category.split(" / ")[-1]
        has_price = isinstance(p.get("price_idr"), (int, float)) and p["price_idr"] > 0
        image_b64 = None
        if p.get("image_file") and not args.no_images:
            image_b64 = encode_image(DATA_ROOT / brand / p["image_file"])
        items.append({
            "default_code": f"{cfg['prefix']}-{p['source_id']}",
            "name": p["name"][:200],
            "description": (p.get("description_text") or "")[:2000] or None,
            "categ_path": [c.strip() for c in category.split(" / ") if c.strip()],
            "pos_categ": pos_categ,
            "company_code": p["company_code"],
            "list_price": float(p["price_idr"]) if has_price else float(placeholder_price(cfg, category)),
            "price_is_placeholder": not has_price,
            "weight": parse_weight_kg(p),
            "image_b64": image_b64,
            "source_url": p.get("source_url"),
        })
    if args.limit:
        items = items[: args.limit]
    return {
        "brand": brand,
        "brand_label": cfg["label"],
        "prefix": cfg["prefix"],
        "source_url": doc.get("source_url"),
        "imported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "dry_run": args.dry_run,
        "verify_only": args.verify_only,
        "archive_missing": args.archive_missing,
        "products": items,
    }


def docker(*cmd: str, stdin=None, capture=True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *cmd], stdin=stdin, capture_output=capture, text=True, encoding="utf-8",
                          errors="replace", check=False)


def run_shell(payload_file: Path, args) -> str:
    with SHELL_SCRIPT.open("rb") as script:
        if args.target == "exec":
            remote = f"/tmp/efn_catalog_payload_{payload_file.stem}.json"
            cp = docker("cp", str(payload_file), f"{args.container}:{remote}")
            if cp.returncode:
                raise SystemExit(f"docker cp gagal: {cp.stderr.strip()}")
            cmd = ["exec", "-i", "-e", f"EFN_CATALOG_PAYLOAD={remote}", args.container, "odoo", "shell",
                   "-c", args.odoo_conf, "-d", args.db, "--no-http", "--log-level=warn"]
        else:
            mount = f"{payload_file.parent.as_posix()}:/payload:ro"
            cmd = ["run", "--rm", "-i", "--network", args.network, "-v", mount,
                   "-v", f"{(REPO_ROOT / 'services' / 'odoo' / 'addons').as_posix()}:/mnt/extra-addons:ro",
                   "-e", f"EFN_CATALOG_PAYLOAD=/payload/{payload_file.name}", "--entrypoint", "odoo", args.image,
                   "shell", f"--db_host={args.db_host}", f"--db_port={args.db_port}", f"--db_user={args.db_user}",
                   "--addons-path=/mnt/extra-addons,/usr/lib/python3/dist-packages/odoo/addons", "-d", args.db,
                   "--no-http", "--log-level=warn"]
        proc = subprocess.run(["docker", *cmd], stdin=script, capture_output=True, check=False,
                              env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        if args.target == "exec":
            docker("exec", args.container, "rm", "-f", remote)
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    line = next((ln for ln in out.splitlines() if ln.startswith("EFN_RESULT ")), None)
    if not line:
        tail = "\n".join([ln for ln in (out + "\n" + err).splitlines() if "Missing not-null" not in ln][-25:])
        raise SystemExit(f"odoo shell gagal (exit {proc.returncode}):\n{tail}")
    return line[len("EFN_RESULT "):]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--brand", required=True, help="parisbaguette | bacha | grandlucky | all")
    ap.add_argument("--dry-run", action="store_true", help="validasi penuh di Odoo lalu rollback")
    ap.add_argument("--verify-only", action="store_true", help="hanya cetak angka verifikasi")
    ap.add_argument("--archive-missing", action="store_true", help="arsipkan produk prefix brand yang hilang dari JSON")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--target", choices=("exec", "run"), default="exec")
    ap.add_argument("--container", default="efn-odoo-1")
    ap.add_argument("--odoo-conf", default="/etc/odoo/odoo.conf")
    ap.add_argument("--db", default="efn_odoo")
    ap.add_argument("--image", default="efn/odoo:dev")
    ap.add_argument("--network", default="container:efn-catalog-pg")
    ap.add_argument("--db-host", default="127.0.0.1")
    ap.add_argument("--db-port", default="5432")
    ap.add_argument("--db-user", default="odoo")
    ap.add_argument("--keep-payload", action="store_true", help="simpan payload JSON (debug) di data/brand-catalog")
    args = ap.parse_args()

    if shutil.which("docker") is None:
        raise SystemExit("docker tidak ditemukan di PATH")
    brands = list(BRANDS) if args.brand == "all" else [args.brand]
    unknown = set(brands) - set(BRANDS)
    if unknown:
        raise SystemExit(f"brand tidak dikenal: {sorted(unknown)}")
    rc = 0
    for brand in brands:
        payload = build_payload(brand, args)
        if not payload["products"] and not args.verify_only:
            log(f"{brand}: 0 produk di products.json -> dilewati (GL: isi lewat scrape_grandlucky.py --from-csv)")
            continue
        n_img = sum(1 for p in payload["products"] if p["image_b64"])
        log(f"{brand}: {len(payload['products'])} produk, {n_img} gambar siap (maks {MAX_IMAGE_PX}px), "
            f"target={args.target} db={args.db}{' DRY-RUN' if args.dry_run else ''}")
        tmpdir = Path(tempfile.mkdtemp(prefix="efn-catalog-"))
        try:
            pfile = tmpdir / f"{brand}.json"
            pfile.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            if args.keep_payload:
                shutil.copyfile(pfile, DATA_ROOT / brand / "odoo_payload.json")
            result = json.loads(run_shell(pfile, args))
        except SystemExit as exc:
            log(f"{brand}: GAGAL - {exc}")
            rc = 1
            continue
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        (DATA_ROOT / brand / "last_import.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                                            encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
