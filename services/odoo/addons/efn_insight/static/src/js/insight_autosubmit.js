/*
 * efn_insight (G42, docs/04 §26.2): kirim otomatis form SSO ke insight-web.
 * File statis yang dimuat langsung oleh halaman controller /efn/insight/open (CSP script-src 'self');
 * sengaja TIDAK didaftarkan di bundle aset Odoo dan bukan modul Odoo (@odoo-module).
 * Tanpa JavaScript, pengguna menekan tombol "Lanjut".
 */
(function () {
    "use strict";
    var form = document.getElementById("efn-insight-sso");
    if (form) {
        form.submit();
    }
})();
