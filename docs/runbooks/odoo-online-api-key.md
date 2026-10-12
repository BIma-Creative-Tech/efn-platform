# Runbook: API key Odoo kanal online (rotasi otomatis 30 hari, R-T15)

Dasar: docs/04 §10.4.1 & §10.10, ADR-034 (Proposed), ADR-024 (rotasi dua kunci), keputusan pemilik proyek 10 Okt 2026
#33 (**key 30 hari + rotasi otomatis**, paket **W-11**), risiko **R-T15** (API key kanal online kedaluwarsa →
checkout berhenti). Audiens: on-call IT EFN + Superadmin.

> **Ringkasan manajemen.** Super-App berbicara dengan Odoo memakai satu "kunci API" per PT (pilot: PT Retail, kanal
> `RTL`). Kunci berlaku **30 hari** dan diganti **otomatis** oleh server setiap hari ke-20 (tanpa downtime; BFF
> di-restart beberapa detik pada 03:15 WIB). Kunci lama dicabut 1 jam setelah kunci baru terbukti bekerja. Bila
> penggantian gagal, kunci lama tetap dipakai dan alert muncul; masih ada ±10 hari untuk memperbaiki sebelum
> pembayaran online berhenti (POS di toko tidak terdampak).

## Fakta teknis (Odoo CE 19, diverifikasi dari source image `efn/odoo:dev`, 9–10 Okt 2026)

- Key = 40 karakter hex acak (`res.users.apikeys._generate`), disimpan Odoo sebagai hash; teks asli hanya terlihat
  sekali saat dibuat.
- `/json/2` hanya menerima key **scope global** (`scope = None`); key ber-scope lain ditolak (401).
- User non-admin wajib memberi `expiration_date` ≤ **maksimum** `api_key_duration` dari **semua** grupnya
  (`_check_expiration_date`: `max(group.api_key_duration for group in all_group_ids)`). Grup
  `efn_online_order.group_online_channel` = **35 hari** (`efn_setup_online`, W-11: 30 hari + margin 5). ⚠ Bila grup
  lain milik user teknis (mis. `base.group_user`) punya durasi lebih besar, batas efektifnya ikut lebih besar — jangan
  beri durasi di grup umum. Membuat key lewat `sudo()`/admin **melewati** batas ini — skrip selalu `with_user(<user teknis>)`.
- Satu key = satu user teknis = satu PT: `efn_online_rtl` (kelak `efn_online_fnb`). User teknis hanya `base.group_user`
  + `group_online_channel`, satu company, tanpa ACL dokumen. Skrip menolak bila user tidak ada/nonaktif/bukan anggota
  grup kanal/punya `base.group_system` atau `base.group_erp_manager`.
- Secret Docker: `odoo_api_key_online_rtl` (`_fnb` disiapkan, tidak di-mount selama kanal mati). Sejak G18 secret ini
  berjenis **`host:`** di `scripts/secrets.list`: **dikelola di host**, TIDAK ada di GitHub Environment, tidak dikirim
  `deploy.yml`, ditolak `efn-deploy` dari stdin, dan **tidak ditimpa** `deploy.sh` (file belum ada → placeholder
  `disabled`). Dev/CI berisi placeholder `disabled`; BFF menolak placeholder bila kanal online diaktifkan.
- Pencabutan (`_remove`) membersihkan cache registry + `signal_changes()`; skrip **membuktikan** key lama ditolak (401)
  lewat gateway setelah dicabut.

## 1. Pemasangan (sekali per host, Superadmin) {#pemasangan}

Prasyarat: deploy G18 sudah jalan (menulis `/opt/efn/compose/.efn-compose-files`), user teknis + `efn_setup_online`
terpasang, `jq` + `util-linux` (flock) di host.

```bash
# 1) skrip + unit dari commit yang di-review (cocokkan sha256 dengan PR)
install -o root -g root -m 0755 scripts/host/efn-rotate-online-key /usr/local/sbin/efn-rotate-online-key
sha256sum /usr/local/sbin/efn-rotate-online-key
install -o root -g root -m 0644 scripts/host/systemd/efn-rotate-online-key@.service \
  scripts/host/systemd/efn-rotate-online-key@.timer /etc/systemd/system/
# 2) (opsional) /etc/efn/online-key-rotation.conf root:root 0644 - default: KEY_DAYS=30 ROTATE_BEFORE_DAYS=10
#    OVERLAP_SECONDS=3600 (lihat header skrip; nilai divalidasi, kunci di luar allowlist ditolak)
# 3) key pertama (menggantikan placeholder; kanal online boleh masih MATI - verifikasi langsung ke gateway)
sudo efn-rotate-online-key --channel=RTL          # rotasi karena state belum ada
sudo efn-rotate-online-key --channel=RTL --status # id, nama, kedaluwarsa (TANPA nilai key)
# 4) timer harian 03:15 WIB
systemctl daemon-reload && systemctl enable --now efn-rotate-online-key@RTL.timer
# 5) GitHub: hapus secret lama ODOO_API_KEY_ONLINE_RTL dari Environment staging/production (tidak dipakai lagi)
```

Migrasi dari pemasangan G17 (key manual via `gh secret set`): langkah 3 membuat key baru, memasangnya, dan mencabut
**semua** key lain user teknis setelah overlap (termasuk key manual). File metrik manual lama
`efn_odoo_api_key.prom` ditimpa skrip (satu file untuk semua kanal).

## 2. Cara kerja rotasi otomatis {#rotasi}

`efn-rotate-online-key@<KANAL>.timer` (harian, `Persistent=true`) → `efn-rotate-online-key --channel=<KANAL>`:

1. Daftar key user teknis (`odoo shell` di container `odoo` yang berjalan). Rotasi bila: `--force`, state tidak dikenal,
   key tanpa kedaluwarsa, atau sisa masa berlaku < `ROTATE_BEFORE_DAYS` (10). Selain itu: "TIDAK PERLU" + metrik.
2. Key **baru** `efn-online-<KANAL>-<UTC>` berlaku 30 hari (`with_user`, batas grup ditegakkan Odoo).
3. Salinan key lama ke `/var/lib/efn/online-key` (root 0700, file 0400); key baru ditulis **atomik** ke file secret
   yang di-mount compose (mktemp di direktori sama → `mv`; `root:efn-superadmin`, mode = `deploy.sh` 0444 — lihat
   [key-custody.md "Mekanisme uid"](key-custody.md); direktori 0750 tetap batas akses host).
4. Recreate service yang me-mount secret (+ sidecar `network_mode`), tunggu sehat (file compose sama dengan deploy).
5. Verifikasi di **setiap** replika BFF: `POST /json/2/efn.online.channel/efn_online_catalog` (`limit 1`) lewat
   `odoo-rpc-gateway` (mTLS, CA internal, key dari `/run/secrets`) → wajib **200**.
6. Gagal → **rollback**: key lama dipasang kembali + recreate + verifikasi, key **baru dicabut segera** (rc 1; rc 3 bila
   rollback juga gagal). Sukses → state baru, tunggu `OVERLAP_SECONDS` (1 jam), verifikasi ulang, cabut **semua** key
   lain user teknis, buktikan key lama ditolak (**401**), hancurkan salinan key lama. Bila proses mati saat overlap,
   run harian berikutnya menyelesaikan pencabutan tertunda.

Jejak: journald `efn-rotate-online-key` (hanya root, `_UID 0`) → `promtail-journal` → Loki
`{service="host-efn-rotate-online-key"}`: `MULAI`, `ROTASI`, `KEY BARU id= nama= kedaluwarsa=`, `VERIFIKASI OK|GAGAL`,
`ROLLBACK OK|GAGAL`, `DICABUT`, `KEY LAMA DITOLAK`, `SELESAI rc=`. **Nilai key tidak pernah dicetak** (stdout/stderr/
journald/argv); stderr `odoo shell` yang dilog disaring (hex ≥ 32 → `[REDACTED]`).

Rotasi darurat (dugaan kebocoran): `sudo efn-rotate-online-key --channel=RTL --force --overlap-seconds=0`, lalu
perlakukan sebagai insiden ([security-incident.md](security-incident.md)).

## 3. Metrik & alert (R-T15) {#metrik-masa-berlaku}

Textfile node-exporter `efn_odoo_api_key.prom` (ditulis setiap run, semua kanal):
`efn_odoo_api_key_expiry_timestamp_seconds`, `_active_keys`, `_rotation_last_run_ok`,
`_rotation_last_run_timestamp_seconds`, `_rotation_last_success_timestamp_seconds` (label `channel`).

| Alert | Kondisi | Severity |
|---|---|---|
| `EfnOnlineApiKeyRotationFailed` | run terakhir gagal (`last_run_ok == 0`) | warning |
| `EfnOnlineApiKeyRotationNotRunning` | tidak ada run > 2 hari + 1 jam | warning |
| `EfnOnlineApiKeyExtraKeys` | > 1 key aktif selama 26 jam | warning (security) |
| `EfnOnlineApiKeyExpiringSoon` / `ExpiryCritical` | sisa < 7 hari (= ≥ 3 hari gagal) / < 3 hari | warning / critical |
| `EfnOnlineApiKeyExpiryUnknown` | kanal dipakai tetapi metrik masa berlaku tidak ada | warning |
| `EfnOnlineApiKeyRollbackFailed` (Loki) | baris `ROLLBACK GAGAL` | critical |
| `EfnOnlineApiKeyOldKeyStillAccepted` (Loki) | key lama masih dijawab setelah dicabut | warning (security) |

## 4. Rotasi gagal / timer tidak jalan {#rotasi-gagal}

1. `journalctl -u efn-rotate-online-key@RTL --since -2d` (atau Loki `service="host-efn-rotate-online-key"`).
2. Penyebab umum per baris log:
   - `odoo create: ValidationError` → `KEY_DAYS` > `api_key_duration` grup (cek `efn_setup_online` = 35).
   - `odoo list: user_missing|user_inactive|user_not_channel|user_privileged` → perbaiki user teknis (UI/RPC,
     [odoo-access-changes.md](odoo-access-changes.md)).
   - `VERIFIKASI GAGAL status=401` dengan key baru → Odoo menolak key (DB lain? `ODOO_DB`); `status=000` → gateway/
     sertifikat/jaringan (`odoo-rpc-gateway` sehat?); `503` → worker online penuh (coba ulang).
   - `tidak sehat` / `timeout menunggu sehat` → `$DC logs api-gateway`.
   - `key lama masih dijawab` → pencabutan belum efektif di worker Odoo: `--status` (key masih terdaftar?), restart
     `odoo` di luar jam ramai, jalankan ulang; bila menetap = temuan keamanan.
3. Setelah diperbaiki: `sudo efn-rotate-online-key --channel=RTL --force`.
4. Key ekstra (`EfnOnlineApiKeyExtraKeys`): run biasa mencabut key lain setelah overlap; key buatan manual di luar
   prosedur = insiden. Cabut manual (break-glass, shell):
   `env['res.users.apikeys'].search([('user_id.login','=','efn_online_rtl'),('id','!=',<id terpasang>)]).sudo()._remove()`.

## 5. ROLLBACK GAGAL (critical) {#rollback-gagal}

Key baru gagal **dan** key lama tidak bisa dipasang kembali/diverifikasi → checkout online kemungkinan berhenti.

1. Kill switch bila perlu: `ONLINE_ENABLED=false` (cart/quote/order → 503; order yang sudah dibayar diproses saat
   kanal pulih — saga idempoten).
2. Pulihkan dependensi (BFF/`odoo-rpc-gateway`/Odoo sehat), lalu `sudo efn-rotate-online-key --channel=RTL --force`.
3. Key baru yang gagal sudah dicabut skrip (baris `DICABUT ... (key baru gagal)`); bila tidak, cabut lewat §4 butir 4.

## 6. `EfnOnlineOdooAuthFailure` (critical) {#efnonlineodooauthfailure}

Odoo menjawab 401/403 untuk kanal → key kedaluwarsa, dicabut, user teknis dinonaktifkan/berubah grup, atau secret salah.

1. `$DC logs --since 30m odoo-rpc-gateway | tail` — status 401 dengan `why=-` = ditolak **Odoo** (bukan filter gateway).
   Penolakan gateway (`why=auth|header|path|…`) → [online-orders.md](online-orders.md#efnodoorpcgatewaydenied).
2. `sudo efn-rotate-online-key --channel=RTL --status` (id/nama/kedaluwarsa; key terpasang ditandai).
3. `sudo efn-rotate-online-key --channel=RTL --force`. Selama itu kill switch opsional (§5 butir 1).
4. Bila key dicabut tanpa perubahan terencana → perlakukan sebagai insiden ([security-incident.md](security-incident.md)).

## 7. Dev / uji

- Dev: `scripts/smoke/run-e2e-smoke.sh` langkah setup membuat key dev (skrip smoke) — tidak memakai timer.
- Uji regresi skrip (docker/Odoo/BFF palsu, 42 kasus, CI job infra):
  `docker run --rm -v "$PWD:/src:ro" python:3.12-bookworm@sha256:e91fec3d… bash /src/scripts/host/test-efn-rotate-online-key.sh`.
- Uji nyata stack dev (Docker Desktop, 10 Okt 2026): skrip dijalankan di container `docker:27-cli` (socket Docker +
  repo di-mount pada `/run/desktop/mnt/host/e/...` agar path bind compose valid), `/etc/efn/online-key-rotation.conf`
  dengan `COMPOSE_DIR`/`COMPOSE_FILES=docker-compose.yml,docker-compose.dev.yml`/`SECRETS_DIR` repo,
  `SECRET_GROUP=root`, `OVERLAP_SECONDS=20`, env `COMPOSE_PROFILES`. Hasil: `--status` → rotasi (key 1 → key 2, kedua
  verifikasi 200, key 1 dicabut, 401 terbukti) → run kedua "TIDAK PERLU" → metrik lengkap.

## Batas / belum

- Recreate BFF (2 replika prod) bersamaan → permintaan online bisa 503 beberapa detik pada 03:15 WIB (rolling restart
  belum; compose tidak mendukung tanpa orchestrator).
- Kanal `FNB` / `FNB-<brand>`: pasang instance timer `efn-rotate-online-key@FNB.timer` saat kanal diaktifkan
  (overlay `docker-compose.online-fnb.yml` di-mount oleh deploy).
- Uji nyata rollback (key baru ditolak) hanya di test palsu; jalur sukses sudah diuji nyata di dev.
- Belum diuji di host Linux sungguhan dengan systemd/journald (staging belum ada, ADR-004).
