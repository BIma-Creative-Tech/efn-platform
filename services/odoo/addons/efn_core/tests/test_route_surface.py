"""SEC-79 (r8): gerbang CI untuk controller ``http.route`` di addon ``efn_*``.

Melengkapi ``test_rpc_surface.py`` (metode model). Setiap ``@http.route`` / ``@route`` di addon
``efn_*`` (di luar ``tests``/``migrations``) wajib tercantum di :data:`ROUTE_ALLOWLIST` dengan
pasangan ``(path, auth)`` yang SAMA persis + alasan/cek aksesnya. Aturan:

- ``auth`` yang tidak ditulis dianggap ``"user"`` (default Odoo);
- ``auth="public"``/``"none"``/``"bearer"`` (pra-autentikasi) hanya lolos bila entri juga ada di
  :data:`PREAUTH_REVIEWED` (ditinjau efn-security-reviewer, sebut ID review);
- ``csrf=False`` hanya lolos bila ada di :data:`CSRF_EXEMPT_REVIEWED`;
- route tanpa path (override route CE, ``@http.route()``) dicatat sebagai ``<override>:Kelas.metode``
  dan juga wajib di allowlist;
- entri allowlist tanpa route di kode = gagal (hapus entri basi).

Murni AST (``BaseCase``, tanpa DB) — sama seperti gerbang statis SEC-58.
"""

import ast
import pathlib
import tempfile

from odoo.tests import BaseCase, tagged

ADDONS_DIR = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_AUTH = "user"
PREAUTH = frozenset({"public", "none", "bearer"})

# (path, auth) -> cek akses di server. WAJIB ditinjau security sebelum ditambah.
ROUTE_ALLOWLIST = {
    # efn_pos_kds (Gelombang 14, r8 terverifikasi): GET saja, _efn_kds_check_device (grup perangkat
    # stasiun itu / manager POS + record rule + company), 404 seragam, Cache-Control no-store.
    ("/efn_kds/<int:station_id>", "user"): "efn_pos_kds/controllers/main.py: _efn_kds_check_device",
    # efn_pii_l10n_id (G21, ADR-036 §0.8): override route CE unduhan XML Coretax; check_access('read') + grup
    # account.group_account_invoice + semua lampiran milik l10n_id_efaktur_coretax.document; dekripsi di server.
    ("<override>:EfnEfakturDownloadController.download_invoice_attachments", "user"): "efn_pii_l10n_id/controllers/"
    "download_efaktur.py: check_access + account.group_account_invoice + res_model Coretax",
}
# Route pra-autentikasi yang sudah ditinjau: (path, auth) -> ID review. Saat ini kosong.
PREAUTH_REVIEWED = {}
# Route dengan csrf=False yang sudah ditinjau: (path, auth) -> ID review. Saat ini kosong.
CSRF_EXEMPT_REVIEWED = {}


def _is_route_decorator(node):
    func = node.func if isinstance(node, ast.Call) else node
    text = ast.unparse(func)
    return text in ("http.route", "route", "odoo.http.route")


def _const(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def scan_routes(addons_dir=ADDONS_DIR):
    """[(file, class.method, path, auth, csrf)] untuk setiap path route di addon efn_*."""
    found = []
    for path in sorted(addons_dir.glob("efn_*/**/*.py")):
        rel = path.relative_to(addons_dir)
        if len(rel.parts) > 1 and rel.parts[1] in ("tests", "migrations"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            for func in cls.body:
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for dec in func.decorator_list:
                    if not _is_route_decorator(dec):
                        continue
                    kwargs = {kw.arg: kw.value for kw in dec.keywords if kw.arg} if isinstance(dec, ast.Call) else {}
                    args = dec.args if isinstance(dec, ast.Call) else []
                    route_node = args[0] if args else kwargs.get("route")
                    auth_node = kwargs.get("auth")
                    auth = _const(auth_node) if auth_node is not None else DEFAULT_AUTH
                    csrf_node = kwargs.get("csrf")
                    csrf = _const(csrf_node) if csrf_node is not None else True
                    where = f"{cls.name}.{func.name}"
                    if route_node is None:
                        paths = [f"<override>:{where}"]
                    else:
                        value = _const(route_node)
                        if isinstance(value, str):
                            paths = [value]
                        elif isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
                            paths = list(value)
                        else:
                            # Path dinamis (variabel/ekspresi) tidak dapat ditinjau statis -> ditolak.
                            paths = [f"<dynamic>:{where}"]
                    for route_path in paths:
                        found.append((str(rel), where, route_path, auth, csrf))
    return found


@tagged("post_install", "-at_install", "efn")
class TestEfnRouteSurfaceStatic(BaseCase):
    def test_scanner_sees_kds_route(self):
        found = {(p, a) for _f, _w, p, a, _c in scan_routes()}
        self.assertIn(("/efn_kds/<int:station_id>", "user"), found)

    def test_routes_are_allowlisted(self):
        offenders = sorted(
            f"{file}: {where} {route_path!r} auth={auth!r}"
            for file, where, route_path, auth, _csrf in scan_routes()
            if (route_path, auth) not in ROUTE_ALLOWLIST
        )
        self.assertFalse(
            offenders,
            "http.route baru di addon efn_* harus ditinjau security dan didaftarkan (path, auth) di "
            "ROUTE_ALLOWLIST (SEC-79): " + ", ".join(offenders),
        )

    def test_preauth_routes_reviewed(self):
        offenders = sorted(
            f"{file}: {where} {route_path!r} auth={auth!r}"
            for file, where, route_path, auth, _csrf in scan_routes()
            if auth in PREAUTH and (route_path, auth) not in PREAUTH_REVIEWED
        )
        self.assertFalse(
            offenders,
            "Route pra-autentikasi (public/none/bearer) di efn_* wajib tinjauan security eksplisit "
            "(PREAUTH_REVIEWED): " + ", ".join(offenders),
        )

    def test_unknown_auth_rejected(self):
        offenders = [
            f"{file}: {where} auth={auth!r}"
            for file, where, _p, auth, _c in scan_routes()
            if not isinstance(auth, str) or auth not in PREAUTH | {"user"}
        ]
        self.assertFalse(offenders, "Nilai auth route tidak dikenal/dinamis: " + ", ".join(offenders))

    def test_csrf_exempt_reviewed(self):
        offenders = sorted(
            f"{file}: {where} {route_path!r}"
            for file, where, route_path, auth, csrf in scan_routes()
            if csrf is not True and (route_path, auth) not in CSRF_EXEMPT_REVIEWED
        )
        self.assertFalse(offenders, "csrf=False di route efn_* wajib ditinjau: " + ", ".join(offenders))

    def test_allowlist_has_no_stale_entries(self):
        found = {(p, a) for _f, _w, p, a, _c in scan_routes()}
        self.assertFalse(set(ROUTE_ALLOWLIST) - found, "Entri ROUTE_ALLOWLIST tanpa route (hapus).")
        self.assertFalse(set(PREAUTH_REVIEWED) - found, "Entri PREAUTH_REVIEWED tanpa route (hapus).")
        self.assertFalse(set(CSRF_EXEMPT_REVIEWED) - found, "Entri CSRF_EXEMPT_REVIEWED tanpa route (hapus).")

    def test_scanner_detects_public_route(self):
        """Uji-diri scanner pada sumber sintetis (pola yang harus tertangkap)."""
        src = (
            "from odoo import http\n"
            "class C(http.Controller):\n"
            "    @http.route('/efn_x/pub', auth='public', csrf=False)\n"
            "    def a(self): pass\n"
            "    @http.route(['/efn_x/a', '/efn_x/b'])\n"
            "    def b(self): pass\n"
            "    @http.route()\n"
            "    def c(self): pass\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "efn_x" / "controllers"
            target.mkdir(parents=True)
            (target / "main.py").write_text(src, encoding="utf-8")
            found = {(p, a, c) for _f, _w, p, a, c in scan_routes(pathlib.Path(tmp))}
        self.assertEqual(
            found,
            {
                ("/efn_x/pub", "public", False),
                ("/efn_x/a", "user", True),
                ("/efn_x/b", "user", True),
                ("<override>:C.c", "user", True),
            },
        )
