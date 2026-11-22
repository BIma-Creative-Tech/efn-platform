# efn_web_home — Beranda EFN (home console) + gaya login (G56)

Versi `19.0.1.0.0` · LGPL-3 · `depends: web, mail` · Odoo 19.0 Community.

## Tujuan
- Halaman beranda pasca-login (client action `efn_web_home.home`): sapaan, logo PT aktif, kolom cari (membuka command
  palette Ctrl+K bawaan), pintasan per user, "Terakhir dibuka", kartu aplikasi per kelompok, aktivitas user.
- Tombol apps (ikon grid) di navbar desktop membuka beranda; menu root **Beranda** (`efn_web_home.menu_home_root`).
- Branding per PT di **Settings > Users & Companies > Companies > tab "Beranda EFN"**: warna aksen
  (`efn_brand_accent_color`, #RRGGBB), logo beranda (`efn_brand_logo_home`), pengumuman (`efn_home_announcement_html`
  + `efn_home_announcement_active`).
- Preferensi per user (form user, tab "Beranda EFN"): `efn_home_density`, `efn_home_pinned_action_ids`.
- Gaya ringan halaman `/web/login`: logo EFN + warna EFN. Form autentikasi (field, CSRF, action, TOTP) tidak diubah.

### Kelompok kartu (deterministik, dihitung server di `res.users.efn_home_bootstrap`)
| Kelompok | Menu root (xmlid) |
|---|---|
| Operasional | `point_of_sale.menu_point_root`, `stock.menu_stock_root`, `mrp.menu_mrp_root`, `purchase.menu_purchase_root`, `efn_agri.menu_efn_agri_root`; root apa pun dari `efn_pos_kds`, `efn_ck_production`, `efn_kitting`, `efn_online_kds_bridge` |
| Finance | `account.menu_finance`, `efn_online_transfer.menu_efn_online_transfer_root` |
| Accounting | `efn_setup_menu.menu_accounting_root` (G55; dicari dengan `env.ref(..., raise_if_not_found=False)` sehingga beranda tetap jalan tanpa modul itu) |
| Insight & Data | `efn_insight.menu_insight`, `efn_data_ask.menu_efn_data_ask_root`, `efn_loyalty_accounting.menu_efn_loyalty_root` (Poin EFN), `efn_campaign.menu_efn_campaign_root`, `efn_promo.menu_efn_promo_root` |
| Admin | `base.menu_administration`, `base.menu_management`, `efn_audit.menu_efn_audit_root` |
| Lainnya | sisanya |

Menu root yang tidak ada di tabel memakai fallback kata kunci nama kategori modul pemilik xmlid (en_US: accounting /
invoicing -> Finance; point of sale / inventory / manufacturing / purchase / sales -> Operasional; administration /
technical -> Admin). Penggantian nama menu (mis. `mrp` -> "Produksi", `account` -> "Finance") di luar modul ini;
kartu memakai nama menu apa adanya.

Daftar aplikasi = `ir.ui.menu.load_menus()` milik user pemanggil (visibilitas CE: grup menu + akses model action),
jadi user yang hanya POS User tidak melihat Invoicing/Settings/Apps.

## ADR
- **ADR-049** (port kode `odoo-platform` milik pemilik proyek; status: diputuskan 23 Nov 2026, kepemilikan menunggu
  konfirmasi tertulis Legal sebelum production).
- ADR-031 (tanpa dependency modul pihak ketiga), ADR-015 (penamaan `efn_*`).

## Provenance (ADR-049)
- Modul asal: `odoo-platform` `addons/core/custom_home_console`, manifest versi `19.0.1.0.0`, author asal
  "Custom Platform", LGPL-3. Commit `odoo-platform` terakhir yang menyentuh modul: `cfb2e5f` (origin/main,
  dibaca 23 Nov 2026).
- Yang diubah:
  - Dependency `custom_core` dibuang (tidak dipakai); depends hanya `web`, `mail`.
  - **309 ikon PNG tidak disalin** (banyak ikon aplikasi Odoo Enterprise, lisensi proprietary). Kartu memakai ikon
    menu CE (`web_icon` / `web_icon_data`) dengan fallback tile huruf awal (CSS). Tidak ada aset Enterprise.
  - Semua xmlid, path aset, tag client action (`efn_web_home.home`), nama modul JS/template, kelas CSS (`o_efn_home_*`),
    nama service (`efn_web_home_recent`) diganti; field baru di `res.company`/`res.users` diberi awalan `efn_`.
  - Model `ir.module.category.home_console_group` (heuristik nama) dan peta slug modul di JS diganti pemetaan
    deterministik xmlid menu root di server (tabel di atas).
  - Kartu sintetis "Settings" dan daftar ikon override dibuang; menu Settings CE tampil apa adanya (Admin).
  - Bootstrap memakai `env.company` (PT aktif), bukan `user.company_id`; warna aksen divalidasi `#RRGGBB` (cegah
    injeksi CSS di atribut style); checklist onboarding (search produk lintas PT dengan sudo) dibuang.
  - Pengumuman dirender dengan `markup()` OWL (HTML sudah disanitasi server); penanda "sembunyikan 24 jam" per PT.
  - Hook post-init hanya mengisi `action_id` user internal (`share=False`) yang kosong; uninstall hanya mengosongkan
    pointer ke action modul ini (sama dengan sumber).
  - Baru: gaya halaman login (`views/login_templates.xml` mewarisi `web.login_layout`, `static/src/login/login.scss`)
    dengan logo `static/src/img/efn_logo.png` = salinan aset EFN sendiri
    `services/superapp-web/public/brands/efn/ebn.png`.
- Tidak ada branding/data klien lain (Erajaya-Levi's/EAL/ARKA) yang terbawa.

## Risiko yang dimitigasi
- R-T03 / R-L08 (aset/lisensi pihak lain): tanpa ikon Enterprise, tanpa dependency non-CE.
- Kebocoran lintas PT: bootstrap tanpa argumen, hak user pemanggil, hanya `env.company`; test memastikan pengumuman
  PT kedua tidak ikut. Metode terdaftar di `RPC_ALLOWLIST` (`efn_core/tests/test_rpc_surface.py`, SEC-58).
- Menu yang tidak boleh diakses tidak tampil sebagai kartu (visibilitas menu CE); daftar "Terakhir dibuka" lokal
  difilter ke aplikasi yang masih terlihat.

## Cara uji manual
1. Install `efn_web_home`, login sebagai admin -> landing = Beranda; kartu terkelompok; klik kartu membuka app dan
   navbar berganti ke app itu; ikon grid navbar kembali ke Beranda.
2. Companies > PT A > tab "Beranda EFN": aksen `#dfb422`, logo, pengumuman aktif. Ganti PT aktif di switcher ->
   branding/pengumuman mengikuti PT aktif; PT B tanpa pengumuman tidak menampilkan banner.
3. Login sebagai user POS saja -> tidak ada kartu Invoicing/Settings/Apps.
4. Logout -> `/web/login` menampilkan logo EFN di atas logo company; login tetap berfungsi (termasuk TOTP).

## Batasan / belum
- Belum dijalankan di Odoo 19 nyata oleh penulis (lead menjalankan test di container).
- File `static/src/img/efn_logo.png` harus disalin dari `services/superapp-web/public/brands/efn/ebn.png` (alat penulis
  tidak bisa menyalin biner); test `test_logo_asset_served` gagal bila belum.
- User yang dibuat SETELAH install tidak otomatis mendapat landing Beranda (perilaku sumber); admin dapat mengisi
  "Home Action" user. Belum ada default `action_id` untuk user baru.
- Mode mobile (sidebar apps CE) dan PWA scoped app tidak diganti; beranda tetap bisa dibuka lewat menu Beranda.
- Aktivitas di beranda = aktivitas milik user yang dokumennya terbaca di PT aktif (filter `mail.activity._search`
  CE); tidak ada filter company tambahan.
- Label kelompok berbahasa Indonesia, belum diterjemahkan (`_t`/`.po`).
