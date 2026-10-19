# efn_pii_pos (19.0.1.0.0) — E-38a POS

Modul jembatan `auto_install` (`efn_pii` + `point_of_sale`). **Belum untuk produksi/CI** (keputusan lead).
ADR-036 §0 (Accepted; 18-30, 18-31), K-36-9, ADR-017 (fitur server nonaktif saat POS offline), ADR-031.

## Tujuan
1. `pos.order.email` dan `pos.order.mobile` **dienkripsi untuk semua order** (`EfnPiiChar`, `_efn_pii_always`):
   salinan dari partner (`_compute_contact_details`) maupun email yang diketik kasir untuk struk
   (`action_send_receipt` → `self.email = email`). Format `efnpii2:` AAD `efn:odoo:pos_order.<kolom>:<id>:v2`.
   BFF/ETL tidak membaca kolom ini.
2. **Partner terlindungi tidak di-preload** ke PC kasir: `pos.config.get_limited_partners_loading` disaring (dipakai
   preload sesi dan gulir daftar pelanggan tanpa kueri). Pengecualian: partner order draft yang dimuat sesi.
3. **Pencarian kasir** (Enter di daftar pelanggan → `res.partner.get_new_partner(config, domain, offset)`):
   HP lengkap via `phone_mobile_search` (blind index), nama via token (`complete_name ilike`), email/NPWP lengkap.
   Penulisan ulang domain ada di `efn_pii`; modul ini tidak menambah patch OWL.

Risiko dimitigasi: R-L01, R-L10 (salinan kontak POS), R-T18 (SEC-137 untuk `pos.order`).

## Mode terkunci
Penjualan tetap jalan: salinan email/HP dari partner **tidak dibuat** (kosong). Tulis eksplisit (kirim struk email)
ditolak `UserError` + CRITICAL. Tidak ada fallback plaintext. OTP/poin/MGM saat POS offline tetap dinonaktifkan
oleh modul masing-masing (tidak diubah di sini).

## Order lama
`post_init_hook` mengenkripsi email/HP order yang sudah ada bila kunci tersedia; tanpa kunci hanya WARNING (jumlah).
Ulangi setelah kunci dipasang: `env['pos.order'].sudo()._efn_pii_encrypt_legacy(); env.cr.commit()`.

## Uji manual
1. Kunci terpasang, install `efn_pii` + `point_of_sale` (modul ini ikut otomatis).
2. Buka POS, buat pelanggan "Dewi Lestari" HP `0812…`, jual, kirim struk email.
   `SELECT email, mobile FROM pos_order ORDER BY id DESC LIMIT 1` → `efnpii2:…`.
3. Tutup/buka sesi: daftar pelanggan awal tidak memuat "Dewi" (perusahaan tetap). Ketik `dewi` + Enter → muncul;
   ketik HP lengkap + Enter → muncul; ketik `7000` + Enter → tidak.

## Test
`tests/test_pii_pos.py` (tag `efn`, `efn_pii`; `TestPoSCommon`, kunci dipasang di `setUpClass` sebelum partner dibuat).

## Asumsi / belum terverifikasi
- `@api.private` pada override `get_limited_partners_loading`: diasumsikan klien POS 19 tidak memanggilnya via RPC
  (pemanggil yang terlihat di source 19.0 hanya `res.partner._load_pos_data_domain` dan `get_new_partner`). Bila
  ternyata dipanggil JS, hapus dekorator dan tambahkan entri `RPC_ALLOWLIST` di `efn_core/tests/test_rpc_surface.py`.
- UX: hasil lokal kosong tidak otomatis memanggil server; kasir harus menekan Enter (perilaku POS 19 standar,
  `partner_list.js`). Pelatihan kasir (R-O07). Tour belum ditulis (AC-12, QA).
- AC-1 (`_load_pos_data` nyata, overhead < 5 %, p95 pencarian ≤ 300 ms pada 200 rb partner) **belum diukur**.
- `mail.mail` struk email: sejak efn_pii 19.0.3.0.0 (G21, SEC-153) `mail.mail` ber-`model='pos.order'` selalu
  menyimpan `email_to`/`email_cc` terenkripsi (`pos.order._efn_pii_always`); `auto_delete` template struk menghapusnya
  setelah terkirim (perilaku `mail.template` CE, belum diuji runtime untuk struk POS 19). `pos_self_order`/
  `pos_restaurant` yang mungkin mendefinisikan ulang `email`/`mobile`: belum diperiksa (tertangkap `_register_hook` +
  SEC-137 bila terjadi).
- 19.0.1.1.0: `_create` mencatat id baru (SEC-148); `_efn_pii_encrypt_legacy` hanya mengikat `efnpii1` dalam mode
  migrasi (`EFN_ODOO_PII_ACCEPT_V1=true`).

## Referensi desain (ADR-031)
Ditulis ulang dari source Odoo CE 19.0 (`point_of_sale/models/{pos_order,pos_config,res_partner}.py`,
`static/src/app/screens/partner_list/partner_list.js`). Tidak ada kode OCA/Cybrosys yang disalin.
