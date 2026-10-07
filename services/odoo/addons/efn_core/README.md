# efn_core

Fondasi addons EFN di Odoo CE 19 (satu database multi-company, ADR-001). TLS/kredensial mengikuti `docs/04_SERVICE_CONTRACTS.md` §6–§7 (v2) dan ADR-022.

## Isi
- `res.company`: `efn_brand_code` (Selection `EFN`/`RTL`/`FNB`/`FARM`, unik — sesuai `docs/04` §1); `efn_business_type` **compute stored readonly** dari kode (3b — dipilih daripada menghapus field karena paling sedikit risiko: nama & nilai tetap untuk pembaca yang ada, tidak mungkin lagi tidak konsisten; rezim pajak tetap dari konfigurasi pajak standar, bukan field ini); `efn_is_merchant_of_record` (label "Agen Penagih / Pemegang Akun Gateway", ADR-019) — **maksimal satu company** (constraint Python, menghitung lintas company termasuk arsip). Tab "EFN" di form company.
- `efn.bff.client` (AbstractModel): klien HTTP server-to-server ke BFF NestJS.
  - **TLS wajib:** base URL dari env `EFN_BFF_URL` (bila ada) atau `ir.config_parameter` `efn.bff_url` (default `https://api-gateway:3000`). Sertifikat BFF diverifikasi terhadap CA internal di env `EFN_CA_FILE` (default `/run/secrets/efn_ca_crt`) — `requests.post(..., verify=<path CA>)`, tidak pernah `verify=False`.
  - `http://` ditolak (`bff_insecure_url`) kecuali `EFN_ALLOW_PLAINTEXT=true` **dan** `APP_ENV` ∈ {`development`,`dev`,`local`,`test`,`ci`}. `APP_ENV` kosong = produksi (fail-closed).
  - Timeout dari `efn.pos.otp_timeout_seconds` (default 5 s, maks 30 s).
  - Service key dari file yang ditunjuk env `EFN_BFF_API_KEY_FILE` (default `/run/secrets/odoo_bff_api_key`), header `X-EFN-Service-Key`. Dibaca per panggilan (mendukung rotasi).
  - `post(path, payload, log_context)` dan **`get(path, params, log_context)`** (Gelombang 5, docs/04 §8.8: status payment & daftar erasure) berbagi satu implementasi `_efn_request` → aturan TLS/CA, service key, timeout, tanpa redirect, tanpa log payload/parameter **identik**. Path ditolak bila memuat `?`, `#`, `..`, `//`, `\` atau spasi/karakter kontrol (`ValueError`, tanpa HTTP); `params` hanya nilai skalar. Hasil `BffResponse(status, data, body)`: `data` selalu dict, `body` = JSON mentah (bisa list, untuk endpoint array) untuk 2xx/4xx; `EfnBffError(code)`: `bff_not_configured`, `bff_insecure_url`, `bff_ca_missing`, `bff_key_missing`, `bff_tls_error` (verifikasi sertifikat gagal — tidak di-retry tanpa verify), `bff_timeout`, `bff_unreachable`, `bff_server_error` (5xx/3xx). Redirect tidak diikuti.
  - Tidak pernah me-log key, payload, OTP, atau token; nomor HP di log di-mask (`mask_phone` → `62812****890`).
  - **R2 (Gelombang 9, docs/04 §8.13): tidak ada PII di URL.** `get()` menolak (`ValueError`, tanpa HTTP) kunci query `PII_QUERY_KEYS` (`member_ref`, `member_hash`, `phone`, `mobile`, `msisdn`, `email`, `token`, `discount_token`, `otp`, `code`, `challenge_id`, `nik`, `npwp`; tidak peka huruf besar) dan nilai yang tampak seperti nomor HP (≥ 8 digit, boleh `+`/spasi/`-`); `post()`/`get()` menolak segmen path seperti nomor HP. Audit pemakaian `get` di addons: hanya `GET /v1/pos/payments/<uuid>` dan `GET /v1/pos/privacy/erasures?store=odoo` — tanpa PII. Odoo tidak memanggil saldo poin; bila dibutuhkan, wajib `post("/v1/pos/loyalty/balance", {"member_ref": …})`.
  - Helper nomor: `parse_member_ref()` → `(member_ref, None)` | `(None, no_phone|foreign_phone|invalid_phone)`; `normalize_member_ref()`; `partner_member_ref(partner)` (dari `res.partner.phone`/`mobile`, error tambahan `partner_missing`). Hanya `^62[0-9]{8,13}$`.
- `ir.http._handle_error` (Gelombang 12b, SEC-22; `models/ir_http.py`): respons error **JSON-RPC** dan **`/json/2`** untuk user bukan `base.group_system` (kasir/POS, portal, publik, tanpa sesi) tidak lagi memuat traceback — `debug` dikosongkan (tetap string; web client & POS memakainya sebagai string). Exception bisnis (`UserError` & turunannya, `RedirectWarning`, `SessionExpiredException`, HTTPException) mempertahankan `name`/`message`/`arguments` (dialog POS/web tidak berubah); exception tak terduga → pesan generik, `name`=`Exception`, tanpa argumen/konteks. Admin sistem (uid **sesi**, bukan `request.env.uid` yang bisa dinaikkan controller; API key untuk `/json/2`) tetap mendapat `debug`. Gagal cek group / gagal parse → fail closed. Log server tidak berubah. Route `type='http'` tidak disentuh. Batasan: request tanpa DB (`_serve_nodb`) tidak lewat `ir.http`.
- Parameter default (noupdate): `efn.bff_url`, `efn.pos.otp_required_for_promo`, `efn.pos.promo_disabled_when_offline`, `efn.pos.otp_timeout_seconds`. Migrasi `19.0.1.2.0` mengubah nilai lama `http://api-gateway:3000` → `https://…`.

## Container (`services/odoo/`)
`entrypoint-efn.sh` → `render_odoo_conf.py` (Python + `configparser`, **tanpa `sed`**) → `exec odoo` (entrypoint resmi tidak lagi dipakai karena mengoper `--db_password` di argumen CLI dan punya fallback password `odoo`).

| Env | Default | Keterangan |
|---|---|---|
| `HOST` / `PORT` | `postgres` / `5432` | Host harus cocok dengan SAN sertifikat Postgres (verify-full) |
| `DB_USER` atau `USER` | `efn_odoo` | `postgres` ditolak (least privilege, §7.3) |
| `PASSWORD_FILE` | — (wajib) | Secret `pg_password_odoo`. Env `PASSWORD`/`PGPASSWORD` **ditolak** |
| `ODOO_ADMIN_PASSWORD_FILE` | — (wajib, ≥16 karakter) | Tidak ada fallback `change-me` (SEC-15). Ditulis sebagai hash pbkdf2_sha512 |
| `EFN_CA_FILE` | `/run/secrets/efn_ca_crt` | CA internal (BFF & Postgres) |
| `PGSSLROOTCERT` | = `EFN_CA_FILE` | Dibaca libpq; wajib terbaca (PEM) |
| `ODOO_HTTP_INTERFACE` | `127.0.0.1` | Pola sidecar ghostunnel (`network_mode: service:odoo`) — plaintext hanya di loopback. Non-loopback hanya dengan plaintext dev |
| `ODOO_WORKERS` / `ODOO_MAX_CRON_THREADS` | `2` / `1` | |
| `ODOO_DEV_MODE` | kosong | Ditolak bila `APP_ENV=production` |
| `ODOO_DB_WAIT_TIMEOUT` | `30` | Tunggu Postgres (dengan TLS yang sama) sebelum start; `0` = lewati |
| `EFN_ALLOW_PLAINTEXT` + `APP_ENV` | — | Dev saja: izinkan `ODOO_DB_SSLMODE` ≠ verify-full, BFF `http://`, interface non-loopback. Flag di APP_ENV produksi/kosong → gagal start |

Hasil render: `db_sslmode = verify-full`, `proxy_mode = True`, `http_interface`, kredensial DB; file `0600` milik `odoo`, ditulis atomik (tmp + rename). Odoo 19 memprioritaskan env (`PGSSLMODE`, `PGHOST`, …) di atas file, sehingga render gagal bila `PGSSLMODE` bertentangan.

**Kenapa bukan `sed`/`envsubst`:** nilai tidak pernah masuk ke teks substitusi/regex, sehingga `| & / \ % $ ;` aman. Yang tidak dapat direpresentasikan format INI (baris baru/karakter kontrol, spasi di awal/akhir, literal `False`/`None` yang diperlakukan khusus Odoo 19) **ditolak dengan pesan jelas** — tidak dirusak diam-diam.

## Risiko dimitigasi
R-S01 (secret tidak di config repo/CLI/log), R-S03, SEC-15, SEC-22 (traceback di respons RPC), enkripsi in transit (docs/04 §7.1), UU PDP (masking nomor HP).

## Uji
`odoo -d <db> -i efn_core --test-tags /efn_core` (tag `efn`). HTTP di-mock (`unittest.mock`): verify CA dipakai, CA hilang, SSLError, http ditolak di produksi / tanpa flag / APP_ENV kosong, http diizinkan di dev, env URL override, default https, kode error nomor. `test_error_redaction` (HttpCase): kasir UserError → pesan utuh tanpa traceback; kasir exception tak terduga → generik; tanpa sesi → tanpa traceback; admin → traceback ada; `/json/2` dengan API key kasir vs admin.

## Batasan / belum terverifikasi
Test dijalankan di CI lokal (Docker). R4: deskripsi Apps dari `static/description/index.html` (semua addon `efn_*`) agar README.md tidak dirender docutils sebagai RST saat install. View inherit `base.view_company_form` mengasumsikan ada `<notebook>`. `render_odoo_conf.py` belum punya unit test otomatis (tidak ada runner Python untuk `services/odoo` di CI) — diuji lewat start container.
