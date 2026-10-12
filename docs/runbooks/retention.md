# Runbook: Retensi data (W-19) — env, deploy pertama G18, housekeeping Metabase

Dasar: keputusan pemilik proyek 10 Okt 2026 (`docs/decisions/README.md` bagian E), `docs/compliance/PDP_DATA_INVENTORY.md`
v7, paket **W-18/W-19** (`docs/10_RENCANA_GELOMBANG_18_PLUS.md`). Audiens: on-call IT + Superadmin; DPO (Legal) untuk
perubahan nilai.

> **Ringkasan manajemen.** Masa simpan data sekarang mengikuti keputusan 10 Okt: jejak audit CS 10 tahun, daftar
> tolak subjek terhapus 1 tahun, data analitik 10 tahun dengan identitas samaran dihapus setelah 5 tahun, lokasi
> laporan harga dihapus setelah 90 hari, akun Metabase yang tidak dipakai 90 hari dinonaktifkan otomatis. Nilai
> diatur lewat konfigurasi rilis (tanpa ubah kode); mengubahnya = keputusan DPO.

## 1. Env retensi (default = keputusan; kosong = default compose)

Production/staging: GitHub Environment **variables** → manifest rilis bertanda tangan (`deploy.yml` `CFG_*`) → divalidasi
`scripts/lib/deploy-validate.sh` (di luar rentang = deploy ditolak) → `.env` host. ⚠ Variabel lama yang masih terisi di
Environment (mis. `ONLINE_ADMIN_AUDIT_RETENTION_DAYS=400`, `CH_MART_RETENTION_YEARS=5`) **menimpa** default baru —
hapus/ubah sebelum deploy G18.

| Env | Service | Default | Rentang | Keputusan |
|---|---|---|---|---|
| `ONLINE_ADMIN_AUDIT_RETENTION_DAYS` | api-gateway | **3650** | 30..3650 | Q-P19 |
| `ERASURE_SUPPRESSION_DAYS` | api-gateway | **365** | 0..730 (0 = tidak disimpan) | Q-P12 |
| `DSAR_OPEN_ORDER_MAX_DEFER_DAYS` | api-gateway | **14** | 1..14 | Q-P18 (W-18) |
| `MEMBER_MIN_AGE` | api-gateway | **17** | 17..21 | Q-P3 |
| `PRICE_HUNTER_GEO_RETENTION_DAYS` | api-gateway | **90** | 1..3650 | Q-P8 |
| `ONLINE_ORDER_MEMBER_LINK_DAYS` / `ONLINE_EVENT_RETENTION_DAYS` | api-gateway | 730 / 180 | 30..3650 / 7..3650 (= batas BFF) | Q-P17 |
| `CH_MART_RETENTION_YEARS` | clickhouse-init | **10** | 1..30 | Q-6/Q-49 |
| `CH_PSEUDONYM_RETENTION_YEARS` | clickhouse-init + etl | **5** | 1..30 dan ≤ `CH_MART_RETENTION_YEARS` | Q-P7 (⚠ penafsiran, docs/10 §3) |
| `CH_GEO_RETENTION_DAYS` | clickhouse-init + etl | **90** | 1..3650 | Q-P8 |
| `MB_AUDIT_MAX_RETENTION_DAYS` | metabase | **3650** | 30..3650 | Q-P14 (riwayat query) |

Di luar env (bukan devsecops): GC outbox poin Odoo 10 th (efn-odoo-engineer), log audit Odoo 730 h (konfigurasi).
Dev: `infra/compose/.env` lokal lama masih bisa berisi nilai lama (mis. `CH_MART_RETENTION_YEARS=5`) — samakan dengan
`.env.example`.

## 2. Deploy pertama G18 (sekali) {#deploy-pertama-g18}

1. Migrasi Postgres `08-platform-g18-privacy.sql` **wajib** ada di bundel (deploy.sh menolak bundel tanpa file itu) dan
   dijalankan sebelum BFF G18 start (otomatis).
2. ClickHouse: `clickhouse-init` G18 memigrasi `loyalty_events` sekali (ORDER BY berubah; salin + `EXCHANGE`, jendela
   migrasi 1 jam `_loaded_at`). ETL yang berjalan selama itu bisa menyalin ulang baris subjek yang baru dihapus →
   **`deploy.sh` (G18) menghentikan `etl` sebelum `up -d`**; ETL baru start setelah `clickhouse-init` exit 0
   (`depends_on: service_completed_successfully`). Berlaku setiap deploy (jeda ETL beberapa menit, inkremental).
   Jalur manual (mis. menjalankan `clickhouse-init` ulang di luar deploy):
   ```bash
   cd /opt/efn/compose && DC="docker compose $(sed 's/^/-f /' .efn-compose-files | tr '\n' ' ')"
   $DC stop etl && $DC up --no-deps clickhouse-init && $DC logs clickhouse-init | tail -20 && $DC up -d --no-build etl
   ```
3. Setelah deploy: pasang timer housekeeping Metabase (§3) dan timer rotasi API key bila kanal online dipakai
   ([odoo-online-api-key.md](odoo-online-api-key.md#pemasangan)).

## 3. Housekeeping Metabase (akun tidak aktif 90 hari) {#metabase-housekeeping}

Metabase OSS tidak punya fitur nonaktif otomatis → `infra/metabase/provision.py --housekeeping-only` (data-AI G18;
log hanya id user). Dijalankan harian 04:00 WIB oleh timer host:

```bash
install -o root -g root -m 0644 scripts/host/systemd/efn-metabase-housekeeping.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now efn-metabase-housekeeping.timer
systemctl start efn-metabase-housekeeping.service && journalctl -u efn-metabase-housekeeping -n 20
```

`scripts/metabase-housekeeping.sh` memakai file compose deploy terakhir (`.efn-compose-files`) dan argumen service
`metabase-provision` dari `docker compose config` (mTLS + CA internal, tanpa egress), lalu menulis metrik
`efn_metabase_housekeeping_last_run_ok` / `_last_success_timestamp_seconds`.

**`EfnMetabaseHousekeepingStale` (warning):** tidak ada run sukses > 3 hari.
1. `journalctl -u efn-metabase-housekeeping --since -3d` (juga di Loki `service="host-efn-metabase-housekeeping"`).
2. Metabase/`metabase-tls` sehat? (`$DC ps metabase metabase-tls`); profil `data` aktif?
3. Jalankan ulang: `systemctl start efn-metabase-housekeeping.service`.
Dampak bila lama tidak jalan: akun karyawan yang sudah tidak dipakai tetap aktif (minimisasi akses PDP tertunda),
bukan kehilangan data.

## Batas / belum

- `MB_AUDIT_MAX_RETENTION_DAYS`: efek pemangkasan (task terjadwal Metabase) belum terverifikasi (data-AI G18).
- Timer housekeeping dan langkah stop ETL belum diuji di host Linux sungguhan (staging belum ada).
