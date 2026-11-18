# Runbook: migrasi ESB → Odoo (`esb-migrate` + importer Odoo, G43)

Keputusan: `docs/decisions/ADR-042-migrasi-esb-odoo-dan-histori.md` (**Accepted** 18 Nov 2026; pilot = Paris Baguette,
company `PB`) · Kontrak: `docs/04_SERVICE_CONTRACTS.md` §27 · Fit-gap: `docs/fit-gap/G43_MIGRASI_ESB_ODOO.md` ·
Kode sisi data: `scripts/migration` (README = format bundel) · Importer Odoo: `scripts/migration/odoo_import_esb.py`
(efn-odoo-engineer) · Modul histori: `services/odoo/addons/efn_legacy_history` · Ekstraksi: [esb-extract.md](esb-extract.md).

> **Status 18 Nov 2026: belum pernah dijalankan end-to-end.** Semua perintah di bawah ditulis dari kode dan kontrak,
> belum diuji terhadap ClickHouse/Odoo nyata. Dry-run PB di dev (§4) adalah uji pertama; catat setiap penyimpangan di
> laporan agent dan perbaiki runbook ini.

## 0. Ringkas

| Hal | Nilai |
|---|---|
| Alur | `esb-extract` (snapshot P1 + backfill) → ClickHouse `efn_dwh` → `esb-migrate export` (bundel `.efnmig` AES-256-GCM) → `validate_import.py` (laporan bertanda tangan) → `run-import.sh` (importer `odoo shell` di container Odoo **sekali jalan**, dry-run = rollback) → `<run_id>.result.efnmig` → `writeback` (`efn_dwh.map_esb_odoo`) + `reconcile` → `reconcile --final` → berita acara |
| Lokasi | **Hanya di infrastruktur EFN** (dev/staging/production EFN). **Tidak** dijalankan di VPS pihak ketiga (host B ADR-043 §3.4, [vps-bima.md](vps-bima.md) B6): di sana kedua secret migrasi wajib `disabled` (SEC-199) |
| Container | `esb-migrate` (overlay `docker-compose.esb.yml`, **profil `migrate`**, sekali jalan `docker compose run --rm`) — hanya network `esb` (ClickHouse HTTPS 8443, CA internal), **tanpa egress**, `read_only`, tmpfs `/work` 768 MB, limit 1536M |
| Sisi Odoo | overlay `docker-compose.migration.yml` (HANYA selama jendela migrasi): `migration_bundle_key`, direktori pertukaran `/mnt/efn-mig`, kode `scripts/migration` hanya-baca di `/opt/efn-migration`. Odoo tidak pernah terhubung ke ClickHouse (L-129) |
| Secret | `clickhouse_password_mig` (user ClickHouse `efn_mig`, role `mig_rw`) dan `migration_bundle_key` (64 hex, `[SA]`). Jenis `host:disabled`: produksi diisi Superadmin di host saat jendela dibuka ([key-custody.md §4f](key-custody.md#migrasi-esb)), dikembalikan `disabled` saat ditutup. Dev/CI: nilai nyata dari `APP_ENV=dev bash scripts/gen-secrets.sh` (tanpa `APP_ENV` dev/ci/test ditulis `disabled`, SEC-199) |
| User Odoo | `efn_migration`: tanpa password/API key, grup minimum, anggota company target saja; `active=True` hanya selama jendela (§2.6, §7.4; SEC-205) |
| Pertukaran | `${MIG_EXCHANGE_DIR}` (host) = `/out` di esb-migrate = `/mnt/efn-mig` di odoo. Disk terenkripsi, mode 0700. Isi = data klien: **tidak boleh** di repo, tiket, chat, e-mail |
| Exit code | 0 OK · 1 data/selisih rekonsiliasi · 2 argumen/konfigurasi · 3 integritas (dekripsi/HMAC/hash) · 4 gerbang (PT diblokir, hasil dry-run ke writeback) |
| Retensi | bundel, hasil impor, laporan validator, dump `odoo_before_*` dihapus **≤ 90 hari setelah berita acara** brand (Q-ESB24, §7); setelah brand terakhir `migration_bundle_key` = `disabled` + salinan tersegel `[SA]` dimusnahkan (crypto-erase salinan yang terlewat) |
| Risiko | R-O02 (data induk), R-O12/R-O13 (histori/rekonsiliasi), R-L01 (PDP), R-L12 |

## 1. Variabel shell

Git Bash di laptop dev, dari root repo. `MSYS_NO_PATHCONV=1` wajib agar argumen path container (`/out/...`, `/maps`)
tidak diubah Git Bash menjadi path Windows.

```bash
export MSYS_NO_PATHCONV=1
cd infra/compose
DC="docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.esb.yml --profile core --profile data"
# + sisi Odoo jendela migrasi + profil migrate (esb-migrate)
DCM="docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.esb.yml -f docker-compose.migration.yml --profile core --profile data --profile migrate"
MIG="$DCM run --rm -e EFN_GIT_SHA=$(git rev-parse HEAD) esb-migrate"
ODOO_DB=$(grep -E '^ODOO_DB=' .env | cut -d= -f2); ODOO_DB=${ODOO_DB:-efn_odoo}
MIG_EXCHANGE_DIR=$(grep -E '^MIG_EXCHANGE_DIR=' .env | cut -d= -f2 | awk '{print $1}')   # path HOST direktori pertukaran
chq() { $DC exec -T clickhouse sh -c 'CLICKHOUSE_USER="${CLICKHOUSE_USER:-efn}" CLICKHOUSE_PASSWORD="$(cat /run/secrets/clickhouse_password)" clickhouse-client --host clickhouse --multiquery'; }
psqlo() { $DC exec -T postgres psql -U "${PG_SUPERUSER:-postgres}" -d "$ODOO_DB" -v ON_ERROR_STOP=1 "$@"; }
```

Staging/prod lokal (K25-06): ganti `docker-compose.dev.yml` dengan `docker-compose.prod.yml` (+ `docker-compose.host.yml`
di host) dan nama project environment yang bersangkutan.

## 2. Membuka jendela migrasi (sekali per environment)

0. **Lokasi**: hanya lingkungan EFN. Di host VPS pihak ketiga (B, ADR-043) langkah ini **tidak** dilakukan; `export`
   tidak dijalankan di sana (data dipindah ke infrastruktur EFN lebih dulu).
1. **Secret** (Superadmin/operator; nilai tidak pernah di-echo ke log/tiket):
   - Dev/CI: `APP_ENV=dev bash scripts/gen-secrets.sh` (idempoten; file berisi `disabled` diganti nilai acak). Tanpa
     `APP_ENV=dev|ci|test` skrip menulis `disabled` (SEC-199) — `APP_ENV` di sini = env shell, bukan `.env` compose.
   - Staging/prod (host): prosedur [key-custody.md §4f](key-custody.md#migrasi-esb) (dibuat di host, `root:efn-superadmin
     0444` dalam direktori 0750, hash dicatat di register kunci). **Jangan** menjalankan `gen-secrets.sh` di host.
     `migration_bundle_key` hilang = semua bundel/hasil tidak bisa dibuka lagi (aman, tetapi ekspor harus diulang).
     Satu kunci per environment; **jangan** menyalin kunci dev ke staging/prod atau sebaliknya.
2. **User ClickHouse `efn_mig`**: `$DC up -d --force-recreate clickhouse-init` → log berisi `create_user efn_mig`
   tanpa baris "user efn_mig dihapus", exit 0 (`$DC ps -a clickhouse-init`). Uji hak (harus berhasil / ditolak):
   ```bash
   chq <<'SQL'
   SHOW GRANTS FOR efn_mig;
   SQL
   ```
   Harus: `mig_rw` saja (SELECT dim/fact/view ESB, INSERT `efn_dwh.map_esb_odoo`); tanpa CREATE/ALTER/DROP, tanpa
   SELECT `efn_esb.raw_doc`.
3. **Direktori pertukaran & peta** di disk terenkripsi (LUKS di host Linux; BitLocker di laptop Windows), lalu isi
   `MIG_EXCHANGE_DIR` / `MIG_MAPS_DIR` di `infra/compose/.env`. Compose **tidak** membuat direktori ini
   (`create_host_path: false`): direktori belum ada → `run` gagal, bukan membuat folder root di tempat salah.
   ```bash
   ODOO_UID=$($DC exec -T odoo id -u | tr -d '\r'); ODOO_GID=$($DC exec -T odoo id -g | tr -d '\r')
   sudo install -d -m 0700 -o "$ODOO_UID" -g "$ODOO_GID" /var/lib/efn/migration/exchange
   sudo install -d -m 0500 -o "$ODOO_UID" -g "$ODOO_GID" /var/lib/efn/migration/maps
   # .env: MIG_EXCHANGE_DIR=..., MIG_MAPS_DIR=..., EFN_MIG_UID=$ODOO_UID, EFN_MIG_GID=$ODOO_GID
   ```
   `EFN_MIG_UID/GID` = uid/gid user `odoo` karena bundel ditulis mode 0600 dan importer di container Odoo harus
   membacanya; hasil impor (0600 milik odoo) dibaca balik oleh `writeback`. Bind mount Windows (Docker Desktop)
   mengemulasi izin, jadi langkah `install` cukup `mkdir`.
4. **Tabel pemetaan** disetujui PIC brand + Finance (`branch_map.csv`, `uom_map.csv`; format di
   `scripts/migration/README.md`, contoh sintetis `docs/templates/migrasi/esb/`) disalin ke `MIG_MAPS_DIR`.
5. **Image**: dev `$DCM build esb-migrate`. Staging/prod: pakai image bertanda tangan dari `build-push.yml`
   (`IMAGE_ESB_MIGRATE=ghcr.io/<owner>/esb-migrate@sha256:<digest>` di `.env`, verifikasi `cosign verify` seperti
   [esb-extract.md](esb-extract.md) §8); image belum masuk `deploy.yml`/`efn-deploy` (sekali jalan, bukan service).
6. **Sisi Odoo**: modul `efn_legacy_history` (+ `mrp`) terpasang. Produk hasil migrasi dibuat tanpa `responsible_id`,
   tetapi record lain (gudang, POS) tetap memeriksa konsistensi company terhadap pelaku → user teknis harus anggota
   company target.
   **User teknis `efn_migration`** (`EFN_MIG_USER_LOGIN`, docs/04 §27.9, ADR-042; SEC-205) — dibuat/diaktifkan SEBELUM
   impor pertama jendela ini, oleh Admin Odoo (bukan oleh importer):
   - user internal, **tanpa password** (login interaktif tidak mungkin) dan **tanpa API key**;
   - grup minimum: Inventaris administrator, Manufaktur administrator, POS administrator, Akuntansi penagih — **bukan**
     Settings/Administrator; tambahan hanya bila importer menolak dengan pesan grup (catat di berita acara);
   - `company_ids` = **hanya** company brand yang dimigrasi (mis. PB; Bacha = BCF + BCR), `company_id` = salah satunya;
   - `active=True` hanya selama jendela (ditutup di §7.4).
   ```bash
   $DC exec -T odoo odoo shell -d "$ODOO_DB" --no-http --log-level=warn <<'PY'
   CODES = ["PB"]   # kode PT brand yang dimigrasi jendela ini
   Users = env["res.users"].with_context(active_test=False)
   companies = env["res.company"].search([("efn_brand_code", "in", CODES)])   # sesuaikan bila field kode PT berbeda
   assert len(companies) == len(CODES), "company target tidak lengkap"
   groups = [env.ref(x) for x in ("base.group_user", "stock.group_stock_manager", "mrp.group_mrp_manager",
                                  "point_of_sale.group_pos_manager", "account.group_account_invoice")]
   gfield = "group_ids" if "group_ids" in Users._fields else "groups_id"   # Odoo 19: group_ids
   vals = {"name": "EFN Migrasi ESB (teknis)", "company_id": companies[0].id,
           "company_ids": [(6, 0, companies.ids)], gfield: [(6, 0, [g.id for g in groups])], "active": True}
   user = Users.search([("login", "=", "efn_migration")], limit=1)
   if user:
       user.write(vals)
   else:
       user = Users.create(dict(vals, login="efn_migration"))
   assert env.ref("base.group_system") not in user[gfield], "efn_migration tidak boleh Settings/Administrator"
   assert not env["res.users.apikeys"].sudo().search_count([("user_id", "=", user.id)]), "hapus API key efn_migration"
   # field ORM `password` selalu terbaca kosong -> cek kolom DB langsung
   env.cr.execute("SELECT COALESCE(password, '') = '' FROM res_users WHERE id = %s", [user.id])
   assert env.cr.fetchone()[0], "efn_migration tidak boleh punya password"
   env.cr.commit()
   print("efn_migration aktif; company:", companies.mapped("name"))
   PY
   ```
   (Langkah administratif singkat; impor sendiri **tidak** lewat `exec` ke Odoo live, lihat §4.2.) Bila skema field
   berbeda di versi Odoo terpasang, lakukan hal yang sama lewat UI Settings → Users dan catat di berita acara.
   Pasang overlay: `$DCM up -d odoo` (recreate odoo + sidecar). Cek: `$DCM exec -T odoo ls /run/secrets/migration_bundle_key /mnt/efn-mig /opt/efn-migration/efn_mig`.
7. **Prasyarat produksi** (bukan dev): entri PDP "Bundel migrasi ESB" ditandatangani DPO; Q-ESB7 (izin beban API
   ESB) untuk backfill; base backup WAL-G terbaru sukses + uji restore ([backup-restore.md](backup-restore.md)) sebelum
   impor nyata; jadwal di luar jam ramai POS.

## 3. Menyiapkan data ClickHouse (esb-extract)

Detail kredensial/backfill: [esb-extract.md](esb-extract.md). Untuk dry-run cukup rentang terbatas.

```bash
# Master (cabang, produk, BoM, menu, supplier) ikut otomatis di awal backfill. Rentang terbatas: 2 bulan tertutup.
$DC run --rm esb-extract python -m efn_esb backfill --brand pb --from 2026-09-01 --to 2026-10-31 --ignore-window
# Entitas migrasi P1 (E1 BOM detail, E2 harga menu, E4 customer B2B whitelist, E5 lokasi) - snapshot hari ini
$DC run --rm esb-extract python -m efn_esb snapshot --brand pb \
  --entity bom_detail --entity menu_price --entity customer --entity location
```

- `--ignore-window` hanya dev (production ditolak kecuali `ESB_ALLOW_IGNORE_WINDOW=true` dan rentang ≤ 1 hari).
- P2 (`stock_opname`, `ap_open`, `ar_open`, `coa`) **jangan** di dry-run master: belum diverifikasi, butuh
  `--allow-unverified`, dan langkah `stock`/`opening` export belum dibangun (exit 2).
- Kesegaran & kualitas: `chq <<< "SELECT * FROM efn_dwh.v_esb_data_quality WHERE brand = 'pb' FORMAT Vertical"`.

## 4. Dry-run PB end-to-end di dev

Tujuan: membuktikan rantai lengkap pada DB Odoo dev, mengukur ukuran tabel histori, dan menghasilkan draf berita
acara. Isi tanggal cutover rencana (mis. `2026-12-01`) atau tanggal uji.

### 4.1 Master
```bash
$MIG python esb_to_odoo.py export --brand pb --step master --maps /maps --out /out --cutover-date 2026-12-01
#   -> "export master: <RUN>.efnmig"; cabang aktif tanpa baris branch_map -> manifest.unmapped_branches -> exit 1
RUN=<run_id dari keluaran>
$MIG python validate_import.py --source esb-bundle /out/$RUN.efnmig --out /out
#   -> validation_$RUN.json/.md; errors harus 0 (importer menolak bila tidak)
```

### 4.2 Impor master ke Odoo dev (dry-run lalu nyata)
Satu-satunya jalur impor = pembungkus `scripts/migration/run-import.sh` (opsi persis: `run-import.sh --help` / header
skrip; bila berbeda dari contoh ini, skrip yang berlaku). Perilaku yang ditetapkan lead (perbaikan security G43):
- importer berjalan di **container Odoo sekali jalan** (image + env + overlay migration yang sama dengan service
  `odoo`, dihapus setelah selesai), **bukan** `docker compose exec` ke container Odoo yang melayani POS — bundel
  besar tidak bisa meng-OOM worker produksi (SEC-200). **Jangan** memanggil `exec ... odoo shell` manual untuk impor;
- sebelum menyentuh DB, laporan validator diverifikasi **tanda tangannya** (HMAC kunci bundel) dan `manifest_sha256`-nya
  harus sama dengan bundel; laporan buatan tangan/diubah ditolak (SEC-201);
- default **dry-run**; run nyata `--apply`; di **staging dan production** run nyata wajib `--confirm-brand <brand>`
  (`APP_ENV` kosong/tidak dikenal diperlakukan production).

```bash
RI=../../scripts/migration/run-import.sh
export DCM ODOO_DB   # dipakai run-import.sh
$RI "$RUN.efnmig" --validation "validation_$RUN.json"                   # dry-run -> <run_id>.dryrun.result.efnmig
# periksa import_report (created/updated/conflict/failed); bila bersih:
$RI "$RUN.efnmig" --validation "validation_$RUN.json" --apply             # dev
# staging/production: ... --apply --confirm-brand pb
$MIG python esb_to_odoo.py writeback --result /out/$RUN.result.efnmig      # dry_run=true ditolak (exit 4)
$MIG python esb_to_odoo.py reconcile --bundle /out/$RUN.efnmig --result /out/$RUN.result.efnmig --out /out
```

Ulang impor nyata dengan bundel yang sama harus menghasilkan `created = 0` (idempoten lewat xmlid `efn_mig_esb.*`).
`reconcile` dry-run: beri `--result /out/$RUN.dryrun.result.efnmig` (run_id sama dengan bundel).
Kategori produk induk brand (`PB`) dibuat sebagai kategori tingkat atas (Odoo 19 tanpa kategori "All").

### 4.3 Histori (rentang terbatas)
```bash
$MIG python esb_to_odoo.py export --brand pb --step history --cutover-date 2026-12-01 --maps /maps --out /out \
  --months 2026-09,2026-10
#   -> satu bundel per company x bulan: <PARENT>_PB_202609.efnmig, ... ; "parent_run_id=<PARENT> segmen=N"
PARENT=<parent_run_id dari keluaran>
for f in "$MIG_EXCHANGE_DIR/${PARENT}"_PB_*.efnmig; do   # path host (§1)
  b=$(basename "$f" .efnmig)
  $MIG python validate_import.py --source esb-bundle "/out/$b.efnmig" --out /out
done
```
Impor setiap segmen seperti §4.2 (`run_id` segmen = isi header/manifest; nama `validation_<run_id>.json` dari keluaran
validator). **Samakan** `--sales-line-months` ekspor dengan parameter Odoo `efn_mig_esb.sales_line_months`: bila
parameter lebih kecil, baris di luar jendela dilewati importer (`rows_skipped_window`) dan rekonsiliasi 5c hanya
mengeluarkan peringatan; bila parameter 0, importer melaporkan `skipped_by_param` dan rekonsiliasi memuat baris info
`5c_dilewati_param` (bukan selisih). Ukur WAL & waktu per segmen (§5). Lalu satu laporan rekonsiliasi per `parent_run_id`:
```bash
$MIG python esb_to_odoo.py reconcile --bundle /out/<seg1>.efnmig --bundle /out/<seg2>.efnmig \
  --result /out/<seg1>.result.efnmig --result /out/<seg2>.result.efnmig --out /out --with-source
```
Exit 1 = ada butir pemblokir (selisih Σ per bulan × gudang × produk/menu di luar toleransi, `bulk_check` ≠ 0,
gagal impor) → investigasi sebelum lanjut; peringatan rasio net/pembayaran di luar 0,80–0,98 = catat di berita acara.

**Laporan akhir**: setelah SEMUA segmen histori (dan master) satu ekspor selesai diimpor nyata, jalankan sekali
`reconcile --final` dengan seluruh bundel + hasil `parent_run_id` itu (opsi persis: `esb_to_odoo.py reconcile --help`).
Laporan per segmen di atas hanya kontrol antara; yang dilampirkan ke berita acara (§6) adalah laporan `--final`, dan
exit ≠ 0 dari `--final` = berita acara tidak boleh ditandatangani.
```bash
$MIG python esb_to_odoo.py reconcile --final --bundle /out/<seg1>.efnmig ... --result /out/<seg1>.result.efnmig ... \
  --out /out --with-source
```

### 4.4 Validasi fungsional di Odoo (checklist)
- [ ] Gudang/lokasi/`pos.config` PB sesuai `branch_map`; tidak ada sesi POS terbuka dibuat.
- [ ] Sampel 20 produk + 20 menu: nama, kategori, UoM, harga jual, aktif/arsip benar; takeaway `" T"` = xmlid kedua pada
      menu dasar (bukan produk baru).
- [ ] BoM phantom (menu) dan normal (CK) dengan komponen & qty sesuai ESB untuk 5 sampel.
- [ ] Menu "Histori ESB (arsip)": list/pivot/graph `efn.legacy.sales.daily` per outlet × bulan cocok dengan dashboard
      ESB/Metabase untuk 3 outlet; label "arsip sistem lama — bukan transaksi Odoo" tampil.
- [ ] User PT lain (mis. BCF) **tidak** melihat baris histori PB (record rule multi-company).
- [ ] Tidak ada `pos.order`/`account.move`/`stock.move` baru dari impor (`SELECT count(*) ... WHERE create_date > <mulai>`).
- [ ] Forecast `efn_demand_forecast` membaca `efn.legacy.flow.daily` untuk tanggal < cutover (`efn_mig_esb.cutover_date.PB`
      hanya ditulis run nyata `cutover-param`; di dry-run cukup cek metode `_efn_legacy_outflow()` mengembalikan data).

## 5. Ukuran tabel histori & dampak backup (wajib dilaporkan di dry-run PB)

```bash
psqlo -c "SELECT c.relname, pg_size_pretty(pg_total_relation_size(c.oid)) AS total,
                 pg_size_pretty(pg_relation_size(c.oid)) AS heap,
                 pg_size_pretty(pg_indexes_size(c.oid)) AS idx, c.reltuples::bigint AS rows_est
          FROM pg_class c WHERE c.relname IN ('efn_legacy_flow_daily','efn_legacy_sales_daily','efn_legacy_sales_line')
          ORDER BY pg_total_relation_size(c.oid) DESC;"
psqlo -c "SELECT pg_size_pretty(pg_database_size(current_database()));"
# WAL per segmen impor: catat sebelum & sesudah
psqlo -Atc "SELECT pg_current_wal_lsn();"          # LSN_A ... impor ... LSN_B
psqlo -Atc "SELECT pg_size_pretty(pg_wal_lsn_diff('<LSN_B>','<LSN_A>'));"
```

Ekstrapolasi ke rentang penuh: `ukuran_24bln ≈ ukuran_terukur × 24 / bulan_dimuat` untuk `sales_daily` & `sales_line`
(`sales_line` mengikuti `efn_mig_esb.sales_line_months`), `flow_daily ≈ × legacy_days / hari_dimuat`. Laporkan:
ukuran tabel, pertambahan base backup WAL-G (≈ ukuran heap + indeks, terkompresi), volume WAL saat bulk load (≈ 1–2×
data yang ditulis; arsip WAL ke object storage dan alert disk > 85%), dan waktu impor per segmen. Bila `sales_line`
24 bulan membuat base backup/RTO 4 jam (R-T08) tidak tercapai, usulkan `sales_line_months` lebih kecil ke efn-lead
(parameter, tanpa ubah kode).

Produksi: jalankan bulk load histori di luar jam ramai, pantau `EfnBackup*`/disk selama dan setelah impor, dan picu base
backup WAL-G setelah semua segmen histori selesai (agar restore tidak memutar ulang WAL bulk load).

## 6. Berita acara (per brand, per run nyata)

Disimpan di arsip dokumen keuangan (bukan repo). Lampiran = laporan tanpa data pribadi: `validation_*.md`,
`reconcile_*.md/.csv`, `import_report` ringkas, keluaran §5.

| Butir | Isi |
|---|---|
| Brand / company | mis. `pb` / `PB` |
| Environment & tanggal | dev dry-run / staging / production; tanggal-jam mulai & selesai (WIB) |
| Versi | `git rev-parse HEAD` (= `tool.git_sha` manifest), digest image `esb-migrate`, versi modul `efn_legacy_history` |
| Cutover | `--cutover-date`, rentang histori (`legacy_days`, bulan `sales_daily`, `sales_line_months`) |
| Run | `run_id` master, `parent_run_id` histori, jumlah segmen; `manifest_sha256` tiap bundel |
| Hasil impor | per entitas: created/updated/skipped/conflict/failed; baris histori per model |
| Rekonsiliasi | laporan `reconcile --final` (§4.3): status butir 1–7 §27.5, selisih + penjelasan, peringatan rasio net/pembayaran |
| User teknis | `efn_migration`: tanggal aktif/nonaktif, grup, company (§2.6, §7.4) |
| Ukuran & backup | §5 (ukuran tabel, WAL, waktu, dampak base backup) |
| Pengecualian | baris tak terpetakan, menu arsip, asumsi [B] (Q-ESB17 BoM, `lastHpp`), langkah yang tidak dijalankan (stok/saldo) |
| Rencana hapus bundel | tanggal ≤ 90 hari setelah tanda tangan + metode (§7): bundel, hasil, laporan validator, dump `odoo_before_*` |
| Crypto-erase (brand terakhir) | tanggal `migration_bundle_key` → `disabled`, hash kunci yang dimusnahkan, pemusnahan salinan tersegel `[SA]` (saksi, log brankas) — [key-custody.md §4f](key-custody.md#migrasi-esb) |
| Tanda tangan | PIC brand, Finance, operator migrasi, efn-lead; DPO (produksi) |

## 7. Penghapusan bundel (≤ 90 hari) dan menutup jendela

1. Setelah berita acara ditandatangani, jadwalkan hapus (kalender tim, tanggal di berita acara).
2. Pindahkan laporan yang dilampirkan ke berita acara (`validation_*.md`, `reconcile_*.md/.csv` — tanpa data pribadi)
   ke arsip berita acara, lalu hapus **semua** isi `MIG_EXCHANGE_DIR` yang tersisa: bundel (`*.efnmig`), bundel hasil
   (`*.result.efnmig`, `*.dryrun.result.efnmig`), laporan validator (`validation_*.json/.md`), laporan antara, berkas
   sementara (`*.part`), dan dump DB `odoo_before_*` (`*.dump`, `*.dump.age`):
   ```bash
   sudo find "$MIG_EXCHANGE_DIR" -type f \( -name '*.efnmig' -o -name '*.part' -o -name '*.json' -o -name '*.md' \
     -o -name '*.csv' -o -name '*.dump' -o -name '*.dump.age' \) -print -delete
   sudo find "$MIG_EXCHANGE_DIR" -mindepth 1 -print     # harus kosong; sisa apa pun = periksa & hapus manual
   ```
   `shred` tidak andal di SSD/LUKS; perlindungan utamanya = enkripsi bundel/dump + pemusnahan kunci (langkah 4).
3. Salinan lain (laptop operator, media transfer, kunci privat `age` dump bila khusus migrasi) dihapus dan dikonfirmasi
   tertulis.
4. **Menutup jendela** (setelah brand terakhir, atau bila jeda migrasi > 30 hari):
   ```bash
   printf 'disabled' | sudo tee /opt/efn/secrets/clickhouse_password_mig.txt > /dev/null
   printf 'disabled' | sudo tee /opt/efn/secrets/migration_bundle_key.txt > /dev/null   # = rotasi/crypto-erase
   $DC up -d --force-recreate clickhouse-init     # log: "user efn_mig dihapus (migrasi nonaktif)"
   $DC up -d odoo                                  # TANPA -f docker-compose.migration.yml -> kunci & mount lepas
   ```
   Cek: `$DC exec -T odoo ls /run/secrets | grep -c migration_bundle_key` = 0 dan `SHOW USERS` ClickHouse tanpa
   `efn_mig`.
   - **Arsipkan user `efn_migration`** (SEC-205) — tidak dihapus (jejak `create_uid/write_uid` histori tetap terbaca):
     ```bash
     $DC exec -T odoo odoo shell -d "$ODOO_DB" --no-http --log-level=warn <<'PY'
     u = env["res.users"].with_context(active_test=False).search([("login", "=", "efn_migration")], limit=1)
     env["res.users.apikeys"].sudo().search([("user_id", "=", u.id)]).unlink()
     u.active = False
     env.cr.commit()
     print("efn_migration active =", u.active)
     PY
     ```
     Di antara brand (jendela tetap terbuka) user boleh tetap aktif; bila jeda > 30 hari arsipkan dan aktifkan lagi
     lewat §2.6 saat brand berikutnya.
   - **Crypto-erase**: musnahkan salinan tersegel `[SA]` `migration_bundle_key` (bila dibuat) dan salinan lain sesuai
     [key-custody.md §4f](key-custody.md#migrasi-esb) langkah 2; catat tanggal, hash kunci, metode, dan saksi di berita
     acara (§6), log kustodi, dan inventaris PDP (Q-ESB24 / Q-P32).
   - Dev: sama, atau `rm secrets/migration_bundle_key.txt` lalu `APP_ENV=dev bash scripts/gen-secrets.sh` (kunci baru =
     bundel dev lama tak terbaca).

## 8. Rollback

| Situasi | Tindakan |
|---|---|
| Dry-run gagal | Tidak ada perubahan (transaksi di-rollback importer). Perbaiki peta/data, ekspor ulang (run_id baru). |
| Validator/integritas gagal (exit 1/3) | Jangan impor. Exit 3 = bundel rusak/kunci salah/berkas diubah → ekspor ulang; jangan "memperbaiki" bundel manual. |
| Impor master nyata salah sebagian | Ulang impor setelah perbaikan (upsert per xmlid; hanya field milik migrasi). Record salah yang tidak boleh ada → arsipkan (`active=False`) lewat Odoo oleh fungsional, bukan hapus massal SQL. |
| Histori satu irisan salah | Impor ulang segmen yang sama/terbaru: importer mengganti irisan company × gudang × bulan (`DELETE`+`INSERT`, §27.8a), tidak menggandakan. |
| Kerusakan luas / sebelum go-live | **Pulihkan DB Odoo** ke titik sebelum impor: dev = dump terenkripsi yang diambil sebelum §4.2 (`age -d` → `pg_restore` ke DB baru lalu ganti `ODOO_DB`); staging/prod = PITR WAL-G ke waktu sebelum impor ([backup-restore.md](backup-restore.md)). Setelah go-live POS Odoo, PITR membuang transaksi baru → hanya dengan keputusan efn-lead + Finance. |
| `map_esb_odoo` berisi run yang dibatalkan | Admin ClickHouse (bukan `efn_mig`, yang tanpa DELETE): `ALTER TABLE efn_dwh.map_esb_odoo DELETE WHERE run_id = '<run_id>'` lalu catat di berita acara. |

Sebelum impor nyata di dev, ambil cadangan cepat **terenkripsi** (`age`, kunci publik penerima milik EFN; dump DB
penuh tidak pernah disimpan plaintext). Staging/production memakai WAL-G/PITR, bukan dump ini.
```bash
AGE_RECIPIENT='age1...'   # kunci publik EFN (bukan rahasia); kunci privat tidak berada di host ini
$DC exec -T postgres pg_dump -U "${PG_SUPERUSER:-postgres}" -Fc "$ODOO_DB" \
  | age -r "$AGE_RECIPIENT" -o "$MIG_EXCHANGE_DIR/odoo_before_$(date +%Y%m%d%H%M).dump.age"
# pulih: age -d -i <kunci-privat> odoo_before_<ts>.dump.age | pg_restore -d <db_baru> ...
```
Berkas ini berada di `MIG_EXCHANGE_DIR` agar ikut terhapus di §7 (≤ 90 hari setelah berita acara).

## 9. Masalah umum

| Gejala | Penyebab / tindakan |
|---|---|
| `run` gagal "bind source path does not exist" | Direktori `MIG_EXCHANGE_DIR`/`MIG_MAPS_DIR` belum dibuat (§2.3). |
| `konfigurasi: MIG_BUNDLE_KEY_FILE ...` / `migration_bundle_key wajib hex >= 64` | Secret masih `disabled` atau bukan hex → §2.1 (dev: `gen-secrets.sh` dijalankan tanpa `APP_ENV=dev` menulis `disabled`). |
| `konfigurasi: CH_CA_FILE ...` / TLS error | CA internal tidak ter-mount atau sertifikat ClickHouse kedaluwarsa (`make pki`). Tidak ada fallback plaintext. |
| ClickHouse `AUTHENTICATION_FAILED` untuk `efn_mig` | clickhouse-init belum dijalankan ulang setelah secret diisi, atau secret placeholder → user dihapus (§2.2). |
| Importer `Permission denied` membaca bundel | `EFN_MIG_UID/GID` ≠ uid/gid odoo (§2.3); ekspor ulang setelah diperbaiki. |
| OOM-kill esb-migrate (exit 137) | Segmen terlalu besar: turunkan `EFN_MIG_SEGMENT_MAX_ROWS` (dipecah per gudang). |
| `gerbang ditolak` (exit 4) | PT di `EFN_MIG_SALDO_BLOCKED` untuk langkah saldo, atau `writeback` atas hasil dry-run. |
