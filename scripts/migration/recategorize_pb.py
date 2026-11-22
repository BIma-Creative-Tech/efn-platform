# ruff: noqa: F821
# G61 — restrukturisasi kategori produk PB per peran akuntansi (docs/fit-gap/G61_KATEGORI_PRODUK_PB.md; spesifikasi
# docs/fit-gap/G54_G55_G61_ODOO_SETUP.md §G61; runbook docs/runbooks/migrasi-esb-odoo.md §4.5).
# Dijalankan di `odoo shell` (container Odoo sekali jalan + overlay migration:
#   /opt/efn-migration = scripts/migration, /mnt/efn-mig = pertukaran):
#   $DCM run --rm --no-deps -T -e EFN_G61_REPORT=/mnt/efn-mig/g61_dryrun.csv \
#     odoo odoo shell -d "$ODOO_DB" --no-http --max-cron-threads=0 --log-level=warn \
#     < /opt/efn-migration/recategorize_pb.py        (path host: ../../scripts/migration/recategorize_pb.py)
# Env: EFN_G61_REPORT (wajib, berkas CSV baru), EFN_G61_APPLY=1 (tulis; default DRY-RUN = semua langkah dijalankan lalu
# di-rollback), EFN_G61_COMPANY (default PB, efn_brand_code; SMD ditolak), EFN_G61_BRAND (default pb, xmlid importer),
# EFN_MIG_CODE_DIR (default /opt/efn-migration; folder berisi efn_mig/).
"""Pindahkan produk PB ke pohon kategori per peran (`category_roles.csv`) dan menu ke `Menu / <akar pos.category>`.

Prasyarat dicek ulang saat jalan (batal bila gagal): 0 sesi POS belum tutup di PT, 0 stock.move dan 0 stock.quant untuk
produk PT. Kategori ESB tidak terpetakan / produk tanpa kategori yang bukan menu -> batal (fail closed). Idempoten:
kategori dicari menurut complete_name, field produk hanya ditulis bila berbeda, akun kategori lama hanya diisi bila
kosong. SMD (dan PT lain) tidak disentuh. Laporan CSV tanpa data pribadi (id, default_code, kategori, flag).
"""

import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

TAG = "[efn-g61]"
FLAGS = ("type", "is_storable", "sale_ok", "purchase_ok", "available_in_pos")
# Properti akuntansi per path (prefiks terpanjang menang) — PLACEHOLDER sampai Finance (Q-ESB16), spesifikasi §G61.5.
# Kunci: cost -> property_cost_method, valuation -> property_stock_valuation_account_id,
# expense -> property_account_expense_categ_id, income -> property_account_income_categ_id. None = belum ada akun.
ROLE_PROPS = {
    "Persediaan": {"cost": "average", "valuation": "l10n_id_11300180", "expense": "l10n_id_51000020"},
    "Persediaan / Barang Jadi": {
        "cost": "average", "valuation": "l10n_id_11300180", "expense": "l10n_id_51000010", "income": "l10n_id_41000010",
    },
    "Persediaan / Merchandise": {
        "cost": "average", "valuation": "l10n_id_11300180", "expense": "l10n_id_51000010", "income": "l10n_id_41000010",
    },
    "Perlengkapan": {"expense": "l10n_id_63110140"},
    "Perlengkapan / Kantor & ATK": {"expense": "l10n_id_63110120"},
    "Beban": {"expense": "l10n_id_63110140"},
    "Beban / Jasa / Pemeliharaan & Instalasi": {"expense": "l10n_id_65110080"},
    "Beban / Jasa / Pengiriman": {"expense": "l10n_id_65110090"},
    "Beban / Jasa / Biaya Impor": {"expense": "l10n_id_65110090"},
    "Beban / Jasa / Perizinan & Sertifikasi": {"expense": "l10n_id_65110010"},
    "Beban / Jasa / Sewa": {"expense": "l10n_id_65110040"},
    "Aset Tetap": {"expense": "l10n_id_12210030"},
    "Aset Tetap / Bangunan & Renovasi": {"expense": "l10n_id_12210010"},
    "Aset Tetap / Perangkat Lunak": {"expense": None},  # A10: Finance membuat akun
    "Aset Tetap / LVA": {"expense": "l10n_id_63110120"},  # A6: dibebankan
    "Menu": {"expense": "l10n_id_51000010", "income": "l10n_id_41000010"},
}  # fmt: skip
PROP_FIELDS = {
    "valuation": "property_stock_valuation_account_id",
    "expense": "property_account_expense_categ_id",
    "income": "property_account_income_categ_id",
}


class Abort(Exception):
    pass


def _efn_mig():
    code_dir = os.environ.get("EFN_MIG_CODE_DIR") or "/opt/efn-migration"
    if not Path(code_dir, "efn_mig", "category_roles.py").is_file():
        raise Abort(f"efn_mig tidak ada di {code_dir} (overlay migration terpasang? EFN_MIG_CODE_DIR)")
    if code_dir not in sys.path:
        sys.path.insert(0, code_dir)
    from efn_mig import category_roles as cr  # noqa: PLC0415
    from efn_mig.xmlid import MODULE, xmlid_name  # noqa: PLC0415

    return cr, MODULE, xmlid_name


def _props_for(path):
    best = max((p for p in ROLE_PROPS if path == p or path.startswith(p + " / ")), key=len, default=None)
    return ROLE_PROPS.get(best, {})


def _menu_root_name(tmpl):
    """(nama akar pos.category pertama, akar berbeda > 1?)."""
    cats = tmpl.pos_categ_ids.sorted(lambda c: (c.sequence, c.id)) if "pos_categ_ids" in tmpl._fields else []
    roots = []
    for cat in cats:
        while cat.parent_id:
            cat = cat.parent_id
        if cat not in roots:
            roots.append(cat)
    return (roots[0].name if roots else None), len(roots) > 1


def _main(env):
    cr, module, xmlid_name = _efn_mig()
    apply = os.environ.get("EFN_G61_APPLY") == "1"
    code = (os.environ.get("EFN_G61_COMPANY") or "PB").strip().upper()
    brand = (os.environ.get("EFN_G61_BRAND") or code.lower()).strip()
    report_raw = os.environ.get("EFN_G61_REPORT")
    if not report_raw:
        raise Abort("EFN_G61_REPORT wajib (path berkas CSV laporan yang belum ada)")
    if Path(report_raw).exists() or not Path(report_raw).parent.is_dir():
        raise Abort("EFN_G61_REPORT sudah ada atau foldernya tidak ada (laporan tidak ditimpa)")
    if code == "SMD":
        raise Abort("SMD tidak boleh disentuh (aturan independensi supermarket)")
    Company = env["res.company"]
    if "efn_brand_code" not in Company._fields:
        raise Abort("res.company.efn_brand_code tidak ada (efn_setup_company terpasang?)")
    company = Company.search([("efn_brand_code", "=", code)])
    if len(company) != 1:
        raise Abort(f"company dengan efn_brand_code={code} harus tepat satu (ditemukan {len(company)})")
    # SEC-G61-02: pohon kategori G61 khusus F&B; tolak PT non-F&B (supermarket/retail, holding, farm) apa pun kodenya.
    if "efn_business_type" in Company._fields and company.efn_business_type != "fnb":
        raise Abort(f"company {code} bukan F&B (efn_business_type={company.efn_business_type}); G61 hanya untuk F&B")

    roles = cr.load_roles()
    path_role = {}
    for role in roles.values():
        prev = path_role.setdefault(role.target_path, role)
        if (prev.role, prev.type, prev.is_storable, prev.sale_ok, prev.purchase_ok, prev.available_in_pos) != (
            role.role, role.type, role.is_storable, role.sale_ok, role.purchase_ok, role.available_in_pos,
        ):  # fmt: skip
            raise Abort(f"category_roles.csv: flag berbeda untuk path sama {role.target_path!r}")

    Template = env["product.template"].with_company(company).with_context(active_test=False, tracking_disable=True)
    templates = Template.search([("company_id", "=", company.id)])
    variants = templates.product_variant_ids
    # (a) prasyarat (dicek ulang saat jalan)
    open_sessions = (
        env["pos.session"].search_count([("company_id", "=", company.id), ("state", "!=", "closed")])
        if "pos.session" in env
        else 0
    )
    moves = env["stock.move"].sudo().search_count([("product_id", "in", variants.ids)])
    quants = env["stock.quant"].sudo().search_count([("product_id", "in", variants.ids)])
    if open_sessions or moves or quants:
        raise Abort(f"prasyarat gagal: sesi POS terbuka={open_sessions}, stock.move={moves}, stock.quant={quants}")

    # Klasifikasi setiap produk -> (path target, nilai flag baru, catatan)
    has_pos = "available_in_pos" in Template._fields
    plan, unmapped, notes = {}, [], defaultdict(list)
    for tmpl in templates:
        categ = tmpl.categ_id
        name = categ.complete_name or ""
        in_menu = not categ or name == cr.MENU_ROOT or name.startswith(cr.MENU_ROOT + cr.SEP)
        # Sudah di pohon target -> peran menurut path (idempoten); selain itu nama kategori ESB lama (`PB / <ESB>`).
        role = path_role.get(name) or (roles.get(cr.key(categ.name)) if not in_menu else None)
        if role:
            is_fin = role.esb_category == "FIN"
            vals = {
                "type": role.type,
                "is_storable": role.is_storable,
                "sale_ok": role.sale_ok,
                "purchase_ok": role.purchase(bool(tmpl.seller_ids), is_fin=is_fin),
            }
            if has_pos and role.available_in_pos == "false":
                vals["available_in_pos"] = False
            if role.note:
                notes[tmpl.id].append(role.note.split(" ", 1)[0])
            if role.note.startswith("A1 ") and (tmpl.available_in_pos if has_pos else False):
                notes[tmpl.id].append("A1_available_in_pos")
            plan[tmpl.id] = (role.target_path, vals)
        elif tmpl.sale_ok and in_menu:
            root, multi = _menu_root_name(tmpl)
            if multi:
                notes[tmpl.id].append("menu_multi_root")
            path = f"{cr.MENU_ROOT}{cr.SEP}{root}" if root else cr.MENU_OTHER
            plan[tmpl.id] = (path, {"sale_ok": True, "purchase_ok": False, "is_storable": False})
        else:
            unmapped.append(tmpl)

    # (b) kategori target (cari complete_name; buat berjenjang) + xmlid importer + properti
    Category = env["product.category"].with_company(company)
    chart = env["account.chart.template"].with_company(company)
    categ_by_path, created, warnings = {}, [], []
    nodes = {node for p, _v in plan.values() for node in cr.path_chain(p)}
    for path in sorted(nodes, key=lambda x: (x.count(cr.SEP), x)):
        found = Category.search([("complete_name", "=", path)], limit=1)
        if not found:
            parent = categ_by_path.get(path.rsplit(cr.SEP, 1)[0]) if cr.SEP in path else None
            found = Category.create({"name": path.rsplit(cr.SEP, 1)[-1], "parent_id": parent.id if parent else False})
            created.append(path)
        categ_by_path[path] = found
        is_new = path in created
        props = _props_for(path)
        vals = {}
        if is_new and props.get("cost"):
            vals["property_cost_method"] = props["cost"]
        for key, field in PROP_FIELDS.items():
            if key not in props or (not is_new and found[field]):
                continue
            account = chart.ref(props[key], raise_if_not_found=False) if props[key] else None
            if account:
                vals[field] = account.id
            else:
                warnings.append(f"akun {key} kosong untuk {path!r} ({props[key] or 'belum ada di CoA, Finance'})")
        if vals:
            found.write(vals)
        if not path.startswith(cr.MENU_ROOT):  # xmlid bundel ESB -> impor ulang memakai record yang sama
            name = xmlid_name(brand, code, "category", path)
            imd = env["ir.model.data"].search([("module", "=", module), ("name", "=", name)], limit=1)
            if not imd:
                env["ir.model.data"].create(
                    {"module": module, "name": name, "model": "product.category", "res_id": found.id, "noupdate": True}
                )
            elif (imd.model, imd.res_id) != ("product.category", found.id):
                warnings.append(f"xmlid {module}.{name} menunjuk record lain (tidak diubah)")

    # (c)-(e) simpan harga, tulis produk (hanya field yang berbeda, dikelompokkan), cek ulang harga
    prices = {v.id: v.with_company(company).standard_price for v in variants}
    rows, groups = [], defaultdict(lambda: Template.browse())
    for tmpl in templates:
        old = {f: tmpl[f] for f in FLAGS if f in tmpl._fields}
        if tmpl.id not in plan:
            rows.append([tmpl.id, tmpl.default_code or "", tmpl.categ_id.complete_name or "", "", json.dumps(old), "",
                         "UNMAPPED"])  # fmt: skip
            continue
        path, vals = plan[tmpl.id]
        diff = {f: v for f, v in vals.items() if tmpl[f] != v}
        if tmpl.categ_id != categ_by_path[path]:
            diff["categ_id"] = categ_by_path[path].id
        new = {**old, **vals}
        rows.append([tmpl.id, tmpl.default_code or "", tmpl.categ_id.complete_name or "", path, json.dumps(old),
                     json.dumps(new), " ".join(notes.get(tmpl.id, []))])  # fmt: skip
        if diff and not unmapped:
            groups[tuple(sorted(diff.items()))] |= tmpl
    for key, recs in groups.items():
        for start in range(0, len(recs), 500):
            recs[start : start + 500].write(dict(key))
    restored = 0
    for variant in variants.with_company(company).with_context(disable_auto_revaluation=True):
        if abs(variant.standard_price - prices[variant.id]) > 1e-6:
            variant.standard_price = prices[variant.id]
            restored += 1

    # (f) kategori lama di bawah induk brand yang kosong & tidak dirujuk -> dihapus (xmlid ikut terhapus)
    removed, kept = [], []
    root = env.ref(f"{module}.{brand}_category_root", raise_if_not_found=False)
    if root and not unmapped:
        refs = [("product.template", "categ_id"), ("product.pricelist.item", "categ_id")]
        refs += [("stock.putaway.rule", "category_id")] if "stock.putaway.rule" in env else []

        def used(categ):
            if categ.child_id or ("route_ids" in categ._fields and categ.route_ids):
                return True
            return any(
                env[model].sudo().with_context(active_test=False).search_count([(fname, "=", categ.id)])
                for model, fname in refs
            )

        for categ in env["product.category"].search([("id", "child_of", root.id)], order="parent_path desc"):
            if used(categ):
                kept.append(categ.complete_name)
            else:
                removed.append(categ.complete_name)
                categ.unlink()

    # (g) laporan
    service_components = 0
    if "mrp.bom.line" in env:
        service_tmpls = [tid for tid, (_p, v) in plan.items() if v.get("type") == "service"]
        service_components = env["mrp.bom.line"].sudo().search_count([("product_tmpl_id", "in", service_tmpls)])
    pos_outside_menu = sum(
        1 for t in templates if has_pos and t.available_in_pos and t.id in plan
        and not plan[t.id][0].startswith(cr.MENU_ROOT) and plan[t.id][1].get("available_in_pos") is not False
    )  # fmt: skip
    out = Path(report_raw)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["product_tmpl_id", "default_code", "old_categ", "new_categ", "old_flags", "new_flags", "note"])
        writer.writerows(rows)
    per_target = defaultdict(int)
    for path, _v in plan.values():
        per_target[path] += 1
    summary = {
        "mode": "apply" if apply and not unmapped else "dry-run",
        "company": code,
        "products": len(templates),
        "unmapped": len(unmapped),
        "categories_created": created,
        "categories_removed": removed,
        "categories_kept_old": kept,
        "per_target": dict(sorted(per_target.items())),
        "standard_price_restored": restored,
        "bom_lines_service_component": service_components,
        "available_in_pos_outside_menu": pos_outside_menu,
        "warnings": warnings,
        "report": str(out),
    }
    if unmapped:
        env.cr.rollback()
        summary["error"] = "ada produk tak terpetakan (lihat baris UNMAPPED di laporan); tidak ada yang ditulis"
        return summary, 1
    if apply:
        env.cr.commit()
    else:
        env.cr.rollback()
    return summary, 0


try:
    _summary, _code = _main(env)
except Abort as exc:
    env.cr.rollback()
    _summary, _code = {"error": str(exc)}, 2
print(f"{TAG} " + json.dumps(_summary, ensure_ascii=False, sort_keys=True, default=str), flush=True)
if _code:
    sys.exit(_code)
