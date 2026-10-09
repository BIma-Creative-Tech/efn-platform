# infra/metabase — dashboard sebagai kode (Fase 1)

**Tujuan:** 3 dashboard Fase 1 (docs/01 §7 "ETL dasar + 3 dashboard Metabase") yang bisa di-review, di-diff, dan
di-provision ulang secara idempoten: *Penjualan Harian Outlet*, *Stok & Kedaluwarsa*, *Price Hunter*.
**ADR:** ADR-003 (ETL 15 menit), ADR-022 (TLS di setiap koneksi), ADR-031 (tanpa modul pihak ketiga Odoo; BI di luar
Odoo). **Risiko:** R-S05 (kartu hanya view semantic layer, user `efn_metabase` read-only), R-L01 (tanpa identitas
pelanggan di mart/kartu). Daftar dashboard final menunggu **Q-47** (10 dashboard C-level) — ketiganya usulan Fase 1.

## Isi

| File | Fungsi |
|---|---|
| `Dockerfile` | `metabase/metabase:v0.58.35@sha256` (LTS). G16: driver ClickHouse sudah bawaan Metabase sejak v0.54 -> jar eksternal 1.52.0 dihapus. **Belum diverifikasi** dengan `provision.py` (kunci `details` koneksi ClickHouse driver bawaan) dan migrasi DB aplikasi 0.52 -> 0.58 (otomatis, tidak bisa dibalik: backup DB `metabase` dulu). |
| `provision.py` | Skrip idempoten (stdlib saja): setup awal (admin dari file secret), setting (tanpa telemetri, zona Asia/Jakarta, locale id), koneksi `EFN DWH (ClickHouse)`, collection `EFN Fase 1`, kartu & dashboard, lalu menjalankan setiap kartu sekali (`--no-verify-cards` untuk melewati). |
| `dashboards/*.json` | Definisi dashboard: parameter (tanggal, PT, outlet/gudang, pesaing, produk) + kartu SQL native. |
| `tests/test_provision.py` | pytest: validasi definisi repo, SQL hanya `efn_dwh.v_*`, mapping filter, TLS wajib. |

Kartu dan dashboard dikenali lewat tag `[efn:<dashboard>/<kartu>]` di deskripsi: run ulang memperbarui (bukan
menggandakan); kartu yang dihapus dari JSON diarsipkan. Perubahan manual di UI pada dashboard ini **ditimpa** run
berikutnya — buat dashboard eksplorasi di collection lain.

## Keamanan
- Kartu: hanya `SELECT/WITH` satu statement ke view `efn_dwh.v_*`; DDL/DML/fungsi tabel (`url`, `file`, `remote`, `s3`, …)
  ditolak saat validasi. Lapisan kedua: role ClickHouse `metabase_reader` (`readonly=2`, grant kolom, tanpa
  `customer_id`/hash) — diverifikasi: `SELECT customer_id` dan `INSERT` ditolak 497.
- Koneksi Metabase → ClickHouse: `https://clickhouse:8443`, `ssl=true`, `sslmode=strict`, `sslrootcert=/run/secrets/efn_ca_crt`
  (diverifikasi: CA salah → "Failed to create connection").
- Skrip → Metabase: HTTPS terverifikasi (CA sistem / `--ca-file`), lewat Traefik `bi.<domain>` (wg-https + allowlist
  staf); mTLS opsional ke sidecar. Password admin dan `clickhouse_password_metabase` hanya dibaca dari file.
- Instance yang belum di-setup bisa "diklaim" siapa pun yang membuka `bi.` lebih dulu → jalankan `provision.py` segera
  setelah Metabase pertama kali hidup (deploy).

## Menjalankan (dev)
```bash
python infra/metabase/provision.py --url https://bi.efn.local:9443 --connect-host 127.0.0.1 \
  --ca-file secrets/dev_edge_crt.txt --admin-email admin@efn.local \
  --admin-password-file secrets/metabase_admin_password.txt \
  --ch-password-file secrets/clickhouse_password_metabase.txt
```

## Belum
- Compose masih memakai image Metabase polos: ganti ke `build: ../metabase` (devsecops). Di dev, jar driver disalin
  manual ke container (`docker cp`) — hilang saat container dibuat ulang.
- Secret `metabase_admin_password` belum ada di `scripts/secrets.list`; langkah provision belum ada di `deploy.sh`/CI.
- Filter PT/outlet/gudang = teks (cocok sebagian, tidak peka huruf besar), bukan dropdown (butuh field filter
  dengan id field hasil sync).
- SSO/kebijakan password staf (Q-54), izin per grup (collection `EFN Fase 1` masih terlihat semua user Metabase).
