# Security & Compliance Re-review (r6): Gelombang 13/13b, Fase 1 (2026-10-07)

Reviewer: `efn-security-reviewer` (read-only terhadap kode). Re-review atas `SECURITY_REVIEW_2026-10-07-r5.md` setelah Gelombang 13 (SEC-49..54/56, loyalty POS di BFF, migrasi K-7, mart Fase 1 + Metabase sebagai kode, heartbeat ETL, k6) dan 13b (`efn_pos_loyalty_ledger`, perluasan `efn_pos_otp_gate`, `efn_setup_pos`). Basis: `main` @ `c1b770a`.

Cakupan yang dibaca:
- BFF: `services/api-gateway/src/{pos/pos-loyalty.*, pos/pos.service.ts, pos/pos.controller.ts, pos/discount-token.service.ts, pos/loyalty-reservation.sweeper.ts, pos/dto/pos-loyalty.dto.ts, loyalty/loyalty.service.ts, migration/*, privacy/erasure.service.ts, common/net/client-ip.ts, common/route-pii-audit.spec.ts, config/app-config.ts}`, `infra/postgres/init/05-platform-loyalty-pos.sql`.
- Odoo: `efn_pos_loyalty_ledger/**`, `efn_pos_otp_gate/models/pos_config.py`, `efn_core/models/efn_bff_client.py`, `efn_core/tests/test_error_redaction.py`, `efn_privacy/models/efn_privacy_erasure.py`, `efn_setup_pos/data/*`, `efn_audit/models/audit_policy.py`.
- Infra/data: `scripts/lib/deploy-validate.sh`, `scripts/host/efn-deploy`, `scripts/deploy.sh`, `infra/acme/acme-publish.sh`, `infra/compose/docker-compose.yml` (traefik, acme-*, api-gateway, metabase*), `infra/observability/{promtail.yml, loki-rules/*}`, `services/etl/efn_etl/sink.py`, `scripts/check-odoo-deps.sh`, `tests/load/*`.
- Dokumen: `docs/04` §9 (v4.0–4.2), `docs/08` §6, laporan agent bagian Gelombang 13/13b.

Metode: membaca kode dan konfigurasi. **Tidak ada yang dijalankan.** Perilaku runtime yang belum pasti ditandai **PLAUSIBLE**.

## Ringkasan eksekutif (untuk manajemen)

1. **Status 57 temuan lama: 46 Fixed, 7 Partial, 4 Open, 0 Regressed.** Semua Medium r5 sudah tertutup. Perubahan hak akses Odoo kini diaudit dan memicu alert (SEC-49). Pemegang kunci SSH deploy tidak lagi bisa membuka Odoo/BI ke internet atau menyisipkan baris `.env` (SEC-50). `acme-dns` tidak bisa lagi menulis router Traefik (SEC-53).
2. **Ada 9 temuan baru: 1 High, 3 Medium, 4 Low, 1 Info.** High-nya: **setiap user Odoo yang login (termasuk kasir) bisa memanggil klien BFF Odoo langsung lewat RPC** (SEC-58). Klien ini memakai service key Odoo, sehingga user itu bisa mencetak Poin EFN tanpa batas ke nomor mana pun, mengembalikan poin, dan mengirim OTP ke nomor sembarang. Ia juga bisa menandai permintaan penghapusan data (UU PDP) "selesai" di sisi Odoo padahal datanya tidak dihapus.
3. **Tiga Medium semuanya celah fraud poin di POS:**
   - Retur tanpa struk (PLAUSIBLE) atau refund yang ditandai palsu justru *menambah* poin (SEC-59).
   - Refund tanpa baris penyesuaian poin tetap mengembalikan poin pelanggan, padahal uangnya sudah dikembalikan penuh (SEC-60).
   - Plafon earn per transaksi sangat longgar (100.000 poin ≈ Rp10 juta). Tidak ada batas harian, rekonsiliasi dengan order Odoo, atau alert anomali poin (SEC-61).
4. **Yang sudah baik:**
   - Ledger BFF serial per member (advisory lock). Token tukar poin sekali pakai di database. Idempotensi earn/commit/reverse konsisten. Clawback kumulatif dibatasi.
   - Odoo mendeteksi baris poin dari produk, bukan flag klien. Outbox tanpa nomor HP.
   - Endpoint migrasi mati secara default, kuncinya terpisah, dan tidak dirutekan ke publik.
   - Validasi deploy (SEC-50) dan pemisahan `acme-publish` (SEC-53) solid.
5. **Rekomendasi:** **jangan aktifkan `efn_loyalty_earn_enabled`/`redeem_enabled` di outlet pilot sebelum SEC-58..61 ditutup.** SEC-58 bisa diperbaiki dalam hitungan jam (ubah `post`/`get` menjadi metode privat). SEC-43 butir 5 (`base.group_system` hanya Superadmin) kini juga melindungi ledger poin. Alasannya, admin Odoo bisa mencetak poin lewat server action.

## Status temuan lama (SEC-01..SEC-57)

| ID | Sev. awal | Status r6 | Bukti utama (file:line) | Catatan sisa |
|---|---|---|---|---|
| SEC-01..10 | High/Medium | **Fixed** | tidak berubah sejak r5 | SEC-03 PLAUSIBLE runtime |
| SEC-11 | Medium | **Partial** | `docs/compliance/PDP_DATA_INVENTORY.md` belum memuat tabel/model G13 (lihat SEC-65) | DPO, koreksi/ganti nomor, inventaris G13 |
| SEC-12..32 | Low/Medium/Info | **Fixed** | tidak berubah | — |
| SEC-33 | Medium | **Open** | `payments.service.ts:221` (claim sebelum intent ditemukan), `:274` (cek nominal dilewati bila `amount` null) | blocker sebelum vendor payment aktif |
| SEC-34..37 | Low | **Fixed** | — | — |
| SEC-38 | Low (PLAUSIBLE) | **Open** | — | DPO (Q-P11) |
| SEC-39..42 | Info/Low/Medium | **Fixed** | — | — |
| SEC-43 | Medium | **Partial** | butir 1–4 Fixed (r5); `efn_setup_security` masih tanpa pembatasan `base.group_system` (grep = 0) | butir 5 = keputusan klien. Kini juga relevan untuk ledger poin (SEC-61) |
| SEC-44..46 | Low/Info | **Fixed** | — | — |
| SEC-47 | Info | **Partial** | `scripts/check-odoo-deps.sh:24` masih hanya `efn_*/__manifest__.py` | direktori non-`efn_` & `requirements.txt` tidak diperiksa |
| SEC-48 | Info | **Partial** | `common/route-pii-audit.spec.ts:22-36` memuat `PosLoyaltyController`, `MigrationController` | tetap daftar statis |
| SEC-49 | Medium | **Fixed** | `efn_audit/models/audit_policy.py:89-103` (`res.users`, `res.groups`, `ir.rule`, `ir.model.access`, `res.users.apikeys` di `CONTROL_SPECS`), `loki-rules/efn-audit-sink.yml:14-40` (alert critical/warning) | belum tercatat: ACL/rule dari data XML saat upgrade, autovacuum API key, SQL langsung (diakui README) |
| SEC-50 | Medium | **Fixed** | `scripts/lib/deploy-validate.sh:16-23` (topologi dipin di `deploy.conf`, config di manifest bertanda tangan), `:78-130` (WG bind ∈ 10.88/16 \| 10.89/16, CIDR ⊂ 10.88.0.0/15 prefix ≥ /16, profil wajib prod), `:166-176` (kontrol tidak bisa `false` di prod tanpa izin lokal), `:198-217` (`.env` satu kunci satu baris); `efn-deploy:76-88,216-226,233-239`; `deploy.sh:66-71` (IP harus di `wg0`) | aturan validasi ikut rilis bertanda tangan (dapat diterima, lihat SEC-66) |
| SEC-51 | Low (PLAUSIBLE) | **Partial** | `promtail.yml:75-78` (regex berjangkar v2 + `log_id`), `efn_audit/models/base.py:166-182` (HMAC), purge berumur ditolak (laporan Odoo G13) | **verifikasi `mac` belum di-wire**: `services/etl/efn_etl/sink.py` tidak dipanggil job mana pun (laporan data-AI G13 §5). Injeksi baris multi-baris dengan `mac` palsu masih lolos promtail dan memicu alert critical palsu. Filter escape `\r\n` global (efn_core) belum ada |
| SEC-52 | Low | **Partial** | butir 1: `loki-rules/efn-ops-logs.yml:42-86` (heartbeat/dead-man); butir 2: `promtail.yml:46-53,91-94` (`stream=efn_audit_anchor` 400 hari) | butir 3 (duplikat `log_id`, `chain_company` asing, user recon terpisah), 4 (HMAC row_hash), 5 (anggaran erasure vs DSAR) belum; perbandingan kepala rantai antar-hari belum otomatis |
| SEC-53 | Low | **Fixed** | `docker-compose.yml:432-464` (`acme-publish`: network none, read-only, uid 65533), `acme-publish.sh:40-52,69-90` (PEM murni, validasi ulang salinan, template tetap); `acme-dns` hanya menulis `acme-dns-out` (`:411`) | SAN sertifikat tidak dicocokkan dengan domain yang diharapkan (Info) |
| SEC-54 | Low | **Partial** | `common/net/client-ip.ts:11` (normalisasi `::ffff:`), `OTP_RL_SOURCE_LIMIT` 60 (`docker-compose.yml:1001`) | level ALTCHA berbasis rasio gagal + kuota CGNAT = backlog BFF |
| SEC-55 | Info | **Open** | tidak berubah | — |
| SEC-56 | Info | **Fixed** | relasi ke model orang = `model,id`; pola `street|zip|address|birth|login` (laporan Odoo G13, `audit_policy.py`) | `city` sengaja tidak di-mask |
| SEC-57 | Info | **Open** | catatan kecil r5 tidak ditindaklanjuti (promtail-journal root, mart GL lintas PT menunggu Q-6, dsb.) | backlog |

**Rekap:** Fixed 46 · Partial 7 (SEC-11, 43, 47, 48, 51, 52, 54) · Open 4 (SEC-33, 38, 55, 57) · Regressed 0.

## Temuan baru

| ID | Severity | Judul | Lokasi utama | Risiko |
|---|---|---|---|---|
| SEC-58 | **High** | `efn.bff.client.post/get` dapat dipanggil lewat RPC oleh user Odoo mana pun → proxy service key BFF (cetak poin, restore poin, OTP ke nomor sembarang, palsukan penyelesaian erasure) | `efn_core/models/efn_bff_client.py:259-296,298-311` | R-B05/R-B06, R-S01, R-L01 |
| SEC-59 | Medium | Earn memakai `abs(eligible)` untuk order non-refund; status refund berasal dari `is_refund` kiriman klien; refund tanpa tepat satu order asal dilewati diam-diam | `efn_pos_loyalty_ledger/models/pos_order.py:150-154,236-239,296-308` | R-B06 |
| SEC-60 | Medium | Refund dengan baris penyesuaian poin yang salah/hilang hanya ditandai `review`, tetapi `reverse` tetap dikirim dan mengembalikan poin redeem (uang + poin kembali) | `pos_order.py:381-400`, `efn_pos_loyalty_outbox.py:177-207` | R-B06 |
| SEC-61 | Medium | Batas kepercayaan earn: plafon 100.000 poin/order, tanpa batas harian per member, tanpa rekonsiliasi ledger↔order Odoo, tanpa alert loyalty | `pos-loyalty.service.ts:191-199`, `app-config.ts:817-819`, `infra/observability/*` (tidak ada rule loyalty) | R-B05, R-S10 |
| SEC-62 | Low | Validasi redeem saat sync hanya detektif: nilai/partner beda → order diterima dengan diskon, poin tidak didebit; tidak ada cek batas % terhadap total akhir; tanda baris poin tidak diperiksa | `pos_order.py:211-231,267-284`, `pos_config.py:227-249` | R-B06 |
| SEC-63 | Low | Kontrol kasir POS sebagian hanya di browser: PIN `pos_hr` SHA-1 di perangkat, batas selisih kas, void sebelum sinkron, refund ke metode lain; barcode harga `23` melewati `restrict_price_control` dan tidak muncul di filter audit non-timbang; aturan diskon `22` masih aktif di PT selain RTL | `efn_setup_pos/data/barcode_nomenclature_data.xml:92-96`, `pos_audit_filters.xml:72-81`, `docs/08` §6.2 #18/#21/#24/#25 | R-O05, R-O06, R-B06 |
| SEC-64 | Low | Metabase tanpa `MB_ENCRYPTION_SECRET_KEY` (kredensial ClickHouse plaintext di DB aplikasi Metabase); jendela pengambilalihan setup bila provision gagal | `docker-compose.yml:1259-1303,1337-1369` | R-S07 |
| SEC-65 | Low | UU PDP: data G13 belum masuk inventaris; erasure Odoo tidak menyentuh `efn.pos.loyalty.reservation` (`member_ref_masked`, `partner_id`, retensi 400 hari); migrasi dapat menghidupkan kembali member yang sudah dihapus | `efn_privacy_erasure.py:216-251`, `efn_pos_loyalty_reservation.py:18,56-57`, `migration.service.ts:50-84` | R-L01 |
| SEC-66 | Info | Catatan kecil (lihat detail) | — | — |

---

## Detail temuan baru

### SEC-58: High: Klien BFF Odoo dapat dipanggil lewat RPC oleh user mana pun
**Lokasi:** `services/odoo/addons/efn_core/models/efn_bff_client.py:183-185` (`AbstractModel efn.bff.client`), `:259-270` (`post`), `:272-296` (`get`). Keduanya `@api.model` **publik**: tanpa awalan `_`, tanpa `@api.private`, tanpa cek grup, tanpa allowlist path. `_efn_check_path` (`:298-307`) hanya menolak `?#..//\` dan segmen berbentuk nomor HP. Service key dibaca dari file (`:242-256`) dan selalu dikirim (`:315-318`).
**Bukti keterjangkauan:** Odoo mengekspos setiap metode model publik ke `/web/dataset/call_kw` dan `/json/2`. Test repo sendiri (`efn_core/tests/test_error_redaction.py:37-39,49-66,79-82`) membuktikan bahwa user `base.group_user` biasa dapat memanggil metode publik `@api.model` sembarang lewat `call_kw` dan metode itu benar-benar dieksekusi. Tidak ada override `call_kw` di addon `efn_*` (grep = 0). Status: **terverifikasi dari kode. Uji RPC nyata belum dijalankan.**
**Skenario (kasir yang login ke POS, di WireGuard outlet):** dari devtools browser POS:
```
POST /web/dataset/call_kw  {"params":{"model":"efn.bff.client","method":"post",
  "args":["/v1/pos/loyalty/earn",{"idempotency_key":"pos_earn:<uuid acak>","order_ref":"<uuid acak>",
  "member_ref":"62812xxxxxxx","company_code":"RTL","eligible_amount":1000000000}],"kwargs":{}}}
```
- **Cetak poin:** BFF menerima karena service key sah. Satu panggilan memberi hingga `LOYALTY_POS_MAX_EARN_POINTS` = 100.000 poin (≈ Rp10 juta pada nilai default). Batasnya hanya 30 panggilan/15 menit per member (`pos-loyalty.service.ts:646-648`), dan bisa diulang untuk banyak nomor. Poin langsung bisa ditukar di outlet mana pun.
- **Restore poin:** `/v1/pos/loyalty/reverse` dengan `refund_ratio` 1 atas order milik sendiri mengembalikan poin yang sudah ditukar.
- **Release reservasi milik orang lain:** `/v1/pos/loyalty/redeem/release` hanya butuh `reservation_id`.
- **OTP pumping:** `/v1/pos/otp/request` ke nomor sembarang, melewati `_efn_check_otp_caller`, partner record rule, dan pemeriksaan sesi. Batas tersisa hanya budget BFF per jam.
- **UU PDP:** `GET /v1/pos/privacy/erasures?store=odoo` lalu `POST /v1/pos/privacy/erasures/<id>/complete` menandai erasure Odoo selesai tanpa anonimisasi (`privacy/pos-privacy.controller.ts:20-25`). Akibatnya DSAR `completed` padahal data pelanggan masih ada di Odoo.
- **Payment:** `/v1/pos/payments/<id>/cancel` untuk intent orang lain.
- Jejak di Odoo hanya `EFN BFF POST <path> -> 200` tanpa uid (`:359`). Di BFF, ledger hanya menunjuk `order_ref` yang tidak ada di Odoo.
**Rekomendasi:**
1. Segera ubah `post`/`get` menjadi metode privat (`_efn_post`/`_efn_get`, atau `@api.private` bila tersedia di 19.0) dan perbarui semua pemanggil (`grep -rn 'efn.bff.client"\]\.\(post\|get\)'`).
2. Tambahkan HttpCase: kasir `group_pos_user` memanggil `efn.bff.client.post` lewat `/web/dataset/call_kw` dan `/json/2` → harus `AccessError`.
3. Audit semua metode publik di `efn_*` (model biasa dan abstrak) dengan gerbang CI: metode publik baru wajib masuk allowlist RPC yang ditinjau, seperti `route-pii-audit` di BFF.
4. Defense in depth di BFF: service key per lingkup (POS vs privacy vs payments), atau minimal alert untuk earn dengan `order_ref` yang tidak pernah muncul di mart penjualan (lihat SEC-61).

### SEC-59: Medium: Earn atas retur / refund yang ditandai klien
**Lokasi:** `efn_pos_loyalty_ledger/models/pos_order.py:150-154` (`value = abs(raw) * factor`, juga untuk order **non-refund**), `:236-242` (cabang refund/earn ditentukan `self.is_refund`; refund hanya diproses bila `len(originals) == 1`, selain itu **tidak ada** reverse dan **tidak ada** review), `:296-308` (earn untuk setiap order non-refund ber-partner). Test fixture `tests/test_loyalty_ledger.py:664-670` mengirim `is_refund` di payload order, sehingga field ini berasal dari klien (PLAUSIBLE bahwa server Odoo 19 tidak menghitung ulang).
**Skenario:**
1. *(PLAUSIBLE, UI standar)* Retur tanpa struk: kasir peran *basic* memakai tombol `-` (docs/08 F-34), pelanggan member dipilih, order bernilai negatif. Bila order ini tidak berstatus `is_refund`, earn dihitung dari `abs(raw)`, sehingga pelanggan **mendapat** poin senilai barang yang diretur dan tidak ada clawback.
2. *(terverifikasi dari kode, butuh klien dimodifikasi)* Refund dari layar Orders dikirim dengan `is_refund=false`. Earn atas nilai refund terjadi, reverse tidak dikirim, dan poin earn order asal tetap utuh.
3. *(PLAUSIBLE)* Satu order refund berisi baris dari dua order asal: `refunded_order_id` tidak tunggal, sehingga clawback dua order dilewati tanpa jejak.
**Rekomendasi:**
- Tentukan "retur" di server dari baris, bukan dari flag klien: `any(l.qty < 0 or l.refunded_orderline_id for l in lines)`.
- Earn hanya bila `raw` bertanda positif. Hapus `abs()` untuk order non-refund.
- Order bernilai negatif tanpa tepat satu order asal: tanpa earn, tandai `review`, dan buat aktivitas.
- Tambahkan test untuk ketiga skenario.

### SEC-60: Medium: Refund tanpa penyesuaian poin tetap mengembalikan poin
**Lokasi:** `pos_order.py:381-385` (selisih penyesuaian → `_efn_loyalty_flag_review`), lalu `:386-400` tetap meng-*enqueue* `reverse` berstatus `pending`. Cron outbox mengirimnya (`efn_pos_loyalty_outbox.py:177-198`, tanpa memeriksa `efn_loyalty_state == 'review'`). BFF me-restore `floor(redeemed × ratio)` (`pos-loyalty.service.ts:491-515`).
**Skenario:** order Rp100.000 dibayar Rp50.000 tunai + 500 poin (Rp50.000). Saat refund penuh, kasir menghapus baris penyesuaian poin di layar refund (validasi server hanya menolak baris poin **negatif**, `:217-222`). Uang Rp100.000 kembali tunai, **dan** 500 poin dikembalikan ke member. Review supervisor datang setelah poin sudah bisa dipakai lagi.
**Rekomendasi:**
- Saat mismatch, buat outbox `reverse` dengan restore = 0 (clawback earn tetap jalan), atau tahan record (`state='hold'`) sampai supervisor menyetujui.
- Lebih baik lagi, hitung poin yang di-restore dari nilai baris penyesuaian aktual (`actual / point_value`), bukan dari rasio. Tambahkan field `restore_points` eksplisit di kontrak (BFF tetap membatasi ≤ sisa redeem).
- Karena refund berpoin memerlukan sync online, server boleh menolak (`UserError`) refund berpoin tanpa baris penyesuaian yang benar.

### SEC-61: Medium: Earn sepenuhnya dipercaya dari Odoo, plafon longgar, tanpa deteksi
**Lokasi:** `pos-loyalty.service.ts:191-199` (satu-satunya pagar: `points > LOYALTY_POS_MAX_EARN_POINTS`), `app-config.ts:817-819` (default 100.000; = belanja Rp1 miliar pada 1 poin/Rp10.000), rate limit per member `:859-864` (30/15 menit). Tidak ada rule Prometheus/Loki untuk loyalty (`grep loyalty infra/observability` = 0). Tidak ada job ETL yang mencocokkan `loyalty_ledger.source_ref` (pos_earn) dengan `pos_order.uuid`.
**Kondisi:** siapa pun yang bisa membuat Odoo memanggil earn dapat mencetak poin. Itu mencakup SEC-58, admin `base.group_system` lewat server action `env['efn.bff.client']...` (tetap mungkin setelah SEC-58 ditutup; pembuatan server action hanya memicu warning `EfnAuditControlChange`), dan kompromi container Odoo. Liabilitas poin adalah kewajiban keuangan PT (ADR-020).
**Rekomendasi:**
1. Turunkan plafon per order ke nilai basket realistis (mis. 2.000 poin, keputusan Finance), dan tambahkan batas harian per member (`loy:earn:day:<hash>`) dengan `earn_limit_exceeded` + review.
2. Alert: `earn_limit_exceeded`, lonjakan `efn_loyalty_pos_points_total{kind="earned"}` per jam, saldo negatif, outcome `idempotency_conflict`, dan jumlah order `review`.
3. Rekonsiliasi harian ETL: setiap entri `pos_earn`/`pos_reverse` harus punya `pos.order` dengan uuid yang sama, company yang sama, dan `eligible_amount` ≤ nilai bersih order. Selisih → alert critical.
4. SEC-43 butir 5 (batasi `base.group_system`) mengurangi jumlah orang yang dapat mencetak poin lewat Odoo.

### SEC-62: Low: Validasi redeem saat sync hanya detektif
**Lokasi:** `pos_order.py:267-284`. Nilai beda atau partner beda hanya memicu `review`: order diterima dengan diskon, commit tidak dikirim, dan reservasi BFF kedaluwarsa setelah 24 jam, sehingga poin **tidak** didebit. `abs()` membuat baris poin bertanda positif tetap lolos. `pos_config.py:227-249`: `amount_total` reserve berasal dari klien, dan saat sync tidak dicek bahwa nilai poin ≤ `redeem_max_pct` × total barang aktual (nilai OTP dapat digelembungkan, lalu order akhir jauh lebih kecil atau bertotal negatif = uang keluar).
**Skenario:** kasir (berkolusi dengan pelanggan) melakukan OTP dan reservasi 100 poin, lalu mengubah baris poin menjadi −Rp500.000 di klien. Order tersinkron, ditandai review, diskon tetap diberikan, dan poin pelanggan tidak berkurang.
**Rekomendasi:** order berpoin selalu disinkron online (ADR-017 opsi a), sehingga server **boleh menolak** (`UserError`) bila salah satu kondisi berikut terpenuhi:
- baris poin bertanda positif;
- |nilai| ≠ `value_idr` di luar toleransi;
- partner ≠ partner reservasi;
- nilai poin > pct × total barang server;
- total akhir < 0.

Review tetap dipakai untuk jalur offline/terlambat.

### SEC-63: Low: Kontrol kasir POS sebagian hanya di browser
**Lokasi & kondisi:**
- **Barcode diskon `22{NN}`:** nomenklatur EFN hanya dipasang ke PT Retail saat **install** (`barcode_nomenclature_data.xml:92-96`, `noupdate`). PT F&B/EFN/FARM dan DB yang sudah memasang `efn_setup_pos` tetap memakai nomenklatur default. Akibatnya barcode cetakan `2250` memberi diskon 50% tanpa cek `manual_discount`, melewati default `manual_discount=False` (`pos_config_defaults.xml:13`).
- **Barcode harga `23`:** `setUnitPrice` + `price_type=manual` tanpa cek `restrict_price_control`. Filter audit "Harga manual (bukan produk timbang)" justru mengecualikan produk timbang (`pos_audit_filters.xml:75-81`), sehingga label harga palsu untuk produk timbang mahal hanya terlihat di filter umum yang penuh positif palsu.
- **PIN `pos_hr`:** hash SHA-1 tanpa salt dimuat ke browser (docs/08 §6.2 #18). PIN 4–6 digit dapat di-*brute force* offline dalam hitungan detik oleh pemegang perangkat, sehingga kasir dapat menjadi "manager" di UI (refund, tutup sesi dengan selisih).
- **Kontrol lain:** batas selisih kas hanya di frontend (#21). Void sebelum sinkron tanpa jejak (#24). Refund ke metode bayar apa pun (#25).
**Rekomendasi:**
- Terapkan nomenklatur EFN ke semua company yang memakai scanner, atau hapus/nonaktifkan rule `discount` di nomenklatur default lewat data `efn_setup_pos` dengan `noupdate=0`.
- Tambahkan filter "label harga timbangan dengan `price_unit` < `list_price` × berat minimum" atau laporan selisih.
- Perlakukan PIN sebagai kontrol operasional, bukan autentikasi. Prioritaskan G13-2 (approver diverifikasi server) dan G13-6 (batas selisih di server) untuk keputusan Q-27.
- Wajibkan badge `041` untuk manager bila memungkinkan, dan rotasi PIN.

### SEC-64: Low: Metabase menyimpan kredensial tanpa enkripsi aplikasi; jendela setup
**Lokasi:** `docker-compose.yml:1270-1279`: tidak ada `MB_ENCRYPTION_SECRET_KEY`, sehingga detail koneksi ClickHouse (password `efn_metabase` yang ditulis `provision.py`) tersimpan plaintext di DB `metabase` dan di backup-nya. Ini bertentangan dengan prinsip "kredensial/data at rest terenkripsi". `:1337-1369`: provision otomatis setelah sehat. Bila gagal (secret salah, Metabase lambat), instance yang belum di-setup dapat diklaim user di subnet staf lewat `setup-token` publik `/api/session/properties` (`provision.py:357-379`). Deploy gagal (terdeteksi), tetapi admin palsu sudah ada.
**Rekomendasi:**
- Tambahkan secret `metabase_encryption_key` (≥ 32 byte) lewat wrapper entrypoint (`MB_ENCRYPTION_SECRET_KEY`).
- Blok `/api/setup` di router `bi.` (middleware Traefik) secara permanen, karena provision lewat sidecar mTLS tidak melalui Traefik.
- Runbook: bila provision gagal, hapus volume `metabase-data` sebelum mencoba ulang.
- Pantau advisori Metabase v0.52.x.

### SEC-65: Low: UU PDP: cakupan data G13
**Lokasi & kondisi:**
- `efn.pos.loyalty.reservation` menyimpan `partner_id` dan `member_ref_masked` (8 dari 12–13 digit HP terlihat, `efn_bff_client.py:112-117`) selama 400 hari (`efn_pos_loyalty_reservation.py:18,104-109`). `_efn_anonymize_partners` (`efn_privacy_erasure.py:216-251`) tidak membersihkannya, sehingga setelah erasure nomor ter-mask tetap tertaut ke reservasi dan order.
- `MigrationService.importMember` (`migration.service.ts:53-71`) membuat ulang `platform.member` bila baris sudah dihapus erasure (member dihapus total, `erasure.service.ts:192`), tanpa daftar penolakan. Impor ulang file lama setelah DSAR menghidupkan kembali subjek beserta consent lamanya.
- Inventaris PDP tidak menyebut `loyalty_reservation`, `loyalty_pos_earn`, `efn.pos.loyalty.reservation/outbox`, dan mart Fase 1 (grep = 0).
**Rekomendasi:**
- Erasure Odoo: kosongkan `member_ref_masked` dan `partner_id` di reservasi milik partner yang dihapus. Pertimbangkan menyimpan mask yang lebih pendek (`62***789`).
- Migrasi: tolak `member_hash` yang ada di daftar tombstone erasure. Simpan HMAC hash yang dihapus selama masa migrasi, atau minta skrip migrasi mengecualikan DSAR yang sudah selesai.
- Perbarui `PDP_DATA_INVENTORY.md` (v4) dengan tabel G13, retensinya, dan dasar pemrosesan.

### SEC-66: Info: Catatan kecil
- **Aturan validasi deploy ikut rilis** (`efn-deploy:142-145`). Commit ke `main` yang melonggarkan `deploy-validate.sh` berlaku untuk staging. Nilai topologi tetap dipin root di `deploy.conf`, jadi dampaknya terbatas. Production butuh tag. Dapat diterima. Sebaiknya perubahan `scripts/lib/` masuk CODEOWNERS Superadmin.
- **`acme-publish`** tidak memeriksa bahwa SAN sertifikat = `erp|bi|ops|status.<domain>` dan bahwa kunci cocok dengan sertifikat. Dampak kecil, karena pemegang token DNS memang bisa menerbitkan nama di zona itu.
- **Load test** (`tests/load/run.sh:101`) mengirim service key dev lewat `docker run -e` (terlihat di `ps`/`docker inspect`). Hanya stack dev, hasil di-ignore git, dan tidak ada secret/nomor nyata ter-commit (diperiksa). Lebih baik pakai `--env-file` sementara di `$WORK`.
- **Rate limit saldo:** kunci per-config BFF memakai `pos_config_id` kiriman Odoo (`pos.service.ts:109`). Nilainya diisi server (`pos_config.py:202`), tetapi pemanggil langsung (SEC-58/server action) bebas mengubahnya. Limiter Odoo bersifat in-process per worker (`pos_config.py:116-140`).
- **Migrasi saldo pembuka** (`migration.service.ts:87-125`): tanpa log baris dan tanpa plafon per member. Kunci terpisah dan default mati sudah benar. Catat total impor per batch ke log ter-mask untuk rekonsiliasi Finance.
- **Retensi `loyalty_pos_earn` 400 hari:** reverse setelahnya → `earn_pending` hingga 48 jam → `failed`/review. Perilakunya aman (tidak ada restore), tetapi perlu dicatat di runbook retur.

---

## Kontrol yang sudah benar (untuk manajemen)
- **Ledger BFF:**
  - Setiap mutasi saldo diserialkan per member (`pg_advisory_xact_lock`, `loyalty.service.ts:222-225`), dan commit memakai `FOR UPDATE`. Dua reservasi paralel tidak bisa melebihi saldo.
  - Token `pos_redeem` sekali pakai lewat `UNIQUE(token_jti)` di PostgreSQL (`05-...sql:60`), dan tetap terbakar setelah release.
  - Satu reservasi aktif per order (indeks unik parsial).
  - Commit memverifikasi order, member, dan nilai. Release idempoten dan aman terhadap balapan dengan commit.
  - Reverse dibatasi kumulatif terhadap total earn/redeem asal dan memeriksa member order asal.
  - Idempotensi dengan pemeriksaan konsistensi plus `UNIQUE(idempotency_key)` sebagai pagar terakhir. Utang/FIFO bucket konsisten.
- **Privasi BFF:** tabel baru hanya menyimpan `member_hash`. `efn_readonly` mendapat grant kolom tanpa hash/jti. Erasure DSAR menghapus reservasi dan catatan earn. Identitas hanya di body (route audit memuat controller baru).
- **Odoo:**
  - Baris poin dideteksi dari `product_id`, bukan flag klien. Order berbaris poin tanpa reservasi ditolak.
  - Outbox transaksional tanpa payload/HP, kode error disaring (`_safe_code`), `FOR UPDATE SKIP LOCKED`, dan retry hanya untuk kode yang benar.
  - RPC kasir memeriksa grup, sesi terbuka, partner via record rule, dan company. `refund_preview` memeriksa `check_access` + company.
  - Kiosk tidak bisa redeem. Record rule multi-company untuk model baru. `bff_reservation_id` hanya admin.
- **Migrasi K-7:** default 404, kunci terpisah, perbandingan waktu-konstan, tidak dirutekan Traefik (`docker-compose.yml:1079-1081`), dan diaktifkan hanya lewat manifest bertanda tangan.
- **Deploy (SEC-50):** topologi dipin root, config ditandatangani, karakter kontrol ditolak di semua jalur, `.env` diverifikasi per baris, WG bind diperiksa di `wg0`, kontrol keamanan tidak bisa dimatikan di prod tanpa izin lokal, dan 74 uji.
- **ACME (SEC-53):** penulis konfigurasi Traefik tanpa network, PEM divalidasi dua kali, template tetap.
- **Audit (SEC-49/51):** perubahan grup/rule/ACL/API key diaudit dan memicu alert critical. Sink bertanda HMAC. Promtail berjangkar format.
- **Data:** grant Odoo `pos_payment` tanpa data kartu (PAN/cardholder/authcode). `metabase_reader` tanpa `customer_id`. Dead-man heartbeat ETL untuk salinan audit dan recon.

## UU PDP: status

| Kewajiban | Status r6 | Bukti / sisa |
|---|---|---|
| Consent server-side | **Implementasi** | migrasi consent hanya dengan bukti versi + sha256 |
| Hak akses (ekspor) | **Implementasi** | ekspor besar belum |
| Hak hapus lintas store | **Implementasi, ada celah** | reservasi loyalty Odoo tidak dibersihkan; migrasi dapat menghidupkan subjek (SEC-65); penyelesaian erasure dapat dipalsukan via SEC-58 |
| Koreksi / ganti nomor | **Belum** | — |
| Retensi | **Implementasi** (menunggu DPO) | reservasi BFF 30 hari, earn 400 hari, reservasi Odoo 400 hari |
| Akuntabilitas akses data pribadi | **Implementasi** | pemberian grup auditor kini diaudit + alert (SEC-49) |
| Notifikasi insiden 3×24 jam | **Runbook ada** | — |
| Inventaris data / RoPA | **Perlu v4** | tabel/model G13 belum tercantum |
| DPO, kebijakan privasi final, transfer lintas negara | **Belum diputuskan** | Q-P9..P11 |

## Prioritas perbaikan
1. **Segera (sebelum modul `efn_pos_loyalty_ledger` dipasang di DB staging/pilot):** SEC-58. Jadikan `post`/`get` privat, tambahkan uji RPC, dan audit metode publik `efn_*`.
2. **Sebelum loyalty POS diaktifkan di outlet pilot:** SEC-59 (retur tidak menghasilkan poin), SEC-60 (tanpa restore saat mismatch), SEC-61 (plafon + batas harian + alert + rekonsiliasi), SEC-62 (tolak mismatch saat sync).
3. **Sebelum outlet pilot:** SEC-43 butir 5 (keputusan klien), SEC-63 (nomenklatur untuk semua PT, keputusan Q-27 untuk G13-2/G13-6), SEC-65, dan wiring verifikasi `mac` sink (sisa SEC-51).
4. **Sebelum vendor payment diaktifkan:** SEC-33 (tetap).
5. **Hardening terjadwal:** SEC-64, SEC-52 butir 3–5, SEC-54 (level berbasis rasio gagal), SEC-47, SEC-48.
6. **Backlog:** SEC-55, SEC-57, SEC-66, SEC-11 (DPO, koreksi nomor), SEC-38.
7. **Verifikasi runtime yang dibutuhkan:**
   - SEC-58: panggil `efn.bff.client.post` sebagai kasir lewat `call_kw` dan `/json/2`.
   - SEC-59: apakah order retur tombol `-` di Odoo 19 berstatus `is_refund`, dan apakah server menghitung ulang `is_refund` dari payload.
   - SEC-63: nomenklatur aktif per company di DB dev.
