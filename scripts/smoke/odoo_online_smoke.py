# Smoke E2E sisi Odoo untuk penjualan online G17 (dev saja) — dijalankan lewat `odoo shell` oleh
# scripts/smoke/run-e2e-smoke.sh tahap `o` (docs/04 §10, ADR-034/ADR-019 Proposed).
#
# Bukan modul Odoo dan bukan test unit. Skrip ini:
#   - `setup`    : konfigurasi kanal RTL dev (data FIKTIF "SMOKE", idempoten) setara demo efn_setup_online: pembulatan
#                  Rupiah, gudang outlet SMKOL, jurnal titipan bank, pricelist IDR, user teknis `efn_online_rtl`
#                  (TANPA API key — key dibuat lewat runbook odoo-online-api-key §1), kanal RTL + outlet TOKO-01,
#                  4 produk online (PPN l10n_id), stok awal, dan user staf outlet `smoke_online_staff`.
#                  G19: kanal RTL batas bayar 70 menit + `stock_hold_mode=confirm` (SO `sale` + stok tercadang sejak
#                  place), promo kode dev `SMOKE10` (diskon 10 %, pricelist SMOKE), satu user teknis per kanal (L-37).
#   - `stock`    : set stok bebas satu produk smoke di gudang outlet (inventory mode standar).
#   - `free`     : baca free_qty (dibulatkan) satu produk smoke di gudang outlet; dipakai tahap `o` untuk membuktikan
#                  reservasi G19 (free_qty turun setelah place, pulih setelah kedaluwarsa/batal).
#   - `order`    : baca state efn.online.order + SO + faktur (tanpa kode ambil; hanya boolean ada/tidaknya hash).
#   - `ready`    : aksi staf `action_efn_ready` sebagai user staf (bukan admin) -> kirim outbox sekarang.
#   - `handover` : wizard serah terima sebagai user staf dengan SMOKE_CODE -> kirim outbox sekarang.
#   - `outbox`   : proses outbox event yang jatuh tempo sekarang (tanpa menunggu cron 1 menit).
#   - `ops_cancel`: manajer `smoke_online_mgr` membatalkan SO berbayar dari UI (SEC-95, alasan wajib).
#   - `picking_direct`: staf memvalidasi picking keluar SO online di luar wizard (harus ditolak, SEC-95).
#   - `fake_ctx`: context `efn_online_internal` palsu (staf batal, superuser tulis state) -> ditolak (SEC-104).
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
    # SEC-95 (L-16): batal SO online berbayar dari UI hanya manajer penjualan (login aman -> initiated_by odoo-login:)
    mgr = su["res.users"].with_context(active_test=False).search([("login", "=", "smoke_online_mgr")], limit=1)
    if not mgr:
        mgr = (
            su["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "SMOKE manajer penjualan online",
                    "login": "smoke_online_mgr",
                    "company_id": company.id,
                    "company_ids": [Command.set([company.id])],
                    "group_ids": [
                        Command.set([env.ref("base.group_user").id, env.ref("sales_team.group_sale_manager").id])
                    ],
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
        # G19 (docs/04 §11.1): Odoo 70 menit (BFF 3600 s + 10) + reservasi stok saat place (E-35).
        "payment_timeout_minutes": 70,
        "stock_hold_mode": "confirm",
        "max_order_amount": 5000000,  # L-41 (SEC-131): wajib > 0 di mode confirm
        "max_line_qty": 20,
        "active": True,
    }
    if channel:
        channel.write(vals)
    else:
        channel = Channel.create({**vals, "code": "RTL"})
    # L-37: satu user teknis per kanal -> lepas efn_online_rtl dari kanal lain (DB dev lama: demo brand G19 awal).
    for other in Channel.search([("id", "!=", channel.id), ("user_ids", "in", user.ids)]):
        other.write({"user_ids": [Command.unlink(user.id)]})
    channel.write({"user_ids": [Command.set([user.id])]})
    # E-36: promo kode dev (FIKTIF) pada pricelist SMOKE -> BFF/QA dapat menguji promo_codes (SMOKE10 = diskon 10 %).
    promo = (
        su["loyalty.program"]
        .with_context(active_test=False)
        .search([("name", "=", "SMOKE Promo Kode 10%"), ("company_id", "=", company.id)], limit=1)
    )
    if not promo:
        promo = su["loyalty.program"].create(
            {
                "name": "SMOKE Promo Kode 10%",
                "program_type": "promo_code",
                "trigger": "with_code",
                "applies_on": "current",
                "company_id": company.id,
                "sale_ok": True,
                **({"pos_ok": False} if "pos_ok" in su["loyalty.program"]._fields else {}),
                "pricelist_ids": [Command.set([pricelist.id])],
                "rule_ids": [
                    Command.create(
                        {
                            "mode": "with_code",
                            "code": "SMOKE10",
                            "minimum_amount": 0,
                            "reward_point_amount": 1,
                            "reward_point_mode": "order",
                        }
                    )
                ],
                "reward_ids": [
                    Command.create(
                        {
                            "reward_type": "discount",
                            "discount": 10,
                            "discount_mode": "percent",
                            "discount_applicability": "order",
                            "required_points": 1,
                        }
                    )
                ],
            }
        )
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
        "manager_id": mgr.id,
        "products": ids,
    }
    save_state(st)
    env.cr.commit()
    out(
        ok=True,
        channel=channel.code,
        stock_hold_mode=channel.stock_hold_mode,
        payment_timeout_minutes=channel.payment_timeout_minutes,
        promo_code="SMOKE10",
        promo_program_ok=bool(promo.sale_ok and promo.active),
        company_code=company.efn_brand_code,
        outlet=outlet.code,
        products=ids,
        tech_user_groups_ok=user.has_group("efn_online_order.group_online_channel"),
        staff_is_admin=staff.has_group("base.group_system"),
        tax_label=tax.invoice_label,
        tax_group=tax.tax_group_id.name,
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


def step_free():
    st = load_state()
    su = env(su=True, context={"allowed_company_ids": [st["company_id"]]})
    product = su["product.product"].browse(st["products"][os.environ["SMOKE_PRODUCT"]])
    wh = su["stock.warehouse"].browse(st["warehouse_id"])
    product = product.with_context(warehouse_id=wh.id)
    product.invalidate_recordset(["free_qty", "qty_available"])
    out(ok=True, product_id=product.id, free_qty=int(round(product.free_qty)))


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
        # G19 E-35: mode confirm -> SO `sale` sejak place dan move storable tercadang (`assigned`) sampai keluar.
        "reserved": bool(order)
        and bool(order._efn_reservation_moves())
        and all(m.state == "assigned" for m in order._efn_reservation_moves()),
        "reserved_picked": bool(order) and any(m.picked for m in order._efn_reservation_moves()),
        "reward_lines": len(so.order_line.filtered("reward_id")) if "reward_id" in so.order_line._fields else 0,
        # G19 §11.1: batas bayar Odoo = 70 menit sejak place (BFF 3600 s + 10 menit)
        "hold_minutes": round((order.expires_at - order.create_date).total_seconds() / 60)
        if order and order.expires_at and order.create_date
        else None,
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


def step_ops_cancel():
    """SEC-95: manajer membatalkan SO online berbayar dari UI Odoo (alasan wajib) -> event cancelled initiator=odoo."""
    st = load_state()
    mgr = env["res.users"].browse(st["manager_id"])
    me = env(user=mgr, context={"allowed_company_ids": [st["company_id"]]})
    order = find_order(env(su=True))
    so = me["sale.order"].browse(order.sale_order_id.id)
    error = None
    try:
        if os.environ.get("SMOKE_NOTE", "1") == "1":
            so.write({"efn_online_cancel_note": "Pelanggan minta batal, tiket SMOKE-CS-001"})
        so.action_cancel()
        env.cr.commit()
    except Exception as exc:  # noqa: BLE001 - dilaporkan sebagai hasil smoke
        env.cr.rollback()
        error = type(exc).__name__
    process_outbox()
    order = find_order(env(su=True))
    refund = order.refund_ids.sorted("id")[-1:]
    out(
        ok=error is None,
        error=error,
        refund_initiator=refund.initiator or None,
        refund_amount=refund.amount or None,
        **describe(order),
    )


def step_picking_direct():
    """SEC-95: picking keluar SO online divalidasi di luar wizard serah terima -> ditolak (juga context palsu)."""
    se = staff_env()
    order = find_order(env(su=True))
    ids = order.sale_order_id.picking_ids.filtered(lambda p: p.state not in ("done", "cancel")).ids[:1]
    picking = se["stock.picking"].browse(ids)
    results = []
    for ctx in ({}, {"efn_online_handover_picking_ids": picking.ids, "efn_online_internal": True}):
        try:
            for move in picking.sudo().move_ids:
                move.quantity = move.product_uom_qty
            picking.with_context(**ctx).button_validate()
            env.cr.commit()
            results.append("validated")
        except Exception as exc:  # noqa: BLE001 - dilaporkan sebagai hasil smoke
            env.cr.rollback()
            results.append(type(exc).__name__)
    order = find_order(env(su=True))
    out(ok=bool(ids), attempts=results, picking_state=picking.sudo().state, **describe(order))


def step_fake_ctx():
    """SEC-104 (L-19a): context `efn_online_internal` palsu tidak membuka guard (penanda internal = cr.cache).

    (1) staf non-manajer: action_cancel SO online berbayar; (2) superuser: tulis `state` SO langsung."""
    se = staff_env()
    order = find_order(env(su=True))
    so_id = order.sale_order_id.id
    results = {}
    attempts = {
        # alasan diisi superuser agar yang diuji hanya guard grup/penanda internal, bukan alasan kosong
        "staff_cancel": lambda: (
            env["sale.order"].sudo().browse(so_id).write({"efn_online_cancel_note": "konteks palsu SMOKE-CTX"}),
            se["sale.order"].browse(so_id).with_context(efn_online_internal=True).action_cancel(),
        ),
        "sudo_write_state": lambda: (
            env["sale.order"].sudo().browse(so_id).with_context(efn_online_internal=True).write({"state": "cancel"})
        ),
    }
    for name, fn in attempts.items():
        try:
            fn()
            env.cr.commit()
            results[name] = "allowed"
        except Exception as exc:  # noqa: BLE001 - dilaporkan sebagai hasil smoke
            env.cr.rollback()
            results[name] = type(exc).__name__
    order = find_order(env(su=True))
    out(ok=True, results=results, **describe(order))


def step_apikey():
    st = load_state()
    user = env["res.users"].browse(st["user_id"])
    assert user.has_group("efn_online_order.group_online_channel"), "user teknis efn_online_rtl belum ada"
    # W-11 (G18): batas efektif user teknis = 35 hari (efn_setup_online) -> default 30 seperti rotasi produksi.
    days = int(os.environ.get("SMOKE_KEY_DAYS", "30"))
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
    "free": step_free,
    "order": step_order,
    "ready": step_ready,
    "handover": step_handover,
    "outbox": step_outbox,
    "apikey": step_apikey,
    "ops_cancel": step_ops_cancel,
    "picking_direct": step_picking_direct,
    "fake_ctx": step_fake_ctx,
}[STEP]()
