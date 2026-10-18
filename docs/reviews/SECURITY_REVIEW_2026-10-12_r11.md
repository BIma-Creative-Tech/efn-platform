# Security Review (r11): Gelombang 18, Poin EFN sebagai alat bayar, refund ke metode asal, privasi, rotasi key (2026-10-12)

Reviewer: `efn-security-reviewer` (read-only terhadap kode). Lanjutan `SECURITY_REVIEW_2026-10-09_r10.md`.

Cakupan: branch `chore/wave-18` (commit sejak `98469c5`, HEAD `47ab818`).
Spesifikasi: `docs/04_SERVICE_CONTRACTS.md` §9.9 v6 (K-18-1..8, L-23, L-24), §10.17 (L-21/L-22), `docs/08_ODOO_FIT_GAP.md` §9, `docs/10_RENCANA_GELOMBANG_18_PLUS.md`, `docs/decisions/README.md` (PP-1..PP-6).

File yang dibaca:
- Odoo:
  - `efn_pos_loyalty_ledger/models/{pos_order,pos_config,pos_payment_method,pos_session,efn_pos_loyalty_reservation,efn_pos_loyalty_outbox}.py`, `migrations/19.0.2.0.0/post-migrate.py`, `security/ir.model.access.csv`;
  - `efn_pos_refund_origin/models/{pos_order,pos_config}.py`, `__manifest__.py`;
  - `efn_pos_otp_gate/models/{pos_config,res_partner}.py`, `efn_pos_otp_challenge.py` (retensi);
  - `efn_setup_pos/{data/pos_cashier_groups.xml,data/pos_loyalty_points_payment.xml,data/pos_member_price_data.xml,models/pos_config_points.py}`;
  - `efn_setup_online/{data/efn_online_setup_data.xml,demo/efn_online_demo.xml,hooks.py}`;
  - `efn_setup_company/hooks.py`; `efn_core/tests/test_rpc_surface.py`.
- BFF: `pos/{pos.service,pos-loyalty.service,loyalty-reservation.sweeper}.ts`, `pos/dto/*`; `privacy/{privacy.controller,dsar.service,erasure.service,erasure-deferral,erasure-pending}.ts`; `cli/online-erasure-pending.ts`; `infra/postgres/init/08-platform-g18-privacy.sql`.
- Frontend: `app/api/me/route.ts`, `app/api/auth/otp/verify/route.ts`, `lib/{auth-age,dsar}.ts` (sekilas).
- Host/infra:
  - `scripts/host/efn-rotate-online-key`, `scripts/host/systemd/*`, `scripts/host/efn-deploy` (rsync rilis);
  - `scripts/deploy.sh` (secrets `host:`, stop etl), `scripts/lib/deploy-validate.sh`, `scripts/secrets.list`;
  - `infra/clickhouse/{clickhouse-init.sh,init/02-loyalty-etl.sql}`;
  - `services/etl/efn_etl/{erasure,loyalty_recon,config}.py`;
  - `infra/compose/docker-compose.yml` (ETL env), `.github/workflows/deploy.yml` (ETL env).

Metode: reviewer hanya membaca kode dan konfigurasi. Tidak ada yang dijalankan. Perilaku yang tidak bisa dipastikan dari kode, terutama perilaku inti Odoo CE 19 yang source-nya tidak ada di repo, ditandai **PLAUSIBLE**.

## Ringkasan eksekutif (untuk manajemen)

1. **Tidak ada temuan Critical/High.** Validasi Poin EFN saat sinkron order sudah kuat:
   - Odoo mendeteksi pembayaran poin dari metodenya, bukan dari flag yang dikirim kasir.
   - Pembayaran poin wajib cocok persis dengan reservasi ber-OTP: POS, order, pelanggan, dan nominal (toleransi 0).
   - Batas % order dihitung ulang di server, dan kembalian tunai tidak bisa berasal dari poin.
   - BFF membatasi pengembalian poin ke sisa poin yang dipakai, di bawah kunci per member. Pengembalian ganda tidak mungkin terjadi.
2. **Tiga Medium. Ketiganya celah pengendalian fraud internal di kasir, bukan celah dari internet.**
   - **SEC-113:** validasi hanya berjalan saat order disinkron. Pembayaran yang sudah tersimpan mungkin masih bisa diubah lewat RPC selama sesi terbuka, misalnya "Tunai" diganti "Poin EFN" sehingga kasir mengantongi tunainya.
   - **SEC-114:** rekonsiliasi harian Poin EFN (alat untuk mendeteksi SEC-113) **mati secara default**, karena mode ETL masih `product`.
   - **SEC-115:** kunci nomor HP member kedaluwarsa setelah 30 hari bagi member yang didaftarkan kasir atau hanya mengumpulkan/menukar poin. Di G18 kasir kini boleh mengubah kontak.
3. **Satu Medium-PLAUSIBLE di refund (SEC-116).** Pembayaran bertanda "kembalian" dikecualikan dari aturan "refund ke metode asal". Bila flag itu dikirim klien, aturan E-31 bisa dilewati.
4. **Temuan lain: 5 Low dan 3 Info.** Isinya pengerasan dan konsistensi.
5. **Rekomendasi:** perbaiki SEC-113..116 sebelum metode Poin EFN dan refund-asal diaktifkan di outlet pilot. SEC-114 cukup dengan perubahan konfigurasi deploy.

> **Status setelah re-review G18 (14 Okt 2026, perbaikan L-25: `e881ae1`, `a045a0a`, `e0d3f2e`, `751dd89`, commit Odoo terakhir):**
> - Status temuan r11:
>   - **Fixed:** SEC-113, 114, 115, 116, 119, 120, 121, 123, 124;
>   - **Partial:** SEC-118 (rekonsiliasi ETL belum memakai `points_restored`), SEC-122 (perlu cek `installed` di DB lama);
>   - **Reopened:** SEC-117, karena `FOR UPDATE` tanpa UPDATE tidak menyegarkan snapshot REPEATABLE READ.
> - **Temuan baru:**
>   - 2 Medium PLAUSIBLE: **SEC-125** (order dibayar di luar `_process_saved_order`: `action_pos_order_paid`, `write({'state': 'paid'})`, wizard `pos.make.payment`) dan **SEC-126** (penanda sync berlaku untuk seluruh `_process_order`, termasuk perintah x2many atas pembayaran order lain);
>   - 2 Low: SEC-127 (= SEC-117 reopened) dan SEC-128 (cek restore BFF berbasis rasio eligible menolak refund barang yang dikecualikan dari earn; toleransi 10 %);
>   - 1 Info: SEC-129.
> - **Rekomendasi: GO bersyarat untuk merge ke `main`.** Tetap **NO-GO** untuk mengaktifkan tukar Poin EFN (`efn_loyalty_redeem_enabled`) dan mengandalkan E-31 di outlet pilot sampai SEC-125, SEC-126, dan SEC-127 Fixed.
>
> Lihat bagian *Re-review G18* di akhir dokumen.

## Temuan baru

| ID | Severity | Verdict | Judul | Lokasi utama | Risiko |
|---|---|---|---|---|---|
| SEC-113 | **Medium** | PLAUSIBLE (ACL `pos.payment` inti Odoo); jalur efn CONFIRMED | Validasi Poin EFN & refund-asal hanya saat sync. `pos.payment` order paid bisa diubah/ditambah lewat RPC selama sesi terbuka (Tunai → Poin EFN), tanpa pemeriksaan ulang | `efn_pos_loyalty_ledger/models/pos_order.py:548-558`, `efn_pos_refund_origin/models/pos_order.py:154-158`, `efn_pos_loyalty_ledger/models/pos_session.py:15-28` (tidak ada `_inherit = "pos.payment"` di addon efn) | R-S10, R-B05 |
| SEC-114 | **Medium** | CONFIRMED | `ETL_LOYALTY_POINTS_MODE` default `product` di semua jalur deploy, padahal Odoo G18 sudah menolak produk poin. Rekonsiliasi harian K-18-6 mati, dan setiap redeem menjadi anomali critical palsu | `infra/compose/docker-compose.yml:1531`, `.github/workflows/deploy.yml:266`, `scripts/deploy.sh:311`, `services/etl/efn_etl/config.py:51`, `loyalty_recon.py:613` | R-B05, R-S10 |
| SEC-115 | **Medium** | CONFIRMED | Kunci HP member (SEC-07/44) hanya dari challenge OTP (retensi 30 hari) atau order promo ber-OTP. Member `pos_enroll`/redeem/earn-saja tidak terkunci lagi setelah 30 hari, sementara kasir G18 memegang *Contact Creation* | `efn_pos_otp_gate/models/res_partner.py:131-148`, `efn_pos_otp_challenge.py:121-130`, `efn_setup_pos/data/pos_cashier_groups.xml:20-25` | R-S10, R-L01 |
| SEC-116 | **Medium** | PLAUSIBLE (`is_change` dari payload klien) | E-31 mengecualikan pembayaran ber-`is_change`. Refund negatif yang ditandai "kembalian" lolos aturan metode asal | `efn_pos_refund_origin/models/pos_order.py:105-107,124-125` | R-S10 |
| SEC-117 | Low | PLAUSIBLE | Dua refund paralel atas order asal yang sama (dua terminal) sama-sama lolos cek "sisa per metode"; tidak ada kunci baris order asal | `efn_pos_refund_origin/models/pos_order.py:48-58,129-152`, `efn_pos_loyalty_ledger/models/pos_order.py:321-338,445-459` | R-S10 |
| SEC-118 | Low | CONFIRMED | Pertahanan berlapis K-18-3/K-18-6 tautologis: outbox mengirim `payment_amount = reservation.value_idr` (bukan Σ pembayaran order); sisi restore rekonsiliasi membandingkan data Odoo dengan nilai yang juga dilaporkan Odoo | `efn_pos_loyalty_outbox.py:326-328`, `pos-loyalty.service.ts:373-381`, `loyalty_recon.py:191-197` | R-B05 |
| SEC-119 | Low | CONFIRMED | POS Manager dapat mematikan flag `efn_is_loyalty_points` (atau mengarsipkan metode); semua validasi server dan rekonsiliasi untuk metode itu ikut mati, termasuk retroaktif | `efn_pos_loyalty_ledger/models/pos_payment_method.py:18-54` | R-S10 |
| SEC-120 | Low | PLAUSIBLE | Rotasi key tanpa kunci bersama dengan `deploy.sh`. Operasi compose paralel bisa memicu rollback/rc=3. Key yang sudah dibuat tetapi keluarannya gagal diparse tidak dicabut sampai rotasi berikutnya | `scripts/host/efn-rotate-online-key:135-136,293,415-421`, `scripts/deploy.sh` (tanpa `flock`) | R-T15 |
| SEC-121 | Low | PLAUSIBLE (ACL inti) | Grup kasir "boleh membuat pelanggan" mewarisi seluruh `base.group_partner_manager`: ubah partner bersama lintas PT (company kosong) dan, menurut ACL standar, `res.partner.bank` (rekening vendor) | `efn_setup_pos/data/pos_cashier_groups.xml:23` | R-S10, R-S03 |
| SEC-122 | Info | CONFIRMED | `efn_pos_refund_origin` tidak menjadi dependency modul setup mana pun dan tidak ada di daftar instal. Kontrol E-31 bisa absen tanpa terdeteksi | `efn_pos_refund_origin/__manifest__.py:13-16` | R-S10 |
| SEC-123 | Info | CONFIRMED | Pagar earn BFF (`enforceMoneyPortion`) dilewati bila earn diproses sebelum commit redeem (commit sedang retry) | `pos-loyalty.service.ts:647-654`, `efn_pos_loyalty_outbox.py:205-226` | R-B05 |
| SEC-124 | Info | CONFIRMED | Rekonsiliasi K-18-6 memakai `loyalty_reservation` committed yang dihapus saat erasure DSAR / purge retensi. Hasilnya positif palsu `points_payment_daily_mismatch` | `privacy/erasure.service.ts:311`, `loyalty-reservation.sweeper.ts:80-92`, `loyalty_recon.py:184-189` | R-B05, R-L01 |

**Rekap: 0 Critical · 0 High · 4 Medium · 5 Low · 3 Info.** SEC-113 dan SEC-116 berstatus Medium dengan verdict PLAUSIBLE.

Status temuan lama tidak berubah sejak r10.

---

## Detail temuan

### SEC-113: Medium: Pembayaran POS bisa diubah setelah sync, melewati validasi Poin EFN dan refund-asal

**Fakta (CONFIRMED di kode efn):**
- Semua validasi E-30/E-31 hanya berjalan di `_process_saved_order` (`efn_pos_loyalty_ledger/models/pos_order.py:548-558`, `efn_pos_refund_origin/models/pos_order.py:154-158`). Contoh validasi: reservasi, nominal, `change_from_points`, metode asal, sisa per metode.
- Tidak ada addon efn yang meng-override `pos.payment` `create`/`write`/`unlink` (grep `_inherit = "pos.payment"`: nihil).
- `pos_session.py:15-28` hanya menolak *selisih* tutup sesi pada metode poin. Nilai yang diharapkan dihitung dari baris `pos.payment` yang ada saat itu.

**Asumsi (PLAUSIBLE, source inti tidak ada di repo):**
- ACL standar CE memberi `point_of_sale.group_pos_user` hak write/create pada `pos.payment`. Hak ini diperlukan karena sync berjalan dengan uid kasir.
- Constraint inti `_check_amount` hanya menolak order `done`/`invoiced`. Order `paid` dalam sesi terbuka masih bisa diubah.

**Skenario:**
1. Pelanggan membayar Rp200.000 tunai. Order tersinkron sah.
2. Kasir memanggil `/web/dataset/call_kw` `pos.payment.write([id], {"payment_method_id": <Poin EFN>})`. Constraint metode milik config lolos karena Poin EFN tertaut ke POS.
3. Tutup sesi: kas yang diharapkan turun Rp200.000. Kasir mengambil tunai itu tanpa selisih. Akun liabilitas poin didebit Rp200.000 tanpa redeem di BFF.

Varian lain:
- Menaikkan `amount` baris Poin EFN dan menurunkan tunai.
- Membuat `pos.payment` baru pada order paid.
- Mengganti metode baris refund yang sudah divalidasi E-31, misalnya kartu → tunai.

Satu-satunya deteksi adalah rekonsiliasi harian K-18-6. Rekonsiliasi itu mati secara default (SEC-114).

**Rekomendasi:**
- `efn_pos_loyalty_ledger` (dan `efn_pos_refund_origin` untuk metode refund): tambahkan `_inherit = "pos.payment"`. Tolak `create`/`write`(`amount`, `payment_method_id`, `pos_order_id`)/`unlink` bila salah satu kondisi berikut terpenuhi:
  - metode lama atau baru `efn_is_loyalty_points`;
  - order bukan `draft`, kecuali di dalam jalur sync.

  Penanda jalur sync disimpan di `cr.cache` dan diisi wrapper `_process_saved_order`, pola SEC-44, bukan context. Superuser (OdooBot) dikecualikan.
- Test HttpCase: kasir `call_kw` `pos.payment.write`/`create` pada order paid → `AccessError`/`UserError`.
- Sebelum go-live, verifikasi ACL `pos.payment` di image `efn-odoo:ci`, lalu ubah verdict menjadi CONFIRMED/Fixed.

### SEC-114: Medium: Mode rekonsiliasi Poin EFN default `product` padahal Odoo G18 sudah memakai metode bayar

**Fakta:**
- Default `product` ada di `docker-compose.yml:1531`, `deploy.yml:266`, `deploy.sh:311`, `config.py:51`, dan `LoyaltyReconSettings.points_mode = "product"` (`loyalty_recon.py:613`).
- Pada rilis yang sama, Odoo menolak baris produk Poin EFN (`pos_order.py:269-273`) dan mengarsipkan produknya (migrasi 19.0.2.0.0).
- `deploy-validate.sh:172` hanya mengecek regex `product|payment`.

**Dampak:**
- Pemeriksaan `points_payment_daily_mismatch` (K-18-6), satu-satunya detektor SEC-113 dan restore tidak sah, tidak berjalan.
- Setiap redeem sah memicu anomali critical `redeem_without_points_line`, karena ledger `pos_redeem` tidak punya baris produk. Alert menjadi bising sehingga anomali nyata tenggelam.

**Rekomendasi:**
- Ubah default ke `payment` di rilis G18 (compose, workflow, deploy.sh, config).
- Atau buat `deploy-validate` gagal bila `efn_pos_loyalty_ledger` ≥ 19.0.2 terpasang dan mode masih `product`. ETL juga bisa mendeteksi sendiri kolom `pos_payment_method.efn_is_loyalty_points` dan menolak start bila mode tidak cocok.

### SEC-115: Medium: Kunci HP member kedaluwarsa setelah 30 hari, kasir kini memegang Contact Creation

**Fakta:**
- `_efn_otp_member_partners` (`res_partner.py:131-148`) menganggap partner sebagai "member" hanya dari dua sumber:
  - (a) `efn.pos.otp.challenge` yang ada, padahal autovacuum menghapusnya setelah `efn.pos.otp_retention_days` (default 30, `efn_pos_otp_challenge.py:121-130`);
  - (b) `pos.order.efn_otp_gate_state`, yang hanya diisi untuk promo ber-OTP (`efn_pos_otp_gate/models/pos_order.py:157,226`).
- Member hasil `pos_enroll` (G18), member yang hanya redeem (reservasi menyimpan `partner_id` 400 hari tetapi tidak dicek), dan member yang hanya earn **tidak terkunci lagi setelah 30 hari**.
- G18 memberi kasir `base.group_partner_manager` lewat `group_efn_cashier_contact_creation` (`pos_cashier_groups.xml:20-25`).

**Skenario:**
1. Pelanggan tetap "Budi" didaftarkan lewat `pos_enroll` dan sejak itu hanya mengumpulkan poin.
2. Pada hari ke-31, kasir mengganti HP partner Budi dengan nomornya sendiri. Tidak ada `AccessError`, dan chatter/audit hanya mencatat perubahan oleh manajer.
3. Setiap pembelian Budi berikutnya (kasir memilih "Budi") menambah poin ke nomor kasir.
4. Bila Budi menukar poin, OTP dikirim ke nomor kasir, jadi kasir juga bisa menukar poin miliknya sendiri atas transaksi Budi.

Pagar SEC-61 (batas earn per kasir per jam) hanya membatasi laju.

**Rekomendasi:**
- Jadikan status "member terkunci" persisten dan tanpa PII, misalnya boolean `efn_member_locked` di `res.partner` yang tidak bisa ditulis klien. Status ini diisi saat:
  - OTP pertama (semua purpose termasuk `pos_enroll`);
  - reservasi redeem;
  - outbox earn non-skipped.

  Status tidak pernah dihapus otomatis. Erasure (OdooBot) tetap lolos.
- Minimal, tambahkan `efn.pos.loyalty.reservation.partner_id` dan `pos.order` dengan `efn_loyalty_state != False` ke pencarian.
- Tambahkan aturan `efn_setup_audit` untuk perubahan HP oleh siapa pun, bukan hanya oleh manajer.

### SEC-116: Medium: Refund ke metode asal (E-31) bisa dilewati dengan menandai pembayaran sebagai kembalian

**Fakta:** `outgoing` = pembayaran negatif **selain** `is_change` (`efn_pos_refund_origin/models/pos_order.py:105-107`). Bila tidak ada `outgoing`, validasi selesai (`:124-125`). Field `is_change` adalah field tersimpan `pos.payment` yang ikut dalam payload `payment_ids` sync dari browser (PLAUSIBLE: CE 19 menerima nilainya dari klien; source inti tidak di repo).

**Skenario:** Order asal dibayar kartu Rp500.000. Kasir menyusun refund, misalnya lewat devtools, dengan pembayaran tunai −Rp500.000 `is_change: true`. Pembayaran itu tidak dinilai, refund tersimpan, dan tunai keluar dari laci untuk penjualan yang dananya masuk ke kartu. Ini pola fraud klasik yang justru menjadi tujuan E-31.

Untuk order asal berpoin, jalur loyalty hanya menahan restore poin (detektif, `pos_order.py:516-524`). Tunai tetap keluar.

**Rekomendasi:**
- Abaikan `is_change` untuk order yang punya baris `refunded_orderline_id` atau qty < 0, sehingga semua pembayaran negatif dinilai.
- Atau terima `is_change` hanya bila metodenya `is_cash_count` dan order punya tender positif ≥ |kembalian|.
- Tambahkan test sync dengan payload `is_change` palsu.

### SEC-117: Low: Race refund paralel atas order asal yang sama

**Fakta:** `_efn_refunded_by_method(exclude=self)` dan `_efn_loyalty_points_restored` membaca order refund lain yang **sudah** tersinkron. Dua sync paralel dari dua terminal berjalan di transaksi REPEATABLE READ yang tidak saling melihat. Keduanya menyisipkan baris berbeda tanpa konflik tulis pada order asal, sehingga keduanya lolos batas "sisa per metode".

Poin aman karena BFF membatasi restore di bawah kunci member (`pos-loyalty.service.ts:609-618`). Tunai/kartu tidak aman: total refund bisa melebihi yang dibayar.

**Rekomendasi:** di awal `_efn_refund_origin_validate`, untuk order asal tunggal, jalankan `SELECT id FROM pos_order WHERE id = %s FOR UPDATE` atau `pg_advisory_xact_lock` per order asal. Dengan begitu refund atas satu struk diserialkan (pola SEC-100).

### SEC-118: Low: Pertahanan berlapis K-18-3/K-18-6 tidak membandingkan apa pun yang independen

**Fakta:**
- `efn_pos_loyalty_outbox.py:326-328` mengirim `"payment_amount": reservation.value_idr`. Akibatnya cek BFF `payment_amount !== value_idr` (`pos-loyalty.service.ts:373-381`) tidak pernah gagal, dan anomali `commit_payment_mismatch` tidak bisa muncul.
- Rekonsiliasi restore memakai `points_restored_value` yang **dilaporkan Odoo** (`loyalty_recon.py:193-197`), bukan `points_restored` aktual hasil pembatasan BFF.

Karena validasi preventif Odoo ada, ini bukan celah langsung. Namun kontrak K-18-3 ("nilai baris pembayaran Poin EFN") tidak dipenuhi.

**Rekomendasi:**
- Kirim `round(order._efn_points_paid())` saat payload dibangun.
- Di rekonsiliasi, bandingkan juga `points_restored × LOYALTY_POINT_VALUE_IDR` (kolom sudah ada di `loyalty_pos_reverse`) dengan nilai pembayaran negatif Odoo. Selisih > sisa pembulatan = temuan.

### SEC-119: Low: Flag metode Poin EFN bisa dimatikan oleh POS Manager

**Fakta:**
- Semua deteksi server (`_efn_points_payments`) dan ETL (`m.efn_is_loyalty_points`) bergantung pada boolean `efn_is_loyalty_points`.
- Field itu tidak dibatasi grup. `pos.payment.method` dapat ditulis POS Manager.
- Constraint (`pos_payment_method.py:34-54`) hanya berjalan saat flag **True**.

Bila flag dimatikan:
- metode menjadi metode bank biasa tanpa reservasi/OTP;
- pembayaran historis hilang dari rekonsiliasi.

**Rekomendasi:**
- Batasi field dengan `groups="base.group_system"`.
- Tolak `write` yang mematikan flag, mengganti jurnal, atau mengarsipkan metode yang sudah punya `pos.payment`.
- Tambahkan aturan `efn_setup_audit` untuk `pos.payment.method`.

### SEC-120: Low: Rotasi API key tanpa koordinasi dengan deploy, dan key yatim

**Fakta:**
- `flock` hanya per kanal di `STATE_DIR` (`efn-rotate-online-key:135-136`). `deploy.sh` tidak mengambil kunci yang sama.
- Deploy yang berjalan bersamaan (`docker compose up -d`, migrasi, stop etl) dapat bertabrakan dengan `--force-recreate` milik rotasi (`:293`). Akibatnya: rollback, atau rc=3 "kanal online bisa mati".
- Odoo meng-commit key baru (`env.cr.commit()`) sebelum skrip mem-parse baris `NEW`. Bila parse gagal (`:418-420`), key 30 hari yang sah tertinggal tanpa dicabut sampai rotasi sukses berikutnya. Metrik `active_keys` = 2 akan terlihat.

**Rekomendasi:**
- Kunci host bersama `/run/efn/deploy.lock`, dipakai `deploy.sh`/`efn-deploy` dan skrip rotasi (`flock -w`).
- Pada kegagalan parse, cabut key yang dibuat run ini berdasarkan nama `efn-online-<KANAL>-<UTC>` lewat op `revoke` per nama.

### SEC-121: Low: Contact Creation untuk kasir lebih luas dari kebutuhan onboarding

**Fakta:** grup kasir mewarisi `base.group_partner_manager` (`pos_cashier_groups.xml:23`). Risiko nama/email/alamat sudah diterima di `docs/08` §9 (W-25). Dua hal belum disebut:
- (a) partner tanpa `company_id`, yang dipakai bersama semua PT, bisa diubah kasir PT mana pun;
- (b) **PLAUSIBLE**: ACL standar base memberi `group_partner_manager` hak tulis pada `res.partner.bank`, sehingga kasir bisa mengubah rekening vendor. Dampaknya diredam oleh mekanisme *trusted account* akuntansi CE, yang belum diverifikasi di 19.0.

**Rekomendasi:**
- Record rule write untuk grup kasir: hanya partner non-perusahaan, tanpa `supplier_rank`, dengan `company_id` di company user atau kosong.
- Rule `res.partner.bank` false untuk grup ini.
- Verifikasi ACL di image CI.

### SEC-122: Info: Modul E-31 bisa tidak terpasang

`efn_pos_refund_origin` hanya bergantung pada `point_of_sale`/`efn_core`. Modul ini tidak menjadi dependency `efn_setup_pos` dan tidak ada di runbook instalasi. Akibatnya tidak ada yang memaksa modul terpasang di produksi.

Rekomendasi: tambahkan ke `depends` `efn_setup_pos` (sejalan dengan `efn_pos_otp_gate`), atau buat cek pra-go-live yang gagal bila modul tidak `installed`.

### SEC-123: Info: Pagar earn BFF bergantung pada urutan outbox

`enforceMoneyPortion` keluar lebih awal bila reservasi order belum `committed` (`pos-loyalty.service.ts:653-654`). Outbox memproses berdasarkan `id` (commit lebih dulu), tetapi commit yang gagal sementara di-retry belakangan sehingga earn terkirim tanpa pagar. Faktor yang dihitung Odoo tetap benar, jadi ini hanya hilangnya satu lapis.

Rekomendasi:
- Earn menunggu `redeem_commit` order yang sama selesai, seperti reverse (`_efn_original_pending`).
- Atau BFF menjawab `earn_pending` (retryable) bila ada reservasi `reserved` untuk `order_ref` itu.

### SEC-124: Info: Rekonsiliasi K-18-6 kehilangan sisi BFF setelah erasure/purge

Erasure menghapus `platform.loyalty_reservation` milik subjek, termasuk yang `committed` (`erasure.service.ts:311`). Sweeper juga mem-purge reservasi non-`reserved` setelah retensi. Sisi Odoo (`pos.payment`) tetap ada, sehingga hari yang masih dalam jendela lookback memunculkan `points_payment_daily_mismatch` palsu.

Rekomendasi: saat erasure, pseudonimkan reservasi committed (`member_hash` → NULL/tombstone) alih-alih menghapusnya. Alternatifnya, catat `value_idr` redeem di tabel keuangan tanpa PII seperti `loyalty_pos_reverse`, dengan retensi 10 tahun (Q-P2).

---

## Yang sudah benar (kontrol yang ada)

**Poin EFN sebagai alat bayar (E-30)**
- Pembayaran poin dideteksi dari **metode** (`efn_is_loyalty_points`), bukan flag klien (SEC-12).
- Sync order paid ditolak (rollback) bila salah satu kondisi berikut:
  - pembayaran poin tanpa reservasi `reserved` untuk (config, uuid);
  - nominal ≠ `value_idr` (toleransi 0);
  - ada baris poin ≤ 0;
  - partner berbeda dari reservasi;
  - nilai > `redeem_max_pct` × total yang dihitung server dari harga & pajak;
  - Σ non-tunai > total, sehingga kembalian tidak bisa berasal dari poin;
  - ada baris produk poin lama.
- Draft tanpa reservasi aktif dibersihkan.
- Jenis order (jual/refund/retur tanpa struk/ambigu) ditentukan server dari baris (SEC-59). Retur tanpa struk dan refund campuran tidak boleh memakai metode poin.
- Refund poin hanya negatif, hanya bila order asal dibayar poin, dan ≤ sisa. Alokasi proporsional yang meleset > Rp1 masuk `hold` + review, tidak dikirim otomatis.
- BFF reverse:
  - restore dibatasi `redeemed − priorRestore` di bawah `lockMember`;
  - retry dengan nilai berbeda → `409 idempotency_conflict`.

  Tabel `loyalty_pos_reverse` append-only: `efn_bff` hanya SELECT/INSERT, `efn_readonly` hanya kolom non-PII.
- Batasan metode Poin EFN:
  - wajib jurnal bank, tanpa terminal/QR, tanpa split, satu metode aktif per company;
  - tidak ikut `_default_payment_methods`, hanya ditautkan ke POS redeem-aktif;
  - redeem dilarang di kiosk.
- Selisih tutup sesi pada metode poin ditolak.
- RPC baru (`efn_loyalty_refund_preview`, `efn_refund_origin_allocation`) terdaftar di `RPC_ALLOWLIST` dengan cek grup + `check_access` config + company + sesi terbuka + `check_access` order asal + company order asal. Respons tanpa data pelanggan.

**Refund ke metode asal (E-31)**
- Subset metode asal dan nominal per metode ≤ dibayar − refund sebelumnya.
- Satu order asal saja.
- Retur tanpa struk default tunai saja, dengan mode `deny` tersedia.
- Pelanggaran → `UserError` (sync ditolak).

**Pendaftaran member di POS (`pos_enroll`)**
- `age_confirmed` wajib `True` persis di Odoo dan BFF; Odoo hanya meneruskan `True` literal.
- Tidak ada token diskon.
- `account_deleting` hanya terlihat setelah OTP pemilik nomor.
- Rate limit per member & per pos_config dipakai bersama purpose POS lain.
- Kunci HP SEC-07/44 tetap aktif untuk partner yang memenuhi syarat (lihat SEC-115 untuk celahnya).

**Privasi (W-18/W-19, L-21/L-22)**
- Pra-cek `409 open_online_orders` dijalankan sebelum token step-up dikonsumsi.
- Konfirmasi tercatat (`open_orders_ack_at`).
- Kunci member yang sama dengan checkout dipakai saat INSERT `dsar_request` maupun di job erasure (SEC-100).
- Checkout ditolak selama permintaan hapus `scheduled`/`processing`.
- Penundaan maksimal N hari, lalu erasure dipaksa dengan alert critical tanpa PII (id bagian opak).
- Endpoint status tanpa sesi tetap berotorisasi receipt, menjawab 404 generik, dan di-rate-limit per IP. Isinya hanya jumlah bagian terbuka.
- Laporan CS `online-erasure-pending` read-only tanpa member_id/hash/HP.
- Data keuangan (ledger tombstone, `online_order` tanpa tautan, `loyalty_pos_reverse` 10 tahun) dipertahankan.
- `age_confirmed_at` tanpa tanggal lahir.

**Rotasi API key Odoo (W-11)**
- Nilai key tidak pernah lewat argv, log, atau journald:
  - alirannya hanya stdout `odoo shell` → variabel bash → file;
  - `BASH_REMATCH` ditimpa setelah dipakai;
  - stderr disaring hex ≥ 32.
- File secret ditulis atomik (mktemp di direktori yang sama → `mv`).
- Salinan key lama 0400 di direktori 0700, lalu di-`shred`.
- Verifikasi lewat mTLS ke gateway di setiap container.
- Rollback otomatis; pencabutan key lama dibuktikan dengan 401.
- `with_user` (bukan sudo), sehingga `api_key_duration` tetap ditegakkan.
- Menolak user teknis yang memegang grup admin.
- Konfigurasi tidak di-*source*, kunci di-allowlist, file harus milik root.
- `flock` per kanal; systemd dengan `NoNewPrivileges`/`ProtectSystem`.
- `deploy.sh` tidak menimpa secret `host:` dan `efn-deploy` menolaknya dari stdin.
- `base.group_user.api_key_duration = 0` (noupdate=0) menutup bypass 90 hari dari grup umum.

**Presisi IDR**
- Hook hanya `pre_init_hook` (instal baru) dan berhenti dengan WARNING bila IDR sudah dipakai jurnal. Tidak ada perubahan presisi di DB berjurnal.

**ClickHouse `loyalty_events`**
- Migrasi memakai salin → `EXCHANGE TABLES` (atomik) → salin sisa 1 jam → `DROP ... SYNC`, sehingga tabel lama berisi hash tidak tertinggal.
- `etl` dihentikan selama `clickhouse-init`.
- Erasure ETL baru melapor selesai setelah `count() = 0`. Mutation yang tertunda saat migrasi diulang pada tabel baru (self-healing).
- TTL kolom pseudonim terpisah dari TTL baris.

**Lain-lain**
- Timer housekeeping Metabase menjalankan skrip dari `/opt/efn/scripts`, yang ditulis hanya dari rilis bertanda tangan (`efn-deploy`, `chown root`).

## Yang belum terverifikasi

- Perilaku inti Odoo CE 19:
  - ACL `pos.payment` untuk `group_pos_user` (SEC-113);
  - penerimaan `is_change` dari payload sync (SEC-116);
  - ACL `res.partner.bank` untuk `group_partner_manager` dan proteksi *trusted account* (SEC-121).

  Verifikasi di image `efn-odoo:ci` dengan `call_kw` sebagai kasir.
- Tidak ada test/tour/skrip yang dijalankan reviewer. Klaim test di laporan agent G18 tidak diperiksa ulang.

---

## Re-review G18 (14 Okt 2026)

Cakupan: branch `chore/wave-18` HEAD terbaru, yang memuat perbaikan L-25 (`docs/04` §9.9): `e881ae1` (ETL), `a045a0a` (BFF), `e0d3f2e` (ops), `751dd89` (SEC-121), dan commit Odoo terakhir (SEC-113/115..119/122).

Dasar tambahan: laporan `docs/agent-reports/efn-odoo-engineer.md` bagian "Review r11" dan `efn-bff-engineer.md` bagian "Review r11 sisi BFF". Menurut laporan engineer, ketiga temuan PLAUSIBLE (ACL `pos.payment`, `is_change` dari klien, `res.partner.bank` untuk `group_partner_manager`) sudah dikonfirmasi lewat `call_kw` sebagai kasir di Odoo 19. Reviewer menerima konfirmasi itu, tetapi tidak menjalankannya ulang.

File yang dibaca ulang:
- Odoo:
  - `efn_core/scope.py`;
  - `efn_pos_refund_origin/{models/pos_order.py,models/pos_payment.py,__manifest__.py}`;
  - `efn_pos_loyalty_ledger/models/{pos_payment.py,pos_payment_method.py,pos_order.py:236-435}`, `efn_pos_loyalty_outbox.py` (payload commit), `migrations/19.0.2.1.0`;
  - `efn_pos_otp_gate/{models/res_partner.py,migrations/19.0.1.9.0}`;
  - `efn_setup_pos/{data/pos_cashier_groups.xml,models/res_partner.py}`.
- BFF: `pos/pos-loyalty.service.ts:500-715`, `pos/loyalty-reservation.sweeper.ts`, `privacy/erasure.service.ts:300-320`, `infra/postgres/init/08-platform-g18-privacy.sql`.
- ETL/ops: `services/etl/efn_etl/{config,loyalty_recon}.py`, `scripts/{deploy.sh,lib/deploy-validate.sh}`, `scripts/host/{efn-deploy,efn-rotate-online-key}`, `.github/workflows/deploy.yml`, `infra/compose/{docker-compose.yml,.env.example}`.

### Status temuan r11

| ID | Status | Bukti (file:line) | Catatan |
|---|---|---|---|
| SEC-113 | **Fixed** (dengan residual SEC-125/126) | `efn_pos_refund_origin/models/pos_payment.py:27-61`; `efn_pos_loyalty_ledger/models/pos_payment.py:18-44`; `efn_core/scope.py:22-42`; scope di `refund_origin/models/pos_order.py:207-210` & `loyalty_ledger/models/pos_order.py:556-558` | Create/write field finansial/unlink `pos.payment` order non-draft ditolak di luar jalur sync. Poin EFN ditolak juga untuk draft. Penanda disimpan di atribut cursor, bukan context RPC. |
| SEC-114 | **Fixed** | `config.py:57-65,151`; `loyalty_recon.py:616`; `docker-compose.yml:1536`; `deploy.yml:268`; `deploy.sh:323`; `deploy-validate.sh:173-177` | Default `payment` di semua jalur. Mode `product` ditolak di production oleh ETL maupun deploy-validate. |
| SEC-115 | **Fixed** | `efn_pos_otp_gate/models/res_partner.py:73-91,141-172`; penandaan di `efn_pos_otp_gate/models/pos_config.py:205`, `loyalty_ledger/models/pos_config.py:318`, `pos_order.py:431`; migrasi `otp_gate 19.0.1.9.0`, `loyalty 19.0.2.1.0` | Penanda `efn_member_locked_at` tahan lama dan hanya bisa ditulis server. HP member hanya bisa diubah `base.group_system`. Kasir kini juga tanpa hak write partner (SEC-121). Lihat analisis pemalsuan/backfill di bawah. |
| SEC-116 | **Fixed** (selama order melewati `_process_saved_order`, lihat SEC-125) | `refund_origin/models/pos_order.py:105-140,148,203-204` | `is_change` hanya sah untuk tunai negatif yang tertutup tender tunai. Untuk refund, allowance = 0. |
| SEC-117 | **Reopened → SEC-127** | `refund_origin/models/pos_order.py:142-144,173`; `loyalty_ledger/models/pos_order.py:322-328` | `SELECT … FOR UPDATE` saja tidak cukup di REPEATABLE READ. Lihat SEC-127. |
| SEC-118 | **Partial** | Odoo: `efn_pos_loyalty_outbox.py:329` (`payment_amount = round(_efn_points_paid())`). BFF: `pos-loyalty.service.ts:525,589-617` (expected restore independen) | Cek commit K-18-3 kini bermakna. Sisa: `loyalty_recon.py` belum membandingkan `points_restored × nilai poin` (grep `points_restored`: nihil). Cek BFF tetap memakai `refund_ratio` kiriman Odoo (lihat SEC-128). |
| SEC-119 | **Fixed** | `pos_payment_method.py:60-110` | Flag hanya diubah admin. Flag, jurnal, dan arsip metode berpembayaran terkunci. Residual Info: `outstanding_account_id` metode Poin EFN berpembayaran masih bisa diubah POS Manager (pemetaan akun; tercatat audit bila aturan `efn_setup_audit` ditambahkan). |
| SEC-120 | **Fixed** | `deploy.sh:60-67`; `efn-deploy:327-328`; `efn-rotate-online-key:384-405,444,452-471,200-206` | Lock bersama `/run/efn/deploy.lock` (dir 0700 root, `flock -w`), dipegang selama fase compose/secret/verifikasi dan dilepas selama overlap. Key yatim dicabut per nama berversi yang divalidasi regex. |
| SEC-121 | **Fixed** | `efn_setup_pos/data/pos_cashier_groups.xml:28-66`; `models/res_partner.py:21-27` | Grup tidak lagi mewarisi `group_partner_manager`. ACL hanya read+create; rule create hanya individu tanpa induk dengan `company_id` = company aktif. `res.partner.bank` tetap read-only, sehingga nested create `bank_ids`/`child_ids` ditolak ACL/rule. |
| SEC-122 | **Partial** | `efn_pos_refund_origin/__manifest__.py:28-30` (`auto_install: True`) | DB baru: aman. DB lama yang sudah punya `point_of_sale` + `efn_core`: `auto_install` tidak selalu memasang modul baru saat `-u` (PLAUSIBLE). Cek `state = installed` di staging, lalu jadikan cek pra-go-live. |
| SEC-123 | **Fixed** | `pos-loyalty.service.ts:688-696` | Earn ditunda (`409 earn_pending`, retryable) selama reservasi order masih `reserved` dan `hold_until` belum lewat. |
| SEC-124 | **Fixed** | `erasure.service.ts:314-315`; `loyalty-reservation.sweeper.ts:84-89`; `08-platform-g18-privacy.sql:94` | Reservasi committed dipertahankan dengan tombstone acak; retensi committed minimal 3650 hari; grant hanya `UPDATE (member_hash)`. |

### Analisis fokus re-review

**1. Bypass penanda `efn_core/scope.py`**
- Atribut cursor tidak bisa diisi lewat context, `/web/dataset/call_kw`, atau `/json/2`. `_process_order`/`_process_saved_order` berawalan `_` sehingga tidak bisa dipanggil lewat RPC. Penanda dibersihkan di `finally` dan pencacahnya aman bila bersarang. Sampai di sini benar.
- Kelemahannya, penanda berlaku untuk **seluruh** isi `_process_order`, bukan hanya untuk pembayaran milik order yang sedang disinkron. Lihat **SEC-126**.
- Guard juga dilewati oleh `env.su`, artinya semua kode server ber-`sudo()`. Di addon efn tidak ada jalur sudo yang menulis `pos.payment` dari input klien (grep: nihil). Jalur sudo inti, misalnya pembayaran online/kiosk, memerlukan transaksi provider nyata.

**2. Jalur lain ke `pos.payment` dan status order**
- `account.payment` dan wizard tutup sesi (`close_session_from_ui` / `_validate_session`) hanya membuat jurnal, pembayaran akuntansi, dan selisih kas. Keduanya tidak mengubah `amount`/`payment_method_id` `pos.payment`. Selisih metode Poin EFN tetap ditolak (`pos_session.py`).
- `pos.make.payment` (wizard "Payment" di backend order), RPC publik `pos.order.add_payment`, `pos.order.action_pos_order_paid`, dan `pos.order.write({'state': 'paid'})` adalah jalur yang **tidak** melewati `_process_saved_order`. Lihat **SEC-125**.

**3. `efn_member_locked_at`: pemalsuan dan backfill**
- **Menghapus atau mengosongkan:** ditolak untuk non-superuser (`res_partner.py:89-91`). Kasir juga tidak punya hak write partner (SEC-121).
- **Mengisi lewat `create`:** `create` tidak dijaga, jadi pembuat partner bisa mengisi penanda sendiri. Dampaknya hanya over-lock (HP partner baru terkunci), tanpa keuntungan. Info, tidak dicatat sebagai temuan.
- **Backfill:**
  - Migrasi hanya berjalan saat upgrade (`if not version: return`) dan memakai `now()`, bukan tanggal riwayat. Selisih tanggal ini kosmetik.
  - Member yang riwayatnya sudah dibersihkan sebelum upgrade (challenge > 30 hari tanpa reservasi atau order loyalty) tidak ter-backfill.
  - Order dengan earn `skipped` (HP asing) juga ikut ditandai. Ini over-lock dan aman.
  - Karena belum ada produksi, dampaknya nihil. Untuk staging lama, cukup jalankan cek manual.
- **Merge partner:** wizard merge memerlukan hak write partner, yang kini tidak dimiliki kasir.

**4. Grup kasir sempit vs `company_id` multi-company**
- Rule `('company_id', '=', company_id)` dievaluasi terhadap company aktif, yang selalu berada di `company_ids` user. Partner bersama (company kosong) dan partner PT lain ditolak.
- Efek fungsional: pelanggan yang didaftarkan di PT Retail tidak terlihat oleh POS PT F&B (rule global multi-company), sehingga member perlu didaftarkan ulang per PT. Identitas poin tetap nomor HP, jadi tidak ada risiko keamanan. Catat sebagai keputusan fungsional (duplikasi partner per PT).
- Field `property_product_pricelist` saat create: kasir bisa memberi pricelist apa pun yang tersedia di POS ke pelanggan baru. Risiko ini setara dengan tombol Pricelist POS yang sudah dikenal (R-B06), jadi tidak ada temuan baru.

**5. Toleransi 10 % restore BFF:** lihat **SEC-128**.
- Over-restore tetap dibatasi `left` (sisa poin redeem) dan validasi Odoo Rp1. Dampak maksimum per refund adalah kelebihan ≤ 10 % × nilai redeem, dan itu hanya terjadi bila payload Odoo/outbox dipalsukan (pemegang service key).
- Masalah yang lebih nyata adalah positif palsu pada refund barang yang dikecualikan dari earn.

**6. Lock host deploy/rotasi**
- `/run/efn` dibuat 0700 root, sehingga pengguna non-root tidak bisa menanam symlink.
- Lock dilepas selama overlap 1 jam, jadi deploy tidak tertahan.
- Pewarisan fd 8 dari `efn-deploy` memakai pemeriksaan `readlink /proc/$$/fd/8`, lalu `flock -n`. Bila deskripsi bukan milik pemegang lock, `-n` gagal. Implementasi benar.

### Temuan baru

| ID | Severity | Verdict | Judul | Lokasi utama | Risiko |
|---|---|---|---|---|---|
| SEC-125 | **Medium** | PLAUSIBLE (metode publik & isi wizard inti CE 19); ketiadaan guard efn CONFIRMED | Order bisa menjadi `paid` tanpa `_process_saved_order`, sehingga E-30, E-31, SEC-116, dan gerbang OTP promo tidak berjalan | Tidak ada override `action_pos_order_paid`/`pos.order.write(state)` di addon efn (grep); `loyalty_ledger/models/pos_order.py:236-254` (draft tanpa cek nominal); `refund_origin/models/pos_payment.py:30-31` (draft dikecualikan) | R-S10, R-B05, R-B06 |
| SEC-126 | **Medium** | PLAUSIBLE (perintah x2many inti tanpa cek kepemilikan) | Penanda sync berlaku untuk seluruh `_process_order`. Perintah `payment_ids` di payload yang menunjuk pembayaran order lain (`[1,id,…]`, `[2,id]`, `[4,id]`) lolos guard SEC-113 | `efn_core/scope.py:22-42`; `refund_origin/models/pos_payment.py:27-28`; `loyalty_ledger/models/pos_payment.py:18-19` | R-S10 |
| SEC-127 | Low | CONFIRMED (semantik PostgreSQL) | (= SEC-117 reopened) `SELECT … FOR UPDATE` dalam transaksi REPEATABLE READ Odoo tidak melihat refund paralel yang baru di-commit. Race tetap ada | `refund_origin/models/pos_order.py:142-144`; `loyalty_ledger/models/pos_order.py:322-324` | R-S10 |
| SEC-128 | Low | CONFIRMED | Cek restore BFF memakai `refund_ratio` berbasis **eligible** (kiriman Odoo), sedangkan restore Odoo berbasis nilai incl pajak. Refund barang yang dikecualikan dari earn ditolak final `409 restore_value_mismatch`. Toleransi 10 % lebar dan rasio tidak independen | `pos-loyalty.service.ts:589-617`; `loyalty_ledger/models/pos_order.py:485-508` (`_efn_loyalty_refund_ratio`); `efn_pos_loyalty_outbox.py:65` (`RETRYABLE_REASONS`) | R-B05 |
| SEC-129 | Info | PLAUSIBLE | `auto_install` (SEC-122) tidak menjamin modul terpasang di DB lama | `efn_pos_refund_origin/__manifest__.py:30` | R-S10 |

**Rekap re-review: 0 Critical · 0 High · 2 Medium · 2 Low · 1 Info.**

#### SEC-125: Medium: Order dibayar di luar jalur sync tidak divalidasi

**Fakta:**
- Seluruh validasi dan pemrosesan E-30/E-31/SEC-116 (dan gerbang OTP promo `efn_pos_otp_gate`) hanya ada di `_process_saved_order`.
- Guard SEC-113 sengaja mengizinkan perubahan pembayaran order **draft** (`refund_origin/models/pos_payment.py:30-31`). Untuk draft, guard Poin EFN hanya menolak pembuatan baris poin di luar sync. Draft yang **di-sync** dengan reservasi aktif tetap boleh membawa baris Poin EFN bernominal **berapa pun**, karena `_efn_loyalty_strip_draft` (`pos_order.py:236-254`) hanya mengecek keberadaan reservasi.
- Addon efn tidak meng-override `action_pos_order_paid` maupun `write` status `pos.order` (grep `def action_pos_order_paid|def write` di `models/pos_*.py`: hanya `pos.payment`/`pos.payment.method`/`pos.config`).

**Asumsi (PLAUSIBLE, source inti tidak di repo):**
- `pos.order.action_pos_order_paid` dan `pos.order.add_payment` adalah metode publik, sehingga bisa dipanggil `call_kw` oleh `group_pos_user` yang memiliki hak write `pos.order`.
- Wizard `pos.make.payment.check()` memanggil `action_pos_order_paid()`, bukan `_process_saved_order`.
- `pos.order.write({'state': 'paid'})` tidak dijaga inti.

**Skenario A (Poin EFN):**
1. Kasir yang juga member meminta OTP tukar poin ke HP-nya sendiri atas order pelanggan (reservasi Rp10.000).
2. Kasir sync order sebagai **draft** dengan baris Poin EFN Rp500.000. Baris itu lolos karena reservasi ada.
3. Pelanggan membayar Rp500.000 tunai, tetapi kasir tidak mencatatnya.
4. Kasir memanggil `pos.order.action_pos_order_paid([id])`. Order menjadi paid tanpa validasi nominal dan tanpa outbox `redeem_commit`.
5. Reservasi kedaluwarsa sehingga poin kasir kembali. Kasir mengantongi Rp500.000. Rekonsiliasi K-18-6 (sekarang aktif, SEC-114) akan mendeteksinya keesokan hari.

**Skenario B (E-31):**
1. Kasir membuat refund dari backend (tombol Refund → order refund draft).
2. Kasir membayarnya lewat wizard Payment dengan metode **Tunai**, padahal order asal dibayar kartu.
3. Order menjadi paid tanpa `_efn_refund_origin_validate`.

**Skenario C:** order draft dengan reward promo ber-OTP dibuat paid tanpa konsumsi token (gerbang R-B06 terlewati).

**Rekomendasi:**
- Override `action_pos_order_paid` (dan `_process_saved_order` tetap) agar validasi E-30/E-31/SEC-116 serta `_efn_loyalty_process_paid`/konsumsi OTP berjalan tepat sekali, apa pun jalurnya. Cara sederhana: pindahkan validasi ke `action_pos_order_paid` yang dipanggil `_process_saved_order` inti.
- Tolak `pos.order.write` atas `state` (ke `paid`/`done`/`invoiced`), `amount_paid`, dan `amount_return` untuk non-superuser di luar penanda sync atau di luar `action_pos_order_paid` itu sendiri.
- Di `_efn_loyalty_strip_draft`, tolak atau hapus baris Poin EFN yang nominalnya ≠ `value_idr` reservasi.
- Test `call_kw` kasir untuk ketiga skenario.

#### SEC-126: Medium: Penanda sync terlalu luas, perintah x2many atas pembayaran order lain

**Fakta:**
- `internal_scope(POS_PAYMENT_SYNC_SCOPE)` aktif selama seluruh `_process_order`. Kedua guard `pos.payment` mengembalikan "tidak aktif" untuk **semua** record dalam rentang itu (`pos_payment.py:27-28` dan `18-19`), tanpa mengecek bahwa record tersebut milik order yang sedang disinkron.

**Asumsi (PLAUSIBLE):**
- `_process_order` CE 19 menulis `payment_ids` dari payload browser lewat ORM.
- Perintah One2many `UPDATE`/`DELETE`/`LINK` (`[1, id, vals]`, `[2, id]`, `[4, id]`) menjalankan `write`/`unlink` pada id mana pun tanpa memeriksa kepemilikan oleh order induk.

**Skenario:** kasir menyusun payload sync order baru (draft atau paid) yang memuat `payment_ids: [[1, <id pembayaran tunai order paid lain>, {"payment_method_id": <Poin EFN>}]]`. Guard dilewati karena berada di scope. Order lain itu tidak divalidasi ulang. Hasilnya sama dengan SEC-113 sebelum perbaikan.

**Rekomendasi:**
- Simpan id order yang sedang disinkron di dalam scope (atau uuid sebelum create).
- Di guard, untuk `write`/`unlink`, izinkan hanya record yang `pos_order_id`-nya berada dalam himpunan itu. Untuk `create`, izinkan hanya `pos_order_id` dalam himpunan atau kosong selama create order.
- Alternatif: sanitasi `order['payment_ids']` di awal `_process_order` sehingga hanya `Command.CREATE` yang diterima.
- Tambahkan probe `call_kw sync_from_ui` dengan perintah `[1, id_asing, …]`.

#### SEC-127: Low: Kunci order asal tidak efektif di REPEATABLE READ (SEC-117 reopened)

**Fakta:**
- Odoo membuka transaksi dengan isolasi REPEATABLE READ. Snapshot terbentuk pada query pertama permintaan, jauh sebelum `_efn_lock_origin`.
- Bila transaksi A memegang `FOR UPDATE` lalu commit tanpa **mengubah** baris order asal (refund hanya menyisipkan order/baris/pembayaran baru, dan FK ke baris asal hanya mengambil `FOR KEY SHARE`), transaksi B yang menunggu memperoleh kunci tanpa serialization error (dokumentasi PostgreSQL: rollback hanya bila baris *di-update/di-delete*, bukan hanya dikunci).
- Transaksi B kemudian membaca "refund sebelumnya" dari snapshot lama, sehingga race tetap terjadi.
- Test spy (`test_payment_integrity.py:169-193`) hanya memastikan SQL dipanggil.

**Rekomendasi:**
- Ganti dengan `UPDATE pos_order SET write_date = write_date WHERE id = %s` (atau UPDATE kolom penanda) di kedua modul.
- Transaksi kedua akan gagal dengan `SerializationFailure`, lalu Odoo mengulang RPC (`service.model.retrying`) dengan snapshot baru dan menolak refund berlebih.
- Uji dengan dua koneksi nyata (skrip staging), bukan spy.

#### SEC-128: Low: Cek restore BFF berbasis rasio eligible, toleransi 10 %

**Fakta:**
- `assertExpectedRestore` menghitung `expected = V × min(1, Σ refund_ratio) − Σ nilai sebelumnya`, dengan `refund_ratio` yang **dikirim Odoo**.
- Di Odoo, `_efn_loyalty_refund_ratio` (`pos_order.py:485-508`) berbasis subtotal **eligible** (tanpa kategori yang dikecualikan). Untuk refund yang hanya berisi barang dikecualikan (misalnya rokok) atas order yang juga punya barang eligible, rasionya = 0. Sementara itu Odoo mengembalikan poin secara proporsional terhadap nilai incl pajak (> 0).
- Selisih > max(nilai poin, 10 % × V) → `409 restore_value_mismatch`. Kode ini tidak masuk `RETRYABLE_REASONS` outbox (`efn_pos_loyalty_outbox.py:65`), sehingga outbox `failed` dan order masuk review. Refund sah gagal otomatis.
- Sebaliknya, karena rasio berasal dari Odoo, pemalsu payload bisa menyelaraskan rasio dan nilai. Toleransi 10 % hanya menambah ruang. Kerugian dibatasi `left` dan validasi Odoo Rp1.

**Rekomendasi:**
- Odoo mengirim rasio nilai barang incl pajak (`refund_value_ratio`) terpisah dari rasio clawback eligible. BFF memakainya untuk cek restore dengan toleransi beberapa Rupiah per baris (atau ≤ 1 nilai poin + 1 %).
- Turunkan `LOYALTY_RESTORE_TOLERANCE_PCT` setelah itu.
- Tambahkan test refund kategori dikecualikan.

#### SEC-129: Info: `auto_install` pada DB lama

Untuk DB yang sudah memasang `point_of_sale` + `efn_core` sebelum modul ini ada, `-u` tidak selalu memasang modul `auto_install` baru (PLAUSIBLE, perilaku loader inti).

Rekomendasi:
- Cek `ir_module_module.state = 'installed'` untuk `efn_pos_refund_origin` di skrip pra-go-live/`deploy-validate`.
- Atau tambahkan modul ini ke `depends` `efn_setup_pos`.

### Yang sudah benar (perbaikan L-25)

**Kontrol pembayaran dan restore poin**
- Guard `pos.payment` berlapis: umum untuk order non-draft, ditambah khusus Poin EFN yang berlaku juga untuk draft.
- Penanda sync tidak bisa diisi dari RPC. Pesan galat tidak membocorkan detail.
- `payment_amount` commit memakai nilai aktual. Cek BFF K-18-3 kini bermakna.
- BFF menolak restore yang tidak konsisten, tanpa entri ledger (rollback), dengan anomali critical.
- Earn ditunda selama commit belum masuk.

**Kunci member dan grup kasir**
- Kunci HP member tahan lama dan hanya bisa diubah admin.
- Grup kasir sempit: tanpa write/unlink partner, tanpa rekening bank, partner terikat ke company aktif.

**Privasi dan retensi**
- Reservasi committed dipertahankan secara pseudonim (tombstone acak) selama 10 tahun. Erasure tetap memutus tautan ke member.

**Operasional**
- Mode rekonsiliasi `payment` sebagai default dan dikunci di production.
- Lock host bersama deploy/rotasi. Key yatim dicabut berdasarkan nama tervalidasi.

### Rekomendasi GO/NO-GO

**GO bersyarat untuk merge `chore/wave-18` → `main`.** Alasannya:
- Perbaikan L-25 menutup seluruh Medium r11.
- Celah SEC-125/126 bukan regresi; jalur yang sama sudah ada di `main` (G13: validasi produk poin juga hanya di `_process_saved_order`).
- Belum ada produksi.

**NO-GO untuk mengaktifkan tukar Poin EFN (`efn_loyalty_redeem_enabled`) dan mengandalkan E-31 di outlet pilot** sampai syarat berikut terpenuhi:
- SEC-125 dan SEC-126 Fixed. Keduanya perlu dikonfirmasi dulu dengan probe `call_kw` kasir, seperti yang sudah dilakukan untuk SEC-113.
- SEC-127 diganti dengan UPDATE + retry serialisasi.
- SEC-122/129 diverifikasi `installed` di DB target.

SEC-118 (sisi ETL) dan SEC-128 boleh menyusul sebelum go-live.
