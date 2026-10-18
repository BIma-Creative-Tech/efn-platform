# Security Review (r12): Gelombang 19, reservasi stok, promo online, kanal per brand, PoC enkripsi PII Odoo (2026-10-18)

Reviewer: `efn-security-reviewer` (read-only terhadap kode). Lanjutan `SECURITY_REVIEW_2026-10-12_r11.md` (SEC tertinggi sebelumnya: SEC-129).

Cakupan: branch `chore/wave-19`, commit `ad4dbd1` (basis `main` `05557d0`).
Spesifikasi: `docs/04_SERVICE_CONTRACTS.md` §11.1–§11.7 (L-27..L-39), laporan G19 di `docs/agent-reports/{efn-odoo-engineer,efn-bff-engineer,efn-frontend-engineer,efn-devsecops-engineer}.md`.

File yang dibaca:
- Odoo:
  - `efn_online_order/models/{efn_online_channel,efn_online_order,efn_online_common,loyalty_program,sale_order}.py`, `hooks.py`, `migrations/19.0.1.2.0/post-migrate.py`;
  - `efn_setup_online/{demo/efn_online_demo.xml,data/efn_online_setup_data.xml,hooks.py}` (grep);
  - `efn_pii/{__manifest__.py,models/keyring.py,models/fields_pii.py,models/res_partner.py,tests/data/efn_pii_test_vectors.json}`, `efn_pii_base_vat/{__manifest__.py,models/res_partner.py}`;
  - `efn_core/tests/test_rpc_surface.py` (grep).
- BFF: `cart/{cart.controller,quote.service}.ts`, `odoo-rpc/{promo-codes,odoo-online.parse}.ts`, `catalog/{catalog.controller,online-http}.ts`, `config/{app-config.ts:1480-1615, online-config.spec.ts}`, `orders/orders.service.ts` (grep), `payments/payments.service.ts:313-345`, `common/net/client-ip.ts`, `common/testing/fakes.ts:244-285`, `infra/postgres/init/07-platform-online.sql` (grep).
- Frontend: `app/api/cart/quote/route.ts`, `app/api/catalog/channels/route.ts`, `lib/bff.ts`, `components/online/CartView.tsx` (promo).
- Infra/host: `scripts/host/efn-rotate-online-key:95-320`, `scripts/deploy.sh` (overlay & kanal), `scripts/check-encryption.sh:157-171`, `infra/compose/docker-compose.dev.yml:80-95,208-209`, `infra/compose/docker-compose.online-*.yml` (grep), `.github/workflows/ci.yml:255-300`.

Metode: hanya membaca kode dan konfigurasi. Tidak ada yang dijalankan. Perilaku inti Odoo CE 19 (`sale_loyalty`, `res.partner`) yang source-nya tidak ada di repo ditandai **PLAUSIBLE**.

## Ringkasan eksekutif (untuk manajemen)

1. **Tidak ada temuan Critical/High.** Kontrol utama G19 sudah benar: Odoo satu-satunya penghitung promo, baris reward tidak pernah diterima dari klien, kode promo tidak membedakan penyebab penolakan, kupon sekali pakai aman terhadap pemakaian ganda, dan kunci PII Odoo hanya untuk container Odoo di dev.
2. **Dua Medium di jalur online, keduanya memblokir aktivasi fitur, bukan merge:** kuota promo (`limit_usage`) bisa terlampaui saat dua checkout berjalan bersamaan, karena perbaikan lead (`invalidate_model`) tidak menyegarkan snapshot database (**SEC-130**). Reservasi stok 1 jam bisa dipakai untuk "menimbun" stok satu outlet dengan beberapa akun (**SEC-131**): batas 99 per baris × 50 baris × 2 order per member.
3. **Dua Medium di PoC enkripsi PII (ADR-036), memblokir E-38 penuh di G20, bukan merge:** NPWP partner terlindungi tersalin plaintext ke kontak anak dan jalur standar lain (**SEC-136**). Deteksi R-T18 hanya mencatat log, tidak menolak tulis (**SEC-137**).
4. **Satu Low perlu dibereskan sebelum merge (SEC-133):** pemeriksaan "env literal" BFF ikut menolak variabel `ODOO_API_KEY_ONLINE_*_FILE`. Bila terkonfirmasi, BFF tidak bisa start di staging/produksi. Sifatnya fail-closed (bukan kebocoran), tetapi memblokir deploy.
5. **Rekomendasi: GO bersyarat untuk merge G19** dengan online tetap OFF di produksi, setelah SEC-133 diperbaiki atau dibuktikan false positive dengan satu test.

## Temuan baru

| ID | Severity | Verdict | Judul | Lokasi utama | Risiko | Memblokir |
|---|---|---|---|---|---|---|
| SEC-130 | **Medium** | PLAUSIBLE (kunci & hitungan `sale_loyalty` CE 19); semantik PostgreSQL CONFIRMED | Kuota `limit_usage` promo terlampaui pada place paralel. `invalidate_model` hanya membuang cache ORM, bukan snapshot REPEATABLE READ | `efn_online_channel.py:741-747`; `efn_online_order.py:400-401,409-415` | R-B06, R-S10 | Aktivasi promo berkuota |
| SEC-131 | **Medium** | CONFIRMED (konfigurasi); dampak PLAUSIBLE | *Stock griefing*: reservasi 60–70 menit tanpa batas qty per SKU per member; `max_order_amount` 0 = tanpa batas | `efn_online_channel.py:144-160`; `app-config.ts:1339`; `cart.service.ts:12`; `efn_online_order.py:409-417` | R-B06 | Aktivasi mode `confirm` di kanal publik |
| SEC-132 | Low | CONFIRMED | Tag kode promo di log: Odoo level INFO (kontrak: debug), SHA-256 tanpa kunci 32 bit. Kode kupon Odoo (~32 bit) dan kode promo kamus bisa dipulihkan pembaca log | `efn_online_channel.py:833-840`; `efn_online_common.py:134-136`; `quote.service.ts:146-151`; `promo-codes.ts:38-40` | R-S10, R-B06 | Go-live online |
| SEC-133 | Low (ketersediaan) | CONFIRMED dari pembacaan kode, belum dieksekusi | Regex env literal `^ODOO_API_KEY_ONLINE_[A-Z0-9_]+$` juga cocok dengan `…_FILE`. Di luar `APP_ENV=dev`, BFF menolak start bila compose men-set `ODOO_API_KEY_ONLINE_RTL_FILE` (base compose selalu men-set-nya) | `app-config.ts:1544-1550`; `docker-compose.yml:1157-1159` | R-T15 | **Merge** (deploy) |
| SEC-134 | Low | CONFIRMED | Rate limit promo dihitung per quote, bukan per kode. 3 kode per quote = 30 kode/15 menit/member, 90/15 menit/IP | `cart.controller.ts:72-78`; `app-config.ts:1600-1601` | R-B06 | Go-live online |
| SEC-135 | Info | PLAUSIBLE | Perbedaan waktu respons: kode tak dikenal/brand lain keluar cepat; kode yang ada tetapi kedaluwarsa/kuota habis melewati `_try_apply_code` di savepoint | `efn_online_channel.py:702-719,738-740` | R-B06 | – |
| SEC-136 | **Medium** | PLAUSIBLE (sinkronisasi *commercial fields* inti) | PoC PII: NPWP partner terlindungi tersalin plaintext ke kontak anak (`_commercial_fields` = `vat`), wizard merge, partner baru dari email masuk; riwayat tracking sebelum penandaan tetap plaintext | `efn_pii/models/res_partner.py:147-192` (tanpa propagasi penanda) | R-L01, R-T18 | E-38 penuh |
| SEC-137 | **Medium** | CONFIRMED | PoC PII: R-T18 hanya dideteksi dengan log CRITICAL. Tulis tetap berjalan plaintext bila modul lain mendefinisikan ulang field. Nilai plaintext pada partner terlindungi diterima saat baca tanpa alarm | `efn_pii/models/res_partner.py:75-83`; `fields_pii.py:126-129` | R-T18, R-L01 | E-38 penuh |
| SEC-138 | Low | CONFIRMED | PoC PII: AAD tidak mengikat id record. Ciphertext bisa dipindah antar-partner (pemegang akses tulis DB) tanpa gagal autentikasi | `keyring.py:83-87`; `fields_pii.py:123` | R-L01 | E-38 penuh |
| SEC-139 | Low | PLAUSIBLE (perilaku saga BFF) | Mode `confirm`: SO sudah `sale` sebelum dibayar. Staf Sales/Accounting bisa membuat faktur. Bila kemudian kedaluwarsa, pembatalan membuat refund `cancel` atas order yang tidak pernah dibayar | `efn_online_order.py:409-411,656-674`; `sale_order.py:64-74` (hanya `state` dijaga) | R-S10, R-O05 | Aktivasi mode `confirm` |
| SEC-140 | Info | CONFIRMED | Blind index bukan oracle tambahan (pencari sudah bisa membaca nilai lewat ORM). Sisa: kesetaraan nilai bocor di dump DB, dan kunci enkripsi + kunci blind index berada di container yang sama | `fields_pii.py:159-207`; `docker-compose.dev.yml:91-94` | R-L01 | – |
| SEC-141 | Info | CONFIRMED | Deskripsi `RPC_ALLOWLIST` `efn_online_quote` masih "new() tanpa tulis DB". Quote berpromo kini membuat SO di savepoint (nomor urut terpakai, kunci baris program) | `efn_core/tests/test_rpc_surface.py:82` | – | – |

**Rekap: 0 Critical · 0 High · 4 Medium · 5 Low · 3 Info.** Hanya SEC-133 yang memblokir merge.

Status temuan lama (SEC-125..129) tidak diperiksa ulang di review ini dan tidak berubah sejak re-review r11.

---

## Detail temuan

### SEC-130: Medium: Kuota promo terlampaui pada checkout paralel

**Fakta (kode efn, CONFIRMED):**
- `_efn_online_try_code` memanggil `invalidate_model(["order_count", "total_order_count"])` lalu `_try_apply_code` (`efn_online_channel.py:743-747`).
- Docstring `:735-737` mengandalkan `FOR UPDATE NOWAIT` program dan retry Odoo agar "hasil kupon sekali pakai tetap benar".
- Place menerapkan kode lagi (`efn_online_order.py:400-401`), lalu `action_confirm` (`:411`). Tidak ada `UPDATE` atas baris `loyalty_program` di kode efn.

**Semantik PostgreSQL (CONFIRMED, pola SEC-127):**
- Odoo memakai REPEATABLE READ. Snapshot terbentuk pada query pertama request, yaitu di `_efn_online_check_caller`, jauh sebelum kode diterapkan.
- `SELECT … FOR UPDATE [NOWAIT]` atas baris yang hanya **dikunci** (tidak di-UPDATE) oleh transaksi lain yang sudah commit tidak memicu serialization failure.
- `invalidate_model` membuang cache ORM, sehingga compute dijalankan ulang, tetapi query compute membaca **snapshot yang sama**.

**Asumsi (PLAUSIBLE):**
- `total_order_count` dihitung dari baris SO non-batal (`read_group`), bukan dari kolom tersimpan.
- `action_confirm` tidak meng-UPDATE baris program.

**Skenario:**
1. Promo "100 pembeli pertama" (`limit_usage`, `max_usage = 100`), sudah terpakai 99.
2. Member A dan B menekan bayar hampir bersamaan.
3. Transaksi B membuka snapshot. Transaksi A mengunci program, menerapkan kode, konfirmasi, lalu commit.
4. B baru tiba di `_try_apply_code`. NOWAIT berhasil karena kunci A sudah lepas, dan B menghitung 99 dari snapshot lamanya. Kode diterima dan SO tercadang.
5. Hasil: 101 pemakaian.

Lonjakan wajar saat promo dibuka sudah cukup untuk memicu ini, tanpa niat jahat. Penyerang dengan banyak akun (R-B06) bisa memperbesar kelebihan.

Kupon sekali pakai (`coupons`) **tidak** terdampak. Poin kartu di-UPDATE saat konfirmasi, sehingga transaksi kedua gagal serialisasi, diulang, lalu menjadi `price_changed` (PLAUSIBLE; test lead `kupon sekali pakai: place kedua price_changed` hanya berurutan, bukan paralel).

**Efek samping (PLAUSIBLE):** kunci program juga diambil quote. Banyak quote serentak dengan kode populer dapat membuat place lain gagal `LockNotAvailable` sampai batas retry Odoo, sehingga BFF menjawab 503 untuk seluruh checkout.

**Rekomendasi:**
- Di jalur **place** saja, sebelum `_efn_online_apply_promotions`, jalankan `UPDATE loyalty_program SET write_date = write_date WHERE id IN %s` untuk program ber-`limit_usage` yang ikut berlaku. Cakupannya program dari kode yang di-resolve plus program otomatis.
- Dengan begitu transaksi kedua mendapat `SerializationFailure`. Odoo mengulang request dengan snapshot baru, dan place menjadi `price_changed`. Hapus klaim di docstring `:735-737` dan komentar `:743-745`.
- Uji dengan dua koneksi nyata (skrip staging), bukan test berurutan.
- Sampai diperbaiki: jangan pakai `limit_usage` untuk promo online. Catat di checklist Marketing.

### SEC-131: Medium: Penimbunan stok lewat reservasi 1 jam

**Fakta:**
- Mode `confirm` mencadangkan stok sejak place selama `payment_timeout_minutes` (default 70; BFF 3600 detik).
- Batas per order: `max_lines` 50, `max_qty_per_line` 99 (`efn_online_channel.py:158-159`), dan `max_order_amount` 0 yang berarti tanpa batas (`:160`).
- BFF: `ONLINE_MAX_OPEN_ORDERS_PER_MEMBER` 2 (`app-config.ts:1339`), `QTY_HARD_MAX` 999 (`cart.service.ts:12`), `CHECKOUT` 5/600 detik. `captcha_required` belum aktif (§11.1).
- Tidak ada batas qty tercadang per SKU per member atau per outlet.

**Skenario:**
1. Pesaing atau pembeli ulang memakai 5 nomor HP (OTP + ALTCHA lolos).
2. Setiap akun membuat 2 order × 99 unit produk laris di outlet pilot dan tidak membayar.
3. Selama 60–70 menit, 990 unit tidak bisa dibeli online (`out_of_stock`).
4. Setelah kedaluwarsa, siklus diulang. Rate limit checkout 5/10 menit tidak menghalangi.
5. Penjualan POS tetap jalan karena POS bisa mencabut reservasi (L-27). Kanal online yang mati. Saat promo bersamaan, stok promo habis bagi pelanggan sah.

**Rekomendasi:**
- Batas qty per SKU storable per order di mode `confirm`, misalnya `max_qty_per_line` kanal ritel 10–20 lewat checklist atau data setup. Isi `max_order_amount` kanal produksi.
- Batas akumulatif di BFF: Σ qty tercadang per member per produk lintas order terbuka.
- Metrik/alert: rasio order `placed` yang kedaluwarsa per member/IP, dan Σ qty tercadang per outlet. Penolakan `429 too_many_open_orders` sudah ada.
- Pertimbangkan masa cadang pendek (15 menit) yang diperpanjang saat QR dibuat. Aktifkan `captcha_required` bila metrik naik.

### SEC-132: Low: Tag kode promo di log dapat dibalik

**Fakta:**
- Odoo mencatat `refs=` untuk **semua** kode yang dicoba di level **INFO** (`efn_online_channel.py:833-840`). L-36 hanya mengizinkan level debug.
- Tag = 8 heks pertama SHA-256 **tanpa kunci** (`efn_online_common.py:134-136`; BFF `promo-codes.ts:38-40`, log debug `quote.service.ts:150`). Laporan BFF mencatat level debug ikut tercetak di produksi (`main.ts` tanpa filter level).
- Kode kupon `coupons` Odoo berentropi sekitar 32 bit (prefiks tetap + 8 heks dari uuid4; PLAUSIBLE). Tag 32 bit hampir unik di ruang itu, dan 2^32 SHA-256 bisa dihitung dalam hitungan menit dengan CPU biasa.

**Skenario:** pembaca Loki/Grafana (ops, kontraktor) mengambil tag kupon dari log quote yang belum di-place (pelanggan batal atau L-35). Ia menghitung ulang ruang kode, mendapat kupon yang masih sah, lalu memakainya. Kode promo yang dipilih manusia (`GL10`, `RAMADAN50`) langsung terbalik dengan kamus.

**Rekomendasi:**
- Ganti SHA-256 dengan HMAC-SHA256 berkunci secret (pepper `efn:redis_key:v1` yang sudah ada di BFF, dan secret terpisah di Odoo) atau hapus tag.
- Turunkan log Odoo ke `debug`.
- Batasi level log Nest di produksi (`logger: ['log','warn','error']`).

### SEC-133: Low (ketersediaan): BFF menolak `*_FILE` sebagai "env literal"

**Fakta:**
- `onlineStartupProblems` (`app-config.ts:1544-1550`) memeriksa setiap env yang cocok dengan `/^ODOO_API_KEY_ONLINE_[A-Z0-9_]+$/` dan bernilai, di luar `APP_ENV=dev`. Pola itu juga cocok dengan `ODOO_API_KEY_ONLINE_RTL_FILE`.
- Base compose selalu men-set `ODOO_API_KEY_ONLINE_RTL_FILE` (`docker-compose.yml:1159`) dan overlay brand men-set `…_RTL_GL_FILE`, dst.
- `assertStartupSecrets` melempar bila ada masalah (`:204-205`), terlepas dari `ONLINE_ENABLED`.
- Tidak ada test yang membuktikan start sukses di luar dev dengan variabel `_FILE` ini. `fakes.testConfig` tidak men-set-nya, dan `online-observability.spec.ts:92` justru mengharapkan error.

**Dampak:** BFF staging/produksi gagal start. Deploy rollback (fail-closed), dan seluruh Super-App/POS-BFF tidak bisa dirilis.

**Rekomendasi:**
- Kecualikan akhiran `_FILE`, misalnya `/^ODOO_API_KEY_ONLINE_[A-Z0-9_]+$/.test(name) && !name.endsWith('_FILE')`.
- Tambahkan test: `APP_ENV=production` + `ODOO_API_KEY_ONLINE_RTL_FILE` + `ODOO_API_KEY_ONLINE_RTL_GL_FILE` → `assertStartupSecrets()` tidak melempar.
- Bila lead membuktikan ini false positive lewat run container non-dev, tutup temuan.

### SEC-134: Low: Rate limit promo per quote, bukan per kode

**Fakta:** `PROMO_MEMBER`/`PROMO_IP` dikonsumsi satu kali per quote (`cart.controller.ts:74-77`), padahal satu quote membawa hingga 3 kode. Kontrak §11.2 no. 6 menulis "10 / 15 menit / member", tanpa menyebut per kode.

**Dampak:** satu akun bisa menebak 30 kode per 15 menit, dan satu IP 90 kode. Dengan banyak akun (R-B06), kode promo berentropi rendah untuk kalangan terbatas (karyawan/VIP) bisa ditemukan.

**Rekomendasi:**
- Konsumsi kuota sebanyak jumlah kode **baru**, yaitu yang belum ada di `promo_codes` quote terakhir member (`RateLimiter` sudah menerima `amount`).
- Kebijakan Marketing: kode untuk kalangan terbatas minimal 8 karakter acak.

### SEC-135: Info: Perbedaan waktu antar-penyebab penolakan kode

Kode yang tidak cocok dengan program kanal (tidak dikenal atau milik brand lain) keluar setelah dua `search` (`efn_online_channel.py:702-719,738-740`). Kode yang ada tetapi kedaluwarsa/kuota habis melewati savepoint + `_try_apply_code`. Respons identik (L-28), tetapi selisih waktu beberapa milidetik secara teori membedakan "pernah ada" dari "tidak pernah ada".

Biaya pembuatan SO di savepoint dan jitter jaringan menutupi sebagian besar selisih ini, dan rate limit membatasi pengukuran. Tidak perlu tindakan sekarang. Catat bila kelak ada kode berhadiah besar.

### SEC-136: Medium (blokir E-38 penuh): NPWP/kontak terlindungi tersalin plaintext lewat jalur standar

**Fakta:**
- Penanda `efn_pii_protected` hanya di-set admin per partner dan tidak dipropagasi ke anak. `copy_data` hanya untuk duplikat (`res_partner.py:158-167`).
- Enkripsi bergantung pada penanda record yang **ditulis** (`fields_pii.py:117-118`).

**Jalur penyalinan (PLAUSIBLE, perilaku inti CE):**
- `vat` termasuk `_commercial_fields`. Saat kontak anak dibuat/diubah di bawah perusahaan terlindungi (`_commercial_sync_from_company`), atau saat NPWP induk diubah (`_commercial_sync_to_children`), ORM membaca NPWP induk (terdekripsi) lalu menulisnya ke anak. Anak tidak terlindungi, sehingga kolom `vat` anak berisi plaintext.
- Wizard *merge contact* menulis nilai sumber ke tujuan yang tidak terlindungi.
- Partner baru dari email masuk: `email_normalized` partner terlindungi dikosongkan, sehingga pencarian `mail` tidak menemukan pemiliknya dan bisa membuat partner baru dengan email plaintext.
- Riwayat `mail.tracking.value` dan `mail.message.email_from` dari sebelum partner ditandai tetap plaintext. `_mail_track` hanya mencegah pencatatan baru.

**Rekomendasi (E-38 penuh):**
- Propagasi penanda ke `child_ids` dan ke partner yang menerima nilai dari partner terlindungi, di `create`/`write`/`_commercial_sync_*`. Atau tolak sinkronisasi `vat` ke anak yang tidak terlindungi.
- Saat penandaan, redaksi tracking value lama untuk field PII.
- Test: buat anak di bawah perusahaan terlindungi, lalu SQL mentah kolom `vat` anak = `efnpii1:`. Lakukan hal yang sama untuk merge.

### SEC-137: Medium (blokir E-38 penuh): R-T18 tidak fail-closed

**Fakta:**
- `_register_hook` hanya mencatat `CRITICAL` bila `phone/email/vat` bukan `EfnPiiChar` (`res_partner.py:75-83`). Tulis tetap diproses oleh `fields.Char` biasa, sehingga plaintext masuk ke partner terlindungi.
- `convert_to_record` mengembalikan nilai bukan-ciphertext apa adanya, tanpa membedakan partner terlindungi (`fields_pii.py:126-129`).
- L-39 sudah membuktikan R-T18 nyata (`base_vat`). Modul penghubung menutup satu kasus, tetapi modul berikutnya (lokalisasi, `partner_autocomplete`, dsb.) bisa mengulanginya.

**Rekomendasi:**
- Di `create`/`write` `res.partner` (metode tetap milik `efn_pii` walau field diganti), tolak tulis field PII untuk partner terlindungi bila `self._fields[f]` bukan `EfnPiiChar`.
- Saat baca partner terlindungi, nilai non-kosong tanpa penanda dicatat CRITICAL dengan kode saja (deteksi regresi/penyusupan).
- Gerbang CI L-39 (d): run gabungan `efn_pii` + semua modul dengan pemeriksaan SQL mentah.

### SEC-138: Low (blokir E-38 penuh): AAD tanpa id record

AAD = `efn:odoo:<tabel>.<kolom>:v1` (`keyring.py:83-87`). Pemegang akses tulis DB, termasuk restore backup yang dimanipulasi, bisa memindahkan ciphertext HP korban ke partner lain atau menukar dua HP. Dekripsi tetap sah, sehingga OTP/poin mengikuti nomor yang dipindah. Plaintext juga diterima saat baca (SEC-137), jadi enkripsi saat ini tidak memberi integritas.

Rekomendasi: tambahkan id record ke AAD (`…:<id>:v1`), diputuskan di ADR-036 karena `create` belum tahu id. Opsinya enkripsi di flush pasca-insert, atau AAD id untuk UPDATE saja dengan migrasi. Bila tidak dipilih, catat sebagai risiko yang diterima.

### SEC-139: Low: SO online ber-status `sale` sebelum dibayar bisa difakturkan staf

**Fakta:**
- Mode `confirm` mengonfirmasi SO saat place (`efn_online_order.py:409-411`).
- Guard SEC-104 hanya menjaga `state` (`sale_order.py:64-74`), dan pengeluaran barang dijaga SEC-105. Pembuatan faktur tidak dijaga (grep `_create_invoices`: hanya jalur internal `:593`).

**Skenario (PLAUSIBLE, sisi saga BFF):**
1. Staf Sales menekan *Create Invoice* dan mem-posting faktur untuk order yang belum dibayar.
2. Order kedaluwarsa. `_efn_do_cancel` menemukan faktur ter-posting dan membuat `efn.online.refund` (`origin=cancel`) beserta event `cancelled` + refund (`:660-674`) untuk order yang tidak pernah dibayar.
3. Dampaknya bergantung pada apakah BFF menolak refund tanpa pembayaran. Varian lain: dibayar → `nothing_to_invoice` → `review`.

**Rekomendasi:**
- Tolak `_create_invoices` untuk SO online di luar `internal_scope` (pola SEC-104).
- Di `_efn_do_cancel`, jangan buat refund bila `paid_idempotency_key` kosong.

### SEC-140: Info: Blind index dan lokasi kunci

- Pencarian `=`/`in` memakai blind index HMAC-SHA256 penuh (tanpa pemotongan), dengan subkey HKDF per field. Pencari sudah bisa membaca HP lewat ORM dalam batas record rule, sehingga ini **bukan oracle tambahan**. Record rule tetap berlaku pada traversal domain.
- Sisa risiko yang wajar dicatat di ADR-036:
  - (a) dump DB membocorkan partner mana yang ber-HP/NPWP sama;
  - (b) `odoo_pii_key_v*` dan `odoo_pii_bidx_key` di-mount ke container yang sama, sehingga kompromi container Odoo membuka keduanya (setara Odoo membaca plaintext);
  - (c) ruang HP Indonesia kecil, sehingga kebocoran kunci blind index memungkinkan brute force. Rotasi kunci bidx perlu prosedur reindex.

### SEC-141: Info: Deskripsi permukaan RPC usang

`test_rpc_surface.py:82` menulis `efn_online_quote` "baca, new() tanpa tulis DB". Sejak G19, quote berpromo membuat SO nyata di savepoint: nomor urut SO terpakai, kunci baris program (SEC-130), dan kartu loyalty di-rollback.

Perbarui deskripsinya agar reviewer berikutnya menilai efek samping dengan benar.

---

## Yang sudah benar (kontrol yang ada)

**Promo online (E-36)**
- Program yang boleh berlaku difilter dua lapis:
  - constraint `loyalty.program` (`loyalty_program.py:32-72`): tipe online saja, company wajib, `pricelist_ids` wajib dan ⊆ pricelist kanal company itu, bukan `efn_requires_otp`, bukan `portal_visible`;
  - filter runtime per kanal (`efn_online_channel.py:675-690`), sehingga perubahan pricelist kanal tidak membocorkan program.
- Default `sale_ok=False`, plus hook/migrasi yang mematikan program lama yang melanggar.
- Kode di-resolve hanya terhadap program kanal (`=ilike` dengan escape wildcard). Hasil ambigu ditolak. Kupon/program lain hasil `_try_apply_code` ditolak (`:752-753`). Semua galat menjadi `promo_rejected` tanpa pesan Odoo.
- Baris reward tidak pernah dari klien:
  - cart BFF hanya berisi produk katalog terpublikasi;
  - place mengirim `placeLines` tanpa reward dan `promo_applied` dari Redis, bukan dari klien;
  - Odoo menghitung ulang dan mencocokkan `quote_hash` (memuat `reward_id` + kode urut);
  - parser BFF menolak reward > 0, lebih dari 20 baris, dan kode di luar permintaan (fail-closed).
- L-35: reward kode yang membuat tagihan < Rp1 dibatalkan di savepoint. Tagihan 0 dari promo otomatis tertahan `CHECK (amount_payable > 0)` di `online_order_part`/`payment_intent_portion` dan cek intent `amount > 0`.
- Kupon sekali pakai hanya di mode `confirm`, terkonsumsi saat place dan dikembalikan saat batal. Mode `none` menolak semua kode.
- Frontend: kode hanya di `useRef` (bukan URL/storage/analytics), route Next membatasi body 2 KB, format salah ditolak sebelum BFF, `quote_hash` tidak ke browser.

**Reservasi stok (E-35)**
- Place satu savepoint: reservasi tidak penuh → rollback total + `out_of_stock` (tanpa SO setengah tercadang).
- `mark_paid` memeriksa move tercadang (bukan `free_qty`). Dicabut POS → `_action_assign` → `stock_short` → alur kompensasi.
- `ready` menandai `picked`. Barang tetap hanya keluar lewat serah terima berkode (SEC-104/105/110 utuh).

**Kanal per brand & secret**
- Nama env/secret per kanal hanya diturunkan dari kode lolos `^[A-Z][A-Z0-9-]{1,15}$` (tanpa `/ . _`). Pemetaan `-`→`_` injektif, dan test traversal ada.
- Overlay compose per kanal hanya me-mount key ke `api-gateway`. `check-encryption.sh` memaksa semua `odoo_api_key_online_*` hanya ke BFF dan `odoo_pii_*` hanya ke `odoo`.
- L-37: satu user teknis per kanal ditegakkan constraint Odoo (`efn_online_channel.py:249-263`), demo diperbaiki, dan skrip rotasi menolak `user_shared`.
- `efn-rotate-online-key --all/--bootstrap`:
  - CHANNELS divalidasi regex, unik, maks 8;
  - file conf, `.env` fallback, dan skrip sendiri wajib milik root dan tidak dapat ditulis grup/lainnya;
  - kunci `--all` terpisah dari kunci per kanal dan tidak diwariskan ke anak;
  - `--bootstrap` hanya bila secret masih `disabled`, dengan overlay dari kode tervalidasi dan bukan symlink;
  - path secret wajib di bawah `SECRETS_DIR`.
- `deploy.sh` memvalidasi kode kanal sebelum membentuk nama overlay dan gagal bila overlay tidak ada.
- `?brand=` divalidasi regex di Next dan BFF, dan pembatasan brand hanya di UI.

**PoC PII (ADR-036)**
- AES-256-GCM dengan IV acak 12 byte per enkripsi, AAD per tabel/kolom, dan byte versi kunci (manipulasi byte versi → gagal tag).
- HKDF-SHA256 subkey per field untuk blind index.
- Kunci dari file secret, divalidasi tepat 32 byte. Log hanya kode (tanpa nilai/id). Vektor uji publik (GCM TC14/16, RFC 5869), bukan kunci nyata.
- Mode terkunci fail-closed untuk tulis (tanpa fallback plaintext), dan baca mengembalikan placeholder (bukan `False`) sehingga form tidak menimpa ciphertext.
- Penanda satu arah: `write(False)` ditolak, `copy_data` mempertahankan penanda walau `default` RPC mencoba mematikannya, dan field hanya `base.group_system`. Penanda format palsu (`efnpii1:` dari input) ditolak di create/write.
- `email_normalized`/`phone_sanitized` dikosongkan untuk partner terlindungi, dan tracking baru tidak mencatat nilai PII.
- Modul tidak ada di daftar instal CI/produksi. `efn_pii_base_vat` hanya auto-install bila `efn_pii` terpasang. Secret PoC hanya ada di compose dev.

**Multi-company & RPC**
- Tidak ada model baru. Field baru (`brand_code`, `efn_*_bidx`, `efn_pii_protected`) berada di model yang sudah ber-rule.
- Pencarian kartu/aturan promo memakai env kanal (`sudo` + `with_company`) dan difilter program company kanal.
- Tidak ada metode publik baru (scanner SEC-58 tetap relevan).

## Yang belum terverifikasi

- Perilaku inti Odoo CE 19:
  - kunci `FOR UPDATE NOWAIT` dan hitungan `total_order_count` di `sale_loyalty` (SEC-130);
  - sinkronisasi `_commercial_fields`, wizard merge, dan pembuatan partner dari email (SEC-136);
  - entropi `loyalty.card._generate_code` (SEC-132).
- SEC-133 dibaca dari kode, tetapi start BFF non-dev tidak dijalankan.
- Penanganan BFF atas refund `cancel` untuk order belum dibayar (SEC-139).
- Tidak ada test/skrip yang dijalankan reviewer. Klaim test di laporan agent dan L-39 (574 test) tidak diperiksa ulang.

## Rekomendasi GO/NO-GO

**GO bersyarat untuk merge `chore/wave-19` → `main`, dengan online tetap OFF di produksi.**

Wajib sebelum merge:
1. **SEC-133**: kecualikan `*_FILE` dari pemeriksaan env literal, ditambah satu test start non-dev. Atau buktikan false positive dengan run container `APP_ENV=production`.

Wajib sebelum mengaktifkan fitur (boleh setelah merge):
- Promo berkuota (`limit_usage`) di kanal online: **SEC-130**.
- Mode `confirm` di kanal publik: **SEC-131** (batas qty/nominal + metrik) dan **SEC-139**.
- Go-live online: **SEC-132**, **SEC-134**.
- E-38 penuh (G20): **SEC-136**, **SEC-137**, **SEC-138**, plus syarat L-39 (a)–(d).

Info SEC-135, SEC-140, SEC-141: tidak memblokir.

---

## Re-review r12 (f7d699b)

Cakupan: commit `f7d699b` di `chore/wave-19` (putaran perbaikan r12). Keputusan lead L-40..L-46 di `docs/04_SERVICE_CONTRACTS.md` §11.7 (baris 865-871).

File yang dibaca:
- Odoo: `efn_online_order/models/{efn_online_channel.py, efn_online_order.py, sale_order.py, efn_online_common.py}`, `migrations/19.0.1.3.0/post-migrate.py`, `__manifest__.py` (versi), `tests/test_online_g19.py` (kelas `TestEfnOnlineSecurityR12`, baris 614-911); `efn_setup_online/demo/efn_online_demo.xml` (grep); `efn_core/tests/test_rpc_surface.py:80-85`; `efn_pii/models/res_partner.py:140-210`.
- BFF: `config/app-config.ts:1295-1620`, `config/online-config.spec.ts:60-115`, `catalog/online-http.ts` (`consumePromoCodeQuota`), `common/rate-limit/rate-limiter.ts`, `cart/cart.controller.ts`, `cart/quote.service.ts:110-250`, `odoo-rpc/promo-codes.ts`, `odoo-rpc/odoo-online.parse.ts` (grep issue).
- Frontend: `components/online/CartView.tsx` (alur promo/requote), `lib/cart.ts` (grep).
- Laporan `docs/agent-reports/efn-odoo-engineer.md:1060-1102` (API Odoo 19 yang diverifikasi engineer dan asumsi).

Hasil test nyata (dijalankan lead, tidak diulang reviewer): Odoo 556/0, BFF 630/630, frontend 17 file OK.

### Ringkasan eksekutif re-review (untuk manajemen)

1. **SEC-133, satu-satunya pemblokir merge, sudah diperbaiki dan diuji.** BFF kini bisa start di staging/produksi dengan secret `*_FILE`.
2. **Perbaikan jalur online benar secara desain:** kuota promo dikunci saat checkout (SEC-130), batas nominal dan qty untuk reservasi stok (SEC-131), log tanpa tag kode (SEC-132), rate limit per kode (SEC-134), dan order yang belum dibayar tidak bisa difakturkan atau di-refund (SEC-139).
3. **Tiga temuan baru, tidak ada yang memblokir merge.** Dua Low: kunci kuota promo otomatis membuat semua checkout satu kanal antre (SEC-142), dan pelanggan sah yang mengubah keranjang beberapa kali bisa kehilangan diskon karena kodenya dihitung ulang setiap quote (SEC-143). Satu Info soal L-46 (SEC-144).
4. **PoC PII (SEC-136/137/138/140) ditunda ke G20 sesuai L-45.** Modul tidak terpasang di produksi.
5. **Verdict: GO untuk merge** dengan online tetap OFF di produksi. Syarat aktivasi fitur tercantum di bawah.

### Status SEC-130..141

| ID | Status | Bukti (file:baris) | Catatan sisa |
|---|---|---|---|
| SEC-130 | **Fixed** (desain benar; konkurensi nyata belum diuji dua koneksi) | `efn_online_channel.py:774-809` (`UPDATE loyalty_program SET write_date = write_date`, id terurut, error konkurensi tidak ditangkap); dipanggil sebelum SO dibuat di `efn_online_order.py:393-397`; docstring/komentar lama diralat (`:811-815`, `:821-823`). Test `test_place_locks_quota_code_program_before_promotions`, `test_place_locks_automatic_quota_promo_and_quota_still_enforced` (spy urutan + `xmin` berubah) | Semantik REPEATABLE READ: UPDATE atas baris yang di-UPDATE transaksi lain yang commit setelah snapshot memicu `SerializationFailure`, sehingga Odoo mengulang request. Test hanya berurutan. Bukti paralel (dua cursor, staging) tetap **wajib sebelum promo berkuota diaktifkan**. Efek samping ketersediaan: **SEC-142**. Batasan yang diakui: SO manual staf tidak mengunci program |
| SEC-131 | **Partial** (syarat L-41 terpenuhi) | Constraint `max_order_amount > 0` di mode confirm + `max_line_qty` 1..9999 (`efn_online_channel.py:251-265`); issue `qty_limit` (`:989-993`) → place `invalid_input` (`efn_online_order.py:344-350`); `order_limit` di quote (`:1020-1021`) + cek ulang di place (`:414-415`); katalog `max_qty` = min(99, 20) (`:404-409`); migrasi placeholder Rp5.000.000 (`19.0.1.3.0/post-migrate.py`); demo + smoke diisi; BFF meneruskan `qty_limit` dan menolak isu produk tanpa `product_id` (`odoo-online.parse.ts:242-248`, `quote.service.ts:199-206`) | Belum ada: batas akumulatif per member/SKU lintas order, dan metrik qty tercadang per outlet / rasio kedaluwarsa per member. Qty baris **reward** (beli X gratis Y) tidak dihitung `max_line_qty` (diakui engineer), padahal ikut tercadang. Sisa griefing per akun: 2 order × Rp5 juta. Wajib metrik/alert sebelum mode `confirm` dibuka di kanal publik |
| SEC-132 | **Fixed** | `promo_code_ref` dihapus (`efn_online_common.py:134-135`); log Odoo hanya `codes=%d applied=%d` (`efn_online_channel.py:917-920`); BFF `promoCodeTag` dihapus (`promo-codes.ts:6-7`, test `promo.spec.ts:100`), log debug hanya jumlah (`quote.service.ts:148-149`); test Odoo memastikan tidak ada `refs=` (`test_online_g19.py:513`) | Level log Nest di produksi masih tanpa filter (`main.ts:39`). Isinya kini tidak sensitif, jadi tidak memblokir |
| SEC-133 | **Fixed** | `app-config.ts:1546-1547` (`!name.endsWith('_FILE')`); test `online-config.spec.ts:73-114` (produksi + `RTL_FILE` + `RTL_GL_FILE`, online OFF dan ON; literal tetap ditolak tanpa nilai di pesan). L-46: `ONLINE_CHANNELS` menolak akhiran `-FILE` (`app-config.ts:1311`, test `:19`) | Lihat SEC-144 (L-46 hanya di BFF) |
| SEC-134 | **Fixed** | `cart.controller.ts:74-75` → `consumePromoCodeQuota` (`online-http.ts:47-72`): n kode = n unit di member DAN IP, penolakan dikompensasi (`rate-limiter.ts:50-60`), unit member dikembalikan bila IP menolak/Redis gagal. Fail-closed | Cara hitung berdampak ke pengguna sah: **SEC-143** |
| SEC-135 | Open (Info, diterima) | Tidak berubah | Tidak memblokir |
| SEC-136 | **Deferred-G20** (L-45) | Tidak berubah. `copy_data` (`efn_pii/models/res_partner.py:158-167`) tetap hanya menangani duplikat | Syarat E-38 penuh. Modul tidak terpasang di CI/produksi |
| SEC-137 | **Deferred-G20** (L-45) | Tidak berubah | Syarat E-38 penuh |
| SEC-138 | **Deferred-G20** (L-45) | Tidak berubah | Syarat E-38 penuh (keputusan ADR-036) |
| SEC-139 | **Fixed** (jalur uang muka PLAUSIBLE) | Guard `_prepare_invoice`: SO online tanpa `paid_idempotency_key` ditolak (`sale_order.py:65-81`); dasar guard = pembayaran tercatat, bukan state. `_efn_do_cancel` tidak membuat refund/nota kredit untuk order tanpa pembayaran, hanya aktivitas Accounting (`efn_online_order.py:663-682`). Test `test_unpaid_confirm_order_cannot_be_invoiced` (`_create_invoices`, `_prepare_invoice`, wizard `delivered`) dan `test_cancel_or_expiry_of_unpaid_order_never_refunds` (batal BFF + cron kedaluwarsa; tanpa refund, tanpa nota kredit, stok kembali) | Wizard uang muka (persentase/nominal) hanya dibuktikan lewat `_prepare_invoice`. Engineer memverifikasi bahwa source CE 19 memanggil `_prepare_invoice`, tetapi reviewer tidak membacanya (PLAUSIBLE). Bila guard memblokir, transaksi wizard di-rollback, termasuk baris uang muka di SO |
| SEC-140 | Deferred-G20 (Info) | Tidak berubah | Catat di ADR-036 |
| SEC-141 | **Fixed** | `test_rpc_surface.py:82-84` kini menyebut SO di savepoint, nomor urut terpakai, `FOR UPDATE NOWAIT` program, dan rollback kartu/poin; docstring `efn_online_quote` (`efn_online_channel.py:1026-1029`) | – |

### Temuan baru (re-review)

| ID | Severity | Verdict | Judul | Lokasi utama | Risiko | Memblokir |
|---|---|---|---|---|---|---|
| SEC-142 | Low (ketersediaan) | CONFIRMED (cakupan kunci); dampak PLAUSIBLE | Kunci kuota L-40 diambil **sebelum** kelayakan diketahui. Promo otomatis berkuota membuat semua place kanal antre lalu gagal serialisasi berulang, dan quote berkode kuota bisa terkena `LockNotAvailable` selama ada place berjalan | `efn_online_channel.py:787-807`; `efn_online_order.py:393-397` | R-B06, R-S10 | Aktivasi promo berkuota (bersama bukti paralel SEC-130) |
| SEC-143 | Low (bisnis/UX) | CONFIRMED (pembacaan kode) | Rate limit per kode menghitung ulang kode yang sama di setiap requote. Pelanggan sah dengan 3 kode terkena 429 setelah sekitar 3 perubahan keranjang dalam 15 menit, lalu quote jatuh ke total **tanpa diskon** dan tombol Bayar tetap aktif | `online-http.ts:47-72`; `cart.controller.ts:75`; `CartView.tsx:56-65,164-177,222-235` | R-B06 | Go-live online |
| SEC-144 | Info | CONFIRMED | L-46 (kode kanal tanpa akhiran `-FILE`) hanya ditegakkan di `ONLINE_CHANNELS` BFF. Constraint Odoo (`CHANNEL_CODE_RE`), `efn-rotate-online-key`, dan `deploy.sh` masih menerima `X-FILE`. Akibatnya fail-closed (BFF gagal start, deploy rollback), bukan kebocoran | `app-config.ts:1311`; `efn_online_common.py:30`; `scripts/` (grep `-FILE` kosong) | R-T15 | – |

**Rekap re-review: 0 Critical · 0 High · 0 Medium baru · 2 Low · 1 Info.**

#### SEC-142: Low: Cakupan kunci kuota terlalu awal dan terlalu lebar

**Fakta:**
- `_efn_online_lock_quota_programs` mengunci **semua** program otomatis ber-`limit_usage` milik kanal (`targets = quota.filtered(lambda p: p.trigger == "auto")`, `:790`), plus pemilik kode yang dikirim. Kunci ini diambil sebelum SO dibuat (`efn_online_order.py:397`), tanpa memeriksa apakah order memenuhi aturan program.
- UPDATE menunggu tanpa `NOWAIT` atau `lock_timeout`, dan kunci baris ditahan sampai commit place. Rentang ini mencakup `action_confirm`, reservasi stok, dan outbox.
- Engineer memverifikasi bahwa `_try_apply_code` CE 19 mengambil `FOR UPDATE NOWAIT` pada program (`docs/agent-reports/efn-odoo-engineer.md:1074`), dan efek sampingnya diakui di `:1093-1094`.

**Skenario (PLAUSIBLE):**
1. Marketing memasang promo otomatis "diskon 10 % untuk 500 pesanan pertama di atas Rp200.000" di kanal RTL.
2. Saat jam ramai, 10 checkout RTL berjalan bersamaan. Banyak di antaranya tidak memenuhi syarat (< Rp200.000).
3. Semua 10 transaksi meng-UPDATE baris program yang sama. Setiap commit membuat seluruh waiter dengan snapshot lama gagal `SerializationFailure`.
4. Odoo mengulang hingga 5 kali dengan backoff acak (sampai belasan detik), sambil menahan worker Odoo yang juga dipakai POS.
5. BFF berhenti menunggu setelah `ODOO_RPC_TIMEOUT_MS` 8 detik, sehingga checkout menjadi 503 atau perlu rekonsiliasi.
6. Di sisi lain, quote dengan kode kuota yang datang selama ada place berjalan gagal `LockNotAvailable` dan ikut diulang Odoo. Antrean publik BFF (2 slot) cepat penuh sehingga quote menjawab 503 `busy`.

Penyerang tidak mendapat keuntungan finansial. Dampaknya degradasi checkout saat promo ramai, yang justru menjadi momen paling sensitif bagi bisnis.

**Kenapa kunci bisa dipindah:** di REPEATABLE READ, `SerializationFailure` muncul pada UPDATE baris yang di-UPDATE transaksi lain yang commit setelah snapshot, **kapan pun** UPDATE itu dijalankan sebelum commit. Kunci tidak harus diambil sebelum hitungan dibaca.

**Rekomendasi:**
- Pindahkan UPDATE ke **setelah** `_efn_online_apply_promotions` dan sebelum konfirmasi. Kunci hanya program ber-`limit_usage` yang benar-benar diklaim SO, yaitu `sale_order.order_line.reward_id.program_id.filtered("limit_usage")` (urut id). Kebenaran kuota tetap terjaga, dan place yang tidak mendapat promo kuota tidak lagi antre.
- Tambahkan `SET LOCAL lock_timeout = '2s'` sebelum UPDATE agar place tidak menahan worker lama. `LockNotAvailable` juga termasuk error yang diulang Odoo.
- Uji paralel di staging bersama SEC-130: 2 place memperebutkan sisa kuota 1, dan 10 place tidak layak dengan 1 program otomatis berkuota. Ukur p95 dan jumlah 503.
- Sampai salah satu dilakukan: checklist Marketing melarang promo **otomatis** ber-`limit_usage` di kanal online. Promo berkode berkuota boleh setelah bukti paralel SEC-130.

#### SEC-143: Low: Pelanggan sah kehilangan diskon karena requote dihitung sebagai percobaan kode baru

**Fakta:**
- `consumePromoCodeQuota` mengonsumsi `promoCodes.length` unit di **setiap** `POST /v1/cart/quote` (`cart.controller.ts:75`), termasuk kode yang sudah diterapkan di quote sebelumnya.
- Frontend mengirim ulang semua kode tersimpan setiap kali keranjang berubah (`CartView.tsx:222-235` → `requote` → `fetchQuote(promoRef.current)`, `:164-177`), saat kode dihapus (`:210-216`), dan saat checkout meminta quote ulang (`:265-267`).
- Bila BFF menjawab 429, frontend menghitung ulang **tanpa kode** dan menampilkan total tanpa diskon dengan pesan generik (`:62-65`, `:148-155`). Tombol Bayar tidak diblokir (`:306`).
- Default `PROMO_MEMBER` adalah 10 unit per 15 menit (`app-config.ts:1602`).

**Skenario:** pelanggan memasukkan 3 kode (3 unit), lalu mengubah qty 3 kali (9 unit) dan menghapus satu barang (12 unit > 10). Ia mendapat 429, quote dihitung tanpa diskon, dan bila ia menekan Bayar tanpa membaca pesan, ia membayar harga penuh. Dampaknya keluhan atau permintaan refund ke CS, bukan kebocoran. Rekomendasi r12 SEC-134 ("hanya kode **baru**") belum diikuti.

**Rekomendasi:**
- Hitung hanya kode yang belum ada di `promo_codes` quote terakhir member, karena quote tersimpan di Redis dan terindeks per member (`quote.service.ts:151-169`). Alternatif: set Redis per member per window berisi HMAC kode (pepper `redisKeyPepper`), lalu konsumsi hanya untuk anggota baru. Batas enumerasi tetap n kode berbeda per window.
- Frontend: saat `promoNotice` aktif dan ada kode tersimpan, minta konfirmasi eksplisit sebelum Bayar ("Total belum termasuk kode promo").
- Test: 3 kode yang sama di-quote 5 kali berturut-turut tidak memicu 429.

#### SEC-144: Info: L-46 hanya di BFF

`ONLINE_CHANNELS` menolak kode berakhiran `-FILE` (`app-config.ts:1311`). Namun constraint Odoo `_check_efn_code` (`CHANNEL_CODE_RE`, `efn_online_common.py:30`) dan skrip host (`efn-rotate-online-key`, `deploy.sh`) tidak memeriksa akhiran ini. Operator bisa membuat kanal `RTL-FILE` di Odoo dan mem-bootstrap secret serta overlay-nya, lalu deploy gagal karena BFF menolak start (fail-closed). Tidak ada kebocoran.

Rekomendasi: tolak akhiran `-FILE` juga di constraint Odoo dan di validasi CHANNELS skrip host, supaya kesalahan konfigurasi tertangkap lebih awal. Catatan kecil terkait: komentar `odoo-online.parse.ts:209` menyebut `max_qty_per_line`, padahal batas reservasi L-41 adalah `max_line_qty`. Ini hanya soal dokumentasi.

### Hal yang sudah benar dalam putaran perbaikan

- **Jalur internal tidak terbuka.** Kunci kuota, guard `_prepare_invoice`, dan pembatalan tanpa refund tidak menambah metode publik (gerbang SEC-58 tetap). Guard membaca `paid_idempotency_key` lewat `sudo()` pada record tautan, sehingga field `groups=base.group_system` tetap tersembunyi dari staf. Penanda tidak berbasis context.
- **Error konkurensi tidak ditelan.** `_efn_online_try_code` dan `_efn_online_claim_auto_rewards` hanya menangkap `_PromoRejected`/`UserError`/`ValidationError`, sehingga `SerializationFailure`/`LockNotAvailable` naik ke mekanisme retry Odoo.
- **Rollback quote melepas kunci.** Kunci `FOR UPDATE NOWAIT` quote diambil di savepoint yang selalu di-rollback, jadi kuncinya lepas sebelum request quote selesai. Test memastikan `xmin` program tidak berubah oleh quote.
- **Batas L-41 berlapis.** Constraint (termasuk saat mode diganti ke `confirm`), issue di quote, penolakan di place, cek nominal ulang dari SO tersimpan, dan `max_qty` katalog. Mode `none` tetap boleh `max_order_amount` 0.
- **Migrasi placeholder aman.** Hanya kanal `confirm` dengan nominal ≤ 0 yang disentuh, setiap kanal dicatat WARNING berisi kode kanal saja (tanpa PII), `if not version: return` untuk instal baru, dan versi manifest `19.0.1.3.0` cocok. Nilai Rp5.000.000 adalah placeholder bisnis: masukkan ke checklist go-live.
- **Rate limit promo fail-closed dan konsisten.** Penolakan tidak memakan kuota, unit member dikembalikan bila IP menolak atau Redis gagal, dan key IP di-hash dengan pepper.
- **SEC-133 diuji di tiga arah:** start produksi dengan `*_FILE` (online OFF/ON), literal tetap ditolak, dan pesan error tidak memuat nilai secret.

### Yang belum terverifikasi (re-review)

- Konkurensi nyata L-40 (dua koneksi atau lebih): kebenaran kuota (SEC-130) dan dampak ketersediaan (SEC-142).
- Jalur wizard uang muka `sale.advance.payment.inv` (persentase/nominal) terhadap guard `_prepare_invoice` (SEC-139). Reviewer tidak membaca source CE 19.
- Perilaku retry `/json/2` Odoo 19 untuk `SerializationFailure`/`LockNotAvailable` (diasumsikan sama dengan `service.model.retrying`).
- Reviewer tidak menjalankan test. Angka 556/630/17 berasal dari lead.

### Verdict re-review

**GO untuk merge `chore/wave-19` → `main`**, dengan online tetap OFF di produksi. Pemblokir merge r12 (SEC-133) sudah ditutup, dan tidak ada temuan baru yang memblokir merge.

Syarat sebelum mengaktifkan fitur (boleh setelah merge):
- **Promo berkuota (`limit_usage`) online:** bukti paralel staging SEC-130 + perbaikan cakupan kunci **SEC-142**. Sampai itu selesai, larang promo otomatis berkuota di kanal online.
- **Mode `confirm` di kanal publik:** metrik/alert qty tercadang per outlet dan rasio kedaluwarsa per member (sisa SEC-131), nilai `max_order_amount`/`max_line_qty` bisnis menggantikan placeholder, dan keputusan atas qty reward.
- **Go-live online:** **SEC-143** (hitung hanya kode baru, atau konfirmasi sebelum Bayar saat diskon tidak terhitung).
- **E-38 penuh (G20):** SEC-136, SEC-137, SEC-138, SEC-140, plus syarat L-39 (a)–(d).
- Info SEC-135, SEC-144: tidak memblokir.
