"""Payload ``odoo shell`` untuk import katalog brand (dipanggil oleh ``import_odoo.py``; JANGAN dijalankan langsung).

Berjalan di dalam container Odoo (Python 3.12, ORM 19). Input: file JSON di ``EFN_CATALOG_PAYLOAD`` (dibuat
host: produk + gambar base64 yang sudah di-resize). Output: satu baris ``EFN_RESULT <json>`` di stdout.

Aturan:
- Upsert per ``default_code`` (``<PREFIX>-<source_id>``) + company -> idempoten.
- Harga: placeholder hanya ditulis saat create, atau saat update bila produk MASIH bertag "DEMO harga"
  (tim yang sudah mengisi harga resmi menghapus tag itu -> harga tidak ditimpa lagi).
- Kanal: produk dikaitkan ke kanal kode = kode PT; status aktif kanal TIDAK diubah.
- ``dry_run`` -> semua perubahan di-rollback (constraint Odoo tetap tervalidasi lewat flush).
"""

import base64
import hashlib
import json
import os

from odoo import Command

XMLID_MODULE = "efn_brand_catalog_demo"
DEMO_PRICE_TAG = "DEMO harga"


def _xmlid_record(env, name, model, create_vals):
    imd = env["ir.model.data"].sudo()
    rec = env.ref(f"{XMLID_MODULE}.{name}", raise_if_not_found=False)
    if rec:
        if create_vals.get("name") and rec.name != create_vals["name"]:
            rec.name = create_vals["name"]
        return rec, False
    rec = env[model].create(create_vals)
    imd.create({"module": XMLID_MODULE, "name": name, "model": model, "res_id": rec.id, "noupdate": True})
    return rec, True


def _product_category(env, path, cache):
    parent = env["product.category"]
    for depth in range(1, len(path) + 1):
        key = tuple(path[:depth])
        if key not in cache:
            name = path[depth - 1]
            found = env["product.category"].search([("name", "=ilike", name), ("parent_id", "=", parent.id or False)],
                                                   limit=1)
            if found and found.name != name:
                found.name = name
            cache[key] = found or env["product.category"].create({"name": name, "parent_id": parent.id or False})
        parent = cache[key]
    return parent


def _company_ctx(env, code, cache):
    if code in cache:
        return cache[code]
    company = env["res.company"].search([("efn_brand_code", "=", code)], limit=1)
    if not company:
        raise ValueError(f"company efn_brand_code={code} tidak ada di DB ini")
    channel = env["efn.online.channel"].with_context(active_test=False).search(
        [("code", "=", code), ("company_id", "=", company.id)], limit=1
    )
    if company.efn_business_type == "fnb":
        taxes = company.account_sale_tax_id  # PB1 default PT (efn_setup_company)
    else:
        taxes = env.ref(f"account.{company.id}_tax_ST4", raise_if_not_found=False) or company.account_sale_tax_id
    cache[code] = (company, channel, taxes)
    return cache[code]


def run(env, payload):
    dry = bool(payload.get("dry_run"))
    brand = payload["brand"]
    prefix = payload["prefix"]
    stats = {"brand": brand, "dry_run": dry, "created": 0, "updated": 0, "image_set": 0, "image_same": 0,
             "price_kept": 0, "archived": 0, "categories_created": 0, "warnings": []}
    env = env(context=dict(env.context, tracking_disable=True, mail_create_nolog=True, mail_notrack=True,
                           active_test=False))
    tag = env["product.tag"].search([("name", "=", DEMO_PRICE_TAG)], limit=1) or env["product.tag"].create(
        {"name": DEMO_PRICE_TAG}
    )
    has_pos = "available_in_pos" in env["product.template"]._fields
    companies, categ_cache, pos_cache = {}, {}, {}
    seen_codes = set()
    for seq, item in enumerate(payload["products"], start=1):
        company, channel, taxes = _company_ctx(env, item["company_code"], companies)
        cenv = env(context=dict(env.context, allowed_company_ids=[company.id]))
        if not channel:
            stats["warnings"].append(f"kanal {item['company_code']} tidak ada -> produk tanpa kanal online")
        categ = _product_category(cenv, [payload["brand_label"], *item["categ_path"]], categ_cache)
        pos_categ = None
        if has_pos and item.get("pos_categ"):
            key = item["pos_categ"]
            if key not in pos_cache:
                slug = "".join(ch if ch.isalnum() else "_" for ch in f"{brand}_{key}".lower())
                pos_cache[key], created = _xmlid_record(
                    cenv, f"pos_categ_{slug}", "pos.category", {"name": key, "sequence": 100 + len(pos_cache)}
                )
                stats["categories_created"] += created
            pos_categ = pos_cache[key]
        code = item["default_code"]
        seen_codes.add(code)
        variant = cenv["product.product"].search([("default_code", "=", code), ("company_id", "=", company.id)],
                                                 limit=1)
        tmpl = variant.product_tmpl_id
        is_fnb = company.efn_business_type == "fnb"
        note = (f"<p>DEMO katalog dari situs resmi brand ({item.get('source_url') or payload['source_url']}); "
                f"import {payload['imported_at']}.</p>")
        vals = {
            "name": item["name"],
            "type": "consu",
            "is_storable": not is_fnb,
            "sale_ok": True,
            "purchase_ok": not is_fnb,
            "company_id": company.id,
            "categ_id": categ.id,
            "taxes_id": [Command.set(taxes.ids)],
            "efn_online_description": item.get("description") or False,
            "description_sale": item.get("description") or False,
            "efn_online_sequence": seq,
            "weight": item.get("weight") or 0.0,
        }
        if channel:
            vals["efn_online_channel_ids"] = [Command.link(channel.id)]
        if pos_categ:
            vals["pos_categ_ids"] = [Command.set(pos_categ.ids)]
        if has_pos and is_fnb:
            vals["available_in_pos"] = True
        price_placeholder = item.get("price_is_placeholder", True)
        write_price = not tmpl or not price_placeholder or tag in tmpl.product_tag_ids
        if write_price:
            vals["list_price"] = item["list_price"]
            if price_placeholder:
                vals["product_tag_ids"] = [Command.link(tag.id)]
                vals["description"] = note + "<p><b>Harga = PLACEHOLDER (DEMO harga)</b>: wajib diganti dari master " \
                                             "harga resmi; hapus tag 'DEMO harga' setelah harga resmi diisi.</p>"
            else:
                vals["product_tag_ids"] = [Command.unlink(tag.id)]
                vals["description"] = note
        else:
            stats["price_kept"] += 1
        if tmpl:
            if not tmpl.active:
                vals["active"] = True
            tmpl.write(vals)
            stats["updated"] += 1
        else:
            vals["default_code"] = code
            tmpl = cenv["product.template"].create(vals)
            stats["created"] += 1
        if item.get("image_b64"):
            # Bandingkan checksum attachment (sha1 isi biner) -> gambar sama tidak ditulis ulang (idempoten).
            att = env["ir.attachment"].sudo().search([("res_model", "=", "product.template"),
                                                      ("res_field", "=", "image_1920"), ("res_id", "=", tmpl.id)],
                                                     limit=1)
            new_sha = hashlib.sha1(base64.b64decode(item["image_b64"])).hexdigest()
            if att.checksum != new_sha:
                tmpl.image_1920 = item["image_b64"]
                stats["image_set"] += 1
            else:
                stats["image_same"] += 1
    if payload.get("archive_missing"):
        stale = env["product.product"].search([("default_code", "=like", f"{prefix}-%"),
                                               ("default_code", "not in", list(seen_codes)), ("active", "=", True)])
        stale.product_tmpl_id.write({"active": False})
        stats["archived"] = len(stale)
    env.flush_all()
    stats["verify"] = verify(env, prefix)
    if dry:
        env.cr.rollback()
    else:
        env.cr.commit()
    return stats


def verify(env, prefix):
    """Angka verifikasi: per company, per kanal (domain katalog BFF yang sama), jumlah bergambar."""
    env = env(context=dict(env.context, active_test=True))
    tmpls = env["product.product"].search([("default_code", "=like", f"{prefix}-%")]).product_tmpl_id
    with_img = env["ir.attachment"].sudo().search_count([("res_model", "=", "product.template"),
                                                         ("res_field", "=", "image_1920"),
                                                         ("res_id", "in", tmpls.ids)])
    per_company = {}
    for t in tmpls:
        per_company[t.company_id.efn_brand_code or "-"] = per_company.get(t.company_id.efn_brand_code or "-", 0) + 1
    channels = {}
    for ch in env["efn.online.channel"].with_context(active_test=False).search([]):
        products = env["product.product"].search(ch._efn_online_product_domain())
        mine = products.filtered(lambda p: (p.default_code or "").startswith(f"{prefix}-"))
        shas = ch._efn_online_image_shas(mine.product_tmpl_id)
        sample = None
        if mine:
            price = ch._efn_online_price(mine[0])
            sample = {"name": mine[0].name, **{k: price[k] for k in ("price_excl", "price_incl", "tax_labels")}}
        channels[ch.code] = {"active": ch.active, "products_total": len(products), "brand_products": len(mine),
                             "brand_with_image_sha": len(shas), "sample_price": sample}
    pos_categs = sorted({c.name for c in tmpls.pos_categ_ids})
    return {"templates": len(tmpls), "with_image": with_img, "per_company": per_company,
            "per_channel": channels, "pos_categories": pos_categs}


_path = os.environ.get("EFN_CATALOG_PAYLOAD")
with open(_path, encoding="utf-8") as _fh:
    _payload = json.load(_fh)
if _payload.get("verify_only"):
    _result = {"brand": _payload["brand"], "verify": verify(env, _payload["prefix"])}  # noqa: F821 (odoo shell)
    env.cr.rollback()  # noqa: F821
else:
    _result = run(env, _payload)  # noqa: F821
print("EFN_RESULT " + json.dumps(_result, ensure_ascii=False, default=str))
