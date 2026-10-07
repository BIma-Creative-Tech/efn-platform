# Runbook: anomali Poin EFN (`EfnLoyalty*`)

Gelombang 13c, SEC-61 (review keamanan r6), risiko R-B05/R-B06 (fraud poin), ADR-020 (ledger). Audiens: on-call IT,
Finance (pemilik liabilitas poin), tim keamanan.

Sumber: BFF (`services/api-gateway`) mencetak satu baris per kejadian, tanpa PII (tanpa nomor HP/member_hash/IP):

- `EFN_LOYALTY_ANOMALY {"kind":..,"severity":..,...}`: pagar earn (`409 earn_limit_exceeded`), job rekonsiliasi ledger
  (`LoyaltyReconService`, tiap jam, jendela 26 jam), migrasi K-7. Field umum: `company`, `order_ref` (uuid
  `pos.order`, opak), `points`, `limit`, `pos_config_id`, `source_ref`.
- `EFN_LOYALTY_RECON {"status":"ok","anomalies":N,"lookback_hours":26}` = heartbeat di akhir setiap siklus.
- `EFN_LOYALTY_MIGRATION {"company":..,"points":..,"source_ref":..}`: saldo pembuka migrasi yang dibukukan.

Jalur: stdout BFF (`NO_COLOR=1`) → Promtail (`promtail.yml` tahap 4, regex berjangkar, hanya `service=api-gateway`)
→ Loki stream `{stream="efn_loyalty"}` (retensi **400 hari**) → ruler (`loki-rules/efn-loyalty.yml`) → Alertmanager
(mTLS). Metrik pasangan: `efn_loyalty_anomaly_total{kind,severity}`,
`efn_loyalty_recon_last_success_timestamp_seconds`, `efn_loyalty_recon_runs_total{outcome}`.

Query dasar (Grafana → Explore → Loki):

```logql
{stream="efn_loyalty", loyalty_event="anomaly"} | regexp `EFN_LOYALTY_ANOMALY (?P<payload>[{].*[}])` | line_format "{{.payload}}" | json
sum by (kind) (count_over_time({stream="efn_loyalty", loyalty_event="anomaly"} [24h]))
```

| kind | severity | Arti |
|---|---|---|
| `earn_limit_order` / `earn_limit_member_day` / `earn_limit_pos_config_hour` | warning | earn ditolak plafon per order (5000) / harian per member (2000) / per kasir per jam (10000) |
| `earn_limit_company_hour` | critical | earn satu PT > 50000 poin/jam: kemungkinan service key Odoo disalahgunakan atau skrip massal |
| `earn_above_order_cap`, `member_day_above_limit` | warning | data dalam jendela melebihi plafon saat ini (plafon baru diturunkan / balapan) |
| `earn_ledger_mismatch`, `earn_ledger_orphan`, `ledger_unknown_source` | critical | ledger ditulis di luar BFF (SQL manual, admin) atau bug |
| `earn_above_rule` | critical | poin > floor(eligible / tarif): aturan dilanggar (atau tarif dinaikkan setelah earn = positif palsu) |
| `reverse_exceeds_earn`, `restore_exceeds_redeem` | critical | refund mengembalikan/menarik poin lebih dari transaksi asal |
| `migration_large_balance` | warning | saldo pembuka > `LOYALTY_MIGRATION_ANOMALY_POINTS` (tetap diterima) |
| `migration_erased_member` | warning | impor ditolak `409 member_deleting`/`member_erased` (SEC-65) |

Nilai plafon = placeholder (menunggu Finance, Q-13). Env: `LOYALTY_POS_MAX_EARN_POINTS`,
`LOYALTY_EARN_DAILY_MAX_POINTS`, `LOYALTY_EARN_COMPANY_HOURLY_MAX_POINTS`, `LOYALTY_EARN_POS_CONFIG_HOURLY_MAX_POINTS`.

## EfnLoyaltyAnomalyCritical
Severity **critical** (page). Satu baris critical dalam 15 menit. Temuan rekonsiliasi berulang setiap siklus selama
masih di jendela 26 jam, jadi alert tetap menyala sampai data ditangani atau keluar dari jendela.
1. Ambil `kind`, `company`, `order_ref`, angka dari query dasar. Jangan mengoreksi ledger sebelum bukti disimpan
   (ekspor query Loki + `pg_dump -t platform.loyalty_ledger -t platform.loyalty_pos_earn` ke lokasi bukti terenkripsi).
2. `earn_limit_company_hour`: cek `efn_loyalty_anomaly_total` dan log akses BFF per `pos_config_id`; bila pola
   tidak wajar (banyak order bernilai besar dari satu kasir/sesi), nonaktifkan earn di POS terkait
   (`pos.config.efn_loyalty_earn_enabled`) dan pertimbangkan rotasi `odoo_bff_api_key`
   ([secret-rotation.md](secret-rotation.md)).
3. `earn_ledger_*`/`ledger_unknown_source`: cari penulis langsung ke `platform.loyalty_ledger` (log PostgreSQL,
   role selain `efn_bff`); `reverse_exceeds_*`: buka order asal di Odoo (Point of Sale → Review Poin) dan
   riwayat refund-nya.
4. Indikasi fraud nyata → [security-incident.md](security-incident.md); koreksi saldo hanya lewat entri
   penyesuaian ber-CR Finance (bukan UPDATE/DELETE ledger).
5. Positif palsu `earn_above_rule` setelah perubahan tarif: catat di CR perubahan tarif; alert padam setelah 26 jam.

## EfnLoyaltyAnomalyWarning
Severity **warning** (ringkasan jam kerja; satu notifikasi berisi semua `kind` yang aktif). Pagar batas bekerja:
transaksi ditolak `409 earn_limit_exceeded`, order di Odoo berstatus *review* (tanpa poin) dan muncul di menu
Review Poin.
1. Supervisor/Finance meninjau order *review*: transaksi sah (mis. pembelian grosir) → kompensasi poin mengikuti
   kebijakan Finance (belum ada tombol "kirim ulang earn" untuk order yang ditolak plafon; backlog); tidak sah →
   biarkan tanpa poin dan telusuri kasir.
2. `earn_limit_member_day` berulang untuk satu member → indikasi pemakaian nomor member oleh orang lain di kasir.
3. `migration_*` → cocokkan dengan berkas migrasi Finance (`source_ref`); `{stream="efn_loyalty",
   loyalty_event="migration"}` berisi semua saldo pembuka yang dibukukan.

## EfnLoyaltyReconMissing
Severity **warning**. Tidak ada `EFN_LOYALTY_RECON {"status":"ok"...}` di Loki selama 2 jam (+15 menit). Pasangan
gauge Prometheus `EfnLoyaltyReconStale` ditekan (inhibit) selama alert ini menyala. `EfnLoyaltyReconFailing` =
siklus gagal ≥ 2 kali dalam 3 jam.
1. BFF hidup? (`EfnServiceDown{job="api-gateway"}`). `$DC logs --tail=200 api-gateway | grep LoyaltyReconService`.
2. Ada baris `EFN_LOYALTY_RECON` di log container tetapi tidak di Loki → format log berubah: pastikan `NO_COLOR=1`
   (tanpa kode warna ANSI), format waktu `MM/DD/YYYY, h:mm:ss AM` (locale default container), konteks logger
   `LoyaltyReconService`; sesuaikan regex `promtail.yml` tahap 4 bila versi NestJS mengubah format. Selama itu alert
   anomali juga buta.
3. `status":"error"` → pesan error setelah JSON (koneksi DB, timeout). Job READ ONLY; tidak memengaruhi transaksi.
4. `LOYALTY_RECON_INTERVAL_MS=0` mematikan job (jangan di produksi).

## Batas
- Rekonsiliasi hanya melihat data BFF; order yang tidak pernah ada di Odoo tidak terdeteksi (rekonsiliasi
  ledger ↔ `pos.order` lewat ETL = backlog data-AI, SEC-61 rekomendasi 3).
- Seperti sink audit, baris log palsu utuh hanya mungkin bila input pemanggil berisi newline yang dicetak mentah
  oleh logger lain di BFF; regex berjangkar + label container membatasi ini ke proses BFF sendiri.
