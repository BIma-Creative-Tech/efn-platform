"""G56 efn_web_home: halaman login memuat logo EFN tanpa mengubah form autentikasi."""

from odoo.tests import HttpCase, tagged

LOGO_PATH = "/efn_web_home/static/src/img/efn_logo.png"


@tagged("post_install", "-at_install", "efn")
class TestEfnLoginPage(HttpCase):
    def test_login_page_shows_efn_logo(self):
        response = self.url_open("/web/login")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn(LOGO_PATH, html)
        self.assertIn("o_efn_login", html)
        # Form CE tetap: field login/password + action /web/login.
        self.assertIn('name="login"', html)
        self.assertIn('name="password"', html)
        self.assertIn('action="/web/login"', html)

    def test_logo_asset_served(self):
        response = self.url_open(LOGO_PATH)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers.get("Content-Type", "").startswith("image/png"))
