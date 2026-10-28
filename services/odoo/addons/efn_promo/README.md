# efn_promo — banner slider, pop-up & push promo Super-App

Tujuan: tim marketing mengelola **banner slider hero** dan **pop-up promo** Super-App (web & aplikasi) di Odoo,
untuk semua brand, plus mengantrekan **push notification** promo. Kontrak: `docs/04_SERVICE_CONTRACTS.md` §18.

- Menu **Promo Super-App → Banner slider / Pop-up promo** (grup *Pengelola Promo*). Periode tayang wajib, gambar
  wajib (landscape 16:9 disarankan), tautan tombol: brand / produk / URL https / tanpa tautan.
- Hero lama tetap slide pertama di aplikasi/web; banner di sini menjadi slide berikutnya (urut kolom *Urutan*).
- Pop-up: frekuensi sekali per kampanye / sekali per hari / setiap dibuka. Tombol **Kirim push** mengantrekan push;
  BFF mengambilnya (`efn_promo_push_claim`) dan mengirim **hanya ke member ber-consent marketing**.
- RPC untuk BFF (user teknis `group_promo_bff`, tepat satu company): #22 `efn_promo_feed`, #23 `efn_promo_image`,
  #24 `efn_promo_push_claim`.
- Tidak menyimpan data member; tidak memuat harga/diskon (diskon nyata = `sale_loyalty`).

ADR: ADR-031 (tanpa modul pihak ketiga), ADR-034 (BFF + /json/2 terfilter). Risiko: R-L01 (PDP: push hanya dengan
consent), R-S03 (akses BFF terbatas).

Belum: teks consent & materi push menunggu DPO/Legal; push nyata butuh FCM/EAS di build APK.
