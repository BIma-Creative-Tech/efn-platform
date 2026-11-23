# ruff: noqa: F821, E501
# Jalankan (HANYA environment lokal efn-prod gladi, DB efn_odoo; runbook docs/runbooks/odoo-online-api-key.md §1b):
#   docker exec -i efn-prod-odoo-1 odoo shell -c /etc/odoo/odoo.conf -d efn_odoo --no-http \
#     < scripts/dev/activate_pb_online_demo.py
# Pratinjau tanpa menyimpan (rollback di akhir):
#   docker exec -i -e PB_ONLINE_DRY_RUN=1 efn-prod-odoo-1 odoo shell -c /etc/odoo/odoo.conf -d efn_odoo --no-http \
#     < scripts/dev/activate_pb_online_demo.py
"""Aktivasi kanal online PB (PT Paris Baguette) di efn-prod lokal untuk demo V-01 + belanja ambil-di-gerai.

Keputusan pemilik proyek 23 Nov 2026. Gladi lokal, BUKAN production. Semua nilai = PLACEHOLDER demo sampai Ops/Finance
menjawab (Q-ON1..Q-ON13). Ditulis ulang dari pola efn_setup_supermarket_online/demo (SMD) dan blok PB
efn_setup_online/demo, TANPA xmlid modul tersebut (efn_setup_online tidak terpasang di efn-prod; SMD independen).
ADR-034 (kanal online via BFF), ADR-019 A1 (jurnal titipan), ADR-027 §2 (FP outlet F&B), K25-07 (online wajib lunas).

Idempoten (cari dulu, baru buat). Tidak menghapus apa pun, tidak mengubah company/kanal lain. Record yang SUDAH ada
tidak ditimpa: skrip hanya mengisi yang kosong dan mencetak PERINGATAN bila nilai berbeda dari rencana.
Satu transaksi: commit sekali di akhir; galat apa pun = tidak ada yang tersimpan.

Yang dibuat untuk company PB (efn_brand_code = PB):
  partner generik + tim Super-App, pricelist IDR, pembulatan Rp1, jurnal bank TTPO (titipan) + TRFO (transfer, dengan
  rekening bank PLACEHOLDER milik partner PT, diverifikasi/"trusted" oleh admin), kalender ambil 09:00-21:00 WIB,
  user teknis efn_online_pb (Internal User + grup kanal, TANPA API key), kanal PB aktif, 5 outlet pickup (FP
  non-dine-in), publikasi produk PB (aktif, sale_ok, list_price > 0, pos.category "T Package - *").
"""

import os

from odoo import Command

TAG = "PB-ONLINE"
BRAND = "PB"
CHANNEL_CODE = "PB"
USER_LOGIN = "efn_online_pb"
EXPECTED_COMPANY_ID = 4  # fakta efn-prod 23 Nov 2026; diverifikasi lewat efn_brand_code, bukan dipercaya buta
OUTLETS = (  # (kode gudang = kode outlet, urutan)
    ("P002", 10),
    ("P010", 20),
    ("P018", 30),
    ("P024", 40),
    ("P027", 50),
)
NOT_OUTLETS = ("P004", "P013", "P037", "P038", "P041")  # CK + gudang: tidak boleh jadi outlet online
FP_NON_DINE_IN_ID = 2  # "F&B Non-Dine-in (tanpa SC, PB1 0%)" company PB di efn-prod
POS_CATEG_PATTERN = "T Package - %"
# Rekening tujuan transfer: PLACEHOLDER fiktif (bukan rekening nyata). Wajib diganti rekening PT yang diverifikasi
# Finance sebelum dipakai di luar gladi lokal. Nomor 10 digit (validasi efn_online_transfer: 6..20 digit).
BANK_NAME = "Bank Contoh DEMO (bukan rekening nyata)"
BANK_ACC_NUMBER = "9990000040"
BANK_HOLDER = "PT Paris Baguette (DEMO)"

# Nilai kanal (acuan kanal SMD efn-prod; lihat catatan F&B di bawah).
CHANNEL_VALS = {
    "name": "Super-App Paris Baguette",
    "code": CHANNEL_CODE,
    "brand_code": BRAND,
    "sequence": 20,
    "active": True,
    "payment_modes": "bank_transfer",  # K25-07: wajib lunas; gateway belum untuk demo ini
    "payment_timeout_minutes": 70,  # BFF 60 + 10
    "quote_validity_minutes": 10,
    "max_lines": 50,
    "max_qty_per_line": 99,
    "max_line_qty": 20,
    "max_order_amount": 5000000.0,  # PLACEHOLDER pilot (L-41)
    # F&B (beda dari SMD, pola PB efn_setup_online): produk dibuat di gerai, stok gudang outlet tidak dicatat per
    # produk -> tanpa cek stok dan tanpa reservasi. Dengan free_qty/confirm, produk storable tanpa quant di gudang
    # outlet akan selalu out_of_stock saat quote/place. efn_online_stock kanal F&B tetap mengembalikan in_stock.
    "stock_check": "none",
    "stock_hold_mode": "none",
    "fulfilment_pickup": True,
    "fulfilment_delivery": False,
    "invoice_on_paid": True,
    "require_outlet_fiscal_position": True,  # ADR-027 §2: PB1 dari FP outlet
}
CALENDAR_NAME = "Jam ambil online PB - WIB (PLACEHOLDER)"
CALENDAR_HOURS = (9.0, 21.0)
OUTLET_DEFAULTS = {"pickup_enabled": True, "delivery_enabled": False, "prep_minutes": 15}
# Slot pre-order (hanya bila efn_online_slot terpasang), nilai sama dengan outlet SMD (PLACEHOLDER Q-SMO3).
SLOT_VALS = {"slot_enabled": True, "slot_minutes": "60", "slot_capacity": 10, "slot_lead_minutes": 120, "slot_horizon_days": 7}
REQUIRED_MODULES = ("efn_online_order", "efn_online_transfer", "point_of_sale", "sale_stock")

summary = []


def say(msg):
    print(f"{TAG} {msg}", flush=True)


def warn(msg):
    print(f"{TAG} PERINGATAN {msg}", flush=True)


def abort(msg):
    print(f"{TAG} ABORT {msg}", flush=True)
    env.cr.rollback()
    raise SystemExit(2)


def get_or_create(model, domain, vals, label):
    """Cari (termasuk arsip); buat bila tidak ada. Kembalikan (record, dibuat?)."""
    Model = env[model].sudo().with_context(active_test=False)
    found = Model.search(domain)
    if len(found) > 1:
        abort(f"{label}: {len(found)} record cocok {domain}; bereskan manual dulu")
    if found:
        if "active" in found._fields and not found.active:
            warn(f"{label} id={found.id} diarsipkan; tidak diaktifkan otomatis")
        return found, False
    return Model.create(vals), True


def note(label, record, created):
    summary.append(f"{label}: id={record.id} ({'baru' if created else 'sudah ada'})")
    say(f"{label}: id={record.id} {'DIBUAT' if created else 'sudah ada'}")


# ---------------------------------------------------------------- prasyarat
_installed = set(
    env["ir.module.module"].sudo().search([("name", "in", list(REQUIRED_MODULES)), ("state", "=", "installed")]).mapped("name")
)
_missing = [m for m in REQUIRED_MODULES if m not in _installed]
if _missing:
    abort(f"modul belum terpasang: {', '.join(_missing)}")
HAS_SLOT = bool(env["ir.module.module"].sudo().search_count([("name", "=", "efn_online_slot"), ("state", "=", "installed")]))

_companies = env["res.company"].sudo().search([("efn_brand_code", "=", BRAND)])
if len(_companies) != 1:
    abort(f"harus tepat satu company efn_brand_code={BRAND}, ditemukan {len(_companies)}")
if _companies.id != EXPECTED_COMPANY_ID:
    warn(f"company PB id={_companies.id}, bukan {EXPECTED_COMPANY_ID} seperti catatan efn-prod; lanjut memakai id={_companies.id}")
if _companies.efn_business_type != "fnb":
    warn(f"company PB efn_business_type={_companies.efn_business_type!r}, bukan 'fnb' (katalog akan berlabel retail)")

# Admin (bukan OdooBot): CE melarang OdooBot/superuser shell menandai rekening bank "trusted" (_user_can_trust).
_admin = env.ref("base.user_admin")
if _companies not in _admin.company_ids:
    _admin.sudo().write({"company_ids": [Command.link(_companies.id)]})
    say(f"admin ditautkan ke company {_companies.name} (perlu untuk allowed_company_ids)")
env = env(user=_admin.id, context=dict(env.context, allowed_company_ids=[_companies.id], tz="Asia/Jakarta"))
company = env["res.company"].browse(_companies.id)
say(f"company {company.name} id={company.id}; efn_online_slot={'ya' if HAS_SLOT else 'tidak'}")

Warehouse = env["stock.warehouse"].sudo().with_context(active_test=False)
warehouses = {}
for _code, _seq in OUTLETS:
    if _code in NOT_OUTLETS:
        abort(f"{_code} adalah CK/gudang, bukan outlet")
    _wh = Warehouse.search([("code", "=", _code), ("company_id", "=", company.id)])
    if len(_wh) != 1 or not _wh.active:
        abort(f"gudang aktif {_code} company PB tidak ditemukan tepat satu (ditemukan {len(_wh)})")
    warehouses[_code] = _wh

FP = env["account.fiscal.position"].sudo().with_context(active_test=False)
fp = FP.browse(FP_NON_DINE_IN_ID).exists()
if not (fp and fp.company_id == company and "non-dine" in (fp.name or "").lower()):
    fp = FP.search([("company_id", "=", company.id), ("name", "ilike", "Non-Dine-in")])
    if len(fp) != 1:
        abort(f"fiscal position 'Non-Dine-in' company PB tidak ditemukan tepat satu (ditemukan {len(fp)})")
if not fp.active:
    abort(f"fiscal position {fp.name} diarsipkan")
if fp.auto_apply:
    abort(f"fiscal position {fp.name} auto_apply aktif; outlet online menolak FP auto-apply (ADR-027 §2)")
say(f"fiscal position outlet: id={fp.id} {fp.name}")

idr = env.ref("base.IDR")
if company.currency_id != idr:
    abort(f"mata uang company PB {company.currency_id.name}, bukan IDR")

# ---------------------------------------------------------------- master pendukung
partner, created = get_or_create(
    "res.partner",
    [("name", "=", "Pelanggan Super-App - PT Paris Baguette"), ("company_id", "=", company.id)],
    {"name": "Pelanggan Super-App - PT Paris Baguette", "company_id": company.id, "is_company": False},
    "partner generik",
)
if partner.phone or partner.email or partner.vat:
    abort(f"partner generik id={partner.id} punya HP/email/NPWP (UU PDP); bereskan manual")
note("partner generik", partner, created)

team, created = get_or_create(
    "crm.team",
    [("name", "=", "Super-App Paris Baguette"), ("company_id", "=", company.id)],
    {"name": "Super-App Paris Baguette", "company_id": company.id},
    "tim penjualan",
)
note("tim penjualan", team, created)

pricelist, created = get_or_create(
    "product.pricelist",
    [("name", "=", "Online Paris Baguette (DEMO)"), ("company_id", "=", company.id)],
    {"name": "Online Paris Baguette (DEMO)", "currency_id": idr.id, "company_id": company.id},
    "pricelist",
)
if pricelist.currency_id != idr:
    abort(f"pricelist id={pricelist.id} bukan IDR")
note("pricelist", pricelist, created)

rounding, created = get_or_create(
    "account.cash.rounding",
    [("name", "=", "Rupiah bulat PB (DEMO, Q-ON6)")],
    {"name": "Rupiah bulat PB (DEMO, Q-ON6)", "rounding": 1.0, "strategy": "biggest_tax", "rounding_method": "HALF-UP"},
    "pembulatan",
)
note("pembulatan", rounding, created)

Journal = env["account.journal"].sudo().with_context(active_test=False)


def bank_journal(code, name, extra=None):
    journal = Journal.search([("code", "=", code), ("company_id", "=", company.id)])
    if journal and journal.type != "bank":
        abort(f"jurnal {code} company PB sudah ada tetapi bertipe {journal.type}, bukan bank")
    if journal:
        return journal, False
    vals = {"name": name, "code": code, "type": "bank", "company_id": company.id}
    vals.update(extra or {})
    return Journal.create(vals), True


# ADR-019 A1: jurnal bank "Titipan Online - Holding" milik PT (akun default dibuat Odoo; pemetaan akun = Q-2 Finance).
titipan, created = bank_journal("TTPO", "Titipan Online - Holding")
note("jurnal titipan TTPO", titipan, created)

bank = env["res.bank"].sudo().search([("name", "=", BANK_NAME)], limit=1) or env["res.bank"].sudo().create({"name": BANK_NAME})
PartnerBank = env["res.partner.bank"].sudo().with_context(active_test=False)
account = PartnerBank.search([("acc_number", "=", BANK_ACC_NUMBER), ("partner_id", "=", company.partner_id.id)])
created = not account
if not account:
    account = PartnerBank.create(
        {
            "acc_number": BANK_ACC_NUMBER,
            "partner_id": company.partner_id.id,
            "bank_id": bank.id,
            "acc_holder_name": BANK_HOLDER,
            # company_id sengaja tidak diisi: di CE field ini related ke partner_id.company_id; menulisnya bisa
            # ikut mengubah partner perusahaan PB.
        }
    )
if not account.allow_out_payment:
    # Verifikasi rekening = keputusan Finance; di gladi lokal admin bertindak sebagai Finance (pola smoke G26).
    env["res.partner.bank"].browse(account.id).write({"allow_out_payment": True})
note("rekening bank PLACEHOLDER", account, created)

transfer, created = bank_journal("TRFO", "Transfer Bank Online PB (DEMO)", {"bank_account_id": account.id})
if not transfer.bank_account_id:
    transfer.write({"bank_account_id": account.id})
    say(f"jurnal TRFO id={transfer.id}: rekening bank diisi")
elif transfer.bank_account_id != account:
    warn(f"jurnal TRFO id={transfer.id} memakai rekening lain id={transfer.bank_account_id.id}; tidak diubah")
note("jurnal transfer TRFO", transfer, created)

calendar, created = get_or_create(
    "resource.calendar",
    [("name", "=", CALENDAR_NAME), ("company_id", "=", company.id)],
    {
        "name": CALENDAR_NAME,
        "tz": "Asia/Jakarta",
        "company_id": company.id,
        "attendance_ids": [Command.clear()]
        + [
            Command.create(
                {
                    "name": "Ambil online",
                    "dayofweek": str(day),
                    "hour_from": CALENDAR_HOURS[0],
                    "hour_to": CALENDAR_HOURS[1],
                    "day_period": "full_day",
                }
            )
            for day in range(7)
        ],
    },
    "kalender ambil",
)
note("kalender ambil", calendar, created)

# ---------------------------------------------------------------- user teknis (TANPA API key)
group_channel = env.ref("efn_online_order.group_online_channel")
group_user = env.ref("base.group_user")
Users = env["res.users"].sudo().with_context(active_test=False, no_reset_password=True)
user = Users.search([("login", "=", USER_LOGIN)])
created = not user
if not user:
    user = Users.create(
        {
            "name": "Kanal online PB (user teknis)",
            "login": USER_LOGIN,
            "company_id": company.id,
            "company_ids": [Command.set(company.ids)],
            "group_ids": [Command.set([group_user.id, group_channel.id])],
        }
    )
if user.company_ids != company or user.company_id != company:
    abort(f"user {USER_LOGIN} terikat ke company lain ({', '.join(user.company_ids.mapped('name'))}); bereskan manual")
_extra = user.all_group_ids - env["efn.online.channel"]._efn_online_allowed_groups()
if group_channel not in user.all_group_ids or _extra:
    abort(f"grup user {USER_LOGIN} tidak sesuai SEC-101 (lebih: {', '.join(_extra.mapped('name')) or '-'})")
if not user.active:
    warn(f"user {USER_LOGIN} diarsipkan; aktifkan manual bila memang dipakai")
if user.api_key_ids:
    say(f"user {USER_LOGIN} sudah punya {len(user.api_key_ids)} API key (tidak disentuh)")
note(f"user teknis {USER_LOGIN}", user, created)

# ---------------------------------------------------------------- kanal PB
Channel = env["efn.online.channel"].sudo().with_context(active_test=False)
_other = Channel.search([("user_ids", "in", user.ids), ("code", "!=", CHANNEL_CODE)])
if _other:
    abort(f"user {USER_LOGIN} sudah dipakai kanal {', '.join(_other.mapped('code'))} (L-37: satu user satu kanal)")
channel = Channel.search([("code", "=", CHANNEL_CODE)])
created = not channel
links = {
    "company_id": company.id,
    "pricelist_id": pricelist.id,
    "partner_id": partner.id,
    "team_id": team.id,
    "cash_rounding_id": rounding.id,
    "journal_titipan_id": titipan.id,
    "efn_transfer_journal_id": transfer.id,
}
if not channel:
    channel = Channel.create({**CHANNEL_VALS, **links, "user_ids": [Command.set(user.ids)]})
else:
    if channel.company_id != company:
        abort(f"kanal {CHANNEL_CODE} id={channel.id} milik company {channel.company_id.name}, bukan PB")
    fill = {}
    for fname, value in links.items():
        if not channel[fname]:
            fill[fname] = value
        elif channel[fname].id != value:
            warn(f"kanal {CHANNEL_CODE}.{fname} = id {channel[fname].id} (rencana {value}); tidak diubah")
    for fname, value in CHANNEL_VALS.items():
        if fname != "active" and channel[fname] != value:
            warn(f"kanal {CHANNEL_CODE}.{fname} = {channel[fname]!r} (rencana {value!r}); tidak diubah")
    if not channel.active:
        fill["active"] = True
    if user not in channel.user_ids:
        if channel.user_ids:
            warn(f"kanal {CHANNEL_CODE} punya user teknis lain ({', '.join(channel.user_ids.mapped('login'))}); {USER_LOGIN} tidak ditautkan")
        else:
            fill["user_ids"] = [Command.set(user.ids)]
    if fill:
        channel.write(fill)
        say(f"kanal {CHANNEL_CODE}: diisi {', '.join(sorted(fill))}")
note(f"kanal {CHANNEL_CODE}", channel, created)

# ---------------------------------------------------------------- outlet
Outlet = env["efn.online.outlet"].sudo().with_context(active_test=False)
outlet_rows = []
for code, seq in OUTLETS:
    wh = warehouses[code]
    outlet = Outlet.search([("channel_id", "=", channel.id), ("code", "=", code)])
    if outlet:
        if outlet.warehouse_id != wh:
            warn(f"outlet {code} memakai gudang {outlet.warehouse_id.code}; tidak diubah")
        if not outlet.fiscal_position_id:
            outlet.write({"fiscal_position_id": fp.id})
        elif outlet.fiscal_position_id != fp:
            warn(f"outlet {code} memakai FP {outlet.fiscal_position_id.name}; tidak diubah")
        if not outlet.active:
            warn(f"outlet {code} nonaktif; tidak diaktifkan otomatis")
        outlet_rows.append(f"{code} id={outlet.id} (sudah ada)")
        continue
    vals = {
        **OUTLET_DEFAULTS,
        "channel_id": channel.id,
        "code": code,
        "public_name": f"Paris Baguette {wh.name}",
        "sequence": seq,
        "warehouse_id": wh.id,
        "fiscal_position_id": fp.id,
        "resource_calendar_id": calendar.id,
    }
    if HAS_SLOT:
        vals.update(SLOT_VALS)
    outlet = Outlet.create(vals)
    outlet_rows.append(f"{code} id={outlet.id} (baru)")
say("outlet: " + "; ".join(outlet_rows))
summary.append("outlet: " + "; ".join(outlet_rows))

# ---------------------------------------------------------------- publikasi produk
Template = env["product.template"].sudo()
candidates = Template.search(
    [
        ("company_id", "=", company.id),
        ("active", "=", True),
        ("sale_ok", "=", True),
        ("list_price", ">", 0),
        ("pos_categ_ids.name", "=like", POS_CATEG_PATTERN),
    ]
)
eligible = candidates.filtered(lambda t: not t.taxes_id or t.taxes_id._filter_taxes_by_company(company))
skipped_tax = len(candidates) - len(eligible)
if skipped_tax:
    warn(f"{skipped_tax} produk dilewati: pajak jual tidak memuat pajak company PB (constraint kanal)")
to_link = eligible.filtered(lambda t: channel not in t.efn_online_channel_ids)
for start in range(0, len(to_link), 200):
    to_link[start : start + 200].write({"efn_online_channel_ids": [Command.link(channel.id)]})
published_total = Template.search_count([("efn_online_channel_ids", "in", channel.ids)])
no_fp_tax = eligible.filtered(lambda t: not t.taxes_id)
if no_fp_tax:
    warn(f"{len(no_fp_tax)} produk terpublikasi tanpa pajak jual (PB1 tidak terhitung untuk produk itu)")
say(f"produk: kandidat {len(candidates)}, baru dipublikasikan {len(to_link)}, total di kanal {published_total}")
summary.append(f"produk: kandidat {len(candidates)}, baru {len(to_link)}, total di kanal {published_total}")

# ---------------------------------------------------------------- verifikasi akhir (baca saja)
if channel._efn_online_transfer_account() is None:
    abort("rekening transfer kanal tidak lengkap/terverifikasi (bank_transfer tidak akan berfungsi)")
if not channel.active:
    abort(f"kanal {CHANNEL_CODE} tidak aktif setelah skrip")

if os.environ.get("PB_ONLINE_DRY_RUN") == "1":
    env.cr.rollback()
    say("DRY RUN: semua perubahan di-rollback")
else:
    env.cr.commit()
    say("commit")
say(f"== RINGKASAN kanal {CHANNEL_CODE} id={channel.id} company={company.id}")
for line in summary:
    say(f"  {line}")
print("PB_ONLINE_DONE", flush=True)
