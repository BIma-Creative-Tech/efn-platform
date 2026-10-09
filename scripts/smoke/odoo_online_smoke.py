# Smoke E2E sisi Odoo untuk penjualan online G17 (dev saja) — dijalankan lewat `odoo shell` oleh
# scripts/smoke/run-e2e-smoke.sh tahap `o` (docs/04 §10, ADR-034/ADR-019 Proposed).
#
# Bukan modul Odoo dan bukan test unit. Skrip ini:
#   - `setup`    : konfigurasi kanal RTL dev (data FIKTIF "SMOKE", idempoten) setara demo efn_setup_online: pembulatan
#                  Rupiah, gudang outlet SMKOL, jurnal titipan bank, pricelist IDR, user teknis `efn_online_rtl`
#                  (TANPA API key — key dibuat lewat runbook odoo-online-api-key §1), kanal RTL + outlet TOKO-01,
#                  4 produk online (PPN l10n_id), stok awal, dan user staf outlet `smoke_online_staff`.
#   - `stock`    : set stok bebas satu produk smoke di gudang outlet (inventory mode standar).
#   - `order`    : baca state efn.online.order + SO + faktur (tanpa kode ambil; hanya boolean ada/tidaknya hash).
#   - `ready`    : aksi staf `action_efn_ready` sebagai user staf (bukan admin) -> kirim outbox sekarang.
#   - `handover` : wizard serah terima sebagai user staf dengan SMOKE_CODE -> kirim outbox sekarang.
#   - `outbox`   : proses outbox event yang jatuh tempo sekarang (tanpa menunggu cron 1 menit).
#   - `apikey`   : buat API key user teknis lewat prosedur runbook (with_user, scope global, kedaluwarsa < durasi
#                  grup). Key ditulis ke /tmp/efn_online_key (0600) di container, BUKAN ke stdout.
# Tidak ada secret di file ini; tidak ada data pribadi (kanal online memakai partner generik tanpa HP/email).
# Keluaran: satu baris `SMOKE_RESULT <json>`.
# ruff: noqa: F821  (variabel `env` disediakan odoo shell)
import base64
import io
import json
import os
from datetime import datetime, timedelta

from odoo import Command

if (os.environ.get("APP_ENV") or "").lower() != "dev":
    raise SystemExit("odoo_online_smoke hanya untuk APP_ENV=dev")

STEP = os.environ["SMOKE_STEP"]
STATE_FILE = "/tmp/efn_online_smoke_state.json"  # noqa: S108 - container dev, hanya id record (tanpa PII)
PRODUCTS = {
    # kunci: (nama, harga list incl? -> list_price excl pajak, storable, stok awal)
    "rice": ("SMOKE Beras 5 kg (online)", 78900.0, True, 1000.0),
    "oil": ("SMOKE Minyak Goreng 2 L (online)", 36500.0, True, 1000.0),
    "bag": ("SMOKE Tas Belanja (online)", 5000.0, False, 0.0),
    "scarce": ("SMOKE Stok Terbatas (online)", 12500.0, True, 50.0),
}


def out(**kw):
    print("SMOKE_RESULT " + json.dumps(kw, default=str))


def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(st, fh)


def load_state():
    with open(STATE_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def png_bytes(seed):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 640), (240, 200 - seed * 30, 120 + seed * 20))
    ImageDraw.Draw(img).rectangle([120, 120, 520, 520], fill=(30, 90 + seed * 40, 60))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def step_setup():
    company = env.ref("efn_setup_company.company_rtl")
    admin = env.ref("base.user_admin")
    if company not in admin.company_ids:
        admin.write({"company_ids": [Command.link(company.id)]})
    su = env(user=admin, context={"allowed_company_ids": [company.id]}, su=True)

    rounding = su["account.cash.rounding"].search([("name", "=", "SMOKE Rupiah bulat")], limit=1) or su[
        "account.cash.rounding"
    ].create({"name": "SMOKE Rupiah bulat", "rounding": 1.0, "strategy": "biggest_tax", "rounding_method": "HALF-UP"})
    wh = su["stock.warehouse"].search([("code", "=", "SMKOL"), ("company_id", "=", company.id)], limit=1) or su[
        "stock.warehouse"
    ].create({"name": "SMOKE Toko Online", "code": "SMKOL", "company_id": company.id})
    journal = su["account.journal"].search([("code", "=", "SMKTT"), ("company_id", "=", company.id)], limit=1) or su[
        "account.journal"
    ].create({"name": "SMOKE Titipan Online - Holding", "code": "SMKTT", "type": "bank", "company_id": company.id})
    pricelist = su["product.pricelist"].search(
        [("name", "=", "SMOKE Online RTL"), ("company_id", "=", company.id)], limit=1
    ) or su["product.pricelist"].create(
        {"name": "SMOKE Online RTL", "currency_id": env.ref("base.IDR").id, "company_id": company.id}
    )
    group = env.ref("efn_online_order.group_online_channel")
    user = su["res.users"].with_context(active_test=False).search([("login", "=", "efn_online_rtl")], limit=1)
    if not user:
        user = (
            su["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Kanal online RTL (user teknis)",
                    "login": "efn_online_rtl",
                    "company_id": company.id,
                    "company_ids": [Command.set([company.id])],
                    "group_ids": [Command.set([env.ref("base.group_user").id, group.id])],
                }
            )
        )
    staff = su["res.users"].with_context(active_test=False).search([("login", "=", "smoke_online_staff")], limit=1)
    if not staff:
        staff = (
            su["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "SMOKE staf outlet online",
                    "login": "smoke_online_staff",
                    "company_id": company.id,
                    "company_ids": [Command.set([company.id])],
                    "group_ids": [Command.set([env.ref("base.group_user").id, env.ref("stock.group_stock_user").id])],
                }
            )
        )
    Channel = su["efn.online.channel"].with_context(active_test=False)
    channel = Channel.search([("code", "=", "RTL")], limit=1)
    vals = {
        "name": "Super-App Retail (SMOKE dev)",
        "company_id": company.id,
        "pricelist_id": pricelist.id,
        "partner_id": env.ref("efn_setup_online.partner_superapp_rtl").id,
        "team_id": env.ref("efn_setup_online.team_superapp_rtl").id,
        "cash_rounding_id": rounding.id,
        "journal_titipan_id": journal.id,
        "payment_timeout_minutes": 25,
        "active": True,
    }
    if channel:
        channel.write(vals)
    else:
        channel = Channel.create({**vals, "code": "RTL"})
    channel.write({"user_ids": [Command.set([user.id])]})
    outlet = su["efn.online.outlet"].with_context(active_test=False).search(
        [("channel_id", "=", channel.id), ("code", "=", "TOKO-01")], limit=1
    ) or su["efn.online.outlet"].create(
        {
            "channel_id": channel.id,
            "code": "TOKO-01",
            "public_name": "Toko Smoke (DEV)",
            "warehouse_id": wh.id,
            "prep_minutes": 15,
        }
    )
    tax = env.ref(f"account.{company.id}_tax_ST4")
    ids = {}
    for i, (key, (name, price, storable, qty)) in enumerate(PRODUCTS.items()):
        tmpl = su["product.template"].search([("name", "=", name), ("company_id", "=", company.id)], limit=1)
        if not tmpl:
            tmpl = su["product.template"].create(
                {
                    "name": name,
                    "type": "consu",
                    "is_storable": storable,
                    "list_price": price,
                    "sale_ok": True,
                    "company_id": company.id,
                    "taxes_id": [Command.set([tax.id])],
                    "efn_online_channel_ids": [Command.set([channel.id])],
                    "efn_online_description": f"{name}. Data contoh smoke (fiktif).",
                    "image_1920": base64.b64encode(png_bytes(i)) if key in ("rice", "oil") else False,
                }
            )
        product = tmpl.product_variant_id
        ids[key] = product.id
        if storable:
            set_stock(su, product, wh, qty, only_if_below=key != "scarce")
    st = {
        "company_id": company.id,
        "channel_id": channel.id,
        "outlet_id": outlet.id,
        "warehouse_id": wh.id,
        "user_id": user.id,
        "staff_id": staff.id,
        "products": ids,
    }
    save_state(st)
    env.cr.commit()
    out(
        ok=True,
        channel=channel.code,
        company_code=company.efn_brand_code,
        outlet=outlet.code,
        products=ids,
        tech_user_groups_ok=user.has_group("efn_online_order.group_online_channel"),
        staff_is_admin=staff.has_group("base.group_system"),
    )


def set_stock(su, product, wh, qty, only_if_below=False):
    location = wh.lot_stock_id
    product = product.with_context(warehouse_id=wh.id)
    if only_if_below and product.free_qty >= qty / 2:
        return
    quant = su["stock.quant"].with_context(inventory_mode=True)
    q = quant.search([("product_id", "=", product.id), ("location_id", "=", location.id)], limit=1)
    if q:
        q.write({"inventory_quantity": qty})
    else:
        q = quant.create({"product_id": product.id, "location_id": location.id, "inventory_quantity": qty})
    q.action_apply_inventory()


def step_stock():
    st = load_state()
    su = env(su=True, context={"allowed_company_ids": [st["company_id"]]})
    product = su["product.product"].browse(st["products"][os.environ["SMOKE_PRODUCT"]])
    wh = su["stock.warehouse"].browse(st["warehouse_id"])
    set_stock(su, product, wh, float(os.environ["SMOKE_QTY"]))
    env.cr.commit()
    out(ok=True, product_id=product.id, free_qty=product.with_context(warehouse_id=wh.id).free_qty)


def find_order(su):
    return su["efn.online.order"].search([("platform_order_ref", "=", os.environ["SMOKE_PART"])], limit=1)


def describe(order):
    so = order.sale_order_id
    invoices = so.invoice_ids
    return {
        "found": bool(order),
        "state": order.state,
        "so_name": so.name,
        "so_state": so.state,
        "cancel_reason": order.cancel_reason,
        "review_reason": order.review_reason,
        "pickup_attempts": order.pickup_attempts,
        "pickup_hash_present": bool(order.sudo().pickup_code_hash),
        "checkout_ref": order.checkout_ref,
        "amount_payable": order.amount_payable if "amount_payable" in order._fields else None,
        "invoices": sorted(set(invoices.filtered(lambda m: m.move_type == "out_invoice").mapped("state"))),
        "credit_notes": len(invoices.filtered(lambda m: m.move_type == "out_refund")),
        "pickings_done": len(so.picking_ids.filtered(lambda p: p.state == "done")),
        "outbox": sorted(f"{e.event}:{e.state}" for e in order.event_ids) if "event_ids" in order._fields else [],
    }


def step_order():
    su = env(su=True)
    order = find_order(su)
    out(ok=bool(order), **(describe(order) if order else {"found": False}))


def process_outbox():
    env.cr.commit()
    env["efn.online.event.outbox"].sudo()._efn_process_due()
    env.cr.commit()


def staff_env():
    st = load_state()
    staff = env["res.users"].browse(st["staff_id"])
    return env(user=staff, context={"allowed_company_ids": [st["company_id"]]})


def step_ready():
    se = staff_env()
    order = find_order(se(su=True))
    error = None
    try:
        se["efn.online.order"].browse(order.id).action_efn_ready()
    except Exception as exc:  # noqa: BLE001 - dilaporkan sebagai hasil smoke
        env.cr.rollback()
        error = type(exc).__name__
    process_outbox()
    out(ok=error is None, error=error, **describe(find_order(env(su=True))))


def step_handover():
    se = staff_env()
    order = find_order(se(su=True))
    wiz = se["efn.online.handover.wizard"].create({"order_id": order.id, "code": os.environ.get("SMOKE_CODE", "")})
    wiz.action_confirm()
    msg, done = wiz.message, wiz.done
    process_outbox()
    out(ok=True, done=done, message=msg, **describe(find_order(env(su=True))))


def step_outbox():
    process_outbox()
    order = find_order(env(su=True)) if os.environ.get("SMOKE_PART") else None
    out(ok=True, **(describe(order) if order else {}))


def step_apikey():
    st = load_state()
    user = env["res.users"].browse(st["user_id"])
    assert user.has_group("efn_online_order.group_online_channel"), "user teknis efn_online_rtl belum ada"
    days = int(os.environ.get("SMOKE_KEY_DAYS", "85"))
    exp = datetime.utcnow().replace(microsecond=0) + timedelta(days=days)
    name = "efn-online-RTL-" + datetime.utcnow().strftime("%Y%m%d%H%M")
    key = env["res.users.apikeys"].with_user(user)._generate(None, name, exp)
    env.cr.commit()
    old = os.umask(0o077)
    try:
        with open("/tmp/efn_online_key", "w", encoding="utf-8") as fh:  # noqa: S108 - diambil orkestrator lalu dihapus
            fh.write(key)
    finally:
        os.umask(old)
    out(ok=len(key) == 40, name=name, expires=exp.isoformat(), length=len(key))


{
    "setup": step_setup,
    "stock": step_stock,
    "order": step_order,
    "ready": step_ready,
    "handover": step_handover,
    "outbox": step_outbox,
    "apikey": step_apikey,
}[STEP]()
