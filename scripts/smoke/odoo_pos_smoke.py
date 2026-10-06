# Smoke E2E sisi Odoo (dev saja) — dijalankan lewat `odoo shell` oleh scripts/smoke/run-e2e-smoke.sh.
#
# Bukan modul Odoo dan bukan test unit: skrip ini memanggil metode server yang sama dengan frontend POS
# (`pos.config.efn_otp_request/verify`, `pos.payment.method.efn_gateway_create/status`, `pos.order.sync_from_ui`)
# terhadap BFF NYATA (HTTPS + CA internal, service key dari /run/secrets). Tidak ada secret di file ini.
# Nomor HP berasal dari env (dibuat acak per run oleh orkestrator; nomor fiktif, bukan data pribadi).
#
# Env: SMOKE_STEP (setup|otp_request|otp_verify|sync|gw_create|gw_simulate|gw_status|erasure_cron|partner|
#      audit|audit_partner|balance)
#      dan parameter per langkah (lihat fungsi). Keluaran: satu baris `SMOKE_RESULT <json>`.
# Dipakai hanya di DB dev (`efn_odoo` dev); ditolak bila APP_ENV bukan dev.
# R2 (docs/04 §8.13): skrip ini tidak pernah menaruh nomor HP/token di URL. Saldo poin (bila dicek) wajib lewat
# `efn.bff.client.post("/v1/pos/loyalty/balance", {"member_ref": ...})`; `efn.bff.client.get` menolak PII di query.
# ruff: noqa: F821  (variabel `env` disediakan odoo shell)
import json
import os
import random

from odoo import Command, fields

if (os.environ.get("APP_ENV") or "").lower() != "dev":
    raise SystemExit("odoo_pos_smoke hanya untuk APP_ENV=dev")

STEP = os.environ["SMOKE_STEP"]
STATE_FILE = "/tmp/efn_smoke_state.json"  # noqa: S108 - container dev, hanya id record (tanpa PII)
PREFIX = "SMOKE"


def out(**kw):
    print("SMOKE_RESULT " + json.dumps(kw, default=str))


def load_state():
    with open(STATE_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def ctx_env():
    st = load_state()
    company = env["res.company"].browse(st["company_id"])
    admin = env.ref("base.user_admin")
    return env(user=admin, context={"allowed_company_ids": [company.id]}), st


def step_setup():
    company = env["res.company"].search([("efn_brand_code", "=", "FNB")], limit=1)
    assert company, "company FNB (efn_setup_company) tidak ada"
    admin = env.ref("base.user_admin")
    if company not in admin.company_ids:
        admin.write({"company_ids": [Command.link(company.id)]})
    e = env(user=admin, context={"allowed_company_ids": [company.id]})
    su = e(su=True)

    Journal = su["account.journal"]
    cash_j = Journal.search([("type", "=", "cash"), ("company_id", "=", company.id)], limit=1)
    bank_j = Journal.search([("type", "=", "bank"), ("company_id", "=", company.id)], limit=1)
    assert cash_j and bank_j, "jurnal cash/bank FNB tidak ada (CoA l10n_id?)"

    PM = su["pos.payment.method"]
    cash_pm = PM.search([("name", "=", f"{PREFIX} Cash"), ("company_id", "=", company.id)], limit=1) or PM.create(
        {"name": f"{PREFIX} Cash", "journal_id": cash_j.id, "company_id": company.id}
    )
    vals_q = {"name": f"{PREFIX} QRIS", "journal_id": bank_j.id, "company_id": company.id}
    if "payment_method_type" in PM._fields:
        vals_q["payment_method_type"] = "terminal"
    vals_q["use_payment_terminal"] = "efn_gateway"
    qris_pm = PM.search([("name", "=", f"{PREFIX} QRIS"), ("company_id", "=", company.id)], limit=1) or PM.create(
        vals_q
    )

    Product = su["product.product"]
    product = Product.search([("name", "=", f"{PREFIX} Nasi Goreng"), ("company_id", "=", company.id)], limit=1)
    if not product:
        product = Product.create(
            {
                "name": f"{PREFIX} Nasi Goreng",
                "type": "consu",
                "available_in_pos": True,
                "list_price": 50000.0,
                "taxes_id": [Command.clear()],
                "company_id": company.id,
            }
        )

    Config = su["pos.config"]
    config = Config.search([("name", "=", f"{PREFIX} POS FNB"), ("company_id", "=", company.id)], limit=1)
    if not config:
        config = Config.create(
            {
                "name": f"{PREFIX} POS FNB",
                "company_id": company.id,
                "payment_method_ids": [Command.set([cash_pm.id, qris_pm.id])],
            }
        )

    Program = su["loyalty.program"]
    program = Program.search([("name", "=", f"{PREFIX} Promo OTP"), ("company_id", "=", company.id)], limit=1)
    if not program:
        program = Program.create(
            {
                "name": f"{PREFIX} Promo OTP",
                "company_id": company.id,
                "program_type": "promotion",
                "trigger": "auto",
                "applies_on": "current",
                "pos_ok": True,
                "pos_config_ids": [Command.set(config.ids)],
                "efn_requires_otp": True,
                "rule_ids": [Command.create({"minimum_amount": 0})],
                "reward_ids": [
                    Command.create(
                        {
                            "reward_type": "discount",
                            "discount": 10,
                            "discount_mode": "percent",
                            "discount_applicability": "order",
                        }
                    )
                ],
            }
        )
    su["ir.config_parameter"].set_param("efn.pos.otp_required_for_promo", "True")

    if not config.current_session_id:
        config.with_user(admin).open_ui()
        config.current_session_id.set_opening_control(0, None)

    partners = {}
    for key in ("A", "B", "C", "D", "E"):
        phone = os.environ.get(f"SMOKE_PHONE_{key}")
        if not phone:
            continue
        partner = su["res.partner"].create({"name": f"{PREFIX} Member {key}", "phone": phone})
        partners[key] = partner.id

    state = {
        "company_id": company.id,
        "config_id": config.id,
        "session_id": config.current_session_id.id,
        "cash_pm": cash_pm.id,
        "qris_pm": qris_pm.id,
        "product_id": product.id,
        "program_id": program.id,
        "reward_id": program.reward_ids[:1].id,
        "partners": partners,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    env.cr.commit()
    out(
        ok=True,
        config_id=config.id,
        session_state=config.current_session_id.state,
        admin_pos_user=admin.has_group("point_of_sale.group_pos_user"),
        partners=list(partners),
    )


def step_otp_request():
    e, st = ctx_env()
    config = e["pos.config"].browse(st["config_id"])
    partner_id = st["partners"][os.environ["SMOKE_PARTNER"]]
    res = config.efn_otp_request(os.environ["SMOKE_ORDER_UUID"], float(os.environ["SMOKE_AMOUNT"]), partner_id)
    env.cr.commit()
    out(**res)


def step_otp_verify():
    e, st = ctx_env()
    config = e["pos.config"].browse(st["config_id"])
    res = config.efn_otp_verify(os.environ["SMOKE_ORDER_UUID"], os.environ["SMOKE_CODE"])
    env.cr.commit()
    out(**res)


def _order_data(e, st, uuid, partner_id, qty, with_reward, payment):
    product = e["product.product"].browse(st["product_id"])
    reward = e["loyalty.reward"].browse(st["reward_id"])
    session = e["pos.session"].browse(st["session_id"])
    config = session.config_id
    price = product.lst_price
    lines = [
        (
            0,
            0,
            {
                "id": random.randint(1, 1_000_000),  # noqa: S311
                "pack_lot_ids": [],
                "price_unit": price,
                "product_id": product.id,
                "price_subtotal": price * qty,
                "price_subtotal_incl": price * qty,
                "qty": qty,
                "tax_ids": [(6, 0, [])],
            },
        )
    ]
    if with_reward:
        disc = -round(price * qty * 0.10, 2)
        lines.append(
            (
                0,
                0,
                {
                    "id": random.randint(1, 1_000_000),  # noqa: S311
                    "pack_lot_ids": [],
                    "price_unit": disc,
                    "product_id": reward.discount_line_product_id.id,
                    "price_subtotal": disc,
                    "price_subtotal_incl": disc,
                    "qty": 1,
                    "tax_ids": [(6, 0, [])],
                    "is_reward_line": True,
                    "reward_id": reward.id,
                },
            )
        )
    total = sum(line[2]["price_subtotal_incl"] for line in lines)
    pay = {"amount": total, "name": fields.Datetime.now(), "payment_method_id": st["cash_pm"]}
    if payment:
        # {"method": "qris", "transaction_id": <bff payment_id>, "uuid": <payment_uuid>, "amount"?: n}
        if payment.pop("method", None) == "qris":
            pay["payment_method_id"] = st["qris_pm"]
        pay.update(payment)
        pay["amount"] = float(payment.get("amount", total))
    return {
        "amount_paid": pay["amount"],
        "amount_return": 0,
        "amount_tax": 0,
        "amount_total": total,
        "date_order": fields.Datetime.to_string(fields.Datetime.now()),
        "fiscal_position_id": False,
        "pricelist_id": config.pricelist_id.id,
        "name": f"Order {uuid}",
        "last_order_preparation_change": "{}",
        "lines": lines,
        "partner_id": partner_id,
        "session_id": session.id,
        "payment_ids": [(0, 0, pay)],
        "uuid": uuid,
        "user_id": e.uid,
        "to_invoice": False,
        "state": "paid",
    }, total


def step_sync():
    e, st = ctx_env()
    uuid = os.environ["SMOKE_ORDER_UUID"]
    pkey = os.environ.get("SMOKE_PARTNER")
    partner_id = st["partners"][pkey] if pkey else False
    qty = int(os.environ.get("SMOKE_QTY", "1"))
    with_reward = os.environ.get("SMOKE_REWARD", "1") == "1"
    payment = json.loads(os.environ["SMOKE_PAYMENT"]) if os.environ.get("SMOKE_PAYMENT") else None
    data, total = _order_data(e, st, uuid, partner_id, qty, with_reward, payment)
    try:
        e["pos.order"].sync_from_ui([data])
        env.cr.commit()
    except Exception as exc:  # noqa: BLE001 - laporan smoke
        env.cr.rollback()
        out(ok=False, error=type(exc).__name__, message=str(exc)[:300], total=total)
        return
    order = e["pos.order"].search([("uuid", "=", uuid)], limit=1)
    challenge = e["efn.pos.otp.challenge"].sudo().search([("order_ref", "=", uuid)], order="id desc", limit=1)
    out(
        ok=bool(order),
        total=total,
        state=order.state,
        gate_state=order.efn_otp_gate_state,
        gate_note=order.efn_otp_gate_note,
        challenge_state=challenge.state,
        challenge_reason=challenge.failure_reason,
        reward_lines=len(order.lines.filtered("is_reward_line")),
        token_cleared=not challenge.discount_token,
    )


def step_gw_create():
    e, st = ctx_env()
    pm = e["pos.payment.method"].browse(st["qris_pm"])
    res = pm.efn_gateway_create(
        os.environ["SMOKE_ORDER_UUID"],
        os.environ["SMOKE_PAYMENT_UUID"],
        float(os.environ["SMOKE_AMOUNT"]),
        st["config_id"],
    )
    env.cr.commit()
    res.pop("qr_image", None)
    if res.get("qr_string"):
        res["qr_string"] = res["qr_string"][:24] + "..."
    out(**res)


def step_gw_simulate():
    # Endpoint dev-only BFF (docs/04 §8.11) memakai service key Odoo -> lewat efn.bff.client yang sama.
    # payment_id BFF tidak dikirim ke browser sebelum paid -> dibaca dari intent (sisi server).
    intent = env["efn.pos.payment.intent"].sudo().browse(int(os.environ["SMOKE_INTENT_ID"]))
    payment_id = intent.bff_payment_id
    resp = env["efn.bff.client"].post(
        f"/v1/pos/payments/{payment_id}/simulate", {"status": os.environ.get("SMOKE_SIM_STATUS", "paid")}
    )
    out(http=resp.status, **resp.data)


def step_gw_status():
    e, st = ctx_env()
    pm = e["pos.payment.method"].browse(st["qris_pm"])
    res = pm.efn_gateway_status(int(os.environ["SMOKE_INTENT_ID"]))
    env.cr.commit()
    out(**res)


def step_erasure_cron():
    cron = env.ref("efn_privacy.ir_cron_efn_privacy_pull_erasures", raise_if_not_found=False)
    if not cron:
        cron = env["ir.cron"].search([("cron_name", "ilike", "erasure")], limit=1) or env["ir.cron"].search(
            [("name", "ilike", "erasure")], limit=1
        )
    assert cron, "cron erasure efn_privacy tidak ditemukan"
    cron.method_direct_trigger()
    env.cr.commit()
    out(ok=True, cron=cron.name)


def step_partner():
    # Status partner untuk verifikasi DSAR (tanpa mencetak nomor mentah).
    member_hash = os.environ["SMOKE_MEMBER_HASH"]
    partners = env["res.partner"].with_context(active_test=False).search([("efn_member_hash", "=", member_hash)])
    pid = int(os.environ.get("SMOKE_PARTNER_ID") or 0)
    p = env["res.partner"].with_context(active_test=False).browse(pid).exists() if pid else env["res.partner"]
    # SEC-31 (docs/04 §8.13): partner teranonim hanya punya flag `efn_erased`; tidak ada request_id /
    # member_hash yang tersisa. Penanda sementara idempotensi complete wajib kosong setelah complete.
    Pending = env["efn.privacy.erasure.pending"] if "efn.privacy.erasure.pending" in env else None
    out(
        by_hash=len(partners),
        partner_exists=bool(p),
        name=p.name if p else None,
        phone_present=bool(p.phone) if p else None,
        active=p.active if p else None,
        hash_present=bool(p.efn_member_hash) if p else None,
        erased_flag=bool(p.efn_erased) if p and "efn_erased" in p._fields else None,
        legacy_request_field="efn_erasure_request_id" in env["res.partner"]._fields,
        pending_markers=Pending.sudo().search_count([]) if Pending is not None else None,
    )


def _audit_logs(model_name, res_id):
    return env["efn.audit.log"].sudo().search([("model_name", "=", model_name), ("res_id", "=", res_id)])


def step_audit():
    # Gelombang 10 (ADR-031, efn_audit): perubahan harga produk & HP/email partner tercatat; HP/email TER-MASK.
    # Partner A (dipakai skenario c) mendapat HP baru; partner E (subjek DSAR) hanya email agar member_hash tetap.
    e, st = ctx_env()
    su = e(su=False)
    product = su["product.product"].browse(st["product_id"])
    tmpl = product.product_tmpl_id
    before = _audit_logs("product.template", tmpl.id).ids
    old_price = tmpl.list_price
    tmpl.write({"list_price": old_price + 1000})
    tmpl.write({"list_price": old_price})
    price_lines = (
        _audit_logs("product.template", tmpl.id)
        .filtered(lambda log: log.id not in before)
        .line_ids.filtered(lambda line: line.field_name == "list_price")
    )
    new_phone = os.environ["SMOKE_NEW_PHONE"]
    pa = su["res.partner"].browse(st["partners"]["A"])
    before_a = _audit_logs("res.partner", pa.id).ids
    pa.write({"phone": new_phone})
    phone_lines = (
        _audit_logs("res.partner", pa.id)
        .filtered(lambda log: log.id not in before_a)
        .line_ids.filtered(lambda line: line.field_name == "phone")
    )
    masked = bool(phone_lines) and all(line.is_masked and "****" in (line.new_value or "") for line in phone_lines)
    digits = "".join(ch for ch in new_phone if ch.isdigit())
    raw_hidden = all(digits[3:-3] not in (line.new_value or "") for line in phone_lines)
    pe = su["res.partner"].browse(st["partners"]["E"])
    pe.write({"email": "smoke-e@example.invalid"})
    env.cr.commit()
    out(
        price_logged=len(price_lines) >= 2,
        price_values=[(line.old_value, line.new_value) for line in price_lines[:2]],
        phone_logged=len(phone_lines) >= 1,
        phone_masked=masked,
        phone_raw_hidden=raw_hidden,
        phone_new_value=phone_lines[:1].new_value if phone_lines else None,
        partner_e_logs=len(_audit_logs("res.partner", pe.id)),
        partner_e_id=pe.id,
    )


def step_audit_partner():
    # Setelah erasure: log res.partner subjek terhapus + baris ref lintas model kosong + satu log purge tanpa id/nama.
    st = load_state()
    pid = st["partners"]["E"]
    Line = env["efn.audit.log.line"].sudo()

    def refs(ref):
        parts = (ref or "").split(",")
        return parts[0] == "res.partner" and str(pid) in parts[1:]

    ref_lines = len(
        Line.search(["|", ("old_ref", "=like", "res.partner,%"), ("new_ref", "=like", "res.partner,%")]).filtered(
            lambda line: refs(line.old_ref) or refs(line.new_ref)
        )
    )
    purge = (
        env["efn.audit.log"]
        .sudo()
        .search([("operation", "=", "purge"), ("model_name", "=", "res.partner")], order="id desc", limit=1)
    )
    out(
        partner_logs=len(_audit_logs("res.partner", pid)),
        ref_lines=ref_lines,
        purge_note=purge.note,
        purge_res_id=purge.res_id or None,
        pending_markers=env["efn.privacy.erasure.pending"].sudo().search_count([]),
    )


def step_balance():
    # R2 (docs/04 §8.13): saldo POS lewat POST body (service key Odoo); GET lama sudah dihapus -> 404.
    from odoo.addons.efn_core.models.efn_bff_client import partner_member_ref

    st = load_state()
    partner = env["res.partner"].sudo().browse(st["partners"]["E"])
    client = env["efn.bff.client"]
    resp = client.post("/v1/pos/loyalty/balance", {"member_ref": partner_member_ref(partner)[0]})
    old = client.get("/v1/pos/loyalty/balance")
    bad = client.post("/v1/pos/loyalty/balance", {"member_ref": "bukan-nomor"})
    out(
        http=resp.status,
        points_balance=resp.data.get("points_balance"),
        keys=sorted(resp.data),
        old_get_http=old.status,
        invalid_http=bad.status,
    )


{
    "audit": step_audit,
    "audit_partner": step_audit_partner,
    "balance": step_balance,
    "setup": step_setup,
    "otp_request": step_otp_request,
    "otp_verify": step_otp_verify,
    "sync": step_sync,
    "gw_create": step_gw_create,
    "gw_simulate": step_gw_simulate,
    "gw_status": step_gw_status,
    "erasure_cron": step_erasure_cron,
    "partner": step_partner,
}[STEP]()
