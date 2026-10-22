# Runbook: poin hangus karena tidak aktif 12 bulan (W-17) — aktivasi & alert

Dasar: docs/04 §14.D (job `LoyaltyInactivityJob`, D.3 pengaman massal, D.8 env, D.9 observability), §14.7 L-72
(D-22-13/15/17/18) dan **L-73** (job hangus & anonimisasi **OFF di production** sampai Legal/DPO/Meta setuju), risiko
liabilitas poin (L-67). Audiens: on-call IT EFN, Superadmin, Finance (pemilik angka breakage).

> **Ringkasan manajemen.** Poin member yang tidak beraktivitas 12 bulan akan hangus, didahului pesan WhatsApp 30 hari
> sebelumnya. Fitur ini sudah dibangun tetapi **terkunci mati di production** sampai teks syarat program disetujui Legal
> dan template WhatsApp disetujui Meta. Menyalakannya butuh dua pihak: konfigurasi rilis (tim IT) **dan** izin tertulis
> Superadmin di server. Bila jumlah member yang akan hangus dalam satu malam melebihi batas aman (20.000), sistem
> membatalkan seluruh proses malam itu dan memanggil on-call — tidak ada poin yang hilang karena kesalahan data.

## Kontrol yang terpasang (devsecops G22)

| Kontrol | Di mana | Efek |
|---|---|---|
| Kill switch default `false` | compose `LOYALTY_EXPIRY_ENABLED`, `LOYALTY_INACTIVE_ANONYMIZE`; `check-encryption.sh` (render prod) | Job & anonimisasi mati kecuali dinyalakan eksplisit |
| Gerbang L-73 | `scripts/lib/deploy-validate.sh` `EFN_GATED_FEATURES` | Production `true` ditolak kecuali nama variabel ada di `EFN_ALLOW_FEATURE_ON` di `/etc/efn/deploy.conf` (root, dipin host; tidak bisa lewat manifest/CI) |
| Epoch wajib | deploy-validate | `LOYALTY_EXPIRY_ENABLED=true` tanpa `LOYALTY_ACTIVITY_EPOCH` (YYYY-MM-DD, D-22-15) ditolak |
| Anonimisasi hanya setelah hangus | deploy-validate | `LOYALTY_INACTIVE_ANONYMIZE=true` tanpa job hangus ditolak |
| Dry-run default | compose `LOYALTY_EXPIRY_DRY_RUN=true` | Run pertama tiap lingkungan hanya menghitung kandidat per penerbit |
| Saklar consent notifikasi (SEC-171, kontrak v9.1 §14.D.4) | env BFF `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT`, default **`true`** | WA pengingat hanya dikirim ke member dengan consent `marketing` aktif. Member lain tetap melihat info hangus di aplikasi (D.6), dan tanggal hangus tetap ≥ 30 hari setelah pemberitahuan dicatat, jadi hangus tidak dipercepat. **Dikerjakan paralel G22, belum diverifikasi.** Infra (devsecops G22 r15): compose meneruskan `${…:-true}`, deploy.yml/deploy.sh/manifest rilis, `check-encryption.sh` (render prod wajib `true`), deploy-validate `EFN_GATED_FEATURES_OFF`: production `false` ditolak kecuali nama variabel ada di `EFN_ALLOW_FEATURE_ON` (gerbang yang sama dengan L-73, langkah 4a) |
| D-22-18 | deploy-validate, `check-encryption.sh`, manifest efn-deploy | `LOYALTY_POINT_EXPIRY_DAYS` (dihapus) → production ditolak, staging peringatan; tidak pernah diteruskan ke BFF |
| Rentang | deploy-validate | bulan 6..36, notice 7..60 hari, jam 0..23, batch 1..5000, `MAX_PER_RUN` 1..999999, `MAX_AGE_DAYS` 0..3650, grace 0..90 |
## Aktivasi (urutan wajib) {#aktivasi}

1. **Persetujuan tertulis** (simpan di arsip CR): Legal (teks syarat program: definisi tidak aktif, 12 bulan,
   pemberitahuan 30 hari, poin hangus, akun dihapus bila D-22-14), Meta (template WA `efn_loyalty_inactivity_notice`
   APPROVED, kategori tercatat), Finance (jurnal breakage per PT penerbit siap, L-67), DPO bila anonimisasi.
   **Consent notifikasi (SEC-171):** biarkan `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=true` (default). Nilai `false`
   (WA ke semua member bersaldo atas dasar "pelaksanaan perjanjian program") **hanya** bila Legal/DPO menyatakannya
   tertulis (Q-P25, D-22-16) **dan** Meta mengategorikan template sebagai UTILITY. Bila Meta menggolongkannya
   MARKETING, nilai wajib `true` apa pun keputusan lain. Catat nilai yang dipakai dan dasar hukumnya di RoPA/DPIA §13.
   Sebelum run notice pertama, Finance/CS perlu tahu bahwa dengan `true` sebagian member hanya diberi tahu lewat
   aplikasi. Jalur keberatan (Q-P27) harus siap untuk mereka.
2. **Staging dulu:** GitHub variables staging `LOYALTY_EXPIRY_ENABLED=true`, `LOYALTY_ACTIVITY_EPOCH=<tanggal go-live>`,
   `LOYALTY_EXPIRY_DRY_RUN=true` → Deploy. Tunggu satu run 01:00 WIB; baca heartbeat
   `{stream="efn_loyalty", loyalty_event="expiry_run", expiry_status="dry_run"}` (jumlah kandidat per penerbit). Finance
   mencocokkan angka dengan ekspektasi.
3. **Production, izin host (Superadmin):** pasang ulang `efn-deploy` versi G22 (mengenal kunci baru; sha256 di-review)
   lalu tambahkan ke `/etc/efn/deploy.conf` (root, 0644):
   ```
   EFN_ALLOW_FEATURE_ON='LOYALTY_EXPIRY_ENABLED'
   ```
   (tambahkan `LOYALTY_INACTIVE_ANONYMIZE` dipisah spasi **hanya** setelah DPO + Legal menyetujui anonimisasi).
4. **Production, config rilis:** variables `LOYALTY_EXPIRY_ENABLED=true`, `LOYALTY_ACTIVITY_EPOCH`,
   `LOYALTY_EXPIRY_DRY_RUN=true` → rilis bertag → Deploy. Satu siklus dry-run, review Finance, lalu
   `LOYALTY_EXPIRY_DRY_RUN=false` → Deploy.
4a. **Melepas filter consent notice (opsional, SEC-171)** — hanya bila Legal memutuskan notice hangus berdasar
   "pelaksanaan perjanjian program" (bukan consent) **dan** Meta mengategorikan template sebagai UTILITY. Catat keputusan
   di RoPA/DPIA §13. Superadmin menambahkan `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT` ke `EFN_ALLOW_FEATURE_ON`
   (dipisah spasi), lalu variable `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=false` → rilis bertag → Deploy. Tanpa izin host,
   deploy production gagal di validasi (`production menolak LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=false`). Mengembalikan:
   variable `true` (atau hapus) → Deploy; hapus nama dari `EFN_ALLOW_FEATURE_ON`.
5. Mematikan kembali: `LOYALTY_EXPIRY_ENABLED=false` → Deploy; hapus nama dari `EFN_ALLOW_FEATURE_ON`. Silence
   `EfnLoyaltyExpiryHeartbeatMissing` sampai 7 hari (dead-man masih "bersenjata" dari heartbeat terakhir).

## Alert

Sumber: heartbeat BFF `EFN_LOYALTY_EXPIRY_RUN {"status":"ok|dry_run|aborted|error","phase":"notice|expire|anonymize",...}`
(satu baris per fase per run, tanpa data member) → Promtail (`promtail.yml` tahap 4) → Loki ruler
`infra/observability/loki-rules/efn-loyalty.yml` grup `efn-loyalty-expiry`.

### EfnLoyaltyExpiryAborted (critical) {#efnloyaltyexpiryaborted}

Kandidat hangus satu run > `LOYALTY_EXPIRY_MAX_PER_RUN` (default 20.000) → run dibatalkan, **tidak ada** entri ledger.

1. Jangan menaikkan `LOYALTY_EXPIRY_MAX_PER_RUN` dulu. Ambil jumlah per penerbit dari baris heartbeat (Grafana Explore:
   `{stream="efn_loyalty", loyalty_event="expiry_run", expiry_status="aborted"}`).
2. Penyebab umum: `LOYALTY_ACTIVITY_EPOCH` terlalu lama (≥ 12 bulan lalu → semua member tanpa aktivitas baru langsung
   jatuh tempo), backfill `loyalty_member_activity` migrasi 11 tidak berjalan/kosong, impor saldo migrasi K-7 tanpa
   aktivitas, jam sistem salah.
3. Bila jumlah memang benar (mis. go-live dengan basis member lama): keputusan Finance + PO tertulis, lalu naikkan batas
   sementara lewat config rilis (maks 999.999) atau jalankan beberapa malam dengan batas sama (job melanjutkan besok).
4. Alert padam sendiri ≤ 26 jam setelah baris terakhir; silence setelah tindakan dicatat.

### EfnLoyaltyExpiryFailed (warning) {#efnloyaltyexpiryfailed}

Run berakhir `status=error` (DB, advisory lock, kebijakan global tidak terbaca). Member yang belum diproses dicoba lagi
run berikutnya (idempoten: lock member + cek ulang jangkar + status notice). Periksa log BFF `[LoyaltyInactivity*]`
sekitar 01:00/10:00 WIB; bila berulang 2 malam → eskalasi ke efn-bff-engineer.

### EfnLoyaltyExpiryHeartbeatMissing (warning) {#efnloyaltyexpiryheartbeatmissing}

Tidak ada heartbeat 26 jam padahal ada dalam 7 hari terakhir: BFF mati/restart terus, job dimatikan, lock tertahan,
atau format log berubah (alert aborted/error ikut buta). Cek `docker logs efn-api-gateway-1 | grep EFN_LOYALTY_EXPIRY_RUN`;
bila baris ada tetapi tidak masuk Loki → regex Promtail tahap 4 tidak cocok (uji: `scripts/test-promtail-redaction.sh`).
Bila job sengaja dimatikan: silence ≤ 7 hari.

Notifikasi WA gagal permanen masuk DLQ `notify.push.dlq` (alert DLQ yang sudah ada, [rabbitmq-dlq.md](rabbitmq-dlq.md));
hangus tetap berjalan sesuai syarat program (§14.D.4).

## Batas / belum

- Format heartbeat (kunci pertama `status`, kedua `phase`, konteks logger `LoyaltyInactivity*`) adalah kontrak devsecops
  → efn-bff-engineer; belum diverifikasi terhadap keluaran BFF nyata.
- Metrik Prometheus `efn_loyalty_expiry_total` / `efn_loyalty_expired_points_total` belum dipakai alert (deret counter
  hilang saat restart; dead-man berbasis log lebih andal). Panel Grafana belum dibuat.
- Template WA & kategori Meta, pasal UU PDP anonimisasi: keputusan Legal/DPO (L-73), di luar repo.
- Saklar `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT` (SEC-171) baru masuk kontrak v9.1 (19 Okt 2026). Implementasi BFF,
  penerusan env di compose, dan gerbang deploy (dilaporkan devsecops G22 r15, baris tabel di atas + langkah 4a)
  **belum diverifikasi** architect. Belum ada metrik jumlah member yang
  dilewati karena tanpa consent. Usulan: label `outcome="skipped_no_consent"` di `efn_loyalty_expiry_total`.
