"""Smoke SEC-58 (gelombang 13c): user Odoo NYATA memanggil klien BFF lewat JSON-RPC web client.

Dijalankan oleh run-e2e-smoke.sh di container `python:3.12` (network efn_edge) -> Traefik wg-https
(`erp.efn.local`, sertifikat edge dev diverifikasi dengan CA dev) -> odoo-tls -> Odoo. Login penuh: password + TOTP
(2FA wajib, efn_setup_security), lalu `POST /web/dataset/call_kw`.

Kredensial user smoke (password + secret TOTP, dibuat langkah `rpc_users` odoo_pos_smoke.py di container Odoo)
dibaca dari STDIN (pipe `docker exec ... cat` -> `docker run -i`): tidak pernah di argv, env, file host, atau log.
Hanya untuk dev. Standar library saja (tanpa pip).

Keluaran: satu baris `SMOKE_RESULT {json}` per user.
"""

import base64
import hashlib
import hmac
import http.cookiejar
import json
import re
import ssl
import struct
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://erp.efn.local:8443"
CTX = ssl.create_default_context(cafile="/work/edge_ca.crt")


def totp(secret: str) -> str:
    key = base64.b32decode(secret)
    mac = hmac.new(key, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
    o = mac[-1] & 0xF
    return f"{(struct.unpack('>I', mac[o : o + 4])[0] & 0x7FFFFFFF) % 10**6:06d}"


class Client:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=CTX), urllib.request.HTTPCookieProcessor(self.jar)
        )

    def get(self, path: str) -> str:
        with self.opener.open(BASE + path, timeout=30) as r:
            return r.read().decode("utf-8", "replace")

    def post_form(self, path: str, data: dict) -> str:
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(BASE + path, data=body)  # noqa: S310 - BASE https tetap
        with self.opener.open(req, timeout=30) as r:
            return r.geturl()

    def call_kw(self, model: str, method: str, args: list, kwargs: dict | None = None) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "id": 1,
            "params": {"model": model, "method": method, "args": args, "kwargs": kwargs or {}},
        }
        req = urllib.request.Request(  # noqa: S310 - BASE https tetap
            BASE + "/web/dataset/call_kw",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(req, timeout=30) as r:
            return json.loads(r.read())


def csrf(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else ""


def check_user(login: str, cred: dict) -> dict:
    c = Client()
    token = csrf(c.get("/web/login"))
    url = c.post_form(
        "/web/login", {"csrf_token": token, "login": login, "password": cred["password"], "redirect": "/odoo"}
    )
    if "/web/login/totp" in url:
        token = csrf(c.get("/web/login/totp"))
        url = c.post_form("/web/login/totp", {"csrf_token": token, "totp_token": totp(cred["totp"])})
    control = c.call_kw("res.partner", "search_count", [[]])
    logged_in = "result" in control
    calls = {}
    for method, args in (
        ("_efn_post", ["/v1/pos/loyalty/params", {}]),
        ("_efn_get", ["/v1/pos/loyalty/params"]),
        ("post", ["/v1/pos/loyalty/params", {}]),
        ("get", ["/v1/pos/loyalty/params"]),
    ):
        r = c.call_kw("efn.bff.client", method, args)
        err = r.get("error") or {}
        data = err.get("data") or {}
        calls[method] = {
            "rejected": "error" in r and "result" not in r,
            "exception": (data.get("name") or "").rsplit(".", 1)[-1],
            "message": (data.get("message") or err.get("message") or "")[:90],
        }
    return {
        "user": login,
        "logged_in": logged_in,
        "final_url": url.replace(BASE, "")[:40],
        "all_rejected": logged_in and all(v["rejected"] for v in calls.values()),
        "calls": calls,
    }


def main() -> None:
    creds = json.loads(sys.stdin.read())
    for login in sorted(creds):
        try:
            res = check_user(login, creds[login])
        except Exception as exc:  # noqa: BLE001 - laporan smoke
            res = {
                "user": login,
                "logged_in": False,
                "all_rejected": False,
                "error": type(exc).__name__,
                "detail": str(exc)[:160],
            }
        print("SMOKE_RESULT " + json.dumps(res), flush=True)


if __name__ == "__main__":
    main()
