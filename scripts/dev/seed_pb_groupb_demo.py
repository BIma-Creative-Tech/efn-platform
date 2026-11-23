# Seed DEMO grup B PB di efn-prod (gladi lokal, keputusan pemilik proyek 23 Nov 2026; BUKAN production):
# reservasi meja (efn_table_booking, docs/04 §15), QR ponsel (efn_pos_bff_order, ADR-032, docs/04 §14.A),
# promo Super-App (efn_promo, docs/04 §18). Prasyarat: ketiga modul terpasang, outlet online PB P002/P010/P018/P024/P027
# ada (scripts/dev/activate_pb_online_demo.py) dan POS restoran outlet itu punya denah meja (wizard G54).
# Idempoten. Dry run: env DEMO_DRY_RUN=1 -> rollback.
#   docker exec -i -e DEMO_DRY_RUN=1 efn-prod-odoo-1 odoo shell -c /etc/odoo/odoo.conf -d efn_odoo --no-http \
#     < scripts/dev/seed_pb_groupb_demo.py
# Cleanup sebelum gladi data nyata: arsipkan efn.promo.banner/popup bernama "DEMO PB ...", hapus efn.booking.settings
# outlet di atas + kalender "Jam buka reservasi PB - WIB (DEMO)", matikan efn_qr_enabled di POS outlet tersebut.
import base64
import io
import os
from datetime import timedelta

from PIL import Image, ImageDraw

from odoo import fields

DRY = os.environ.get("DEMO_DRY_RUN") == "1"
PB = env["res.company"].browse(4)
assert PB.efn_brand_code == "PB"
env = env(context=dict(env.context, allowed_company_ids=[PB.id], tz="Asia/Jakarta"))
OUTLETS = ["P002", "P010", "P018", "P024", "P027"]
DINE_IN = env["pos.preset"].browse(4)
TAKEOUT = env["pos.preset"].browse(5)
assert DINE_IN.service_at == "table" and TAKEOUT.service_at == "counter"

# 1. Kalender jam buka reservasi (08:00-22:00 setiap hari, WIB)
Cal = env["resource.calendar"]
cal = Cal.search([("name", "=", "Jam buka reservasi PB - WIB (DEMO)"), ("company_id", "=", PB.id)], limit=1)
if not cal:
    cal = Cal.create({
        "name": "Jam buka reservasi PB - WIB (DEMO)",
        "company_id": PB.id,
        "tz": "Asia/Jakarta",
        "attendance_ids": [(5, 0, 0)] + [
            (0, 0, {"name": f"Hari {d}", "dayofweek": str(d), "hour_from": 8.0, "hour_to": 22.0,
                    "day_period": "morning"})
            for d in range(7)
        ],
    })
print("DEMO-CAL", cal.id)

# 2. Reservasi + QR per outlet
for code in OUTLETS:
    outlet = env["efn.online.outlet"].search([("company_id", "=", PB.id), ("code", "=", code)], limit=1)
    cfg = env["pos.config"].search(
        [("company_id", "=", PB.id), ("picking_type_id.warehouse_id", "=", outlet.warehouse_id.id),
         ("module_pos_restaurant", "=", True)], limit=1)
    assert outlet and cfg, code
    # QR ponsel: preset dine-in wajib
    vals = {}
    if not cfg.use_presets:
        vals["use_presets"] = True
    if not cfg.default_preset_id:
        vals["default_preset_id"] = DINE_IN.id
    if (DINE_IN | TAKEOUT) - cfg.available_preset_ids:
        vals["available_preset_ids"] = [(4, DINE_IN.id), (4, TAKEOUT.id)]
    if vals:
        cfg.write(vals)
    if not cfg.efn_qr_enabled:
        cfg.write({"efn_qr_enabled": True, "efn_qr_preset_id": DINE_IN.id, "efn_qr_confirm_mode": "waiter"})
    s = env["efn.booking.settings"].with_context(active_test=False).search([("outlet_id", "=", outlet.id)])
    if not s:
        s = env["efn.booking.settings"].create({
            "outlet_id": outlet.id, "pos_config_id": cfg.id, "calendar_id": cal.id,
            "slot_minutes": "30", "duration_minutes": 90, "lead_minutes": 60, "horizon_days": 14,
            "min_party": 1, "max_party": 8, "confirm_mode": "staff", "hold_minutes": 15,
        })
    tables = s._efn_tables()
    print("DEMO-OUTLET", code, "pos", cfg.id, "qr", cfg.efn_qr_enabled, "booking", s.id, "meja", len(tables),
          "kursi", sum(tables.mapped("seats")))


# 3. Promo: gambar sederhana (warna PB), 2 banner + 1 pop-up
def make_image(w, h, bg, text):
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([24, 24, w - 24, h - 24], outline=(255, 255, 255), width=6)
    d.text((60, h // 2 - 10), text, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue())


now = fields.Datetime.now()
end = now + timedelta(days=30)
promos = [
    ("efn.promo.banner", "DEMO PB banner roti pagi", {
        "title": "Roti hangat setiap pagi", "subtitle": "Pesan online, ambil di gerai Paris Baguette terdekat.",
        "cta_label": "Pesan sekarang", "link_type": "brand", "link_brand": "PB", "sequence": 10,
        "image": make_image(1600, 800, (0, 56, 118), "PARIS BAGUETTE - Roti hangat setiap pagi (DEMO)")}),
    ("efn.promo.banner", "DEMO PB banner reservasi", {
        "title": "Reservasi meja tanpa antre", "subtitle": "Pilih jam dan jumlah tamu langsung dari aplikasi.",
        "cta_label": "Reservasi", "link_type": "brand", "link_brand": "PB", "sequence": 20,
        "image": make_image(1600, 800, (153, 27, 30), "PARIS BAGUETTE - Reservasi meja (DEMO)")}),
    ("efn.promo.popup", "DEMO PB popup QR meja", {
        "title": "Pesan dari meja lewat QR", "subtitle": "Pindai QR di meja, pesanan langsung ke kasir.",
        "cta_label": "Lihat menu", "link_type": "brand", "link_brand": "PB", "frequency": "once",
        "push_enabled": True, "push_title": "Pesan dari meja", "push_body": "Pindai QR di meja Paris Baguette (DEMO).",
        "image": make_image(1080, 1080, (0, 56, 118), "PARIS BAGUETTE - QR meja (DEMO)")}),
]
for model, name, vals in promos:
    rec = env[model].with_context(active_test=False).search([("name", "=", name), ("company_id", "=", PB.id)], limit=1)
    if not rec:
        rec = env[model].create({"name": name, "company_id": PB.id, "date_start": now, "date_end": end,
                                 "channel_web": True, "channel_app": True, **vals})
    print("DEMO-PROMO", model, rec.id, rec.state_label)

if DRY:
    env.cr.rollback()
    print("DEMO-DONE dry-run (rollback)")
else:
    env.cr.commit()
    print("DEMO-DONE commit")
