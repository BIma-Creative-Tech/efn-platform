# Runbook: Dashboard Insight (`insight-web`) + SSO dari Odoo (`efn_insight`)

- **Kontrak:** `docs/04_SERVICE_CONTRACTS.md` §26 · **Keputusan:** ADR-041 (**Proposed**) · **Gelombang:** G42
- **Komponen:** service `insight-web` + sidecar `insight-web-tls` (profil compose `data`, K25-02), modul Odoo
  `efn_insight` (menu *Dashboard Insight*), user ClickHouse `efn_insight` + row policy `efn_insight_scope`
  (`infra/clickhouse/access/insight-access.sql`, `infra/clickhouse/clickhouse-init.sh`).
- **Alert:** `EfnInsightWebDown` (warning, §6), `EfnHostClockNotSynced` (warning, §5).
- **Risiko:** R-S18 usulan (kunci SSO bocor = pemalsuan sesi semua brand), R-L01 (PDP: nama staf di cookie + log
  audit), R-T06 (angka dashboard dipakai manajemen).

> **Ringkasan manajemen.** Dashboard Insight dibuka dari menu Odoo tanpa password kedua; setiap staf hanya melihat PT
> yang boleh ia akses di Odoo. Fitur ini **mati secara default** (`INSIGHT_ENABLED=false`). Di production hanya bisa
> dinyalakan Superadmin setelah review security, DPO, dan jawaban klien Q-INS1 (siapa dapat grup) serta Q-INS4
> (boleh dari jaringan outlet atau tidak). Bila dashboard mati, Odoo dan POS tidak terdampak.

## 1. Arsitektur singkat (yang perlu diketahui operator)

```
Browser staf (WireGuard, subnet staf)
  -> https://erp.<domain>/efn/insight/open     Odoo: cek grup + INSIGHT_ENABLED, buat token eit1 (60 dtk), audit ir.logging
  -> POST https://insight.<domain>/sso          insight-web: verifikasi HMAC/waktu/jti -> cookie __Host-eis (8 jam)
  -> https://insight.<domain>/...               insight-web -> ClickHouse https://clickhouse:8443 (user efn_insight)
```

| Hal | Nilai |
|---|---|
| Router Traefik | `Host(insight.<domain>)`, entrypoint `wg-https` saja, middleware `internal-ops` (subnet staf) + `secure-headers-insight` (no-referrer) + `ratelimit-insight` |
| Router form SSO Odoo | `odoo-insight-open`: `Host(erp.<domain>) && Path(/efn/insight/open)`, priority 220, middleware `internal-only` + **`secure-headers-insight-sso`** (`Referrer-Policy: strict-origin`) + `ratelimit-odoo`. Wajib `strict-origin`: dengan `no-referrer`/`same-origin` browser mengirim `Origin: null` pada POST ke insight-web dan login sah ditolak (C1). Middleware headers Traefik menimpa nilai dari Odoo, jadi jangan ganti ke `secure-headers` biasa |
| Konkurensi ClickHouse | satu user `efn_insight` untuk semua staf; profil `insight_readonly` `max_concurrent_queries_for_user = 24`; insight-web memakai semaphore `INSIGHT_CH_MAX_CONCURRENCY` (default 12, rentang 1..24, di luar rentang → 12). Satu halaman = 9–13 query |
| Network | `insight-ingress` (traefik, insight-web, blackbox-exporter) dan `insight-data` (insight-web, clickhouse). **Tidak** di `internal`, `data`, atau `egress`: insight-web tidak bisa menjangkau PostgreSQL, RabbitMQ, Odoo, BFF, internet (ADR-041 C7) |
| Odoo ↔ insight-web | **Tidak ada** jalur jaringan; token dibawa browser lewat POST form |
| Replika | 1 (L-124: cache `jti` + rate limit di memori). Restart = cache `jti` hilang (C5, risiko diterima) |
| Sertifikat | leaf PKI `insight-web` (server), SAN `insight-web` — `scripts/gen-pki.sh` |
| Memori | insight-web 512M + sidecar 64M (masuk anggaran profil `data`, K25-06) |

Secret (semua `hex32`/`random` di `scripts/secrets.list`; mount dicek `scripts/check-encryption.sh`):

| Secret | Dipakai | Isi |
|---|---|---|
| `insight_sso_key` | odoo (tanda tangan), insight-web (verifikasi) | 64 hex; **kunci = string hex apa adanya** (bukan hex-decode, §26.3) |
| `insight_session_key` | insight-web | 64 hex; tanda tangan cookie sesi |
| `insight_scope_key` | clickhouse-init (row policy), insight-web | 64 hex; **wajib beda** dari `text2sql_scope_key` (clickhouse-init menolak bila sama) |
| `clickhouse_password_insight` | clickhouse-init (membuat user), insight-web | password user `efn_insight` |

Ketiga kunci HMAC wajib **saling berbeda**: insight-web menolak start (exit 1, log `boot_config`) bila kembar atau
lemah — dicek selalu untuk file yang ada, juga saat `INSIGHT_ENABLED=false`; `clickhouse-init` menolak bila
`insight_scope_key` = `text2sql_scope_key`. Yang tidak dicek otomatis: `insight_sso_key` Odoo vs insight-web identik
(salah = `bad_sig`, §6) dan kunci kembar dengan secret layanan lain.

## 2. Aktivasi

Urutan (staging dulu, lalu production):

1. **Prasyarat deploy pertama G42 (staging/production)** — satu kali:
   - GitHub Environment berisi `INSIGHT_SSO_KEY`, `INSIGHT_SESSION_KEY`, `INSIGHT_SCOPE_KEY`,
     `CLICKHOUSE_PASSWORD_INSIGHT` (nilai: `openssl rand -hex 32`, masing-masing **dibuat terpisah**). Tanpa ini
     `deploy.sh` berhenti (secret wajib) dan, bila dipaksa, `clickhouse-init` gagal sehingga profil `data` tidak naik.
   - **Intermediate PKI diterbitkan ulang** (Superadmin, mesin offline): `make pki-intermediate ENV=<env>` lalu unggah
     ke GitHub Environment (`EFN_PKI_INTERMEDIATE_*`). nameConstraints intermediate lama tidak memuat `insight-web` →
     leaf `tls_insight_web` gagal diverifikasi dan deploy berhenti di "Verifikasi PKI" (docs/runbooks/internal-pki.md).
     Dev/CI: `bash scripts/gen-secrets.sh` menerbitkan ulang otomatis.
   - **Wrapper host `efn-deploy` dipasang ulang** dari rilis ini (`install -o root -g root -m 0755
     scripts/host/efn-deploy /usr/local/sbin/efn-deploy`, key-custody.md §4): manifest rilis kini memuat 10 image
     (`IMAGE_INSIGHT_WEB`); wrapper lama menolaknya ("manifest rilis tidak valid (skema)").
2. **Modul Odoo**: pasang/upgrade `efn_insight` (prosedur modul biasa, docs/runbooks/deploy-odoo-modules.md).
3. **Grup Odoo** (menunggu **Q-INS1**): Settings → Users → user → tab Access Rights:
   - *Insight: pengguna* (`efn_insight.group_insight_user`) — staf per PT; cakupan = PT berkode ESB (BCF, BCR, CUP,
     IMD, PB, WTZ) di **semua** `Allowed Companies` user (Q-INS2).
   - *Insight: semua brand/Holding* (`efn_insight.group_insight_all_brands`) — Direksi/Finance Holding/planner pusat;
     melihat keenam kode. Usulan: penetapan grup ini lewat 4-eyes (R-S16) sampai Q-INS1 dijawab.
4. **Saklar**:
   - Dev: `INSIGHT_ENABLED=true` di `infra/compose/.env` → `docker compose ... up -d odoo insight-web`.
   - Staging: variable GitHub Environment `INSIGHT_ENABLED=true` → deploy.
   - Production: variable `INSIGHT_ENABLED=true` **dan** Superadmin menambahkan `INSIGHT_ENABLED` ke
     `EFN_ALLOW_FEATURE_ON` di `/etc/efn/deploy.conf` (deploy-validate menolak tanpa itu; L-125). Satu saklar berlaku
     untuk Odoo dan insight-web.
5. **Verifikasi** (dari laptop staf di subnet WireGuard staf):
   - Menu *Dashboard Insight* → halaman insight terbuka, PT di filter sesuai akses user.
   - User tanpa grup → 403 di Odoo; user grup tanpa PT berkode ESB → pesan "Tidak ada PT yang bisa ditampilkan".
   - `docker compose ... logs insight-web | grep -E 'ok|bad_'` → kode hasil `ok`; tidak ada token/cookie di log.
   - Dev: `https://insight.efn.local:9443` (hosts → 127.0.0.1), masuk lewat `https://erp.efn.local:9443`.
6. **Akses jaringan**: router `insight.` memakai `internal-ops` (subnet staf). Manajer outlet dari jaringan outlet
   akan mendapat **403 dari Traefik** setelah token dibuat Odoo — ini perilaku yang disengaja sampai **Q-INS4**
   dijawab. Bila klien mengizinkan: ganti middleware router menjadi `internal-only@file` (compose base, label
   `traefik.http.routers.insight.middlewares`) lewat PR + ADR/kontrak diperbarui.

**Menonaktifkan darurat:** set `INSIGHT_ENABLED=false` (config rilis / `.env`) dan deploy/recreate `odoo insight-web`
→ Odoo berhenti membuat token, insight-web menolak semua halaman termasuk cookie yang masih berlaku.
Lebih cepat tanpa deploy: `docker compose ... stop insight-web insight-web-tls` (dashboard mati, Odoo tidak terdampak).

## 3. Metabase hanya untuk analis Holding (SEC-182 / L-126 / Q-INS8)

Pengguna per PT dialihkan ke Insight; `metabase_reader` di ClickHouse membaca **semua PT** (tanpa row policy per PT).
SEC-182 dianggap tertutup bila:
1. Akun Metabase hanya untuk anggota grup Holding (Admin Metabase → People: nonaktifkan akun staf per PT; grup
   Metabase "Holding" satu-satunya grup dengan akses koleksi ESB).
2. Koleksi/dashboard ESB di Metabase hanya diberi izin grup Holding (Admin → Permissions → Collections).
3. Jaringan: router `bi.` tetap `internal-ops` (subnet staf).
4. Bukti (screenshot permission + daftar akun aktif) dilampirkan ke laporan security; dicek ulang tiap kuartal
   bersama housekeeping akun 90 hari (retention.md#metabase-housekeeping).

Status: **menunggu konfirmasi klien Q-INS8**. Sampai itu, jangan buat akun Metabase baru untuk staf per PT.

> **SEC-191 (18 Nov 2026):** sebelum G42, DDL row policy (memuat `text2sql_scope_key` dan `insight_scope_key` apa
> adanya) tercatat di `system.query_log` ClickHouse. `clickhouse-init.sh` kini memakai `log_queries=0`. Pada lingkungan
> yang pernah menjalankan versi lama: **rotasi kedua scope key** (§4) setelah deploy pertama versi ini.

## 4. Rotasi kunci

Jadwal usulan (Q-INS9): 90 hari, dan **segera** bila dicurigai bocor. Nilai baru: `openssl rand -hex 32` (64 hex),
selalu berbeda dari kunci lain. Staging/prod: ganti nilai di GitHub Environment lalu jalankan workflow Deploy;
dev: tulis ke `secrets/<nama>.txt`. Tidak ada mode "dua kunci" (kunci lama + baru) — rotasi selalu memutus sesuatu,
jadi lakukan di luar jam sibuk dan umumkan.

### 4.1 `insight_sso_key` (Odoo + insight-web)
Dampak: token yang sedang dalam perjalanan (≤ 60 dtk) gagal → user klik menu lagi. Sesi yang sudah ada **tidak**
terdampak (cookie memakai kunci sesi).
1. Ganti nilai secret.
2. Deploy / `docker compose ... up -d --force-recreate odoo insight-web` — **keduanya bersamaan** (recreate odoo juga
   me-recreate sidecar `odoo-tls`/`odoo-ws-tls`; POS reconnect beberapa detik → lakukan di luar jam operasional
   outlet, atau terima jeda singkat).
3. Verifikasi login lewat menu Odoo (kode log `ok`). Selisih kunci antar-service → semua login `bad_sig` (§6).
4. Bila rotasi karena kebocoran: cocokkan log `/sso` (`sub`, `jti`) dengan audit Odoo `ir.logging` (`jti` yang tidak
   ada di Odoo = token palsu), lalu juga rotasi `insight_session_key` (sesi palsu yang sudah terbentuk tetap berlaku
   sampai kunci sesi diganti).

### 4.2 `insight_session_key` (insight-web saja)
Dampak: **semua sesi keluar** (cookie lama gagal verifikasi) → user membuka lagi dari Odoo. Ini juga mekanisme
pencabutan darurat (L-119: user dinonaktifkan di Odoo masih memegang cookie ≤ 8 jam).
1. Ganti nilai secret. 2. `up -d --force-recreate insight-web` (sidecar ikut). 3. Verifikasi login ulang.

### 4.3 `insight_scope_key` (clickhouse-init + insight-web) — urutan wajib
Row policy di ClickHouse menyimpan kunci di DDL; insight-web menandatangani setting dengan kunci yang sama. Selama
keduanya berbeda **semua query mengembalikan 0 baris / error** (fail closed, tidak bocor).
1. Ganti nilai secret.
2. Jalankan ulang `clickhouse-init` (idempoten; `CREATE ROW POLICY OR REPLACE`):
   `docker compose ... --profile data up --force-recreate --no-deps clickhouse-init` → harus exit 0
   (`docker compose ... ps -a clickhouse-init`). Deploy penuh juga menjalankannya.
3. Segera recreate insight-web: `up -d --force-recreate --no-deps insight-web insight-web-tls`.
4. Verifikasi: halaman Ringkasan menampilkan angka untuk PT dalam cakupan. Jendela antara langkah 2 dan 3 = dashboard
   kosong (biasanya < 1 menit).
5. Bukti row policy terpasang: `SELECT short_name, table FROM system.row_policies WHERE database = 'efn_insight'`
   (sebagai admin `efn`) — satu baris per view; clickhouse-init sendiri mencabut grant bila ada view tanpa policy.

### 4.4 `clickhouse_password_insight` (clickhouse-init + insight-web)
1. Ganti nilai secret. 2. Jalankan ulang `clickhouse-init` (mengubah password user `efn_insight`).
3. Recreate insight-web. Jendela di antaranya: query gagal autentikasi (halaman error generik), tanpa kebocoran.

## 5. NTP wajib (C6)

Token `eit1` hanya berlaku 60 dtk dengan toleransi `iat` +30 dtk. Odoo dan insight-web bisa berada di **host
berbeda** (stack data terpisah, K25-02) → jam kedua host harus sinkron.

- Host Linux: `timedatectl` → `System clock synchronized: yes`, `NTP service: active`. Gunakan chrony atau
  systemd-timesyncd dengan sumber NTP yang sama di semua host (mis. `id.pool.ntp.org` atau NTP internal klien).
- Cek selisih: `chronyc tracking` (`System time` < 0,1 dtk normal) / `timedatectl timesync-status`.
- Alert `EfnHostClockNotSynced` (warning, 30 menit): `node_timex_sync_status == 0` atau `|node_timex_offset_seconds| > 10`.
  Gejala di Insight: login gagal dengan kode `expired` (jam insight-web lebih cepat) atau `bad_claims` (`iat` di masa
  depan > 30 dtk; jam Odoo lebih cepat).
- Dev (Docker Desktop/WSL2): jam WSL bisa melompat setelah laptop tidur → `wsl --shutdown` atau restart Docker
  Desktop; alert jam di dev boleh diabaikan.

## 6. Troubleshooting

### `EfnInsightWebDown` (probe `https://insight-web:3443/api/health` gagal 15 menit)
1. `docker compose ... ps insight-web insight-web-tls` — keduanya `healthy`?
2. insight-web restart loop → `logs insight-web`: kunci tidak terbaca/pendek (fail closed saat start), `CH_URL` bukan
   https di `APP_ENV=production`, OOM (512M). Perbaiki secret/env lalu recreate.
3. Sidecar tidak sehat → `logs insight-web-tls`: sertifikat `tls_insight_web` kedaluwarsa/tidak cocok (deploy ulang =
   leaf diperbarui) atau insight-web tidak listen di `127.0.0.1:3002`.
4. Keduanya sehat tetapi probe gagal → blackbox-exporter tidak di network `insight-ingress` atau CN klien ditolak
   (`--allow-cn=blackbox-exporter`). `docker compose ... config` untuk memastikan.
5. `clickhouse-init` gagal → insight-web tidak start (depends_on). Lihat `logs clickhouse-init` (kunci cakupan bukan
   hex/≥64, sama dengan `text2sql_scope_key`, secret insight tidak di-mount).

### Halaman 403 dari Traefik di `insight.`
IP sumber bukan subnet staf (`EFN_WG_STAFF_CIDR`), mis. dari LAN outlet atau peer WireGuard outlet — disengaja
(Q-INS4, §2 butir 6). Dev: pastikan akses lewat `127.0.0.1:9443` (`EFN_INTERNAL_EXTRA_CIDR` dev).

### 401 "Tautan masuk tidak valid atau sudah dipakai" setelah klik menu Odoo
Lihat kode di `logs insight-web` (tidak pernah memuat token):

| Kode | Sebab umum | Tindakan |
|---|---|---|
| `bad_sig` | `insight_sso_key` Odoo ≠ insight-web (rotasi hanya di satu sisi, salah satu belum di-recreate, file berisi spasi/baris ganda) | samakan secret, recreate keduanya (§4.1) |
| `expired` | Tombol "Lanjut" diklik > 60 dtk setelah halaman Odoo dibuka; jam insight-web maju; browser memuat ulang form lama dari riwayat | klik menu lagi; cek NTP (§5) |
| `bad_claims` | `iat` di masa depan > 30 dtk (jam Odoo maju), versi `efn_insight` dan insight-web tidak cocok (kunci payload), `codes` di luar daftar | cek NTP; samakan versi modul/image |
| `replay` | Form dikirim dua kali (klik ganda, tombol back + kirim ulang) | klik menu lagi (token baru) |
| `bad_format` | Body bukan `t=eit1...` (proxy/ekstensi browser mengubah form), token terpotong | coba browser lain / nonaktifkan ekstensi |
| `rate_limited` (429) | > 10 POST `/sso` per menit dari IP yang sama (satu NAT kantor = satu IP) | tunggu 1 menit; bila sah dan sering, naikkan batas di aplikasi (PR) |
| `bad_origin` (C1) | Header `Origin` **tidak ada**, `null`, atau ≠ `EFN_INSIGHT_ODOO_URL` (skema+host+port persis; dev wajib `:9443`). Penyebab umum: `EFN_INSIGHT_ODOO_URL` salah; halaman form Odoo tidak ber-`Referrer-Policy: strict-origin` (router `odoo-insight-open` tidak memakai `secure-headers-insight-sso`, atau diakses lewat host lain/IP); klien non-browser (curl) tanpa `-H Origin`. Token **tidak** terbakar (Origin diperiksa sebelum jti) | perbaiki env / middleware; di browser, DevTools → Network → request `sso` → header `Origin` |
| 404 di `/sso` | `INSIGHT_ENABLED` ≠ `true` di insight-web (Odoo sudah `true`; Odoo/insight-web hanya menerima string `true` persis, `1`/`True` = mati). Halaman lain menampilkan "belum diaktifkan" | samakan saklar (`true` persis), recreate keduanya |
| 503 "belum dikonfigurasi" di `/sso` | kunci SSO/sesi tidak terbaca atau `EFN_INSIGHT_ODOO_URL` kosong di insight-web (log `misconfigured`) | perbaiki secret/env, recreate |

Odoo sendiri: 503 "Insight belum dikonfigurasi" + log ERROR `insight_misconfigured` = `EFN_INSIGHT_URL` bukan https /
memuat path, atau `insight_sso_key` tidak terbaca / < 64 hex di container odoo.

### "Sesi berakhir, buka lagi dari Odoo" berulang
Cookie > 8 jam, `insight_session_key` baru dirotasi, atau insight-web dibuka lewat host lain (cookie `__Host-` terikat
host `insight.<domain>`). Bila terjadi tepat setelah login: jam insight-web salah (§5).

### Kartu galat "timeout" / "tidak bisa dimuat" pada banyak blok sekaligus
Log insight-web `ch_query` kind `timeout` dengan `queued` besar = antrean semaphore penuh (banyak staf membuka halaman
bersamaan; satu halaman 9–13 query, antrean maks 200, tunggu maks 20 dtk). ClickHouse galat HTTP `ch_code` **202**
(`TOO_MANY_SIMULTANEOUS_QUERIES`) = `INSIGHT_CH_MAX_CONCURRENCY` > batas profil (24) atau ada klien lain memakai user
`efn_insight`. Tindakan: turunkan `INSIGHT_CH_MAX_CONCURRENCY` (≤ 24), cek `SELECT count() FROM system.processes WHERE user
= 'efn_insight'` sebagai admin, naikkan profil hanya lewat PR (`insight-access.sql` CREATE dan ALTER keduanya).

### Dashboard kosong / angka 0 untuk semua PT
`insight_scope_key` clickhouse-init ≠ insight-web (§4.3 belum lengkap), row policy belum terpasang (clickhouse-init
gagal), atau mart ESB belum terisi (cek halaman *Kualitas data* / runbook esb-extract.md §5).

## 7. Data pribadi & retensi

- Cookie memuat `sub` (id user Odoo) + nama tampilan (≤ 64 karakter), tanpa email/login/telepon; tidak ada sesi di
  server. Log insight-web: kode hasil, `sub`, `jti`, durasi — tanpa token/cookie.
- Audit pembukaan di Odoo `ir.logging` (uid, kode PT, peran, jti) — retensi menunggu **Q-INS3** (usulan 400 hari);
  entri inventaris PDP belum dibuat (tugas DPO/architect, ADR-041 §4.4).

## 8. Yang belum / sisa risiko

- Production menunggu review security, DPO, Q-ESB1/7/9, Q-INS1, Q-INS4 (L-125).
- Kunci HMAC kembar dicek insight-web saat start (§1), tetapi `deploy-validate`/CI tidak memeriksa kesamaan nilai antar
  secret `insight_*` di GitHub Environment; kesalahan baru terlihat saat container start.
- Semaphore konkurensi ClickHouse per proses (replika 1): bila nanti replika > 1, batas efektif = replika × nilai env
  terhadap profil 24 per user ClickHouse → turunkan `INSIGHT_CH_MAX_CONCURRENCY` atau gunakan user per replika.
- Replika tunggal; restart = cache `jti` hilang (token ≤ 60 dtk bisa dipakai ulang sekali, C5).
- Bila stack data dipindah ke mesin lain (K25-02): Traefik di mesin inti tidak bisa menjangkau network Docker
  `insight-ingress` di mesin data → butuh Traefik/edge di mesin data atau jalur WireGuard + TLS verify-full; belum
  dirancang (ADR-004).
