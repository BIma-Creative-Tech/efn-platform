# ADR-040: Integrasi ESB Core → Datamart ClickHouse (ekstraksi GET-only per brand)

- **Status:** Proposed — belum disetujui klien. G41 boleh dibangun mengikuti rekomendasi ini dengan `ESB_ENABLED=false`
  default; ekstraksi production menunggu **Q-ESB1** (user API read-only) dan **Q-ESB7** (izin beban backfill).
- **Tanggal:** 2026-11-17 (Gelombang 41) · revisi 2026-11-18: §3.1 butir 1, §3.2, §4.4 dipersempit mengikuti ADR-042
  (Accepted); keputusan ADR-040 sendiri tetap Proposed
- **Pemutus:** pemilik proyek (lingkup & cutover), IT klien (user API ESB, beban server), Finance (cutover & saldo awal
  G43), Legal/DPO (kepemilikan data & PDP)
- **Referensi review:** R-O02 (migrasi data sistem lama), R-O01 (master data), R-L01 (PDP), R-O03 (beban ETL),
  usulan R-O09, R-O10, R-S17 (`docs/01` §4); Q5 (sistem lama yang harus dimigrasi) dan Q-ESB1..Q-ESB9 (`docs/01` §5.12,
  `docs/04` §25.9)
- **Terkait:** ADR-003 (ETL 15 menit, Accepted 25 Okt), ADR-004 (topologi), ADR-007 (PG source of truth), ADR-010 (AI di
  luar jalur transaksi), ADR-022 (TLS), ADR-024 (kredensial per service), ADR-037 (pola egress allowlist), ADR-038
  (`efn_t2s` row policy), K25-02 (stack data terpisah), K25-12 (forecast CK), `docs/04` §25

> **Ringkasan manajemen.** Saat ini kelima brand (Paris Baguette, Wetzel's, Bacha, Im Donut, Curry Up) masih berjalan di
> ESB. Kami menarik data ESB **hanya dengan membaca** (tanpa mengubah apa pun di ESB) ke gudang data EFN, satu akun API
> per brand, pada jam sepi malam. Hasilnya dipakai untuk dashboard (G42), migrasi ke Odoo (G43), dan prediksi permintaan
> (G44). Data pelanggan/member **tidak** ditarik. Biaya: ±2,5–3,5 minggu-engineer, Rp0 lisensi. Yang dibutuhkan dari
> klien: **akun API ESB yang hanya bisa membaca** (saat ini akun admin) dan izin menarik 24 bulan histori.

## 1. Konteks

### 1.1 Kebutuhan
- Klien memakai **ESB Core + ESB POS** (SaaS, `https://services.esb.co.id/core`) untuk lima brand. Prototipe G41
  (17 Nov 2026, skrip di luar repo, PB saja, satu bulan) membuktikan API ESB cukup untuk merekonstruksi alur produk jadi
  CK → kirim → produksi toko → transfer → jual.
- Permintaan pemilik proyek (rencana induk G41–G44): (1) datamart dari API ESB untuk dashboard dan sumber migrasi;
  (2) dashboard Next.js dengan login akun Odoo (G42); (3) migrasi master + saldo awal + histori ke Odoo (G43);
  (4) satu brand = satu user API; (5) Im Donut (IMD) & Curry Up (CUP) menjadi company baru; histori 24 bulan.

### 1.2 Pertentangan dengan dokumen/prinsip yang berlaku
| Sumber | Bunyi | Pertentangan |
|---|---|---|
| `CLAUDE.md` prinsip | "PostgreSQL = source of truth; ClickHouse hanya turunan via ETL incremental 15 menit dari replica … **Tidak ada tulis langsung API → ClickHouse**." | `esb-extract` menulis ke ClickHouse dari API eksternal, bukan dari replica PostgreSQL. |
| ADR-003 (Accepted 25 Okt) | ETL batch 15 menit dari replica, tanpa CDC. | Data ESB tidak ada di PostgreSQL mana pun; jadwal ESB = per jam / harian, bukan 15 menit. |
| ADR-014 / ADR-037 | Container data/AI tanpa egress kecuali lewat overlay ber-allowlist. | Ekstraksi butuh egress internet ke host ESB. |
| `docs/01` R-O02 | "Migrasi data dari sistem lama tidak disebut sama sekali." | Sistem lama kini diketahui = ESB; ADR ini menjadi dasar migrasi. |
| Rencana induk | "`fact_esb_sales_line` (hari×cabang×menu×produk resep)" dan "`v_esb_hourly` (bila tersedia)" | API laporan baris (`/report/sales-menu-cogs`) tidak memuat jam → view per jam tidak dibuat (L-112). Grain resep = tabel terpisah `fact_esb_fin_sold_daily`. |

### 1.3 Fakta API (diverifikasi 17 Nov 2026 lewat prototipe; spec publik `developers.esb.co.id/esb-core/openapi.json`)
- Login `POST /auth/login` → Bearer JWT; 401 → login ulang.
- `/report/sales-menu-cogs` = level baris transaksi-menu, **limit tetap 20 baris/halaman** (parameter `limit` diabaikan),
  ±0,3–0,5 dtk/halaman, ±450 baris/toko/hari → **±23 panggilan/toko/hari**. Tanpa jam transaksi.
- `/report/sales-menu-cogs-detail?mode=recap&productName=FIN` ±3 panggilan/toko/hari; `/report/sales-payment-summary`
  1 panggilan/hari untuk semua cabang.
- Spec memuat banyak endpoint tulis (`POST …/push`, `…/import`, `…/authorize`) yang **bisa dipanggil akun admin** yang
  saat ini dipakai → risiko R-S17.
- `/inventory/stock-list` menerima `stockDate` + `branchID` (menurut spec; **belum diuji di production**).

## 2. Opsi yang dipertimbangkan

| # | Opsi | Kelebihan | Kekurangan | Biaya / waktu (perkiraan architect) |
|---|---|---|---|---|
| **A** | **Service baru `esb-extract`** (Python, GET-only, satu-satunya egress ke host ESB) → raw JSON + mart di **ClickHouse** | Otomatis, incremental, bisa diulang (idempoten), backfill bisa dilanjutkan; ETL & Odoo tetap tanpa internet; pas dengan K25-02 (pindah ke mesin data) | Satu service + image + secret baru; pengecualian terbatas prinsip "tanpa tulis langsung ke ClickHouse" (§3.2) | **±2,5–3,5 minggu-engineer** (data 2–2,5, devsecops 0,5–1), Rp0 lisensi, RAM ±256–512 MB |
| B | Ekspor CSV/Excel manual dari layar ESB per brand, diunggah staf | Hampir tanpa kode, tidak butuh akun API | Pekerjaan manual harian × 5 brand; tidak ada incremental/koreksi; format laporan bisa berubah tanpa pemberitahuan; rentan salah unggah; histori 24 bulan = ratusan berkas; tidak mendukung forecast harian | Build ±1 minggu, lalu ±0,5–1 jam staf/brand/hari (±10–20 jam/minggu untuk 5 brand) — biaya operasional berulang |
| C | Beri egress internet ke **ETL yang sudah ada** dan tambahkan job ESB | Tanpa service baru (hemat ±3–4 hari) | ETL memegang kredensial replica Odoo + PG platform + ClickHouse writer; menambah internet memperbesar dampak bila ETL disusupi (ADR-024); gagal ESB mengganggu ETL 15 menit; melanggar isolasi ETL | Hemat ±3–4 hari, risiko keamanan naik — **ditolak** |
| D | Tulis langsung **ESB → Odoo** (tanpa datamart) | Satu langkah | Histori sebagai `stock.move`/`account.move`/`pos.order` merusak valuasi stok, pajak, dan penomoran dokumen; tidak ada dashboard/forecast; tidak ada tempat rekonsiliasi/uji ulang; fit-gap belum ada | Tampak cepat, tetapi koreksi pasca-cutover mahal (minggu-engineer + koreksi jurnal) — **ditolak** |
| E | Raw ESB ke **PostgreSQL** (skema staging) lalu ETL 15 menit → ClickHouse | Patuh literal pada "PG source of truth" | Raw 24 bulan menambah beban & ukuran backup PG primary (WAL-G, R-T08); PG bukan pemilik data ini; dua lompatan untuk data yang sama; bertentangan dengan K25-02 (data stack bisa di mesin lain) | +±1 minggu-engineer, + ruang PG & backup — **ditolak** |
| F | Minta vendor ESB memberi ekspor database / feed data warehouse | Paling ringan untuk server ESB | Belum diketahui tersedia; ketergantungan kontrak vendor | Tidak diketahui — **dicatat sebagai pertanyaan** (Q-ESB9), tidak memblokir A |

## 3. Keputusan (direkomendasikan)

### 3.1 Pilih opsi A
1. **ESB Core = sistem sumber eksternal (legacy)** per brand sampai tanggal cutover brand tersebut (Q-ESB6). Setelah
   cutover, **Odoo menjadi sumber kebenaran transaksi**; ekstraksi brand itu dihentikan (brand dikeluarkan dari
   `ESB_BRANDS`) setelah rekonsiliasi terakhir, dan mart ESB menjadi **arsip histori read-only yang tetap menjadi sumber
   angka resmi histori pra-cutover**. *(Dipersempit 18 Nov 2026 mengikuti ADR-042 Accepted:)* yang disalin ke Odoo
   hanya arsip baca-saja `efn_legacy_history` — aliran produk harian (default 400 hari, untuk forecast), penjualan
   harian outlet × menu (24 bulan), dan baris penjualan (parameter `efn_mig_esb.sales_line_months`, default 24 bulan);
   dokumen produksi, delivery, transfer, dan pembayaran per dokumen **tidak** disalin ke Odoo (tetap di mart).
2. **Service `services/esb-extract`** (Python 3.12, paket `efn_esb`) lewat overlay `infra/compose/docker-compose.esb.yml`
   (profil `data`, siap pindah ke project `efn-data`, K25-02). Satu-satunya komponen dengan egress ke
   `services.esb.co.id:443` (allowlist host di aplikasi; network `egress`), dan hanya menjangkau ClickHouse lewat network
   internal `esb` (tidak di `data`: tidak menjangkau PostgreSQL/RabbitMQ). ETL, Odoo, BFF tetap tanpa egress ke ESB.
3. **GET-only**: klien HTTP menolak method selain `GET`, kecuali `POST /auth/login` (ditegakkan di kode + test).
   Satu user API per brand (`ESB_USER_<BRAND>`, password dari `/run/secrets/esb_core_password_<brand>`). Klien diminta
   membuat **user API read-only** (Q-ESB1); sampai ada, akun admin yang ada dipakai **hanya di dev/uji** (R-S17).
4. **Alur**: fetch → `efn_esb.raw_doc` (JSON apa adanya minus field identitas, L-110) → transform (logika prototipe
   `aggregate.py`, ditulis ulang teruji) → `efn_dwh.dim_esb_* / fact_esb_*` (ReplacingMergeTree) → checkpoint
   `efn_esb.extract_checkpoint` ditulis **terakhir**. Idempotensi = **versi slice**: semua baris fakta satu slice
   (brand, entitas, cabang/`*`, hari) membawa `_version` = `finished_at` checkpoint, dan view hanya membaca versi slice
   yang punya checkpoint, sehingga tarik ulang mengganti isi hari itu (termasuk baris yang dihapus di ESB). Batasan teruji:
   bila checkpoint gagal ditulis setelah fakta, hari itu tampak kurang sampai ditarik ulang (`docs/04` §25.4).
5. **Cakupan per `company_code`** (bukan `company_id`): PB, WTZ, BCF/BCR (override per cabang), IMD, CUP. IMD/CUP belum
   ada di Odoo (company dibuat di G43) sehingga `company_id` tidak tersedia.
6. **Retensi**: raw 36 bulan (TTL `doc_date`); mart mengikuti retensi DWH **10 tahun** (keputusan #59 / Q-6).
7. **PDP**: customer/member ESB **tidak ditarik** di G41. Supplier hanya kode, nama perusahaan, status.
8. **Beban server ESB**: token bucket per brand (default 2 req/dtk, 4 worker), backoff 429/5xx, batas panggilan per run,
   backfill 24 bulan hanya di jendela 22:00–05:00 WIB, satu brand pada satu waktu (§4.2).
9. Kill switch `ESB_ENABLED=false` (default) → service idle tanpa memanggil apa pun.

```mermaid
flowchart LR
  subgraph ESB["ESB Core (SaaS, milik vendor ESB)"]
    API["services.esb.co.id/core<br/>GET + POST /auth/login"]
  end
  subgraph DATA["Stack data (profil data / efn-data, K25-02)"]
    X["esb-extract<br/>(efn_esb, GET-only,<br/>token bucket per brand)"]
    RAW[("efn_esb.raw_doc<br/>TTL 36 bln")]
    CP[("efn_esb.extract_checkpoint<br/>extract_run")]
    MART[("efn_dwh.dim_esb_* / fact_esb_*<br/>v_esb_* (FINAL)")]
  end
  API -- "HTTPS, CA publik, user per brand" --> X
  X --> RAW --> X
  X --> MART
  X --> CP
  MART -- "G42 insight-web (SSO Odoo, ADR-041)" --> W["Dashboard"]
  MART -- "G43 bundel berkas (master, saldo awal, arsip histori; ADR-042)" --> O["Odoo CE 19<br/>efn_legacy_history (arsip read-only)"]
  MART -- "G44 forecast-worker" --> F["fact_forecast_daily"]
```

### 3.2 Kenapa ini tidak melanggar "PostgreSQL = source of truth"
- Prinsip itu melindungi **data transaksi EFN** (order, poin, stok, jurnal) yang dibuat oleh sistem EFN: sumbernya PG,
  ClickHouse hanya salinan. Data ESB **bukan milik platform EFN dan tidak pernah ada di PG**; sumber kebenarannya adalah
  database ESB (sistem eksternal). Mart ESB tetap **turunan** (salinan sistem lain), tidak pernah menjadi sumber untuk
  menulis balik ke sistem transaksi mana pun.
- Larangan "tulis langsung API → ClickHouse" ditujukan ke jalur transaksi (BFF/Odoo menulis ke ClickHouse sehingga
  ClickHouse menjadi satu-satunya catatan). Di sini penulis adalah **job batch ekstraksi**, bukan API transaksi, dan
  isinya bisa dibangun ulang kapan saja dari ESB (sampai cutover) atau dari raw 36 bulan.
- Setelah cutover, Odoo (PG) kembali menjadi satu-satunya sumber **transaksi**; salinan histori di Odoo hanya arsip
  read-only `efn_legacy_history` (G43, ADR-042 §3.1) yang tidak pernah menjadi transaksi Odoo, dan selisihnya terhadap
  mart = cacat migrasi (mart tetap acuan angka histori pra-cutover). Jalur ke Odoo = bundel berkas, bukan koneksi
  ClickHouse → Odoo.
- Pengecualian ini **terbatas**: hanya `esb-extract`, hanya database `efn_esb` dan tabel `efn_dwh.*esb*`. Prinsip
  `CLAUDE.md` perlu catatan rujukan ke ADR-040 (tugas efn-lead).

## 4. Konsekuensi

### 4.1 Positif
- Dashboard dan forecast bisa dimulai **sebelum** migrasi Odoo selesai; migrasi G43 membaca sumber yang sudah
  dibersihkan, teruji, dan bisa direkonsiliasi (jumlah & total per entitas).
- Isolasi: kegagalan ESB / `esb-extract` tidak memengaruhi POS, BFF, ETL, atau Odoo.

### 4.2 Beban API & backfill (angka agregat, dari prototipe)
| Komponen | Panggilan per toko-hari | Catatan |
|---|---|---|
| `sales-menu-cogs` (baris) | ±23 | limit tetap 20; ±450 baris/toko/hari |
| `sales-menu-cogs-detail` recap FIN | ±3 | limit tetap 20 |
| payment summary, produksi, delivery, transfer, master | kecil (per brand-hari, bukan per toko) | detail dokumen 1 panggilan/dokumen |

- Perkiraan: **±26–30 panggilan per toko-hari**. Pada 2 req/dtk per brand → ±13–15 dtk per toko-hari. Backfill 24 bulan
  untuk **10 toko** ≈ 7.300 toko-hari ≈ 190–220 ribu panggilan ≈ **26–31 jam efektif** ≈ **4–5 malam** jendela 7 jam.
  Skala linear dengan jumlah toko; brand dijalankan **berurutan** (operasional) agar beban puncak ke ESB tetap
  ±2 req/dtk. Contoh: 60 toko total ≈ 25–30 malam (±1 bulan kalender). Menaikkan rate ke 4 req/dtk memangkas setengahnya
  hanya bila ESB mengizinkan (Q-ESB7). Perkiraan engineer data (README `esb-extract`): PB ±25 toko ≈ **9–10 malam**.
  Satu proses backfill dibatasi `ESB_BACKFILL_MAX_CALLS`=60000 (≈ satu malam), lalu dijalankan ulang malam berikutnya.
- Incremental harian (sekali/hari setelah 03:00 WIB): hari H-1 dan H-7 (`ESB_CORRECTION_DAYS=1,7`) + gap fill hari tanpa
  checkpoint (maks 7 hari) → ±2× beban satu hari per toko dalam kondisi normal; untuk 60 toko ±3–4 ribu panggilan/hari
  (±30 menit pada 2 req/dtk). Entitas `branch` tiap siklus dan `master` sekali/hari (beban kecil). Belum terukur di
  production.

### 4.3 Negatif / utang teknis yang diterima
- Ketergantungan pada API yang tidak kami kontrol: perubahan skema/perilaku (mis. batas 20 baris) memutus ekstraksi
  (R-O09) → test kontrak dengan fixture sintetis, `v_esb_data_quality`, alert umur data.
- Dokumen ESB yang dihapus/diubah **lebih dari 7 hari** setelah tanggalnya tidak tertangkap incremental → rekonsiliasi
  bulanan total per brand (backfill ulang satu bulan) sebelum cutover (L-114).
- Akun admin dipakai sampai Q-ESB1 dijawab (R-S17).
- Network Docker `egress` tidak membatasi tujuan di level jaringan (sama dengan ADR-037); allowlist di aplikasi.
  Rekomendasi: proxy egress allowlist sebelum production.
- Prinsip `CLAUDE.md` butuh catatan pengecualian (lihat §3.2).

### 4.4 Dampak ke gelombang berikutnya (desain G41 tidak boleh menghalangi)
| Gelombang | Yang disiapkan G41 | Keputusan menyusul |
|---|---|---|
| **G42** insight-web (Next.js) + SSO dari Odoo | View `v_esb_*` stabil (nama di `docs/04` §25.5), kolom `company_code` di semua mart → row policy per PT bisa ditempel tanpa mengubah tabel | User ClickHouse `efn_insight` + row policy bertanda tangan (pola `efn_t2s`, ADR-038) **tidak** dibuat di G41; modul `efn_insight` butuh fit-gap `efn-odoo-functional` + ADR tersendiri (token sekali pakai ≤ 60 dtk) → **ADR-041** (G42, `docs/04` §26) |
| **G43** migrasi master + saldo awal + histori | `map_esb_odoo` (kosong), `dim_esb_*` sebagai sumber master, `fact_esb_*` sebagai histori | **ADR-042 (Accepted 18 Nov 2026, dengan amendemen)** — *dipersempit dari rumusan awal "histori 24 bulan masuk `efn_legacy_history`"*: mart tetap sumber angka resmi histori; Odoo menerima arsip read-only `efn.legacy.flow.daily` (400 hari default, maks 730, input forecast), `efn.legacy.sales.daily` (24 bulan) dan `efn.legacy.sales.line` (parameter `efn_mig_esb.sales_line_months`, default 24), `company_id` + record rule, **bukan** `stock.move`/`account.move`/`pos.order`; produksi/delivery/transfer/pembayaran per dokumen tidak disalin; saldo awal = `stock.quant` inventory mode + jurnal pembukaan draft per tanggal cutover; company IMD/CUP mengikuti pola G24 WTZ (dibangun G43-A); pilot PB |
| **G44** forecast worker | `v_esb_sales_daily` & `fact_esb_fin_sold_daily` sebagai input harian | Model, kalender libur, output `fact_forecast_daily`; tetap proposal yang disetujui planner (K25-12), AI di luar jalur transaksi (ADR-010) |

### 4.5 Dampak ke dokumen lain
- `docs/04` §25 (kontrak tabel/view/env) — dibuat bersama ADR ini.
- `docs/01` §5.12 (Q-ESB1..9) dan usulan risiko R-O09, R-O10, R-S17.
- `docs/compliance/PDP_DATA_INVENTORY.md`: entri baru "Datamart ESB" (raw tanpa identitas staf/pelanggan; nama supplier
  perorangan = data pribadi bila supplier adalah orang) — **belum dibuat**, tugas architect/DPO gelombang berikut.
- `CLAUDE.md`: catatan pengecualian prinsip ClickHouse (efn-lead).

## 5. Risiko terkait
| ID | Ringkas | Mitigasi di G41 |
|---|---|---|
| R-O02 | Migrasi sistem lama | Datamart = staging migrasi, rekonsiliasi per entitas (G43) |
| R-O01 | Master data tidak seragam | `dim_esb_product_alias` (normalisasi nama), `NAME:<alias>` untuk yang tak terpetakan, `v_esb_data_quality` |
| R-L01 | PDP | Customer/member tidak ditarik; raw tanpa field identitas; supplier minimal |
| **R-O09** (usulan) | ESB mengubah skema/perilaku API | Parser toleran + test kontrak sintetis + alert umur data + `extract_run.errors` |
| **R-O10** (usulan) | Beban ekstraksi mengganggu server ESB / akun diblokir | Token bucket, backoff, `ESB_MAX_CALLS_PER_RUN`, jendela malam, satu brand per waktu, izin klien (Q-ESB7) |
| **R-S17** (usulan) | Akun admin ESB dipakai ekstraksi (bisa menulis ke ERP produksi klien) | GET-only di kode + test, secret per brand, egress allowlist, user read-only (Q-ESB1), rotasi password setelah user read-only tersedia |

## 6. Pertanyaan terbuka (dijawab klien)
Rinci di `docs/04` §25.9 dan `docs/01` §5.12: **Q-ESB1** user API read-only per brand · **Q-ESB2** WP = Wetzel's
Pretzels (WTZ)? (**terjawab 18 Nov 2026: ya**) · **Q-ESB3** pemetaan cabang Bacha ke BCR/BCF · **Q-ESB4** 11 cabang PB tanpa data produk FIN ·
**Q-ESB5** nama PT, NPWP, jenis pajak Im Donut & Curry Up · **Q-ESB6** tanggal cutover per brand · **Q-ESB7** izin beban
backfill 24 bulan (rate, jendela) · **Q-ESB8** daftar cabang CK per brand · **Q-ESB9** hak EFN atas data & penggunaan API
massal menurut kontrak ESB / ketersediaan ekspor resmi.

## 7. Kapan ditinjau ulang
- Q-ESB1 atau Q-ESB7 dijawab (mengubah status ekstraksi production).
- ESB mengubah API (alert R-O09) atau vendor menawarkan ekspor resmi (opsi F).
- Cutover brand pertama (G43) — ekstraksi brand itu dihentikan, mart menjadi arsip.
- Paling lambat **28 Feb 2027**, atau saat seluruh brand sudah cutover (ADR ini lalu `Superseded` oleh catatan arsip).
