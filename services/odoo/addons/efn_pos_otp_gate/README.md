# efn_pos_otp_gate

Promo/loyalty POS yang ditandai **Wajib OTP** hanya berlaku setelah pelanggan memverifikasi OTP WhatsApp lewat BFF, dengan `discount_token` sekali pakai yang di-consume server saat order di-sync.

- **ADR:** ADR-017 (OTP saat POS offline, opsi a — *Proposed*), ADR-011 (WhatsApp Cloud API), ADR-020 (ledger loyalty). Arsitektur: `docs/02 §3.1`. Kontrak: `docs/04 §3.1`, §6.
- **Risiko dimitigasi:** R-T02 (OTP vs offline), R-S04 (brute force/enumerasi: rate limit & lock di BFF, respons generik), R-B06/R-S10 (fraud promo kasir: SEC-07 OTP terikat ke pelanggan order, SEC-12 gate tidak percaya flag klien), replay token.
- **Enkripsi in transit:** semua panggilan ke BFF lewat `efn.bff.client` (HTTPS + verifikasi CA internal `EFN_CA_FILE`, lihat README `efn_core`).

## Alur
1. Kasir memilih reward di tombol *Reward* (pos_loyalty). Bila program `efn_requires_otp` dan parameter `efn.pos.otp_required_for_promo` = True → alur OTP.
2. **Pelanggan wajib dipilih dulu (SEC-07).** Bila order belum punya pelanggan, POS membuka daftar pelanggan bawaan (`pos.selectPartner`). Kasir **tidak mengetik nomor HP**.
3. Frontend memanggil `pos.config.efn_otp_request(order_uuid, amount, partner_id)` via `pos.data.call` (ORM RPC, **bukan** controller publik). Server membaca `res.partner.phone` (dan `mobile` bila ada modul yang menambahkannya) dengan hak akses user POS, menormalisasi ke `^62[0-9]{8,13}$`, lalu memanggil `POST /v1/pos/otp/request`. Ditolak tanpa memanggil BFF: tanpa pelanggan (`partner_missing`), pelanggan tanpa HP (`no_phone`), nomor non-Indonesia (`foreign_phone`), format salah (`invalid_phone`) — masing-masing dengan pesan jelas untuk kasir.
4. Popup kode 6 digit → `pos.config.efn_otp_verify(order_uuid, code)` → `POST /v1/pos/otp/verify`. Maks 3 percobaan (lock di BFF).
5. `challenge_id` dan `discount_token` **disimpan server-side** di `efn.pos.otp.challenge` (+ `partner_id`, `amount` untuk audit) dan tidak pernah dikirim ke browser. Browser hanya menandai `order.efn_otp_verified` (UI saja, tidak dipercaya server); penanda direset bila kasir mengganti pelanggan.
6. Saat sync (`sync_from_ui` → `_process_order` → `_process_saved_order`):
   - order **draft** dengan reward ber-OTP tanpa OTP terverifikasi → baris reward **dihapus**;
   - order **berbayar** tanpa OTP terverifikasi untuk uuid ini → **ditolak** (`UserError`, rollback);
   - order **berbayar** dengan OTP terverifikasi → `POST /v1/pos/discount-token/consume` dengan `member_ref` dari **partner FINAL order** dan `amount_total` order final (Rupiah bulat, docs/04 §8.8). Sukses → `efn_otp_gate_state = consumed`. Gagal (`already_used`/`expired`/`order_mismatch`/`member_mismatch`/`amount_mismatch`/`invalid`), BFF tidak terjangkau/TLS gagal, atau order final tanpa pelanggan ber-HP (`member_missing:<kode>`, tanpa consume) → order tetap tersimpan, `efn_otp_gate_state = review` (ADR-017 #2: tidak dibatalkan otomatis). Menu *Point of Sale → Orders → Review OTP Promo* (manajer POS).

## Tujuan OTP / `purpose` (E-23, Gelombang 13b, versi 19.0.1.6.0)
`efn.pos.otp.challenge.purpose` = `pos_promo` (default, gate promo) | `pos_redeem` (tukar Poin EFN, modul `efn_pos_loyalty_ledger`). `pos.config.efn_otp_request(order_ref, amount, partner_id, purpose="pos_promo")` mengirim `purpose` ke BFF (`docs/04 §9.3`); `efn_otp_verify(order_ref, code, purpose="pos_promo")` hanya memverifikasi challenge dengan tujuan sama. `_efn_find_latest`/`_efn_find_valid_verified` default `pos_promo`, sehingga gate promo **tidak pernah** menerima challenge tukar poin dan sebaliknya (test lintas-purpose di `tests/test_otp_gate.py`). `efn_otp_flow.js` menerima opsi `{ purpose, amount }`; untuk `pos_redeem` penanda promo order tidak disentuh. Purpose tidak dikenal → `invalid_purpose` tanpa panggilan BFF.

## Deteksi baris ber-OTP di server (SEC-12)
Flag klien `is_reward_line`/`reward_id` tidak dipercaya. Sebuah baris dianggap reward ber-OTP bila:
- `reward_id.program_id.efn_requires_otp` (apa pun nilai `is_reward_line`), **atau**
- `product_id` ∈ `discount_line_product_id` reward dari program ber-OTP (termasuk program diarsipkan), walau klien mengosongkan `reward_id`.

Baris refund (`refunded_orderline_id` dengan qty < 0) dikecualikan. Baris yang flag-nya dikosongkan klien dicatat di log (warning, tanpa data pribadi).

## Offline (ADR-017 opsi a)
- Reward ber-OTP disembunyikan dari daftar *Reward* saat `pos.data.network.offline`.
- Alur OTP menolak saat offline.
- Validasi pembayaran diblokir bila order membawa reward ber-OTP yang belum terverifikasi, atau POS offline dan `efn.pos.promo_disabled_when_offline` = True.
- Transaksi reguler tetap jalan.

## `order_ref`
Odoo 19 membuat `pos_reference` **di server saat order pertama kali di-sync** (`_complete_values_from_session`), sehingga belum ada saat kasir meminta OTP. Modul ini memakai **`pos.order.uuid`** (dibuat di frontend, stabil, unik) sebagai `order_ref`. Kontrak `docs/04 §1` perlu disesuaikan (BFF harus memperlakukan `order_ref` sebagai string opaque ≤ 64 karakter).

## Konfigurasi
| Parameter | Default | Arti |
|---|---|---|
| `efn.bff_url` | `https://api-gateway:3000` | Base URL BFF (efn_core); env `EFN_BFF_URL` mengalahkan. `http://` ditolak di produksi |
| `efn.pos.otp_required_for_promo` | True | **Kill switch** global; False = flag `efn_requires_otp` diabaikan (lihat peringatan di bawah) |
| `efn.pos.promo_disabled_when_offline` | True | Blokir validasi order ber-OTP saat offline |
| `efn.pos.otp_timeout_seconds` | 5 | Timeout HTTP ke BFF |
| `efn.pos.otp_retention_days` | 30 | Retensi `efn.pos.otp.challenge` (autovacuum) |

Company POS wajib punya `efn_brand_code` (dikirim sebagai `company_code`).

### Program yang boleh "Wajib OTP" (Gelombang 3b)
- Hanya `program_type` ∈ `promotion`, `buy_x_get_y`, `promo_code`, `coupons` (constraint + checkbox disembunyikan untuk jenis lain). `loyalty`/`ewallet` tidak dipakai untuk poin member (D-05/ADR-020: ledger tunggal di BFF); `gift_card`/`ewallet` adalah alat bayar; `next_order_coupons` terbit otomatis.
- Program ber-OTP yang tersedia di POS **wajib** mengisi *Point of Sale* (`pos_config_ids`) secara eksplisit — kosong berarti berlaku di semua POS termasuk kiosk.
- **Kiosk dikecualikan lewat konfigurasi standar**: jangan tautkan program ber-OTP ke config `self_ordering_mode = 'kiosk'` (ditolak constraint bila `pos_self_order` terpasang). Tidak ada logika khusus kiosk di server; gate server generik (tolak order berbayar tanpa OTP / hapus reward di draft) tetap berlaku sebagai *defense in depth* untuk semua sumber order. Batasan: constraint dicek saat program diubah; mengubah config yang sudah tertaut menjadi kiosk tidak dicegah (cek di checklist `efn_setup_pos`).

### PERINGATAN — Kill switch `efn.pos.otp_required_for_promo`
Mematikan parameter ini (False) **membuka semua promo ber-OTP tanpa verifikasi** di seluruh company — risiko fraud promo kasir (R-B06/R-S10, SEC-07). Gunakan hanya untuk insiden (mis. WhatsApp/BFF down berkepanjangan dan bisnis memutuskan promo tetap jalan), dengan persetujuan tertulis, dan kembalikan ke True sesegera mungkin. Perubahan `ir.config_parameter` `efn.*` tercatat oleh `efn_audit` bila `efn_setup_audit` terpasang (aturan `audit_rule_ir_config_parameter`). Alternatif yang lebih sempit: nonaktifkan program tertentu, bukan saklar global.

## Kunci nomor HP member (SEC-07 sisa, Gelombang 9)
**Fit-gap standar 19.0 (terverifikasi di source image `efn-odoo:ci`):** `res.partner` create/write hanya untuk grup `base.group_partner_manager` (*Contacts: Creation*); `base.group_user` baca saja. POS mengedit pelanggan lewat form standar (`point_of_sale.res_partner_action_edit_pos` → `base.view_partner_form`), jadi tunduk pada ACL itu. Standar hanya biner: kasir **tanpa** grup tsb tidak bisa membuat pelanggan baru di POS; kasir **dengan** grup tsb bisa mengubah HP siapa pun. Konfigurasi standar yang disarankan tetap: beri *Contact Creation* hanya bila onboarding pelanggan di kasir memang dibutuhkan.

**Gap yang ditutup (`models/res_partner.py`):** HP (`phone`, dan `mobile` bila ada modul yang menambahkannya) partner yang **sudah dipakai sebagai member** — punya riwayat `efn.pos.otp.challenge` (retensi 30 hari) atau `pos.order.efn_otp_gate_state` (consumed/review, permanen) — hanya boleh diubah/dikosongkan oleh `point_of_sale.group_pos_manager` atau `base.group_system` (dan OdooBot, mis. cron erasure). Kasir lain → `AccessError`. Riwayat OTP hanya mungkin untuk HP valid `^62…`, yaitu dasar `efn_member_hash`; pengecekan tidak memakai pepper agar tidak gagal-terbuka saat pepper belum terpasang.
- Perubahan oleh manajer dicatat di **chatter** partner: `Nomor HP member diubah oleh <user> (SEC-07). phone: 62812****890 → 62813****222` (mask, UU PDP) + log aplikasi tanpa nomor. Jejak audit formal: `efn_audit` (aturan res.partner dari `efn_setup_audit`; HP disimpan ter-mask, nilai lengkap tidak disimpan — UU PDP).
- Dua lapis: override `write` (cek awal + chatter) dan `@api.constrains("phone")` sebagai jaring pengaman untuk jalur yang tidak melewati override (constraint dijalankan Odoo dengan sudo, jadi keputusan memakai uid asli; penanda "sudah dicek" ada di `cr.cache`, bukan context yang bisa diisi klien RPC). **Gelombang 11 (SEC-44):** keputusan selalu dari uid asli (`env.user`) **termasuk di dalam `sudo()`** — kode server yang menulis HP member dengan `sudo()` atas nama kasir ikut ditolak (dulu lapis 1 dilewati dan id ditandai "sudah dicek" sehingga lapis 2 ikut terlewati). Penanda hanya diisi setelah pemeriksaan benar-benar dijalankan dan lolos. Proses sistem sah berjalan sebagai OdooBot (`SUPERUSER_ID`, mis. cron erasure) dan selalu lolos tanpa catatan chatter; kode kustom yang perlu mengubah HP member atas nama sistem wajib `with_user(SUPERUSER_ID)` eksplisit. Sejak ADR-031 tidak ada lagi monkeypatch registry OCA auditlog, sehingga override `write` selalu aktif tanpa perlu restart setelah install/upgrade; hook `efn_audit` di model `base` berada di dasar MRO dan melihat `vals` final.
- Batasan: partner yang belum pernah dipakai OTP tidak dikunci (kasir bisa mengganti HP pelanggan baru sebelum OTP pertama); membuat partner baru dengan HP orang lain tetap mungkin, tetapi OTP terkirim ke pemilik nomor. `pos_hr` (login karyawan per PIN) tidak mengubah uid sesi, jadi "manajer" = user Odoo sesi POS, bukan karyawan `pos_hr`.

## Data pribadi
Nomor HP mentah tidak disimpan di modul ini (nomor sumber tetap di `res.partner`; challenge hanya menyimpan mask `62812****890` + referensi `partner_id`); token dihapus setelah consume/kedaluwarsa; challenge dihapus setelah 30 hari. Log hanya memuat nomor ter-mask.

## Uji manual
1. Set `efn_brand_code` company, buat program Promotion dengan *Wajib OTP (EFN)* aktif, buka sesi POS. Siapkan pelanggan dengan HP `08xx…`, pelanggan tanpa HP, dan pelanggan bernomor `+65…`.
2. Tambah produk tanpa pelanggan → *Reward* → pilih promo → POS meminta memilih pelanggan → pilih pelanggan ber-HP → kode OTP dari WA (atau console sender BFF di dev) → reward diterapkan → bayar → cek `efn.pos.otp.challenge` state `consumed`, `partner_id` terisi.
3. Ulangi dengan pelanggan tanpa HP / nomor asing → pesan jelas, tidak ada OTP terkirim.
4. Verifikasi OTP untuk pelanggan A, ganti pelanggan ke B, bayar → order masuk *Review OTP Promo* dengan catatan `member_mismatch` (BFF v2).
5. Matikan jaringan (DevTools offline) → promo ber-OTP hilang dari daftar Reward.
6. Matikan BFF / pasang sertifikat BFF yang tidak ditandatangani CA internal → "Layanan OTP tidak tersedia", transaksi tanpa promo tetap bisa.

## Test otomatis
`tests/test_partner_phone_lock.py` (SEC-07): kasir tidak bisa mengubah/mengosongkan HP member ber-riwayat OTP (lapis 1 & 2), boleh mengubah field lain, HP non-member, dan membuat pelanggan baru; riwayat order consumed juga mengunci; perubahan oleh manajer tercatat di chatter (ter-mask) dan `efn.audit.log` (ter-mask, user manajer), penolakan kasir tidak menambah log; OdooBot/cron erasure tidak dikunci; constraint memakai uid asli dan tidak bisa dilewati lewat context. SEC-44: `with_user(kasir).sudo().write(...)` ditolak (lapis 1) dan `models.Model.write` dengan sudo + uid kasir ditolak (lapis 2), penanda tidak tertinggal; manajer via sudo boleh + tercatat di chatter; OdooBot tanpa/ dengan sudo boleh tanpa chatter.
`tests/test_otp_gate.py` (TestPoSCommon, tag `efn`), BFF di-mock: request/verify sukses + consume (member_ref dari partner, ikut di consume); request tanpa partner / partner_id bukan integer / partner tanpa HP / nomor asing / BFF `invalid_phone`; consume `member_mismatch` dan `amount_mismatch` (reason & objek error) → review, payload consume memuat `amount_total`; order final tanpa partner → review tanpa consume; SEC-12: flag reward dikosongkan klien tanpa OTP ditolak, dengan OTP di-consume, di draft dibuang, program diarsipkan tetap di-gate; OTP salah, OTP terkunci, timeout request/verify, `bff_insecure_url`, order tanpa OTP ditolak, flag browser palsu ditolak, replay (`already_used`) → review, timeout/TLS error consume → review, token order lain tidak bisa dipakai, draft dibuang reward-nya, program non-OTP tidak terpengaruh, saklar global off.
`tests/test_pos_otp_tour.py` (HttpCase + tour Chrome headless, tag `efn_tour`, job CI `odoo-tour`, image target `test`; tour di `static/tests/tours/efn_otp_gate_tour.js`, BFF di-mock server-side): (1) reward ber-OTP tanpa pelanggan -> kasir diminta memilih pelanggan -> popup OTP (nomor ter-mask) -> kode salah (notifikasi, tanpa reward) -> kode benar -> reward terpasang -> bayar -> order `consumed` (payload request/consume diperiksa); (2) tanpa pelanggan dan daftar pelanggan ditutup -> tidak ada panggilan BFF, tanpa reward, order tetap bisa dibayar; (3) offline -> tombol Reward tidak menawarkan reward ber-OTP; reward terverifikasi lalu offline -> validasi diblok dialog; online lagi -> validasi -> `consumed`.

## SEC-58 (Gelombang 13c, versi 19.0.1.7.0)
- Panggilan BFF lewat `efn.bff.client._efn_post` (privat, tidak bisa dipanggil RPC).
- `_efn_check_otp_caller` (dipakai `efn_otp_request/verify` dan RPC `efn_loyalty_*`) kini juga memeriksa `pos.config.check_access('read')` (record rule multi-company) dan company config ∈ company user, karena `call_kw` hanya mem-`browse` id tanpa record rule: kasir PT lain tidak bisa memakai sesi terbuka PT ini. Metode publik tercatat di `RPC_ALLOWLIST` (`efn_core/tests/test_rpc_surface.py`).

## Batasan
- Tour UI (Gelombang 12) memakai Odoo + Chrome headless dengan BFF di-mock server-side; frontend belum diuji terhadap BFF/WhatsApp nyata, belum di perangkat kasir (tablet/mobile, `isActive: ["mobile"]` tidak dijalankan), dan belum dengan `efn_setup_*` terpasang (fixture `TestPointOfSaleHttpCommon` tidak cocok dengan 4 company efn_setup_company).
- `tests/test_pos_otp_tour.py` mengamati: util offline Odoo memicu event `offline` sebelum fetch/XHR diblok, sehingga tour memanggil ulang `checkConnectivity()`; perilaku nyata (ping gagal) tidak terpengaruh.
- Image `test` memakai Google Chrome stable saat build (repo Google tidak menyimpan versi lama) -> image test tidak reprodusibel; tidak pernah di-deploy.
- `UserError` pada sync membatalkan **seluruh batch** `sync_from_ui`, bukan hanya order bermasalah (hanya terjadi untuk klien yang dimodifikasi).
- Saat reward draft dihapus server, total draft di server tidak dihitung ulang (frontend mengirim ulang saat sync berikutnya).
- Bila BFF menjawab 200 untuk consume tetapi commit DB Odoo gagal setelahnya, token sudah terpakai di BFF; sync ulang akan berakhir `review`. Usulan ke BFF: consume idempotent untuk pasangan (`jti`, `order_ref`) yang sama.
- Kiosk/self-order (`pos_self_order`): dicegah lewat `pos_config_ids` program (lihat atas). Bila tetap ada order self-order yang membawa reward ber-OTP (salah konfigurasi), gate server generik menolak/menghapusnya.
- SEC-07 sisa: sejak Gelombang 5 consume membawa `amount_total`; perbandingan dengan nominal saat OTP (toleransi `POS_OTP_AMOUNT_TOLERANCE_PCT`, default 20%) dilakukan **BFF** (`409 amount_mismatch` → review). Kasir yang juga boleh mengedit `res.partner.phone` masih bisa mengarahkan OTP ke nomornya — batasi hak edit kontak untuk grup kasir (rekomendasi ke efn-odoo-functional) dan laporan anomali "nomor sama di banyak order" (BFF).
- SEC-12 sisa: baris harga negatif manual **tanpa** produk diskon program (mis. diskon kasir) tidak terdeteksi modul ini; mitigasinya konfigurasi POS "Restrict price modification" + laporan harian (efn-odoo-functional).
