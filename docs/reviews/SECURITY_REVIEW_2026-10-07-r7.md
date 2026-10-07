# Security & Compliance Re-review (r7): Gelombang 13c, Fase 1 (2026-10-07)

Reviewer: `efn-security-reviewer` (read-only terhadap kode). Re-review atas `SECURITY_REVIEW_2026-10-07-r6.md` setelah Gelombang 13c (SEC-58 High, SEC-59..63, SEC-65, SEC-61 batas earn + rekonsiliasi BFF, sisa SEC-54, stream Loki `efn_loyalty`). Basis: `main` @ `42b6378`.

Cakupan yang dibaca:
- Odoo: `efn_core/models/efn_bff_client.py`, `efn_core/models/ir_http.py`, `efn_core/tests/test_rpc_surface.py`, `efn_pos_otp_gate/models/pos_config.py`, `efn_pos_loyalty_ledger/models/{pos_order,pos_config,efn_pos_loyalty_outbox}.py`, `efn_pos_loyalty_ledger/security/*`, `efn_pos_otp_gate/security/*`, `efn_privacy/models/efn_privacy_erasure.py`, `efn_privacy/security/*`, `efn_setup_pos/data/barcode_hardening_data.xml`, grep semua `def <publik>` di `efn_*/models`.
- BFF: `pos/pos-loyalty.service.ts` (earn/reserve/commit/release/reverse, `enforceEarnLimits`), `pos/loyalty-recon.service.ts`, `pos/dto/pos-loyalty.dto.ts`, `loyalty/loyalty-anomaly.ts`, `common/pii/erasure-suppression.ts`, `privacy/erasure.service.ts` (suppression), `migration/*`, `auth/altcha.service.ts`, `payments/payments.service.ts` (SEC-33), `config/app-config.ts`, grep semua pemanggilan `logger.*` berinterpolasi.
- Infra: `infra/postgres/init/06-platform-loyalty-guard.sql`, `infra/observability/{promtail.yml, loki-rules/efn-loyalty.yml, alert-rules.yml, alertmanager.yml}`, `scripts/check-odoo-deps.sh`.
- Dokumen: `docs/04` §9.8 (v4.3), laporan agent bagian Gelombang 13c (odoo-engineer, bff-engineer, devsecops, qa), `docs/testing/E2E_SMOKE_REPORT.md` §G13c.

Metode: membaca kode dan konfigurasi. **Tidak ada yang dijalankan oleh reviewer.** Bukti runtime dikutip dari laporan QA G13c (smoke 153/153, uji RPC nyata via `call_kw`). Perilaku yang belum pasti ditandai **PLAUSIBLE**.

## Ringkasan eksekutif (untuk manajemen)

1. **Temuan High SEC-58 sudah tertutup dan terverifikasi di stack nyata.** Kasir maupun user Odoo biasa tidak bisa lagi memanggil klien BFF lewat RPC. Gerbang CI baru (`test_rpc_surface.py`) menggagalkan build bila ada metode publik baru di modul `efn_*` yang belum ditinjau.
2. **Status 66 temuan lama: 51 Fixed, 9 Partial, 6 Open, 0 Regressed.** Tiga celah fraud poin r6 (SEC-59 retur menghasilkan poin, SEC-60 refund mengembalikan poin, SEC-62 penukaran poin tidak sah) kini ditutup secara **preventif**: transaksi ditolak atau ditahan, bukan hanya ditandai. SEC-61 kini punya batas harian (2.000 poin ≈ Rp200.000 per member), batas per PT/kasir per jam, rekonsiliasi per jam, dan alert. Sisanya: rekonsiliasi ke order Odoo belum ada.
3. **Ada 5 temuan baru: 0 Critical, 0 High, 0 Medium, 2 Low, 3 Info.** Dua yang Low:
   - Keputusan supervisor atas refund yang ditahan hanya punya dua pilihan "kembalikan semua poin" atau "batalkan semua", dan tidak memisahkan peran pelaku refund dari penyetuju (SEC-67).
   - Retur tanpa struk tanpa memilih pelanggan tidak menarik kembali poin pembelian asal dan tidak ditandai (SEC-68).
4. **Rekomendasi: GO bersyarat** untuk mengaktifkan `efn_loyalty_earn_enabled` dan `efn_loyalty_redeem_enabled` di **satu outlet pilot**, dengan 7 syarat di bagian "Rekomendasi go/no-go". Syarat terpenting:
   - uji `/json/2` di staging;
   - inventaris PDP v4;
   - pembatasan `base.group_system` dan `group_pos_manager`;
   - review harian filter "Review Poin".

   Tidak ada temuan terbuka yang memungkinkan orang luar atau kasir mencetak poin tanpa batas.

## Status temuan lama (SEC-01..SEC-66)

| ID | Sev. awal | Status r7 | Bukti utama (file:line) | Catatan sisa |
|---|---|---|---|---|
| SEC-01..10 | High/Medium | **Fixed** | tidak berubah sejak r6 | SEC-03 PLAUSIBLE runtime |
| SEC-11 | Medium | **Partial** | `docs/compliance/` grep `loyalty_reservation\|loyalty_pos_earn\|erasure_suppression\|efn.pos.loyalty` = 0 | DPO, koreksi/ganti nomor, inventaris G13/G13c |
| SEC-12..32 | Low/Medium/Info | **Fixed** | tidak berubah | — |
| SEC-33 | Medium | **Open** | `payments.service.ts:221` (claim sebelum intent ditemukan), `:274` (cek nominal dilewati bila `amount` null) | blocker sebelum vendor payment aktif (tidak terkait loyalty) |
| SEC-34..37 | Low | **Fixed** | — | — |
| SEC-38 | Low (PLAUSIBLE) | **Open** | — | DPO (Q-P11) |
| SEC-39..42 | Info/Low/Medium | **Fixed** | — | — |
| SEC-43 | Medium | **Partial** | `efn_setup_security` grep `group_system` = 0 | butir 5 (keputusan klien). Admin sistem masih dapat mencetak poin lewat server action ≤ batas SEC-61 (lihat SEC-61) |
| SEC-44..46 | Low/Info | **Fixed** | — | — |
| SEC-47 | Info | **Partial** | `scripts/check-odoo-deps.sh:24` masih `efn_*/__manifest__.py` saja | tidak berubah |
| SEC-48 | Info | **Partial** | tidak ada controller baru di 13c | tetap daftar statis |
| SEC-49, 50 | Medium | **Fixed** | tidak berubah | — |
| SEC-51 | Low (PLAUSIBLE) | **Partial** | `services/etl/efn_etl/sink.py` masih hanya diimpor `tests/test_sink.py` | verifikasi `mac` belum di-wire, escape newline global belum ada |
| SEC-52 | Low | **Partial** | tidak berubah | butir 3–5 |
| SEC-53 | Low | **Fixed** | — | — |
| SEC-54 | Low | **Fixed** | `auth/altcha.service.ts:52-55,84-98` (level dari rasio gagal ALTCHA/verify per IP, ambang 30/50/75 %, sampel min `ALTCHA_FAIL_MIN_SAMPLE`), `auth.service.ts:74-94` | kuota khusus CGNAT tidak dibuat. Rasio gagal sudah membedakan CGNAT dari bot (`auth.service.spec.ts:297`), jadi diterima |
| SEC-55 | Info | **Open** | tidak berubah | backlog |
| SEC-56 | Info | **Fixed** | — | — |
| SEC-57 | Info | **Open** | tidak berubah | backlog |
| **SEC-58** | **High** | **Fixed** | `efn_core/models/efn_bff_client.py:265-302` (`_efn_post`/`_efn_get` privat; tidak ada metode publik di model). Grep `def [a-z]` di `efn_*/models` hanya menghasilkan metode di allowlist + override CE/ORM. `test_rpc_surface.py:122-149` (AST: AbstractModel `efn.*` = 0 metode publik; metode publik wajib ada di allowlist), `:173-189` (registry nyata `get_public_method`), `:222-238` (HttpCase `call_kw` ditolak, `requests` tidak terpanggil). Runtime: QA G13c (a) kasir & user internal → `_efn_post/_efn_get/post/get` ditolak lewat Traefik | `/json/2` belum diuji nyata (memakai `get_public_method` yang sama, sehingga tercakup gerbang registry). Lihat SEC-70 untuk batas gerbang |
| SEC-59 | Medium | **Fixed** | `efn_pos_loyalty_ledger/models/pos_order.py:137-161` (`_efn_loyalty_order_kind` dari `refunded_orderline_id`/qty, `is_refund` hanya menambah kecurigaan), `:184-196` (tanpa `abs()`, earn hanya bila dasar > 0), `:308-320` (retur tak tertaut/ambigu → review, tanpa earn/reverse). Runtime QA G13c (c) | sisa: retur tanpa struk **tanpa pelanggan** tidak ditandai (SEC-68) |
| SEC-60 | Medium | **Fixed** | `pos_order.py:466-492` (mismatch → outbox `hold` + review + aktivitas), `efn_pos_loyalty_outbox.py:204-212` (cron hanya `state='pending'`), `:136-173` (keputusan hanya POS manager + record rule + company) | keputusan biner tanpa pemisahan tugas (SEC-67). Field `restore_points` di kontrak belum ada |
| SEC-61 | Medium | **Partial** | `pos-loyalty.service.ts:219-220,560-591` (plafon/order 5.000, harian member 2.000 di bawah kunci member, PT/jam 50.000, kasir/jam 10.000 → `409` + `EFN_LOYALTY_ANOMALY`), `app-config.ts:852,859`, `loyalty-recon.service.ts:137-255` (5 cek, READ ONLY, satu replika), `loki-rules/efn-loyalty.yml:18-67`, `alert-rules.yml:174-178`. Runtime QA G13c (b) | **belum:** (1) rekonsiliasi ledger ↔ `pos.order` Odoo (ETL; recon BFF sendiri mengakui tidak bisa melihat order fiktif, `loyalty-recon.service.ts:63-65`); (2) alert untuk order Odoo `review`/outbox `hold`/`failed` dan saldo negatif; (3) admin `base.group_system` tetap dapat memanggil `_efn_post` dari server action (PLAUSIBLE: `safe_eval` hanya melarang nama dunder), dibatasi plafon harian/PT |
| SEC-62 | Low | **Fixed** | `pos_order.py:253-306` (`UserError` sebelum langkah standar untuk baris poin ≥ 0, nilai ≠ `value_idr` termasuk `price_subtotal_incl` tersimpan, partner beda, > `redeem_max_pct` × barang hitung server, total < 0), `pos_config.py:312` (`redeem_max_pct` disimpan di reservasi) | reservasi lama sebelum upgrade (`redeem_max_pct` = 0) → fallback 100 % (`pos_order.py:301`), transisi saja. Belum ada tour POS untuk kasus tolak sync |
| SEC-63 | Low | **Partial** | `efn_setup_pos/data/barcode_hardening_data.xml:17-21` (`noupdate=0`: semua `barcode.rule` `discount` → aturan produk `22`), filter audit barcode `23`, README risiko PIN | aturan diskon yang dibuat admin setelah upgrade baru dinetralkan pada upgrade berikutnya. Barcode `23` masih tanpa `restrict_price_control`. PIN `pos_hr`, batas selisih kas, void, refund ke metode lain masih di browser (Q-27) |
| SEC-64 | Low | **Open** | grep `MB_ENCRYPTION_SECRET_KEY` di `infra/` = 0 | tidak berubah |
| SEC-65 | Low | **Partial** | Odoo: `efn_privacy_erasure.py:256-287` (challenge & reservasi: `partner_id`/`member_ref_masked`/token dikosongkan; outbox `pending`/`hold` → `skipped member_erased`). BFF: `erasure.service.ts:247-249`, `common/pii/erasure-suppression.ts:13,30`, `06-...sql:31-46`. Runtime QA G13c (d) | `PDP_DATA_INVENTORY.md` belum v4. Suppression 90 hari = placeholder DPO |
| SEC-66 | Info | **Open** | tidak berubah | backlog |

**Rekap:** Fixed 51 · Partial 9 (SEC-11, 43, 47, 48, 51, 52, 61, 63, 65) · Open 6 (SEC-33, 38, 55, 57, 64, 66) · Regressed 0.

## Temuan baru

| ID | Severity | Judul | Lokasi utama | Risiko |
|---|---|---|---|---|
| SEC-67 | Low | Keputusan outbox `hold` biner tanpa pemisahan tugas: *Setujui* = restore poin penuh, *Batalkan* = clawback earn juga hilang; penyetuju bisa pelaku refund | `efn_pos_loyalty_outbox.py:136-173`, `pos_order.py:466-492` | R-B06, R-O05 |
| SEC-68 | Low | Retur tanpa struk tanpa pelanggan: earn order asal tetap utuh, tidak ada review/flag | `pos_order.py:308-333` | R-B06 |
| SEC-69 | Info | Earn: batas harian dapat dimatikan (`0`) tanpa pagar deploy; batas kasir/jam opsional; rotasi `company_code` oleh pemanggil langsung melipatgandakan kuota PT | `app-config.ts:859`, `pos-loyalty.service.ts:565-590` | R-B05 |
| SEC-70 | Info | Batas gerbang RPC: allowlist hanya dokumentasi (cek akses tidak ditegakkan), override ORM dikecualikan total, tour minimum CI masih 9, `/json/2` belum diuji nyata, `docs/04` §6 usang | `test_rpc_surface.py:34,37-57`, laporan odoo-engineer G13c | R-S01 |
| SEC-71 | Info | Stream Loki `efn_loyalty`: anti-injeksi bergantung pada semua log BFF satu baris (PLAUSIBLE); metrik anomali Prometheus tanpa alert; redaksi HP sebelum match dapat merusak `order_ref`; batas 200 baris recon | `promtail.yml:57-65,103-136`, `contract-exception.filter.ts:66`, `loyalty-recon.service.ts:13` | R-S10 |

---

## Detail temuan baru

### SEC-67: Low: Keputusan `hold` biner, tanpa pemisahan tugas
**Lokasi:**
- `efn_pos_loyalty_outbox.py:147-159`: `action_efn_hold_approve` mengubah `hold → pending`, lalu reverse dikirim **apa adanya**. BFF me-restore `floor(redeemed × ratio)` dan clawback earn.
- `:161-173`: `action_efn_hold_cancel` mengubah status → `skipped`. Akibatnya clawback earn order asal **ikut hilang**.
- `_efn_check_hold_decider` (`:136-145`) hanya memeriksa grup `point_of_sale.group_pos_manager`, record rule, company, dan status `hold`. Tidak ada pemeriksaan bahwa penyetuju ≠ user/karyawan yang membuat refund.
- Aktivitas dikirim ke "POS manager pertama" company (`:449-464`). Orang ini bisa saja pelaku refund.

**Skenario:**
1. Supervisor (atau user perangkat outlet yang ber-grup POS manager, **PLAUSIBLE** bila outlet login dengan akun manager dan peran diatur oleh `pos_hr`) membuat refund penuh atas order yang dibayar sebagian dengan poin.
2. Ia sengaja menghapus baris penyesuaian poin, sehingga outbox berstatus `hold`.
3. Ia lalu menyetujui sendiri. Hasilnya uang kembali penuh **dan** poin kembali. Ini persis skenario SEC-60, hanya kini dengan jejak chatter.

Sebaliknya, supervisor yang jujur dan memilih *Batalkan* kehilangan clawback earn. Koreksinya harus manual lewat Finance, tetapi belum ada alat koreksi ledger yang terdokumentasi.

**Rekomendasi:**
- Tambahkan `restore_points` eksplisit di kontrak reverse (§9.4). Approve mengirim restore = nilai baris penyesuaian aktual / `point_value`, atau 0, sementara clawback tetap jalan. Ini sudah dicatat sebagai sisa SEC-60 oleh odoo-engineer.
- Tolak keputusan bila `env.user` = `order.user_id`, atau karyawan `pos_hr` order = karyawan dari user penyetuju. Lebih baik lagi, wajibkan grup back-office terpisah (mis. `efn_loyalty_approver`) yang tidak dimiliki akun perangkat outlet.
- Masukkan jumlah outbox `hold` yang berumur > 24 jam ke alert (lihat sisa SEC-61).

### SEC-68: Low: Retur tanpa struk tanpa pelanggan tidak menarik poin
**Lokasi:**
- `pos_order.py:153-157`: order dengan barang qty < 0 tanpa `refunded_orderline_id` → `return_unlinked`.
- `:317-320`: review hanya bila `_efn_loyalty_return_relevant`.
- `:325-333`: relevan hanya bila ada baris poin, **pelanggan dipilih**, atau order asal ikut loyalty. Pada retur tanpa struk, `origins` selalu kosong, sehingga syarat ketiga tidak pernah terpenuhi.

**Skenario:**
1. Member membeli barang Rp1.000.000 dan mendapat 100 poin.
2. Esoknya barang diretur dengan tombol `-` tanpa memilih pelanggan.
3. Uang kembali, poin tetap (≈ Rp10.000), dan order tidak masuk filter "Review Poin".

Kerugian per kejadian kecil (1 %) dan dibatasi plafon harian 2.000 poin. Celah ini relevan untuk kolusi berulang, dan kontrol retur tanpa struk sendiri belum diputuskan (Q-27, SEC-63).

**Rekomendasi:**
- Tandai **setiap** `return_unlinked` di POS dengan earn aktif sebagai `review`, termasuk tanpa pelanggan, atau
- wajibkan supervisor/alasan untuk retur tanpa struk (G13-2, Q-27).

Tambahkan filter harian "retur tanpa struk" di `pos_audit_filters.xml`.

### SEC-69: Info: Konfigurasi batas earn
- `LOYALTY_EARN_DAILY_MAX_POINTS` menerima `0` (`app-config.ts:859`, min 0), yang mematikan pagar harian (`pos-loyalty.service.ts:566`). Hal yang sama berlaku untuk batas PT/kasir. Tidak ada pagar `deploy-validate.sh` yang mencegah nilai 0 di production.
- Batas kasir/jam hanya aktif bila `pos_config_id` dikirim (`:583`). Jalur Odoo resmi selalu mengirimnya (`efn_pos_loyalty_outbox.py:308`), tetapi pemanggil service key langsung (server action admin, kompromi container Odoo) dapat menghilangkannya. Pemanggil yang sama juga dapat memutar `company_code` (RTL/FNB/…) untuk mendapat kuota PT/jam terpisah. Batas harian per member tetap berlaku lintas PT.
- **Rekomendasi:**
  - Validasi deploy: di production, nilai batas harus > 0 dan ≤ nilai yang disetujui Finance.
  - Jadikan `pos_config_id` wajib untuk earn setelah semua Odoo ter-upgrade (kontrak v4.4).
  - Tambahkan batas global per jam lintas PT.

### SEC-70: Info: Batas gerbang RPC (`test_rpc_surface.py`)
Gerbangnya solid: AST + registry nyata + HttpCase, AbstractModel `efn.*` tanpa metode publik, dan uji allowlist basi. Allowlist saat ini **tidak terlalu longgar**: semua 13 entri sudah diperiksa, dan masing-masing memanggil pemeriksa grup/akses/company di baris pertama (`pos_config.py` otp_gate `:129,211`; loyalty `:203,238,328,367`; outbox `:149,164`; override CE `pos.order` hanya menambah antrean release). Batasnya:
- Nilai di `RPC_ALLOWLIST` (`:37-57`) hanya teks. Test tidak memastikan metode benar-benar memanggil pemeriksa yang disebut. Usul: cek AST bahwa statement pertama metode allowlist adalah pemanggilan `self._efn_check_*`.
- `ORM_OVERRIDES` (`:34`) membebaskan override `create/write/unlink/read/search/...` dari review. Override seperti `efn_pos_otp_gate/models/res_partner.py:68` atau `efn_audit/models/base.py:343-396` yang suatu saat menambah `sudo()` tidak akan tertangkap. Usul: allowlist eksplisit per (model, metode ORM).
- `HttpCase` kasir (`efn_pos_loyalty_ledger/tests/test_rpc_access.py`) bertag `efn_tour`, sementara `EFN_TOUR_MIN_TESTS` CI masih 9 (laporan devsecops G13c.4). Akibatnya 6 uji RPC bisa hilang tanpa membuat CI gagal. Naikkan ke 15.
- `/json/2` dengan API key belum diuji nyata.
- `docs/04` §6 masih menyebut `efn.bff.client.get`.

### SEC-71: Info: Integritas stream `efn_loyalty`
- Regex promtail (`promtail.yml:103-136`) berjangkar, dan nilai payload disaring BFF (`loyalty-anomaly.ts:50-60`). Namun konteks logger (`[PosLoyaltyService]`) adalah teks di baris itu sendiri, dan `ConsoleLogger` Nest tidak meng-escape newline. Akibatnya, setiap log BFF yang memuat input mentah ber-newline dapat menyuntik baris anomali atau heartbeat palsu. Titik yang diperiksa (DTO loyalty/migrasi, provider webhook) tervalidasi. Kandidat tersisa adalah `contract-exception.filter.ts:66` (`err.message` error tak terduga, pesan pg bisa memuat nilai input). Status: **PLAUSIBLE, sulit dieksploitasi.** Dampaknya alert palsu atau heartbeat palsu. Dead-man gauge Prometheus (`alert-rules.yml:174-178`) tetap independen.
- Metrik `efn_loyalty_anomaly_total{kind,severity}` belum punya rule Prometheus. Bila promtail buta (format waktu Nest berubah), hanya dead-man yang menyala, dan anomali di jendela itu tidak ter-alert per kind.
- Tahap redaksi HP (`promtail.yml:57-65`) berjalan sebelum match loyalty. Segmen UUID `order_ref` yang kebetulan berbentuk `08xxxxxxxxxx` akan diganti `[PHONE_REDACTED]`. Dampaknya kosmetik, tetapi investigasi menjadi lebih sulit. Pola yang sama sudah diatasi untuk jangkar audit dengan mencocokkan sebelum redaksi.
- `RECON_ROW_LIMIT` 200 per cek (`loyalty-recon.service.ts:13`). Banjir positif palsu `earn_above_rule` setelah perubahan tarif dapat menutupi temuan baru di siklus yang sama.
- **Rekomendasi:** logger JSON di BFF (backlog devsecops), rule Prometheus `increase(efn_loyalty_anomaly_total{severity="critical"}[15m]) > 0` sebagai jalur kedua, dan pencocokan stream loyalty sebelum redaksi (payload sudah tanpa PII).

---

## Verifikasi area fokus 13c (yang sudah benar)

- **Gerbang RPC / SEC-58:**
  - Semua metode `efn.bff.client` berawalan `_`, dan Odoo menolak RPC ke nama `_*`.
  - Grep seluruh `efn_*/models` menunjukkan metode publik = 13 entri allowlist + override ORM/CE.
  - Tidak ada controller `http.route` di addon `efn_*`.
  - Tidak ada monkeypatch/`setattr` yang bisa melewati scanner AST.
  - `ir_http.py` hanya meredaksi error dan tidak menambah permukaan.
- **`_efn_check_otp_caller`** (`efn_pos_otp_gate/models/pos_config.py:69-85`):
  - grup `group_pos_user`;
  - `check_access('read')` pada config (record rule multi-company), dengan pesan seragam;
  - company config ∈ `user.company_ids`;
  - sesi `opened`.

  Partner dibaca dengan record rule (`:101-112`). Catatan: user tidak diikat ke sesi tertentu. Kasir outlet A di PT yang sama dapat memakai config outlet B yang sesinya terbuka. Dampaknya terbatas karena reservasi/challenge terikat (config, uuid acak). Diterima.
- **Outbox hold:**
  - ACL outbox untuk POS manager/akuntan hanya baca, dan tulis hanya untuk `base.group_system` (`security/ir.model.access.csv:5-7`). Kasir tidak bisa `write({'state': 'pending'})` lewat ORM.
  - Tombol memeriksa grup, rule, company, dan status, lalu mencatat uid + chatter.
  - `call_kw`/`call_button` memakai JSON-RPC bersesi. Request lintas situs tidak bisa memalsukan `application/json` tanpa preflight, sehingga tidak ada CSRF praktis.
  - Rasio `hold` tetap dihitung dalam batas kumulatif (`pos_order.py:432-440`).
- **Validasi sync redeem (SEC-62):**
  - Nilai dihitung ulang server dari harga/pajak, dan nilai tersimpan dari klien juga dicocokkan.
  - Reservasi terikat (config, uuid). Order bukan `sale` tidak boleh membawa baris poin negatif.
  - Penolakan me-rollback seluruh sync, sehingga diskon poin tidak pernah gratis.
- **Barcode:** penetralan `discount → product 22` berlaku untuk semua nomenklatur pada setiap upgrade, tanpa menghapus record noupdate CE.
- **Erasure loyalty (SEC-65):**
  - Challenge/reservasi dibersihkan (partner, mask, token). Outbox terbuka di-skip.
  - Baris keuangan tetap anonim.
  - Modul opsional dideteksi lewat registry, tanpa dependency baru.
- **Batas earn BFF:**
  - Pemeriksaan batas dilakukan **setelah** cek replay. Retry sah tetap 200 walau batas diturunkan.
  - Batas harian dihitung di bawah `pg_advisory_xact_lock` member, sehingga tepat walau paralel dan lintas outlet/PT.
  - Redis mati → 5xx (fail closed, Odoo retry). Kuota PT dikembalikan bila batas kasir menolak.
  - Memecah order **tidak** menembus batas harian per member. Memecah ke banyak member dibatasi kuota PT/kasir per jam.
- **Rekonsiliasi BFF:**
  - Transaksi READ ONLY, `pg_try_advisory_xact_lock` satu replika.
  - Log tanpa `member_hash`/HP. Heartbeat dobel (Loki + gauge) dengan inhibit yang benar.
- **`erasure_suppression`:**
  - Satu kolom tag HMAC dengan kunci turunan HKDF ber-domain terpisah. Tag tidak bisa di-JOIN ke `member_hash`.
  - Tanpa request_id/waktu/PT. Grant hanya `efn_bff` (tidak ke `efn_readonly`/`efn_etl`), dan `UPDATE` hanya `expires_at` yang hanya bisa diperpanjang.
  - Retensi otomatis. Hanya migrasi yang ditolak, sedangkan pendaftaran ulang sukarela tetap mungkin (benar menurut UU PDP).
  - Sisa: pemegang `otp_pepper` + nomor HP dapat menguji "pernah dihapus?", dan sudah didokumentasikan. Respons `409 member_erased` ke pemegang kunci migrasi juga merupakan oracle kecil yang dapat diterima.
- **Alert:**
  - Severity ditentukan BFF dari daftar tetap. Critical → page `team: security`, warning → ringkasan Finance.
  - Dead-man log 2 jam ditambah dead-man gauge 26 jam yang tidak menyala palsu saat restart. Retensi stream 400 hari.

## UU PDP: status

| Kewajiban | Status r7 | Bukti / sisa |
|---|---|---|
| Consent server-side | **Implementasi** | — |
| Hak akses (ekspor) | **Implementasi** | ekspor besar belum |
| Hak hapus lintas store | **Implementasi** | reservasi/challenge Odoo kini dibersihkan. Migrasi tidak menghidupkan subjek selama 90 hari (placeholder). Pemalsuan penyelesaian lewat RPC tertutup (SEC-58) |
| Koreksi / ganti nomor | **Belum** | — |
| Retensi | **Implementasi** (menunggu DPO) | suppression 90 hari, stream `efn_loyalty` 400 hari (tanpa PII), reservasi Odoo 400 hari (anonim setelah erasure) |
| Inventaris data / RoPA | **Perlu v4** | `loyalty_reservation`, `loyalty_pos_earn`, `erasure_suppression`, `efn.pos.loyalty.*`, stream `efn_loyalty`, mart Fase 1 belum tercantum |
| DPO, kebijakan privasi final, transfer lintas negara | **Belum diputuskan** | Q-P9..P11 |

## Rekomendasi go/no-go: loyalty di outlet pilot

**Keputusan: GO bersyarat** untuk satu outlet pilot (PT Retail). Earn dan redeem boleh aktif bersamaan. Alternatif yang lebih hati-hati: earn dulu, redeem setelah 1–2 minggu tanpa anomali critical.

Alasan: tidak ada temuan Critical/High/Medium terbuka di jalur loyalty. Kasir tidak bisa lagi memanggil BFF langsung. Retur, refund, dan penukaran tidak sah ditolak atau ditahan di server. Potensi kerugian dari jalur yang tersisa (admin Odoo, kolusi supervisor) dibatasi plafon harian per member dan kuota PT/kasir per jam, serta terdeteksi oleh alert.

Syarat sebelum flag dinyalakan:
1. **Staging:**
   - `06-platform-loyalty-guard.sql` sudah diterapkan, modul `efn_core 1.6 / otp_gate 1.7 / loyalty 1.1 / privacy 1.3 / setup_pos 1.3` ter-upgrade di DB yang sama dengan pilot;
   - uji `call_kw` **dan `/json/2`** (API key kasir) ke `efn.bff.client.*` → ditolak.
2. **Finance** menetapkan batas final (`LOYALTY_POS_MAX_EARN_POINTS`, `LOYALTY_EARN_DAILY_MAX_POINTS`, PT/jam, kasir/jam) dan semuanya > 0 di manifest production (SEC-69).
3. **Akses:**
   - daftar pemegang `base.group_system` hanya Superadmin (SEC-43 butir 5, keputusan klien 6 Okt);
   - akun perangkat kasir **tidak** ber-grup `group_pos_manager` (SEC-67).
4. **Operasional:**
   - supervisor meninjau filter *Review Poin* dan outbox `hold` setiap hari;
   - on-call menerima `EfnLoyaltyAnomalyCritical`;
   - dead-man `EfnLoyaltyReconMissing` sudah teramati menyala/padam sekali di staging.
5. **UU PDP:** `PDP_DATA_INVENTORY.md` v4 memuat tabel/model G13/G13c beserta retensinya (SEC-65/SEC-11). Ini dokumen, bukan kode.
6. **CI:** `EFN_TOUR_MIN_TESTS` dinaikkan ke 15 (SEC-70).
7. **SOP:** prosedur retur tanpa struk mewajibkan supervisor sampai SEC-68/Q-27 diputuskan.

Kondisi **NO-GO / matikan flag:** alert `earn_ledger_orphan`, `earn_ledger_mismatch`, `ledger_unknown_source`, `restore_exceeds_redeem`, atau `earn_limit_company_hour` muncul tanpa penjelasan. Matikan `efn_loyalty_*_enabled` di config POS (POS tetap jalan tanpa loyalty), lalu ikuti `docs/runbooks/loyalty-anomaly.md`.

## Prioritas perbaikan

1. **Sebelum pilot (syarat GO):** butir 1–7 di atas, termasuk SEC-70 (CI min 15) dan inventaris PDP v4.
2. **Selama pilot (≤ 2 minggu):**
   - SEC-67 (`restore_points` + pemisahan penyetuju);
   - SEC-68 (review semua retur tanpa struk);
   - sisa SEC-61 (rekonsiliasi ETL ledger ↔ `pos.order`, alert order `review`/`hold`, saldo negatif).
3. **Sebelum perluasan ke outlet lain:** SEC-63 (Q-27: G13-2/G13-6, barcode `23`), SEC-69 (pagar deploy batas > 0, `pos_config_id` wajib), SEC-51 (wiring `mac`).
4. **Sebelum vendor payment diaktifkan:** SEC-33.
5. **Hardening terjadwal:** SEC-64, SEC-71 (logger JSON BFF, rule Prometheus anomali), SEC-52, SEC-47, SEC-48.
6. **Backlog:** SEC-55, SEC-57, SEC-66, SEC-11 (DPO, koreksi nomor), SEC-38.
7. **Verifikasi runtime yang masih dibutuhkan:**
   - `/json/2` (SEC-58);
   - `EfnLoyaltyAnomalyCritical` dipicu nyata;
   - dead-man 2 jam;
   - tour POS untuk order berpoin yang ditolak sync dan refund berpoin `hold`;
   - nomenklatur aktif per company setelah upgrade (SEC-63);
   - apakah `safe_eval` server action dapat memanggil `_efn_post` (SEC-61 butir 3, PLAUSIBLE).
