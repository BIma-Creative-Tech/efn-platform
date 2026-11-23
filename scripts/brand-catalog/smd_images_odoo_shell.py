# Odoo shell: pasang foto produk DEMO SMD (hasil fetch_smd_images.py) ke product.template.image_1920.
# Pencocokan = prefiks nama terpanjang ("Cokelat Bubuk Sachet 10 sachet" -> "Cokelat Bubuk Sachet", bukan
# "Cokelat Bubuk"); hanya produk company SMD. Idempoten: foto yang sudah sama (checksum) tidak ditulis ulang;
# foto yang diganti manual di Odoo (tidak bertanda kredit) dipertahankan kecuali EFN_SMD_IMG_FORCE=1.
# Pakai (folder hasil fetch disalin ke container dulu):
#   docker cp <out-dir> efn-prod-odoo-1:/tmp/smdimg
#   MSYS_NO_PATHCONV=1 docker exec -i -e EFN_SMD_IMG_DIR=/tmp/smdimg efn-prod-odoo-1 \
#     odoo shell -c /etc/odoo/odoo.conf -d efn_odoo --no-http < scripts/brand-catalog/smd_images_odoo_shell.py
import base64
import hashlib
import json
import os
from pathlib import Path

root = Path(os.environ.get("EFN_SMD_IMG_DIR", "/tmp/smdimg"))
force = os.environ.get("EFN_SMD_IMG_FORCE") == "1"
manifest = json.loads((root / "manifest.json").read_text("utf-8"))
company = env.ref("efn_setup_supermarket.company_smd")
bases = sorted(manifest, key=len, reverse=True)
T = env["product.template"].sudo().with_context(active_test=False)
set_n = same = kept = nomatch = 0
for t in T.search([("company_id", "=", company.id)]):
    name = t.with_context(lang="en_US").name or ""
    base = next((b for b in bases if name == b or name.startswith(b + " ")), None)
    if not base:
        nomatch += 1
        continue
    data = (root / "images" / manifest[base]["file"]).read_bytes()
    b64 = base64.b64encode(data)
    if t.image_1920 and hashlib.sha256(base64.b64decode(t.image_1920)).digest() == hashlib.sha256(data).digest():
        same += 1
        continue
    credit = manifest[base]
    marker = f"Foto: {credit.get('creator') or '-'} ({credit.get('provider')}, {credit.get('license')})"
    if t.image_1920 and not force and "Foto: " not in (t.efn_online_description or ""):  # kredit apa pun = foto dari skrip
        kept += 1
        continue
    vals = {"image_1920": b64}
    desc = (t.efn_online_description or "").split(" Foto: ")[0]
    vals["efn_online_description"] = f"{desc} {marker}".strip()[:500]
    t.write(vals)
    set_n += 1
env.cr.commit()
print(f"SMD foto: dipasang {set_n}, sudah sama {same}, dipertahankan (manual) {kept}, tanpa foto {nomatch}")
