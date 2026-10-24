# Runbook: API key Odoo kanal online (rotasi otomatis 30 hari, R-T15)

Dasar: docs/04 §10.4.1 & §10.10, ADR-034 (Proposed), ADR-024 (rotasi dua kunci), keputusan pemilik proyek 10 Okt 2026
#33 (**key 30 hari + rotasi otomatis**, paket **W-11**), risiko **R-T15** (API key kanal online kedaluwarsa →
checkout berhenti). Audiens: on-call IT EFN + Superadmin.

> **Ringkasan manajemen.** Super-App berbicara dengan Odoo memakai satu "kunci API" per **kanal** (sejak G20: satu
> kanal per PT dengan kode PT — Grand Lucky `GL`, Paris Baguette `PB`, Bacha Coffee Retail `BCR`, Bacha Coffee F&B
> `BCF`; kode lama `RTL`/`FNB` dan kanal G19 `RTL-GL`, `RTL-BACHA`, `FNB-PB`, `FNB-BACHA` dihapus). Kunci berlaku **30 hari** dan diganti **otomatis** oleh server setiap hari ke-20 untuk
> semua kanal yang terdaftar (tanpa downtime; BFF di-restart beberapa detik pada 03:15 WIB). Kunci lama dicabut 1 jam
> setelah kunci baru terbukti bekerja. Bila penggantian satu kanal gagal, kanal lain tetap diproses, kunci lama kanal itu
> tetap dipakai dan alert muncul **untuk kanal itu**; masih ada ±10 hari untuk memperbaiki sebelum pembayaran online
> kanal itu berhenti (POS di toko tidak terdampak).

## Fakta teknis (Odoo CE 19, diverifikasi dari source image `efn/odoo:dev`, 9–10 Okt 2026)

- Key = 40 karakter hex acak (`res.users.apikeys._generate`), disimpan Odoo sebagai hash; teks asli hanya terlihat
  sekali saat dibuat.
- `/json/2` hanya menerima key **scope global** (`scope = None`); key ber-scope lain ditolak (401).
- User non-admin wajib memberi `expiration_date` ≤ **maksimum** `api_key_duration` dari **semua** grupnya
  (`_check_expiration_date`: `max(group.api_key_duration for group in all_group_ids)`). Grup
  `efn_online_order.group_online_channel` = **35 hari** (`efn_setup_online`, W-11: 30 hari + margin 5). ⚠ Bila grup
  lain milik user teknis (mis. `base.group_user`) punya durasi lebih besar, batas efektifnya ikut lebih besar — jangan
  beri durasi di grup umum. Membuat key lewat `sudo()`/admin **melewati** batas ini — skrip selalu `with_user(<user teknis>)`.
- Satu key = satu user teknis = **satu kanal** (L-37). G20 (docs/04 §12.1, L-48): kode kanal = kode PT; pemetaan
  kode → nama = huruf kecil. Hanya lima kode ini yang diterima (G24 §16.1: + `WTZ`) (`EFN_ONLINE_CHANNEL_CODES` di
  `scripts/lib/deploy-validate.sh`, `ALLOWED_CHANNELS` di `efn-rotate-online-key`, cek `check-encryption.sh`):

  | Kanal (= PT) | User teknis | Secret Docker | Env BFF | Overlay compose |
  |---|---|---|---|---|
  | `GL` (PT Grand Lucky) | `efn_online_gl` | `odoo_api_key_online_gl` | `ODOO_API_KEY_ONLINE_GL_FILE` | (base, kanal default) |
  | `PB` (PT Paris Baguette) | `efn_online_pb` | `odoo_api_key_online_pb` | `ODOO_API_KEY_ONLINE_PB_FILE` | `docker-compose.online-pb.yml` |
  | `BCR` (Bacha Coffee Retail) | `efn_online_bcr` | `odoo_api_key_online_bcr` | `ODOO_API_KEY_ONLINE_BCR_FILE` | `docker-compose.online-bcr.yml` |
  | `BCF` (Bacha Coffee F&B) | `efn_online_bcf` | `odoo_api_key_online_bcf` | `ODOO_API_KEY_ONLINE_BCF_FILE` | `docker-compose.online-bcf.yml` |
  | `WTZ` (PT Wetzel's Pretzels, G24) | `efn_online_wtz` | `odoo_api_key_online_wtz` | `ODOO_API_KEY_ONLINE_WTZ_FILE` | `docker-compose.online-wtz.yml` |

  Kode G17/G19 (`RTL`, `FNB`, `RTL-GL`, `RTL-BACHA`, `FNB-PB`, `FNB-BACHA`) **ditolak** deploy dan skrip rotasi;
  cutover: [§8](#migrasi-g20).

  User teknis hanya `base.group_user` + `group_online_channel`, satu company, tanpa ACL dokumen, dan **terhubung ke
  tepat satu kanal** (`efn.online.channel.user_ids`). Skrip menolak bila user tidak ada/nonaktif/bukan anggota grup
  kanal/punya `base.group_system` atau `base.group_erp_manager` (`user_privileged`)/tidak terhubung ke kanal itu
  (`user_not_in_channel`)/**terhubung juga ke kanal lain** (`user_shared`). Alasan `user_shared`: setelah overlap skrip
  mencabut **semua** key lain milik user teknis; bila user itu juga dipakai kanal lain, key kanal lain ikut mati (dan
  key satu kanal bisa dipakai untuk kanal lain — isolasi per PT hilang). ⚠ DB dev lama dengan data demo G19
  (satu user untuk beberapa kanal) tetap `user_shared` sampai DB dibuat ulang ([dev-reset.md](dev-reset.md)).
- Secret Docker: lihat tabel. Secret kanal selain `GL` hanya di-mount bila kanal ada di `ONLINE_CHANNELS` (deploy.sh
  menyertakan overlay-nya; kanal tanpa overlay → deploy gagal). Sejak G18 secret ini
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
install -o root -g root -m 0644 scripts/host/systemd/efn-rotate-online-key.service \
  scripts/host/systemd/efn-rotate-online-key.timer /etc/systemd/system/
# 2) (opsional) /etc/efn/online-key-rotation.conf root:root 0644 - default: KEY_DAYS=30 ROTATE_BEFORE_DAYS=10
#    OVERLAP_SECONDS=3600 (lihat header skrip; nilai divalidasi, kunci di luar allowlist ditolak)
#    G20: CHANNELS=GL,PB (kanal yang dirotasi timer; hanya GL/PB/BCR/BCF). Kosong -> ONLINE_CHANNELS dari
#    /opt/efn/compose/.env (= kanal yang di-deploy; disarankan, satu sumber kebenaran).
# 3) key pertama per kanal (menggantikan placeholder; kanal Odoo harus AKTIF - verifikasi memanggil katalog kanal)
sudo efn-rotate-online-key --channel=GL              # kanal sudah di ONLINE_CHANNELS (secret di-mount)
sudo efn-rotate-online-key --channel=GL --status     # id, nama, kedaluwarsa (TANPA nilai key)
# 4) SATU timer harian 03:15 WIB untuk semua kanal
systemctl daemon-reload && systemctl enable --now efn-rotate-online-key.timer
# 5) GitHub: hapus secret lama ODOO_API_KEY_ONLINE_<KANAL> dari Environment staging/production (tidak dipakai lagi)
```

**Migrasi dari timer per kanal (G18):** `systemctl disable --now efn-rotate-online-key@RTL.timer` (dan instance
`@<kode lama>` lain) **sebelum**
`enable --now efn-rotate-online-key.timer`. Jangan menjalankan keduanya untuk kanal yang sama: kunci per kanal membuat
salah satunya gagal "sedang berjalan" (alert palsu). Unit `@.service/@.timer` tetap ada untuk kebutuhan khusus.

## 1b. Mengaktifkan kanal PT (PB / BCR / BCF / WTZ) {#kanal-baru}

Urutan (Superadmin + efn-odoo-functional; contoh `PB`). Prinsip: kunci dibuat dan **terbukti bekerja** sebelum kanal
masuk `ONLINE_CHANNELS`, karena deploy dengan `ONLINE_ENABLED=true` menolak kanal yang key-nya masih placeholder.

1. **Odoo:** kanal `PB` lengkap (outlet, pricelist, `payment_timeout_minutes` ≥ 70 bila BFF 3600, `payment_modes`
   §12.2) dan **aktif**. Kanal aktif di Odoo tetapi belum di `ONLINE_CHANNELS` **tidak terlihat** pelanggan (BFF hanya
   menampilkan kanal di `ONLINE_CHANNELS`).
2. **Odoo:** user teknis **khusus** `efn_online_pb` (Internal User + grup kanal online, company PT Paris Baguette saja),
   lalu isi `user_ids` kanal `PB` = user itu saja. Jangan memakai ulang user kanal lain (`user_shared`).
3. **Rilis G20+** berisi overlay `infra/compose/docker-compose.online-pb.yml` + baris `scripts/secrets.list` sudah
   ter-deploy (deploy.sh membuat file placeholder `/opt/efn/secrets/odoo_api_key_online_pb.txt`).
4. **Host:** key pertama dengan overlay sementara (kanal belum di `ONLINE_CHANNELS`):
   ```bash
   sudo efn-rotate-online-key --channel=PB --bootstrap   # hanya bila secret masih 'disabled'
   sudo efn-rotate-online-key --channel=PB --status
   ```
   `--bootstrap` me-recreate BFF dengan overlay kanal itu, memverifikasi key (200) di setiap replika, lalu selesai
   seperti rotasi biasa. Deploy berikutnya kembali memakai daftar file deploy (overlay hilang sampai langkah 5).
5. **Config rilis:** tambah `PB` ke GitHub variable `ONLINE_CHANNELS` (mis. `GL,PB`) → workflow Deploy (deploy.sh
   menyertakan overlay; cek key nyata lolos). Timer `--all` merotasi kanal itu otomatis (CHANNELS kosong → dari
   `.env`; bila `CHANNELS` diisi manual, tambahkan juga di sana).
6. Verifikasi: `sudo efn-rotate-online-key --all --status`; Grafana/Prometheus `efn_odoo_api_key_expiry_timestamp_seconds{channel="PB"}`.

**Kanal WTZ (G24, docs/04 §16):** entitas ke-7 PT Wetzel's Pretzels sudah ada di semua allowlist (tabel di atas); kanal
demo Odoo `WTZ` nonaktif dengan outlet kios `WTZ-01` (FP non-dine-in). Aktivasi = langkah 1–6 di atas dengan `WTZ`
(`sudo efn-rotate-online-key --channel=WTZ --bootstrap`). Hanya ambil di gerai / bayar di kasir / pre-order: **tidak ada**
`QR-WTZ`/`BOOKING-WTZ` (L-92). Platform Postgres wajib sudah menjalankan `13-platform-g24.sql` (CHECK kode PT + WTZ;
deploy.sh mewajibkannya). DB Odoo lama yang di-upgrade ke `efn_setup_company` 19.0.1.8.0 membuat company WTZ + CoA
`l10n_id` + pajak PB1/SC otomatis; data demo kanal/outlet WTZ hanya ada di DB ber-demo (`efn_setup_online` demo).

**PT/kanal baru di luar tabel di atas** (mis. entitas ke-8): butuh perubahan repo dulu — baris `scripts/secrets.list`
(`host:disabled`), secret top-level `infra/compose/docker-compose.yml`, overlay `docker-compose.online-<kode>.yml`
(pola file yang ada), kode di `EFN_ONLINE_CHANNEL_CODES` (`scripts/lib/deploy-validate.sh`) **dan** `ALLOWED_CHANNELS`
(`scripts/host/efn-rotate-online-key`; skrip host dipasang ulang + sha256 di-review), regex `check-encryption.sh` §6.
CI job `infra` memeriksa secrets.list == compose dan least privilege (secret kanal hanya `api-gateway`).

**Menghentikan kanal:** hapus dari `ONLINE_CHANNELS` (dan `CHANNELS` bila diisi) → Deploy. Cabut key-nya di Odoo
(§4 butir 4, ganti login) lalu hapus state host `rm /var/lib/efn/online-key/<kanal_kecil>.*` agar alert
`EfnOnlineApiKeyRotationNotRunning` kanal itu tidak menyala (metrik lama dibaca dari state), lalu
`sudo efn-rotate-online-key --all --status` untuk menulis ulang `efn_odoo_api_key.prom`.

Migrasi dari pemasangan G17 (key manual via `gh secret set`): langkah 3 membuat key baru, memasangnya, dan mencabut
**semua** key lain user teknis setelah overlap (termasuk key manual). File metrik manual lama
`efn_odoo_api_key.prom` ditimpa skrip (satu file untuk semua kanal).

## 2. Cara kerja rotasi otomatis {#rotasi}

**G19/G20 — semua kanal:** `efn-rotate-online-key.timer` (harian, `Persistent=true`) → `efn-rotate-online-key --all`:
daftar kanal dari `CHANNELS` (konfigurasi) atau `ONLINE_CHANNELS` deploy (kosong = `GL`); setiap kode divalidasi
`^[A-Z][A-Z0-9-]{1,15}$` (tanpa `/ . _` spasi → tidak ada path traversal/injeksi nama secret), wajib salah satu
`GL PB BCR BCF` (G20), unik. Setiap kanal
dijalankan sebagai **proses anak terpisah, berurutan** (`--channel=<KANAL> --no-wait`): kegagalan satu kanal tidak
menghentikan kanal lain, kode keluar induk 0 (semua OK) / 1 (≥ 1 kanal gagal) / 3 (≥ 1 ROLLBACK GAGAL). Kanal yang
dirotasi tidak menunggu overlap sendiri; induk menunggu **sekali** sampai overlap terlama lewat, lalu menjalankan
"cabut tertunda" per kanal (langkah 6 di bawah). Kanal yang gagal sebelum sempat menulis metrik (secret tidak di-mount,
konfigurasi) tetap ditandai `rotation_last_run_ok{channel=…} 0` oleh induk → alert per kanal.
Langkah per kanal (`efn-rotate-online-key --channel=<KANAL>`, juga dipakai timer lama `@<KANAL>.timer`):

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

**Koordinasi dengan deploy (SEC-120, L-25h):** langkah 2–6 (dan pencabutan setelah overlap) memegang kunci host
`/run/efn/deploy.lock` yang juga dipegang `efn-deploy`/`deploy.sh` → rotasi dan deploy tidak pernah paralel. Rotasi
menunggu maks `HOST_LOCK_WAIT_SECONDS` (900 s), lalu gagal **tanpa** membuat key (alert, dicoba lagi besok); deploy
menunggu maks 900 s. Overlap 1 jam berjalan **tanpa** kunci. Bila Odoo sudah meng-commit key tetapi keluarannya gagal
diparse / galat setelah commit, key run itu **dicabut per nama** (`efn-online-<KANAL>-<UTC>`, tidak pernah key yang
terpasang) → tidak ada key 30 hari yatim.

Jejak: journald `efn-rotate-online-key` (hanya root, `_UID 0`) → `promtail-journal` → Loki
`{service="host-efn-rotate-online-key"}`: `MULAI`, `ROTASI`, `KEY BARU id= nama= kedaluwarsa=`, `VERIFIKASI OK|GAGAL`,
`ROLLBACK OK|GAGAL`, `DICABUT`, `KEY LAMA DITOLAK`, `SELESAI rc=`. **Nilai key tidak pernah dicetak** (stdout/stderr/
journald/argv); stderr `odoo shell` yang dilog disaring (hex ≥ 32 → `[REDACTED]`).

Rotasi darurat (dugaan kebocoran): `sudo efn-rotate-online-key --channel=<GL|PB|BCR|BCF> --force --overlap-seconds=0`, lalu
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

1. `journalctl -u efn-rotate-online-key --since -2d` (timer `--all`; baris `HASIL --all kanal=<KANAL> ... rc=<n>`)
   atau `journalctl -u efn-rotate-online-key@GL` (timer per kanal), atau Loki `service="host-efn-rotate-online-key"`.
2. Penyebab umum per baris log:
   - `odoo create: ValidationError` → `KEY_DAYS` > `api_key_duration` grup (cek `efn_setup_online` = 35).
   - `odoo list: user_missing|user_inactive|user_not_channel|user_privileged` → perbaiki user teknis (UI/RPC,
     [odoo-access-changes.md](odoo-access-changes.md)).
   - `odoo list: user_shared` → user teknis kanal ini juga terhubung ke kanal lain: buat user khusus per kanal (§1b
     langkah 2). `user_not_in_channel` → `user_ids` kanal belum berisi user `efn_online_<kanal>`.
   - `secret ... tidak didefinisikan compose` / `tidak ada service yang me-mount` → kanal ada di `CHANNELS` tetapi belum
     di `ONLINE_CHANNELS` deploy (overlay tidak di-mount): ikuti §1b atau keluarkan dari `CHANNELS`.
   - `VERIFIKASI GAGAL status=4xx` selain 401 (UserError Odoo, kemungkinan 422 — kode persis belum diverifikasi) →
     kanal Odoo nonaktif (`efn_online:channel_inactive`); aktifkan kanal dulu (§1b langkah 1).
   - `VERIFIKASI GAGAL status=401` dengan key baru → Odoo menolak key (DB lain? `ODOO_DB`); `status=000` → gateway/
     sertifikat/jaringan (`odoo-rpc-gateway` sehat?); `503` → worker online penuh (coba ulang).
   - `tidak sehat` / `timeout menunggu sehat` → `$DC logs api-gateway`.
   - `key lama masih dijawab` → pencabutan belum efektif di worker Odoo: `--status` (key masih terdaftar?), restart
     `odoo` di luar jam ramai, jalankan ulang; bila menetap = temuan keamanan.
   - `--channel=<KODE> (salah satu: GL PB BCR BCF)` / `CHANNELS harus kode kanal unik dari ...` / `ONLINE_CHANNELS
     tidak valid (G20 ...)` → kode lama G17/G19 masih di `/etc/efn/online-key-rotation.conf`, `.env` deploy, atau unit
     `@<kode lama>.timer`: ikuti [§8](#migrasi-g20).
3. Setelah diperbaiki: `sudo efn-rotate-online-key --channel=<KODE> --force`.
4. Key ekstra (`EfnOnlineApiKeyExtraKeys`): run biasa mencabut key lain setelah overlap; key buatan manual di luar
   prosedur = insiden. Cabut manual (break-glass, shell):
   `env['res.users.apikeys'].search([('user_id.login','=','efn_online_gl'),('id','!=',<id terpasang>)]).sudo()._remove()`.

## 5. ROLLBACK GAGAL (critical) {#rollback-gagal}

Key baru gagal **dan** key lama tidak bisa dipasang kembali/diverifikasi → checkout online kemungkinan berhenti.

1. Kill switch bila perlu: `ONLINE_ENABLED=false` (cart/quote/order → 503; order yang sudah dibayar diproses saat
   kanal pulih — saga idempoten).
2. Pulihkan dependensi (BFF/`odoo-rpc-gateway`/Odoo sehat), lalu `sudo efn-rotate-online-key --channel=<KODE> --force`.
3. Key baru yang gagal sudah dicabut skrip (baris `DICABUT ... (key baru gagal)`); bila tidak, cabut lewat §4 butir 4.

## 6. `EfnOnlineOdooAuthFailure` (critical) {#efnonlineodooauthfailure}

Odoo menjawab 401/403 untuk kanal → key kedaluwarsa, dicabut, user teknis dinonaktifkan/berubah grup, atau secret salah.

1. `$DC logs --since 30m odoo-rpc-gateway | tail` — status 401 dengan `why=-` = ditolak **Odoo** (bukan filter gateway).
   Penolakan gateway (`why=auth|header|path|…`) → [online-orders.md](online-orders.md#efnodoorpcgatewaydenied).
2. `sudo efn-rotate-online-key --channel=<KODE> --status` (id/nama/kedaluwarsa; key terpasang ditandai).
3. `sudo efn-rotate-online-key --channel=<KODE> --force`. Selama itu kill switch opsional (§5 butir 1).
4. Bila key dicabut tanpa perubahan terencana → perlakukan sebagai insiden ([security-incident.md](security-incident.md)).

## 7. Dev / uji

- Dev: `scripts/smoke/run-e2e-smoke.sh` langkah setup membuat key dev (skrip smoke) — tidak memakai timer.
- Uji regresi skrip (docker/Odoo/BFF palsu, 90 kasus termasuk G19 `--all`/`--bootstrap`, G20 allowlist kanal
  `GL/PB/BCR/BCF` + penolakan kode lama, dan blok Python `odoo shell` terhadap env Odoo palsu, CI job infra):
  `docker run --rm -v "$PWD:/src:ro" python:3.12-bookworm@sha256:e91fec3d… bash /src/scripts/host/test-efn-rotate-online-key.sh`.
- Uji nyata stack dev (Docker Desktop, 10 Okt 2026): skrip dijalankan di container `docker:27-cli` (socket Docker +
  repo di-mount pada `/run/desktop/mnt/host/e/...` agar path bind compose valid), `/etc/efn/online-key-rotation.conf`
  dengan `COMPOSE_DIR`/`COMPOSE_FILES=docker-compose.yml,docker-compose.dev.yml`/`SECRETS_DIR` repo,
  `SECRET_GROUP=root`, `OVERLAP_SECONDS=20`, env `COMPOSE_PROFILES`. Hasil: `--status` → rotasi (key 1 → key 2, kedua
  verifikasi 200, key 1 dicabut, 401 terbukti) → run kedua "TIDAK PERLU" → metrik lengkap.

## 8. Cutover kode kanal G20 (RTL/FNB/G19 → GL/PB/BCR/BCF) {#migrasi-g20}

Dasar: docs/04 §12.1 (L-48): belum ada produksi, **tidak ada migrasi data** — DB dev/CI dibuat ulang. Key lama tidak
dipindahkan ke nama baru (user teknis lama `efn_online_rtl*`/`efn_online_fnb*` tidak dipakai lagi).

**Repo (sekali, PR G20):** `git rm infra/compose/docker-compose.online-{fnb,rtl-gl,rtl-bacha,fnb-pb,fnb-bacha}.yml`
(isi sudah dinetralkan `services: {}` sebagai pengaman bila belum terhapus).

**Dev (laptop):**
1. `bash scripts/gen-secrets.sh` → membuat `secrets/odoo_api_key_online_{gl,pb,bcr,bcf}.txt` (`disabled`) dan
   `secrets/odoo_pii_name_key.txt` (32 byte acak, G20 E-38a). File lama `secrets/odoo_api_key_online_{rtl,fnb,rtl_gl,
   rtl_bacha,fnb_pb,fnb_bacha}.txt` tidak lagi direferensikan compose → hapus manual (`rm`), isinya key dev saja.
2. Reset DB dev ([dev-reset.md](dev-reset.md)), install modul G20, jalankan `scripts/smoke/run-e2e-smoke.sh o`
   (langkah `setup` membuat kanal `GL` + user `efn_online_gl`), lalu pasang key nyata ke
   `secrets/odoo_api_key_online_gl.txt` (§1 runbook ini) dan `up -d --force-recreate api-gateway`.
3. `.env` lokal: `ONLINE_CHANNELS` lama (`RTL`, `RTL-GL`, …) **harus** diganti/dihapus (default `GL`).

**Staging/host (bila sudah ada):**
1. GitHub variable `ONLINE_CHANNELS` → `GL` (atau daftar kode G20); nilai lama → deploy **gagal** di validasi
   (`ONLINE_CHANNELS: kanal RTL tidak dikenal`), tidak ada fallback diam-diam.
2. `/etc/efn/online-key-rotation.conf`: `CHANNELS=` lama → kode G20 atau kosongkan. Unit per kanal lama:
   `systemctl disable --now 'efn-rotate-online-key@RTL.timer'` (dst.).
3. Deploy G20 (deploy.sh membuat placeholder `odoo_api_key_online_{gl,pb,bcr,bcf}.txt`; file lama di
   `/opt/efn/secrets` tidak lagi di-mount → hapus: `shred -u /opt/efn/secrets/odoo_api_key_online_{rtl,fnb,rtl_gl,rtl_bacha,fnb_pb,fnb_bacha}.txt`).
4. State/metrik kanal lama: `rm /var/lib/efn/online-key/{rtl,fnb,rtl_gl,rtl_bacha,fnb_pb,fnb_bacha}.*` lalu
   `sudo efn-rotate-online-key --all --status` (menulis ulang `efn_odoo_api_key.prom` → alert
   `EfnOnlineApiKeyRotationNotRunning{channel="RTL"}` dkk. padam).
5. Odoo: cabut key user teknis lama (§4 butir 4, login lama) dan nonaktifkan user itu; buat kanal + user G20 (§1b),
   key pertama `sudo efn-rotate-online-key --channel=GL` (atau `--bootstrap` untuk PB/BCR/BCF).
6. Verifikasi: `sudo efn-rotate-online-key --all --status` hanya menampilkan kode G20; Prometheus
   `count by (channel) (efn_odoo_api_key_expiry_timestamp_seconds)` tidak memuat kode lama.

## Batas / belum

- Recreate BFF (2 replika prod) bersamaan → permintaan online bisa 503 beberapa detik pada 03:15 WIB (rolling restart
  belum; compose tidak mendukung tanpa orchestrator).
- G19 `--all`, `--bootstrap`, pemeriksaan `user_shared` belum diuji terhadap Odoo/BFF sungguhan (uji palsu + blok
  Python dijalankan terhadap env Odoo palsu). Uji nyata kanal G20 `PB`/`BCR`/`BCF` butuh data Odoo G20 (kanal + user
  teknis `efn_online_<kode>`, builder Odoo) + kanal diaktifkan + outlet/SKU. Kanal nonaktif tidak bisa diverifikasi
  (lihat butir berikut).
- Verifikasi memanggil katalog kanal → kanal Odoo **nonaktif** tidak bisa diberi key (bukan 200). Pra-provisioning key
  untuk kanal nonaktif belum didukung (sengaja: verifikasi ketat 200).
- Uji nyata rollback (key baru ditolak) hanya di test palsu; jalur sukses sudah diuji nyata di dev.
- Belum diuji di host Linux sungguhan dengan systemd/journald (staging belum ada, ADR-004).
