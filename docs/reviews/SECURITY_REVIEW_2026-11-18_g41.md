# Security & Compliance Review: Gelombang 41 (ekstraksi ESB Core ke ClickHouse)

> **Status perbaikan (efn-lead, 18 Nov 2026, sebelum commit G41):** SEC-180 diperbaiki (username dihapus dari repo; overlay
> `${ESB_USER_X:-}`; cek `check_esb_users` di `check-encryption.sh`). SEC-181 diperbaiki (aturan sufiks/substring + teks bebas
> + lampiran; diverifikasi terhadap nama kunci respons nyata PB). SEC-183 diperbaiki (network `egress-esb`, ICC mati).
> SEC-185/186/187 diperbaiki (pagar `--ignore-window`, `auth_suspended`, batas 32 MiB). SEC-188 diperbaiki (grant
> `map_esb_odoo` dicabut sampai G43). SEC-184: entri PDP §2.8.11 ditambahkan, **menunggu DPO**. SEC-182: prasyarat
> aktivasi (Metabase hanya Holding) sampai row policy G42. SEC-189: terbuka (TTL mart, keputusan DPO).

Tanggal: 18 Nov 2026 · Reviewer: efn-security-reviewer (read-only) · Branch: `feat/wave-41-esb-datamart` (perubahan belum di-commit)
Lingkup: `services/esb-extract/**`, `infra/clickhouse/init/12-g41-esb.sql`, `infra/clickhouse/access/esb-access.sql`,
`infra/clickhouse/clickhouse-init.sh` (bagian G41), `infra/compose/docker-compose.esb.yml`, `docker-compose.yml` (G41),
`scripts/secrets.list`, `scripts/check-encryption.sh` (6b3, 8), `.github/workflows/{ci,build-push}.yml`,
`docs/runbooks/esb-extract.md`, ADR-040, `docs/04` §25.
Nomor temuan baru dimulai dari SEC-180.

## Ringkasan eksekutif

- Kontrol inti sudah baik: hanya GET (kecuali login) dicek sebelum koneksi dibuka, host ESB dikunci, TLS diverifikasi, redirect dan proxy env diabaikan, password/token tidak masuk repr/log/exception, query ClickHouse berparameter, dan container dikunci (non-root, read_only, cap_drop ALL, tidak di network `data`). Tidak ada temuan P0.
- **Satu temuan P1 harus diperbaiki sebelum commit (repo publik):** username kelima akun API ESB produksi, yang saat ini berhak **admin** (Q-ESB1), tertulis di overlay compose, runbook, dan docs/04 (SEC-180). Karena belum di-commit, perbaikannya murah dan riwayat git belum ikut bocor.
- Empat temuan P2: redaksi PII `raw_doc` memakai denylist yang tidak menangkap pola umum seperti `receivedBy`, `driverName`, dan catatan teks bebas (SEC-181); `metabase_reader` melihat semua PT (SEC-182); network `egress` dipakai bersama BFF dan service lain (SEC-183); inventaris PDP belum diperbarui (SEC-184).
- Lima temuan P3/Info terkait beban ke server klien, retensi, dan least privilege kecil.

## Tabel temuan

| ID | Prioritas / Severity | Judul | File:baris | Risiko |
|---|---|---|---|---|
| SEC-180 | **P1 / High** | Username akun API ESB (admin) tertulis di repo publik | `infra/compose/docker-compose.esb.yml:53-58`, `docs/runbooks/esb-extract.md:17-23`, `docs/04_SERVICE_CONTRACTS.md:2622` | R-S17 (usulan), R-S01 (secret), R-L01 |
| SEC-181 | P2 / Medium | Denylist PII untuk `raw_doc` tidak lengkap (`*By`, `driver/receiver/sender`, teks bebas) | `services/esb-extract/efn_esb/normalize.py:19-87` | R-L01, L-110 |
| SEC-182 | P2 / Medium | `metabase_reader` membaca mart ESB semua PT tanpa row policy | `infra/clickhouse/access/esb-access.sql:44-62` | R-L01, R-S (multi-company), L-108 |
| SEC-183 | P2 / Medium | `esb-extract` berbagi network `egress` dengan BFF dan service lain (lateral) | `infra/compose/docker-compose.esb.yml:90`, `infra/compose/docker-compose.yml:61-63` | R-S17, ADR-024 |
| SEC-184 | P2 / Medium (kepatuhan) | Inventaris PDP "Datamart ESB" belum dibuat; nama supplier perorangan disimpan | `docs/compliance/PDP_DATA_INVENTORY.md` (tidak ada entri), `normalize.py:73`, ADR-040:166-167 | R-L01 |
| SEC-185 | P3 / Low | `--ignore-window` tanpa pagar production; runbook mengizinkan `run` + `backfill` brand sama berjalan bersamaan | `efn_esb/__main__.py:42`, `efn_esb/runner.py:298`, `docs/runbooks/esb-extract.md:104` | R-O10 (usulan), L-111 |
| SEC-186 | P3 / Low | Brand `auth_failed` terus mencoba login setiap siklus (tanpa circuit breaker) | `efn_esb/__main__.py:110-118`, `efn_esb/runner.py:250-268` | R-O10, R-S17 |
| SEC-187 | P3 / Low | Body respons ESB dibaca tanpa batas ukuran (container 512 MB) | `efn_esb/http.py:109`, `:112` | R-O10 |
| SEC-188 | P3 / Low | `esb_writer` punya INSERT `map_esb_odoo` yang belum dipakai G41 | `infra/clickhouse/access/esb-access.sql:32` | least privilege |
| SEC-189 | P3 / Info | Retensi: mart ESB tanpa TTL (beda dengan L-109); TTL raw hanya di `CREATE IF NOT EXISTS` | `infra/clickhouse/init/12-g41-esb.sql:30, 80-310`; `clickhouse-init.sh:150-168` | R-L01, L-109 |

---

## Detail

### SEC-180 (P1 / High): username akun API ESB produksi tertulis di repo publik

**Bukti.** `docker-compose.esb.yml:54-58` meng-hardcode username nyata untuk `ESB_USER_PB`, `ESB_USER_WTZ`, `ESB_USER_BACHA`,
`ESB_USER_IMD`, `ESB_USER_CUP`. Tabel runbook `esb-extract.md:17-23` dan Q-ESB2 di `docs/04:2622` mengulang nilai yang sama.
Komentar overlay baris 53 menyebut "identitas user teknis read-only", padahal ADR-040:19/73/151 dan Q-ESB1 menyatakan akun
ini **akun admin** yang bisa memanggil endpoint tulis (`push/import/authorize`). Kontrak docs/04 §25.1 (baris 2415) sendiri
menulis "Nama user = env non-rahasia (diisi di `.env` host, **tidak di repo**)", jadi kode bertentangan dengan kontrak.

**Skenario.** Repo publik → penyerang mendapat 5 username admin ERP produksi klien beserta host loginnya
(`https://services.esb.co.id/core/auth/login`, juga ada di repo). Langkah berikutnya: (a) password spraying/credential stuffing
terarah ke akun admin, dan bila berhasil penyerang bisa menulis ke ERP produksi 5 brand; (b) walau gagal, percobaan login
berulang bisa **mengunci** akun (bila ESB menerapkan lockout), sehingga ekstraksi dan mungkin operasional brand terganggu.
Pola username yang sama di beberapa brand juga tampak seperti nama orang; bila benar, itu data pribadi di repo publik (PLAUSIBLE, perlu
konfirmasi).

**Rekomendasi (sebelum commit).**
1. Overlay: `ESB_USER_<BRAND>: ${ESB_USER_<BRAND>:-}` (nilai dari `.env` host, `.env` di-gitignore). Fail-closed sudah ada
   (`config.py:229-230`: user kosong → brand gagal start).
2. `.env.example`: tambah `ESB_USER_PB=` dst. **kosong**.
3. Runbook §0 dan docs/04 Q-ESB2: ganti nilai dengan "lihat log kustodi / `.env` host"; Q-ESB2 cukup "user API brand WP".
4. Perbaiki komentar overlay baris 53 (akun saat ini admin, bukan read-only).
5. Tambah aturan gitleaks/`check-encryption.sh`: `ESB_USER_[A-Z]+:\s*[^$\s]` di file compose = error.
6. Karena belum di-commit, cukup ubah file. Bila sempat ter-push, minta pemilik akun mengganti username/password dan
   pertimbangkan pembersihan riwayat.

### SEC-181 (P2 / Medium): denylist PII `raw_doc` tidak lengkap

**Bukti.** `normalize.py:21-71`: kunci yang dibuang = daftar exact (`createdby`, `updatedby`, `approvedby`, `voidby`, ...)
dan substring (`email`, `phone`, `customer`, `member`, `employee`, `cashier`, `waiter`, `picname`, ...). Kunci yang **lolos**
(dicek terhadap `is_pii_key`): `receivedBy`, `requestedBy`, `deliveredBy`, `postedBy`, `submittedBy`, `checkedBy`,
`verifiedBy`, `preparedBy`, `cancelledBy`/`canceledBy`, `deletedBy`, `closedBy`, `driverName`, `senderName`,
`receiverName`, `recipientName`, `pic`, `picID`, `createdUserID`, serta teks bebas `notes`, `remark(s)`, `description`,
`memo`, `reason`. Detail `goods-delivery`, `simple-transfer`, dan `production-result` (`extract.py:113-115, 240, 265, 287`)
adalah jenis dokumen yang umumnya memuat penerima, pengemudi, dan catatan. Mart tidak terdampak karena transform hanya
mengambil kunci bisnis (diverifikasi di `transform.py`), tetapi `raw_doc` menyimpan payload utuh hasil redaksi selama 36 bulan.
Status: **PLAUSIBLE**. Nama kunci persis di respons ESB belum diverifikasi; spec openapi tidak dibaca dalam review ini.

**Skenario.** GET `/inventory/goods-delivery/GD-xxx` → `{"receivedBy":"<nama staf toko>","driverName":"<nama>","notes":"titip ke Bu X 0812..."}`
→ lolos redaksi → `efn_esb.raw_doc.payload` menyimpan nama staf dan nomor HP selama 36 bulan. Ini melanggar L-110 dan
minimisasi UU PDP Pasal 16(2).

**Rekomendasi.**
1. Tambah aturan sufiks: kunci lowercase yang berakhiran `by`, `byname`, `byid`, `userid`, `username`, serta substring
   `driver`, `sender`, `receiver`, `recipient`, `courier`, `pic` (dengan batas kata), `signature`, `ktp`.
2. Buang teks bebas: `note`, `notes`, `remark`, `remarks`, `memo`, `description` (kecuali `categoryDesc` dan sejenisnya yang di-allowlist),
   `reason`, `comment`.
3. Lebih kuat: **allowlist per endpoint** untuk `raw_doc` (pola `redact_supplier`), karena `raw_doc` hanya dibutuhkan untuk
   memproses ulang kolom yang dipakai transform dan migrasi G43.
4. Sebelum `ESB_ENABLED=true` di production: jalankan satu kali skrip yang mendaftar **nama kunci** (bukan nilai) per endpoint dari
   respons nyata, lalu cocokkan dengan denylist. Tambah fixture sintetis untuk kunci-kunci di atas.

### SEC-182 (P2 / Medium): `metabase_reader` melihat mart ESB semua PT

**Bukti.** `esb-access.sql:44-62` memberi `SELECT` 5 view `v_esb_*` dan semua tabel dimensi/fakta ke `metabase_reader`
tanpa row policy. docs/04 §25.7 (baris 2586-2588) menunda row policy ke G42. Data mencakup omzet per cabang, COGS, dan MDR per
metode bayar (syarat komersial dengan bank) untuk PB, WTZ, BCF/BCR, IMD, dan CUP, yaitu PT yang berbeda badan hukumnya.

**Skenario.** Pengguna Metabase yang hanya berwenang untuk satu PT (mis. staf finance Bacha) membuka "Browse data" atau native
query → membaca penjualan, COGS, dan MDR PT lain. Hal ini melewati isolasi multi-company Odoo (record rule per PT).
Pola ini sama dengan mart lain yang sudah ada, jadi bukan regresi, tetapi cakupannya bertambah 5 brand.

**Rekomendasi.** Sampai G42: (a) di Metabase, batasi koleksi dan database ClickHouse hanya untuk grup Holding/analis pusat, dan
matikan native query untuk grup lain; catat ini di runbook. (b) G42: row policy `company_code` per grup Metabase (user CH per
grup, atau view DEFINER bertanda tangan seperti `efn_t2s`), dan jadikan ini gerbang sebelum dashboard ESB dibuka untuk pengguna PT.

### SEC-183 (P2 / Medium): network `egress` dipakai bersama

**Bukti.** `docker-compose.esb.yml:90` `networks: [esb, egress]`. `egress` (`docker-compose.yml:61-63`) bukan `internal`
dan juga dipakai BFF api-gateway (`:1043`, `:1312`, HTTPS :3000), acme-dns (`:459`), ollama, dan service lain (`:2153`, `:2192`,
`:2397`). Bridge Docker mengizinkan koneksi antar-container dalam satu network (icc default).

**Skenario.** `esb-extract` adalah satu-satunya service yang mem-parse JSON dari pihak ketiga internet. Bila ia disusupi
(mis. kerentanan dependency atau `clickhouse-connect`), penyerang punya jalur L3 langsung ke port BFF dan service lain di
`egress`, sehingga melewati Traefik/rate limit. Runbook §9 sudah mencatat "egress tidak membatasi tujuan" sebagai sisa risiko,
tetapi tidak menyebut lateral movement ke container lain.

**Rekomendasi.** Network khusus `egress-esb` (non-internal, subnet sendiri, hanya `esb-extract`) dan
`com.docker.network.bridge.enable_icc: "false"`. Tambah cek `check-encryption.sh`: `esb-extract` hanya di `esb` + `egress-esb`.
Jangka menengah: aturan nftables per subnet itu → IP/443 `services.esb.co.id` (sudah disebut runbook §9).

### SEC-184 (P2 / Medium, kepatuhan): inventaris PDP dan supplier perorangan

**Bukti.** `docs/compliance/PDP_DATA_INVENTORY.md` tidak memuat entri ESB (grep "esb" di `docs/compliance` = 0). ADR-040:166-167
mengakui entri itu "belum dibuat". `SUPPLIER_FIELDS` (`normalize.py:73`) menyimpan `supplierName` ke `raw_doc` dan
`dim_esb_supplier`. Untuk pemasok perorangan (petani/UMKM, relevan dengan `efn_agri`), nama itu adalah data pribadi.
Mart tanpa TTL (SEC-189).

**Rekomendasi.** Sebelum `ESB_ENABLED=true` di staging/prod lokal: entri inventaris "Datamart ESB" (sumber = ESB Core per
brand, pemroses = vendor ESB; kategori = transaksi agregat, nama supplier (bisa perorangan), ID dokumen; dasar
pemrosesan; lokasi `efn_esb.raw_doc` 36 bulan dan `efn_dwh.*esb*`; akses `esb_writer`/`metabase_reader`) + tanda tangan DPO.
Tambahkan Q-ESB9 (hak EFN atas data) sebagai prasyarat. Pertimbangkan flag `is_individual` dan pseudonimisasi nama supplier
perorangan di mart.

### SEC-185 (P3 / Low): beban ke API produksi klien

**Bukti.** (a) `--ignore-window` (`__main__.py:42`, `runner.py:298`) tidak dibatasi environment maupun panjang rentang,
padahal runbook §5 (baris 87) membatasinya ke "≤ 1 hari, dengan persetujuan". (b) Runbook baris 104: "Selama backfill
berjalan, service `esb-extract` (loop incremental) boleh tetap hidup". Token bucket per proses (`http.py:119`, `__main__.py:56`),
sehingga satu brand bisa mendapat 4 req/dtk. Ini bertentangan dengan docs/04 baris 2480-2481 dan L-111.

**Skenario.** Operator menjalankan `backfill --from 2024-11-17 --to 2026-11-16 --ignore-window` pada siang hari di production
→ 2 req/dtk tambahan ke API yang juga melayani kasir, di luar izin Q-ESB7.

**Rekomendasi.** Tolak `--ignore-window` bila `is_production()` (`tls.py:26`) kecuali rentang ≤ 1 hari dan ada env eksplisit
`ESB_ALLOW_IGNORE_WINDOW=true`. Selaraskan runbook baris 104 dengan docs/04 (hentikan `run` untuk brand yang sedang di-backfill
atau turunkan rate ke 1.0). Lock lintas-proses per brand (mis. baris `extract_lock` di ClickHouse atau file lock di volume
bersama) bisa ditambahkan nanti.

### SEC-186 (P3 / Low): tidak ada circuit breaker untuk `auth_failed`

**Bukti.** `run_cycle` menandai `auth_failed`, tetapi `_cmd_run` (`__main__.py:110-118`) memanggil brand yang sama lagi
setiap `ESB_INTERVAL_SECONDS`. Akibatnya 1 login gagal per jam (24 per hari) tanpa batas. Bila password berubah di tengah run,
login dari maksimal `workers*2` slice yang sedang berjalan menambah percobaan (`runner.py:142`, `http.py:293-296`). Runbook §7
mengandalkan operator untuk mengeluarkan brand secara manual.

**Rekomendasi.** Setelah N (mis. 2) siklus `auth_failed` berturut-turut, brand di-skip sampai proses di-restart atau secret
berubah. Catat `status=auth_suspended` di `extract_run`, lalu alert.

### SEC-187 (P3 / Low): respons tanpa batas ukuran

**Bukti.** `http.py:109` `response.read()` dan `:112` `exc.read()` tanpa batas, sementara memori container 512 MB.

**Rekomendasi.** Baca maksimal mis. 32 MB (`response.read(MAX + 1)`, lalu lempar `EsbProtocolError` bila melebihi). Tolak
`Content-Length` di atas batas sebelum membaca.

### SEC-188 (P3 / Low): grant `map_esb_odoo` lebih awal dari kebutuhan

**Bukti.** `esb-access.sql:32` memberi `INSERT` ke tabel yang kosong di G41 dan baru diisi G43. Kode `esb-extract` tidak
menulis ke tabel itu (`transform.MART_TABLES` tidak memuatnya).

**Rekomendasi.** Hapus grant tersebut sampai G43 menetapkan penulisnya. Pemetaan ke Odoo sebaiknya ditulis proses migrasi,
bukan service yang terhubung ke internet.

### SEC-189 (P3 / Info): retensi

**Bukti.** Mart `efn_dwh.*esb*` tidak punya TTL. Hanya `raw_doc` yang punya TTL (`12-g41-esb.sql:30`), sedangkan L-109
menyatakan "mart mengikuti DWH 10 tahun", yang pada mart lain ditegakkan lewat `MODIFY TTL ${RETENTION_YEARS}`
(`clickhouse-init.sh:150-168`). TTL `raw_doc` hanya ada di `CREATE TABLE IF NOT EXISTS`, jadi perubahan retensi berikutnya tidak
akan diterapkan ke tabel yang sudah ada. Raw 36 bulan melebihi kebutuhan G43 (cutover + rekonsiliasi).

**Rekomendasi.** Tambahkan tabel mart ESB ke blok `MODIFY TTL` `clickhouse-init.sh` (`RETENTION_YEARS`) dan pindahkan TTL
`raw_doc` ke sana. Pertimbangkan TTL raw = cutover + 6 bulan (keputusan DPO), karena residu PII dari SEC-181 ikut tersimpan
selama periode itu.

### Catatan Info (tanpa ID)
- `clickhouse-init.sh:204` `HOST ANY` untuk `efn_esb`: pola yang sama dengan user lain. Mitigasinya: ClickHouse hanya
  terjangkau dari network internal. Pembatasan `HOST IP '172.30.7.0/24'` bisa dipertimbangkan.
- Fixture test diperiksa sintetis (`sintetis-Rahasia-123`, `contoh.invalid`, "Toko Tujuh"). Satu nama CK di
  `test_transform.py` mungkin lokasi nyata; **sudah disamarkan** (efn-lead, 18 Nov 2026).
- Data nyata klien akan masuk ClickHouse **dev** saat diuji dengan akun admin (ADR-040:73). Ini bukan PII (bila SEC-181
  diperbaiki), tetapi rahasia dagang. Hapus volume dev setelah uji atau batasi rentang uji.

---

## Kontrol yang sudah benar (diverifikasi di kode)

| Area | Kontrol | Bukti |
|---|---|---|
| GET-only (L-106) | `check_request` dijalankan di `_send` untuk **setiap** percobaan sebelum transport. Hanya `POST /auth/login`. Path wajib berawalan `/`, charset terbatas, tanpa `..`/`//`, segmen tulis ditolak. Nomor dokumen dari server di-`quote(safe='')`, jadi `%`→`%25` dan `/`→`%2F` (tidak ada traversal) | `http.py:152-164, 227`; `extract.py:114` |
| Allowlist host | `ESB_BASE_URL` wajib `https`, host persis `services.esb.co.id`, port 443, tanpa userinfo/query; URL dibangun ulang dari konstanta. Overlay meng-hardcode URL, dicek `check-encryption.sh` | `config.py:90-103`; `docker-compose.esb.yml:50`; `check-encryption.sh:298-304` |
| TLS | certifi (di-pin), TLS ≥ 1.2, `check_hostname`, `CERT_REQUIRED`; transport menolak non-https. ClickHouse HTTPS 8443 + CA internal; plaintext ditolak di production (fail-closed SEC-27) | `http.py:76-81, 104`; `config.py:288-310`; `tls.py:26-39`; `store.py:29-32` |
| Redirect & proxy | `ProxyHandler({})` (proxy env diabaikan), `NoRedirect` (30x → error, header Authorization tidak dikirim ke host lain) | `http.py:84-99` |
| Secret | Password hanya dari `*_FILE`; placeholder (`disabled`, dll.) ditolak; `repr=False` pada password; `EsbClient.__repr__` menyamarkan; token hanya di memori; exception tanpa query/URL (`from None`); log hanya `type(exc).__name__`; heartbeat memakai `safe_token` (anti log injection); log `clickhouse_connect` dipaksa WARNING | `config.py:42-51, 175, 228-235, 281`; `http.py:213, 251, 267`; `runner.py:168-179`; `records.py:84-86`; `__main__.py:161` |
| Secret di repo | `secrets.list` `host:disabled` (tidak di-generate, tidak di GitHub Env); `secrets/`, `__pycache__`, `.ruff_cache` di-gitignore; tidak ditemukan password/JWT di file G41 | `scripts/secrets.list:68-76`; `.gitignore` |
| Supplier (L-110) | Whitelist `supplierID/Code/Name/flagActive` sebelum disimpan | `normalize.py:73, 90-91`; `extract.py:139-140` |
| Redaksi berlapis | `redact` di extractor + ulang di `raw_rows`; mart hanya kunci bisnis | `extract.py:115, 120`; `records.py:40` |
| SQL injection | Semua nilai lewat `parameters={...}`/`client.insert`. Nama DB diinterpolasi dari env operator (`CH_DATABASE`, `CH_RAW_DATABASE`), bukan input eksternal | `store.py:48-79` |
| Least privilege CH | Grant per tabel (tanpa wildcard), tanpa DDL (`allow_ddl=0 CONST`), batas memori/waktu; `metabase_reader` tanpa `raw_doc`/`bom_ratio`; `text2sql_reader` tidak melihat mart ESB (hanya `efn_t2s`) | `esb-access.sql`; `text2sql-access.sql:23, 67` |
| Container | Non-root 65534, `read_only`, tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges`, 512 MB/1 CPU, base image dipin digest, tidak di network `data`/`internal`, network `esb` internal; secret ESB hanya di overlay (dicek CI) | `docker-compose.esb.yml:37-96, 113-117`; `Dockerfile:3, 19`; `check-encryption.sh:288-312` |
| Beban (L-111) | Token bucket 2/dtk per brand, retry maks 5 (cap di kode), retry ikut dihitung budget, `Retry-After` dibatasi 60 dtk, budget per run/backfill, `ESB_MAX_PAGES`, jendela backfill, brand diproses berurutan | `http.py:29, 119-149, 167-173, 228-232`; `paging.py:78`; `runner.py:294-301` |
| Kill switch | `ESB_ENABLED=false` default, idle tanpa membaca secret; dicek render prod-esb | `__main__.py:166-170`; `check-encryption.sh:393-398` |
| Supply chain | Build/scan/sign di `build-push.yml`, pip-audit, Dependabot pip+docker, test CI path-filtered | `build-push.yml:71-79, 161-170`; `ci.yml:58-165`; `dependabot.yml:38, 97` |

## Yang belum diverifikasi
- Nama kunci nyata respons ESB (dasar SEC-181) dan perilaku lockout akun ESB (SEC-180, SEC-186).
- Test tidak dijalankan dalam review ini (read-only, tanpa shell). Kesimpulan didasarkan pada pembacaan kode dan test.
- `deploy.yml`/`deploy.sh` belum membawa overlay ESB (runbook §8), sehingga jalur rilis production untuk service ini belum direview.
