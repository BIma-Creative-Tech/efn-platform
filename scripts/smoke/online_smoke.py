#!/usr/bin/env python3
"""Smoke E2E penjualan online G17 (dev saja) lewat Traefik publik — docs/04 §10, ADR-034/ADR-019 (Proposed).

Dijalankan scripts/smoke/run-e2e-smoke.sh tahap `o` di container python:3.12 pada network `efn_edge` (klien publik),
dengan `--add-host api.efn.local/app.efn.local:<IP traefik>`; TLS diverifikasi terhadap sertifikat edge dev (tanpa -k).
Memakai helper `bff_smoke.py` (ALTCHA, OTP dari log console BFF dev, request HTTPS). Tidak ada secret di file ini;
nomor HP fiktif acak per run (env SMOKE_PHONE); state per member di SMOKE_STATE (JSON, direktori kerja smoke).

Subperintah (argumen tambahan setelah nama):
  login                         login OTP member (state SMOKE_STATE)
  catalog                       katalog publik: kanal/outlet, kategori, produk, detail, gambar, stok, 404 gambar asing
  quote <key:qty,...> [label]   PUT /v1/cart lalu POST /v1/cart/quote (simpan quote di state[label])
  checkout [label]              accept_total salah -> 409; checkout (Idempotency-Key L-8) -> 201; klik ganda -> order
                                sama; body beda -> 409 idempotency_conflict; tanpa key -> 400
  quote_promo <key:qty,...> [label]  G19: PUT cart + quote berkode promo dev SMOKE10 (benar/salah/format salah);
                                simpan quote berpromo di state[label] untuk checkout_only
  checkout_only [label]         checkout tanpa uji negatif (order tambahan)
  pay [label] [paid|expired]    simulate-payment (webhook mock bertanda tangan HMAC) dev
  wait [label] <order_status> <part_status> [detik]   polling GET /v1/orders/:id
  pickup [label]                GET kode ambil (409 bila belum confirmed) -> state (hanya untuk serah terima smoke)
  anti_enum <state_lain> [label]  member ini membaca/membatalkan/meminta kode order member lain -> 404
  dsar_open                     reauth step-up -> DELETE /v1/me -> 409 open_online_orders
  killswitch                    ONLINE_ENABLED=false: cart/quote/order -> 503 online_unavailable, katalog tetap
  web_flow                      lewat Next.js app.efn.local: login cookie -> katalog -> cart -> quote -> checkout x2
                                (key server-side) -> status -> simulate bayar -> pickup-code route
"""

import base64
import hashlib
import json
import os
import re
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bff_smoke as b  # noqa: E402

STATE = os.environ.get("SMOKE_STATE", "/work/online_state.json")
PHONE = os.environ.get("SMOKE_PHONE", "")
CHANNEL, OUTLET = "RTL", "TOKO-01"
SMOKE_PREFIX = "SMOKE "
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
PICKUP_RE = re.compile(r"^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}$")


def load():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def save(st):
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(st, fh)


def catalog_state():
    with open("/work/online_catalog.json", encoding="utf-8") as fh:
        return json.load(fh)


def code_of(body):
    return ((body or {}).get("error") or {}).get("code") if isinstance(body, dict) else None


def auth(st):
    return {"Authorization": f"Bearer {st['token']}"}


def idem_key(quote_id):
    """L-8: kunci yang sama dengan route handler Next.js (lib/orders-route.ts)."""
    d = hashlib.sha256(("efn:checkout:v1:" + quote_id).encode()).digest()
    return "chk1_" + base64.urlsafe_b64encode(d).decode().rstrip("=")


# ------------------------------------------------------------------------------------------------------- login
def cmd_login():
    st = load()
    _, ch, _, _ = b.req("GET", "/v1/auth/challenge")
    altcha = b.solve_altcha(ch)
    off = b.log_offset()
    s, body, _, _ = b.req("POST", "/v1/auth/otp/request", body={"phone": PHONE, "altcha": altcha})
    code = b.wait_otp(b.mask(PHONE), off)
    s, tok, _, _ = b.req(
        "POST", "/v1/auth/otp/verify", body={"challenge_id": body["challenge_id"], "code": code, "age_confirmed": True}
    )
    st["token"] = tok["access_token"]
    st["phone_masked"] = b.mask(PHONE)
    save(st)
    b.result("o.login", s == 200 and bool(st["token"]), http=s, masked=b.mask(PHONE))


# ----------------------------------------------------------------------------------------------------- catalog
def cmd_catalog():
    s, ch, _, _ = b.req("GET", "/v1/catalog/channels")
    rtl = next((c for c in (ch or {}).get("items", []) if c.get("channel_code") == CHANNEL), None)
    outlets = [o.get("outlet_code") for o in (rtl or {}).get("outlets", [])]
    b.result(
        "o.catalog_channels",
        s == 200
        and rtl is not None
        and rtl.get("company_code") == "RTL"
        and OUTLET in outlets
        and rtl.get("fulfilment") == {"pickup": True, "delivery": False},
        http=s,
        channel=rtl,
    )
    s, cats, _, _ = b.req("GET", f"/v1/catalog/categories?channel={CHANNEL}")
    b.result("o.catalog_categories", s == 200 and isinstance((cats or {}).get("items"), list), http=s)
    items, cursor, pages = [], None, 0
    while pages < 10:
        q = f"/v1/catalog/products?channel={CHANNEL}&limit=50" + (f"&cursor={cursor}" if cursor else "")
        s, prod, _, _ = b.req("GET", q)
        if s != 200:
            break
        items += prod.get("items", [])
        cursor, pages = prod.get("next_cursor"), pages + 1
        if not cursor:
            break
    smoke = {p["name"]: p for p in items if p.get("name", "").startswith(SMOKE_PREFIX)}
    keys = {"rice": "Beras", "oil": "Minyak", "bag": "Tas", "scarce": "Terbatas"}
    found = {k: next((p for n, p in smoke.items() if w in n), None) for k, w in keys.items()}
    shape_ok = all(
        p
        and isinstance(p.get("price_incl"), (int, float))
        and p["price_incl"] >= p["price_excl"] > 0
        and isinstance(p.get("tax_labels"), list)
        and p.get("max_qty")
        for p in found.values()
    )
    dumped = json.dumps(items)
    no_odoo_url = "/web/" not in dumped and "odoo" not in dumped.lower() and "quote_hash" not in dumped
    b.result("o.catalog_products", s == 200 and shape_ok and no_odoo_url, http=s, smoke_items=len(smoke))
    s, q, _, _ = b.req("GET", f"/v1/catalog/products?channel={CHANNEL}&q=Beras&limit=5")
    b.result("o.catalog_search", s == 200 and any("Beras" in p["name"] for p in q.get("items", [])), http=s)
    rice = found["rice"] or {}
    s, det, _, _ = b.req("GET", f"/v1/catalog/products/{rice.get('product_id')}?channel={CHANNEL}")
    b.result(
        "o.catalog_detail_text",
        s == 200 and isinstance(det.get("description"), str) and "<" not in det.get("description", "<"),
        http=s,
    )
    sha = rice.get("image_sha")
    img_ok, ctype, cache = False, None, None
    if sha:
        import http.client

        conn = http.client.HTTPSConnection(b.BASE_HOST, 443, context=b.CTX, timeout=30)
        conn.request("GET", f"/v1/catalog/images/{sha}?size=512")
        r = conn.getresponse()
        data = r.read()
        ctype, cache = r.getheader("Content-Type"), r.getheader("Cache-Control")
        img_ok = r.status == 200 and data[:3] == b"\xff\xd8\xff" and "immutable" in (cache or "")
        conn.close()
    b.result("o.catalog_image_jpeg_reencoded", img_ok, sha=bool(sha), ctype=ctype, cache=cache)
    s, _, _, _ = b.req("GET", f"/v1/catalog/images/{'a' * 40}?size=512")
    b.result("o.catalog_image_unknown_404", s == 404, http=s)
    ids = ",".join(str(p["product_id"]) for p in found.values() if p)
    s, stk, _, _ = b.req("GET", f"/v1/catalog/stock?channel={CHANNEL}&outlet={OUTLET}&ids={ids}")
    statuses = {i["product_id"]: i["status"] for i in (stk or {}).get("items", [])}
    b.result(
        "o.catalog_stock",
        s == 200
        and statuses
        and all(v in ("in_stock", "low", "out") for v in statuses.values())
        and "qty" not in json.dumps(stk),
        http=s,
        statuses=statuses,
    )
    # SEC-96 (L-16b): stok hanya untuk produk katalog kanal; id lain -> 404 (tanpa memanggil Odoo), juga bila dicampur
    s1, b1, _, _ = b.req("GET", f"/v1/catalog/stock?channel={CHANNEL}&outlet={OUTLET}&ids=999999")
    s2, b2, _, _ = b.req("GET", f"/v1/catalog/stock?channel={CHANNEL}&outlet={OUTLET}&ids={ids},999998")
    s3, b3, _, _ = b.req("GET", f"/v1/catalog/stock?channel={CHANNEL}&outlet={OUTLET}&ids=999999")  # cache negatif
    b.result(
        "o.catalog_stock_non_catalog_404",
        (s1, s2, s3) == (404, 404, 404) and code_of(b1) == code_of(b2) == "not_found",
        http=[s1, s2, s3],
    )
    with open("/work/online_catalog.json", "w", encoding="utf-8") as fh:
        json.dump({k: (v or {}).get("product_id") for k, v in found.items()}, fh)


# ---------------------------------------------------------------------------------------------- cart & quote
def put_cart(st, lines):
    s, cur, _, _ = b.req("GET", "/v1/cart", headers=auth(st))
    version = (cur or {}).get("version", 0)
    body = {
        "version": version,
        "groups": [{"channel_code": CHANNEL, "outlet_code": OUTLET, "fulfilment": "pickup", "lines": lines}],
    }
    return b.req("PUT", "/v1/cart", body=body, headers=auth(st)) + (version,)


def cmd_quote(spec, label="main"):
    st = load()
    cat = catalog_state()
    lines = [{"product_id": cat[k], "qty": int(q)} for k, q in (x.split(":") for x in spec.split(","))]
    s, cart, _, _, v0 = put_cart(st, lines)
    b.result(
        f"o.cart_put[{label}]",
        s == 200 and cart.get("version") == v0 + 1,
        http=s,
        warnings=(cart or {}).get("warnings"),
    )
    s, q, _, _ = b.req("POST", "/v1/cart/quote", body={}, headers=auth(st))
    g = ((q or {}).get("groups") or [{}])[0]
    ok = (
        s == 200
        and UUID_RE.match(q.get("quote_id", ""))
        and isinstance(q.get("amount_payable_total"), int)
        and g.get("amount_payable") == q.get("amount_payable_total")
        and not g.get("issues")
        and "quote_hash" not in json.dumps(q)
        and all(ln.get("price_unit_incl") and ln.get("tax_labels") for ln in g.get("lines", []))
    )
    b.result(
        f"o.quote[{label}]", ok, http=s, total=(q or {}).get("amount_payable_total"), tax_groups=g.get("tax_groups")
    )
    # G17-QA-04: label pajak ke pelanggan dari Odoo = "PPN 11%" (baris + grup), bukan nama template Inggris
    labels = {lb for ln in g.get("lines", []) for lb in ln.get("tax_labels", [])}
    groups = {tg.get("label") for tg in g.get("tax_groups", [])}
    b.result(
        f"o.quote_tax_label_ppn11[{label}]",
        labels == {"PPN 11%"} and groups == {"PPN 11%"},
        lines=sorted(labels),
        groups=sorted(groups),
    )
    st.setdefault("orders", {})[label] = {"quote_id": q.get("quote_id"), "total": q.get("amount_payable_total")}
    save(st)


PROMO_CODE = "SMOKE10"  # promo_code dev (diskon 10 %) dari odoo_online_smoke.py setup; FIKTIF


def cmd_quote_promo(spec, label="main"):
    """G19 (docs/04 §11.2, L-28/L-34): kode promo lewat BFF -> Odoo nyata. Quote terakhir (SMOKE10) disimpan di
    state[label] sehingga `checkout_only <label>` men-place pesanan berpromo. Kode TIDAK pernah dicetak ke hasil
    selain kode fiktif dev ini. Rate limit promo: 4 quote berkode per member (batas 10 / 15 menit)."""
    st = load()
    cat = catalog_state()
    lines = [{"product_id": cat[k], "qty": int(q)} for k, q in (x.split(":") for x in spec.split(","))]
    s, cart, _, _, v0 = put_cart(st, lines)
    b.result(f"o.promo_cart_put[{label}]", s == 200 and cart.get("version") == v0 + 1, http=s)

    def quote(codes=None):
        body = {} if codes is None else {"promo_codes": codes}
        s_, q_, _, _ = b.req("POST", "/v1/cart/quote", body=body, headers=auth(st))
        return s_, q_ or {}

    def rewards(q_):
        return [ln for g in q_.get("groups", []) for ln in g.get("lines", []) if ln.get("reward")]

    def issues(q_):
        return [i for g in q_.get("groups", []) for i in g.get("issues", [])]

    s0, base = quote()
    base_total = base.get("amount_payable_total")
    # 1. kode benar (huruf kecil dinormalisasi) + kode salah dalam satu permintaan
    s1, q1 = quote([PROMO_CODE.lower(), "NOSUCH99"])
    rw = rewards(q1)
    total1 = q1.get("amount_payable_total")
    expected = round(base_total * 0.9) if isinstance(base_total, int) else None
    b.result(
        f"o.promo_applied_and_invalid[{label}]",
        s0 == 200
        and s1 == 200
        and q1.get("promo") == {"applied": [PROMO_CODE], "invalid": ["NOSUCH99"]}
        and len(rw) == 1
        and rw[0].get("subtotal_incl", 0) < 0
        and expected is not None
        and isinstance(total1, int)
        and abs(total1 - expected) <= 2
        and not issues(q1)
        and "quote_hash" not in json.dumps(q1)
        and "promo_applied" not in json.dumps(q1),
        http=[s0, s1],
        base_total=base_total,
        total=total1,
        reward_lines=len(rw),
        promo=q1.get("promo"),
    )
    # 2. kode salah saja: tidak memblokir (tanpa issues), total tidak berubah, tanpa baris reward, pesan satu bentuk
    s2, q2 = quote(["NOSUCH99"])
    s3, q3 = quote(["ZZZZ0000"])
    b.result(
        f"o.promo_invalid_generic_nonblocking[{label}]",
        s2 == 200
        and s3 == 200
        and q2.get("promo") == {"applied": [], "invalid": ["NOSUCH99"]}
        and q3.get("promo") == {"applied": [], "invalid": ["ZZZZ0000"]}
        and q2.get("amount_payable_total") == base_total == q3.get("amount_payable_total")
        and not rewards(q2)
        and not issues(q2)
        and not issues(q3),
        http=[s2, s3],
    )
    # 3. format salah / lebih dari 3 / bentuk body salah -> 400 invalid_request (L-36), tanpa memanggil Odoo
    bad = {}
    for name, body in {
        "spasi": {"promo_codes": ["bad code!"]},
        "empat": {"promo_codes": ["A1", "B2", "C3", "D4"]},
        "bukan_array": {"promo_codes": "A1"},
        "kunci_lain": {"foo": 1},
    }.items():
        s_, r_, _, _ = b.req("POST", "/v1/cart/quote", body=body, headers=auth(st))
        bad[name] = (s_, code_of(r_))
    b.result(
        f"o.promo_bad_format_400[{label}]",
        all(v == (400, "invalid_request") for v in bad.values()),
        results=bad,
    )
    # 4. quote final berpromo -> dipakai checkout_only (place membawa promo_applied, Odoo menghitung ulang)
    s5, q5 = quote([PROMO_CODE])
    b.result(
        f"o.promo_final_quote[{label}]",
        s5 == 200
        and UUID_RE.match(q5.get("quote_id", ""))
        and q5.get("promo", {}).get("applied") == [PROMO_CODE]
        and isinstance(q5.get("amount_payable_total"), int)
        and q5["amount_payable_total"] < (base_total or 0),
        http=s5,
        total=q5.get("amount_payable_total"),
    )
    st.setdefault("orders", {})[label] = {"quote_id": q5.get("quote_id"), "total": q5.get("amount_payable_total")}
    save(st)


def checkout(st, label, key=None, total=None, quote_id=None):
    o = st["orders"][label]
    qid = quote_id or o["quote_id"]
    h = dict(auth(st))
    if key is not False:
        h["Idempotency-Key"] = key or idem_key(qid)
    return b.req(
        "POST", "/v1/orders", body={"quote_id": qid, "accept_total": o["total"] if total is None else total}, headers=h
    )


def cmd_checkout(label="main", negatives=True):
    st = load()
    o = st["orders"][label]
    if negatives:
        s, body, _, _ = checkout(st, label, key="smoke-wrong-total-" + uuid.uuid4().hex[:12], total=o["total"] + 1000)
        b.result(
            "o.checkout_accept_total_wrong_409",
            s == 409 and code_of(body) == "price_changed",
            http=s,
            code=code_of(body),
        )
    s, body, _, _ = checkout(st, label)
    pay = (body or {}).get("payment") or {}
    part = ((body or {}).get("parts") or [{}])[0]
    ok = (
        s == 201
        and body.get("status") == "awaiting_payment"
        and UUID_RE.match(body.get("order_id", ""))
        and body.get("amount_payable") == o["total"]
        and pay.get("status") == "pending"
        and bool(pay.get("qr_string"))
        and part.get("status") == "placed"
        and part.get("company_code") == "RTL"
        and "pickup_code" not in json.dumps(body)
    )
    b.result(f"o.checkout_201[{label}]", ok, http=s, status=(body or {}).get("status"), code=code_of(body))
    o["order_id"] = (body or {}).get("order_id")
    save(st)
    if not negatives:
        return
    # L-8: klik ganda / reload -> key sama + body sama -> order yang SAMA (quote sudah dihapus setelah checkout)
    s2, body2, _, _ = checkout(st, label)
    b.result(
        "o.checkout_double_click_same_order",
        s2 in (200, 201) and (body2 or {}).get("order_id") == o["order_id"],
        http=s2,
        same=(body2 or {}).get("order_id") == o["order_id"],
        code=code_of(body2),
    )
    s3, body3, _, _ = checkout(st, label, total=o["total"] + 1)
    b.result(
        "o.checkout_same_key_other_body_409",
        s3 == 409 and code_of(body3) == "idempotency_conflict",
        http=s3,
        code=code_of(body3),
    )
    s4, l4, _, _ = b.req("GET", "/v1/orders?limit=20", headers=auth(st))
    n = sum(1 for it in (l4 or {}).get("items", []) if it.get("order_id") == o["order_id"])
    b.result("o.checkout_single_order_in_list", s4 == 200 and n == 1, http=s4, occurrences=n)


def cmd_checkout_nokey(label="main"):
    st = load()
    s, body, _, _ = checkout(st, label, key=False)
    b.result(
        "o.checkout_without_key_400",
        s == 400 and code_of(body) == "idempotency_key_required",
        http=s,
        code=code_of(body),
    )


def cmd_pay(label="main", status="paid"):
    st = load()
    oid = st["orders"][label]["order_id"]
    s, body, _, _ = b.req("POST", f"/v1/orders/{oid}/simulate-payment", body={"status": status}, headers=auth(st))
    b.result(
        f"o.simulate_payment[{label}:{status}]",
        s == 200,
        http=s,
        order_status=(body or {}).get("status"),
        code=code_of(body),
    )


def get_order(st, label):
    return b.req("GET", f"/v1/orders/{st['orders'][label]['order_id']}", headers=auth(st))


def cmd_wait(label, order_status, part_status, timeout="90"):
    st = load()
    deadline = time.time() + int(timeout)
    s, body = None, None
    while time.time() < deadline:
        s, body, _, _ = get_order(st, label)
        part = ((body or {}).get("parts") or [{}])[0]
        if s == 200 and body.get("status") == order_status and part.get("status") == part_status:
            break
        time.sleep(2)
    part = ((body or {}).get("parts") or [{}])[0]
    ok = s == 200 and body.get("status") == order_status and part.get("status") == part_status
    view = {
        "status": (body or {}).get("status"),
        "part": part.get("status"),
        "so": part.get("sale_order_name"),
        "refunded": part.get("refunded_amount"),
        "payable": part.get("amount_payable"),
        "cancel_reason": part.get("cancel_reason"),
        "payment": ((body or {}).get("payment") or {}).get("status"),
        "qr_present": bool(((body or {}).get("payment") or {}).get("qr_string")),
    }
    b.result(f"o.status[{label}]={order_status}/{part_status}", ok, http=s, **view)
    st.setdefault("views", {})[label] = view
    save(st)


def cmd_pickup(label="main"):
    st = load()
    oid = st["orders"][label]["order_id"]
    s, body, _, hdr = b.req("GET", f"/v1/orders/{oid}/pickup-code", headers=auth(st))
    items = (body or {}).get("items") or []
    code = items[0].get("pickup_code") if items else None
    b.result(
        f"o.pickup_code[{label}]",
        s == 200 and bool(code) and bool(PICKUP_RE.match(code or "")),
        http=s,
        items=len(items),
    )
    st["orders"][label]["pickup_code"] = code
    save(st)


def cmd_pickup_in_review(label="main"):
    """L-14: bagian `review` -> kode ambil ditahan (409 order_in_review)."""
    st = load()
    oid = st["orders"][label]["order_id"]
    s, body, _, _ = b.req("GET", f"/v1/orders/{oid}/pickup-code", headers=auth(st))
    b.result(
        f"o.pickup_code_in_review_409[{label}]",
        s == 409 and code_of(body) == "order_in_review",
        http=s,
        code=code_of(body),
    )


def cmd_pickup_unavailable(label="main"):
    st = load()
    oid = st["orders"][label]["order_id"]
    s, body, _, _ = b.req("GET", f"/v1/orders/{oid}/pickup-code", headers=auth(st))
    b.result(
        f"o.pickup_code_unavailable[{label}]",
        s == 409 and code_of(body) == "pickup_code_unavailable",
        http=s,
        code=code_of(body),
    )


def cmd_anti_enum(other_state, label="main"):
    st = load()
    with open(other_state, encoding="utf-8") as fh:
        other = json.load(fh)
    oid = other["orders"][label]["order_id"]
    res = {}
    for name, (m, p, body) in {
        "get": ("GET", f"/v1/orders/{oid}", None),
        "cancel": ("POST", f"/v1/orders/{oid}/cancel", {}),
        "pickup": ("GET", f"/v1/orders/{oid}/pickup-code", None),
        "simulate": ("POST", f"/v1/orders/{oid}/simulate-payment", {"status": "paid"}),
        "random_uuid": ("GET", f"/v1/orders/{uuid.uuid4()}", None),
        "not_uuid": ("GET", "/v1/orders/12345", None),
        "sql_like": ("GET", "/v1/orders/1%20or%201=1", None),
    }.items():
        s, rb, _, _ = b.req(m, p, body=body, headers=auth(st))
        res[name] = (s, code_of(rb))
    ok = all(v == (404, "not_found") for v in res.values())
    b.result("o.anti_enumeration_404", ok, results=res)
    s, rb, _, _ = b.req("GET", f"/v1/orders/{oid}")
    b.result("o.order_without_token_401", s == 401, http=s)


def cmd_dsar_open():
    st = load()
    # Quote sah disiapkan SEBELUM hapus akun dikonfirmasi: checkout dengan quote ini setelah 202 harus ditolak
    # karena akun sedang dihapus (bukan karena quote tidak ada).
    cat = catalog_state()
    put_cart(st, [{"product_id": cat["bag"], "qty": 1}])
    _, pre_q, _, _ = b.req("POST", "/v1/cart/quote", body={}, headers=auth(st))
    off = b.log_offset()
    s, r1, _, _ = b.req("POST", "/v1/me/reauth", body={}, headers=auth(st))
    code = b.wait_otp(st["phone_masked"], off)
    s, r2, _, _ = b.req(
        "POST", "/v1/me/reauth", body={"challenge_id": r1["challenge_id"], "code": code}, headers=auth(st)
    )
    s, r3, _, _ = b.req("DELETE", "/v1/me", headers={**auth(st), "X-Step-Up-Token": r2["step_up_token"]})
    b.result(
        "o.dsar_delete_open_online_orders_409",
        # W-18 (Q-P18): tanpa ?confirm_open_orders=true tetap 409; teks menyebut batas 14 hari
        s == 409
        and code_of(r3) == "open_online_orders"
        and (r3.get("error") or {}).get("max_defer_days") == 14
        and "14 hari" in (r3.get("error") or {}).get("message", ""),
        http=s,
        code=code_of(r3),
        body=r3,
    )
    s, me, _, _ = b.req("GET", "/v1/me", headers=auth(st))
    b.result("o.dsar_account_still_active", s == 200, http=s)
    s, exp, _, _ = b.req("GET", "/v1/me/data", headers={**auth(st), "X-Step-Up-Token": r2["step_up_token"]})
    dumped = json.dumps(exp)
    has_orders = "online" in dumped and st["orders"]["main"]["order_id"] in dumped
    no_secret = "pickup_code" not in dumped and "qr_string" not in dumped
    b.result(
        "o.dsar_export_orders_no_secret",
        s == 200 and has_orders and no_secret,
        http=s,
        has_orders=has_orders,
        no_secret=no_secret,
    )
    # W-18 / L-21a: konfirmasi eksplisit (token step-up yang sama) -> 202 + batas paling lambat 14 hari
    s, r4, _, _ = b.req(
        "DELETE", "/v1/me?confirm_open_orders=true", headers={**auth(st), "X-Step-Up-Token": r2["step_up_token"]}
    )
    deadline = (r4 or {}).get("erasure_deadline")
    b.result(
        "o.dsar_delete_confirmed_202_deadline",
        s == 202
        and (r4 or {}).get("open_online_orders", 0) >= 1
        and isinstance(deadline, str)
        and r4.get("request_id"),
        http=s,
        open_online_orders=(r4 or {}).get("open_online_orders"),
        erasure_deadline=deadline,
    )
    rid, receipt = (r4 or {}).get("request_id"), (r4 or {}).get("receipt_token")
    # L-22: status lewat receipt (tanpa sesi) dan daftar permintaan member memuat batas yang sama
    s5, r5, _, _ = b.req("GET", f"/v1/dsar/requests/{rid}", headers={"X-DSAR-Receipt": receipt or "x"})
    s6, r6, _, _ = b.req("GET", "/v1/me/requests", headers=auth(st))
    items = r6 if isinstance(r6, list) else (r6 or {}).get("items", [])
    mine = next((i for i in items if isinstance(i, dict) and i.get("id") == rid), {})
    b.result(
        "o.dsar_status_erasure_deadline",
        s5 == 200
        and (r5 or {}).get("erasure_deadline") == deadline
        and (r5 or {}).get("open_online_orders", 0) >= 1
        and s6 == 200
        and mine.get("erasure_deadline") == deadline,
        receipt_http=s5,
        receipt_deadline=(r5 or {}).get("erasure_deadline"),
        list_http=s6,
        list_deadline=mine.get("erasure_deadline"),
    )
    # Checkout baru selama permintaan hapus aktif ditolak (L-21a: 409 account_deleting)
    s7, r7, _, _ = b.req(
        "POST",
        "/v1/orders",
        body={"quote_id": (pre_q or {}).get("quote_id"), "accept_total": (pre_q or {}).get("amount_payable_total")},
        headers={**auth(st), "Idempotency-Key": "smoke-deleting-" + uuid.uuid4().hex[:12]},
    )
    b.result(
        "o.dsar_checkout_while_deleting_409", s7 == 409 and code_of(r7) == "account_deleting", http=s7, code=code_of(r7)
    )


def cmd_killswitch():
    st = load()
    res = {}
    res["cart_get"] = b.req("GET", "/v1/cart", headers=auth(st))[:2]
    res["cart_put"] = b.req("PUT", "/v1/cart", body={"version": 0, "groups": []}, headers=auth(st))[:2]
    res["quote"] = b.req("POST", "/v1/cart/quote", body={}, headers=auth(st))[:2]
    res["orders_list"] = b.req("GET", "/v1/orders", headers=auth(st))[:2]
    res["checkout"] = b.req(
        "POST",
        "/v1/orders",
        body={"quote_id": str(uuid.uuid4()), "accept_total": 1000},
        headers={**auth(st), "Idempotency-Key": "smoke-killswitch-" + uuid.uuid4().hex[:12]},
    )[:2]
    flat = {k: (s, code_of(body)) for k, (s, body) in res.items()}
    b.result("o.killswitch_503", all(v == (503, "online_unavailable") for v in flat.values()), results=flat)
    s, _, _, _ = b.req("GET", "/v1/catalog/channels")
    b.result("o.killswitch_catalog_separate", s == 200, http=s)
    s, _, _, _ = b.req("GET", "/v1/me", headers=auth(st))
    b.result("o.killswitch_rest_of_app_ok", s == 200, http=s)


# ------------------------------------------------------------------------------------------- Next.js (FE↔BFF)
def cmd_web_flow():
    host = b.WEB_HOST
    origin = {"Origin": f"https://{host}"}
    _, ch, _, _ = b.req("GET", "/api/auth/challenge", host=host)
    altcha = b.solve_altcha(ch)
    off = b.log_offset()
    _, body, _, _ = b.req(
        "POST", "/api/auth/otp/request", host=host, body={"phone": PHONE, "altcha": altcha}, headers=origin
    )
    code = b.wait_otp(b.mask(PHONE), off)
    s, _, cookies, _ = b.req(
        "POST",
        "/api/auth/otp/verify",
        host=host,
        body={"challenge_id": body["challenge_id"], "code": code, "age_confirmed": True},
        headers=origin,
    )
    sess = next((c for c in cookies if c.startswith("efn_session=")), "").split(";")[0]
    b.result("o.web_login_cookie", s == 200 and bool(sess), http=s)
    ck = {"Cookie": sess}
    s, chans, _, _ = b.req("GET", "/api/catalog/channels", host=host)
    rtl = next((c for c in (chans or {}).get("items", []) if c.get("channel_code") == CHANNEL), None)
    b.result("o.web_catalog_channels", s == 200 and rtl is not None, http=s)
    s, prods, _, _ = b.req("GET", f"/api/catalog/products?channel={CHANNEL}&q=Minyak&limit=10", host=host)
    oil = next((p for p in (prods or {}).get("items", []) if p.get("name", "").startswith(SMOKE_PREFIX)), None)
    b.result(
        "o.web_catalog_products",
        s == 200 and oil is not None and isinstance(oil.get("price_incl"), (int, float)),
        http=s,
    )
    s, cart, _, _ = b.req("GET", "/api/cart", host=host, headers={**ck, "Sec-Fetch-Site": "same-origin"})
    version = (cart or {}).get("version", 0)
    lines = [{"product_id": (oil or {}).get("product_id"), "qty": 2, "price": 1}]  # harga klien harus dibuang
    put = {
        "version": version,
        "groups": [{"channel_code": CHANNEL, "outlet_code": OUTLET, "fulfilment": "pickup", "lines": lines}],
    }
    s, _, _, _ = b.req("PUT", "/api/cart", host=host, body=put, headers={**ck, "Origin": "https://evil.example"})
    b.result("o.web_cart_cross_origin_403", s == 403, http=s)
    s, cart, _, _ = b.req("PUT", "/api/cart", host=host, body=put, headers={**ck, **origin})
    b.result("o.web_cart_put", s == 200 and (cart or {}).get("version") == version + 1, http=s, body=cart)
    s, q, _, _ = b.req("POST", "/api/cart/quote", host=host, body={"amount": 1}, headers={**ck, **origin})
    total = (q or {}).get("amount_payable_total")
    b.result(
        "o.web_quote",
        s == 200 and isinstance(total, int) and total > 0 and "quote_hash" not in json.dumps(q),
        http=s,
        total=total,
    )
    co = {"quote_id": (q or {}).get("quote_id"), "accept_total": total}
    s, c1, _, _ = b.req(
        "POST", "/api/orders", host=host, body=co, headers={**ck, **origin, "Idempotency-Key": "browser-key-ignored-1"}
    )
    s2, c2, _, _ = b.req(
        "POST", "/api/orders", host=host, body=co, headers={**ck, **origin, "Idempotency-Key": "browser-key-ignored-2"}
    )
    oid = (c1 or {}).get("order_id")
    b.result(
        "o.web_checkout_201",
        s == 201 and UUID_RE.match(oid or "") and (c1 or {}).get("status") == "awaiting_payment",
        http=s,
        body=c1,
    )
    b.result(
        "o.web_checkout_double_click_same_order",
        s2 in (200, 201) and (c2 or {}).get("order_id") == oid,
        http=s2,
        same=(c2 or {}).get("order_id") == oid,
    )
    s, od, _, _ = b.req("GET", f"/api/orders/{oid}", host=host, headers={**ck, "Sec-Fetch-Site": "same-origin"})
    pay = (od or {}).get("payment") or {}
    b.result(
        "o.web_order_view",
        s == 200 and od.get("status") == "awaiting_payment" and bool(pay.get("qr_string")),
        http=s,
        keys=sorted(od or {}),
    )
    s, sp, _, _ = b.req(
        "POST", f"/api/orders/{oid}/simulate-payment", host=host, body={"status": "paid"}, headers={**ck, **origin}
    )
    b.result("o.web_simulate_payment", s == 200, http=s, body=sp)
    deadline, st = time.time() + 90, None
    while time.time() < deadline:
        s, od, _, _ = b.req("GET", f"/api/orders/{oid}", host=host, headers={**ck, "Sec-Fetch-Site": "same-origin"})
        st = ((od or {}).get("parts") or [{}])[0].get("status")
        if st == "confirmed":
            break
        time.sleep(2)
    b.result("o.web_order_confirmed", st == "confirmed", status=(od or {}).get("status"), part=st)
    s, pc, _, hdrs = b.req(
        "GET", f"/api/orders/{oid}/pickup-code", host=host, headers={**ck, "Sec-Fetch-Site": "same-origin"}
    )
    items = (pc or {}).get("items") or []
    b.result("o.web_pickup_code", s == 200 and items and PICKUP_RE.match(items[0].get("pickup_code", "")), http=s)
    s, _, _, _ = b.req("GET", f"/api/orders/{uuid.uuid4()}", host=host, headers={**ck, "Sec-Fetch-Site": "same-origin"})
    b.result("o.web_unknown_order_404", s == 404, http=s)
    with open("/work/online_web.json", "w", encoding="utf-8") as fh:
        json.dump({"order_id": oid, "pickup_code": items[0].get("pickup_code") if items else None}, fh)


# ------------------------------------------------------------------- event Odoo -> BFF (jaringan internal)
def cmd_events(part_id, order_id, legacy_key=""):
    """Dijalankan di network efn_internal dengan /s/{efn_ca_crt,odoo_bff_api_key,odoo_online_event_hmac}.txt
    di-mount read-only (secret dibaca di container, tidak dicetak). §10.5: service key + HMAC + replay 300 s.
    G18-QA-01: kunci event `online:<platform_order_ref>:<event>:<seq>` + `event_id` uuid; seq berbasis waktu.
    `legacy_key` = kunci format lama (id lokal Odoo) milik bagian LAIN yang sudah tercatat di BFF (simulasi restore)."""
    import hmac
    import http.client
    import ssl

    ctx = ssl.create_default_context(cafile="/s/efn_ca_crt.txt")
    svc = open("/s/odoo_bff_api_key.txt", encoding="utf-8").read().strip()
    sec = open("/s/odoo_online_event_hmac.txt", encoding="utf-8").read().strip()

    def post(body, *, t=None, secret=sec, key=svc, tamper=False, sign=True):
        raw = json.dumps(body, separators=(",", ":")).encode()
        ts = int(time.time()) if t is None else t
        sig = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
        if tamper:
            raw = raw.replace(b'"seq":1,', b'"seq":9,')
        h = {"Content-Type": "application/json"}
        if key:
            h["X-EFN-Service-Key"] = key
        if sign:
            h["X-EFN-Signature"] = f"t={ts},v1={sig}"
        conn = http.client.HTTPSConnection("api-gateway", 3000, context=ctx, timeout=20)
        conn.request("POST", "/v1/pos/online/order-events", body=raw, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, None

    def event(evname, state, seq, **extra):
        return {
            "schema_version": 1,
            "idempotency_key": f"online:{part_id}:{evname}:{seq}",
            "event_id": str(uuid.uuid4()),
            "seq": seq,
            "platform_order_ref": part_id,
            "checkout_ref": order_id,
            "channel_code": CHANNEL,
            "company_code": "RTL",
            "event": evname,
            "state": state,
            "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "refund": None,
            **extra,
        }

    ev = event("ready", "ready", 1)
    res = {
        "unsigned": post(ev, sign=False),
        "wrong_secret": post(ev, secret="x" * 64),
        "replay_window_600s": post(ev, t=int(time.time()) - 600),
        "future_600s": post(ev, t=int(time.time()) + 600),
        "body_tampered": post(ev, tamper=True),
        "no_service_key": post(ev, key=None),
    }
    flat = {k: (s, code_of(body)) for k, (s, body) in res.items()}
    b.result("o.event_bad_hmac_or_replay_401", all(v[0] == 401 for v in flat.values()), results=flat)
    s1, b1 = post(ev)
    s2, b2 = post(ev)
    b.result(
        "o.event_valid_stale_seq_ignored",
        s1 == 200 and (b1 or {}).get("status") == "ignored_stale",
        http=s1,
        body=b1,
    )
    b.result("o.event_duplicate_idempotent", s2 == 200 and (b2 or {}).get("status") == "duplicate", http=s2, body=b2)
    s, body = post({**event("ready", "ready", 2), "checkout_ref": str(uuid.uuid4())})
    b.result("o.event_unknown_checkout_404", s == 404, http=s, code=code_of(body))
    s, body = post(event("ready", "ready", 3, company_code="FNB"))
    b.result(
        "o.event_company_mismatch_409", s == 409 and code_of(body) == "company_mismatch", http=s, code=code_of(body)
    )
    # G18-QA-01: kunci sama (ref + event + seq + event_id), isi berbeda -> 409 event_conflict (bukan duplikat diam)
    now_seq = int(time.time()) - 1767225600 + 100000  # di depan seq berbasis waktu Odoo (detik sejak 2026-01-01)
    base = event("ready", "ready", now_seq)
    s1, b1 = post(base)
    s2, b2 = post({**base, "state": "review"})
    b.result(
        "o.event_same_key_other_payload_409",
        s1 == 200 and s2 == 409 and code_of(b2) == "event_conflict",
        first=(s1, (b1 or {}).get("status")),
        second=(s2, code_of(b2)),
    )
    # G18-QA-01: restore DB Odoo -> id lokal terpakai ulang: kunci lama milik bagian lain tetap diproses + anomali
    if legacy_key:
        off = b.log_offset()
        s, body = post({**event("picked_up", "picked_up", now_seq + 1), "idempotency_key": legacy_key})
        anomaly = False
        for _ in range(20):
            with open(b.LOG, "rb") as fh:
                fh.seek(off)
                text = fh.read().decode("utf-8", "replace")
            if 'EFN_ONLINE_ANOMALY {"kind":"odoo_event_key_reused"' in text and part_id in text:
                anomaly = True
                break
            time.sleep(0.5)
        b.result(
            "o.event_legacy_key_reused_processed_with_anomaly",
            s == 200 and (body or {}).get("status") in ("applied", "ignored_stale") and anomaly,
            http=s,
            status=(body or {}).get("status"),
            anomaly=anomaly,
        )
    else:
        b.result("o.event_legacy_key_reused_processed_with_anomaly", False, reason="tidak ada kunci lama di BFF")


if __name__ == "__main__":
    cmds = {
        "events": cmd_events,
        "login": cmd_login,
        "catalog": cmd_catalog,
        "quote": cmd_quote,
        "quote_promo": cmd_quote_promo,
        "checkout": cmd_checkout,
        "checkout_nokey": cmd_checkout_nokey,
        "checkout_only": lambda label="main": cmd_checkout(label, negatives=False),
        "pay": cmd_pay,
        "wait": cmd_wait,
        "pickup": cmd_pickup,
        "pickup_unavailable": cmd_pickup_unavailable,
        "pickup_in_review": cmd_pickup_in_review,
        "anti_enum": cmd_anti_enum,
        "dsar_open": cmd_dsar_open,
        "killswitch": cmd_killswitch,
        "web_flow": cmd_web_flow,
    }
    try:
        cmds[sys.argv[1]](*sys.argv[2:])
    except Exception as exc:  # noqa: BLE001 - satu baris FAIL, bukan traceback tanpa ringkasan
        b.result(f"o.{sys.argv[1]}_exception", False, error=f"{type(exc).__name__}: {exc}"[:300])
