# Security Review — feat/odoo-completion (G54/G55/G56/G61/G62) — 2026-11-23

Reviewer: efn-security-reviewer (read-only). Cakupan: perubahan belum di-commit di worktree `efn-platform-odoo`
(`efn_setup_pos` wizard G54 + data QRIS manual, `efn_setup_menu` G55, `efn_web_home` G56, `services/login-gateway` +
wiring infra G62, `scripts/migration` G61 + `efn_legacy_history._adopt_category`, ADR-049/050). Metode: baca kode
(Grep/Read); **tidak ada test yang dijalankan** oleh reviewer (tanpa shell).

## Ringkasan eksekutif

- **0 Critical, 0 High, 1 Medium, 5 Low, 2 Info.** Tidak ada temuan yang memblokir merge dari sisi eksploitasi.
- Medium satu-satunya bersifat **gerbang proses**: metode RPC publik wizard G54 belum didaftarkan di `RPC_ALLOWLIST`
  efn_core, sehingga test `test_rpc_surface` akan merah dan review akses metode baru tidak tercatat.
- Kontrol akses wizard POS, isolasi branding beranda per PT, sanitasi HTML pengumuman, dan login-gateway (allowlist redirect
  dari env, tanpa secret, mTLS, read-only, network terisolasi) sudah benar.
- Risiko sisa ada di proses: QRIS manual (kasir menyatakan lunas sendiri), kategori produk global lintas PT (SMD), dan
  konfirmasi legal provenance ADR-049.

## Tabel temuan

| ID | Severity | Lokasi | Ringkas | Risiko |
|---|---|---|---|---|
| SEC-G54-01 | Medium | `services/odoo/addons/efn_setup_pos/wizard/efn_pos_setup_wizard.py:97,109`; `efn_core/tests/test_rpc_surface.py:549` | `efn.pos.setup.wizard.action_run` dan `default_get` publik, tidak ada di `RPC_ALLOWLIST` -> gerbang RPC gagal | SEC-58, R-S (RPC surface) |
| SEC-G54-02 | Low | `efn_pos_setup_wizard.py:59-61,226-229,313-316` | Teks exception mentah (s.d. 300 char, termasuk error DB/internal) disimpan di baris hasil wizard | konvensi "error tidak membocorkan detail internal" |
| SEC-G54-03 | Low | `efn_setup_pos/data/pos_payment_method_qris_manual_data.xml:2-12` | QRIS manual `payment_method_type=none`: kasir memvalidasi sendiri tanpa bukti sistem -> peluang fraud kasir | fraud/SoD (tambahkan ke register) |
| SEC-G55-01 | Low (PLAUSIBLE) | `efn_setup_menu/data/menu_data.xml:15-34` | Menu baru "Kas & Bank" di Finance memakai `group_account_invoice` (Billing): rekening koran/register kas/transfer internal kini terlihat untuk user Billing bila ACL CE mengizinkan | least privilege |
| SEC-G61-01 | Low (PLAUSIBLE) | `efn_legacy_history/esb_import/importer.py:814-823`; `scripts/migration/recategorize_pb.py:175-180,248-253` | `product.category` global; adopsi menurut nama+induk bisa menautkan xmlid PB ke kategori milik/dipakai SMD -> import/rekategorisasi PB kelak mengubah kategori bersama | aturan independensi SMD, multi-company |
| SEC-G61-02 | Low | `scripts/migration/recategorize_pb.py:104-111` | Penolakan SMD hanya berdasarkan string `EFN_G61_COMPANY == "SMD"`; tidak memeriksa `efn_business_type`/xmlid company | aturan independensi SMD |
| SEC-G56-01 | Info | `efn_web_home/README.md:38-41,78`; ADR-049 §Konsekuensi; `login-gateway/public/brand/ebn.png` | Provenance belum lengkap (hash commit `odoo-platform` kosong, konfirmasi kepemilikan Legal tertunda); logo bertuliskan "EBN" dipakai sebagai "EFN" | R-L08, R-T03 |
| SEC-G62-01 | Info | `services/login-gateway/lib/csp.ts:4`, `app/page.tsx:24,52` | `next/image` memancarkan atribut `style="color:transparent"`; CSP tanpa `style-src-attr` memblokirnya (pelanggaran CSP di konsol, tidak berbahaya). Komentar "tidak ada atribut style" tidak akurat | — |

## Detail

### SEC-G54-01 (Medium) — metode RPC wizard G54 di luar allowlist

- **Fakta.** `scan_public_methods()` (`efn_core/tests/test_rpc_surface.py:581-602`) memindai semua kelas model `efn_*`
  termasuk `TransientModel` dan folder `wizard/`. `action_run` (baris 109) baru dan publik; `default_get` (baris 97) tidak
  ada di `ORM_OVERRIDES` (baris 34). Grep `efn.pos.setup.wizard` di `test_rpc_surface.py` = 0 hasil (bandingkan
  `efn.forecast.run.wizard.action_run` di baris 365-367 yang terdaftar).
- **Dampak.** `test_public_methods_are_allowlisted` dan `test_installed_models_new_public_methods_allowlisted` gagal ->
  CI merah; lebih penting, kontrol "setiap metode RPC baru ditinjau" dilewati. Secara eksploitasi metode ini **sudah
  aman**: ACL hanya `point_of_sale.group_pos_manager`, rule transient (create_uid) + rule company, dan
  `_efn_check_access` (baris 142-157) memeriksa company di `user.company_ids`, grup POS manager, dan untuk apply
  `account.group_account_manager` sebelum `sudo().with_company()`.
- **Perbaikan.** Tambahkan ke `RPC_ALLOWLIST`:
  `("efn.pos.setup.wizard", "action_run"): "G54: ACL pos_manager + rule company; _efn_check_access (company di user.company_ids, pos_manager; apply + account_manager) sebelum sudo"`
  dan `("efn.pos.setup.wizard", "default_get"): "G54: override CE; browse active_ids dengan hak user (record rule)"`.
  Jalankan `efn_core` test tag `efn`.

### SEC-G54-02 (Low) — teks exception mentah di hasil wizard

- `except Exception` -> `_exc_text(exc)` menyimpan `exc.args[0]` ke `message` baris wizard (dibaca user, tersimpan di
  tabel transient). Untuk `psycopg2` IntegrityError/ProgrammingError teks berisi nama constraint/kolom/nilai.
- **Skenario.** Apply gagal karena constraint unik jurnal -> pesan memuat detail SQL. Hanya terlihat oleh POS+Accounting
  manager, jadi dampak rendah.
- **Perbaikan.** Hanya teruskan teks untuk `UserError`/`ValidationError`; untuk exception lain tampilkan pesan generik
  ("Kesalahan internal, lihat log #<config_id>") dan log `type(exc).__name__` (sudah dilakukan).

### SEC-G54-03 (Low) — QRIS manual tanpa bukti sistem

- Metode "QRIS (manual, cek mutasi)" bertipe `none`: kasir menekan validasi setelah "melihat" notifikasi merchant. Kasir
  dapat menandai transaksi lunas tanpa dana masuk (atau memakai screenshot lama). Jurnal bank terpisah (`QRIS`) sudah
  membantu rekonsiliasi.
- **Perbaikan.** SOP rekonsiliasi harian jurnal QRIS vs mutasi merchant oleh pihak non-kasir (SoD, pola Verifikator
  Pembayaran K25-07), laporan selisih per sesi/kasir, dan rencana pindah ke QRIS dinamis `efn_pos_payment_gateway`.
  Catat sebagai risiko operasional di register.

### SEC-G55-01 (Low, PLAUSIBLE) — visibilitas menu Kas & Bank untuk Billing

- `menu_efn_cash_bank` `groups="account.group_account_invoice"`; anak-anaknya tanpa grup sendiri. `load_menus` CE tetap
  menyembunyikan menu bila ACL model action tidak terbaca, jadi tidak ada eskalasi hak data; namun bila ACL CE memberi
  Billing akses baca/tulis `account.bank.statement` / `account.payment`, menu transfer internal kini mudah dijangkau user
  Billing (di CE standar menu ini ada di bawah grup akuntan).
- **Perbaikan.** Verifikasi dengan user Billing-only; bila terlihat, ganti ke `account.group_account_user` (atau
  `group_account_readonly` untuk rekening koran). Akar Accounting (`group_account_readonly,group_account_manager`) dan
  menu CE yang dipindah tetap membawa grup CE masing-masing — benar.

### SEC-G61-01 (Low, PLAUSIBLE) — kategori global lintas PT

- `_adopt_category` mencari `product.category` menurut `name` + `parent_id` tanpa batas company/xmlid, lalu `_bind` ke
  xmlid bundel PB; `recategorize_pb.py` mencari menurut `complete_name` lalu membuat xmlid importer dan (langkah f)
  menghapus kategori kosong di bawah root brand. Saat ini belum ada kategori SMD bernama `Persediaan / ...` (grep), jadi
  belum tereksploitasi.
- **Skenario.** SMD kelak membuat `Persediaan / Bahan Baku`; import PB berikutnya mengadopsinya; perubahan nama/induk di
  bundel ESB PB lalu ditulis `upsert` ke kategori yang dipakai produk SMD (melanggar "SMD independen").
- **Perbaikan.** Adopsi hanya bila kategori belum punya xmlid modul lain dan tidak dirujuk `product.template` milik
  company selain PT target; atau prefiks pohon per brand untuk SMD. Tambahkan test.

### SEC-G61-02 (Low) — guard SMD berbasis string

- Hanya `code == "SMD"`. Bila kode brand SMD berubah atau ada PT supermarket kedua, skrip bisa diarahkan ke sana.
- **Perbaikan.** Tolak juga bila `company.efn_business_type == "supermarket"` atau company = xmlid
  `efn_setup_company.company_smd`.

### SEC-G56-01 (Info) — provenance ADR-049 / aset logo

- Tidak ditemukan aset Enterprise: `efn_web_home/static` hanya berisi kode + `img/efn_logo.png`; 309 ikon sumber tidak
  disalin (README:44-45). Logo brand login-gateway berasal dari `services/superapp-web/public/brands/` (README:35-36).
- Terbuka: hash commit `odoo-platform` belum dicatat; konfirmasi tertulis kepemilikan kode (bukan milik klien
  Erajaya/Levi's/EAL) wajib sebelum production; logo bertuliskan "EBN" — pastikan memang aset resmi EFN dan logo brand
  franchise (Paris Baguette, Bacha, Wetzel's, Grand Lucky) dipakai sesuai lisensi merek.

### SEC-G62-01 (Info) — CSP vs atribut style next/image

- Tidak berdampak keamanan; ubah komentar atau pakai `<img>` biasa agar konsol bersih.

## Yang sudah benar (kontrol yang ada)

- **Wizard G54:** ACL hanya POS manager; record rule company untuk wizard + line; cek server-side company/grup sebelum
  `sudo()`; apply butuh Accounting Administrator; pencarian dibatasi `company_id = wizard.company_id`; config lain
  company ditolak (`_efn_target_configs`); savepoint per langkah/config; mode `check` tidak menulis; log hanya id.
  Data QRIS/EDC tidak dibuat untuk SMD.
- **G55:** uninstall hook memulihkan induk menu CE sebelum akar dihapus (aman, tidak menyentuh data); label lewat
  `sudo()` hanya pada record `ir.ui.menu` yang ada.
- **G56:** `efn_home_bootstrap` tanpa argumen klien, branding dari `env.company` dengan hak user, menu dari `load_menus`
  user; `sudo()` hanya untuk nama pintasan milik user sendiri dan metadata modul; warna aksen divalidasi regex; HTML
  pengumuman `fields.Html(sanitize=True)` (tidak overridable) sebelum `markup()`; tidak ada `t-raw`/`innerHTML`;
  localStorage hanya id/nama menu; form login (CSRF/TOTP) tidak diubah; hook hanya mengisi `action_id` kosong user internal.
- **G62 login-gateway:** redirect hanya dari allowlist env (kunci path, URL https tanpa kredensial) -> tanpa open
  redirect; CSP bernonce tanpa `unsafe-inline/eval` di prod, `frame-ancestors 'none'`, `form-action 'none'`; HSTS,
  nosniff, no-referrer; tanpa secret/cookie; non-root, `read_only`, `cap_drop: ALL`, image dipin digest; network
  `login-ingress` internal-only; sidecar ghostunnel mTLS `--allow-cn` traefik/blackbox, Traefik `serversTransport`
  verifikasi CA internal; `internal-only` + rate limit; semua dikunci `check-encryption.sh`.
- **G61:** DRY-RUN default (rollback), prasyarat sesi POS/stock.move/stock.quant = 0, fail-closed untuk kategori tak
  terpetakan, laporan `O_EXCL` 0600 tanpa PII (id, default_code, kategori, flag).

## Belum diverifikasi

- Test (`efn_setup_pos`, `efn_web_home`, `efn_core` RPC gate, login-gateway vitest) tidak dijalankan reviewer.
- ACL CE 19 `account.bank.statement` untuk grup Billing (SEC-G55-01) belum dicek di image.
