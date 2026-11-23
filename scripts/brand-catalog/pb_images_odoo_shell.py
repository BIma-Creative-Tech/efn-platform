# ruff: noqa: F821
# Odoo shell: pasang foto situs resmi Paris Baguette (hasil scrape_parisbaguette.py, data/brand-catalog/parisbaguette)
# ke product.template.image_1920 produk PB yang terpublikasi di kanal online PB (menu hasil migrasi ESB).
# HANYA environment lokal efn-prod gladi. Izin pemakaian brand: konfirmasi tim Erajaya 20 Okt 2026 (README brand-catalog).
#
# Pencocokan nama setelah normalisasi (huruf kapital, "&" -> "AND", tanda baca dibuang, spasi dirapikan):
#   1) sama persis; 2) nama ESB = nama situs + penanda kanal/isi saja (SUFFIX_RE), mis. "TRUFFLE MUSHROOM SOUP (OC)"
#   -> "Truffle Mushroom Soup" (prefiks terpanjang menang). Tidak ada pencocokan fuzzy: lebih baik tanpa foto daripada
#   foto produk lain.
# Idempoten: foto yang sudah sama (checksum) dilewati; foto lain yang sudah ada di Odoo (dipasang manual) dipertahankan
# kecuali EFN_PB_IMG_FORCE=1. Master produk lain (nama, harga, deskripsi) tidak diubah.
# Pakai:
#   docker cp data/brand-catalog/parisbaguette efn-prod-odoo-1:/tmp/pbimg
#   MSYS_NO_PATHCONV=1 docker exec -i -e EFN_PB_IMG_DRY_RUN=1 efn-prod-odoo-1 \
#     odoo shell -c /etc/odoo/odoo.conf -d efn_odoo --no-http < scripts/brand-catalog/pb_images_odoo_shell.py
#   (ulangi tanpa EFN_PB_IMG_DRY_RUN untuk menyimpan)
import base64
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path

root = Path(os.environ.get("EFN_PB_IMG_DIR", "/tmp/pbimg"))
force = os.environ.get("EFN_PB_IMG_FORCE") == "1"
dry = os.environ.get("EFN_PB_IMG_DRY_RUN") == "1"


# Sisa nama ESB setelah prefiks yang masih dianggap menu yang sama: penanda kanal (OC/T/D/SW/ONLINE), isi "(4 PCS)",
# harga paket "125K". Sisa lain ("CROISSANT DUA SAUDARI", "CHOCOLATE DIPPING SAUCE") = menu berbeda -> tanpa foto.
SUFFIX_RE = re.compile(r"^(?:\s*(?:OC|T|D|SW|ONLINE|\d+K|\d+ PCS))+$")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.upper().replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


products = json.loads((root / "products.json").read_text("utf-8"))["products"]
site = {}
for p in products:
    if p.get("image_file") and (root / p["image_file"]).is_file():
        site.setdefault(norm(p["name"]), p)
bases = sorted(site, key=len, reverse=True)

channel = env["efn.online.channel"].sudo().search([("code", "=", "PB")], limit=1)
if not channel:
    raise SystemExit("kanal PB tidak ada")
T = env["product.template"].sudo()
set_n = same = kept = 0
unmatched = []
used = set()
for t in T.search([("efn_online_channel_ids", "in", channel.ids)], order="name"):
    name = norm(t.with_context(lang="en_US").name)
    base = next(
        (b for b in bases if name == b or (name.startswith(b + " ") and SUFFIX_RE.match(name[len(b):]))), None
    )
    if not base:
        unmatched.append(t.with_context(lang="en_US").name)
        continue
    used.add(base)
    data = (root / site[base]["image_file"]).read_bytes()
    if t.image_1920 and hashlib.sha1(base64.b64decode(t.image_1920)).hexdigest() == hashlib.sha1(data).hexdigest():
        same += 1
        continue
    if t.image_1920 and not force:
        kept += 1
        continue
    print(f"PB-IMG {t.with_context(lang='en_US').name!r} <- {site[base]['name']!r}")
    t.write({"image_1920": base64.b64encode(data)})
    set_n += 1
print(f"PB-IMG foto situs: {len(site)}, terpakai {len(used)}; tanpa pasangan di kanal: {len(site) - len(used)}")
print(f"PB-IMG dipasang {set_n}, sudah sama {same}, dipertahankan (manual) {kept}, menu tanpa foto {len(unmatched)}")
print("PB-IMG foto situs tak terpakai: " + "; ".join(site[b]["name"] for b in sorted(set(site) - used)))
if dry:
    env.cr.rollback()
    print("PB-IMG DRY RUN: di-rollback")
else:
    env.cr.commit()
