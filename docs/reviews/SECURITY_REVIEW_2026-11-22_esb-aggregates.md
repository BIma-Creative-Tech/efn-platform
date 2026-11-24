# Security Review — `feat/esb-daily-aggregates` (a57c1a1) — 22 Nov 2026

Reviewer: efn-security-reviewer (read-only). Lingkup: commit a57c1a1 di atas main a719566 — `infra/clickhouse/init/20-esb-aggregates.sql`,
`infra/clickhouse/access/{esb-access,insight-access}.sql`, `services/esb-extract/efn_esb/{aggregates,runner,__main__,store}.py`,
runbook `esb-extract.md` §6a, docs/04 §25.7/§26.7. Konteks: `clickhouse-init.sh` (loop row policy + pagar fail-closed),
`config.d/encrypted-storage.xml`, access file konsumen lain (text2sql, metabase, forecast, mig, segment, etl).
ADR terkait: ADR-040, ADR-041 (Proposed), K25-02. Risiko: R-S17 (akun/akses ESB), docs/04 §7.3 (least privilege), ADR-022 (enkripsi at rest).

## Ringkasan eksekutif

Materialisasi tiga view ESB lambat ke `efn_dwh.agg_esb_*` **tidak membuka kebocoran lintas PT**: `insight_reader` tetap tanpa hak di
`efn_dwh`, tiga wrapper `efn_insight.*` tetap view DEFINER yang tercakup row policy `efn_insight_scope` + pagar fail-closed, dan tidak ada
konsumen lain (text2sql/metabase/forecast/mig/segment) yang mendapat grant ke tabel agg. SQL dirender dari konstanta modul dan tanggal lewat
parameter; log dan `aggregate_refresh` tidak memuat pesan galat mentah. Dua catatan Medium: (1) grant `CREATE TABLE` + `allow_ddl = 1` tidak
membatasi **engine/setting** tabel yang dibuat esb_writer (`table_engines_require_grant` default false), dan (2) kunci build "basi 2 jam"
memakai waktu CREATE, bukan aktivitas terakhir, sehingga refresh panjang + refresh kedua bisa menerbitkan agregat ganda/campur.
**Verdict: boleh merge ke dev (tanpa Critical/High); SEC-AG-01 dan SEC-AG-02 wajib ditutup sebelum dipakai di efn-prod/pilot.**

## Tabel temuan

| ID | Severity | Lokasi | Ringkas | Risiko |
|---|---|---|---|---|
| SEC-AG-01 | Medium | `infra/clickhouse/access/esb-access.sql:13,20,64-69` | `CREATE TABLE` tingkat tabel + `allow_ddl=1` tidak membatasi engine/SETTINGS; esb_writer bisa membuat agg/build dengan `storage_policy='default'` (plaintext at rest) atau engine non-MergeTree | R-S17, ADR-022, docs/04 §7.3 |
| SEC-AG-02 | Medium | `services/esb-extract/efn_esb/aggregates.py:30,187-190,264-272` | Umur build = `metadata_modification_time` (waktu CREATE). Refresh > 2 jam dianggap basi oleh refresh kedua → build dihapus & dibuat ulang saat proses pertama masih INSERT → data ganda/campur di-EXCHANGE ke Insight | integritas data keuangan Insight |
| SEC-AG-03 | Low | `aggregates.py:286-293` | Tanpa pagar penyusutan: hasil 0 baris (atau turun drastis) tetap di-EXCHANGE dan menggantikan agregat yang sehat | ketersediaan Insight |
| SEC-AG-04 | Info | `efn_esb/config.py:320-321`, `aggregates.py:141-184` | Nama database (`CH_DATABASE`, `CH_RAW_DATABASE`) dari env disisipkan ke SQL tanpa validasi identifier (pola lama, nilai milik operator) | — |
| SEC-AG-05 | Info | `esb-access.sql:64-69`, runbook §6a | Hak `DROP TABLE` pada agg = esb-extract yang disusupi bisa mengosongkan/mematahkan 3 halaman Insight (setara dampak integritas INSERT yang sudah ada); runbook belum memuat langkah pulih bila tabel agg hilang | R-S17 |

## Detail

### SEC-AG-01 (Medium) — Grant CREATE TABLE tidak membatasi engine dan storage policy

**Lokasi:** `infra/clickhouse/access/esb-access.sql:11-23` (`allow_ddl = 1 CONST`), `:64-69` (`CREATE TABLE, DROP TABLE` pada 3 agg + 3 build).

**Yang sudah benar:** grant per nama tabel eksplisit (bukan wildcard `efn_dwh.agg_*`), jadi esb_writer **tidak** bisa membuat/menghapus tabel
lain, tidak punya `CREATE VIEW` / `CREATE DICTIONARY` / `CREATE DATABASE` / `CREATE TEMPORARY TABLE` (table function `url()/file()/remote()`
butuh CREATE TEMPORARY TABLE + grant sumber, keduanya tidak ada), tanpa ALTER/TRUNCATE/OPTIMIZE. esb_writer adalah satu-satunya role aplikasi
dengan CREATE (semua profil lain `allow_ddl = 0`, etl hanya ALTER DELETE pada dua tabel).

**Celah:** grant `CREATE TABLE ON efn_dwh.agg_esb_sales_daily_build` mengizinkan **DDL apa pun** dengan nama itu. ClickHouse 24.8 tidak
mewajibkan grant per engine kecuali server setting `table_engines_require_grant = true` (tidak diset di `infra/clickhouse/config.d/`; diakui
di laporan engineer baris 56). Skenario (prasyarat: kredensial `efn_esb` / container esb-extract disusupi):

1. `CREATE TABLE efn_dwh.agg_esb_sales_daily_build (...) ENGINE = MergeTree ORDER BY tuple() SETTINGS storage_policy = 'default'` →
   INSERT SELECT dari view → `EXCHANGE TABLES` → data penjualan lima PT tersimpan di disk `default` **tanpa enkripsi at rest**, melanggar
   prinsip "enkripsi di setiap transaksi data" (ADR-022; `encrypted-storage.xml` hanya menjadikan `encrypted` **default**, bukan wajib).
   Terverifikasi dari konfigurasi: policy `default` selalu ada di ClickHouse; tidak ada pembatas.
2. Engine non-MergeTree (Memory/Log/Null/Buffer/Merge/Distributed/Join) pada nama agg → Insight membaca tabel yang dikendalikan penyerang.
   **PLAUSIBLE** (tidak diuji di 24.8): engine `Buffer(db, tabel_lain, …)` mem-flush dengan konteks server dan `Distributed` memakai user
   cluster, keduanya historis pernah menjadi jalur bypass grant. Engine eksternal (URL/File/S3/MySQL/PostgreSQL) **seharusnya** ditolak
   karena CREATE memeriksa grant sumber (`URL`, `FILE`, …) yang tidak dimiliki esb_writer — verifikasi di 24.8.14. Disk dinamis
   `SETTINGS disk = disk(type = …)` (jalur SSRF/plaintext) juga **PLAUSIBLE**, perlu diuji.

Dampak integritas (mengganti isi Insight) sendiri tidak baru — esb_writer sudah bisa INSERT ke tabel fakta. Yang baru adalah kemampuan memilih
lapisan penyimpanan/engine, sehingga severity Medium, bukan High.

**Rekomendasi (urut prioritas):**
1. Tambah `config.d/table-engine-grant.xml`: `<clickhouse><table_engines_require_grant>true</table_engines_require_grant></clickhouse>` dan
   `GRANT TABLE ENGINE ON MergeTree TO esb_writer;` di `esb-access.sql`. Admin (`efn`) memiliki ALL sehingga init tidak terpengaruh;
   role lain tidak membuat tabel. Uji di 24.8: esb_writer `CREATE … ENGINE = Memory` / `Buffer` harus ditolak.
2. Kunci storage policy lewat constraint setting MergeTree di profil: `merge_tree_storage_policy = 'encrypted' CONST` pada
   `esb_writer_profile` (fitur constraint `merge_tree_*`; verifikasi sintaks di 24.8 — bila tidak didukung, lanjut ke butir 3).
3. Pagar fail-closed di `clickhouse-init.sh` (pola SEC-215): gagal bila salah satu `efn_dwh.agg_esb_*` bukan `engine = 'MergeTree'` atau
   `storage_policy != 'encrypted'`; dan cek yang sama di `refresh_one` terhadap build sebelum EXCHANGE (menangkap salah konfigurasi, bukan
   penyerang).
4. Alternatif paling ketat (tanpa CREATE sama sekali): admin membuat `agg` dan `agg_build` permanen di init; esb_writer hanya
   `INSERT, SELECT, TRUNCATE` pada build dan `ALTER TABLE agg REPLACE PARTITION <bulan> FROM agg_build` (butuh `SELECT` di build +
   `ALTER DELETE, INSERT` di agg). Swap atomik per bulan, engine/policy ditetapkan admin. Konsekuensi: perubahan kolom view harus lewat DDL
   init (yang memang lebih bisa diaudit). Ini keputusan desain — serahkan ke lead.

### SEC-AG-02 (Medium) — Kunci build "basi" memakai waktu CREATE; refresh panjang bisa ditimpa

**Lokasi:** `services/esb-extract/efn_esb/aggregates.py:30` (`STALE_BUILD_SECONDS = 2 * 3600`), `:187-190` (`BUILD_AGE_SQL` =
`dateDiff('second', metadata_modification_time, now())`), `:264-272` (`_claim_build`), `:289-293` (loop INSERT lalu EXCHANGE).

`metadata_modification_time` adalah waktu file metadata `.sql` ditulis (CREATE), **bukan** waktu INSERT terakhir. Satu refresh agregat =
`jumlah_bulan × ≤ 280 dtk` (`INSERT_SETTINGS`), jadi data ≥ ±26 bulan dengan bulan yang berat sudah bisa melewati 2 jam; runbook §6a
eksplisit menyuruh operator menjalankan `refresh-aggregates` manual saat loop `run` aktif ("refresh lain sedang berjalan (mis. loop run)"),
sehingga dua proses bersamaan adalah skenario normal.

Skenario: proses A (loop `run`) membangun `agg_esb_sales_daily_build` sejak T0, masih di bulan ke-20 pada T0+2j05m. Operator menjalankan
refresh manual B → B melihat umur > 7200 dtk → `DROP … SYNC` build A → `CREATE` build baru → mulai INSERT dari bulan 1. A melanjutkan INSERT
bulan 21..N ke **build milik B** (nama sama), lalu `count()` + `EXCHANGE` → Insight menerbitkan tabel berisi bulan 1..k dari B **ditambah**
bulan 21..N dari A; B kemudian INSERT ke tabel lama hasil exchange yang lalu di-DROP oleh A, atau EXCHANGE lagi dengan isi campuran. Hasil:
omzet/transaksi ganda atau bolong di dashboard keuangan lima PT tanpa galat apa pun (`status=ok`). Tidak ada kebocoran lintas PT
(company_code ikut dari fakta), tetapi integritas angka yang dipakai manajemen rusak.

Catatan tambahan: setelah EXCHANGE, nama `<agg>_build` berisi tabel lama yang `metadata_modification_time`-nya tua — bila DROP pasca-exchange
gagal, refresh berikutnya langsung menghapusnya (benar, tidak berbahaya).

**Rekomendasi:**
1. Catat `uuid` build dari `system.tables` tepat setelah CREATE; sebelum setiap INSERT dan sebelum EXCHANGE pastikan UUID nama build masih
   sama — bila berubah, hentikan tanpa EXCHANGE dan tanpa DROP (build milik proses lain). Perubahan kecil, menutup kelas masalah ini.
2. Ganti indikator "basi": anggap build masih dipakai bila ada query berjalan milik user yang sama yang menyebut build itu
   (`SELECT count() FROM system.processes WHERE query LIKE '%<build>%'` — user hanya melihat query miliknya sendiri, cukup karena hanya
   efn_esb yang menulis), atau naikkan ambang menjadi fungsi `jumlah_bulan × 300 dtk + margin`.
3. Tambah test: dua `refresh_one` berselang dengan build "tua" tidak boleh menghasilkan EXCHANGE dari proses yang kehilangan build.

### SEC-AG-03 (Low) — Hasil kosong/menyusut tetap di-EXCHANGE

**Lokasi:** `aggregates.py:285-293`. Bila `extract_checkpoint` tidak mengembalikan hari `done` untuk entitas (salah konfigurasi
`CH_RAW_DATABASE`, restore DR parsial, perubahan nama entitas) atau view sumber mendadak 0 baris (dimensi/checkpoint rusak), `months = []`
→ build kosong → `EXCHANGE` → tiga halaman Insight kosong dengan `status=ok`. Data sumber tidak hilang (pulih pada refresh berikutnya),
jadi Low, tetapi ini bertentangan dengan klaim runbook "bila refresh gagal, Insight tetap membaca isi sebelumnya".

**Rekomendasi:** sebelum EXCHANGE bandingkan `rows` dengan `count()` tabel agg saat ini; tolak (status `error`, kode mis. `shrink`) bila
`rows == 0` sementara agg berisi, atau turun > X% (mis. 50%), kecuali flag eksplisit `refresh-aggregates --allow-shrink` untuk kasus reset
yang disengaja. Hak `SELECT` pada agg sudah ada.

### SEC-AG-04 (Info) — Identifier database dari env tanpa validasi

**Lokasi:** `services/esb-extract/efn_esb/config.py:320-321` → `aggregates.py:141-184`, `store.py:75,84,92`.
Semua identifier lain berasal dari konstanta `AGGREGATES` (terverifikasi), nilai tanggal/entitas lewat `{start:Date}`, `{end:Date}`,
`{entities:Array(String)}`, `{db:String}`, `{name:String}`. Hanya `mart`/`raw` dari env `CH_DATABASE`/`CH_RAW_DATABASE` yang disisipkan
mentah (pola lama, sudah ada di `store.checkpoints`). Nilai dikendalikan operator dan grant per nama membatasi dampak, jadi bukan celah.
**Saran:** validasi `^[a-z_][a-z0-9_]{0,63}$` di `ClickHouseSettings.from_env` (satu baris, menutup kelas ini untuk semua query).

### SEC-AG-05 (Info) — DROP pada agg = kemampuan mengosongkan Insight; langkah pulih belum ada di runbook

**Lokasi:** `esb-access.sql:64-69`; `aggregates.py:293` (EXCHANGE gagal bila `agg` tidak ada).
`DROP TABLE` (juga `DETACH`, yang memakai hak yang sama) pada nama agg diperlukan untuk EXCHANGE dan tidak bisa dihindari tanpa opsi 4 di
SEC-AG-01. Bila tabel agg hilang (sengaja/tidak), setiap refresh gagal di EXCHANGE (`status=error`), wrapper `efn_insight.*` galat, dan
`20-esb-aggregates.sql` hanya membuat ulang saat `clickhouse-init` dijalankan. Dampak setara INSERT sampah ke fakta yang sudah mungkin →
diterima. **Saran:** tambahkan ke runbook §6a: "tabel agg hilang / `UNKNOWN_TABLE` → jalankan `clickhouse-init` (membuat ulang kosong) lalu
`refresh-aggregates`"; opsional, `refresh_one` membuat `agg` kosong dari `table_definition` bila belum ada (hak CREATE sudah dimiliki).

## Hal yang sudah benar (kontrol yang ada)

- **Multi-company:** `insight_reader` tetap `REVOKE ALL ON efn_dwh.*` (`insight-access.sql:42`); tiga wrapper tetap `CREATE OR REPLACE VIEW
  efn_insight.<nama sama> … DEFINER = efn_insight_definer SQL SECURITY DEFINER` dengan kolom eksplisit (termasuk `company_code`), sehingga loop
  `CREATE ROW POLICY … efn_insight_scope` (`clickhouse-init.sh:443-446`) dan pagar fail-closed (`:453-461`) berlaku tanpa perubahan.
  Grant di ClickHouse berbasis nama, jadi `EXCHANGE TABLES` tidak memutus grant definer maupun policy.
- **Konsumen lain:** grep seluruh `infra/clickhouse/access/*.sql` — `agg_esb_*` hanya di-grant ke `esb_writer` dan `efn_insight_definer`;
  tidak ada grant wildcard `efn_dwh.*` / `*.*` (text2sql SEC-206, forecast, mig, segment, metabase per objek atau REVOKE ALL). Metabase tetap
  membaca view `efn_dwh.v_esb_*` (INVOKER), bukan agg.
- **Definer minimum:** `SELECT v_esb_sales_branch_daily` dicabut dari definer karena tidak dirujuk lagi (`insight-access.sql:79`).
- **Grant esb_writer per nama eksplisit** (6 nama + `aggregate_refresh` + 3 view sumber); tanpa ALTER/TRUNCATE/OPTIMIZE; `map_esb_odoo` tetap
  di-REVOKE (SEC-188).
- **Enkripsi at rest jalur normal:** build dibuat `ENGINE = MergeTree` tanpa `storage_policy` → mewarisi default `encrypted`
  (`encrypted-storage.xml`). Koneksi HTTPS + verifikasi CA internal (`store.py:30-33`, gagal bila `CH_CA_FILE` kosong).
- **SQL injection:** identifier dari konstanta modul; tanggal/entitas/nama tabel di `system.tables` lewat parameter bertipe.
- **Log & PDP:** `error_label` hanya `NamaKelas code=<n>`; log per agregat hanya nama, status, jumlah baris/bulan, durasi; `_cmd_refresh_aggregates`
  hanya `type(exc).__name__` (tidak membocorkan path secret); `aggregate_refresh` tanpa data pribadi, TTL 400 hari; agregat tanpa identitas
  orang (L-110).
- **Ketersediaan:** EXCHANGE atomik di database Atomic (default 24.8, `efn_dwh` tanpa engine eksplisit); kegagalan sebelum/saat EXCHANGE
  menghapus build milik proses ini dan tabel lama tetap dibaca (`aggregates.py:294-299`); CREATE build tanpa `IF NOT EXISTS` sehingga balapan
  CREATE dimenangkan satu proses dan yang kalah tidak menghapus build pemenang (`_claim_build` di luar `try`); kegagalan refresh tidak mengubah
  exit code ekstraksi dan ditandai ulang untuk siklus berikutnya; `max_execution_time` 280 < MAX profil 900 dan < timeout klien 300.

## Belum terverifikasi

- Tidak ada yang dijalankan (review statis; `git diff` tidak dijalankan, file dibaca dari tree branch).
- Perilaku ClickHouse 24.8.14: penolakan engine eksternal oleh pemeriksaan grant sumber, perilaku Buffer/Distributed/disk dinamis terhadap
  grant (SEC-AG-01 butir 2 = PLAUSIBLE), ketersediaan `table_engines_require_grant` dan constraint `merge_tree_storage_policy` di versi ini.
- Durasi nyata refresh per bulan untuk data multi-tahun (menentukan seberapa cepat SEC-AG-02 muncul).
