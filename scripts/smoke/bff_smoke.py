#!/usr/bin/env python3
"""Smoke E2E Super-App / BFF lewat Traefik publik (dev saja). Stdlib + Pillow (untuk foto sintetis).

Dijalankan oleh scripts/smoke/run-e2e-smoke.sh di container python:3.12 pada network `efn_edge`
(klien publik), dengan `--add-host api.efn.local/app.efn.local:<IP traefik>`. TLS diverifikasi
terhadap sertifikat edge dev (`SMOKE_EDGE_CA`, self-signed *.efn.local dari gen-secrets.sh) — bukan -k.

Tidak ada secret di file ini. OTP dev dibaca dari log `ConsoleWhatsAppSender` BFF (hanya dicetak saat
APP_ENV=dev) yang di-stream orkestrator ke `SMOKE_BFF_LOG`. Nomor HP = nomor fiktif acak per run.

Subperintah (state di `SMOKE_STATE`, JSON):
  login        ALTCHA PoW -> /v1/auth/otp/request -> OTP dari log -> verify -> JWT -> GET /v1/me
  login_web    Sama, lewat route handler Next.js (app.efn.local/api/auth/...) -> cookie efn_session -> /api/me
  negatives    captcha hilang/replay, OTP salah, /v1/me tanpa token, nomor asing
  consent      PUT /v1/me/consents/location (versi aktif dari env), GET daftar
  report       upload foto sintetis -> 202 -> poll status sampai final -> GET /v1/me (poin)
  report_dup   upload foto yang sama lagi (anti-fraud duplikat)
  delete       reauth step-up -> DELETE /v1/me (+receipt_token, step-up sekali pakai SEC-40)
  requests     GET /v1/me/requests + GET /v1/dsar/requests/:id (X-DSAR-Receipt, R6) + Next.js
               /api/dsar/requests/:id (cookie efn_dsar_receipt)
"""

import base64
import hashlib
import http.client
import io
import json
import os
import re
import ssl
import sys
import time
import uuid

BASE_HOST = os.environ.get("SMOKE_API_HOST", "api.efn.local")
WEB_HOST = os.environ.get("SMOKE_WEB_HOST", "app.efn.local")
CA = os.environ.get("SMOKE_EDGE_CA", "/work/edge_ca.crt")
LOG = os.environ.get("SMOKE_BFF_LOG", "/work/bff.log")
STATE = os.environ.get("SMOKE_STATE", "/work/state.json")
PHONE = os.environ.get("SMOKE_PHONE", "")
NEG_PHONE = "6281200" + str(uuid.uuid4().int % 1_000_000).zfill(6)

CTX = ssl.create_default_context(cafile=CA)  # verifikasi hostname + rantai (tanpa -k)


def mask(msisdn):
    return f"{msisdn[:5]}****{msisdn[-3:]}"


def load_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def save_state(st):
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(st, fh)


def result(name, ok, **kw):
    print("SMOKE_RESULT " + json.dumps({"check": name, "ok": bool(ok), **kw}, default=str))
    sys.stdout.flush()


def req(method, path, host=BASE_HOST, body=None, headers=None, raw=None, ctype=None):
    conn = http.client.HTTPSConnection(host, 443, context=CTX, timeout=30)
    h = {"Accept": "application/json", "User-Agent": "efn-smoke/1"}
    h.update(headers or {})
    data = None
    if raw is not None:
        data = raw
        h["Content-Type"] = ctype
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=h)
    resp = conn.getresponse()
    payload = resp.read()
    try:
        parsed = json.loads(payload) if payload else None
    except ValueError:
        parsed = payload[:200].decode("utf-8", "replace")
    cookies = resp.headers.get_all("Set-Cookie") or []
    tls = conn.sock.version() if conn.sock else None
    conn.close()
    return resp.status, parsed, cookies, tls


def solve_altcha(ch):
    # SEC-34: maxnumber kini adaptif (bisa naik s.d. ALTCHA_MAX_NUMBER_CAP bila smoke memicu banyak OTP dari satu IP),
    # dan challenge terikat IP -> challenge & otp/request harus dari klien/IP yang sama (selalu benar di smoke ini).
    # Prefix salt di-hash sekali lalu di-copy (±2x lebih cepat dari hash ulang penuh).
    base = hashlib.sha256(ch["salt"].encode())
    target = ch["challenge"]
    for n in range(ch["maxnumber"] + 1):
        h = base.copy()
        h.update(str(n).encode())
        if h.hexdigest() == target:
            payload = {k: ch[k] for k in ("algorithm", "challenge", "salt", "signature")}
            payload["number"] = n
            return base64.b64encode(json.dumps(payload).encode()).decode()
    raise RuntimeError("ALTCHA tidak terpecahkan")


def log_offset():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


def wait_otp(masked, offset, timeout=20):
    pat = re.compile(r"OTP untuk " + re.escape(masked) + r": (\d{6})")
    deadline = time.time() + timeout
    while time.time() < deadline:
        with open(LOG, "rb") as fh:
            fh.seek(offset)
            text = fh.read().decode("utf-8", "replace")
        found = pat.findall(text)
        if found:
            return found[-1]
        time.sleep(0.5)
    raise RuntimeError(f"OTP untuk {masked} tidak muncul di log BFF")


def bearer(st):
    return {"Authorization": f"Bearer {st['token']}"}


# ------------------------------------------------------------------ skenario a
def cmd_login():
    st = load_state()
    s, ch, _, tls = req("GET", "/v1/auth/challenge")
    result("auth.challenge", s == 200 and "salt" in ch, http=s, tls=tls, maxnumber=ch.get("maxnumber"))
    t0 = time.time()
    altcha = solve_altcha(ch)
    off = log_offset()
    s, body, _, _ = req("POST", "/v1/auth/otp/request", body={"phone": PHONE, "altcha": altcha})
    result("auth.otp_request", s == 202 and body.get("challenge_id"), http=s, pow_s=round(time.time() - t0, 2))
    code = wait_otp(mask(PHONE), off)
    result("auth.otp_from_console_log", True, masked=mask(PHONE))
    s, tok, _, _ = req(
        "POST", "/v1/auth/otp/verify", body={"challenge_id": body["challenge_id"], "code": code, "age_confirmed": True}
    )
    ok = s == 200 and tok.get("token_type") == "Bearer" and tok.get("access_token")
    result("auth.otp_verify", ok, http=s, expires_in=(tok or {}).get("expires_in"))
    st["token"] = tok["access_token"]
    claims = json.loads(base64.urlsafe_b64decode(st["token"].split(".")[1] + "=="))
    sub_is_uuid = bool(re.fullmatch(r"[0-9a-f-]{36}", str(claims.get("sub"))))
    result("auth.jwt_sub_opaque", sub_is_uuid and PHONE not in json.dumps(claims), sub_is_uuid=sub_is_uuid)
    s, me, _, _ = req("GET", "/v1/me", headers=bearer(st))
    result("me.get", s == 200 and me.get("member_ref_masked") == mask(PHONE), http=s, me=me)
    st["points_before"] = me.get("points_balance")
    st["phone_masked"] = mask(PHONE)
    save_state(st)


def cmd_login_web():
    origin = {"Origin": f"https://{WEB_HOST}"}
    s, ch, _, tls = req("GET", "/api/auth/challenge", host=WEB_HOST)
    result("web.challenge", s == 200 and "salt" in (ch or {}), http=s, tls=tls)
    altcha = solve_altcha(ch)
    off = log_offset()
    s, body, _, _ = req(
        "POST", "/api/auth/otp/request", host=WEB_HOST, body={"phone": PHONE, "altcha": altcha}, headers=origin
    )
    result("web.otp_request", s == 202, http=s)
    code = wait_otp(mask(PHONE), off)
    s, body2, cookies, _ = req(
        "POST",
        "/api/auth/otp/verify",
        host=WEB_HOST,
        body={"challenge_id": body["challenge_id"], "code": code, "age_confirmed": True},
        headers=origin,
    )
    sess = next((c for c in cookies if c.startswith("efn_session=")), "")
    flags = {f.strip().lower() for f in sess.split(";")[1:]}
    result(
        "web.otp_verify_cookie",
        s == 200 and sess and "httponly" in flags and "secure" in flags,
        http=s,
        cookie_flags=sorted(f.split("=")[0] for f in flags),
    )
    cookie = sess.split(";")[0]
    # /api/me hanya DELETE (hapus akun); GET terautentikasi via cookie diuji lewat /api/me/consents.
    s, body3, _, _ = req("GET", "/api/me/consents", host=WEB_HOST, headers={"Cookie": cookie})
    result("web.cookie_session_consents", s == 200 and isinstance(body3, list), http=s)
    s, _, _, _ = req("GET", "/api/me/consents", host=WEB_HOST)
    result("web.no_cookie_rejected", s == 401, http=s)
    s, _, _, _ = req(
        "POST",
        "/api/auth/otp/request",
        host=WEB_HOST,
        body={"phone": PHONE, "altcha": "x"},
        headers={"Origin": "https://evil.example"},
    )
    result("web.cross_origin_rejected", s == 403, http=s)


def cmd_negatives():
    s, b, _, _ = req("POST", "/v1/auth/otp/request", body={"phone": PHONE})
    result("neg.captcha_missing", s == 400 and b["error"]["code"] == "captcha_required", http=s)
    _, ch, _, _ = req("GET", "/v1/auth/challenge")
    altcha = solve_altcha(ch)
    s1, b1, _, _ = req("POST", "/v1/auth/otp/request", body={"phone": NEG_PHONE, "altcha": altcha})
    s2, b2, _, _ = req("POST", "/v1/auth/otp/request", body={"phone": NEG_PHONE, "altcha": altcha})
    result(
        "neg.captcha_replay", s1 == 202 and s2 == 400 and b2["error"]["code"] == "captcha_required", first=s1, second=s2
    )
    # W-19 (Q-P3): tanpa pernyataan usia 17 th -> 400 age_confirmation_required, percobaan OTP tidak hangus
    s, b, _, _ = req("POST", "/v1/auth/otp/verify", body={"challenge_id": b1["challenge_id"], "code": "000000"})
    result(
        "neg.age_confirmation_required",
        s == 400 and (b.get("error") or {}).get("code") == "age_confirmation_required",
        http=s,
    )
    s, b, _, _ = req(
        "POST",
        "/v1/auth/otp/verify",
        body={"challenge_id": b1["challenge_id"], "code": "000000", "age_confirmed": True},
    )
    result("neg.otp_wrong", s == 400 and b.get("ok") is False and b.get("attempts_left") == 2, http=s, body=b)
    _, ch, _, _ = req("GET", "/v1/auth/challenge")
    s, b, _, _ = req("POST", "/v1/auth/otp/request", body={"phone": "6591234567", "altcha": solve_altcha(ch)})
    result("neg.foreign_phone", s == 400 and b["error"]["code"] == "invalid_phone", http=s)
    s, b, _, _ = req("GET", "/v1/me")
    result("neg.me_without_token", s == 401, http=s)
    s, b, _, _ = req("GET", "/v1/me", headers={"Authorization": "Bearer eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0."})
    result("neg.me_alg_none", s == 401, http=s)
    for path in ("/metrics", "/internal/v1/x"):
        s, _, _, _ = req("GET", path)
        result("neg.public_blocked " + path, s == 404, http=s)
    # R2 (docs/04 §8.13): saldo POS = POST body (nomor HP tidak di URL); /v1/pos tidak dirutekan publik
    s, _, _, _ = req("POST", "/v1/pos/loyalty/balance", body={"member_ref": NEG_PHONE})
    result("neg.public_blocked /v1/pos/loyalty/balance", s == 404, http=s)


# ------------------------------------------------------------------ skenario b
def cmd_consent():
    st = load_state()
    version = os.environ["SMOKE_CONSENT_VERSION"]
    s, b, _, _ = req(
        "PUT", "/v1/me/consents/location", body={"granted": True, "version": "location@1999-01-01"}, headers=bearer(st)
    )
    result("consent.stale_text", s == 409 and b["error"]["code"] == "stale_text", http=s)
    s, b, _, _ = req("PUT", "/v1/me/consents/location", body={"granted": True, "version": version}, headers=bearer(st))
    result("consent.grant_location", s == 200 and b.get("granted") is True, http=s, body=b)
    s, b, _, _ = req("GET", "/v1/me/consents", headers=bearer(st))
    result("consent.list", s == 200 and any(c["purpose"] == "location" and c["granted"] for c in b), http=s, body=b)


def make_jpeg(name, price_text, seed, noisy=False):
    from PIL import Image, ImageDraw, ImageFont

    # Variasi posisi/ukuran teks per seed -> pHash antar-run berjarak >= 14 bit (ambang duplikat worker 6),
    # tanpa elemen non-teks (blok/garis menurunkan confidence Tesseract < 0.80 -> jalur VLM). Diuji G8.
    rnd = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    img = Image.new("RGB", (1200, 800), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=80 + (rnd % 30))
    dx, dy, gap = (rnd >> 8) % 300, (rnd >> 20) % 200, 180 + (rnd >> 32) % 120
    d.text((30 + dx, 60 + dy), name, fill="black", font=font)
    d.text((30 + (rnd >> 40) % 500, 60 + dy + gap), price_text, fill="black", font=font)
    small = ImageFont.load_default(size=36 + (rnd >> 52) % 20)
    token = "".join(chr(65 + int(c, 16)) for c in seed)  # huruf saja (tidak terbaca sebagai harga)
    d.text((40 + (rnd >> 60) % 700, 60 + dy + gap + 130 + (rnd >> 70) % 40), token, fill="black", font=small)
    if noisy:  # batang hitam -> confidence Tesseract < 0.80 -> tahap 2 VLM (Ollama)
        for i in range(24):
            x = (rnd >> (i * 5)) % 1100
            d.rectangle([x, 720, x + 40, 790], fill="black")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def multipart(fields, file_field, filename, content, ctype):
    boundary = "----efnsmoke" + uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    out.write(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n".encode()
    )
    out.write(content)
    out.write(f"\r\n--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def submit_report(st, jpeg, name, price, label):
    raw, ctype = multipart(
        {"product_name": name, "price": str(price), "competitor_name": "Toko Smoke", "lat": "-6.2", "lng": "106.8"},
        "photo",
        "tag.jpg",
        jpeg,
        "image/jpeg",
    )
    s, b, _, _ = req("POST", "/v1/market-scout/reports", raw=raw, ctype=ctype, headers=bearer(st))
    result(f"{label}.submit", s == 202 and b.get("status") == "queued", http=s, body=b)
    if s != 202:
        return None
    rid = b["report_id"]
    final = None
    for _ in range(90):
        s, b, _, _ = req("GET", f"/v1/market-scout/reports/{rid}", headers=bearer(st))
        if b.get("status") in ("valid", "rejected", "flagged"):
            final = b
            break
        time.sleep(2)
    result(f"{label}.final_status", final is not None, report_id=rid, final=final)
    return rid, final


def cmd_report():
    st = load_state()
    seed = uuid.uuid4().hex[:8]
    name = os.environ.get("SMOKE_PRODUCT", "INDOMIE GORENG")
    price = int(os.environ.get("SMOKE_PRICE", "3500"))
    jpeg = make_jpeg(name, "Rp 3.500", seed)
    with open("/work/report.jpg", "wb") as fh:
        fh.write(jpeg)
    s, b, _, _ = req("POST", "/v1/market-scout/reports", raw=b"not an image", ctype="text/plain", headers=bearer(st))
    result("report.non_multipart_rejected", s == 415, http=s)
    rid, final = submit_report(st, jpeg, name, price, "report")
    st["report_id"] = rid
    st["report_final"] = final
    time.sleep(3)
    s, me, _, _ = req("GET", "/v1/me", headers=bearer(st))
    gained = (me or {}).get("points_balance", 0) - (st.get("points_before") or 0)
    expect = (final or {}).get("points_awarded", 0)
    result(
        "report.points_in_ledger",
        s == 200 and gained == expect,
        gained=gained,
        points_awarded=expect,
        status=(final or {}).get("status"),
    )
    st["points_after"] = me.get("points_balance")
    save_state(st)


def cmd_report_dup():
    st = load_state()
    with open("/work/report.jpg", "rb") as fh:
        jpeg = fh.read()
    rid, final = submit_report(st, jpeg, os.environ.get("SMOKE_PRODUCT", "INDOMIE GORENG"), 3500, "report_dup")
    result("report_dup.not_valid", (final or {}).get("status") in ("rejected", "flagged"), final=final)


def cmd_report_vlm():
    """Foto ber-noise: Tesseract ragu -> VLM. Tanpa model = flagged/vlm_unavailable; dengan model = valid/rejected."""
    st = load_state()
    name = os.environ.get("SMOKE_PRODUCT", "INDOMIE GORENG")
    jpeg = make_jpeg(name, "Rp 3.500", uuid.uuid4().hex[:8], noisy=True)
    _rid, final = submit_report(st, jpeg, name, 3500, "report_vlm")
    result("report_vlm.final", final is not None, final=final)
    # Dengan model terpasang, jalur VLM harus benar-benar dipakai (bukan fallback vlm_unavailable).
    result("report_vlm.model_used", (final or {}).get("reason") not in (None, "vlm_unavailable"), final=final)


# ------------------------------------------------------------------ skenario e
def cmd_delete():
    st = load_state()
    s, b, _, _ = req("DELETE", "/v1/me", headers=bearer(st))
    result("dsar.delete_without_step_up", s == 403 and b["error"]["code"] == "step_up_required", http=s)
    off = log_offset()
    s, b, _, _ = req("POST", "/v1/me/reauth", body={}, headers=bearer(st))
    result("dsar.reauth_request", s == 202 and b.get("challenge_id"), http=s)
    code = wait_otp(st["phone_masked"], off)
    s, b2, _, _ = req(
        "POST", "/v1/me/reauth", body={"challenge_id": b["challenge_id"], "code": code}, headers=bearer(st)
    )
    result("dsar.reauth_verify", s == 200 and b2.get("step_up_token"), http=s)
    s, b3, _, _ = req("DELETE", "/v1/me", headers={**bearer(st), "X-Step-Up-Token": b2["step_up_token"]})
    result(
        "dsar.delete_accepted",
        s == 202 and b3.get("request_id") and b3.get("receipt_token"),
        http=s,
        body={k: v for k, v in (b3 or {}).items() if k != "receipt_token"},
    )
    st["dsar_request_id"] = b3.get("request_id")
    st["dsar_receipt"] = b3.get("receipt_token")
    save_state(st)
    # SEC-40: token step-up DELETE sekali pakai
    s, b4, _, _ = req("DELETE", "/v1/me", headers={**bearer(st), "X-Step-Up-Token": b2["step_up_token"]})
    code = ((b4 or {}).get("error") or {}).get("code")
    result("dsar.step_up_single_use", s == 403 and code == "step_up_invalid", http=s)


def cmd_requests():
    st = load_state()
    s, b, _, _ = req("GET", "/v1/me/requests", headers=bearer(st))
    result("dsar.requests", s in (200, 401, 409), http=s, body=b)
    # R6 (docs/04 §8.13): status lewat receipt, tetap jalan setelah akun terhapus; receipt salah/hilang -> 404 generik
    rid, receipt = st.get("dsar_request_id"), st.get("dsar_receipt")
    if not (rid and receipt):
        result("dsar.receipt_status", False, reason="receipt belum ada (jalankan delete dulu)")
        return
    s, b, _, _ = req("GET", f"/v1/dsar/requests/{rid}", headers={"X-DSAR-Receipt": receipt})
    keys = {
        "id",
        "type",
        "status",
        "requested_at",
        "scheduled_at",
        "completed_at",
        "open_online_orders",
        "erasure_deadline",
    }
    ok = s == 200 and isinstance(b, dict) and b.get("id") == rid and set(b) == keys
    result("dsar.receipt_status", ok, http=s, status=(b or {}).get("status") if isinstance(b, dict) else None)
    receipt_body = b
    i = len(receipt) - 10
    bad = receipt[:i] + ("B" if receipt[i] == "A" else "A") + receipt[i + 1 :]
    s, _, _, _ = req("GET", f"/v1/dsar/requests/{rid}", headers={"X-DSAR-Receipt": bad})
    result("dsar.receipt_wrong_404", s == 404, http=s)
    s, _, _, _ = req("GET", f"/v1/dsar/requests/{rid}")
    result("dsar.receipt_missing_404", s == 404, http=s)
    pii = [PHONE, PHONE[2:], PHONE[4:]]
    dumped = json.dumps(receipt_body)
    result("dsar.receipt_no_pii", receipt_body is not None and not any(p and p in dumped for p in pii))
    # R6 lewat Next.js: cookie HttpOnly efn_dsar_receipt (Path /api/dsar) -> route handler -> BFF. Tanpa sesi.
    same = {"Sec-Fetch-Site": "same-origin"}
    path = f"/api/dsar/requests/{rid}"
    s, b, _, _ = req("GET", path, host=WEB_HOST, headers={**same, "Cookie": f"efn_dsar_receipt={receipt}"})
    web_keys = {
        "id",
        "status",
        "requested_at",
        "scheduled_at",
        "completed_at",
        "open_online_orders",
        "erasure_deadline",
    }
    ok = s == 200 and isinstance(b, dict) and b.get("id") == rid and set(b) <= web_keys
    ok = ok and not any(p in json.dumps(b) for p in pii) and receipt not in json.dumps(b)
    result("web.dsar_receipt_status", ok, http=s, keys=sorted(b) if isinstance(b, dict) else None)
    s, _, _, _ = req("GET", path, host=WEB_HOST, headers={**same, "Cookie": f"efn_dsar_receipt={bad}"})
    result("web.dsar_receipt_wrong_404", s == 404, http=s)
    s, _, _, _ = req("GET", path, host=WEB_HOST, headers=same)
    result("web.dsar_receipt_missing_404", s == 404, http=s)
    cross = {"Sec-Fetch-Site": "cross-site", "Cookie": f"efn_dsar_receipt={receipt}"}
    s, _, _, _ = req("GET", path, host=WEB_HOST, headers=cross)
    result("web.dsar_receipt_cross_site_rejected", s == 403, http=s)


if __name__ == "__main__":
    {
        "login": cmd_login,
        "login_web": cmd_login_web,
        "negatives": cmd_negatives,
        "consent": cmd_consent,
        "report": cmd_report,
        "report_dup": cmd_report_dup,
        "report_vlm": cmd_report_vlm,
        "delete": cmd_delete,
        "requests": cmd_requests,
    }[sys.argv[1]]()
