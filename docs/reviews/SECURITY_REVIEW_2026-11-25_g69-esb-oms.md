# Security review G69: penjualan level struk ESB OMS (2026-11-25)

Reviewer: efn-security-reviewer (read-only). Branch `feat/esb-oms-sales` (worktree `efn-platform-oms`) vs `origin/main`.
Kontrak acuan: docs/04 §33, ADR-040 §6c (Proposed), brief `docs/agent-reports/g69-contract-brief.md`, dan review
sebelumnya: G41 SEC-18x, SEC-AG-01, G52 §28.15.7.

Keterbatasan: review ini hanya membaca kode. Shell tidak tersedia, jadi `git diff`, pytest, vitest, dan
`check-encryption.sh` tidak dijalankan. Daftar file yang direview diambil dari pencarian `G69|OMS` di worktree.

## Ringkasan eksekutif

- **Hasil: 0 Critical, 0 High, 0 Medium, 4 Low, 5 Info. Verdict: boleh merge.** `ESB_OMS_ENABLED` tetap `false` di
  production sampai Q-ESB1, Q-OMS2, Q-OMS3, dan persetujuan DPO terpenuhi. Hal ini sudah tertulis di kontrak dan di
  inventaris PDP.
- **Allowlist klien OMS ketat.** Hanya POST ke 5 path persis yang lolos. Base URL dinormalisasi ke konstanta. Query dan
  body dicek terhadap allowlist sebelum koneksi dibuka, redirect tidak diikuti, dan ukuran respons dibatasi. Klien Core
  tetap GET-only. Tidak ditemukan cara memanggil endpoint tulis atau host lain.
- **Data member, kartu, dan staf dibuang sebelum disimpan.** Pembuangan dilakukan sebelum `raw_doc` dan mart.
  `fact_esb_oms_bill_payment` dan `raw_doc` tidak di-grant ke pembaca mana pun. Row policy kelas PT/BR untuk 7 view
  baru sudah benar, dan writer hanya punya SELECT + INSERT.
- **Temuan Low** berupa pertahanan berlapis dan minimalisasi data:
  - (01) `table_name` disimpan padahal belum dipakai dan isinya belum diverifikasi;
  - (02) redaksi memakai denylist, belum whitelist;
  - (03) circuit breaker auth OMS terpisah dari Core walau keduanya memakai akun ESB yang sama;
  - (04) larangan nyala di production hanya berupa default env, tidak dikunci di kode.

## Tabel temuan

| ID | Severity | Lokasi | Ringkas | Risiko |
|---|---|---|---|---|
| SEC-OMS-01 | Low (PLAUSIBLE) | `services/esb-extract/efn_esb/oms_transform.py:375`; `infra/clickhouse/init/26-g69-esb-oms.sql:49`; `infra/clickhouse/access/insight-access.sql:251` | `tableName` disimpan di raw + mart + wrapper Insight padahal tidak dipakai G69; untuk take away/ojol kolom ini bisa berisi nama pelanggan | R-L01 (UU PDP minimalisasi) |
| SEC-OMS-02 | Low | `services/esb-extract/efn_esb/oms_transform.py:24-41, 98-108` | Redaksi raw berbasis denylist: kunci kartu/verifikasi baru dari vendor (mis. `maskedCardNumber`, `cardNo`, `approvalCode`) akan tersimpan di `raw_doc` | R-L01, PCI DSS (tanpa PAN) |
| SEC-OMS-03 | Low (PLAUSIBLE) | `services/esb-extract/efn_esb/oms_runner.py:513-527`; `efn_esb/runner.py:314-342` | `AuthBreaker` OMS terpisah dari Core walau akun ESB-nya sama, sehingga kegagalan auth berturut-turut ke akun bisa dua kali batas SEC-186 | R-S17, SEC-186 |
| SEC-OMS-04 | Low | `services/esb-extract/efn_esb/config.py:390-419`; `scripts/check-encryption.sh:966-977` | Larangan production (akun admin, Q-OMS2 beban vendor, DPO) hanya ditegakkan default env `false`, tidak dikunci di kode seperti `prodplan` | R-S17, Q-OMS2 |
| SEC-OMS-05 | Info | `efn_esb/oms_runner.py:198, 432`; `config.py:190-191` | Cabang OMS yang tak dikenal di `dim_esb_branch` mendapat `branch_id=0` lalu masuk PT default brand | ADR-001 |
| SEC-OMS-06 | Info | `infra/clickhouse/access/insight-access.sql:246-262, 273-276` | Wrapper `v_esb_oms_bill`, `v_esb_oms_menu_hourly`, dan `v_esb_oms_recon_daily` dibuat tetapi belum dipakai insight-web | least privilege |
| SEC-OMS-07 | Info | `services/esb-extract/efn_esb/__main__.py:203-205, 448-451` | Konfigurasi OMS yang tidak sah menghentikan start Core juga (fail closed); exit 2 dengan `restart: unless-stopped` membuat restart loop | ketersediaan |
| SEC-OMS-08 | Info | `docs/agent-reports/g69-contract-brief.md:1, 12` | Brief bertanda "DRAF INTERNAL, dihapus sebelum PR" masih ada; memuat jumlah cabang hasil probe production | repo publik |
| SEC-OMS-09 | Info | `services/esb-extract/efn_esb/oms_http.py:67` | `\d` di `_DATE_RE` juga menerima digit Unicode; tidak bisa dieksploitasi | — |

## Detail

### SEC-OMS-01: Low (PLAUSIBLE). `table_name` disimpan tanpa dipakai

**Lokasi.** Data mengalir sebagai berikut:
- `transform_bills` menulis `text(row.get("tableName"), 64)` (`oms_transform.py:375`);
- DDL menyimpannya di `fact_esb_oms_bill.table_name` (`26-g69-esb-oms.sql:49`);
- wrapper `efn_insight.v_esb_oms_bill` (`insight-access.sql:251`, kelas BR) mengeksposnya;
- `tableName` juga ikut di `raw_doc` karena tidak ada di denylist.

Tidak ada query insight-web yang membaca kolom ini (`lib/queries.ts:869-928` hanya memakai freshness, hourly,
fin_hourly, dan mapping_quality).

**Skenario.** Pada POS F&B, struk take away atau ojol sering memakai field meja sebagai label pesanan, misalnya nama
pelanggan, nama driver, atau kode order aplikasi. Jika hal ini terjadi di PB, nama orang akan tersimpan 36 bulan di raw
dan 10 tahun di mart. Data itu juga bisa dibaca persona Toko lewat wrapper. Kontrak §33.5/§33.6 menganggap `tableName`
bukan data pribadi dan menyebutnya "opsional, boleh tidak dimuat bila tidak dipakai di G69". Anggapan itu belum
diverifikasi terhadap data nyata, jadi temuan ini berstatus PLAUSIBLE.

**Rekomendasi.** Pilih salah satu sebelum `ESB_OMS_ENABLED=true` di production:
- (a) Masukkan `tablename` ke `OMS_DROP_KEYS`, kosongkan kolom (atau hapus dari DDL selagi tabel belum berisi data), lalu
  keluarkan dari wrapper.
- (b) Verifikasi pola isi `tableName` di efn-dev (hitungan saja, tanpa mencatat nilai) dan catat hasilnya di inventaris
  PDP §2.8.16.

Opsi (a) lebih sederhana karena kolom ini belum dipakai.

### SEC-OMS-02: Low. Redaksi `raw_doc` berbasis denylist

**Lokasi.** `strip_oms` (`oms_transform.py:102-108`) membuang `OMS_DROP_KEYS` (exact match, lowercase) ditambah
denylist L-110 (`normalize.is_pii_key`). Semua kunci lain disimpan apa adanya ke `raw_doc`. Pola ini berbeda dari G43/G65,
yang memakai whitelist per entitas sebelum data disimpan.

**Skenario.** Vendor ESB menambahkan field di `salesPayments[]`, misalnya `maskedCardNumber`, `cardNo`, `cardHolder`,
`approvalCode`, `traceNumber`, atau `edcRefNo`. Tidak satu pun cocok dengan denylist: `cardnumber` dicek exact, dan
L-110 tidak punya substring `card`. Akibatnya field itu tersimpan diam-diam di `raw_doc` selama 36 bulan. Kontrol mart
tetap aman karena kolom mart dipilih eksplisit. PAN utuh pun tidak mungkin dikirim oleh API POS yang sudah memasking
`cardNumber`, sehingga dampaknya terbatas pada raw.

**Rekomendasi.** Pilih salah satu:
- Tambah pemeriksaan substring untuk `card`, `verification`, `approval`, `cvv`, `pin`, `trace`, `holder`, `voucher` di
  `is_oms_drop_key`.
- Lebih kuat: terapkan whitelist untuk elemen `salesPayments[]` di raw. Kunci yang dibutuhkan hanya `paymentMethodID`,
  `paymentMethodName`, `paymentMethodTypeID`, `paymentMethodTypeName`, `bankName`, `paymentAmount`,
  `fullPaymentAmount`, `salesPaymentBackendID`, dan `salesPaymentPosID`.

Tambahkan test dengan kunci `maskedCardNumber`.

### SEC-OMS-03: Low (PLAUSIBLE). Circuit breaker auth tidak dibagi antara Core dan OMS

**Lokasi.** `oms_loop` membuat `AuthBreaker()` sendiri (`oms_runner.py:513`). `_cmd_run` juga membuat breaker sendiri
untuk Core (`__main__.py:206`). Keduanya memakai `ESB_USER_<BRAND>` dan `esb_core_password_<brand>` yang sama (§33.1).

**Skenario.** Password brand diganti di ESB tetapi secret belum diperbarui:
1. Core gagal 2 siklus (login + retry sekali per siklus), lalu brand Core ditangguhkan.
2. Loop OMS, yang interval-nya 15 menit, tetap mengirim Basic auth dan gagal 2 kali lagi sebelum ditangguhkan.
3. `oms-probe` dan `oms-backfill` dari CLI tidak memakai breaker sama sekali.

Total percobaan gagal ke akun yang juga dipakai operasional ESB jadi sekitar dua kali asumsi SEC-186. Ambang kunci akun
ESB belum diketahui, jadi temuan ini berstatus PLAUSIBLE.

**Rekomendasi.** Bagikan satu instance `AuthBreaker` per proses ke loop Core dan OMS (keduanya sudah thread-safe untuk
operasi set/dict sederhana; bila perlu tambahkan lock). Dengan begitu, brand yang ditangguhkan di salah satu jalur juga
berhenti di jalur lain. Alternatif minimal: `oms_loop` melewati brand yang sudah ditangguhkan breaker Core.

### SEC-OMS-04: Low. Larangan production hanya berupa default

**Lokasi.** `OmsSettings.from_env` (`config.py:390-419`) hanya mensyaratkan `ESB_ENABLED=true` dan brand yang valid.
`check-encryption.sh:975-977` memastikan default render prod adalah `false`. Tidak ada pengunci berbasis `APP_ENV`.

**Skenario.** Operator menyalakan semua flag di efn-prod (pola yang pernah terjadi, lihat catatan "efn-prod semua flag
ON"). Loop OMS lalu langsung berjalan:
- dengan akun berhak admin (R-S17), meskipun allowlist tetap membatasi ke baca;
- dengan beban intraday sekitar 5.800–9.800 panggilan/hari, padahal izin vendor belum ada (Q-OMS2);
- sebelum persetujuan DPO (inventaris PDP §2.8.16, "MENUNGGU DPO").

Dampaknya ke kepatuhan dan hubungan dengan vendor, bukan kebocoran data.

**Rekomendasi.** Ikuti pola `prodplan` (ADR-051): pada `APP_ENV=production`, `ESB_OMS_ENABLED=true` ditolak kecuali ada
flag penerimaan eksplisit, misalnya `ESB_OMS_PROD_APPROVED=true`, yang didokumentasikan bersama Q-ESB1, Q-OMS2, Q-OMS3,
dan DPO. Bila dianggap berlebihan untuk jalur baca, cukup catat penerimaan risikonya di runbook §10.

### SEC-OMS-05: Info. Cabang tak dikenal masuk PT default

`branch_ids.get(code, 0)` lalu `cfg.company_for(0)` menghasilkan `company_code` default brand
(`oms_runner.py:198`, `oms_transform.py:432-436`). Asumsi [B] "branchCode OMS = branch_code Core" belum diverifikasi.
Akibatnya:
- Sesi kelas BR tertutup aman: `brand:0` tidak pernah ada di `brs`.
- Sesi PT default brand melihat struk cabang yang tak terpetakan.

Untuk PB (satu PT) dampaknya nihil. Rekomendasi: hitung baris `branch_id=0` di `v_esb_oms_recon_daily` atau di data
quality, lalu pertimbangkan untuk menolak slice cabang tak dikenal bila brand punya override PT per cabang. Polanya sama
dengan SEC-G65-05.

### SEC-OMS-06: Info. Wrapper Insight yang belum dipakai

`efn_insight.v_esb_oms_bill` (per struk), `v_esb_oms_menu_hourly`, dan `v_esb_oms_recon_daily` dibuat dan diberi row
policy, tetapi tidak dirujuk `lib/queries.ts`. Row policy-nya benar: bill dan menu_hourly kelas BR, recon kelas PT. User
`efn_insight` juga hanya dipakai lewat query bernama di server. Risikonya kecil. Saran: tunda wrapper per struk
(`v_esb_oms_bill`) sampai ada kebutuhan halaman, atau biarkan dengan catatan di README insight-web.

### SEC-OMS-07: Info. Konfigurasi OMS memengaruhi start Core

Dua jalur menghentikan Core:
- `OmsSettings.from_env()` dipanggil sebelum loop Core (`__main__.py:203`), sehingga nilai `ESB_OMS_*` yang tidak sah
  menggagalkan start Core.
- `ESB_OMS_ENABLED=true` dengan `ESB_ENABLED=false` keluar dengan kode 2 (`__main__.py:449-451`). Dengan
  `restart: unless-stopped`, container masuk restart loop.

Ini fail closed dan sesuai §33.3. Di runtime, kegagalan OMS memang terisolasi: thread terpisah, klien ClickHouse
sendiri, dan exception ditangkap. Cukup dicatat di runbook §10 bahwa salah konfigurasi OMS = Core tidak start.

### SEC-OMS-08: Info. Brief internal masih di branch

Baris 1 `g69-contract-brief.md` menyatakan file ini dihapus sebelum PR. Isinya memuat jumlah cabang PB dan detail probe
production. Tidak ada angka penjualan atau username, tetapi sesuai brief file ini harus dihapus (atau dipindah ke luar
repo) sebelum PR dibuka. Catatan tambahan: §33.9 docs/04 menyebut "±21 toko" dan perkiraan panggilan. Ini angka
operasional, bukan angka penjualan, jadi masih dapat diterima.

### SEC-OMS-09: Info. Digit Unicode di regex tanggal

Pola `_DATE_RE = ^\d{4}-\d{2}-\d{2}$` dengan `fullmatch` menerima digit non-ASCII, misalnya angka fullwidth. Nilainya
hanya dikirim sebagai string JSON ke filter ESB, tidak dipakai di SQL atau path, dan pemanggil selalu
`date.isoformat()`. Akibatnya tidak bisa dieksploitasi. Opsional: ganti dengan `[0-9]` atau `re.ASCII` agar konsisten
dengan `_BRANCH_RE`.

## Kontrol yang sudah benar

1. **Allowlist OMS** (`oms_http.py:71-108`):
   - Skema, host (lowercase), dan port 443 dicek. Userinfo, query, fragment, dan path ditolak. Base dinormalisasi ke
     konstanta `https://esbcore.co.id`, sehingga host tidak bisa dibelokkan lewat `@`, huruf besar, trailing dot, atau
     subdomain.
   - Path dicek dengan keanggotaan `frozenset` persis. Traversal, `%xx`, trailing slash, `?` di path, `push/*`,
     `sales-data`, `create-*`, `update-*`, dan `/auth/login` ditolak.
   - Query hanya `page` (int 1..10000, bukan bool) dan hanya untuk `sales-head`.
   - Body wajib berupa objek, kuncinya per path, dan nilainya divalidasi pola. `sales-head` wajib membawa `page` dan
     `filterBranchCode`.
   - Semua guard berjalan sebelum `_send` dan dicakup test parametrik (`test_oms.py:286-403`).
2. **Transport** sama dengan Core: `ProxyHandler({})`, `NoRedirect` (30x jadi `EsbHttpError`), `https://` wajib, CA
   publik, dan `read_limited` (SEC-187) baik dari `Content-Length` maupun dari pembacaan aktual. 401 tidak di-retry.
   Klien Core tetap GET-only dan ditegaskan oleh test `test_core_client_still_get_only`.
3. **Kredensial**:
   - Header Basic dibangun sekali di konstruktor dan user/password tidak disimpan terpisah.
   - `__repr__` menyembunyikan auth. Pesan exception hanya berisi path/status/nama tipe (`from None`).
   - Log runner hanya mencatat brand, entitas, hari, status, dan nama tipe error.
   - Username tidak ada di repo: compose memakai default kosong, `.env.example` kosong, dan ada gerbang
     `check-encryption.sh:1320`.
   - Tidak ada secret baru. Fixture test sintetis.
4. **PDP/PCI**:
   - `strip_oms` rekursif di semua tingkat, termasuk `salesInfo[]` utuh, dan dijalankan sebelum `raw_doc` dan transform
     (`oms_runner.py:197, 240, 331-332`).
   - `has_member` dihitung dari payload mentah dan hanya disimpan sebagai 0/1.
   - Kolom mart dipilih eksplisit dan tidak ada kolom kartu, rekening, atau verifikasi.
   - Test sentinel rekursif tersedia (`test_oms.py:489`).
   - Inventaris PDP §2.8.16 dan baris retensi sudah ditambahkan dengan status MENUNGGU DPO.
5. **ClickHouse**:
   - Semua tabel baru memakai `storage_policy = 'encrypted'`.
   - `esb_writer` hanya mendapat SELECT + INSERT per tabel, tanpa CREATE/ALTER/TRUNCATE (pola SEC-AG-01).
   - `efn_insight_definer` hanya mendapat grant per objek; `fact_esb_oms_bill_payment`, `raw_doc`, dan resep G43 (HPP)
     tidak di-grant.
   - Tidak ada grant wildcard `efn_dwh.*` ke role mana pun. metabase, text2sql, dan forecast tidak mendapat akses.
   - Row policy sig3: 6 view kelas BR memakai `brand:branch_id`, recon kelas PT karena memuat pembayaran Core.
     `ESB_POLICY_EXPECTED=27` (16 PT + 11 BR) cocok dengan daftar view.
6. **Runner**:
   - `ESB_OMS_ENABLED=false` default, sehingga thread tidak dibuat.
   - `true` mensyaratkan `ESB_ENABLED=true` dan brand subset `ESB_BRANDS` (fail closed).
   - Budget per siklus dan per backfill, token bucket terpisah, dan batas halaman membuat slice `partial` tanpa
     checkpoint.
   - Urutan tulis: raw, mart, lalu checkpoint.
   - Thread dan klien ClickHouse terpisah dari Core, dan exception ditangkap di loop OMS.
7. **Compose dan gerbang**:
   - `ESB_OMS_BASE_URL` di-hardcode di overlay dan dicek persis oleh `check-encryption.sh`. `ESB_OMS_*` dilarang di
     service lain.
   - Container berjalan non-root (`65534`), `read_only`, `cap_drop: ALL`, `no-new-privileges`, dengan jaringan egress
     khusus `egress-esb`.
8. **insight-web**:
   - Semua query `foms_*` memakai parameter terikat (`{ref_day:Date}`, `{product:Array(String)}`) dan makro `${PT}` /
     `${BR}`.
   - Komponen OMS adalah server component (tanpa `use client` dan tanpa `dangerouslySetInnerHTML`). Hanya agregat yang
     dikirim ke klien.

## Verdict

**Boleh merge.** Tidak ada temuan Critical, High, atau Medium.

Wajib sebelum PR dibuka:
- Hapus `docs/agent-reports/g69-contract-brief.md` (SEC-OMS-08).

Wajib sebelum `ESB_OMS_ENABLED=true` di lingkungan mana pun yang memakai data nyata:
- SEC-OMS-01: buang `tableName` atau verifikasi isinya.
- SEC-OMS-02: tambah substring kartu, atau terapkan whitelist untuk `salesPayments[]`.

Wajib sebelum production:
- SEC-OMS-03: satukan breaker auth Core dan OMS.
- SEC-OMS-04: pengunci production, atau catatan penerimaan risiko.
- Q-ESB1, Q-OMS2, Q-OMS3, dan persetujuan DPO.

## Status tindak lanjut (efn-data-ai-engineer, 25 Nov 2026)

| ID | Status | Tindakan |
|---|---|---|
| SEC-OMS-01 | **Ditutup** | `tablename` masuk `OMS_DROP_KEYS` (raw); kolom `table_name` dihapus dari `fact_esb_oms_bill` (DDL 26), transform, `efn_dwh.v_esb_oms_bill`, dan wrapper `efn_insight.v_esb_oms_bill`; docs/04 §33.5/§33.6 diperbarui. Test: sentinel `tableName` tidak ada di raw/mart, kolom `table_name` tidak ada. Catatan: tabel yang sudah terlanjur dibuat dengan kolom itu (lingkungan uji lead) wajib di-DROP manual oleh admin (DDL idempoten tidak mengubah tabel yang ada; README esb-extract). |
| SEC-OMS-02 | **Ditutup** | Elemen `salesPayments[]` = whitelist 9 kunci (`oms_transform.PAYMENT_FIELDS`, nilai skalar saja); denylist substring case-insensitive `card`/`verification`/`approval`/`account`/`member`/`phone`/`email` rekursif di seluruh payload struk (di atas `OMS_DROP_KEYS` + L-110). Test: `maskedCardNumber`, `approvalCode`, `customerPhone`, `traceNumber`, `edcRefNo`, objek bersarang di pembayaran, `customerEmail`, `loyaltyAccount`, `MemberTier`, `driverPhoneNo`. |
| SEC-OMS-03 | **Diterima** (sisa risiko) | Tidak di-refactor sesuai arahan lead. Didokumentasikan di README esb-extract ("Sisa risiko yang diterima"): breaker OMS terpisah dari Core (±2× batas SEC-186), CLI `oms-*` tanpa breaker; mitigasi operasional = perbarui secret lalu restart. Tetap direkomendasikan disatukan sebelum production. |
| SEC-OMS-04 | **Ditutup** | Pola prodplan L-250: konstanta kode `config.OMS_PROD_UNLOCKED = False` (bukan env) + `check_oms_prod_lock`; production dideteksi dengan konsep yang sudah ada di esb-extract (`tls.is_production`, fail closed: `APP_ENV`/`NODE_ENV` kosong/tidak dikenal = production). `ESB_OMS_ENABLED=true` di production → `OmsSettings.from_env` gagal → proses tidak start. Tanpa env baru. Test parametrik (APP_ENV kosong/production/staging/prod ditolak; dev diterima). docs/04 §33.3 + README diperbarui. Catatan: efn-stg (`staging`) juga terkunci. |
