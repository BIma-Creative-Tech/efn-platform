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
