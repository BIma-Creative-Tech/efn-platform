# Runbook: alert sink audit Odoo (`EfnAudit*`)

Gelombang 11, SEC-43 (review keamanan r4), risiko R-S10/R-B06. Sumber: Odoo `efn_audit` menulis baris
`EFN_AUDIT_SINK {json}` (logger `odoo.addons.efn_audit.sink`, **setelah commit**, tanpa nilai lama/baru dan tanpa
PII) untuk perubahan model kontrol audit (aturan audit, `ir.config_parameter` `efn.audit.*`, server action, cron,
modul, otomasi) dan setiap operasi purge log audit.

Jalur: stdout Odoo → Promtail (`infra/observability/promtail.yml`, hanya container `service=odoo`, label
`stream="efn_audit_sink"`, `audit_op`) → Loki (mTLS) → ruler Loki (`infra/observability/loki-rules/efn-audit-sink.yml`)
→ Alertmanager (mTLS, SAN `loki` di `web-config-alertmanager.yml`). Retensi stream ini **400 hari**
(`retention_stream` di `loki.yml`); stream lain 30 hari. Salinan ini berada di luar kendali admin Odoo: menghapus
log di Odoo tidak menghapus jejaknya di Loki.

Query dasar (Grafana → Explore → Loki):

```logql
{stream="efn_audit_sink"} | regexp `EFN_AUDIT_SINK (?P<payload>[{].*[}])` | line_format "{{.payload}}" | json
```

Field: `log_id`, `operation` (create/write/unlink/purge), `model`, `res_id`, `uid`, `sudo`, `fields` (nama field
yang berubah), `note` (purge: `retensi: N log` atau `erasure: ...`).

## EfnAuditControlChange
Severity **warning**. Model kontrol audit berubah dalam 5 menit terakhir.
1. Lihat `model`, `fields`, `uid`, `sudo` di query dasar. Buka log lengkap di Odoo: Pengaturan → Audit → Log
   (`log_id`).
2. Cocokkan dengan change request / jadwal deploy (upgrade modul lewat pipeline juga memicu `ir.module.module`).
3. Tanpa CR, atau `efn.audit.retention_days` / `retention_days` aturan diturunkan, atau server action berisi kode
   baru: perlakukan sebagai insiden ([breach-response-72h.md](breach-response-72h.md)), kunci akun `uid`, simpan
   bukti dari Loki (ekspor query), kembalikan konfigurasi.

## EfnAuditUnexpectedPurge
Severity **critical**. Ada log `purge` dengan `note` yang bukan `retensi:`/`erasure:`. Jalur sah hanya GC retensi
terjadwal dan erasure UU PDP; purge lain berarti kode kustom/server action memanggil method purge.
1. Ambil `uid`, `model`, `note`, waktu dari Loki. Cari server action / cron yang berubah di jendela yang sama
   (alert `EfnAuditControlChange`).
2. Insiden keamanan: nonaktifkan server action/cron terkait, cabut hak admin `uid`, pulihkan log dari backup
   PostgreSQL (WAL-G PITR ke container sementara, `scripts/restore-test.sh`) bila perlu sebagai bukti.

## EfnAuditRetentionPurgeBurst
Severity **warning**. > 5 purge retensi dalam 1 jam (normal: sekali per jalan autovacuum harian).
Periksa perubahan `efn.audit.retention_days` / aturan audit (alert `EfnAuditControlChange` sebelumnya) dan jumlah
log yang dihapus (`note`).

## Diagnosis jalur
- Tidak ada data di `{stream="efn_audit_sink"}`: cek `docker logs efn-odoo-1 | grep EFN_AUDIT_SINK` (modul
  `efn_audit` versi gelombang 11 terpasang?), lalu `docker logs efn-promtail-1`.
- Ruler tidak mengirim: `docker logs efn-loki-1 | grep -i ruler` (error TLS = sertifikat loki belum ber-EKU
  clientAuth → `bash scripts/gen-secrets.sh` di dev / deploy di staging-prod, lalu recreate `loki`).

## Batas (jujur)
- Admin sistem Odoo yang juga punya akses host/Docker dapat mematikan Promtail; itu terlihat sebagai target
  hilang di Prometheus/Uptime Kuma, bukan dicegah.
- Baris sink dapat dipalsukan oleh kode yang berjalan di proses Odoo (menambah alert palsu), tetapi tidak dapat
  menghapus baris yang sudah terkirim ke Loki.
- Loki bukan penyimpanan WORM; salinan append-only jangka panjang (ClickHouse tanpa DELETE / object lock) masih
  backlog SEC-43 butir 4.

## Format sink v2 & pemilihan baris (gelombang 13, SEC-49/SEC-51)
- Promtail memasukkan baris ke `stream="efn_audit_sink"` HANYA bila seluruh baris cocok regex berjangkar: format log
  Odoo (`<tgl> <jam>,<ms> <pid> INFO <db> odoo.addons.efn_audit.sink: EFN_AUDIT_SINK {json}`), JSON v2 diawali
  `"audit_op"`, `"critical"`, memuat `"log_id"` numerik, diakhiri `"v": 2`. Label: `audit_op`, `critical`. Baris dari
  logger lain (mis. login gagal berisi teks `EFN_AUDIT_SINK`), level lain, atau format v1 tidak masuk stream audit.
  Diuji G13 dengan container sintetis berlabel `service=odoo`: 2 baris sah masuk (label benar), 3 baris palsu ditolak.
- Batas: baris palsu UTUH tetap mungkin bila teks pengguna bisa menyisipkan newline di logger Odoo lain. Odoo v2
  meng-escape newline di isi sink, dan `mac` (HMAC, secret `efn_audit_sink_key`) diverifikasi ETL di luar Odoo.
  Selama verifikasi ETL belum aktif, perlakukan alert sebagai indikasi; cocokkan `log_id` dengan `efn_audit_log`.
- G14: baris sink dicocokkan **sebelum** tahap redaksi nomor HP/Bearer dan stream `efn_audit_sink` dikecualikan dari
  redaksi. Sebelumnya redaksi mengubah isi sink yang memuat pola `08…`/`62 8…` (mis. di `note`) sehingga ETL
  menghitung `sink_mac_invalid` palsu. Diuji dengan baris sintetis ber-MAC (kunci dev) berisi `0812…` dan `+628…`:
  setelah pipeline Promtail, `efn_etl.sink.parse_sink_line` → `valid=True`; varian yang diredaksi → `valid=False`.
  Konsekuensi: sink tidak boleh memuat PII (by design `emit_sink`); bila memuat, perbaiki di `efn_audit`.
  `sink_mac_invalid` dari periode sebelum G14 pada baris berpola nomor = kemungkinan artefak redaksi, bukan tamper —
  verifikasi dengan `efn_audit_log` (sumber) sebelum eskalasi.

## EfnAuditCriticalAccessChange
Odoo menandai `critical=true`: grant (langsung/implied) `base.group_system`, `base.group_erp_manager`,
`efn_audit.group_auditor_personal`, `efn_audit.group_manager`; perubahan `ir.rule`/ACL (`access_rule_change`); purge
manual (`purge_manual`). Langkah: (1) cocokkan dengan change request yang disetujui Superadmin (SEC-43 butir 5);
(2) tanpa CR → [security-incident.md](security-incident.md): cabut grant lewat UI/RPC (bukan `odoo shell`, lihat
[odoo-access-changes.md](odoo-access-changes.md)), rotasi kredensial user terdampak (`users_added`), periksa
`efn_audit_log` + salinan ClickHouse untuk `log_id` sekitar kejadian.

## EfnAuditSinkUnsigned
Baris sink `"mac": null` = Odoo tidak membaca `/run/secrets/efn_audit_sink_key` (secret tidak ter-mount / kurang dari
32 byte). Periksa `docker compose config` service `odoo` (secret `efn_audit_sink_key`, env `EFN_AUDIT_SINK_KEY_FILE`)
dan log Odoo `EFN audit sink: kunci HMAC tidak tersedia`. Dev/CI tanpa secret: wajar.
