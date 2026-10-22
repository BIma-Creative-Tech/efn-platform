#!/usr/bin/env python3
"""Smoke E2E G23 slot pre-order (dev): cart pickup_slot -> quote -> checkout -> pickup_slot pesanan.

Juga kapasitas slot dan kasus negatif.
"""

import base64
import hashlib
import os
import sys
import time

sys.path.insert(0, "/work")
import booking_smoke as b  # scripts/smoke/booking_smoke.py  # noqa: E402

DATE = os.environ["SMOKE_DATE"]


def idem_key(qid):
    d = hashlib.sha256(("efn:checkout:v1:" + qid).encode()).digest()
    return "chk1_" + base64.urlsafe_b64encode(d).decode().rstrip("=")


def first_product(st):
    s, cats = b.req("GET", "/v1/catalog/products?channel=PB&limit=20")
    for p in (cats or {}).get("items", []):
        if p.get("available", True) is not False:
            return p["product_id"]
    raise SystemExit(f"tidak ada produk PB (http {s})")


def put_cart(st, slot=None, outlet="RESTO-01"):
    s, cur = b.req("GET", "/v1/cart", headers=b.auth(st))
    g = {
        "channel_code": "PB",
        "outlet_code": outlet,
        "fulfilment": "pickup",
        "lines": [{"product_id": st["pid"], "qty": 1}],
    }
    if slot:
        g["pickup_slot"] = slot
    return b.req("PUT", "/v1/cart", body={"version": (cur or {}).get("version", 0), "groups": [g]}, headers=b.auth(st))


def main():
    st = b.load()
    st["pid"] = first_product(st)
    s, sl = b.req("GET", f"/v1/catalog/slots?channel=PB&outlet=RESTO-01&date={DATE}")
    slots = (sl or {}).get("slots", [])
    slot = slots[2]["start_at"]
    rem0 = slots[2]["remaining"]
    s, cart = put_cart(st, slot)
    g = ((cart or {}).get("groups") or [{}])[0]
    b.result("slot.cart_put", s == 200 and g.get("pickup_slot") == slot, http=s, pickup_slot=g.get("pickup_slot"))
    s, q = b.req("POST", "/v1/cart/quote", body={}, headers=b.auth(st))
    qg = ((q or {}).get("groups") or [{}])[0]
    b.result(
        "slot.quote",
        s == 200 and qg.get("pickup_slot") == slot and not qg.get("issues"),
        http=s,
        issues=qg.get("issues"),
        total=(q or {}).get("amount_payable_total"),
    )
    qid, total = q["quote_id"], q["amount_payable_total"]
    s, o = b.req(
        "POST",
        "/v1/orders",
        body={"quote_id": qid, "accept_total": total},
        headers={**b.auth(st), "Idempotency-Key": idem_key(qid)},
    )
    oid = (o or {}).get("order_id")
    b.result("slot.checkout", s in (200, 201) and oid, http=s, status=(o or {}).get("status"), code=b.code_of(o))
    s, od = b.req("GET", f"/v1/orders/{oid}", headers=b.auth(st))
    part = ((od or {}).get("parts") or [{}])[0]
    b.result(
        "slot.order_detail_pickup_slot",
        s == 200 and part.get("pickup_slot") == slot,
        http=s,
        part_keys=sorted(part.keys()),
    )
    time.sleep(32)  # cache slot BFF 30 dtk (catalog.service) -> sisa kapasitas baru terlihat setelah kedaluwarsa
    s, sl2 = b.req("GET", f"/v1/catalog/slots?channel=PB&outlet=RESTO-01&date={DATE}")
    rem1 = next((x["remaining"] for x in (sl2 or {}).get("slots", []) if x["start_at"] == slot), None)
    b.result("slot.capacity_decremented", rem1 == rem0 - 1, before=rem0, after=rem1)
    # Negatif: slot di luar jam (03:30 lokal) & salah bentuk & slot bukan kelipatan
    bad = DATE + "T20:00:00Z"
    s, c2 = put_cart(st, bad)
    s, q2 = b.req("POST", "/v1/cart/quote", body={}, headers=b.auth(st))
    iss = [i.get("code") for gg in (q2 or {}).get("groups", []) for i in gg.get("issues", [])]
    b.result("slot.quote_outside_hours_issue", s == 200 and "slot_unavailable" in iss, http=s, issues=iss)
    s, c3 = put_cart(st, "besok jam 10")
    g3 = ((c3 or {}).get("groups") or [{}])[0]
    b.result("slot.cart_malformed_slot_rejected_or_dropped", s == 400 or (s == 200 and "pickup_slot" not in g3), http=s)
    # Tanpa slot -> quote biasa (perilaku lama)
    s, c4 = put_cart(st, None)
    s, q4 = b.req("POST", "/v1/cart/quote", body={}, headers=b.auth(st))
    g4 = ((q4 or {}).get("groups") or [{}])[0]
    b.result("slot.quote_without_slot_unchanged", s == 200 and "pickup_slot" not in g4 and not g4.get("issues"), http=s)
    st["slot_order"] = oid
    st["slot"] = slot
    b.save(st)


main()
