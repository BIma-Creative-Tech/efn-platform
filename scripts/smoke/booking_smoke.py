#!/usr/bin/env python3
"""Smoke E2E G23 reservasi meja + slot pre-order (dev saja), lewat Traefik publik api.efn.local (TLS edge diverifikasi).

Subperintah (state JSON di /work/state.json):
  login        ALTCHA -> OTP (log BFF dev) -> JWT
  public       GET /v1/booking/outlets, /availability, /v1/catalog/slots
  create       POST reservasi (Idempotency-Key) + ulang (idempoten) + tanpa key (400) + konflik isi (409)
  limit        reservasi kedua (OK) lalu ketiga -> 409 booking_limit (BOOKING_MAX_ACTIVE_PER_MEMBER=2)
  status       GET /v1/booking/reservations/:id -> cetak status (setelah konfirmasi staf + event Odoo)
  cancel       POST cancel -> status cancelled; cancel lagi idempoten
  list         GET /v1/booking/reservations
Tidak ada secret; nomor HP fiktif acak per run.
"""

import base64
import hashlib
import http.client
import json
import os
import re
import ssl
import sys
import time
import uuid

HOST = "api.efn.local"
CA = "/work/edge_ca.crt"
LOG = "/work/bff.log"
STATE = "/work/state.json"
PHONE = os.environ.get("SMOKE_PHONE", "")
CTX = ssl.create_default_context(cafile=CA)


def load():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def save(st):
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(st, fh)


def result(name, ok, **kw):
    print("SMOKE_RESULT " + json.dumps({"check": name, "ok": bool(ok), **kw}, default=str), flush=True)


def req(method, path, body=None, headers=None):
    conn = http.client.HTTPSConnection(HOST, 443, context=CTX, timeout=60)
    h = {"Accept": "application/json", "User-Agent": "efn-smoke-g23/1"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=h)
    resp = conn.getresponse()
    payload = resp.read()
    try:
        parsed = json.loads(payload) if payload else None
    except ValueError:
        parsed = payload[:200].decode("utf-8", "replace")
    conn.close()
    return resp.status, parsed


def auth(st):
    return {"Authorization": f"Bearer {st['token']}"}


def code_of(body):
    return (body or {}).get("error", {}).get("code") if isinstance(body, dict) else None


def mask(m):
    return f"{m[:5]}****{m[-3:]}"


def cmd_login():
    st = load()
    s, ch = req("GET", "/v1/auth/challenge")
    base = hashlib.sha256(ch["salt"].encode())
    for n in range(ch["maxnumber"] + 1):
        h = base.copy()
        h.update(str(n).encode())
        if h.hexdigest() == ch["challenge"]:
            break
    p = {k: ch[k] for k in ("algorithm", "challenge", "salt", "signature")}
    p["number"] = n
    altcha = base64.b64encode(json.dumps(p).encode()).decode()
    off = os.path.getsize(LOG)
    s, body = req("POST", "/v1/auth/otp/request", body={"phone": PHONE, "altcha": altcha})
    pat = re.compile(r"OTP untuk " + re.escape(mask(PHONE)) + r": (\d{6})")
    code = None
    for _ in range(40):
        with open(LOG, "rb") as fh:
            fh.seek(off)
            found = pat.findall(fh.read().decode("utf-8", "replace"))
        if found:
            code = found[-1]
            break
        time.sleep(0.5)
    s, tok = req(
        "POST", "/v1/auth/otp/verify", body={"challenge_id": body["challenge_id"], "code": code, "age_confirmed": True}
    )
    result("login", s == 200 and tok.get("access_token"), http=s)
    st["token"] = tok["access_token"]
    save(st)


def slot_starts(date, party=2):
    s, av = req("GET", f"/v1/booking/availability?outlet=RESTO-01&date={date}&party={party}")
    return s, av, [x["start_at"] for x in (av or {}).get("slots", []) if x.get("available")]


def cmd_public():
    date = os.environ["SMOKE_DATE"]
    s, out = req("GET", "/v1/booking/outlets?brand=PB")
    codes = [o["outlet_code"] for o in (out or {}).get("items", [])]
    result("booking.outlets", s == 200 and "RESTO-01" in codes, http=s, outlets=codes)
    s, av, free = slot_starts(date, 3)
    result(
        "booking.availability",
        s == 200 and len(free) > 0 and av.get("timezone") == "Asia/Jakarta",
        http=s,
        free=len(free),
        first=free[:1],
        keys=sorted((av or {}).keys()),
    )
    s, bad = req("GET", f"/v1/booking/availability?outlet=RESTO-01&date={date}&party=40")
    result("booking.availability_party_limit", s in (400, 409), http=s, code=code_of(bad))
    s, sl = req("GET", f"/v1/catalog/slots?channel=PB&outlet=RESTO-01&date={date}")
    result(
        "slots.list",
        s == 200 and len((sl or {}).get("slots", [])) > 0,
        http=s,
        n=len((sl or {}).get("slots", [])),
        remaining0=((sl or {}).get("slots") or [{}])[0].get("remaining"),
    )
    s, gl = req("GET", f"/v1/catalog/slots?channel=GL&outlet=TOKO-01&date={date}")
    result(
        "slots.outlet_without_slots",
        s in (404, 409, 422, 400) or (s == 200 and not gl.get("slots")),
        http=s,
        code=code_of(gl),
    )


def create(st, start, party=2, key=None, label="Rina"):
    key = key or ("smoke-" + uuid.uuid4().hex)
    body = {"outlet_code": "RESTO-01", "start_at": start, "party_size": party, "guest_label": label}
    s, b = req("POST", "/v1/booking/reservations", body=body, headers={**auth(st), "Idempotency-Key": key})
    return s, b, key, body


def cmd_create():
    st = load()
    date = os.environ["SMOKE_DATE"]
    _, _, free = slot_starts(date, 2)
    start = free[2]
    s, b, key, body = create(st, start)
    rid = (b or {}).get("booking_id")
    result(
        "booking.create",
        s in (200, 201) and rid and b.get("status") in ("requested", "confirmed"),
        http=s,
        status=(b or {}).get("status"),
        table=(b or {}).get("table_label"),
        keys=sorted((b or {}).keys()),
    )
    leak = any(k in json.dumps(b) for k in ("Rina", PHONE))
    result("booking.create_no_guest_echo_or_phone", not leak)
    s2, b2, _, _ = create(st, start, key=key)
    result("booking.create_idempotent", s2 in (200, 201) and (b2 or {}).get("booking_id") == rid, http=s2)
    s3, b3 = req("POST", "/v1/booking/reservations", body=body, headers=auth(st))
    result("booking.create_without_key_rejected", s3 == 400, http=s3, code=code_of(b3))
    s4, b4, _, _ = create(st, free[3], key=key)
    result("booking.create_key_conflict", s4 in (409, 422), http=s4, code=code_of(b4))
    s5, b5 = req(
        "POST",
        "/v1/booking/reservations",
        body={**body, "table_id": 1},
        headers={**auth(st), "Idempotency-Key": "x" * 24},
    )
    result("booking.create_extra_field_rejected", s5 == 400, http=s5)
    s6, b6 = req(
        "POST",
        "/v1/booking/reservations",
        body={**body, "note": "hubungi 081234567890"},
        headers={**auth(st), "Idempotency-Key": "smoke-" + uuid.uuid4().hex},
    )
    result("booking.create_note_phone_rejected", s6 == 400, http=s6, code=code_of(b6))
    s7, b7 = req(
        "POST", "/v1/booking/reservations", body=body, headers={"Idempotency-Key": "smoke-" + uuid.uuid4().hex}
    )
    result("booking.create_requires_login", s7 == 401, http=s7)
    st.update({"booking_id": rid, "start": start, "free": free})
    save(st)


def cmd_limit():
    st = load()
    free = st["free"]
    s, b, _, _ = create(st, free[6], label="Budi")
    result("booking.create_second", s in (200, 201), http=s, status=(b or {}).get("status"))
    st["booking2"] = (b or {}).get("booking_id")
    s, b, _, _ = create(st, free[9])
    result("booking.limit_active_per_member", s == 409 and code_of(b) == "booking_limit", http=s, code=code_of(b))
    save(st)


def cmd_status():
    st = load()
    want = os.environ.get("SMOKE_WANT", "confirmed")
    status = None
    for _ in range(30):
        s, b = req("GET", f"/v1/booking/reservations/{st['booking_id']}", headers=auth(st))
        status = (b or {}).get("status")
        if status == want:
            break
        time.sleep(3)
    result(f"booking.status_{want}", status == want, http=s, status=status, table=(b or {}).get("table_label"))
    s, b = req("GET", f"/v1/booking/reservations/{uuid.uuid4()}", headers=auth(st))
    result("booking.get_foreign_404", s == 404, http=s)


def cmd_cancel():
    st = load()
    rid = st[os.environ.get("SMOKE_WHICH", "booking_id")]
    s, b = req("POST", f"/v1/booking/reservations/{rid}/cancel", body={}, headers=auth(st))
    result("booking.cancel", s == 200 and (b or {}).get("status") == "cancelled", http=s, body=b)
    s2, b2 = req("POST", f"/v1/booking/reservations/{rid}/cancel", body={}, headers=auth(st))
    result("booking.cancel_idempotent", s2 == 200, http=s2, body=b2)


def cmd_list():
    st = load()
    s, b = req("GET", "/v1/booking/reservations", headers=auth(st))
    items = (b or {}).get("items", [])
    result("booking.list", s == 200 and len(items) >= 2, http=s, statuses=sorted(i.get("status") for i in items))


if __name__ == "__main__":
    {
        "login": cmd_login,
        "public": cmd_public,
        "create": cmd_create,
        "limit": cmd_limit,
        "status": cmd_status,
        "cancel": cmd_cancel,
        "list": cmd_list,
    }[sys.argv[1]]()
