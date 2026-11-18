"""`GET /efn/insight/open`: buka Dashboard Insight dengan token SSO sekali pakai (G42, ADR-041 Proposed, docs/04 §26).

Urutan (fit-gap efn-odoo-functional G42 §C.4):
  (a) tanpa grup `group_insight_user` -> 403;
  (b) kill switch `INSIGHT_ENABLED` mati -> halaman info "belum diaktifkan" (tanpa token);
  (c) konfigurasi: `EFN_INSIGHT_URL` https (http hanya bila `plaintext_allowed()`), kunci dari
      `EFN_INSIGHT_SSO_KEY_FILE` (hex >= 64) -> gagal = halaman info "konfigurasi" (tanpa token, log tanpa isi kunci);
  (d) cakupan PT (sudo; PT arsip tidak ikut; `all_brands` -> 6 kode ESB) -> kosong = halaman info "tidak ada PT";
  (e) token eit1 -> halaman POST auto-submit ke `<EFN_INSIGHT_URL>/sso` (token tidak pernah di query string);
  audit `ir.logging` + log server tanpa token/nama/login.
"""

import logging
import os
import time
import uuid

from werkzeug.exceptions import Forbidden

from odoo import http
from odoo.http import request

from odoo.addons.efn_core.models.efn_bff_client import plaintext_allowed

from ..lib import insight_token

_logger = logging.getLogger(__name__)

ENABLED_ENV = "INSIGHT_ENABLED"
URL_ENV = "EFN_INSIGHT_URL"
KEY_FILE_ENV = "EFN_INSIGHT_SSO_KEY_FILE"
DEFAULT_KEY_FILE = "/run/secrets/insight_sso_key"
GROUP_USER = "efn_insight.group_insight_user"
GROUP_ALL_BRANDS = "efn_insight.group_insight_all_brands"
# Dimuat template sso_post (file statis, route /efn_insight/static/...); tanpa inline script.
SCRIPT_PATH = "/efn_insight/static/src/js/insight_autosubmit.js"

CSP_INFO = "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
INFO_MESSAGES = {
    "disabled": "Dashboard Insight belum diaktifkan.",
    "config": "Konfigurasi Dashboard Insight belum lengkap. Hubungi admin.",
    "no_company": "Tidak ada PT yang bisa ditampilkan untuk akun Anda. Hubungi admin untuk akses PT atau grup "
    "Insight semua brand.",
}


def _enabled():
    # Harus identik dengan insight-web (`INSIGHT_ENABLED === 'true'`, docs/04 §26.2/§26.4): "1"/"yes"/"on" tidak
    # mengaktifkan, supaya Odoo tidak membuat token untuk insight-web yang menolaknya (satu saklar, dua sisi).
    return os.environ.get(ENABLED_ENV) == "true"


def _load_config():
    """(action_url, origin, key) atau None. Tidak me-log isi kunci/URL. Kode log: insight_misconfigured."""
    origin = insight_token.parse_insight_url(os.environ.get(URL_ENV), allow_plaintext=plaintext_allowed())
    if origin is None:
        _logger.error("insight_misconfigured: %s tidak sah (wajib https, tanpa path/query)", URL_ENV)
        return None
    key = insight_token.load_key(os.environ.get(KEY_FILE_ENV) or DEFAULT_KEY_FILE)
    if key is None:
        _logger.error("insight_misconfigured: kunci SSO tidak terbaca / bukan hex >= 64 (%s)", KEY_FILE_ENV)
        return None
    return f"{origin}/sso", origin, key


def _insight_scope(env, user):
    """(codes, cids, roles) sesuai docs/04 §26.2.

    - all_brands: 6 kode tetap; cids = semua company ber-kode ESB (sudo search: record rule
      res.company hanya membuka PT yang aktif di pemilih perusahaan, padahal kontrak meminta semua).
    - lainnya: id company dari tabel relasi `res_company_users_rel` (pola efn_data_ask: membaca
      m2m sebagai user menyaring PT yang tidak aktif di pemilih perusahaan). Kode/aktif dibaca sudo
      hanya untuk id tersebut; PT arsip tidak ikut.
    """
    companies = env["res.company"].sudo()
    if user.has_group(GROUP_ALL_BRANDS):
        found = companies.search([("efn_brand_code", "in", sorted(insight_token.ESB_CODES))])
        return sorted(insight_token.ESB_CODES), sorted(found.ids), ["all_brands"]
    env["res.users"].flush_model(["company_ids"])
    env.cr.execute("SELECT cid FROM res_company_users_rel WHERE user_id = %s", (user.id,))
    ids = sorted({row[0] for row in env.cr.fetchall()})
    own = companies.with_context(active_test=False).browse(ids).exists()
    own = own.filtered(lambda c: c.active and c.efn_brand_code in insight_token.ESB_CODES)
    return sorted({c.efn_brand_code for c in own}), sorted(own.ids), ["user"]


class EfnInsightController(http.Controller):
    @http.route("/efn/insight/open", type="http", auth="user", methods=["GET"], sitemap=False)
    def efn_insight_open(self, **kw):
        user = request.env.user
        if not user.has_group(GROUP_USER):
            _logger.info("insight_denied uid=%s", user.id)
            raise Forbidden()
        if not _enabled():
            return self._info("disabled")
        config = _load_config()
        if config is None:
            return self._info("config", status=503)
        action, origin, key = config
        codes, cids, roles = _insight_scope(request.env, user)
        if not codes:
            _logger.info("insight_no_company uid=%s", user.id)
            return self._info("no_company")
        payload = insight_token.build_payload(
            sub=user.id,
            name=user.name,
            codes=codes,
            cids=cids,
            roles=roles,
            iat=int(time.time()),
            jti=uuid.uuid4().hex,
        )
        token = insight_token.encode_token(payload, key)
        self._audit(user.id, payload)
        response = request.render("efn_insight.sso_post", {"action": action, "token": token})
        self._secure_headers(
            response,
            f"default-src 'none'; script-src 'self'; form-action {origin}; base-uri 'none'; frame-ancestors 'none'",
            # strict-origin: header Origin wajib terkirim ke /sso (cek C1); no-referrer -> Origin: null (docs/04 §26.2).
            referrer="strict-origin",
        )
        return response

    # ------------------------------------------------------------------ helper (bukan route)
    @staticmethod
    def _secure_headers(response, csp, referrer="no-referrer"):
        # Odoo 19 Application.set_csp tidak menimpa CSP yang sudah ada (hanya memasang nosniff).
        response.headers["Content-Security-Policy"] = csp
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = referrer
        response.headers["X-Frame-Options"] = "DENY"

    def _info(self, state, status=200):
        response = request.render("efn_insight.info_page", {"state": state, "message": INFO_MESSAGES[state]})
        response.status_code = status
        self._secure_headers(response, CSP_INFO)
        return response

    @staticmethod
    def _audit(uid, payload):
        # Tanpa token/sig/kunci/nama/login (minimisasi PDP). Retensi ir.logging: README (Q-INS3, keputusan DPO).
        codes = ",".join(payload["codes"])
        cids = ",".join(str(c) for c in payload["cids"])
        roles = ",".join(payload["roles"])
        line = f"insight_open uid={uid} codes={codes} cids={cids} roles={roles} jti={payload['jti']}"
        request.env["ir.logging"].sudo().create(
            {
                "name": "efn_insight",
                "type": "server",
                "dbname": request.env.cr.dbname,
                "level": "INFO",
                "message": line,
                "path": "efn_insight",
                "func": "open",
                "line": "0",
            }
        )
        _logger.info(line)
