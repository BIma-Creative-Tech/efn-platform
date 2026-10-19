# Security Review r13 — Gelombang 20 (branch `chore/wave-20`, basis `chore/wave-19`/PR #62)

| | |
|---|---|
| Tanggal | 19 Okt 2026 |
| Reviewer | efn-security-reviewer (read-only, tanpa shell) |
| Cakupan | G20: bayar di kasir E-45 (`efn_online_pos_counter`, `efn_online_order` payment_mode/counter_code, BFF orders/order-events, frontend), gerbang deploy L-55, retur tanpa struk E-41 (`efn_pos_refund_origin`), E-38a `efn_pii*`, 6 entitas W-32 (+ migrasi `09-platform-g20.sql`, ETL), batas berat W-33 |
| Acuan | docs/04 §12 + L-48..L-58, docs/decisions/README "Keputusan pemilik proyek 18 Okt 2026", ADR-036 §0, ADR-001 revisi, laporan G20 di docs/agent-reports |
| Hasil test (dari lead, tidak saya jalankan) | Odoo 624/0 (17 modul); gabungan + efn_pii* 691/0; BFF 686/686; frontend 18 file; ETL 282; deploy 160/0; rotasi 90/0 |
| Nomor temuan | SEC-145 .. SEC-153 |

## Ringkasan eksekutif (untuk manajemen)

1. **Tidak ada Critical/High. Merge G20 boleh (GO)**, dengan online tetap OFF di produksi dan `efn_pii*` belum dipasang di produksi/CI.
2. **Satu Medium memblokir aktivasi "bayar di kasir" (SEC-145).** Refund pelunasan di kasir tidak meminta persetujuan dan tidak memverifikasi pelanggan. Bila Sales Manager membatalkan pesanan dari Odoo, kasir bisa langsung mengeluarkan uang tunai **sebelum** CS menyetujui di BFF. Ini melewati pemisahan tugas SEC-95/L-16. Kasir outlet mana pun di PT itu juga bisa me-refund pesanan yang sudah batal karena stok kurang, tanpa pelanggan hadir.
3. **Satu Medium memblokir aktivasi E-38a (SEC-153), bukan merge.** Cakupan §12.5/AC belum lengkap: nama di faktur (`invoice_partner_display_name`) masih plaintext, residu di `mail.message`/`mail.mail`, gerbang CI AC-4 belum ada, dan secret produksi kunci ketiga (AC-13) belum ada.
4. **Retur tanpa struk (E-41) jauh lebih kuat dari mode tunai lama**, tetapi ada tiga celah Low. Kasir yang melihat PIN Store Manager di layarnya bisa menyetujui sendiri seterusnya (SEC-146). Order campuran (satu barang ber-struk + barang tanpa struk) lolos tanpa persetujuan (SEC-147). PIN sementara tersimpan plaintext di tabel wizard (SEC-151).
5. **Perbaikan SEC-136/137/138 benar secara desain.** Sisa yang tercatat: `efnpii1` masih diterima saat baca (SEC-148), dan padding token nama acak sehingga jumlah token asli terbuka bila ada dua snapshot (SEC-149).

## Tabel temuan

| ID | Severity | Status verifikasi | Judul | File:baris | Risiko | Memblokir |
|---|---|---|---|---|---|---|
| SEC-145 | **Medium** | CONFIRMED (kode EFN); jangkauan lintas-outlet di UI POS PLAUSIBLE | Refund pelunasan counter via POS tanpa persetujuan BFF (SEC-95 dilewati), tanpa verifikasi pelanggan, tanpa ikatan outlet | `efn_online_pos_counter/models/pos_order.py:113-136`; `efn_online_order/models/efn_online_order.py:844-857,1151-1161`; `api-gateway/src/orders/online-saga.service.ts:210`; `order-events.service.ts:256` | R-O05, R-B06, R-S10 | **Aktivasi counter** (prod) |
| SEC-146 | Low | CONFIRMED (desain) | PIN Store Manager diketik di browser kasir. Kasir bisa merekam PIN lalu menyetujui sendiri seterusnya (approver ≠ kasir hanya dicek per uid); nominal di dialog dikendalikan JS kasir | `efn_pos_refund_origin/models/pos_config.py:188-253`; `static/.../payment_screen_patch.js:146-161` | R-B06 | Go-live retur: kontrol detektif |
| SEC-147 | Low | CONFIRMED (server); perakitan di UI POS 19 PLAUSIBLE | Order campuran (≥1 baris ber-`refunded_orderline_id` + baris qty<0 tanpa asal) dinilai "refund ber-struk", sehingga E-41 dan aturan tunai-saja dilewati | `efn_pos_refund_origin/models/pos_order.py:218-237`; `payment_screen_patch.js:24-27` | R-B06 | – (perbaiki G21) |
| SEC-148 | Low | CONFIRMED | `efnpii1:` (AAD tanpa id) tetap didekripsi di luar transaksi create (hanya alarm). Setiap INSERT menulis `efnpii1` ke WAL, sehingga pemegang WAL/backup fisik + akses tulis DB masih bisa memindah ciphertext antar-record | `efn_pii/models/fields_pii.py:189-192,238-241`; `pii_model.py:216-222` | R-L01 | Aktivasi E-38a |
| SEC-149 | Low | CONFIRMED | Token padding acak dibuat ulang tiap kali token dihitung ulang (tulis `name` walau nilainya sama, migrasi). Dua snapshot cukup untuk memisahkan token asli dari padding, jadi jumlah kata/awalan terbuka | `efn_pii/tools/name_tokens.py:74-88`; `fields_pii.py:123-128`; `res_partner.py:208-223` | R-T19 | Aktivasi E-38a (atau terima di ADR) |
| SEC-150 | Info | CONFIRMED | Kunci PIN E-41 berupa jendela geser 15 menit (bukan "15 menit sejak gagal ke-5"); hitungan direset oleh persetujuan sukses siapa pun; satu kasir bisa mengunci approver terus-menerus dengan 1 PIN salah per ~3 menit | `efn_pos_return_approval.py:94-115` | R-B06 | – |
| SEC-151 | Low | CONFIRMED (pola transient Odoo) | PIN baru plaintext di `efn_pos_return_pin_wizard` (disimpan `web_save` sebelum `action_apply`). Bila validasi gagal, baris tetap ada sampai vacuum (±1 jam); juga ada di WAL/backup | `wizard/efn_pos_return_pin_wizard.py:40-41,52-72` | R-L01 | – |
| SEC-152 | Info | CONFIRMED | Dua POS bisa melakukan lookup kode yang sama dan sama-sama menerima uang; hanya sync pertama diterima, sync kedua ditolak sehingga uang kedua tidak tercatat di Odoo | `efn_online_pos_counter/models/pos_order.py:89-104`; `efn_online_counter_lookup.py:62-69` | R-O05 | – |
| SEC-153 | **Medium** | CONFIRMED (status kode vs §12.5/ADR-036 §0.11) | E-38a belum memenuhi cakupan/AC: `account.move.invoice_partner_display_name` plaintext, residu `mail.message` (email_from/body/record_name) & `mail.mail`, AC-4 (CI gabungan + cek SQL mentah) dan AC-13 (secret prod kunci ketiga, deploy-validate) belum ada, AC-1 belum diukur | `efn_pii/README.md:59-60,171-185`; `efn_pii_account/models/res_partner.py` (hanya `name`); `infra/compose/docker-compose.dev.yml:92-98` (kunci hanya dev) | R-L01, R-T18 | **Aktivasi E-38a** |

**Rekap: 0 Critical · 0 High · 2 Medium · 5 Low · 2 Info.** Tidak ada yang memblokir merge.

## Detail temuan

### SEC-145: Medium: Refund pelunasan counter di POS melewati persetujuan dan verifikasi pelanggan

`_efn_counter_plan_refund` (`pos_order.py:113-136`) menerima refund order POS pelunasan bila (a) Σ `efn.online.refund` berstatus `requested` = nominal, atau (b) pesanan `cancelled` tanpa faktur. Tidak ada cek lain: pengirim refund, approval BFF, config/outlet asal (`settlement.config_id`), maupun bukti dari pelanggan.

**Skenario 1 (pemisahan tugas dilewati):** pesanan counter lunas dan sudah difaktur. Sales Manager membatalkannya dari UI Odoo (`_efn_ops_cancelled`, `efn_online_order.py:1151-1161`). Odoo langsung membuat refund `requested` dengan `initiator=odoo` (`:844-857`). Di BFF, refund ini sengaja `approval_required` (SEC-95/L-16, empat mata). Di POS, kondisi (a) sudah terpenuhi, jadi kasir yang bekerja sama dengan Sales Manager bisa langsung me-refund tunai. Event `refund_settled` lalu ditolak BFF sebagai `unapproved` (`online-saga.service.ts:210`), tetapi hanya dicatat sebagai anomali **warning** (`order-events.service.ts:256`). Uangnya sudah keluar. Status BFF juga macet: refund tetap `approval_required`, dan Odoo tidak akan mengirim `refund_settled` lagi. README modul (§Refund) menyebut syarat (a) sebagai "nota kredit dari alur CS + approver BFF", padahal jalur ops-cancel tidak melewati approver.

**Skenario 2 (tanpa pelanggan):** order counter dibayar, lalu `stock_short`. BFF membatalkan dengan `reason=stock_short` tanpa faktur, sehingga syarat (b) terpenuhi. Pelanggan diberi tahu agar mengambil uangnya di kasir. Kasir mana pun di PT itu, termasuk di outlet lain (tidak ada cek config/outlet), bisa mencari tiket pelunasan, me-refund tunai, dan menyimpan uangnya. Tidak ada kode yang harus ditunjukkan pelanggan.

**Rekomendasi (sebelum aktivasi counter di produksi):**
1. Syarat (a) hanya untuk refund `initiator=bff`. Untuk `initiator=odoo`, wajib ada penanda persetujuan dari BFF, misalnya RPC baru `efn_online_refund_approved(channel, refund_ref, approver)` yang dipanggil `online-refund-approve`. Sampai itu ada, tolak.
2. Refund pelunasan wajib persetujuan Store Manager (pakai mekanisme E-41, terikat uuid order refund + nominal), **atau** kode refund sekali pakai yang ditampilkan ke pelanggan di aplikasi (pola kode bayar: digest per company).
3. Ikat refund ke outlet asal: `self.config_id.picking_type_id.warehouse_id == settlement.config_id.picking_type_id.warehouse_id`.
4. Naikkan `refund_settled_rejected` dengan `unapproved` ke **critical**, dan tandai bagian untuk rekonsiliasi Finance.

### SEC-146: Low: PIN Store Manager terekspos di perangkat kasir

Desain 18-15/L-51 memverifikasi PIN di server (benar). Namun PIN diketik di browser POS kasir (`payment_screen_patch.js:150-161`), dan JS itu sepenuhnya dikendalikan perangkat kasir. Siapa pun yang bisa membuka devtools, memasang ekstensi, atau sekadar melihat dari samping akan mendapat PIN. Setelah itu kasir bisa memanggil `efn_return_approval_request(order_ref, approver_id=<manager>, pin, amount, reason)` sendiri kapan saja. Cek "approver ≠ kasir" (`pos_config.py:130`) hanya membandingkan uid, jadi tetap lolos. Nominal `amount` juga berasal dari klien: manager melihat angka yang dirender JS kasir, sedangkan server menyimpan angka yang dikirim JS.

**Rekomendasi:** (a) notifikasi ke approver untuk setiap persetujuan (aktivitas Odoo / push), berisi nominal dari **server**; (b) laporan harian persetujuan per approver vs jadwal kerja; (c) rotasi PIN berkala, dan wajib ganti setelah ada kasir keluar; (d) G21/W-31: opsi persetujuan dari sesi Odoo milik manager sendiri (perangkat kedua), sehingga PIN tidak pernah diketik di perangkat kasir.

### SEC-147: Low: Order campuran melewati E-41

`_efn_refund_origin_validate` (`pos_order.py:218-235`) hanya menjalankan cabang retur tanpa struk bila `unlinked and not origins`. Order dengan satu baris ber-struk (`refunded_orderline_id`) ditambah baris qty negatif tanpa asal masuk ke cabang alokasi per metode. Akibatnya tidak ada persetujuan, dan pembatasan tunai-saja tidak berlaku. JS memakai aturan yang sama (`payment_screen_patch.js:24-27`). Uang keluar tetap dibatasi sisa bayar order asal per metode, sehingga dampak kas tidak lebih besar dari refund penuh ber-struk (yang memang tanpa persetujuan). Yang hilang adalah kontrol E-41 dan jejak barang: barang "tanpa struk" masuk stok atas nama refund ber-struk.

**Rekomendasi:** bila `unlinked` tidak kosong, terapkan aturan E-41 terlepas dari ada/tidaknya `origins` (persetujuan wajib, dengan nominal = porsi baris tanpa asal), atau tolak order campuran. Tambah test sync campuran.

### SEC-148: Low: `efnpii1` masih sah saat baca; salinannya ada di WAL

Saat INSERT, kolom ditulis `efnpii1:` (AAD tanpa id, `fields_pii.py:189-192`), lalu ditulis ulang ke `efnpii2:` di transaksi yang sama (`pii_model.py:216-222`). Baris akhir sudah benar. Namun WAL dan backup fisik (WAL-G) menyimpan versi `efnpii1` setiap record. `convert_to_record` tetap mendekripsi `efnpii1` di mana pun dengan alarm CRITICAL (`fields_pii.py:238-241`). Penyerang yang memegang arsip WAL lama + akses tulis DB bisa menyalin blob `efnpii1` HP korban ke record lain, dan dekripsinya sah. Kontrol SEC-138 menjadi detektif saja untuk aktor ini (hak sangat tinggi, jadi Low).

**Rekomendasi:** setelah migrasi PoC selesai (produksi tidak punya data PoC, L-36-1), terima `efnpii1` hanya untuk id yang dibuat di transaksi berjalan (catat `(tabel, id)` di `env.cr.cache` saat `create`). Di luar itu perlakukan sebagai `auth_failed`. Sediakan saklar env `EFN_ODOO_PII_ACCEPT_V1=false` sebagai default produksi. Tambahkan ke AC-7.

### SEC-149: Low: Padding token nama tidak stabil

`pad_tokens` memakai `os.urandom` setiap kali `_compute_efn_name_tokens` berjalan. Compute ini terpicu oleh setiap `write` yang memuat `name`, termasuk dengan nilai yang sama (sinkron member/impor), oleh perubahan `complete_name` dari induk, dan oleh `_efn_pii_migrate`. Dari dua dump atau backup, token yang bertahan = token asli. Penyerang jadi tahu jumlah token persis, dan dari situ jumlah kata serta panjang awalan, padahal ADR-036 §0.5 menyatakan hanya "ember 16/32/48" yang bocor.

**Rekomendasi:** padding deterministik, misalnya `HMAC(k_pad, record_id ‖ H(sorted(real)) ‖ i)` dengan subkunci HKDF `nametok:pad`, atau pertahankan token padding lama bila himpunan token asli tidak berubah. Alternatif: terima secara eksplisit di ADR-036 §0.5 (R-T19).

### SEC-150: Info: Semantik kunci PIN

Mengonfirmasi catatan QA G20.4 no. 2 (`efn_pos_return_approval.py:95-111`). Laju tebakan tetap ≤ 5 per 15 menit per approver, setara dengan kunci tetap, sehingga brute force PIN 6 digit butuh tahunan. Dua catatan: (1) persetujuan sukses siapa pun mereset hitungan, sehingga setiap persetujuan sah memberi penyerang 4 tebakan tambahan; (2) DoS: satu PIN salah setiap ~3 menit menjaga approver terkunci terus (L-54 menerima kunci 15 menit, bukan kunci permanen). **Saran:** `locked_until` = waktu gagal ke-5 + 15 menit; reset hanya oleh sukses approver itu **di config yang sama**; alert bila satu kasir ≥ 5 gagal / jam.

### SEC-151: Low: PIN plaintext di tabel transient

`pin`/`pin_confirm` adalah `fields.Char` tersimpan. Klik tombol `action_apply` didahului `web_save`, sehingga PIN tertulis plaintext. Bila `action_apply` gagal (identitas kedaluwarsa, PIN lemah, konfirmasi beda), baris tetap ada sampai vacuum transient. Sukses mengosongkannya, tetapi versi lama tetap di WAL/backup. Dapat dibaca pembuatnya dan `base.group_system`. Pola ini sama dengan `change.password.user` Odoo.

**Rekomendasi:** override `create`/`write` wizard agar langsung menyimpan hash (dan hash konfirmasi), tanpa pernah menyimpan PIN; atau dialog OWL yang memanggil RPC `efn_set_return_pin(pin, confirm)` dengan `@check_identity` tanpa model transient.

### SEC-152: Info: Pembayaran ganda di dua kasir

Klaim lookup terikat config tanpa TTL dan tanpa reservasi (`efn_online_counter_lookup.py:62-69`). Dua kasir bisa melakukan lookup kode yang sama dan sama-sama menerima uang. Sync kedua ditolak `already_settled` (`pos_order.py:102-104`; unik di settlement). Server tetap benar, tetapi uang kedua ada di laci tanpa catatan. **Saran:** RPC cek ulang `payable` + klaim eksklusif ber-TTL tepat sebelum layar bayar divalidasi; SOP: sync ditolak `already_settled` → kembalikan uang dan catat.

### SEC-153: Medium (blokir aktivasi E-38a): Cakupan E-38a belum lengkap

Status terhadap ADR-036 §0.11 (dibaca dari kode/README, tidak dijalankan):

| AC | Status |
|---|---|
| AC-5 (SEC-136) | Sebagian. Turunan, merge, email masuk, dan tracking (tiga lapis) sudah ada. Residu `mail.message.email_from/body/record_name` (termasuk log merge) dan `mail.mail.email_to` (struk email POS) belum ditangani (README :178-181) |
| AC-6 (SEC-137) | Ya untuk create/write; compute tanpa write tetap detektif (README :184-185) |
| AC-7 (SEC-138) | Ya untuk baris; lihat SEC-148 |
| AC-10 | **Belum:** `account.move.invoice_partner_display_name` (compute stored) masih plaintext; `efn_pii_account` hanya memulihkan `name` (README :59-60) |
| AC-3 | Belum (`efn_pii_l10n_id` belum ada) |
| AC-4 | Belum: modul tidak di `EFN_TEST_MODULES`; run gabungan 691 test dijalankan manual oleh lead |
| AC-13 | Belum: kunci hanya di `docker-compose.dev.yml:92-98`; tidak ada di compose prod, `secrets.list`, maupun `deploy-validate` |
| AC-1 | Belum diukur |

**Rekomendasi:** jangan pasang `efn_pii*` di produksi sebelum AC-10/AC-4/AC-13 selesai dan residu `mail.message`/`mail.mail` punya keputusan (redaksi saat protect + retensi `mail.mail` `auto_delete`).

## Status temuan sebelumnya (G19 r12 + PoC PII)

| ID | Status r13 | Bukti |
|---|---|---|
| SEC-136 | **Sebagian diperbaiki** | `res_partner.py:306-333` (anak terlindungi sebelum menerima nilai), `_efn_pii_protect` (`:335-350`, enkripsi → redaksi → penanda), `base_partner_merge.py:16-20`, `email_normalized` E + bidx, `mail_tracking_value.py` (tiga lapis, label m2o diperbaiki). Sisa: SEC-153 |
| SEC-137 | **Diperbaiki** (fail-closed tulis) | `pii_model.py:68-110`; `efn_pii_base_vat`, `efn_pii_account` memulihkan kelas; alarm baca `scan_fetch` |
| SEC-138 | **Diperbaiki untuk baris tersimpan** | `efnpii2` AAD `…:<id>:v2` (`keyring.py:100-112`); sisa SEC-148 |
| SEC-140 | Terbuka (Info) | Kunci ketiga terpisah (`odoo_pii_name_key`), hanya ke service odoo (dev compose `:96-98`); masih satu container dengan kunci enkripsi |
| SEC-142/143 | Tidak berubah di G20 (Low, blok aktivasi promo berkuota / go-live online) | – |
| SEC-144 | Tidak berubah (Info) | – |

## Fokus yang diperiksa dan kontrol yang sudah benar

**Bayar di kasir (E-45):**
- Kode bayar dibuat BFF (`generatePickupCode`, ≠ kode ambil), disimpan AES-GCM AAD terikat `part_id`, didekripsi hanya untuk pemilik saat `awaiting_counter_payment` (`orders.service.ts:474-485`), dan di-NULL-kan saat batal/keluar `placed`. Tidak ada di log, URL, RSC, atau analytics. Frontend membuang kode di respons checkout dan status lain (`orders.ts:290`, `route.ts:72`), dan `/api/*` `no-store`.
- Odoo hanya menyimpan digest PBKDF2-SHA512 210k dengan salt `sha256(company ‖ database.secret)` (`efn_online_common.py:78-83`), `groups=base.group_system`, dan dihapus saat bayar/batal/review. Brute force dari dump DB tidak praktis: 31⁸ × 210k dengan umur ≤ 70 menit.
- Lookup: grup POS + `check_access` + company user + sesi terbuka; rate limit 5 gagal/15 menit per sesi dan per kasir. PBKDF2 hanya dihitung setelah cek rate limit dan format, sehingga DoS CPU terbatas. Kode salah, PT lain, dan outlet lain menghasilkan `not_found` yang sama. Respons persis 2 kunci, tanpa PII. Enumerasi tidak bernilai: penyerang hanya bisa *membayar* pesanan orang lain.
- Tabrakan digest: dicek saat place (`efn_online_order.py:485-486`), peluang ~n/8,5·10¹¹ (diterima, QA G20.4 no. 4).
- Sync POS: satu baris, produk pelunasan PT, qty 1, tanpa diskon/pajak/pelanggan/faktur, nominal = `amount_payable` di empat tempat, wajib klaim lookup `ok` di config yang sama, dan outlet = gudang config. `FOR UPDATE` + unik `online_order_id`/`pos_order_id`/`uuid` mencegah bayar dobel. `mark_paid` BFF ditolak untuk counter (`:580-582`); `refund_settled` BFF ditolak untuk counter (`:941-944`).
- Bayar setelah batal/kedaluwarsa → pembayaran dicatat + `review`, tanpa faktur; BFF menerima `cancelled → review` hanya untuk counter + dua alasan (`order-events.service.ts:265`), dan `review_reason` sekarang ikut payload (perbaikan QA).
- `refund_settled` (L-58): HMAC + seq, `provider_refund_ref` regex `pos:<uuid>`, cocok `refund_ref` + nominal persis + ≤ porsi; `refunded` tidak pernah men-settle.
- Kliring: produk pelunasan berakun pendapatan = akun kliring, jurnal kliring masuk/keluar = akun yang sama, sehingga saldo bersih 0 (dengan catatan PLACEHOLDER Finance).
- SEC-105 utuh: POS tidak memuat SO, baris pelunasan tanpa `sale_order_origin_id`.

**Gerbang L-55:** `deploy-validate.sh:253-265` dan `app-config.ts:1598-1610` konsisten: production + online + `qris` wajib xendit/midtrans; `disabled` hanya dengan `ONLINE_PAYMENT_METHODS=counter`; `mock` ditolak. Simulasi bayar counter 404 di produksi dan hanya dengan stub Odoo.

**E-41:** hash pbkdf2-sha512 600k di `res.users` (`groups=base.group_system`, tidak dimuat POS), verifikasi passlib, kunci baris approver (serialisasi), approver ≠ kasir + grup POS manager + company, token = record server (tidak keluar ke klien), terikat config + sesi + uuid + kasir + nominal + TTL 300 s, sekali pakai dengan idempotensi resync, append-only (non-su ditolak), offline diblok, wizard PIN `@check_identity` + cek identitas di `action_apply`, target diri sendiri / admin sistem, PIN lemah ditolak. Migrasi `cash → manager_approval` hanya mengubah nilai default lama.

**E-38a:** K-a context `_ServerToken` tidak bisa dibuat dari JSON-RPC (`pii_model.py:27-55`); penanda format di input ditolak; mode terkunci fail-closed (ketiga kunci wajib), tanpa fallback plaintext; log hanya kode; `pos.order.email/mobile` selalu ciphertext, dan tidak disalin saat kunci hilang; partner terlindungi tidak di-preload POS (`@api.private`); `phone_sanitized` dikosongkan; pencarian angka POS mengeluarkan baris terlindungi dari SQL mentah. Catatan: partner terlindungi tetap bisa dicari kasir dengan pola `%` (semantik standar Odoo, akses diatur ACL; bukan regresi).

**6 entitas:** semua company root (tanpa Branches), CoA per company, user kanal demo `company_ids` satu PT, filter produk kanal `company_id in [False, PT]`. Model baru G20 punya `company_id` + `_check_company_auto` + record rule `company_ids` + ACL baca saja. Migrasi `09-platform-g20.sql`: idempoten, satu transaksi, `SET LOCAL ROLE efn_platform_owner`, constraint `NOT VALID` + `VALIDATE` bila bersih, dan tanpa hak akses baru. ETL dan BFF menolak kode lama (fail closed).

**W-33:** berat dihitung server dari SO (bukan input klien), masuk `quote_hash`, place ditolak bila ada issue berat, batas 0..100 000 kg divalidasi, issue diallowlist di BFF dan frontend.

## Yang belum terverifikasi

- Semua di atas dibaca dari kode; tidak ada yang saya jalankan.
- Apakah UI POS 19 mengizinkan merakit order campuran (SEC-147) dan me-refund tiket dari config lain (SEC-145 skenario 2). Sisi server sudah pasti tidak menolak.
- Apakah `mail.message.record_name` disimpan di 19.0 (SEC-153).
- Urutan `web_save` → `action_apply` pada wizard PIN di 19.0 (SEC-151).
- Tour POS E-45/E-41 dan smoke tahap `q` / `ret_approval` (ditulis QA tanpa runtime).

## Verdict

**GO untuk merge `chore/wave-20`**, dengan online tetap OFF di produksi dan `efn_pii*` tidak dipasang di produksi/CI. Tidak ada temuan yang memblokir merge.

Memblokir aktivasi:
- **Bayar di kasir di produksi:** SEC-145 (approval refund + ikatan outlet + bukti pelanggan, alert critical). Saran: SEC-152 (SOP).
- **E-38a di produksi:** SEC-153 (AC-10, AC-4, AC-13, residu mail), SEC-148; SEC-149 diperbaiki atau diterima di ADR-036.
- **Go-live retur tanpa struk (sudah default saat merge):** kontrol detektif SEC-146 (notifikasi approver + laporan harian) disarankan sebelum outlet live. SEC-147, SEC-151 masuk G21. SEC-150 Info.
