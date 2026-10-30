# Runbook: reset stack dev (cutover G20 — kode entitas & kanal baru)

Dasar: docs/04 §12.1 (W-32, L-48): kode PT `EFN/GL/PB/BCR/BCF/FARM` menggantikan `EFN/RTL/FNB/FARM`; kode kanal online =
kode PT (`GL`, `PB`, `BCR`, `BCF`). **Tidak ada migrasi data** — belum ada produksi; DB dev/CI dibuat ulang. Event/ledger
BFF lama di dev boleh dibuang. Audiens: developer & QA. **Hanya dev/laptop** — staging/produksi: jangan pernah `down -v`;
pemulihan produksi lewat [backup-restore.md](backup-restore.md).

> **Ringkasan manajemen.** Setelah kode perusahaan diganti (G20), data uji lama di laptop developer tidak cocok lagi
> dengan aturan baru. Cara paling aman: hapus seluruh data uji dev lalu bangun ulang (±30 menit). Tidak ada data
> pelanggan asli di dev, jadi tidak ada yang hilang.

## Kenapa reset (bukan migrasi)
- Odoo: kode `efn_brand_code` company berubah + 2 company baru (BCR, BCF) sebagai **root**; hierarki company tidak bisa
  diubah setelah dibuat (§12.1) → DB Odoo dibuat ulang.
- BFF (`efn_platform`): CHECK `company_code` 7 tabel kini 6 kode (migrasi `infra/postgres/init/09-platform-g20.sql`).
  Baris lama `RTL`/`FNB` membuat constraint tetap `NOT VALID` (log `WARNING 09-platform-g20 ...`).
- ClickHouse: `dim_company`/mart memakai kode lama (data-AI).
- Secret dev kanal lama (`secrets/odoo_api_key_online_{rtl,fnb,rtl_gl,...}.txt`) tidak lagi direferensikan compose.

## Opsi A (disarankan): reset total volume dev

```bash
DC="docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.dev.yml --env-file infra/compose/.env"
# 0. (opsional) cadangan data dev di LUAR repo bila masih dibutuhkan
# 1. Matikan & hapus volume efn_* (pg-data, odoo-data, clickhouse, redis, rabbitmq, minio, loki, ...)
$DC --profile core --profile data --profile ai --profile observability --profile ops down -v
# 2. Secret: kunci baru G20 dibuat, yang lama dibuang (nilai dev saja, tidak pernah dipakai di staging/produksi)
bash scripts/gen-secrets.sh            # membuat odoo_api_key_online_{gl,pb,bcr,bcf}.txt (disabled) + odoo_pii_name_key.txt
rm -f secrets/odoo_api_key_online_{rtl,fnb,rtl_gl,rtl_bacha,fnb_pb,fnb_bacha}.txt
# 3. .env lokal: hapus/ganti ONLINE_CHANNELS lama (RTL, RTL-GL, ...). Kosong = GL. Kode lama -> BFF/deploy menolak.
grep -n '^ONLINE_CHANNELS=' infra/compose/.env || true
# 4. Bangun ulang: docs/runbooks/dev-stack-boot.md §2 (up), §3 (DB Odoo + modul G20), §3d (grant ETL)
# 5. Kanal online dev GL: scripts/smoke/run-e2e-smoke.sh o (langkah setup membuat kanal GL + user efn_online_gl),
#    key nyata -> secrets/odoo_api_key_online_gl.txt (odoo-online-api-key.md §1), lalu:
$DC up -d --no-deps --force-recreate api-gateway
```

Verifikasi:

| Cek | Perintah | Harapan |
|---|---|---|
| Constraint BFF valid | `docker exec efn-postgres-1 psql -U postgres -d efn_platform -tAc "select count(*) from pg_constraint where conname like '%company_code_check' and not convalidated"` | `0` |
| Kode PT baru | `... -tAc "select pg_get_constraintdef(oid) from pg_constraint where conname='online_order_part_company_code_check'"` | memuat `'GL'`, `'BCR'`, `'BCF'` |
| Secret kanal | `docker compose ... config --format json \| jq '.services["api-gateway"].secrets[].source' \| grep online` | hanya `odoo_api_key_online_gl` (+ overlay yang dipilih) |
| Kunci PII Odoo | `docker exec efn-odoo-1 ls /run/secrets \| grep odoo_pii` | `odoo_pii_bidx_key`, `odoo_pii_key_v1`, `odoo_pii_name_key` |
| Lint | `bash scripts/check-encryption.sh` | `OK` |

## Opsi B: hanya DB Odoo (data BFF/ClickHouse dipertahankan)
Ikuti [dev-stack-boot.md §3e](dev-stack-boot.md#reset-db-odoo) **ditambah**: baris BFF ber-kode lama tetap ada sehingga
constraint `*_company_code_check` tetap `NOT VALID` dan rekonsiliasi loyalty/ETL bisa menandai anomali. Gunakan hanya bila
ada alasan kuat menyimpan data BFF dev; bila ragu, pakai Opsi A.

## Opsi C: buang hanya baris berkode lama (data BFF/Odoo/ClickHouse lain dipertahankan)
Dipakai bila DB Odoo sudah dibuat ulang (Opsi B) tetapi BFF/ClickHouse masih menyimpan baris `RTL`/`FNB`. Gejala:
ETL `loyalty_events` gagal tiap siklus dengan `ValueError: company_code tidak dikenal` (fail-closed
`efn_etl/entities.company_code`), heartbeat `status=error`, dan constraint `*_company_code_check` tetap `NOT VALID`.

```bash
# 1. Cadangan BFF
docker exec efn-postgres-1 pg_dump -U postgres -Fc -d efn_platform > <scratch>/efn_platform-pre-purge.dump
# 2. BFF: hapus baris RTL/FNB (5 tabel) + VALIDATE constraint, satu transaksi. Menolak bila ada baris RTL/FNB setelah
#    2026-10-19 (bukan data pra-G20) atau payment_intent lama dirujuk pesanan/refund/porsi.
docker exec -i efn-postgres-1 psql -X -v ON_ERROR_STOP=1 -v confirm=dev-only -U postgres -d efn_platform \
  -f - < scripts/dev/purge-legacy-company-codes.sql
# 3. ClickHouse (admin; etl_writer sengaja tanpa DELETE): loyalty_events, loyalty_points_daily, loyalty_recon_finding,
#    fact_sales_line
clickhouse-client --config-file <admin.xml> --multiquery < scripts/dev/purge-legacy-company-codes-clickhouse.sql
```

Verifikasi: kueri "Constraint BFF valid" di Opsi A = `0`; log ETL `job=loyalty_events status=ok` pada siklus berikut.
Diverifikasi di dev 31 Okt 2026: 20.437 baris ledger + 10.184/10.191 earn POS + 54 reservasi + 28 payment intent +
18 reverse dihapus, 5 constraint divalidasi, ClickHouse 20.440 event lama dihapus, `loyalty_events` kembali `ok`.

## Batas / belum
- Belum diverifikasi di runtime oleh penulis (tanpa Docker di sesi penulisan): perintah mengikuti dev-stack-boot.md
  yang sudah diverifikasi; lead/QA menjalankan dan mencatat hasil.
- Staging/produksi tidak memakai runbook ini; cutover kanal di host: [odoo-online-api-key.md §8](odoo-online-api-key.md#migrasi-g20).
