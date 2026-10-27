# ruff: noqa: F821, E501
# Jalankan: docker exec -i efn-odoo-1 odoo shell -c /etc/odoo/odoo.conf -d efn_odoo --no-http < scripts/dev/seed_demo_g28.py
"""G28 seed demo back-office (DB dev efn_odoo). Idempoten: data ber-tag [DEMO G28] dibuat sekali.

Alur yang dibangun & DIJALANKAN agar layar tidak kosong saat demo:
  FARM (mitra tani) -> GL collection center (PO, terima, QC gate, grade) -> PT antar-PT GL -> PB (PO->SO->faktur->tagihan)
  -> PB Central Kitchen (MO paket + disassembly by-product + yield/varians) -> outlet PB (resupply) -> waste scrap
  -> forecast min/max -> laporan keuangan per PT & konsolidasi.
"""

import logging
from datetime import timedelta

from odoo import Command, fields

_log = logging.getLogger("efn.seed.g28")
TAG = "[DEMO G28]"
out = []


def say(msg):
    out.append(msg)
    print("SEED", msg)


C = env["res.company"].sudo()
gl = C.search([("efn_brand_code", "=", "GL")])
pb = C.search([("efn_brand_code", "=", "PB")])
farm = C.search([("efn_brand_code", "=", "FARM")])
admin = env.ref("base.user_admin")
admin.sudo().write({"company_ids": [Command.link(c.id) for c in (gl | pb | farm)]})
env = env(user=admin.id, context=dict(env.context, allowed_company_ids=(gl | pb | farm).ids, tz="Asia/Jakarta"))
# grup demo untuk admin (By-Products, multi-step routes, Farming manager)
for xid in (
    "mrp.group_mrp_byproducts",
    "stock.group_adv_location",
    "stock.group_stock_multi_warehouses",
    "stock.group_production_lot",
    "efn_agri.group_agri_manager",
):
    g = env.ref(xid, raise_if_not_found=False)
    if g and g not in admin.group_ids:
        admin.sudo().group_ids = [Command.link(g.id)]
kg = env.ref("uom.product_uom_kgm")
unit = env.ref("uom.product_uom_unit")
Product = env["product.product"].sudo()


def product(name, **vals):
    rec = Product.search([("name", "=", name)], limit=1)
    if rec:
        return rec
    base = {"name": name, "type": "consu", "is_storable": True, "company_id": False}
    base.update(vals)
    return Product.create(base)


# ---------------------------------------------------------------- 1. Gudang
W = env["stock.warehouse"].sudo()


def warehouse(company, code, name, **vals):
    wh = W.search([("company_id", "=", company.id), ("code", "=", code)], limit=1)
    if not wh:
        wh = W.with_company(company).create(dict({"name": name, "code": code, "company_id": company.id}, **vals))
    elif vals:
        wh.write(vals)
    return wh


wh_cc = warehouse(gl, "GLCC", "GL Collection Center (DEMO)")
if not wh_cc.efn_agri_collection_center:
    wh_cc.action_efn_agri_enable_qc_gate()
wh_ck = warehouse(pb, "PBCK", "PB Central Kitchen (DEMO)", efn_is_central_kitchen=True, efn_forecast_enabled=True)
wh_ol = warehouse(pb, "PBO1", "PB Outlet Senayan (DEMO)", efn_forecast_enabled=True)
if wh_ol.efn_ck_supplier_wh_id != wh_ck:
    wh_ol.efn_ck_supplier_wh_id = wh_ck
say(
    f"gudang: {wh_cc.code} 3-langkah={wh_cc.reception_steps}, {wh_ck.code} CK, {wh_ol.code} dipasok {wh_ol.efn_ck_supplier_wh_id.code}"
)

# ---------------------------------------------------------------- 2. Antar-PT: user teknis per PT
Users = env["res.users"].sudo().with_context(no_reset_password=True)
ic_group = env.ref("efn_intercompany.group_ic_technical")
for comp in (gl, pb, farm):
    login = f"efn_ic_{comp.efn_brand_code.lower()}"
    user = Users.search([("login", "=", login)], limit=1)
    if not user:
        user = Users.create(
            {
                "name": f"Antar-PT {comp.efn_brand_code} (teknis)",
                "login": login,
                "company_id": comp.id,
                "company_ids": [Command.set(comp.ids)],
                "group_ids": [Command.set([env.ref("base.group_user").id, ic_group.id])],
            }
        )
    comp.sudo().write(
        {
            "efn_ic_user_id": user.id,
            "efn_ic_so_from_po": True,
            "efn_ic_auto_confirm_so": True,
            "efn_ic_bill_from_invoice": True,
        }
    )
gl.sudo().efn_ic_warehouse_id = wh_cc
TP = env["efn.ic.transfer.price"].sudo()
if not TP.search([("company_id", "=", gl.id), ("buyer_company_id", "=", pb.id)]):
    TP.create(
        {
            "company_id": gl.id,
            "buyer_company_id": pb.id,
            "markup_pct": 10,
            "note": "PLACEHOLDER demo - markup final menunggu Finance + opini pajak TP",
        }
    )
say("antar-PT: user teknis GL/PB/FARM, aturan harga transfer GL->PB cost+10%")

# ---------------------------------------------------------------- 3. Produk
cabai = product("Cabai Merah Keriting (DEMO G28)", uom_id=kg.id, tracking="lot", standard_price=28000, list_price=45000)
ayam = product("Ayam Utuh Segar (DEMO G28)", uom_id=kg.id, standard_price=38000, list_price=48000)
tepung = product("Tepung Bumbu (DEMO G28)", uom_id=kg.id, standard_price=15000, list_price=20000)
dada = product("Dada Ayam Fillet (DEMO G28)", uom_id=kg.id, standard_price=0)
paha = product("Paha Ayam (DEMO G28)", uom_id=kg.id, standard_price=0)
sambal = product("Paket Sambal Merah 100 g (DEMO G28)", uom_id=unit.id, standard_price=0, list_price=8000)
geprek = product(
    "Paket Ayam Geprek Siap Goreng (DEMO G28)",
    uom_id=unit.id,
    standard_price=0,
    list_price=25000,
    efn_is_kitting_package=True,
)
for p, comp in ((cabai, gl), (ayam, gl), (tepung, gl)):
    p.with_company(comp).standard_price = p.standard_price or 1
    p.with_company(pb).standard_price = p.standard_price or 1
geprek.product_tmpl_id.efn_ck_production_mode = "both"
sambal.product_tmpl_id.efn_ck_production_mode = "mts"
say("produk demo dibuat (bahan bersama, paket CK mode 'both'/'mts')")

# ---------------------------------------------------------------- 4. Komoditas & mitra tani
Com = env["efn.agri.commodity"].sudo()
com = Com.search([("code", "=", "CBM-G28")], limit=1)
if not com:
    com = Com.create(
        {
            "name": "Cabai merah keriting",
            "code": "CBM-G28",
            "company_id": gl.id,
            "product_ids": [Command.set(cabai.ids)],
            "uom_id": kg.id,
            "floor_price": 25000,
            "shelf_life_days": 7,
            "param_ids": [
                Command.create(
                    {"sequence": 1, "code": "MOIST", "name": "Kadar air", "unit": "%", "has_max": True, "max_value": 85}
                ),
                Command.create(
                    {
                        "sequence": 2,
                        "code": "REJECT",
                        "name": "Reject (busuk/patah)",
                        "unit": "%",
                        "has_max": True,
                        "max_value": 30,
                    }
                ),
                Command.create(
                    {
                        "sequence": 3,
                        "code": "LEN",
                        "name": "Panjang rata-rata",
                        "unit": "cm",
                        "has_min": True,
                        "min_value": 8,
                        "blocking": False,
                    }
                ),
                Command.create({"sequence": 4, "code": "PEST", "name": "Bebas hama & residu", "kind": "pass"}),
            ],
        }
    )
    moist, rej, ln, _pest = com.param_ids.sorted("sequence")
    env["efn.agri.grade"].sudo().create(
        [
            {
                "commodity_id": com.id,
                "sequence": 1,
                "name": "A",
                "price": 32000,
                "rule_ids": [
                    Command.create({"param_id": moist.id, "max_value": 75}),
                    Command.create({"param_id": rej.id, "max_value": 5}),
                    Command.create({"param_id": ln.id, "has_min": True, "min_value": 10, "has_max": False}),
                ],
            },
            {
                "commodity_id": com.id,
                "sequence": 2,
                "name": "B",
                "price": 28000,
                "rule_ids": [Command.create({"param_id": rej.id, "max_value": 15})],
            },
            {
                "commodity_id": com.id,
                "sequence": 3,
                "name": "C",
                "price": 25000,
                "rule_ids": [Command.create({"param_id": rej.id, "max_value": 30})],
            },
            {"commodity_id": com.id, "sequence": 9, "name": "Reject", "price": 0, "is_reject": True},
        ]
    )
Partner = env["res.partner"].sudo()
Farmer = env["efn.agri.farmer"].sudo()
farmers = []
for name, region, ha in (
    ("Poktan Sumber Rejeki (DEMO)", "Kab. Garut", 3.5),
    ("Poktan Tani Makmur (DEMO)", "Kab. Magelang", 2.0),
):
    partner = Partner.search([("name", "=", name)], limit=1) or Partner.create(
        {"name": name, "is_company": True, "supplier_rank": 1}
    )
    f = Farmer.search([("partner_id", "=", partner.id)], limit=1) or Farmer.with_company(gl).create(
        {
            "partner_id": partner.id,
            "company_id": gl.id,
            "region": region,
            "land_area_ha": ha,
            "group_name": name,
            "commodity_ids": [Command.set(com.ids)],
            "contract_start": fields.Date.today() - timedelta(days=60),
        }
    )
    farmers.append(f)
say(f"komoditas {com.code} (4 parameter, grade A/B/C/Reject), mitra tani: {', '.join(f.code for f in farmers)}")

# ---------------------------------------------------------------- 5. Panen -> PO -> terima -> QC
Harvest = env["efn.agri.harvest"].sudo()
today = fields.Date.today()
if not Harvest.search([("farmer_id", "in", [f.id for f in farmers]), ("state", "!=", "planned")]):
    plan = [(farmers[0], 120, 70, 4, 11, True), (farmers[1], 80, 80, 12, 9, True)]
    for f, qty, moist_v, reject_qty, length, pest in plan:
        h = Harvest.with_company(gl).create(
            {
                "farmer_id": f.id,
                "commodity_id": com.id,
                "product_id": cabai.id,
                "expected_date": today - timedelta(days=1),
                "expected_qty": qty,
                "planting_date": today - timedelta(days=95),
                "land_area_ha": f.land_area_ha,
            }
        )
        h.with_company(gl).action_create_purchase()
        po = h.purchase_id.with_company(gl)
        po.picking_type_id = wh_cc.in_type_id
        po.button_confirm()
        rcpt = po.picking_ids.filtered(lambda p: p.picking_type_id == wh_cc.in_type_id)
        for ml in rcpt.move_ids.move_line_ids:
            ml.lot_name = f"CBM-{f.code}-{today.strftime('%y%m%d')}"
        rcpt.move_ids.picked = True
        rcpt.button_validate()
        qc = rcpt.move_ids.move_dest_ids.picking_id
        qc.action_efn_agri_create_inspections()
        insp = qc.efn_agri_inspection_ids
        insp.qty_reject = reject_qty
        for line in insp.line_ids:
            code = line.param_id.code
            if code == "MOIST":
                line.value_num = moist_v
            elif code == "LEN":
                line.value_num = length
            elif code == "PEST":
                line.value_pass = pest
                line.value_set = True
        insp.grade_id = insp.suggested_grade_id
        insp.action_done()
        qc.move_ids.picked = True
        qc.button_validate()
        store = qc.move_ids.move_dest_ids.picking_id
        if store:
            store.move_ids.picked = True
            store.button_validate()
        bill = po.with_context(default_invoice_date=today).action_create_invoice()
        h.state = "harvested"
        say(
            f"panen {f.code}: PO {po.name}, QC {insp.name} grade {insp.grade_id.name} harga {po.order_line.price_unit:,.0f}, reject {reject_qty} kg"
        )
    # satu inspeksi menunggu untuk didemokan langsung
    h = Harvest.with_company(gl).create(
        {
            "farmer_id": farmers[0].id,
            "commodity_id": com.id,
            "product_id": cabai.id,
            "expected_date": today,
            "expected_qty": 60,
        }
    )
    h.with_company(gl).action_create_purchase()
    po = h.purchase_id.with_company(gl)
    po.picking_type_id = wh_cc.in_type_id
    po.button_confirm()
    rcpt = po.picking_ids.filtered(lambda p: p.picking_type_id == wh_cc.in_type_id)
    for ml in rcpt.move_ids.move_line_ids:
        ml.lot_name = f"CBM-{farmers[0].code}-{today.strftime('%y%m%d')}-B"
    rcpt.move_ids.picked = True
    rcpt.button_validate()
    say(f"PO {po.name} sudah diterima, transfer QC menunggu inspeksi (untuk demo langsung)")
    for f in (farmers[1],):
        Harvest.with_company(gl).create(
            {
                "farmer_id": f.id,
                "commodity_id": com.id,
                "product_id": cabai.id,
                "expected_date": today + timedelta(days=7),
                "expected_qty": 100,
            }
        )

# ---------------------------------------------------------------- 6. Stok bahan GL + antar-PT GL -> PB
Quant = env["stock.quant"].sudo()
for p, q in ((ayam, 300), (tepung, 80)):
    if p.with_context(warehouse_id=wh_cc.id).qty_available < q / 2:
        Quant.with_company(gl)._update_available_quantity(p, wh_cc.lot_stock_id, q)
PO = env["purchase.order"].sudo()
ic_po = PO.search([("origin", "=", TAG + " bahan CK")], limit=1)
if not ic_po:
    ic_po = PO.with_company(pb).create(
        {
            "partner_id": gl.partner_id.id,
            "company_id": pb.id,
            "origin": TAG + " bahan CK",
            "picking_type_id": wh_ck.in_type_id.id,
            "order_line": [
                Command.create({"product_id": ayam.id, "product_qty": 60, "price_unit": 1}),
                Command.create({"product_id": tepung.id, "product_qty": 15, "price_unit": 1}),
                Command.create({"product_id": cabai.id, "product_qty": 20, "price_unit": 1}),
            ],
        }
    )
    ic_po.button_confirm()
    so = ic_po.efn_ic_sale_id.sudo()
    pick = so.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
    for pk in pick:
        pk.action_assign()
        for mv in pk.move_ids:
            mv.quantity = mv.product_uom_qty
            mv.picked = True
        pk.button_validate()
    inv = so.with_company(gl)._create_invoices()
    inv.invoice_date = today
    inv.action_post()
    bill = inv.efn_ic_bill_ids
    rcpt = ic_po.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
    for pk in rcpt:
        for mv in pk.move_ids:
            mv.quantity = mv.product_uom_qty
            mv.picked = True
        pk.button_validate()
    if bill and bill.state == "draft":
        bill.sudo().invoice_date = today
        bill.sudo().action_post()
    say(
        f"antar-PT: PO PB {ic_po.name} -> SO GL {so.name} -> faktur {inv.name} -> tagihan PB {bill.name} "
        f"(harga ayam {ic_po.order_line[0].price_unit:,.0f} = biaya+10%)"
    )

# ---------------------------------------------------------------- 7. CK: BoM, MO, disassembly, waste
Bom = env["mrp.bom"].sudo()
bom_geprek = Bom.search([("product_tmpl_id", "=", geprek.product_tmpl_id.id)], limit=1) or Bom.create(
    {
        "product_tmpl_id": geprek.product_tmpl_id.id,
        "product_qty": 1,
        "type": "normal",
        "company_id": pb.id,
        "bom_line_ids": [
            Command.create({"product_id": ayam.id, "product_qty": 0.25, "product_uom_id": kg.id}),
            Command.create({"product_id": tepung.id, "product_qty": 0.05, "product_uom_id": kg.id}),
        ],
    }
)
bom_sambal = Bom.search([("product_tmpl_id", "=", sambal.product_tmpl_id.id)], limit=1) or Bom.create(
    {
        "product_tmpl_id": sambal.product_tmpl_id.id,
        "product_qty": 10,
        "type": "normal",
        "company_id": pb.id,
        "bom_line_ids": [Command.create({"product_id": cabai.id, "product_qty": 1.2, "product_uom_id": kg.id})],
    }
)
bom_dada = Bom.search([("product_tmpl_id", "=", dada.product_tmpl_id.id)], limit=1) or Bom.create(
    {
        "product_tmpl_id": dada.product_tmpl_id.id,
        "product_qty": 0.35,
        "product_uom_id": kg.id,
        "type": "normal",
        "company_id": pb.id,
        "bom_line_ids": [Command.create({"product_id": ayam.id, "product_qty": 1.0, "product_uom_id": kg.id})],
        "byproduct_ids": [
            Command.create({"product_id": paha.id, "product_qty": 0.30, "product_uom_id": kg.id, "cost_share": 40})
        ],
    }
)
MO = env["mrp.production"].sudo().with_company(pb)


def run_mo(bom, qty, produced, consumed, origin):
    mo = MO.search([("origin", "=", origin)], limit=1)
    if mo:
        return mo
    mo = MO.create(
        {
            "product_id": bom.product_tmpl_id.product_variant_id.id,
            "bom_id": bom.id,
            "product_qty": qty,
            "picking_type_id": wh_ck.manu_type_id.id,
            "origin": origin,
            "company_id": pb.id,
        }
    )
    mo.action_confirm()
    mo.qty_producing = produced
    for mv in mo.move_raw_ids:
        mv.quantity = consumed(mv)
        mv.picked = True
    act = mo.button_mark_done()
    for _i in range(3):
        if not isinstance(act, dict) or not act.get("res_model"):
            break
        wiz = env[act["res_model"]].sudo().with_context(**act.get("context", {})).create({})
        if act["res_model"] == "mrp.consumption.warning":
            act = wiz.action_confirm()
        elif act["res_model"] == "mrp.production.backorder":
            act = wiz.action_close_mo()
        else:
            break
    return mo


mo1 = run_mo(bom_geprek, 100, 100, lambda m: m.product_uom_qty, TAG + " MO normal")
mo2 = run_mo(
    bom_geprek, 80, 76, lambda m: m.product_uom_qty * (1.12 if m.product_id == ayam else 1.0), TAG + " MO boros"
)
mo3 = run_mo(bom_sambal, 100, 100, lambda m: m.product_uom_qty, TAG + " MO sambal")
mo4 = run_mo(bom_dada, 7, 7, lambda m: m.product_uom_qty, TAG + " MO disassembly")
for mo in (mo1, mo2, mo3, mo4):
    say(
        f"MO {mo.name} {mo.product_id.name}: yield {mo.efn_yield_pct:.1f}%, varians {mo.efn_variance_value:,.0f}, "
        f"by-product {mo.efn_byproduct_qty:g}, alert={mo.efn_yield_alert}"
    )

# resupply CK -> outlet (reorder rule outlet + replenish)
OP = env["stock.warehouse.orderpoint"].sudo().with_company(pb)
route = wh_ol.efn_ck_supply_route_id
op_ol = OP.search([("warehouse_id", "=", wh_ol.id), ("product_id", "=", geprek.id)], limit=1) or OP.create(
    {
        "warehouse_id": wh_ol.id,
        "location_id": wh_ol.lot_stock_id.id,
        "product_id": geprek.id,
        "company_id": pb.id,
        "product_min_qty": 20,
        "product_max_qty": 60,
        "trigger": "manual",
        "route_id": route.id,
    }
)
op_ol.route_id = route
if (
    not env["stock.picking"]
    .sudo()
    .search(
        [("origin", "ilike", op_ol.name or "OP/"), ("location_dest_id", "child_of", wh_ol.view_location_id.id)], limit=1
    )
):
    op_ol.action_replenish()
transfers = (
    env["stock.picking"]
    .sudo()
    .search([("picking_type_id", "=", wh_ck.out_type_id.id), ("state", "not in", ("done", "cancel"))])
)
for pk in transfers:
    pk.action_assign()
    for mv in pk.move_ids:
        mv.quantity = mv.product_uom_qty
        mv.picked = True
    pk.button_validate()
ins = (
    env["stock.picking"]
    .sudo()
    .search([("picking_type_id", "=", wh_ol.in_type_id.id), ("state", "not in", ("done", "cancel"))])
)
for pk in ins:
    pk.action_assign()
    for mv in pk.move_ids:
        mv.quantity = mv.product_uom_qty
        mv.picked = True
    pk.button_validate()
say(
    f"resupply CK->outlet: {len(transfers)} kirim, {len(ins)} terima; stok outlet geprek "
    f"{geprek.with_context(warehouse_id=wh_ol.id).qty_available:g}"
)
OP.search([("warehouse_id", "=", wh_ck.id), ("product_id", "=", sambal.id)], limit=1) or OP.create(
    {
        "warehouse_id": wh_ck.id,
        "location_id": wh_ck.lot_stock_id.id,
        "product_id": sambal.id,
        "company_id": pb.id,
        "product_min_qty": 30,
        "product_max_qty": 80,
        "trigger": "manual",
    }
)

# penjualan outlet 3 minggu (keluar stok -> histori forecast), backdated
cust = env.ref("stock.stock_location_customers")
Move = env["stock.move"].sudo().with_company(pb)
if not Move.search([("origin", "=", TAG + " jual")], limit=1):
    need = 0
    sales = []
    for i in range(21, 0, -1):
        d = today - timedelta(days=i)
        q = 14 if d.weekday() >= 5 else 9
        if 25 <= d.day or d.day <= 5:
            q += 2
        sales.append((d, q))
        need += q
    Quant.with_company(pb)._update_available_quantity(geprek, wh_ol.lot_stock_id, need)
    for d, q in sales:
        mv = Move.create(
            {
                "product_id": geprek.id,
                "product_uom_qty": q,
                "location_id": wh_ol.lot_stock_id.id,
                "location_dest_id": cust.id,
                "origin": TAG + " jual",
                "company_id": pb.id,
            }
        )
        mv._action_confirm()
        mv._action_assign()
        mv.quantity = q
        mv.picked = True
        mv._action_done()
        env.cr.execute("UPDATE stock_move SET date = %s WHERE id = %s", (f"{d} 05:00:00", mv.id))
    say(f"histori penjualan outlet: 21 hari, total {need} paket")

# waste
Scrap = env["stock.scrap"].sudo().with_company(pb)
if not Scrap.search([("origin", "=", TAG)], limit=1):
    tags = {x: env.ref(f"efn_ck_production.scrap_reason_{x}") for x in ("susut", "rusak", "gagal_masak", "kedaluwarsa")}
    for prod, qty, loc, tag in (
        (ayam, 2.5, wh_ck.lot_stock_id, "susut"),
        (geprek, 4, wh_ck.lot_stock_id, "gagal_masak"),
        (geprek, 3, wh_ol.lot_stock_id, "kedaluwarsa"),
        (tepung, 0.8, wh_ck.lot_stock_id, "rusak"),
    ):
        s = Scrap.create(
            {
                "product_id": prod.id,
                "scrap_qty": qty,
                "location_id": loc.id,
                "origin": TAG,
                "company_id": pb.id,
                "scrap_reason_tag_ids": [Command.set(tags[tag].ids)],
            }
        )
        s.do_scrap()
    say("waste: 4 scrap beralasan (susut, gagal masak, kedaluwarsa, rusak)")

# ---------------------------------------------------------------- 8. Forecast
F = env["efn.demand.forecast"].sudo().with_company(pb)
fc = F._efn_generate(wh_ol, geprek, history_days=21, lead_days=1, review_days=3)
fc2 = F._efn_generate(wh_ck, sambal, history_days=21, lead_days=2, review_days=7)
say(
    f"forecast {wh_ol.code} geprek: dasar {fc.base_daily:.1f}/hari, faktor {fc.season_factor:.2f}, usulan min {fc.proposed_min:g} max {fc.proposed_max:g} (menunggu persetujuan planner)"
)

# ---------------------------------------------------------------- 9. Ringkasan laporan keuangan
E = env["efn.financial.report.engine"].sudo()
bs = E._efn_balance_sheet(gl | pb | farm, today)
pl = E._efn_profit_loss(gl | pb | farm, today.replace(month=1, day=1), today)
say(
    f"konsolidasi GL+PB+FARM: aset {bs['totals'].get('asset', 0):,.0f}, selisih neraca {bs['totals']['difference']:,.0f}, "
    f"laba bersih {pl['totals']['net_profit']:,.0f}, baris eliminasi {len(pl['eliminations']) + len(bs['eliminations'])}"
)
env.cr.commit()
print("SEED_DONE")
