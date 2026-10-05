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
Mematikan parameter ini (False) **membuka semua promo ber-OTP tanpa verifikasi** di seluruh company — risiko fraud promo kasir (R-B06/R-S10, SEC-07). Gunakan hanya untuk insiden (mis. WhatsApp/BFF down berkepanjangan dan bisnis memutuskan promo tetap jalan), dengan persetujuan tertulis, dan kembalikan ke True sesegera mungkin. Perubahan `ir.config_parameter` belum tercatat otomatis oleh modul ini — aktifkan aturan audit (`efn_setup_audit`/auditlog) untuk model `ir.config_parameter`. Alternatif yang lebih sempit: nonaktifkan program tertentu, bukan saklar global.

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
`tests/test_otp_gate.py` (TestPoSCommon, tag `efn`), BFF di-mock: request/verify sukses + consume (member_ref dari partner, ikut di consume); request tanpa partner / partner_id bukan integer / partner tanpa HP / nomor asing / BFF `invalid_phone`; consume `member_mismatch` dan `amount_mismatch` (reason & objek error) → review, payload consume memuat `amount_total`; order final tanpa partner → review tanpa consume; SEC-12: flag reward dikosongkan klien tanpa OTP ditolak, dengan OTP di-consume, di draft dibuang, program diarsipkan tetap di-gate; OTP salah, OTP terkunci, timeout request/verify, `bff_insecure_url`, order tanpa OTP ditolak, flag browser palsu ditolak, replay (`already_used`) → review, timeout/TLS error consume → review, token order lain tidak bisa dipakai, draft dibuang reward-nya, program non-OTP tidak terpengaruh, saklar global off.

## Batasan
- Belum pernah di-install/dijalankan (tidak ada Odoo lokal). Tidak ada tour/QUnit untuk frontend.
- `UserError` pada sync membatalkan **seluruh batch** `sync_from_ui`, bukan hanya order bermasalah (hanya terjadi untuk klien yang dimodifikasi).
- Saat reward draft dihapus server, total draft di server tidak dihitung ulang (frontend mengirim ulang saat sync berikutnya).
- Bila BFF menjawab 200 untuk consume tetapi commit DB Odoo gagal setelahnya, token sudah terpakai di BFF; sync ulang akan berakhir `review`. Usulan ke BFF: consume idempotent untuk pasangan (`jti`, `order_ref`) yang sama.
- Kiosk/self-order (`pos_self_order`): dicegah lewat `pos_config_ids` program (lihat atas). Bila tetap ada order self-order yang membawa reward ber-OTP (salah konfigurasi), gate server generik menolak/menghapusnya.
- SEC-07 sisa: sejak Gelombang 5 consume membawa `amount_total`; perbandingan dengan nominal saat OTP (toleransi `POS_OTP_AMOUNT_TOLERANCE_PCT`, default 20%) dilakukan **BFF** (`409 amount_mismatch` → review). Kasir yang juga boleh mengedit `res.partner.phone` masih bisa mengarahkan OTP ke nomornya — batasi hak edit kontak untuk grup kasir (rekomendasi ke efn-odoo-functional) dan laporan anomali "nomor sama di banyak order" (BFF).
- SEC-12 sisa: baris harga negatif manual **tanpa** produk diskon program (mis. diskon kasir) tidak terdeteksi modul ini; mitigasinya konfigurasi POS "Restrict price modification" + laporan harian (efn-odoo-functional).
