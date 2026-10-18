# Smoke E2E sisi Odoo (dev saja) — dijalankan lewat `odoo shell` oleh scripts/smoke/run-e2e-smoke.sh.
#
# Bukan modul Odoo dan bukan test unit: skrip ini memanggil metode server yang sama dengan frontend POS
# (`pos.config.efn_otp_request/verify`, `pos.payment.method.efn_gateway_create/status`, `pos.order.sync_from_ui`)
# terhadap BFF NYATA (HTTPS + CA internal, service key dari /run/secrets). Tidak ada secret di file ini.
# Nomor HP berasal dari env (dibuat acak per run oleh orkestrator; nomor fiktif, bukan data pribadi).
#
# Env: SMOKE_STEP (setup|otp_request|otp_verify|sync|gw_create|gw_simulate|gw_status|erasure_cron|partner|
#      audit|audit_partner|balance|loy_config|loy_otp_request|loy_otp_verify|loy_reserve|loy_sync|loy_outbox|
#      loy_partner|loy_balance|loy_release|loy_cancel|loy_token_probe|loy_refund|rpc_users|loy_refund_noreceipt)
#      G13c: `rpc_users` (SEC-58, user + kasir ber-2FA untuk odoo_rpc_smoke.py), `loy_refund_noreceipt` (SEC-59).
#      Langkah `loy_*` (Gelombang 13b, butuh modul efn_pos_loyalty_ledger terpasang di DB dev): urutan
#      loy_config -> loy_otp_request -> loy_otp_verify (SMOKE_CODE) -> loy_reserve (SMOKE_POINTS) -> loy_sync
#      -> loy_outbox. Dirangkai di run-e2e-smoke.sh skenario `l` (Gelombang 13, QA).
#      dan parameter per langkah (lihat fungsi). Keluaran: satu baris `SMOKE_RESULT <json>`.
# Dipakai hanya di DB dev (`efn_odoo` dev); ditolak bila APP_ENV bukan dev.
# R2 (docs/04 §8.13): skrip ini tidak pernah menaruh nomor HP/token di URL. Saldo poin (bila dicek) wajib lewat
# `efn.bff.client._efn_post("/v1/pos/loyalty/balance", {"member_ref": ...})`;
# `efn.bff.client._efn_get` menolak PII di query. SEC-58: klien BFF hanya metode privat (`_efn_*`).
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


def _bff_method(name):
    # SEC-58 (r6) mengganti `post`/`get` publik menjadi `_efn_post`/`_efn_get`; smoke harus jalan di kedua versi
    # efn_core (DB dev bisa tertinggal satu upgrade dari kode).
    client = env["efn.bff.client"]
    return getattr(client, f"_efn_{name}", None) or getattr(client, name)


def bff_post(path, payload):
    return _bff_method("post")(path, payload)


def bff_get(path):
    return _bff_method("get")(path)


def load_state():
    with open(STATE_FILE, encoding="utf-8") as fh:
        return json.load(fh)


RETRYABLE = ("SerializationFailure", "TransactionRollbackError", "LockNotAvailable")


def sync_with_retry(e, data, attempts=4):
    """`pos.order.sync_from_ui` + commit; ulangi pada konflik serialisasi seperti `odoo.service.model.retrying`."""
    import time

    for attempt in range(attempts):
        try:
            e["pos.order"].sync_from_ui([data])
            env.cr.commit()
            return
        except Exception as exc:
            env.cr.rollback()
            if type(exc).__name__ not in RETRYABLE or attempt == attempts - 1:
                raise
            time.sleep(0.5 * (attempt + 1))


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
    # G18 (E-31): metode non-tunai tanpa terminal (EDC manual) untuk uji refund ke metode asal.
    edc_j = Journal.search([("code", "=", "SMEDC"), ("company_id", "=", company.id)], limit=1) or Journal.create(
        {"name": f"{PREFIX} EDC", "type": "bank", "code": "SMEDC", "company_id": company.id}
    )
    edc_pm = PM.search([("name", "=", f"{PREFIX} EDC"), ("company_id", "=", company.id)], limit=1) or PM.create(
        {"name": f"{PREFIX} EDC", "journal_id": edc_j.id, "company_id": company.id}
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

    # G18 (W-03): produk ber-PB1 10 % (exclude) dengan harga yang menghasilkan pecahan sen di IDR 0,01.
    taxed = Product.search([("name", "=", f"{PREFIX} Roti PB1"), ("company_id", "=", company.id)], limit=1)
    if not taxed:
        taxed = Product.create(
            {
                "name": f"{PREFIX} Roti PB1",
                "type": "consu",
                "available_in_pos": True,
                "list_price": 12345.0,
                "taxes_id": [Command.set([env.ref("efn_setup_company.tax_pb1_10_fnb").id])],
                "company_id": company.id,
            }
        )

    categ_x = su["product.category"].search([("name", "=", f"{PREFIX} Tanpa Poin")], limit=1) or su[
        "product.category"
    ].create({"name": f"{PREFIX} Tanpa Poin", "efn_loyalty_earn_excluded": True})
    excluded = Product.search([("name", "=", f"{PREFIX} Rokok Uji"), ("company_id", "=", company.id)], limit=1)
    if not excluded:
        excluded = Product.create(
            {
                "name": f"{PREFIX} Rokok Uji",
                "type": "consu",
                "available_in_pos": True,
                "list_price": 50000.0,
                "taxes_id": [Command.clear()],
                "categ_id": categ_x.id,
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
                "payment_method_ids": [Command.set([cash_pm.id, qris_pm.id, edc_pm.id])],
                # E-30: redeem aktif saat dibuat -> efn_setup_pos menautkan metode Poin EFN PT (sesi belum buka).
                "efn_loyalty_redeem_enabled": True,
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
        "edc_pm": edc_pm.id,
        "points_pm": config._efn_loyalty_points_method_record().id,
        "product_id": product.id,
        "taxed_product_id": taxed.id,
        "excluded_product_id": excluded.id,
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
        points_pm=config._efn_loyalty_points_method_record().id,
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
    resp = bff_post(f"/v1/pos/payments/{payment_id}/simulate", {"status": os.environ.get("SMOKE_SIM_STATUS", "paid")})
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
    resp = bff_post("/v1/pos/loyalty/balance", {"member_ref": partner_member_ref(partner)[0]})
    old = bff_get("/v1/pos/loyalty/balance")
    bad = bff_post("/v1/pos/loyalty/balance", {"member_ref": "bukan-nomor"})
    out(
        http=resp.status,
        points_balance=resp.data.get("points_balance"),
        keys=sorted(resp.data),
        old_get_http=old.status,
        invalid_http=bad.status,
    )


def step_loy_config():
    e, st = ctx_env()
    config = e["pos.config"].browse(st["config_id"])
    config.write({"efn_loyalty_earn_enabled": True, "efn_loyalty_redeem_enabled": True})
    env.cr.commit()
    method = config._efn_loyalty_points_method_record()
    out(
        ok=True,
        points_pm=method.id,
        points_pm_type=method.payment_method_type,
        points_pm_cash=method.is_cash_count,
        legacy_product_active=bool(e.ref("efn_pos_loyalty_ledger.product_efn_points").active),
    )


def step_loy_otp_request():
    e, st = ctx_env()
    config = e["pos.config"].browse(st["config_id"])
    partner_id = st["partners"][os.environ["SMOKE_PARTNER"]]
    res = config.efn_otp_request(
        os.environ["SMOKE_ORDER_UUID"], float(os.environ["SMOKE_AMOUNT"]), partner_id, "pos_redeem"
    )
    env.cr.commit()
    out(**res)


def step_loy_otp_verify():
    e, st = ctx_env()
    config = e["pos.config"].browse(st["config_id"])
    res = config.efn_otp_verify(os.environ["SMOKE_ORDER_UUID"], os.environ["SMOKE_CODE"], "pos_redeem")
    env.cr.commit()
    out(**res)


def step_loy_reserve():
    e, st = ctx_env()
    config = e["pos.config"].browse(st["config_id"])
    partner_id = st["partners"][os.environ["SMOKE_PARTNER"]]
    res = config.efn_loyalty_redeem_reserve(
        os.environ["SMOKE_ORDER_UUID"],
        partner_id,
        int(os.environ.get("SMOKE_POINTS", "50")),
        float(os.environ["SMOKE_AMOUNT"]),
    )
    env.cr.commit()
    out(**res)


def step_loy_sync():
    """Order berbayar: redeem = pembayaran metode Poin EFN senilai reservasi aktif (E-30), sisa tunai."""
    e, st = ctx_env()
    uuid = os.environ["SMOKE_ORDER_UUID"]
    partner_id = st["partners"][os.environ["SMOKE_PARTNER"]]
    if os.environ.get("SMOKE_PRODUCT") == "excluded":
        st = {**st, "product_id": st["excluded_product_id"]}
    data, total = _order_data(e, st, uuid, partner_id, int(os.environ.get("SMOKE_QTY", "1")), False, None)
    draft = os.environ.get("SMOKE_DRAFT") == "1"
    reservation = e["efn.pos.loyalty.reservation"].sudo().search([("order_uuid", "=", uuid)], limit=1)
    value = reservation.value_idr if reservation else 0
    if os.environ.get("SMOKE_POINTS_VALUE"):
        value = int(os.environ["SMOKE_POINTS_VALUE"])  # SEC-125: nominal sengaja beda dari reservasi
    if value:
        data["payment_ids"][0][2]["amount"] = total - value
        data["payment_ids"].insert(
            0, (0, 0, {"amount": value, "name": fields.Datetime.now(), "payment_method_id": st["points_pm"]})
        )
    if draft:
        # Order belum dibayar (mis. disimpan lalu dibatalkan kasir) -> jalur release via cancel; baris Poin EFN tetap.
        data.update({"state": "draft", "amount_paid": value})
        data["payment_ids"] = [p for p in data["payment_ids"] if p[2]["payment_method_id"] == st["points_pm"]]
    try:
        sync_with_retry(e, data)
    except Exception as exc:  # noqa: BLE001 - laporan smoke
        out(ok=False, error=type(exc).__name__, message=str(exc)[:300])
        return
    order = e["pos.order"].search([("uuid", "=", uuid)], limit=1)
    outbox = e["efn.pos.loyalty.outbox"].sudo().search([("order_id", "=", order.id)])
    out(
        ok=True,
        total=order.amount_total,
        state=order.state,
        points_payments=len(order._efn_points_payments()),
        points_paid=order._efn_points_paid(),
        loyalty_state=order.efn_loyalty_state,
        outbox=sorted(outbox.mapped("kind")),
    )


def step_loy_outbox():
    e, _st = ctx_env()
    uuid = os.environ["SMOKE_ORDER_UUID"]
    e["efn.pos.loyalty.outbox"]._efn_process_due()
    env.cr.commit()
    order = e["pos.order"].search([("uuid", "=", uuid)], limit=1)
    outbox = e["efn.pos.loyalty.outbox"].sudo().search([("order_id", "=", order.id)])
    flat = {}
    for rec in outbox:
        flat.update(
            {
                f"{rec.kind}_state": rec.state,
                f"{rec.kind}_error": rec.last_error or "",
                f"{rec.kind}_delta": rec.points_delta,
                f"{rec.kind}_attempts": rec.attempt_count,
            }
        )
        if rec.kind == "reverse":
            flat["reverse_points_restored_value"] = rec.points_restored_value
    reservation = e["efn.pos.loyalty.reservation"].sudo().search([("order_uuid", "=", uuid)], limit=1)
    out(
        order_state=order.state,
        loyalty_state=order.efn_loyalty_state,
        loyalty_note=(order.efn_loyalty_note or "")[:80],
        points_earned=order.efn_loyalty_points_earned,
        reservation_state=reservation.state if reservation else None,
        **flat,
    )


# --------------------------------------------------------------------------------------------------------------
# Gelombang 13 (QA): langkah tambahan skenario `l` run-e2e-smoke.sh (loyalty POS end-to-end terhadap BFF nyata).
def step_loy_partner():
    """Partner fiktif tambahan (kunci SMOKE_KEY) untuk skenario loyalty; ditambahkan ke state."""
    st = load_state()
    key = os.environ["SMOKE_KEY"]
    partner = env["res.partner"].sudo().create({"name": f"{PREFIX} Member {key}", "phone": os.environ["SMOKE_PHONE"]})
    st["partners"][key] = partner.id
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(st, fh)
    env.cr.commit()
    out(ok=True, key=key, partner_id=partner.id, hash_present=bool(partner.efn_member_hash))


def step_loy_balance():
    e, st = ctx_env()
    config = e["pos.config"].browse(st["config_id"])
    res = config.efn_loyalty_balance(st["partners"][os.environ["SMOKE_PARTNER"]])
    out(**res)


def step_loy_release():
    """Kasir membatalkan penukaran (dialog Batal/Back) -> RPC release."""
    e, st = ctx_env()
    uuid = os.environ["SMOKE_ORDER_UUID"]
    config = e["pos.config"].browse(st["config_id"])
    res = config.efn_loyalty_redeem_release(uuid)
    env.cr.commit()
    reservation = e["efn.pos.loyalty.reservation"].sudo().search([("order_uuid", "=", uuid)], limit=1)
    out(ok=res.get("ok"), reservation_state=reservation.state, note=reservation.note)


def step_loy_cancel():
    """Order draft berpoin dibatalkan (`action_pos_order_cancel`) -> outbox redeem_release -> BFF."""
    e, _st = ctx_env()
    uuid = os.environ["SMOKE_ORDER_UUID"]
    order = e["pos.order"].search([("uuid", "=", uuid)], limit=1)
    order.action_pos_order_cancel()
    env.cr.commit()
    e["efn.pos.loyalty.outbox"]._efn_process_due()
    env.cr.commit()
    reservation = e["efn.pos.loyalty.reservation"].sudo().search([("order_uuid", "=", uuid)], limit=1)
    outbox = e["efn.pos.loyalty.outbox"].sudo().search([("reservation_id", "=", reservation.id)])
    out(
        order_state=order.state,
        reservation_state=reservation.state,
        release_state=",".join(outbox.filtered(lambda r: r.kind == "redeem_release").mapped("state")),
    )


def step_loy_token_probe():
    """Token OTP dipakai lintas purpose LANGSUNG ke BFF (melewati filter purpose Odoo).

    SMOKE_PURPOSE = purpose challenge yang sudah verified untuk order ini. Token promo -> reserve
    (harus 409 token_invalid); token redeem -> consume promo (harus 409).
    """
    e, st = ctx_env()
    uuid = os.environ["SMOKE_ORDER_UUID"]
    purpose = os.environ["SMOKE_PURPOSE"]
    config = e["pos.config"].browse(st["config_id"])
    challenge = e["efn.pos.otp.challenge"]._efn_find_valid_verified(config, uuid, purpose=purpose)
    partner = e["res.partner"].sudo().browse(st["partners"][os.environ["SMOKE_PARTNER"]])
    from odoo.addons.efn_core.models.efn_bff_client import partner_member_ref

    member = partner_member_ref(partner)[0]
    amount = int(float(os.environ["SMOKE_AMOUNT"]))
    if not challenge or not challenge.discount_token:
        out(ok=False, error="no_verified_challenge")
        return
    if purpose == "pos_promo":
        resp = bff_post(
            "/v1/pos/loyalty/redeem/reserve",
            {
                "discount_token": challenge.discount_token,
                "order_ref": uuid,
                "member_ref": member,
                "points": int(os.environ.get("SMOKE_POINTS", "50")),
                "amount_total": amount,
            },
        )
    else:
        resp = bff_post(
            "/v1/pos/discount-token/consume",
            {
                "discount_token": challenge.discount_token,
                "order_ref": uuid,
                "member_ref": member,
                "amount_total": amount,
            },
        )
    error = resp.data.get("error")
    code = error.get("code") if isinstance(error, dict) else resp.data.get("reason")
    out(http=resp.status, code=code)


def step_loy_refund():
    """Refund sebagian order asal berpoin (SMOKE_ORIG_UUID, SMOKE_QTY unit barang) dengan baris
    penyesuaian Poin EFN sesuai `efn_loyalty_refund_preview` (perilaku layar refund POS)."""
    e, st = ctx_env()
    uuid = os.environ["SMOKE_ORDER_UUID"]
    qty = int(os.environ.get("SMOKE_QTY", "1"))
    original = e["pos.order"].search([("uuid", "=", os.environ["SMOKE_ORIG_UUID"])], limit=1)
    config = e["pos.config"].browse(st["config_id"])
    goods = original.lines[:1]
    preview = config.efn_loyalty_refund_preview(original.id, [[goods.id, qty]])
    session = e["pos.session"].browse(st["session_id"])
    price = goods.price_unit
    lines = [
        (
            0,
            0,
            {
                "id": random.randint(1, 1_000_000),  # noqa: S311
                "pack_lot_ids": [],
                "price_unit": price,
                "product_id": goods.product_id.id,
                "price_subtotal": -price * qty,
                "price_subtotal_incl": -price * qty,
                "qty": -qty,
                "tax_ids": [(6, 0, [])],
                "refunded_orderline_id": goods.id,
            },
        )
    ]
    adjust = preview.get("value_idr") or 0
    total = -price * qty
    payments = [(0, 0, {"amount": total + adjust, "name": fields.Datetime.now(), "payment_method_id": st["cash_pm"]})]
    if adjust:
        payments.insert(
            0, (0, 0, {"amount": -adjust, "name": fields.Datetime.now(), "payment_method_id": st["points_pm"]})
        )
    data = {
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
        "partner_id": original.partner_id.id,
        "session_id": session.id,
        "payment_ids": payments,
        "uuid": uuid,
        "user_id": e.uid,
        "to_invoice": False,
        "state": "paid",
        "is_refund": True,
    }
    try:
        sync_with_retry(e, data)
    except Exception as exc:  # noqa: BLE001 - laporan smoke
        out(ok=False, error=type(exc).__name__, message=str(exc)[:300], preview=preview)
        return
    order = e["pos.order"].search([("uuid", "=", uuid)], limit=1)
    outbox = e["efn.pos.loyalty.outbox"].sudo().search([("order_id", "=", order.id), ("kind", "=", "reverse")])
    out(
        ok=True,
        state=order.state,
        is_refund=order.is_refund,
        refunded_order=order.refunded_order_id.uuid,
        preview_ratio=preview.get("ratio"),
        preview_value=adjust,
        total=total,
        loyalty_state=order.efn_loyalty_state,
        reverse_ratio=outbox.refund_ratio,
        reverse_eligible=outbox.eligible_amount,
        reverse_state=outbox.state,
        points_restored_value=outbox.points_restored_value,
        points_paid=order._efn_points_paid(),
    )


# --------------------------------------------------------------------------------------------------------------
# Gelombang 13c (QA/devsecops): langkah tambahan regresi SEC-58 / SEC-59 / SEC-61.
RPC_CRED_FILE = "/tmp/efn_smoke_rpc.json"  # noqa: S108 - container dev, 0600, dibaca lewat pipe oleh orkestrator


def step_rpc_users():
    """User internal biasa + kasir POS (FNB) dengan password & TOTP acak per run (2FA wajib).

    Kredensial ditulis ke RPC_CRED_FILE (0600) di container Odoo dan TIDAK dicetak; orkestrator mem-pipe file ini
    ke odoo_rpc_smoke.py (stdin) lalu menghapusnya.
    """
    import base64
    import secrets

    st = load_state()
    company = env["res.company"].browse(st["company_id"])
    spec = {
        "smoke_rpc_internal": ["base.group_user"],
        "smoke_rpc_cashier": [
            "base.group_user",
            "point_of_sale.group_pos_user",
            "efn_setup_pos.group_efn_cashier_contact_creation",
        ],
    }
    Users = env["res.users"].sudo().with_context(no_reset_password=True)
    creds, ids = {}, {}
    for login, groups in spec.items():
        pw = secrets.token_urlsafe(24)
        secret = base64.b32encode(secrets.token_bytes(20)).decode()
        vals = {
            "name": f"{PREFIX} RPC {login.rsplit('_', 1)[-1]}",
            "login": login,
            "company_id": company.id,
            "company_ids": [Command.set([company.id])],
            "group_ids": [Command.set([env.ref(g).id for g in groups])],
        }
        user = Users.search([("login", "=", login)], limit=1)
        if user:
            user.write(vals)
        else:
            user = Users.create(vals)
        user.password = pw
        user.totp_secret = secret
        creds[login] = {"password": pw, "totp": secret}
        ids[login] = user.id
    env.cr.commit()
    fd = os.open(RPC_CRED_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(creds, fh)
    out(ok=True, users=ids)


def step_loy_refund_noreceipt():
    """SEC-59: retur tanpa struk (qty < 0 tanpa `refunded_orderline_id`) untuk member -> review, tanpa earn/reverse."""
    e, st = ctx_env()
    uuid = os.environ["SMOKE_ORDER_UUID"]
    qty = int(os.environ.get("SMOKE_QTY", "1"))
    partner_id = st["partners"][os.environ["SMOKE_PARTNER"]]
    product = e["product.product"].browse(st["product_id"])
    config = e["pos.config"].browse(st["config_id"])
    price = product.list_price
    total = -price * qty
    data = {
        "amount_paid": total,
        "amount_return": 0,
        "amount_tax": 0,
        "amount_total": total,
        "date_order": fields.Datetime.to_string(fields.Datetime.now()),
        "fiscal_position_id": False,
        "pricelist_id": config.pricelist_id.id,
        "name": f"Order {uuid}",
        "last_order_preparation_change": "{}",
        "lines": [
            (
                0,
                0,
                {
                    "id": random.randint(1, 1_000_000),  # noqa: S311
                    "pack_lot_ids": [],
                    "price_unit": price,
                    "product_id": product.id,
                    "price_subtotal": total,
                    "price_subtotal_incl": total,
                    "qty": -qty,
                    "tax_ids": [(6, 0, [])],
                },
            )
        ],
        "partner_id": partner_id,
        "session_id": st["session_id"],
        "payment_ids": [(0, 0, {"amount": total, "name": fields.Datetime.now(), "payment_method_id": st["cash_pm"]})],
        "uuid": uuid,
        "user_id": e.uid,
        "to_invoice": False,
        "state": "paid",
        "is_refund": True,
    }
    try:
        e["pos.order"].sync_from_ui([data])
        env.cr.commit()
    except Exception as exc:  # noqa: BLE001 - laporan smoke
        env.cr.rollback()
        out(ok=False, error=type(exc).__name__, message=str(exc)[:300])
        return
    e["efn.pos.loyalty.outbox"]._efn_process_due()
    env.cr.commit()
    order = e["pos.order"].search([("uuid", "=", uuid)], limit=1)
    outbox = e["efn.pos.loyalty.outbox"].sudo().search([("order_id", "=", order.id)])
    out(
        ok=True,
        state=order.state,
        loyalty_state=order.efn_loyalty_state,
        outbox=",".join(sorted(outbox.mapped("kind"))) or "none",
        note=(order.efn_loyalty_note or "")[:120],
        points_earned=order.efn_loyalty_points_earned,
    )


# --------------------------------------------------------------------------------------------------------------
# Gelombang 18 (QA): Rupiah Rp1 (W-03), redeem = alat bayar (E-30), refund ke metode asal (E-31), integritas
# pembayaran r11 (SEC-113/116), daftar member 17+ (E-34). Order dibangun dengan harga & pajak dihitung SERVER
# (compute_all, mata uang company) seperti frontend POS.
def _taxed_lines(e, st, qty, refunded_line=None):
    product = e["product.product"].browse(st["taxed_product_id"])
    company = e["res.company"].browse(st["company_id"])
    taxes = product.taxes_id.filtered(lambda t: t.company_id == company)
    res = taxes.compute_all(product.lst_price, company.currency_id, abs(qty), product=product)
    sign = -1 if qty < 0 else 1
    vals = {
        "id": random.randint(1, 1_000_000),  # noqa: S311
        "pack_lot_ids": [],
        "price_unit": product.lst_price,
        "product_id": product.id,
        "price_subtotal": sign * res["total_excluded"],
        "price_subtotal_incl": sign * res["total_included"],
        "qty": qty,
        "tax_ids": [(6, 0, taxes.ids)],
    }
    if refunded_line:
        vals["refunded_orderline_id"] = refunded_line.id
    return [(0, 0, vals)], sign * res["total_included"], sign * (res["total_included"] - res["total_excluded"])


def _ui(e, st, uuid, lines, total, tax, payments, partner_id=False, refund=False, amount_return=0.0):
    session = e["pos.session"].browse(st["session_id"])
    data = {
        "amount_paid": sum(p[2]["amount"] for p in payments),
        "amount_return": amount_return,
        "amount_tax": tax,
        "amount_total": total,
        "date_order": fields.Datetime.to_string(fields.Datetime.now()),
        "fiscal_position_id": False,
        "pricelist_id": session.config_id.pricelist_id.id,
        "name": f"Order {uuid}",
        "last_order_preparation_change": "{}",
        "lines": lines,
        "partner_id": partner_id,
        "session_id": session.id,
        "payment_ids": payments,
        "uuid": uuid,
        "user_id": e.uid,
        "to_invoice": False,
        "state": "paid",
    }
    if refund:
        data["is_refund"] = True
    return data


def _pay(method_id, amount, **extra):
    return (0, 0, {"amount": amount, "name": fields.Datetime.now(), "payment_method_id": method_id, **extra})


def _try_sync(e, data):
    try:
        sync_with_retry(e, data)
        return None, None
    except Exception as exc:  # noqa: BLE001 - laporan smoke
        return type(exc).__name__, str(exc)[:200]


def _int_like(value):
    return float(value).is_integer()


def step_g18_tax_order():
    """W-03: total & pajak order POS ber-PB1 = Rupiah bulat (IDR rounding 1); dibayar tunai/EDC (SMOKE_METHOD)."""
    e, st = ctx_env()
    uuid = os.environ["SMOKE_ORDER_UUID"]
    qty = int(os.environ.get("SMOKE_QTY", "3"))
    lines, total, tax = _taxed_lines(e, st, qty)
    method = st["edc_pm"] if os.environ.get("SMOKE_METHOD") == "edc" else st["cash_pm"]
    pkey = os.environ.get("SMOKE_PARTNER")
    partner = st["partners"][pkey] if pkey else False
    err, msg = _try_sync(e, _ui(e, st, uuid, lines, total, tax, [_pay(method, total)], partner_id=partner))
    order = e["pos.order"].search([("uuid", "=", uuid)], limit=1)
    cur = e["res.company"].browse(st["company_id"]).currency_id
    out(
        ok=err is None and bool(order),
        error=err,
        message=msg,
        idr_rounding=cur.rounding,
        idr_decimals=cur.decimal_places,
        amount_total=order.amount_total,
        amount_tax=order.amount_tax,
        total_int=_int_like(order.amount_total) and _int_like(order.amount_tax),
        client_total=total,
        state=order.state,
    )


def step_g18_tax_redeem():
    """E-30 (K-18-1): order ber-PB1 dengan pembayaran Poin EFN senilai reservasi -> DPP & pajak tetap atas harga
    penuh (sama dengan order tanpa poin), total tetap bulat."""
    e, st = ctx_env()
    uuid = os.environ["SMOKE_ORDER_UUID"]
    qty = int(os.environ.get("SMOKE_QTY", "3"))
    partner_id = st["partners"][os.environ["SMOKE_PARTNER"]]
    lines, total, tax = _taxed_lines(e, st, qty)
    reservation = e["efn.pos.loyalty.reservation"].sudo().search([("order_uuid", "=", uuid)], limit=1)
    value = reservation.value_idr
    pays = [_pay(st["points_pm"], value), _pay(st["cash_pm"], total - value)]
    err, msg = _try_sync(e, _ui(e, st, uuid, lines, total, tax, pays, partner_id=partner_id))
    order = e["pos.order"].search([("uuid", "=", uuid)], limit=1)
    e["efn.pos.loyalty.outbox"]._efn_process_due()
    env.cr.commit()
    commit = e["efn.pos.loyalty.outbox"].sudo().search([("order_id", "=", order.id), ("kind", "=", "redeem_commit")])
    out(
        ok=err is None and bool(order),
        error=err,
        message=msg,
        value_idr=value,
        amount_total=order.amount_total,
        amount_tax=order.amount_tax,
        expected_tax=tax,
        tax_unchanged=bool(order) and order.currency_id.compare_amounts(order.amount_tax, tax) == 0,
        total_int=_int_like(order.amount_total),
        points_paid=order._efn_points_paid() if order else None,
        commit_state=commit.state,
        reservation_state=reservation.state,
    )


def step_g18_refund_method():
    """E-31: refund order asal (SMOKE_ORIG_UUID, dibayar tunai) ke metode lain (EDC) ditolak; ke tunai melebihi
    yang dibayar ditolak; ke tunai sesuai -> OK."""
    e, st = ctx_env()
    original = e["pos.order"].search([("uuid", "=", os.environ["SMOKE_ORIG_UUID"])], limit=1)
    line = original.lines[:1]
    lines, total, tax = _taxed_lines(e, st, -1, refunded_line=line)
    res = {}
    for name, pays in (
        ("other_method", [_pay(st["edc_pm"], total)]),
        ("over_paid", [_pay(st["cash_pm"], total - original.amount_total)]),
        ("same_method", [_pay(st["cash_pm"], total)]),
    ):
        uuid = f"{os.environ['SMOKE_ORDER_UUID'][:-4]}{len(res):04d}"
        data = _ui(e, st, uuid, lines, total, tax, pays, partner_id=original.partner_id.id, refund=True)
        if name == "over_paid":
            data["amount_paid"] = pays[0][2]["amount"]
        err, msg = _try_sync(e, data)
        res[name] = err or e["pos.order"].search([("uuid", "=", uuid)], limit=1).state
        res[name + "_msg"] = (msg or "")[:90]
    out(ok=True, **res)


def step_g18_unlinked_noncash():
    """E-31 (Q-F18-5 default): retur tanpa struk dengan uang keluar NON-tunai (EDC) ditolak."""
    e, st = ctx_env()
    uuid = os.environ["SMOKE_ORDER_UUID"]
    lines, total, tax = _taxed_lines(e, st, -1)
    err, msg = _try_sync(e, _ui(e, st, uuid, lines, total, tax, [_pay(st["edc_pm"], total)], refund=True))
    out(ok=err is not None, error=err, message=msg, created=bool(e["pos.order"].search([("uuid", "=", uuid)])))


def step_g18_is_change():
    """SEC-116: `is_change` dihitung server. (a) Penjualan EDC 2x lipat + tunai -separuh ber-is_change palsu ->
    tersimpan is_change False; (b) refund order EDC (SMOKE_ORIG_UUID) yang dibayar tunai ber-is_change palsu ->
    ditolak (tunai bukan metode asal)."""
    e, st = ctx_env()
    lines, total, tax = _taxed_lines(e, st, 1)
    uuid_a = os.environ["SMOKE_ORDER_UUID"]
    pays = [_pay(st["edc_pm"], total * 2), _pay(st["cash_pm"], -total, is_change=True)]
    err_a, msg_a = _try_sync(e, _ui(e, st, uuid_a, lines, total, tax, pays, amount_return=0.0))
    order = e["pos.order"].search([("uuid", "=", uuid_a)], limit=1)
    cash_change = order.payment_ids.sudo().filtered(lambda p: p.payment_method_id.id == st["cash_pm"])
    original = e["pos.order"].search([("uuid", "=", os.environ["SMOKE_ORIG_UUID"])], limit=1)
    rlines, rtotal, rtax = _taxed_lines(e, st, -1, refunded_line=original.lines[:1])
    uuid_b = f"{uuid_a[:-4]}0b0b"
    rpays = [_pay(st["edc_pm"], 0.0), _pay(st["cash_pm"], rtotal, is_change=True)]
    data = _ui(e, st, uuid_b, rlines, rtotal, rtax, rpays, partner_id=original.partner_id.id, refund=True)
    err_b, msg_b = _try_sync(e, data)
    out(
        ok=True,
        sale_error=err_a,
        sale_msg=(msg_a or "")[:90],
        sale_state=order.state,
        stored_is_change=cash_change.mapped("is_change"),
        refund_error=err_b,
        refund_msg=(msg_b or "")[:120],
    )


def step_g18_refund_points():
    """Refund 1 unit order ber-PB1 berpoin (SMOKE_ORIG_UUID). Pengembalian Poin EFN = preview server, atau
    SMOKE_RESTORE (sengaja salah -> Odoo hold `refund_points_allocation_mismatch`, SEC-60/L-26)."""
    e, st = ctx_env()
    uuid = os.environ["SMOKE_ORDER_UUID"]
    original = e["pos.order"].search([("uuid", "=", os.environ["SMOKE_ORIG_UUID"])], limit=1)
    config = e["pos.config"].browse(st["config_id"])
    line = original.lines[:1]
    preview = config.efn_loyalty_refund_preview(original.id, [[line.id, 1]])
    restore = int(os.environ.get("SMOKE_RESTORE") or preview.get("value_idr") or 0)
    lines, total, tax = _taxed_lines(e, st, -1, refunded_line=line)
    pays = [_pay(st["points_pm"], -restore), _pay(st["cash_pm"], total + restore)]
    err, msg = _try_sync(e, _ui(e, st, uuid, lines, total, tax, pays, partner_id=original.partner_id.id, refund=True))
    e["efn.pos.loyalty.outbox"]._efn_process_due()
    env.cr.commit()
    order = e["pos.order"].search([("uuid", "=", uuid)], limit=1)
    rev = e["efn.pos.loyalty.outbox"].sudo().search([("order_id", "=", order.id), ("kind", "=", "reverse")])
    out(
        ok=err is None,
        error=err,
        message=msg,
        preview_value=preview.get("value_idr"),
        restore=restore,
        reverse_state=rev.state,
        reverse_error=rev.last_error or "",
        loyalty_state=order.efn_loyalty_state,
    )


def _reverse_of(e, uuid):
    order = e["pos.order"].search([("uuid", "=", uuid)], limit=1)
    return order, e["efn.pos.loyalty.outbox"].search([("order_id", "=", order.id), ("kind", "=", "reverse")])


def step_g18_hold_decide():
    """POS manager memutuskan reverse yang ditahan (SMOKE_ACTION = hold_approve | restore_approve), lalu outbox
    diproses. L-26: hold_approve mengirim apa adanya (BFF bisa menolak 409 restore_value_mismatch -> hold lagi);
    restore_approve mengirim ulang dengan `restore_approved: true`."""
    e, _st = ctx_env()  # admin = POS manager
    uuid = os.environ["SMOKE_ORDER_UUID"]
    _order, rev = _reverse_of(e, uuid)
    action = os.environ["SMOKE_ACTION"]
    err = None
    try:
        getattr(rev, f"action_efn_{action}")()
        env.cr.commit()
    except Exception as exc:  # noqa: BLE001 - laporan smoke
        env.cr.rollback()
        err = type(exc).__name__
    e["efn.pos.loyalty.outbox"]._efn_process_due()
    env.cr.commit()
    _order, rev = _reverse_of(e, uuid)
    out(
        ok=err is None,
        error=err,
        reverse_state=rev.state,
        reverse_error=rev.last_error or "",
        restore_approved=rev.restore_approved,
        reverse_delta=rev.points_delta,
        points_restored_value=rev.points_restored_value,
    )


def step_g18_enroll():
    """E-34 / K-18-8: daftar member oleh kasir. SMOKE_AGE=0 -> ditolak lokal (age_confirmation_required);
    SMOKE_AGE=1 -> OTP request ke BFF (verify terpisah, SMOKE_CODE)."""
    e, st = ctx_env()
    config = e["pos.config"].browse(st["config_id"])
    partner_id = st["partners"][os.environ["SMOKE_PARTNER"]]
    uuid = os.environ["SMOKE_ORDER_UUID"]
    age = os.environ.get("SMOKE_AGE") == "1"
    if os.environ.get("SMOKE_CODE"):
        res = config.efn_otp_verify(uuid, os.environ["SMOKE_CODE"], "pos_enroll", age_confirmed=age)
    else:
        res = config.efn_otp_request(uuid, 0, partner_id, "pos_enroll", age_confirmed=age)
    env.cr.commit()
    partner = e["res.partner"].sudo().browse(partner_id)
    challenge = e["efn.pos.otp.challenge"].sudo().search([("order_ref", "=", uuid)], order="id desc", limit=1)
    out(
        **res,
        challenge_state=challenge.state,
        member_locked=bool(partner.efn_member_locked_at) if "efn_member_locked_at" in partner._fields else None,
    )


def step_g18_rpc_targets():
    """Target probe RPC kasir (odoo_rpc_smoke.py): pembayaran order paid (SMOKE_ORIG_UUID) & partner member terkunci
    (SMOKE_PARTNER). Hanya id (tanpa PII) ditambahkan ke RPC_CRED_FILE (0600)."""
    e, st = ctx_env()
    order = e["pos.order"].search([("uuid", "=", os.environ["SMOKE_ORIG_UUID"])], limit=1)
    partner = e["res.partner"].sudo().browse(st["partners"][os.environ["SMOKE_PARTNER"]])
    draft = e["pos.order"].search([("uuid", "=", os.environ["SMOKE_DRAFT_UUID"])], limit=1)
    session = e["pos.session"].browse(st["session_id"])
    targets = {
        "draft_order_id": draft.id,
        "session_id": session.id,
        "pricelist_id": session.config_id.pricelist_id.id,
        "product_id": st["product_id"],
        "cash_pm": st["cash_pm"],
        "payment_id": order.payment_ids[:1].id,
        "payment_amount": order.payment_ids[:1].amount,
        "other_method_id": st["edc_pm"],
        "member_partner_id": partner.id,
        "company_id": st["company_id"],
    }
    # Digabung ke file kredensial (langkah rpc_users sebelumnya) di kunci `_targets` -> satu pipe stdin.
    with open(RPC_CRED_FILE, encoding="utf-8") as fh:
        creds = json.load(fh)
    creds["_targets"] = targets
    fd = os.open(RPC_CRED_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(creds, fh)
    out(
        ok=bool(order and partner and draft),
        draft_state=draft.state,
        member_locked=bool(partner.efn_member_locked_at),
        **targets,
    )


def step_g18_rpc_after():
    """Keadaan setelah probe RPC kasir: pembayaran & HP member tidak berubah (dibandingkan nilai sebelum)."""
    e, st = ctx_env()
    order = e["pos.order"].search([("uuid", "=", os.environ["SMOKE_ORIG_UUID"])], limit=1)
    pay = order.payment_ids[:1]
    partner = e["res.partner"].sudo().browse(st["partners"][os.environ["SMOKE_PARTNER"]])
    created = e["res.partner"].sudo().search([("name", "=like", f"{PREFIX} Kasir %")], order="id desc", limit=5)
    draft = e["pos.order"].search([("uuid", "=", os.environ["SMOKE_DRAFT_UUID"])], limit=1)
    out(
        draft_state=draft.state,
        paid_orders_by_cashier=e["pos.order"].search_count(
            [("name", "=like", "SMOKE-SEC125%"), ("state", "in", ("paid", "done", "invoiced"))]
        ),
        payment_amount=pay.amount,
        payment_method=pay.payment_method_id.id,
        payments=len(order.payment_ids),
        phone_unchanged=partner.phone == os.environ.get("SMOKE_PHONE_EXPECTED", partner.phone),
        cashier_created=[{"company": c.company_id.id, "is_company": c.is_company} for c in created],
    )


{
    "g18_tax_order": step_g18_tax_order,
    "g18_tax_redeem": step_g18_tax_redeem,
    "g18_refund_method": step_g18_refund_method,
    "g18_unlinked_noncash": step_g18_unlinked_noncash,
    "g18_is_change": step_g18_is_change,
    "g18_enroll": step_g18_enroll,
    "g18_refund_points": step_g18_refund_points,
    "g18_hold_decide": step_g18_hold_decide,
    "g18_rpc_targets": step_g18_rpc_targets,
    "g18_rpc_after": step_g18_rpc_after,
    "rpc_users": step_rpc_users,
    "loy_refund_noreceipt": step_loy_refund_noreceipt,
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
    "loy_config": step_loy_config,
    "loy_otp_request": step_loy_otp_request,
    "loy_otp_verify": step_loy_otp_verify,
    "loy_reserve": step_loy_reserve,
    "loy_sync": step_loy_sync,
    "loy_outbox": step_loy_outbox,
    "loy_partner": step_loy_partner,
    "loy_balance": step_loy_balance,
    "loy_release": step_loy_release,
    "loy_cancel": step_loy_cancel,
    "loy_token_probe": step_loy_token_probe,
    "loy_refund": step_loy_refund,
}[STEP]()
