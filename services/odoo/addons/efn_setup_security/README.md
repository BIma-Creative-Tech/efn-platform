# efn_setup_security

Modul konfigurasi **data-only**: kebijakan login user internal Odoo.

- **Acuan:** CLAUDE.md (`auth_totp` untuk user internal), `docs/04 §7`, Q-54. **Risiko:** R-S03 (akun internal diambil alih), R-S10 (insider).
- **Fit-gap:** `docs/08_ODOO_FIT_GAP.md` F-19.

## Apa yang dikonfigurasi
| Setting | Nilai | Sumber (19.0, terverifikasi) |
|---|---|---|
| `auth_totp.policy` | `employee_required` | `auth_totp_mail/models/res_config_settings.py` (`auth_totp_enforce`, `auth_totp_policy`: `employee_required`/`all_required`) |
| `auth_password_policy.minlength` | `12` (usulan) | `auth_password_policy/models/res_config_settings.py` |

Modul yang di-install: `auth_totp` (TOTP authenticator), `auth_totp_mail` (undangan + **penegakan** 2FA), `auth_password_policy`.
`auth_totp_mail_enforce` **tidak ada** di 19.0 (404) — penegakan sudah dilebur ke `auth_totp_mail`.

## Perilaku (dari kode `auth_totp_mail/models/res_users.py` 19.0)
- User internal tanpa TOTP: saat login diarahkan ke `/web/login/totp` dan menerima **kode 6 digit via email** (berlaku ±1 jam). Setelah mengaktifkan aplikasi authenticator, TOTP yang dipakai.
- Konsekuensi:
  1. **Outgoing mail server wajib** (SMTP dengan TLS terverifikasi — prinsip enkripsi CLAUDE.md). Tanpa itu user internal tidak bisa login.
  2. **Kasir POS** adalah user internal → kena 2FA saat login Odoo. Rekomendasi: satu user per outlet/terminal + `pos_hr` (login karyawan dengan PIN di dalam sesi POS), perangkat ditandai *trusted device* setelah login pertama.
  3. **User integrasi** (BFF → Odoo, ETL) memakai **API key** (`res.users.apikeys`), bukan password; jangan menonaktifkan 2FA untuk user tersebut.
- Panjang password minimum 12: berlaku saat ganti/set password berikutnya.

## Keputusan klien yang dibutuhkan
- **Q-54** kebijakan password/SSO (panjang minimum, SSO Google Workspace/Azure AD?). Bila SSO dipilih: `auth_oauth` (CE) — 2FA lalu ditangani IdP.
- Apakah portal user (pelanggan) juga wajib 2FA (`all_required`)? Default: tidak (pelanggan login lewat Super-App/BFF, bukan Odoo).
- SMTP relay mana yang dipakai Odoo (lihat devsecops).

## Cara install
```bash
odoo -d <db> -i efn_setup_security
```
Ubah kemudian via *Settings → Permissions → Enforce two-factor authentication* dan *Minimum Password Length* (tidak ditimpa upgrade).

## Batasan
Belum pernah di-install. Aturan audit log dipisah ke `efn_setup_audit` (data untuk modul custom `efn_audit`, Gelombang 10 — tanpa dependency OCA) karena menarik dependensi `stock`/`loyalty`.
