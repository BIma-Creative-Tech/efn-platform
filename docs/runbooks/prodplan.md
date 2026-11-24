# Runbook: prodplan (production planning CK di atas ESB, G66)

Kontrak: `docs/04_SERVICE_CONTRACTS.md` §30. Keputusan: ADR-051 (**Proposed**). Overlay:
`infra/compose/docker-compose.prodplan.yml` (profil `data`). Risiko: R-S17, R-S23 (usulan), R-O15, R-T23.
Status: tahap 1 (baca + rencana) dan tahap 2 (tulis ke **staging** ESB). Tulis ke ESB **production terkunci di kode**
(`PROD_WRITE_UNLOCKED = False`, L-250), bukan lewat env.

## 1. Ringkasan komponen

| Service | Isi | Network | Port |
|---|---|---|---|
| `prodplan` | `python -m efn_prodplan serve` (API + worker job + worker outbox, replika 1), uid 65534 | `prodplan-db` (.23), `insight-plan` (.24), `egress-prodplan` (.25) | HTTP plaintext **hanya** `127.0.0.1:8080` |
| `prodplan-tls` | ghostunnel server, namespace prodplan, CN klien `insight-web-pp` + `prometheus` | (namespace prodplan) | `:8443` mTLS, status `127.0.0.1:6078` |
| `insight-web-pp-tls` | ghostunnel client, namespace insight-web, verifikasi CN `prodplan` | (namespace insight-web) | `127.0.0.1:7443` -> `prodplan:8443`, status `127.0.0.1:6079` |
| `insight-web` | + network `insight-plan`, env `INSIGHT_PRODPLAN_ENABLED`, `PRODPLAN_URL=http://127.0.0.1:7443`, `PRODPLAN_ASSERT_KEY_FILE` | + `insight-plan` | – |
| `postgres` | + network `prodplan-db`, DB `efn_prodplan`, role `efn_prodplan` | + `prodplan-db` | – |
| `prometheus` | scrape `https://prodplan:8443/metrics` (job `prodplan`) | + `insight-plan` | – |

Tidak ada port host dan tidak ada router Traefik. prodplan tidak menjangkau RabbitMQ, ClickHouse, Odoo, MinIO, Loki.
Satu-satunya jalur keluar = `egress-prodplan` (bridge, `enable_icc=false`); allowlist host ESB + method/path ditegakkan
di kode (§30.4). Allowlist tingkat jaringan (nftables) belum ada (§9).

**pg_hba / `samenet` (L-252):** postgres kini di dua network bila overlay dipakai, sehingga `samenet` mencakup subnet
`prodplan-db` untuk semua baris role. Dibatasi oleh `scripts/check-encryption.sh`: postgres boleh tepat `[data]` atau
`[data, prodplan-db]`, dan anggota `prodplan-db` tepat `{postgres, prodplan}`. Container prodplan hanya memegang
password `efn_prodplan` (SCRAM per role). CIDR literal per baris tidak dipakai karena subnet bergantung pada
`EFN_SUBNET_BASE` (K25-06).

## 2. Secret & konfigurasi

| Secret (`/run/secrets/…`) | Generator | Di-mount ke |
|---|---|---|
| `pg_password_prodplan` | `random` | postgres (00-roles.sh) + prodplan |
| `prodplan_assert_key` | `hex32` | insight-web + prodplan |
| `esb_prodplan_password_pb` | `host:disabled` (operator) | prodplan |
| `tls_prodplan_crt/_key` | `pki` (server) | prodplan-tls |
| `tls_insight_web_pp_crt/_key` | `pki` (client) | insight-web-pp-tls |
| `efn_ca_crt` | `pki` | prodplan, kedua sidecar |

**Folder stack.** Path compose (`../../secrets/*.txt`) relatif terhadap worktree yang menjalankan `docker compose`.
Secret, CA dev, dan `infra/compose/.env` harus milik stack yang sedang berjalan (runbook multi-env-local §2). Bila stack
dev berjalan dari worktree lain dan ingin diuji dari worktree `feat/production-planning`, salin `secrets/` dan
`infra/compose/.env` stack itu ke worktree ini (env yang sama, bukan antar-env), atau jalankan setelah branch digabung
ke worktree stack.

**Generate secret + sertifikat dev** (Git Bash, root worktree; idempoten):

```bash
APP_ENV=dev bash scripts/gen-secrets.sh
```

Membuat `pg_password_prodplan`, `prodplan_assert_key`, `esb_prodplan_password_pb` (`disabled`), `tls_prodplan_*`,
`tls_insight_web_pp_*`. **Peringatan:** dua service PKI baru (`prodplan`, `insight-web-pp`) tidak tercakup
nameConstraints intermediate dev lama, jadi `gen-pki.sh` mengarsipkan `secrets/pki-ca`, membuat CA dev baru, dan
menerbitkan ulang **semua** leaf. Setelah itu seluruh stack wajib `up -d --force-recreate` (runbook internal-pki
§service-baru). Staging/production: intermediate wajib diterbitkan ulang Superadmin (§9).

**Password ESB staging.** Password staging ada di host sebagai `secrets/esb_core_password_stg.txt` (gitignored). Nama
secret kontrak tetap `esb_prodplan_password_pb`; sumber file dialihkan lewat `.env` lokal:

```bash
# infra/compose/.env (gitignored) - path relatif terhadap infra/compose, atau absolut (E:/Projects/.../secrets/...)
PRODPLAN_ESB_PASSWORD_PB_SECRET_FILE=../../secrets/esb_core_password_stg.txt
```

Alternatif setara: `cp secrets/esb_core_password_stg.txt secrets/esb_prodplan_password_pb.txt`. File berisi password
saja, tanpa baris baru di akhir (`printf '%s' '<password>' > file`). Jangan pakai file password production di stack yang
menunjuk staging, dan sebaliknya: staging dan production = file berbeda per host.

**Username ESB (SEC-180).** Tidak pernah di repo. Isi hanya di `infra/compose/.env` lokal:

```bash
PRODPLAN_ESB_USER_PB=<username API ESB staging>
```

**Flag minimal dev dengan staging ESB** (`infra/compose/.env`):

```bash
PRODPLAN_ENABLED=true
PRODPLAN_WRITE_ENABLED=false          # tahap 1. Tahap 2 (tulis ke STAGING): true, hanya setelah uji tahap 1 lolos
PRODPLAN_ESB_BASE_URL=https://stg7.esb.co.id/core-stg
INSIGHT_PRODPLAN_ENABLED=true
```

Variabel lain (§30.3) beserta default ada di `.env.example` bagian G66.

## 3. Urutan boot (dev lokal, ESB staging)

Git Bash: perintah `exec` dengan path container `/docker-entrypoint-initdb.d/...` butuh `MSYS_NO_PATHCONV=1` agar path
tidak diubah menjadi path Windows.

```bash
cd infra/compose
F=(-f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.prodplan.yml)
P=(--profile core --profile data)          # + --profile observability bila Prometheus ikut

# 0) Validasi render (wajib bersih)
docker compose "${F[@]}" "${P[@]}" config -q

# 1) PostgreSQL ikut network prodplan-db + secret role (container di-recreate; koneksi service lain putus sebentar)
docker compose "${F[@]}" "${P[@]}" up -d postgres

# 2) Role efn_prodplan (password dari secret) lalu DB/skema/grant (idempoten; volume baru menjalankannya otomatis)
MSYS_NO_PATHCONV=1 docker compose "${F[@]}" exec -T postgres bash /docker-entrypoint-initdb.d/00-roles.sh
MSYS_NO_PATHCONV=1 docker compose "${F[@]}" exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -f /docker-entrypoint-initdb.d/23-prodplan-g66.sql

# 3) Build + jalankan prodplan dan sidecar; insight-web di-recreate (network/env/secret baru) bersama kedua sidecar-nya
docker compose "${F[@]}" "${P[@]}" build prodplan
docker compose "${F[@]}" "${P[@]}" up -d prodplan prodplan-tls
docker compose "${F[@]}" "${P[@]}" up -d --force-recreate insight-web insight-web-tls insight-web-pp-tls

# 4) (opsional) Prometheus ikut insight-plan
docker compose "${F[@]}" "${P[@]}" --profile observability up -d prometheus
```

Bila langkah `gen-secrets.sh` membuat CA dev baru (§2), ganti langkah 1–4 dengan `up -d --force-recreate` seluruh stack
memakai `F` di atas.

## 4. Verifikasi

```bash
# Semua healthy
docker compose "${F[@]}" "${P[@]}" ps prodplan prodplan-tls insight-web insight-web-pp-tls

# Jalur insight-web -> sidecar klien -> mTLS -> prodplan: /healthz 200, /readyz 200 (= koneksi PG verify-full OK)
docker compose "${F[@]}" exec insight-web node -e \
  "Promise.all(['/healthz','/readyz'].map(p=>fetch('http://127.0.0.1:7443'+p).then(r=>p+' '+r.status))).then(console.log)"

# Negatif: tanpa sertifikat klien ditolak (harus error TLS / bukan 200)
docker compose "${F[@]}" exec insight-web node -e \
  "fetch('https://prodplan:8443/healthz').then(r=>console.log('TIDAK BOLEH', r.status)).catch(e=>console.log('ditolak OK', e.cause?.code||e.message))"

# Grant DB sesuai §30.6 (efn_prodplan: DML; audit_event hanya SELECT/INSERT)
MSYS_NO_PATHCONV=1 docker compose "${F[@]}" exec -T postgres psql -U postgres -d efn_prodplan -c '\dp prodplan.*'

# Log: login ESB staging berhasil / gagal (tidak boleh memuat password, token, username, query string)
docker compose "${F[@]}" logs --tail=100 prodplan
```

Gerbang konfigurasi (dari root worktree). `check-encryption.sh` merender juga varian **prod** yang membaca
`infra/compose/.env`; selama `.env` lokal berisi flag `true` untuk uji dev, paksa default lewat env shell:

```bash
PRODPLAN_ENABLED=false PRODPLAN_WRITE_ENABLED=false INSIGHT_PRODPLAN_ENABLED=false bash scripts/check-encryption.sh
```

## 5. Mematikan

- **Idle (disarankan):** `PRODPLAN_ENABLED=false` (dan `INSIGHT_PRODPLAN_ENABLED=false`) di `.env`, lalu
  `docker compose "${F[@]}" "${P[@]}" up -d prodplan insight-web` (+ `--force-recreate insight-web-tls insight-web-pp-tls`).
  `/v1/*` 503 `disabled`, tanpa panggilan ESB/PG selain readiness. Outbox `pending` tidak dikirim.
- **Hentikan tulis saja:** `PRODPLAN_WRITE_ENABLED=false` + `up -d prodplan`. Rekonsiliasi GET tetap jalan.
- **Lepas overlay sepenuhnya:**
  ```bash
  docker compose "${F[@]}" "${P[@]}" rm -sf prodplan prodplan-tls insight-web-pp-tls
  F0=(-f docker-compose.yml -f docker-compose.dev.yml)
  docker compose "${F0[@]}" "${P[@]}" up -d --force-recreate postgres insight-web insight-web-tls
  MSYS_NO_PATHCONV=1 docker compose "${F0[@]}" exec -T postgres psql -U postgres -c 'ALTER ROLE efn_prodplan NOLOGIN'
  ```
  DB `efn_prodplan` tetap (retensi §30.10); hapus hanya dengan keputusan pemilik data. Dokumen yang sudah terkirim ke
  ESB tidak ikut terhapus.

## 6. Rotasi secret

| Secret | Langkah | Dampak |
|---|---|---|
| `pg_password_prodplan` | tulis nilai baru (`openssl rand -base64 36`) -> `00-roles.sh` (ALTER ROLE) -> `up -d --force-recreate prodplan prodplan-tls` | prodplan reconnect |
| `prodplan_assert_key` | `openssl rand -hex 32 > secrets/prodplan_assert_key.txt` -> recreate **insight-web (+2 sidecar) dan prodplan bersamaan** | asersi `ppa1` yang sedang terbang (≤ 60 dtk) ditolak 401 |
| `esb_prodplan_password_pb` | ganti password di ESB -> tulis file (atau file yang ditunjuk `PRODPLAN_ESB_PASSWORD_PB_SECRET_FILE`) -> `up -d --force-recreate prodplan prodplan-tls` | job berjalan gagal `esb_auth_failed` sampai restart |
| `tls_prodplan_*`, `tls_insight_web_pp_*` | otomatis < 30 hari sisa (`gen-pki.sh`); manual: `bash scripts/gen-pki.sh prodplan insight-web-pp` -> recreate sidecar terkait | putus sesaat |

Akun admin ESB dipakai sampai Q-PP1 (akun non-admin per brand). Saat akun baru tersedia: isi `PRODPLAN_ESB_USER_PB` +
file password baru; `esb_core_password_pb` (esb-extract) tidak disentuh.

## 7. Alert

Service bukan jalur transaksi: semua alert `warning`, jam kerja. Job `prodplan` dikecualikan dari `EfnServiceDown`.

### EfnProdplanDown

`up{job="prodplan"} == 0` selama 15 menit (target hanya ada bila overlay dipakai).
1. `docker compose ... ps prodplan prodplan-tls` dan `logs --tail=200 prodplan prodplan-tls`.
2. Exit 2 di log = konfigurasi ditolak (§8). Restart loop karena PG = cek §8 baris PG.
3. Scrape gagal tetapi service sehat = mTLS: `prometheus` hilang dari `--allow-cn`, CA berganti tanpa recreate, atau
   Prometheus tidak di network `insight-plan`.

### EfnProdplanOutboxStuck

Ada baris outbox `unknown`/`failed` > 30 menit. Dokumen mungkin **sudah** tercatat di ESB.
1. Lihat baris (read-only):
   ```sql
   SELECT id, plan_id, seq, doc_kind, status, marker, esb_doc_number, attempts, last_http_status, last_error_code, updated_at
     FROM prodplan.outbox WHERE status IN ('unknown','failed') ORDER BY updated_at;
   ```
2. `unknown`: tunggu rekonsiliasi (worker mencari marker `pp:…` di ESB, grace `PRODPLAN_RECONCILE_GRACE_SECONDS`). Bila
   ESB staging tidak bisa dijangkau, perbaiki konektivitas dulu.
3. `failed` dengan `not_found_after_timeout`: cek manual di UI ESB bahwa dokumen dengan marker itu memang tidak ada,
   baru tekan **Kirim ulang** di halaman Posting (`POST /v1/outbox/{id}/resend`, marker sama).
4. `failed` validasi 4xx: perbaiki penyebab (lokasi/produk/BOM), batalkan rencana, buat ulang. **Jangan** UPDATE baris
   outbox lewat SQL dan jangan membuat dokumen pengganti manual di ESB sebelum langkah 1–3 (dokumen ganda = stok
   bergerak dua kali).

### EfnProdplanDeleteFailed

ESB menolak DELETE saat rencana dibatalkan (goods delivery sudah ada / stok hasil sudah terpakai). Tidak diulang
otomatis. Admin gudang/produksi menangani dokumen di ESB (nomor di `esb_doc_number`), lalu catat tindakannya di tiket.
Baris tetap `delete_failed` sebagai jejak audit.

## 8. Troubleshooting

| Gejala | Penyebab umum | Tindakan |
|---|---|---|
| prodplan exit 2, log `prodplan_config_error` | `PRODPLAN_ESB_BASE_URL` di luar dua URL; brand aktif tanpa `PRODPLAN_ESB_USER_<BRAND>`; `PG_SSLMODE` bukan verify-full; `reason=prod_write_locked` (write=true + base production) | Perbaiki `.env`. `prod_write_locked` = perilaku yang disengaja (L-250), bukan bug |
| `/readyz` 503, log PG `password authentication failed` | `00-roles.sh` belum dijalankan setelah overlay dipasang (role masih NOLOGIN) | §3 langkah 2 |
| PG `permission denied` / `relation does not exist` | `23-prodplan-g66.sql` belum dijalankan | §3 langkah 2 |
| PG `no pg_hba.conf entry` | postgres belum di-recreate dengan overlay (tidak di `prodplan-db`) atau `pg_hba.conf` lama | `up -d --force-recreate postgres` |
| insight-web: prodplan tidak terjangkau, sidecar log `bad certificate` / `unknown authority` | CA dev dibuat ulang tetapi container lama masih memegang bundle lama; CN salah | `up -d --force-recreate` stack / sidecar |
| Login ESB gagal `esb_auth_failed` | file password masih `disabled`, ada baris baru di akhir, user/password staging vs production tertukar | §2 |
| Halaman `/produksi` ke `/tidak-berwenang` | `INSIGHT_PRODPLAN_ENABLED` bukan `true`, atau sesi tanpa grup Odoo `group_prodplan_planner`/`_warehouse` | aktifkan flag; login ulang Insight setelah grup diberikan (sesi 2 jam) |
| DNS/egress ke `stg7.esb.co.id` gagal | Docker Desktop tanpa internet / proxy korporat | uji `docker compose exec prodplan python -c "import socket;print(socket.gethostbyname('stg7.esb.co.id'))"` |

## 9. Rantai rilis & tindak lanjut (belum dibangun)

- Image `prodplan` di-build, dipindai Trivy, dan ditandatangani `build-push.yml`, tetapi **belum** masuk rantai rilis:
  `IMAGE_PRODPLAN` belum ada di `deploy.yml`/`efn-deploy`/`deploy.sh`, dan `deploy.sh` tidak memasang overlay ini
  (pola esb-extract G41). K25-06: tidak ada deploy ke VPS saat ini.
- Deploy VPS berikutnya (kapan pun) butuh GitHub Environment `PG_PASSWORD_PRODPLAN` dan `PRODPLAN_ASSERT_KEY`
  (generator `random`/`hex32`; deploy.sh menolak nilai kosong), meskipun overlay tidak dipasang.
- Intermediate staging/production wajib diterbitkan ulang (nameConstraints memuat `prodplan`, `insight-web-pp`) sebelum
  deploy pertama yang memuat G66; tanpa itu `deploy.sh` berhenti di langkah PKI untuk semua service.
- Allowlist egress tingkat jaringan (nftables subnet `<base>.25.0/24` -> IP ESB:443) belum ada; saat ini hanya di kode.
- `docs/runbooks/internal-pki.md` belum punya bagian "Gelombang 66" (isi sama dengan §2 di atas).

## 10. Daftar periksa sebelum tulis ke ESB production (tetap terkunci di kode)

Semua wajib tercentang **sebelum** PR terpisah yang mengubah `PROD_WRITE_UNLOCKED` (dengan review security):

- [ ] ADR-051 berstatus **Accepted**; Q-PP2 (penyimpangan §3.4 diterima pemilik proyek) tertulis.
- [ ] Q-PP1: akun API ESB **non-admin** per brand (baca + buat/hapus transfer & produksi, tanpa authorize) tersedia;
      `esb_prodplan_password_<brand>` production berisi akun itu (bukan admin, bukan sama dengan esb-extract).
- [ ] Q-PP3: mode costing production dikonfirmasi (`PRODPLAN_ESB_MFG_MODE`); mode `standard` sudah diuji di staging.
- [ ] Uji E2E tahap 2 di staging lolos: transfer -> authorize manusia -> produksi -> pembatalan (DELETE), termasuk
      skenario timeout setelah kirim (`unknown` -> rekonsiliasi tanpa POST kedua).
- [ ] Rantai rilis (§9) selesai: `IMAGE_PRODPLAN` bertanda tangan, overlay dipasang deploy.sh, flag di deploy-validate
      (`EFN_ALLOW_FEATURE_ON` Superadmin untuk `PRODPLAN_WRITE_ENABLED`).
- [ ] Allowlist egress nftables aktif; alert `efn-prodplan` terbukti terkirim (uji Alertmanager).
- [ ] Entri `docs/compliance/PDP_DATA_INVENTORY.md` (aktor = uid + nama staf, retensi 5 tahun outbox/audit, Q-PP10).
- [ ] Restore test menyertakan DB `efn_prodplan` (otomatis: `scripts/restore-test.sh` membandingkan semua DB hidup).
- [ ] SoD: grup `pp_planner` dan `pp_warehouse` dipegang orang berbeda untuk CK yang ditulis (Q-PP7).
