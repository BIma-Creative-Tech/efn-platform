# Security Review (r10): G17 penjualan online Super-App (2026-10-09)

Reviewer: `efn-security-reviewer` (read-only terhadap kode). Lanjutan `SECURITY_REVIEW_2026-10-09_r9.md`.

Cakupan: branch `chore/wave-17` (commit sejak `2ff77d0`: `d9f0ac9`, `4ec8ead`, `0c5ffb3`, `5944651`, `07729c4`).
Spesifikasi: `docs/04_SERVICE_CONTRACTS.md` §10 (+ §10.16/§10.17 L-1..L-15), ADR-034, ADR-019 revisi, `docs/08_ODOO_FIT_GAP.md` §8.

File yang dibaca:
- Odoo: `efn_online_order/models/{efn_online_common,efn_online_channel,efn_online_order,efn_online_refund,efn_online_event_outbox,sale_order,res_partner,product_template}.py`, `wizard/efn_online_handover_wizard.py`, `security/*`, `data/ir_config_parameter.xml`; `efn_setup_online/{hooks.py,data/*,demo/*}`; `efn_core/tests/test_rpc_surface.py`; `services/odoo/render_odoo_conf.py`.
- Gateway: `infra/odoo-rpc-gateway/{haproxy.cfg,entrypoint.sh}`; compose (`odoo`, `odoo-tls`, `odoo-rpc-gateway`, `api-gateway`), `docker-compose.prod.yml`.
- BFF: `orders/*` (controller, service, saga, events, guard, jobs, admin, privacy, repository), `cart/{cart-store,cart.service,quote.service}.ts`, `catalog/{catalog.controller,catalog.service,catalog-storage,online-http}.ts`, `odoo-rpc/json2-client.ts`, `payments/{payments.service,online-payments,payment-gateway.factory,gateways/mock.gateway}.ts`, `privacy/{dsar,erasure}.service.ts`, `cli/{online-cli,online-refund}.ts`, `orders/pickup-code.ts`, `common/rate-limit/rate-limiter.ts`, `config/app-config.ts` (bagian online), `infra/postgres/init/07-platform-online.sql`.
- Frontend: `lib/{orders-route,catalog-route,request-guard}.ts`, `app/api/{orders,cart,catalog}/**/route.ts`, `components/online/PickupCodePanel.tsx`, `next.config.mjs`.
- Infra: `infra/redis/redis-entrypoint.sh` (ACL), `infra/rabbitmq/rabbitmq-entrypoint.sh`, `infra/observability/promtail*.yml`, `scripts/{check-encryption.sh,deploy.sh,lib/deploy-validate.sh,secrets.list}`.

Metode: reviewer hanya membaca kode dan konfigurasi. Tidak ada yang dijalankan. Perilaku yang tidak bisa dipastikan dari kode ditandai **PLAUSIBLE**.

## Ringkasan eksekutif (untuk manajemen)

1. **Tidak ada temuan Critical/High.** Kontrol inti penjualan online sudah benar:
   - harga tidak pernah berasal dari klien;
   - Odoo hanya bisa dijangkau lewat filter 10 metode dengan mTLS di kedua hop;
   - kode ambil tersimpan sebagai hash lambat di Odoo dan terenkripsi di BFF;
   - event Odoo dan webhook pembayaran ditandatangani HMAC dengan replay window;
   - tidak ada IDOR antar-member;
   - Odoo tidak menyimpan PII pelanggan online.
2. **Medium SEC-95, risiko fraud internal (pemisahan tugas).**
   - Salesman Odoo mana pun di PT itu bisa membatalkan SO online yang sudah dibayar. Pembatalan ini otomatis membuat nota kredit dan **refund dana ke pelanggan lewat gateway**, tanpa persetujuan siapa pun.
   - Staf gudang bisa memvalidasi pengiriman SO online **tanpa kode ambil**.
   - Kolusi kedua celah ini bisa menghasilkan "barang keluar + uang kembali".
3. **Medium SEC-96, risiko gangguan POS.** Endpoint stok publik (tanpa login) memanggil Odoo untuk `product_id` sembarang tanpa cache negatif. Tidak ada batas konkuren global, sementara gateway mengizinkan 32 koneksi ke Odoo yang di produksi hanya punya 6 worker. Worker itu dipakai bersama POS.
4. **Temuan lain: 4 Low dan 3 Info.**
   - mutual exclusion consumer refund;
   - audit CLI CS (aktor tidak diautentikasi);
   - retensi kode ambil untuk bagian `confirmed`/`review`;
   - race L-4 hapus akun;
   - hardening.
5. **Rekomendasi:** perbaiki SEC-95 dan SEC-96 sebelum `ONLINE_ENABLED=true` di produksi. SEC-97 dan SEC-98 masuk checklist ADR-016 (refund vendor) dan UI CS.

> **Status setelah re-review G17 (commit `e107b07`, 10 Okt 2026):**
> - SEC-96, 97, 99, 100, 101, 102 dan 103 **Fixed**.
> - SEC-95 dan SEC-98 **Partial**.
> - Temuan baru: **3 Medium (SEC-104, SEC-105, SEC-107)**, **2 Low (SEC-106, SEC-108)**, **1 Info (SEC-109)**.
>
> Lihat bagian *Re-review G17* di akhir dokumen.

## Temuan baru

| ID | Severity | Verdict | Judul | Lokasi utama | Risiko |
|---|---|---|---|---|---|
| SEC-95 | **Medium** | CONFIRMED (kode efn); perilaku UI standar Odoo PLAUSIBLE | Tidak ada pemisahan tugas. Salesman bisa membatalkan SO online berbayar dan memicu nota kredit (sudo) + refund gateway otomatis. Picking SO online bisa divalidasi tanpa kode ambil | `efn_online_order/models/sale_order.py:17-25`, `models/efn_online_order.py:527-562,758-769`, `api-gateway/src/orders/order-events.service.ts:134-156` | R-S10, R-O05 |
| SEC-96 | **Medium** | CONFIRMED (jalur kode); dampak ke POS PLAUSIBLE | `/v1/catalog/stock` publik memicu json2 Odoo untuk id sembarang tanpa cache negatif dan tanpa batas konkuren global. Gateway `maxconn 32` lebih besar dari Odoo `workers 6` | `catalog/catalog.service.ts:239-263`, `catalog/catalog.controller.ts:110-122`, `odoo-rpc/json2-client.ts:61-70`, `infra/odoo-rpc-gateway/haproxy.cfg:144`, `infra/compose/docker-compose.prod.yml:80` | R-T11, R-T07 |
| SEC-97 | Low | PLAUSIBLE | Consumer `order.refund.requested` tanpa klaim eksklusif per refund. Pesan ganda (rekonsiliasi, outbox tanpa dedupe) bisa memanggil refund gateway dua kali. Σ refund mengabaikan refund in-flight | `orders/online-saga.service.ts:357-403`, `orders/online-jobs.service.ts:140-144`, `orders/online.repository.ts:453-455`, `payments/gateways/mock.gateway.ts:52-64` | R-O05, R-T10 |
| SEC-98 | Low | CONFIRMED | CLI refund CS: aktor dideklarasikan sendiri; nota kredit Odoo diposting sebelum cek porsi BFF; `settle-manual` bisa men-settle refund yang gagal karena melebihi porsi | `orders/online-admin.service.ts:42-45,59-85,108-119`, `cli/online-refund.ts:50` | R-S10, R-O05 |
| SEC-99 | Low | CONFIRMED | Retensi kode ambil: bagian `confirmed` yang tidak pernah `ready`, dan bagian `review`, menyimpan `pickup_code_enc` (BFF) dan `pickup_code_hash` (Odoo) tanpa batas waktu | `orders/online.repository.ts:543-548`, `efn_online_order.py:694` | R-L01 |
| SEC-100 | Low | PLAUSIBLE | Race L-4: `prepareDelete` + INSERT `dsar_request` tidak di bawah kunci member yang sama dengan checkout. Erasure tidak mengecek ulang bagian terbuka | `privacy/dsar.service.ts:231-250`, `orders/online-privacy.service.ts:53-70`, `orders/orders.service.ts:117-122`, `privacy/erasure.service.ts:199-203` | R-L01, R-O05 |
| SEC-101 | Info | CONFIRMED | Constraint user teknis kanal tidak melarang grup tambahan. User teknis memegang `base.group_user` (ACL baca luas bila gateway terlewati) | `efn_online_channel.py:196-208`, `efn_setup_online/demo/efn_online_demo.xml:40` | R-S03 |
| SEC-102 | Info | CONFIRMED | AAD `pickup_code_enc` per kolom, tidak terikat id bagian. Ciphertext bisa ditukar antar-baris tanpa terdeteksi | `common/pii/crypto.ts:47`, `orders/pickup-code.ts:24-31` | R-L01 |
| SEC-103 | Info | PLAUSIBLE | Teks kode ambil tersimpan di tabel transient wizard antara `web_save` dan `action_confirm`; tertinggal bila aksi kedua gagal | `wizard/efn_online_handover_wizard.py:21,36-38` | R-L01 |

**Rekap: 0 Critical · 0 High · 2 Medium · 4 Low · 3 Info.** Status temuan lama tidak berubah sejak r9.

---

## Detail temuan

### SEC-95: Medium: Tidak ada pemisahan tugas pada pembatalan SO online dan serah terima barang
**Fakta:**
- `sale_order.py:17-25` meng-override `_action_cancel`. Untuk pembatalan dari UI (tanpa context `efn_online_internal`), satu-satunya cek adalah `_efn_ops_cancel_check` (`efn_online_order.py:758-761`), yang hanya menolak state `picked_up`. Tidak ada cek grup.
- `_efn_ops_cancelled` → `_efn_do_cancel` (`:527-562`) berjalan pada record `sudo` (`self.sudo().efn_online_order_id`). Bila faktur sudah diposting, kode membuat dan memposting nota kredit penuh lewat `efn.online.refund.sudo()._efn_create` (`:533-545`), lalu mengirim event `cancelled` dengan `refund.initiator = "odoo"`.
- BFF, saat menerima event itu (`order-events.service.ts:134-156`), langsung menyisipkan `online_refund(status=odoo_done)` dan meng-enqueue `order.refund.requested`. Dana dikembalikan ke pelanggan lewat gateway tanpa persetujuan siapa pun.
- SO online dibuat dengan `user_id = False` (`efn_online_channel.py:492`). Record rule standar `sale` "own documents" untuk salesman mencakup SO tanpa salesperson, jadi **salesman mana pun** di PT itu bisa membuka dan membatalkannya. Tidak perlu hak Accounting.
- Tidak ada override `stock.picking` di modul. Staf `stock.group_stock_user` bisa memvalidasi picking pengiriman SO online langsung dari Inventory, tanpa wizard kode ambil. State `efn.online.order` tetap `confirmed`/`ready`, sehingga pembatalan di atas tetap diizinkan.
- PLAUSIBLE: apakah UI Odoo 19 menampilkan wizard peringatan saat membatalkan SO berfaktur. Peringatan bukan kontrol akses.

**Skenario:**
1. Pelanggan kolusif membayar Rp 2 jt (pickup).
2. Staf gudang memvalidasi picking dan menyerahkan barang tanpa kode.
3. Salesman (atau staf yang sama bila punya dua grup) menekan *Cancel* pada SO. Hasilnya: nota kredit penuh, event `cancelled` + refund `initiator=odoo`, lalu BFF me-refund Rp 2 jt.

Hasil akhir: barang keluar dan uang kembali. Satu-satunya jejak adalah selisih stok pada stock opname berikutnya. Varian tanpa kolusi: staf membatalkan pesanan siap-ambil secara sepihak (merugikan pelanggan dan reputasi).

**Rekomendasi:**
1. Batasi pembatalan SO online berstatus `paid|confirmed|ready|review` ke grup khusus, misalnya `efn_online_order.group_online_ops_manager` atau minimal `sales_team.group_sale_manager`. Wajibkan alasan dan catat ke `efn_audit`.
2. Di `_efn_ops_cancel_check`, tolak bila ada picking SO yang sudah `done`. Arahkan ke alur refund/retur.
3. Override `stock.picking.button_validate` (atau `_action_done`) untuk picking dengan `sale_id.efn_online_order_id`. Hanya izinkan bila dipanggil dari `_efn_handover` (context privat yang diset setelah verifikasi kode), dengan pengecualian manajer + audit.
4. Di BFF, refund `initiator=odoo` di atas ambang (PLACEHOLDER, keputusan Finance) masuk `manual_required` atau persetujuan 4-mata lewat CLI, bukan otomatis.
5. Tambahkan test `efn`: salesman tidak bisa membatalkan SO online berbayar; picking SO online tidak bisa divalidasi di luar wizard.

### SEC-96: Medium: Endpoint stok publik bisa menghabiskan worker Odoo yang dipakai bersama POS
**Fakta:**
- `GET /v1/catalog/stock` publik, dengan rate limit 60/menit/IP (`catalog.controller.ts:110-122`).
- `catalog.service.ts:239-263` hanya memvalidasi kanal dan outlet. `ids` (≤ 50, angka apa pun) tidak dicocokkan ke `platform.online_catalog_item`.
- Hanya status yang dikembalikan Odoo yang di-cache (`:258-262`). Id yang bukan produk kanal tidak pernah di-cache, sehingga **setiap** request dengan id acak menjadi satu panggilan `/json/2` ke Odoo.
- Klien json2 mengizinkan `maxSockets: 32` (`json2-client.ts:61-70`). Gateway `maxconn 32` ke Odoo (`haproxy.cfg:144`), sedangkan Odoo produksi `ODOO_WORKERS=6` (`docker-compose.prod.yml:80`). Worker ini dipakai bersama POS, backoffice, dan cron.
- Circuit breaker hanya terbuka pada kegagalan transport. Antrean yang lambat tetapi sukses tidak memicunya.

**Skenario:** Penyerang dari beberapa puluh IP (CGNAT seluler, VPS murah) mengirim `GET /api/catalog/stock?channel=RTL&outlet=TOKO-01&ids=<50 angka acak>` ±1/detik per IP. Ini menghasilkan puluhan panggilan json2 per detik. Setiap panggilan menjalankan autentikasi API key, `_efn_online_check_caller`, dan pencarian produk. Hingga 32 request paralel bisa mengantre di 6 worker Odoo, sehingga POS (validasi order, OTP gate, loyalty) melambat atau timeout. Ini bertentangan dengan prinsip "POS & checkout tetap jalan" (R-T11).

**Rekomendasi:**
1. Saring `ids` ke item katalog aktif kanal (query DB BFF) sebelum memanggil Odoo. Id lain langsung tidak dikembalikan.
2. Tambahkan cache negatif atau *request coalescing* per `(channel, outlet)` (satu panggilan Odoo untuk banyak request bersamaan).
3. Pasang semaphore global per kanal di klien json2 untuk metode baca publik (mis. ≤ 2 in-flight untuk `stock`, ≤ 4 untuk `quote`) dengan antrean pendek → `503 online_unavailable`. Saga tulis memakai kuota terpisah.
4. Turunkan `maxconn` backend gateway di bawah jumlah worker (mis. ≤ workers/2). Pertimbangkan `maxconn` per path lewat backend terpisah untuk `efn_online_stock`/`efn_online_quote`.
5. Jangka menengah: worker Odoo khusus online (instance kedua di DB yang sama, ADR-034 follow-up) agar beban storefront terisolasi dari POS.

### SEC-97: Low (PLAUSIBLE): Refund bisa dikirim dua kali ke gateway
**Fakta:**
- `handleRefundRequested` (`online-saga.service.ts:357-403`) memeriksa `isClaimed` tanpa kunci. Status refund baru berubah setelah `payments.refund` kembali.
- Rekonsiliasi meng-enqueue ulang `order.refund.requested` untuk refund `requested|odoo_done|gateway_pending|gateway_done` yang lebih tua dari 15 menit (`online-jobs.service.ts:140-144`). `enqueue` tidak punya dedupe (`online.repository.ts:453-455`).
- Akibatnya, refund yang lambat di gateway (>15 menit) atau consumer paralel (prefetch > 1 / beberapa replika BFF) bisa memproses refund yang sama bersamaan.
- Perlindungan satu-satunya adalah idempotensi provider berbasis `refundId`. Pada `mock` idempotensi itu disimpan in-memory per proses (`mock.gateway.ts:52-64`), sehingga tidak berlaku lintas replika. Adapter xendit/midtrans belum ada (ADR-016).
- Selain itu, `already` (`:366-369`) hanya menjumlahkan refund `gateway_done|settled`; refund in-flight lain tidak dihitung. `markGatewayDone` memakai `Math.min(...)` (`:429`), sehingga kelebihan refund tidak terlihat di `refunded_amount`, hanya di anomali rekonsiliasi.

**Skenario:** Gateway nyata merespons `pending` lalu macet. Setelah 15 menit rekonsiliasi menerbitkan ulang pesan, dan dua consumer memanggil `refund` untuk refund yang sama. Bila adapter tidak meneruskan `refundId` sebagai idempotency key provider, pelanggan menerima dana dua kali, dibatasi hanya oleh total intent (bisa memakan porsi PT lain).

**Rekomendasi:**
- Klaim refund secara atomik sebelum memanggil provider: `UPDATE online_refund SET status='gateway_in_flight' WHERE id=$1 AND status IN (...) RETURNING`, atau `SELECT … FOR UPDATE SKIP LOCKED`.
- Hitung refund in-flight dalam Σ.
- Jadikan "`refundId` = idempotency key provider" syarat wajib adapter di ADR-016, dengan test kontrak.
- Ganti `Math.min` dengan penolakan + anomali critical.

### SEC-98: Low: Audit dan urutan cek pada CLI refund CS
**Fakta:**
- `--actor` adalah string bebas yang hanya divalidasi regex (`online-admin.service.ts:42-45`, `cli/online-refund.ts:50`). Siapa pun yang bisa `docker exec` ke container BFF bisa mencatat aktor lain. Regex mengizinkan `@`, sehingga email staf (PII karyawan) bisa masuk log `EFN_ONLINE_CS_AUDIT`.
- `refund()` memanggil `odoo.refund` (nota kredit **diposting**) di `:60-65` **sebelum** cek porsi BFF (`:70-72`). Bila `over`, refund BFF dicatat `failed`, tetapi nota kredit Odoo tetap ada. Akibatnya akuntansi berbeda dengan dana yang sebenarnya keluar.
- `settleManual` (`:108-119`) menerima status `failed`, termasuk refund yang gagal karena `refund_exceeds_portion`. Cek porsinya hanya memakai `refunded_amount` (refund `gateway_done`), tanpa refund in-flight.

**Rekomendasi:**
1. Ikat aktor ke identitas host: wrapper `efn-online-cs` di host deploy yang mengambil `SUDO_USER`/akun SSH dan mencatatnya ke journald. Dokumentasikan bahwa `--actor` dari wrapper. Batasi regex aktor ke ID staf, bukan email.
2. Lakukan pre-check porsi (Σ semua refund non-`failed`) di bawah kunci bagian **sebelum** `efn_online_refund`.
3. Simpan `failure_reason` dan tolak `settle-manual` untuk `refund_exceeds_portion`.

### SEC-99: Low: Kode ambil bagian `confirmed`/`review` tidak pernah kedaluwarsa
**Fakta:**
- Job retensi (`online.repository.ts:543-548`) menghapus `pickup_code_enc` hanya untuk status final atau `ready_at < now - 7 hari`.
- Odoo mengizinkan serah terima langsung dari `confirmed` (`efn_online_order.py:694`), jadi outlet bisa melewatkan langkah "siap". Bagian seperti itu yang tidak diambil, dan bagian `review` (mis. setelah 5 kode salah), menyimpan ciphertext tanpa batas.
- `pickup_code_hash` Odoo juga baru dihapus saat final.
- Ini bertentangan dengan §10.8.3 ("maks 7 hari setelah ready") dari sisi semangat minimisasi.

**Rekomendasi:**
- Pakai `COALESCE(ready_at, confirmed_at)` dan sertakan `review` di job retensi.
- Tambahkan cron Odoo yang menghapus `pickup_code_hash` pada order non-final setelah N hari (parameter) + aktivitas ke staf. Serah terima sesudahnya lewat verifikasi manual manajer.
- Catat di inventaris PDP.

### SEC-100: Low (PLAUSIBLE): Race antara hapus akun (L-4) dan checkout
**Fakta:**
- `requestDelete` menjalankan `prepareDelete` (cek bagian terbuka) lalu INSERT `dsar_request` (`dsar.service.ts:231-250`) tanpa `lockMember`.
- Checkout memeriksa `hasActiveDelete` di bawah `lockMember` (`orders.service.ts:117-122`). Checkout yang mulai di antara kedua langkah lolos.
- Erasure (setelah masa tunda 7 hari) meng-NULL-kan `member_id` tanpa cek ulang bagian terbuka (`erasure.service.ts:199-203`). Pelanggan yang sudah membayar kehilangan akses kode ambil dan notifikasi refund.

**Rekomendasi:**
- Ambil `pg_advisory_xact_lock` member yang sama di `requestDelete` untuk seluruh `prepareDelete` + INSERT.
- Di job erasure, bila masih ada bagian `paying|confirmed|ready|review`, tunda dan beri alert (bukan unlink diam-diam).

### SEC-101: Info: Hak user teknis kanal tidak dibatasi oleh constraint
- `_check_efn_users` (`efn_online_channel.py:196-208`) hanya memastikan grup kanal ada dan company tunggal.
- Kontrak §10.10 menyatakan "tanpa grup Sales/Inventory/Accounting", tetapi tidak dipaksakan. User teknis memegang `base.group_user`, yang memberi ACL baca luas (partner, produk, dsb.).
- Saat ini mitigasinya adalah filter 10 path di gateway dan blok `/json/2` di Traefik.
- Rekomendasi: constraint bahwa `all_group_ids` user teknis ⊆ {`base.group_user`, `group_online_channel`} + implied keduanya, dan test `efn` yang memverifikasinya. Pertimbangkan menurunkan user teknis ke grup internal minimal tanpa ACL dokumen umum bila json2 Odoo 19 mengizinkan (belum terverifikasi).

### SEC-102: Info: AAD kode ambil tidak terikat baris
- AAD = `efn:online_order_part.pickup_code_enc:v1` (`crypto.ts:47`), sama untuk semua baris.
- Pihak dengan akses tulis DB bisa menyalin ciphertext bagian A ke bagian B, lalu pemilik B melihat kode A. Risikonya rendah karena butuh akses tulis DB.
- Rekomendasi: versi AAD berikut menyertakan `part_id` (`…:v2:<part_id>`), migrasi lewat `pii-reencrypt`.

### SEC-103: Info (PLAUSIBLE): Kode ambil di tabel transient wizard
- Web client Odoo menyimpan record wizard (`web_save`, termasuk `code`) sebelum memanggil `action_confirm` di request terpisah.
- Bila `action_confirm` gagal (mis. `UserError` validasi picking `efn_online_order.py:721`), kode teks asli tertinggal di `efn_online_handover_wizard` sampai vacuum transient. Ini bertentangan dengan klaim "teks kode tidak disimpan".
- Rekomendasi: jadikan field `code` non-stored (`store=False`, nilai dibaca dari argumen/`context` tombol), atau kosongkan `code` di `create`/`write` setelah dipindah ke variabel lokal, dan uji jalur gagal.

---

## Yang sudah benar (kontrol yang ada)

**Permukaan Odoo `/json/2` & gateway**
- `odoo-rpc-gateway`:
  - mTLS masuk dengan CN persis `api-gateway` + `verify required`;
  - path mentah dicocokkan **persis** ke 10 metode (encoded `%`, `;`, `//`, segmen titik, query, absolute-form, `#` ditolak);
  - POST + HTTP/1.1 saja;
  - allowlist nama header (method-override, TE, upgrade ditolak), `Authorization` regex `bearer\x20…` (bug spasi ditemukan dan diperbaiki);
  - `X-Odoo-Database` dipaksa, cookie/XFF dibuang, `Set-Cookie` respons dibuang;
  - log tanpa header/body;
  - tanpa label Traefik/port host, `read_only`, `cap_drop: ALL`, PEM di tmpfs `0600`.
- Re-encrypt mTLS ke `odoo-tls` (`verifyhost odoo`, `allow-cn=odoo-rpc-gateway`). Traefik tetap memblok `/json/2`. `check-encryption.sh` mengunci semua ini di CI.
- `_efn_online_check_caller`:
  - context klien dibuang sebelum operasi ORM apa pun (hanya `lang` whitelist);
  - grup + keanggotaan kanal + company dicek dengan `AccessError` seragam (tidak membedakan kanal tidak ada vs bukan milikmu);
  - `sudo` + `with_company` hanya setelah cek.
- Kunci idempotensi Odoo global, tetapi hasil lama hanya dikembalikan untuk kanal yang sama (`channel_id`/`order_id` dibandingkan). Semua pencarian order/refund difilter kanal. `RPC_ALLOWLIST` memuat tepat 13 metode.
- Grup user teknis tanpa ACL dokumen. Record rule multi-company ada untuk kelima model baru. Field rahasia (`pickup_code_hash`, `payment_ref`, kunci idempotensi) bergrup `base.group_system`.

**Integritas transaksi**
- Place: advisory lock + constraint unik + `payload_sha256`; kode ambil sengaja di luar hash payload.
- Quote dihitung ulang dari pricelist/pajak standar. `quote_hash` dibandingkan saat evaluasi **dan** setelah SO tersimpan.
- `FOR UPDATE` per order untuk mark_paid/cancel/refund/settle/cron. Nominal dibayar ≠ `amount_payable` → `review`.
- Guard `sale_loyalty` (#37): konfirmasi di savepoint, total berubah → rollback + `review`. Hook `sale_ok=False` + `ir.default`.
- Refund Odoo kumulatif ≤ dibayar per SO. Pelanggaran menjadi `UserError`, seluruh transaksi RPC di-rollback.
- BFF:
  - harga tidak pernah dari klien (cart hanya `product_id`/`qty`);
  - quote terikat member di Redis dan `quote_hash` tidak pernah ke klien;
  - `accept_total` hanya pembanding;
  - Σ hasil place = `accept_total`, margin `expires_at` Odoo ≥ BFF + 5 menit (fail-closed);
  - kompensasi idempoten.
- Idempotensi checkout `UNIQUE(member_id, key)` + `request_sha256`. Key deterministik L-8 tidak bisa disalahgunakan lintas member, karena quote terikat member dan key di-scope member.
- Bayar terlambat / cancel vs paid: transisi intent dalam transaksi `FOR UPDATE` yang sama dengan hook order. Jalur terlambat → kompensasi refund, tanpa `mark_paid`.
- Ekspirasi BFF memeriksa ulang intent `paid` di bawah kunci order.

**Kode ambil**
- `crypto.randomInt`, 31 simbol.
- Odoo menyimpan pbkdf2-sha512 600k putaran (passlib, verifikasi waktu-konstan), dihapus saat final, 5 salah → `review`, tidak di-log.
- BFF menyimpan AES-256-GCM dengan AAD, ikut key ring/re-encrypt (L-13), di-NULL-kan saat final.
- Endpoint kode ambil: `no-store`, rate limit sendiri, 409 selama `review` (L-14).
- Frontend menyimpan kode di memori komponen saja (bukan URL/storage), auto-hide 2 menit / saat tab tersembunyi, `no-store` + `Pragma` + `Referrer-Policy: no-referrer`.
- Redaksi Promtail mencakup pola `XXXX-XXXX`, `pickup_code`, `qr_string`, `quote_hash`, `bearer`.

**Webhook & event**
- Event Odoo → BFF:
  - service key + HMAC atas `"<t>." + rawBody` + replay 300 detik;
  - tanpa fallback tanpa tanda tangan di kedua sisi (secret < 32 byte dianggap hilang);
  - klaim `processed_message` di transaksi yang sama; 404/409 di-rollback sebelum commit;
  - `seq` monoton, kanal/PT dicocokkan;
  - payload outbox dibangun saat kirim tanpa PII;
  - TLS CA internal, `allow_redirects=False`.
- Webhook payment `mock`: HMAC + replay + idempotensi. `mock` dan `ODOO_ONLINE_MOCK` ditolak di produksi (config BFF, `deploy-validate.sh`, `check-encryption.sh`). `simulate-payment` 404 di produksi pada BFF **dan** frontend.

**Akses pelanggan & frontend**
- Semua endpoint order memakai `getOrderOfMember` → 404 seragam (anti-enumeration). Id non-UUID → 404. Body ≤ 16 KB. Rate limit per member/IP dengan IP di-HMAC. Kill switch `ONLINE_ENABLED`/`ONLINE_CATALOG_ENABLED` default `false` di produksi.
- Route handler tulis memakai `isSameOrigin`; GET data member memakai Fetch Metadata. `next.config` memaksa `no-store` untuk `/api/*` selain katalog publik.
- Deskripsi produk ditolak bila mengandung HTML di Odoo, lalu dirender sebagai teks oleh React.
- Proxy gambar:
  - hanya `sha` hex yang dirujuk item aktif;
  - `image/jpeg` + batas 5 MB (stream dipotong);
  - CSP `sandbox` + `nosniff`;
  - BFF me-re-encode gambar Odoo dengan `sharp` (magic bytes, `limitInputPixels`, metadata dibuang).
- Tidak ada URL Odoo/MinIO ke klien, tidak ada jalur SSRF (tidak ada URL dari input).

**PDP & least privilege**
- Odoo hanya memakai partner generik per PT. Constraint tanpa HP/email/NPWP dipasang di dua sisi (kanal dan `res.partner`).
- Ekspor DSAR tanpa kode/QR/id internal. L-4 `409 open_online_orders`. Erasure melepas `member_id` dan mempertahankan baris keuangan. Retensi usulan (L-5) diimplementasikan sebagai job.
- PostgreSQL: objek dimiliki role NOLOGIN, `efn_bff` DML minimum, audit CS insert-only, `efn_readonly`/`efn_etl` tanpa akses.
- Redis ACL prefix `~cart:* ~oq:* ~ocat:*`. Queue baru quorum + DLQ + `x-delivery-limit`. Bucket `catalog` privat + SSE lewat `s3-gateway` TLS.
- Secret API key kanal hanya di-mount ke `api-gateway`, HMAC event hanya ke `api-gateway` + `odoo` (dicek CI). Placeholder `disabled` ditolak `deploy.sh` saat online aktif.

## Belum terverifikasi oleh reviewer
- Apakah UI Odoo 19 memblokir atau sekadar memperingatkan pembatalan SO berfaktur (SEC-95 tetap berlaku karena hook efn berjalan dengan sudo).
- Kapasitas nyata Odoo di bawah beban stok/quote (SEC-96). Perlu uji beban di staging dengan POS aktif.
- Perilaku idempotensi refund adapter vendor (SEC-97). Adapter belum ada.

---

# Re-review G17 (efn-security-reviewer, 10 Okt 2026, commit `e107b07`)

Cakupan: perbaikan SEC-95..103 sesuai keputusan L-16..L-18 (`docs/04_SERVICE_CONTRACTS.md` §10.17:766-768).
- BFF: `31c4410`, `91c46f0`.
- Gateway: `maxconn` + antrean 503.
- Wrapper host: `scripts/host/efn-online-cs`.
- Odoo: `e107b07`.

File yang dibaca ulang:
- Odoo: `efn_online_order/models/{sale_order,efn_online_order,efn_online_channel,efn_online_event_outbox,res_users}.py`, `wizard/efn_online_handover_wizard.py`.
- BFF: `orders/{order-events.service,online-saga.service,online-admin.service,online.repository,pickup-code}.ts`, `catalog/catalog.service.ts`, `odoo-rpc/json2-client.ts`, `cli/online-cli.ts`, `privacy/{dsar,erasure}.service.ts`.
- Infra: `infra/odoo-rpc-gateway/haproxy.cfg`, `infra/observability/promtail-journal.yml`.

Tetap read-only; tidak ada yang dijalankan.

## Ringkasan re-review (manajemen)
1. **Perbaikan sebagian besar tepat.**
   - Stok publik tidak lagi bisa membebani Odoo.
   - Refund diklaim eksklusif.
   - Kode ambil kedaluwarsa di kedua sisi.
   - Race hapus akun ditutup.
   - Hak user teknis dipaksakan.
   - AAD terikat baris.
   - Wizard tidak lagi menyimpan kode.
   - Refund dari pembatalan Odoo kini **menunggu persetujuan CS**, tidak otomatis.
2. **Masih ada tiga celah Medium baru pada kontrol SEC-95 (pemisahan tugas):**
   - **SEC-104:** penjaga pembatalan bisa dilewati salesman dengan menambahkan satu flag context dari klien.
   - **SEC-105 (PLAUSIBLE):** penjaga picking hanya berlaku untuk tipe "outgoing" di level picking. Jalur lain (ubah tipe, pindah move, *settle* SO di POS) masih bisa mengeluarkan barang tanpa kode ambil.
   - **SEC-107:** aturan empat mata persetujuan refund **tidak pernah aktif** untuk refund dari Odoo, karena BFF selalu menyimpan pemicu sebagai `odoo-uid:<n>` yang tidak pernah dianggap sama dengan operator mana pun.
3. **Rekomendasi:** SEC-104, SEC-105 dan SEC-107 diperbaiki sebelum `ONLINE_ENABLED=true` di produksi. SEC-106 dan SEC-108 boleh bertahap.

## Status temuan r10

| ID | Status | Bukti (file:line) | Catatan |
|---|---|---|---|
| SEC-95 | **Partial** | Odoo: `sale_order.py:31-43` (cek grup memakai user asli), `efn_online_order.py:796-819` (manager + alasan ≥ 10, tolak bila picking keluar `done`), `sale_order.py:46-66` (guard `_action_done` berbasis `cr.cache`, tidak bisa dipalsukan context). BFF: `order-events.service.ts:137-160` (refund `initiator=odoo` → `approval_required`), `online-admin.service.ts:154-183` (CLI approve) | Bypass tersisa: SEC-104 (context klien), SEC-105 (jalur picking lain), SEC-107 (empat mata tidak aktif) |
| SEC-96 | **Fixed** | `catalog.service.ts:243-245` (id di luar katalog → 404 tanpa Odoo), `:251,266-268` (cache negatif `none` 30 s), `json2-client.ts:64,146,165-178` (semaphore global `ODOO_RPC_MAX_CONCURRENCY`=4, antrean 8×, tunggu 2 s → `busy`), `haproxy.cfg:139-142,155,157` (`maxconn ${ODOO_RPC_MAXCONN}` default 4, antrean ≥ 16 → 503, `timeout queue 2s`) | Sizing `ODOO_WORKERS ≥ worker POS + ODOO_RPC_MAXCONN` masih keputusan sebelum prod (L-17). Residual pembagian slot: SEC-109 |
| SEC-97 | **Fixed** | `online.repository.ts:426-434` (`claimRefund` UPDATE atomik → `gateway_in_flight`), `online-saga.service.ts:377-397` (klaim + Σ `MONEY_OUT_REFUND` termasuk in-flight di bawah kunci bagian), `:457-469` (`Math.min` diganti `failed` + anomali), L-17 (adapter wajib `refundId` = idempotency key) | Residual: pelepasan klaim tanpa CAS → SEC-106 |
| SEC-98 | **Partial** | Wrapper `scripts/host/efn-online-cs:40-48,106` (aktor = `SUDO_USER` tervalidasi, env pemanggil diabaikan), `online-admin.service.ts:16,93-102` (regex aktor tanpa `@`; pre-check porsi sebelum nota kredit), `:192-202` (`settle-manual` menolak `refund_exceeds_portion`, Σ termasuk in-flight) | Residual: fallback identitas `node/<container>` bila CLI dijalankan langsung (`online-cli.ts:49-55`). Alasan teks bebas tercatat oleh sudo, lihat SEC-107/SEC-108 |
| SEC-99 | **Fixed** | BFF `online.repository.ts:561-567` (`COALESCE(ready_at, confirmed_at, updated_at)`, termasuk `review`, `stock_short`). Odoo `efn_online_order.py:195-199` (hash dihapus saat `review`), `:775-793` (cron kedaluwarsa param `efn_online.pickup_code_max_days`), `:705-707` | — |
| SEC-100 | **Fixed** | `dsar.service.ts:240-248` (`lockAndAssertNoOpenOrders` + INSERT dalam satu transaksi, kunci `efn:online:member:` sama dengan `online.repository.ts:270-271` / `orders.service.ts:128-131`), `erasure.service.ts:150-160` (kunci + cek ulang → `ErasureDeferred`, rollback) | — |
| SEC-101 | **Fixed** | `efn_online_channel.py:209-225` (grup ⊆ `base.group_user` + grup kanal + implied), `res_users.py:9-22` (cek ulang saat grup/company user atau implied grup berubah) | — |
| SEC-102 | **Fixed** | `pickup-code.ts:20-23,35-48` (AAD `…:v2:<part_id>`), `orders.service.ts:420` (baris v1 ditulis ulang saat dibaca) | Saran: hapus fallback v1 setelah `pii-reencrypt` dijalankan di semua lingkungan, karena selama fallback ada, ciphertext v1 masih bisa ditukar |
| SEC-103 | **Fixed** | `efn_online_handover_wizard.py:42-56` (kode dipindah ke variabel lokal, semua jalur gagal ditangkap di savepoint, `code=False` selalu ditulis) | Kode masih ada di DB di antara `web_save` dan `action_confirm`; ini inheren pada pola wizard dan dapat diterima |

## Temuan baru

| ID | Severity | Verdict | Judul | Lokasi | Risiko |
|---|---|---|---|---|---|
| SEC-104 | **Medium** | CONFIRMED | Penjaga pembatalan SO online dilewati lewat context klien `efn_online_internal=True` | `sale_order.py:33`, `efn_online_order.py:454,559,723-739` | R-S10, R-O05 |
| SEC-105 | **Medium** | PLAUSIBLE | Penjaga serah terima hanya di `stock.picking._action_done` untuk `picking_type_code == "outgoing"`. Ubah tipe, pindah move, atau *settle* SO di POS (`pos_sale`) melewati kode ambil | `sale_order.py:49-66`, `efn_online_order.py:723-739` | R-S10 |
| SEC-106 | Low | PLAUSIBLE | Pelepasan/penulisan status refund setelah panggilan gateway tidak dikondisikan pada klaim (tanpa CAS). Klaim basi 15 menit bisa saling menimpa | `online-saga.service.ts:375,408,414,419,424`, `online.repository.ts:422-425` | R-O05, R-T10 |
| SEC-107 | **Medium** | CONFIRMED | Aturan empat mata refund inisiatif Odoo tidak pernah menolak. Refund CS tetap satu orang. Identitas aktor fallback bila CLI dijalankan tanpa wrapper | `order-events.service.ts:66-67`, `online-admin.service.ts:34-41,85-151,163`, `online-cli.ts:49-55` | R-S10, R-O05 |
| SEC-108 | Low | CONFIRMED | Alasan teks bebas CS (bisa memuat PII pelanggan) tercatat sudo di auth log/I/O log host. Jejak audit wrapper tidak dikirim ke Loki | `scripts/host/efn-online-cs:10-11,82,106`, `promtail-journal.yml:36-39` | R-L01, R-S10 |
| SEC-109 | Info | CONFIRMED | Satu semaphore json2 untuk jalur publik (stok/quote) dan saga tulis. Slot ditahan selama backoff retry | `json2-client.ts:146,165-178` (+ loop retry `callInner`) | R-T11, R-T10 |

**Rekap re-review: 0 Critical · 0 High · 3 Medium · 2 Low · 1 Info baru.** Status r10: 7 Fixed, 2 Partial (SEC-95, SEC-98).

### SEC-104: Medium: Penjaga pembatalan dilewati lewat context klien
**Fakta:**
- `SaleOrder._action_cancel` (`sale_order.py:33`) melewati **seluruh** pemeriksaan bila `self.env.context.get("efn_online_internal")` benar:
  - cek Sales Manager;
  - alasan wajib;
  - tolak bila picking keluar `done`;
  - hook `_efn_ops_cancelled`.
- Context RPC Odoo (`/web/dataset/call_kw`, `kwargs.context`) sepenuhnya dikendalikan klien. Guard picking (`sale_order.py:52`) sengaja memakai `cr.cache` karena alasan ini, tetapi guard pembatalan masih memakai context.

**Skenario:**
1. Salesman non-manager memanggil `sale.order.action_cancel` dengan context `{"efn_online_internal": true, "disable_cancel_warning": true}` (lewat devtools/RPC dari sesi web WireGuard).
2. SO dan picking-nya dibatalkan. `efn.online.order` tetap `confirmed`/`ready`: tidak ada nota kredit, tidak ada event, dan faktur tetap diposting. Akuntansi dan BFF tidak sinkron.
3. Saat pelanggan datang dengan kode yang benar, `_efn_handover` (`efn_online_order.py:723-739`) hanya memvalidasi picking yang belum `done/cancel`. Karena semua sudah `cancel`, perulangan kosong dan order ditandai `picked_up` tanpa mutasi stok.
4. Kombinasi dengan SEC-105: barang bisa keluar lewat jalur lain sebelumnya, lalu jejak stok hilang.

Penjaga manager + alasan dari SEC-95 jadi tidak mengikat.

**Rekomendasi:**
- Ganti penanda context dengan penanda privat yang tidak bisa disuntik klien. Contoh: `self.env.cr.cache["efn_online_cancel_ids"]` yang diisi `_efn_do_cancel` sebelum memanggil `_action_cancel` dan dibersihkan di `finally`, pola yang sama dengan `HANDOVER_CACHE_KEY`. Lakukan hal yang sama untuk `action_confirm` (`efn_online_order.py:454`).
- Di `_efn_handover`, tolak (`UserError` → review) bila SO tidak punya picking keluar yang tervalidasi/bisa divalidasi, atau bila SO berstatus `cancel`.
- Pertimbangkan menolak `write({'state': ...})` langsung pada SO ber-`efn_online_order_id` dari non-internal.
- Tambah test: `action_cancel` oleh salesman dengan context `efn_online_internal=True` → `AccessError`.

### SEC-105: Medium (PLAUSIBLE): Jalur pengeluaran barang di luar penjaga picking
**Fakta:** Guard (`sale_order.py:49-66`) hanya memeriksa picking dengan `picking_type_code == "outgoing"` dan `picking.sale_id.efn_online_order_id`, di level `stock.picking._action_done`. Jalur yang tidak tercakup (perilaku standar Odoo 19, belum diuji):
1. **Ubah tipe operasi:** staf Inventory menulis `picking_type_id` picking pengiriman ke tipe internal lewat RPC (field model tidak readonly; batasnya hanya di view), lalu memvalidasi. `picking_type_code` menjadi `internal`, sehingga guard lolos, sementara lokasi tujuan bisa tetap Customers.
2. **Pindah move:** `write({'picking_id': <picking manual baru>})` pada `stock.move` SO. Picking baru tanpa `sale_id` lolos guard, sedangkan `sale_line_id` tetap ada, sehingga qty terkirim SO tercatat.
3. **POS `pos_sale` (auto-install bersama POS):** kasir memuat SO online di POS lalu *settle*. Barang keluar lewat picking POS (tipe POS, tanpa `sale_id`), dan `pos_sale` menurunkan/membatalkan move picking SO. Order online tetap `confirmed`; pelanggan asli kemudian ditolak atau mendapat `picked_up` tanpa barang (lihat SEC-104 butir 3). PLAUSIBLE: tergantung domain daftar SO di POS (SO yang sudah difaktur penuh mungkin tersaring di UI, tetapi filternya sisi klien).
- Backorder, mass action "Validate", dan `stock_picking_batch` melewati `button_validate` → `_action_done`, jadi **tertangkap** guard. Return (tipe incoming) tidak relevan untuk pengeluaran barang.

**Rekomendasi:**
- Pindahkan penjaga ke `stock.move._action_done`: tolak move dengan `sale_line_id.order_id.efn_online_order_id` dan `location_dest_id.usage in ('customer', 'transit')` kecuali id move ada di penanda `cr.cache` dari `_efn_handover`.
- Tolak `write` atas `picking_type_id`/`picking_id`/`location_dest_id` pada picking/move SO online kecuali internal.
- Keluarkan SO ber-`efn_online_order_id` dari pemuatan `pos_sale` (domain server-side di loader data POS) dan tolak `pos.order.line` dengan `sale_order_origin_id` online.
- Tambah test untuk ketiga jalur.

### SEC-106: Low (PLAUSIBLE): Klaim refund dilepas/ditimpa tanpa compare-and-set
**Fakta:**
- Setelah klaim atomik, semua penulisan status berikutnya memakai `updateRefund` tanpa syarat status (`online.repository.ts:422-425`):
  - pelepasan saat error transien `{status: before}` (`online-saga.service.ts:414`);
  - `manual_required` (`:408`), `gateway_pending` (`:419`), `failed` (`:424`).
- Klaim basi direbut ulang setelah 15 menit (`IN_FLIGHT_STALE_MS`, `:42`).
- Consumer A yang macet > 15 menit lalu gagal bisa menimpa status `gateway_done`/`settled` milik consumer B kembali ke `odoo_done`, sehingga refund diproses ulang.
- `before` juga kehilangan status asal: `requested`/`gateway_pending` dipulihkan sebagai `odoo_done` bila klaim direbut ulang.

**Dampak:** dana ganda hanya bila adapter provider mengabaikan `refundId` (dilarang L-17). Selebihnya inkonsistensi status dan `refunded_amount` (ditolak `markGatewayDone` → `failed` + anomali).

**Rekomendasi:** kolom `claim_token` (UUID per klaim), lalu semua penulisan pasca-gateway memakai `WHERE id=$1 AND status='gateway_in_flight' AND claim_token=$2`. Simpan status asal di kolom terpisah untuk pelepasan.

### SEC-107: Medium: Empat mata refund tidak efektif
**Fakta:**
- Parser event (`order-events.service.ts:66-67`) **mengutamakan** `initiator_uid`, sehingga `initiated_by = "odoo-uid:<n>"` selalu terisi, karena Odoo selalu mengirim uid (`efn_online_event_outbox.py:152-158`).
- `sameActor` (`online-admin.service.ts:39`) selalu mengembalikan `false` untuk `odoo-uid:`. Akibatnya penolakan `rejected_same_actor` (`:163`) **tidak pernah terjadi** untuk satu-satunya jenis refund yang memerlukan persetujuan.
- Bahkan dengan login, perbandingan login Odoo ↔ username OS hanya berupa kecocokan string antar dua namespace identitas.
- Refund yang dibuat CS lewat `online-refund` (`:85-151`) langsung `odoo_done` → gateway tanpa persetujuan kedua, termasuk refund penuh bagian `handed_over`. Satu orang dengan akses grup `efn-cs` cukup untuk mengembalikan dana pesanan yang barangnya sudah diserahkan.
- `operatorIdentity` (`online-cli.ts:49-55`) jatuh ke `SUDO_USER`/user OS container (`node`) + hostname bila `EFN_CS_OPERATOR` kosong. `docker exec` langsung (anggota grup docker) tetap berjalan dengan aktor generik atau aktor yang dipalsukan lewat `-e EFN_CS_OPERATOR=`.

**Skenario:** Manajer penjualan yang juga anggota `efn-cs` di host membatalkan SO berbayar di Odoo, lalu menyetujui refund-nya sendiri lewat `efn-online-cs online-refund-approve`. Persetujuan lolos.

**Rekomendasi:**
1. Pemisahan berbasis **peran**, bukan perbandingan string. Persetujuan hanya untuk grup OS `efn-cs-approver` (dicek wrapper, diteruskan sebagai `EFN_CS_ROLE`) yang beranggotakan orang berbeda dari Sales Manager Odoo. Tambahkan tabel pemetaan `odoo_uid ↔ akun OS` yang dikelola Superadmin; tolak persetujuan bila pemicu ada di peta dan sama dengan penyetuju.
2. Refund CS di atas ambang (PLACEHOLDER Finance) atau untuk bagian `handed_over` → `approval_required` (empat mata yang sama).
3. CLI menolak berjalan bila `EFN_CS_OPERATOR` kosong (tanpa fallback `node`). Dokumentasikan bahwa anggota grup docker setara root dan di luar kontrol ini.
4. Test: refund `initiated_by=odoo-uid:N` + penyetuju yang terpetakan ke uid N → ditolak.

### SEC-108: Low: Alasan CS di log host dan audit host tidak terpusat
**Fakta:**
- sudoers yang dianjurkan (`efn-online-cs:10-11`) memakai `log_output`. sudo juga selalu mencatat baris perintah lengkap (`COMMAND=… --reason=…`) ke auth log/journald, padahal komentar wrapper (`:25-26`) menyatakan alasan tidak di-log. Alasan teks bebas sampai 800 byte (`:82`) bisa memuat nama/HP pelanggan.
- Baris `logger -t efn-online-cs` tidak termasuk filter `promtail-journal.yml:36-39` (hanya `efn-deploy` dan unit backup). Jejak audit sisi host hanya lokal dan bisa dihapus oleh pemilik root.

**Rekomendasi:**
- Baca alasan dari stdin (prompt) alih-alih argv. Pertahankan `log_output` hanya bila output CLI dijamin tanpa PII (saat ini hanya id/status/nominal). Tambahkan peringatan "jangan tulis data pribadi" + scrub pola HP/email di BFF sebelum disimpan ke `online_admin_audit`.
- Tambahkan identifier `efn-online-cs` ke allowlist `promtail-journal.yml` (alasan tidak ikut bila dibaca dari stdin).

### SEC-109: Info: Slot json2 dibagi antara jalur publik dan saga
- Semaphore tunggal (`json2-client.ts:146`) melayani stok, quote, place, mark_paid, cancel, dan refund. `callInner` menahan slot selama `sleep` backoff retry.
- Lonjakan quote dari banyak member terautentikasi bisa membuat place/mark_paid mendapat `busy` (503/nack). Kerugian dana tidak ada, karena saga idempoten dan diulang.
- Saran: pool terpisah (baca publik vs saga tulis) dan lepas slot selama backoff.

## Yang sudah benar (perubahan G17 r10)
- Guard picking memakai penanda di `cr.cache` yang hanya diisi `_efn_handover` setelah kode terverifikasi dan dibersihkan di `finally` (`efn_online_order.py:729-736`). Context klien `efn_online_handover_picking_ids` diuji tidak berpengaruh (`tests/test_online_api.py:746-748`).
- Cek grup pembatalan memakai user asli, bukan sudo (`sale_order.py:34-38`). Pemanggilan `_action_cancel` lewat `sudo()` dari modul lain tetap dicek terhadap user sesi.
- Refund inisiatif Odoo tidak lagi otomatis (`approval_required`); consumer mengabaikannya sampai disetujui (`online-saga.service.ts:371`). Event membawa kode alasan tanpa teks bebas; login berbentuk email tidak dikirim (L-18).
- Wrapper host:
  - root-owned, `env_reset`/`!setenv`;
  - `SUDO_USER`/`SUDO_UID` dicek konsisten + keanggotaan grup;
  - allowlist subcommand dan flag, validasi nilai, tolak karakter kontrol dan flag ganda;
  - argumen diteruskan sebagai argv `docker exec` tanpa shell, `PATH` dipaku.
  - Tidak ditemukan injeksi argumen/env.
- Batas kapasitas gateway gagal-cepat dengan header berbeda (`X-EFN-Gateway-Error`), sehingga tidak tertukar dengan penolakan filter.

## Belum terverifikasi (re-review)
- Perilaku `pos_sale` 19.0 terhadap SO yang sudah difaktur penuh (SEC-105 butir 3) dan apakah `picking_type_id` dapat ditulis RPC pada picking `assigned` (butir 1). Perlu uji di stack dev oleh QA.
- Sizing `ODOO_WORKERS` produksi terhadap `ODOO_RPC_MAXCONN` (L-17).
