# Smoke E2E sisi Odoo untuk penjualan online G17 (dev saja) — dijalankan lewat `odoo shell` oleh
# scripts/smoke/run-e2e-smoke.sh tahap `o` (docs/04 §10, ADR-034/ADR-019 Proposed).
#
# Bukan modul Odoo dan bukan test unit. Skrip ini:
#   - `setup`    : konfigurasi kanal GL dev (data FIKTIF "SMOKE", idempoten) setara demo efn_setup_online: pembulatan
#                  Rupiah, gudang outlet SMKOL, jurnal titipan bank, pricelist IDR, user teknis `efn_online_gl`
#                  (TANPA API key — key dibuat lewat runbook odoo-online-api-key §1), kanal GL + outlet TOKO-01,
#                  4 produk online (PPN l10n_id), stok awal, dan user staf outlet `smoke_online_staff`.
#                  G19: kanal batas bayar 70 menit + `stock_hold_mode=confirm` (SO `sale` + stok tercadang sejak
#                  place), promo kode dev `SMOKE10` (diskon 10 %, pricelist SMOKE), satu user teknis per kanal (L-37).
#                  G20 (docs/04 §12.1, L-48): kode kanal = kode PT -> kanal `GL` (PT Grand Lucky, xmlid company_rtl),
#                  semula `RTL`; user teknis `efn_online_gl` (semula `efn_online_rtl`).
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
import random
from datetime import datetime, timedelta

from odoo import Command, fields

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
        [("name", "=", "SMOKE Online GL"), ("company_id", "=", company.id)], limit=1
    ) or su["product.pricelist"].create(
        {"name": "SMOKE Online GL", "currency_id": env.ref("base.IDR").id, "company_id": company.id}
    )
    group = env.ref("efn_online_order.group_online_channel")
    user = su["res.users"].with_context(active_test=False).search([("login", "=", "efn_online_gl")], limit=1)
    if not user:
        user = (
            su["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Kanal online GL (user teknis)",
                    "login": "efn_online_gl",
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
    channel = Channel.search([("code", "=", "GL")], limit=1)
    vals = {
        "name": "Super-App Grand Lucky (SMOKE dev)",
        "brand_code": "GL",
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
        channel = Channel.create({**vals, "code": "GL"})
    # L-37: satu user teknis per kanal -> lepas efn_online_gl dari kanal lain (bila DB dev memakai user di kanal lain).
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
        # G20 E-45: mode bayar + ada/tidaknya digest kode bayar (digest tidak pernah dicetak)
        "payment_mode": order.payment_mode or None,
        "counter_hash_present": bool(order.sudo().counter_code_hash),
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
    assert user.has_group("efn_online_order.group_online_channel"), "user teknis efn_online_gl belum ada"
    # W-11 (G18): batas efektif user teknis = 35 hari (efn_setup_online) -> default 30 seperti rotasi produksi.
    days = int(os.environ.get("SMOKE_KEY_DAYS", "30"))
    exp = datetime.utcnow().replace(microsecond=0) + timedelta(days=days)
    name = "efn-online-GL-" + datetime.utcnow().strftime("%Y%m%d%H%M")
    key = env["res.users.apikeys"].with_user(user)._generate(None, name, exp)
    env.cr.commit()
    old = os.umask(0o077)
    try:
        with open("/tmp/efn_online_key", "w", encoding="utf-8") as fh:  # noqa: S108 - diambil orkestrator lalu dihapus
            fh.write(key)
    finally:
        os.umask(old)
    out(ok=len(key) == 40, name=name, expires=exp.isoformat(), length=len(key))


# --------------------------------------------------------------------------------------------------------------
# G20 (docs/04 §12.2, L-55..L-58): bayar pesanan online di kasir POS. Kanal GL hasil `setup`.
#   counter_setup  : kanal GL `payment_modes=qris,counter` (menyiapkan data kliring PT), kasir `smoke_counter_cashier`
#                    (POS user, bukan admin), POS config di gudang outlet SMKOL + metode tunai, sesi dibuka.
#   counter_lookup : kasir -> pos.config.efn_online_counter_lookup(SMOKE_CODE); keluaran hanya kunci hasil.
#   counter_pay    : kasir menyinkronkan order POS pelunasan (SMOKE_PART, SMOKE_ORDER_UUID) lalu outbox diproses.
#   counter_refund : kasir me-refund order POS pelunasan (SMOKE_PART) -> event `refund_settled` (L-58).
def counter_cashier_env():
    st = load_state()
    cashier = env["res.users"].browse(st["cashier_id"])
    return st, env(user=cashier, context={"allowed_company_ids": [st["company_id"]]})


def counter_pos_order_data(ce, st, uuid, lines, total, label_payment):
    config = ce["pos.config"].browse(st["pos_config_id"])
    return {
        "amount_paid": total,
        "amount_return": 0,
        "amount_tax": 0,
        "amount_total": total,
        "date_order": fields.Datetime.to_string(fields.Datetime.now()),
        "fiscal_position_id": False,
        "pricelist_id": config.pricelist_id.id,
        "name": f"Order {uuid}",
        "last_order_preparation_change": "{}",
        "lines": lines,
        "partner_id": False,
        "session_id": st["pos_session_id"],
        "payment_ids": [(0, 0, {"amount": total, "name": fields.Datetime.now(), "payment_method_id": label_payment})],
        "uuid": uuid,
        "user_id": ce.uid,
        "to_invoice": False,
        "state": "paid",
    }


def step_counter_setup():
    st = load_state()
    company = env["res.company"].browse(st["company_id"])
    admin = env.ref("base.user_admin")
    su = env(user=admin, context={"allowed_company_ids": [company.id]}, su=True)
    channel = su["efn.online.channel"].browse(st["channel_id"])
    channel.write({"payment_modes": "qris,counter"})
    wh = su["stock.warehouse"].browse(st["warehouse_id"])
    cashier = su["res.users"].with_context(active_test=False).search([("login", "=", "smoke_counter_cashier")], limit=1)
    if not cashier:
        cashier = (
            su["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "SMOKE kasir bayar di kasir",
                    "login": "smoke_counter_cashier",
                    "company_id": company.id,
                    "company_ids": [Command.set([company.id])],
                    "group_ids": [
                        Command.set([env.ref("base.group_user").id, env.ref("point_of_sale.group_pos_user").id])
                    ],
                }
            )
        )
    Journal = su["account.journal"]
    cash_j = Journal.search([("type", "=", "cash"), ("company_id", "=", company.id)], limit=1) or Journal.create(
        {"name": "SMOKE Kas Kasir GL", "type": "cash", "code": "SMKCS", "company_id": company.id}
    )
    PM = su["pos.payment.method"]
    cash_pm = PM.search([("name", "=", "SMOKE Cash GL"), ("company_id", "=", company.id)], limit=1) or PM.create(
        {"name": "SMOKE Cash GL", "journal_id": cash_j.id, "company_id": company.id}
    )
    Config = su["pos.config"]
    config = Config.search([("name", "=", "SMOKE POS GL online"), ("company_id", "=", company.id)], limit=1)
    if not config:
        config = Config.create(
            {
                "name": "SMOKE POS GL online",
                "company_id": company.id,
                "payment_method_ids": [Command.set([cash_pm.id])],
                "picking_type_id": wh.pos_type_id.id,
            }
        )
    if not config.current_session_id:
        config.with_user(admin).open_ui()
        config.current_session_id.set_opening_control(0, None)
    st.update(
        {
            "cashier_id": cashier.id,
            "pos_config_id": config.id,
            "pos_session_id": config.current_session_id.id,
            "pos_cash_pm": cash_pm.id,
        }
    )
    save_state(st)
    env.cr.commit()
    out(
        ok=True,
        payment_modes=channel.payment_modes,
        counter_enabled=bool(config.efn_online_counter_enabled),
        counter_product=bool(company.efn_online_counter_product_id),
        counter_journal=bool(company.efn_online_counter_journal_id),
        session_state=config.current_session_id.state,
        cashier_is_admin=cashier.has_group("base.group_system"),
        cashier_pos_user=cashier.has_group("point_of_sale.group_pos_user"),
    )


def step_counter_lookup():
    st, ce = counter_cashier_env()
    config = ce["pos.config"].browse(st["pos_config_id"])
    try:
        res = config.efn_online_counter_lookup(os.environ.get("SMOKE_CODE", ""))
    except Exception as exc:  # noqa: BLE001 - dilaporkan sebagai hasil smoke
        env.cr.rollback()
        out(ok=False, error=type(exc).__name__)
        return
    env.cr.commit()  # setiap lookup (berhasil/gagal) adalah jejak untuk rate limit + klaim sync
    out(
        ok=bool(res.get("order_label")) and not res.get("error_code"),
        keys=sorted(res),
        order_label=res.get("order_label"),
        amount_payable=res.get("amount_payable"),
        error_code=res.get("error_code"),
    )


def step_counter_pay():
    st, ce = counter_cashier_env()
    su = env(su=True)
    online = find_order(su)
    company = su["res.company"].browse(st["company_id"])
    uuid = os.environ["SMOKE_ORDER_UUID"]
    amount = online.amount_payable
    line = {
        "id": random.randint(1, 1_000_000),  # noqa: S311 - id sementara klien POS
        "pack_lot_ids": [],
        "price_unit": amount,
        "product_id": company.efn_online_counter_product_id.id,
        "price_subtotal": amount,
        "price_subtotal_incl": amount,
        "qty": 1,
        "tax_ids": [(6, 0, [])],
        "efn_online_order_label": online.name,
    }
    data = counter_pos_order_data(ce, st, uuid, [(0, 0, line)], amount, st["pos_cash_pm"])
    error = message = None
    try:
        ce["pos.order"].sync_from_ui([data])
        env.cr.commit()
    except Exception as exc:  # noqa: BLE001 - dilaporkan sebagai hasil smoke
        env.cr.rollback()
        error, message = type(exc).__name__, str(exc)[:160]
    process_outbox()
    order = find_order(env(su=True))
    pos_order = env(su=True)["pos.order"].search([("uuid", "=", uuid)], limit=1)
    clearing = env(su=True)["account.payment"].search([("journal_id", "=", company.efn_online_counter_journal_id.id)])
    out(
        ok=error is None,
        error=error,
        message=message,
        pos_state=pos_order.state or None,
        invoice_payment_state=order.invoice_id.payment_state if "invoice_id" in order._fields else None,
        clearing_payments=len(clearing),
        paid_events=len(order.event_ids.filtered(lambda e: e.event == "paid")),
        **describe(order),
    )


def step_counter_refund():
    st, ce = counter_cashier_env()
    su = env(su=True)
    online = find_order(su)
    settlement = su["efn.online.counter.settlement"].search([("online_order_id", "=", online.id)], limit=1)
    origin = settlement.pos_order_id.lines[:1]
    amount = origin.price_subtotal_incl
    uuid = os.environ["SMOKE_ORDER_UUID"]
    line = {
        "id": random.randint(1, 1_000_000),  # noqa: S311 - id sementara klien POS
        "pack_lot_ids": [],
        "price_unit": amount,
        "product_id": origin.product_id.id,
        "price_subtotal": -amount,
        "price_subtotal_incl": -amount,
        "qty": -1,
        "tax_ids": [(6, 0, [])],
        "refunded_orderline_id": origin.id,
    }
    data = counter_pos_order_data(ce, st, uuid, [(0, 0, line)], -amount, st["pos_cash_pm"])
    data["is_refund"] = True
    error = message = None
    try:
        ce["pos.order"].sync_from_ui([data])
        env.cr.commit()
    except Exception as exc:  # noqa: BLE001 - dilaporkan sebagai hasil smoke
        env.cr.rollback()
        error, message = type(exc).__name__, str(exc)[:160]
    process_outbox()
    order = find_order(env(su=True))
    out(
        ok=error is None,
        error=error,
        message=message,
        refund_states=sorted(order.refund_ids.mapped("state")),
        refund_settled_events=len(order.event_ids.filtered(lambda e: e.event == "refund_settled")),
        outbox=sorted(f"{e.event}:{e.state}" for e in order.event_ids),
    )


# ---------------------------------------------------------------------------------------------------------------
# G26 transfer bank + bukti (docs/04 §17, tahap `t`):
#   transfer_setup  : rekening bank FIKTIF milik partner PT GL + jurnal bank SMKBT, kanal GL
#                     `payment_modes=qris,bank_transfer` + jurnal transfer, user `smoke_payment_verifier` (grup
#                     Verifikator Pembayaran saja, bukan kasir/gudang -> SoD SOD-PAY-01/02).
#   transfer_proof  : baca bukti SMOKE_PART (state, lampiran JPEG tanpa EXIF, salinan antrean); foto tidak dicetak.
#   transfer_decide : keputusan lewat wizard SEBAGAI Verifikator (SMOKE_DECISION approve|reject, SMOKE_REASON,
#                     SMOKE_AMOUNT_DELTA) -> kirim outbox sekarang.
#   transfer_outsider: staf outlet (bukan Verifikator) membaca bukti / memutuskan -> ditolak.
SMOKE_BANK_ACC = "8800123456"  # FIKTIF (dev)


def verifier_env():
    st = load_state()
    user = env["res.users"].browse(st["verifier_id"])
    return st, env(user=user, context={"allowed_company_ids": [st["company_id"]]})


def proofs_of(su):
    order = find_order(su)
    return order, su["efn.online.transfer.proof"].search([("order_id", "=", order.id)], order="id")


def step_transfer_setup():
    st = load_state()
    company = env["res.company"].browse(st["company_id"])
    su = env(su=True, context={"allowed_company_ids": [company.id]})
    bank = su["res.bank"].search([("name", "=", "SMOKE Bank Demo")], limit=1) or su["res.bank"].create(
        {"name": "SMOKE Bank Demo"}
    )
    PB = su["res.partner.bank"].with_context(active_test=False)
    account = PB.search([("acc_number", "=", SMOKE_BANK_ACC), ("partner_id", "=", company.partner_id.id)], limit=1)
    if not account:
        account = PB.create(
            {
                "acc_number": SMOKE_BANK_ACC,
                "partner_id": company.partner_id.id,
                "bank_id": bank.id,
                "acc_holder_name": "PT SMOKE Grand Lucky",
                "company_id": company.id,
            }
        )
    Journal = su["account.journal"]
    journal = Journal.search([("code", "=", "SMKBT"), ("company_id", "=", company.id)], limit=1) or Journal.create(
        {
            "name": "SMOKE Transfer Bank Online GL",
            "type": "bank",
            "code": "SMKBT",
            "company_id": company.id,
            "bank_account_id": account.id,
        }
    )
    channel = su["efn.online.channel"].browse(st["channel_id"])
    # Rekening baru = untrusted (CE anti-phishing): kanal tidak boleh menampilkannya / menyalakan bank_transfer.
    # Trust/untrust hanya boleh oleh user ber-hak (CE melarang OdooBot/superuser shell) -> sebagai admin (= Finance).
    finance = env(user=env.ref("base.user_admin"), context={"allowed_company_ids": [company.id]})
    acc_fin = finance["res.partner.bank"].browse(account.id)
    acc_fin.write({"allow_out_payment": False})
    untrusted_hidden = channel._efn_online_transfer_account() is None
    rejected = False
    try:
        with env.cr.savepoint():
            channel.write({"efn_transfer_journal_id": journal.id, "payment_modes": "qris,bank_transfer"})
    except Exception:  # noqa: BLE001 - ValidationError yang diharapkan
        rejected = True
    # Finance memverifikasi rekening PT (Rekening Bank -> Send Money / trusted), baru kanal dinyalakan.
    acc_fin.write({"allow_out_payment": True})
    channel.invalidate_recordset()
    channel.write({"efn_transfer_journal_id": journal.id, "payment_modes": "qris,bank_transfer"})
    # Outlet TOKO-01 bisa berasal dari data demo efn_setup_online (gudang OL01, bukan SMKOL): stok smoke di gudang
    # outlet yang sebenarnya dipakai quote/place.
    outlet = channel.outlet_ids.filtered(lambda o: o.code == "TOKO-01")[:1]
    for key in ("rice", "oil"):
        set_stock(
            su, su["product.product"].browse(st["products"][key]), outlet.warehouse_id, 1000.0, only_if_below=True
        )
    group = env.ref("efn_online_transfer.group_payment_verifier")
    Users = su["res.users"].with_context(active_test=False)
    verifier = Users.search([("login", "=", "smoke_payment_verifier")], limit=1)
    if not verifier:
        verifier = Users.with_context(no_reset_password=True).create(
            {
                "name": "SMOKE Verifikator Pembayaran",
                "login": "smoke_payment_verifier",
                "company_id": company.id,
                "company_ids": [Command.set([company.id])],
                "group_ids": [Command.set([env.ref("base.group_user").id, group.id])],
            }
        )
    st.update({"verifier_id": verifier.id, "transfer_journal_id": journal.id})
    save_state(st)
    env.cr.commit()
    out(
        ok=True,
        payment_modes=channel.payment_modes,
        account=channel._efn_online_transfer_account(),
        verifier_is_verifier=verifier.has_group("efn_online_transfer.group_payment_verifier"),
        verifier_is_admin=verifier.has_group("base.group_system"),
        verifier_pos_user=verifier.has_group("point_of_sale.group_pos_user"),
        verifier_stock_user=verifier.has_group("stock.group_stock_user"),
        outlet_warehouse=outlet.warehouse_id.code,
        untrusted_hidden=untrusted_hidden,
        untrusted_channel_rejected=rejected,
        sod_rules=sorted(
            su["efn.sod.rule"].search([("code", "like", "SOD-PAY-%")]).mapped(lambda r: f"{r.code}:{r.mode}")
        ),
    )


def step_transfer_proof():
    su = env(su=True)
    order, proofs = proofs_of(su)
    rows = []
    for p in proofs:
        att = p.attachment_id
        raw = base64.b64decode(att.datas) if att and att.datas else b""
        rows.append(
            {
                "state": p.state,
                "duplicate_suspect": p.duplicate_suspect,
                "amount_due": p.amount_due,
                "transfer_ref": p.transfer_ref,
                "account_label": p.account_label,
                "reject_reason": p.reject_reason or None,
                "att_model": att.res_model if att else None,
                "att_public": bool(att.public) if att else None,
                "att_mimetype": att.mimetype if att else None,
                "jpeg": raw[:3] == b"\xff\xd8\xff",
                "exif": b"Exif\x00" in raw[:4096],
                "bytes": len(raw),
            }
        )
    out(ok=bool(order), order_state=order.state, proofs=rows, n=len(rows))


def step_transfer_decide():
    st, ve = verifier_env()
    su = env(su=True)
    order, proofs = proofs_of(su)
    proof = proofs.filtered(lambda p: p.state == "awaiting_verification")[-1:]
    decision = os.environ.get("SMOKE_DECISION", "approve")
    error = message = None
    try:
        vals = {"proof_id": proof.id, "decision": decision}
        if decision == "reject":
            vals["reject_reason"] = os.environ.get("SMOKE_REASON", "unreadable")
        else:
            vals.update(
                {
                    "bank_date": fields.Date.context_today(su["res.users"]),
                    "bank_amount": proof.amount_due + float(os.environ.get("SMOKE_AMOUNT_DELTA", "0")),
                    "bank_ref": "SMOKE-MUTASI-" + proof.proof_id[:8],
                }
            )
        ve["efn.online.transfer.decide"].create(vals).action_confirm()
        env.cr.commit()
    except Exception as exc:  # noqa: BLE001 - dilaporkan sebagai hasil smoke
        env.cr.rollback()
        error, message = type(exc).__name__, str(exc)[:160]
    process_outbox()
    su = env(su=True)
    order = find_order(su)
    journal = su["account.journal"].browse(st["transfer_journal_id"])
    pays = (
        su["account.payment"]
        .search([("journal_id", "=", journal.id)])
        .filtered(lambda p: order.sale_order_id.invoice_ids & p.reconciled_invoice_ids)
    )
    out(
        ok=error is None,
        error=error,
        message=message,
        proof_state=proof.state if proof else None,
        decided_by_verifier=bool(proof) and proof.decided_by.id == st["verifier_id"],
        transfer_journal_payments=len(pays),
        invoice_payment_state=sorted(set(order.sale_order_id.invoice_ids.mapped("payment_state"))),
        **describe(order),
    )


def step_transfer_outsider():
    se = staff_env()
    su = env(su=True)
    _, proofs = proofs_of(su)
    proof = proofs[-1:]
    read_err = decide_err = None
    try:
        se["efn.online.transfer.proof"].browse(proof.id).read(["amount_due", "image"])
    except Exception as exc:  # noqa: BLE001
        read_err = type(exc).__name__
    env.cr.rollback()
    try:
        se["efn.online.transfer.proof"].browse(proof.id)._efn_decide("reject", reason="unreadable")
    except Exception as exc:  # noqa: BLE001
        decide_err = type(exc).__name__
    env.cr.rollback()
    out(ok=bool(read_err and decide_err), read_error=read_err, decide_error=decide_err)


{
    "counter_setup": step_counter_setup,
    "counter_lookup": step_counter_lookup,
    "counter_pay": step_counter_pay,
    "counter_refund": step_counter_refund,
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
    "transfer_setup": step_transfer_setup,
    "transfer_proof": step_transfer_proof,
    "transfer_decide": step_transfer_decide,
    "transfer_outsider": step_transfer_outsider,
}[STEP]()
