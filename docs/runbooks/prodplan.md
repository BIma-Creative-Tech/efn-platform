# Runbook: prodplan (production planning CK di atas ESB, G66)

Kontrak: `docs/04_SERVICE_CONTRACTS.md` §30, **§30.17 (amandemen A1: login per pengguna ESB) menang** atas §30.2–§30.12
dan §30.16. Keputusan: ADR-051 (**Proposed**) §8. Overlay: `infra/compose/docker-compose.prodplan.yml` (profil `data`) +
opsional `infra/compose/docker-compose.prodplan-odoo.yml` (provisioning identitas ke Odoo, §11). Risiko: R-S17, R-S24,
R-S25, R-S26, R-O15, R-O16, R-T23, R-T24.
Status: tahap 1 (baca + rencana) dan tahap 2 (tulis ke **staging** ESB). Tulis ke ESB **production terkunci di kode**
(`PROD_WRITE_UNLOCKED = False`, L-250), bukan lewat env (§30.17.12).

## 1. Ringkasan komponen

| Service | Isi | Network | Port |
|---|---|---|---|
| `prodplan` | `python -m efn_prodplan serve` (API + worker job + worker outbox + sesi ESB di memori, replika 1), uid 65534, `ulimits core=0` | `prodplan-db` (.23), `insight-plan` (.24), `egress-prodplan` (.25); + `prodplan-rpc` (.26) bila overlay `prodplan-odoo` | HTTP plaintext **hanya** `127.0.0.1:8080` |
| `prodplan-tls` | ghostunnel server, namespace prodplan, CN klien `insight-web-pp` + `prometheus` | (namespace prodplan) | `:8443` mTLS, status `127.0.0.1:6078` |
| `insight-web-pp-tls` | ghostunnel client, namespace insight-web, verifikasi CN `prodplan` | (namespace insight-web) | `127.0.0.1:7443` -> `prodplan:8443`, status `127.0.0.1:6079` |
| `insight-web` | + network `insight-plan`, env `INSIGHT_PRODPLAN_ENABLED`, `PRODPLAN_URL=http://127.0.0.1:7443`, `PRODPLAN_ASSERT_KEY_FILE` (asersi `ppa2`) | + `insight-plan` | – |
| `postgres` | + network `prodplan-db`, DB `efn_prodplan`, role `efn_prodplan` | + `prodplan-db` | – |
| `prometheus` | scrape `https://prodplan:8443/metrics` (job `prodplan`) | + `insight-plan` | – |
| `odoo-rpc-gateway` | (hanya overlay `prodplan-odoo`) + network `prodplan-rpc`; CN `prodplan-odoo` hanya satu path | + `prodplan-rpc` | – |

Tidak ada port host dan tidak ada router Traefik. prodplan tidak menjangkau RabbitMQ, ClickHouse, Odoo langsung, MinIO,
Loki. Jalur keluar internet = `egress-prodplan` (bridge, `enable_icc=false`); allowlist host ESB + method/path ditegakkan
di kode (§30.4, + `POST /auth/logout`, `GET /user/access` §30.17.3). Allowlist tingkat jaringan (nftables) belum ada (§9).

**pg_hba / `samenet` (L-252):** postgres kini di dua network bila overlay dipakai, sehingga `samenet` mencakup subnet
`prodplan-db` untuk semua baris role. Dibatasi oleh `scripts/check-encryption.sh`: postgres boleh tepat `[data]` atau
`[data, prodplan-db]`, dan anggota `prodplan-db` tepat `{postgres, prodplan}`. Container prodplan hanya memegang
password `efn_prodplan` (SCRAM per role). CIDR literal per baris tidak dipakai karena subnet bergantung pada
`EFN_SUBNET_BASE` (K25-06).

### 1.1 Alur login per pengguna ESB (§30.17.1)

1. Staf membuka `https://insight.<domain>/produksi` (lewat WireGuard) -> tanpa cookie `__Host-epp` dialihkan ke
   `/produksi/masuk`. Halaman ini **tidak** memakai sesi Odoo Insight.
2. Staf mengetik **username + password ESB miliknya sendiri**. insight-web -> prodplan `POST /v1/session` -> ESB
   `POST /auth/login` satu kali, lalu `GET /user/access` + `GET /branch/list/user` dengan token orang itu.
3. Peran (`pp_planner` / `pp_warehouse`) dan cabang dihitung dari izin ESB (env `PRODPLAN_ESB_ROLE_*`,
   `PRODPLAN_ESB_DOC_ACCESS`, §30.17.6). Tanpa izin PP -> "Akun ESB ini tidak memiliki izin modul Produksi."
4. Token ESB (maks **1 jam**, tanpa refresh) disimpan **hanya di memori proses prodplan**; browser memegang cookie buram
   `__Host-epp`. Password tidak pernah ditulis ke log, PG, Odoo, cookie, atau metrik.
5. Setelah 1 jam (atau **Keluar**) staf login lagi. Posting yang sedang berjalan berhenti aman di `needs_login`
   (§7 `EfnProdplanNeedsLogin`), dilanjutkan dengan tombol **Lanjutkan posting** setelah login.

Batas (konstanta kode, bukan env): 10 percobaan/15 menit per IP (insight-web); 5 gagal/15 menit per username -> terkunci
15 menit (prodplan); 3 sesi per aktor, 200 sesi total.

**Restart prodplan = SEMUA pengguna login ulang** (L-260): `up -d --force-recreate prodplan`, rotasi secret, deploy,
atau OOM menghapus semua sesi. Umumkan ke planner/kepala gudang sebelum restart terencana; outbox yang belum selesai
berhenti di `needs_login` (aman, tanpa POST ganda) sampai pemicunya login lagi.

**SoD butuh dua akun ESB berbeda** (planner vs kepala gudang). Dengan satu akun admin staging, alur approve belum bisa
diuji (Q-PP17).

## 2. Secret & konfigurasi

| Secret (`/run/secrets/…`) | Generator | Di-mount ke |
|---|---|---|
| `pg_password_prodplan` | `random` | postgres (00-roles.sh) + prodplan |
| `prodplan_assert_key` | `hex32` | insight-web + prodplan (asersi `ppa2`) |
| `tls_prodplan_crt/_key` | `pki` (server) | prodplan-tls |
| `tls_insight_web_pp_crt/_key` | `pki` (client) | insight-web-pp-tls |
| `efn_ca_crt` | `pki` | prodplan, kedua sidecar |
| `odoo_api_key_esb_provisioner` | `host:disabled` (Superadmin, §11.3) | prodplan, **hanya** overlay `prodplan-odoo` |
| `tls_prodplan_odoo_crt/_key` | `pki` (client, CN `prodplan-odoo`) | prodplan, **hanya** overlay `prodplan-odoo` |

Tidak ada lagi password/username ESB di disk (§30.17.10). Env baru (non-rahasia, default di `.env.example` bagian G66):

| Env | Default | Arti |
|---|---|---|
| `PRODPLAN_ESB_COMPANY_CODES` | kosong (wajib diisi di `.env` lokal) | `pb=<companyCode ESB>`; staging ≠ production. Kosong + brand aktif = exit 2 |
| `PRODPLAN_ESB_ROLE_PLANNER` | `B.4:view+insert;K.5:view;D.16:view;C.1:view` | izin ESB -> `pp_planner` |
| `PRODPLAN_ESB_ROLE_WAREHOUSE` | `D.9:view+insert;D.4:view+insert;D.16:view` | izin ESB -> `pp_warehouse` |
| `PRODPLAN_ESB_DOC_ACCESS` | `simple_transfer=D.9;goods_transfer_request=D.4;simple_manufacturing=B.4` | kode akses per jenis dokumen (insert/delete) |
| `PRODPLAN_ODOO_PROVISION_ENABLED` | `false` | §11; tanpa overlay `prodplan-odoo` dipaku `false` |
| `PRODPLAN_ODOO_RPC_URL` | `https://odoo-rpc-gateway:8443` (tetap, di overlay) | nilai lain = exit 2 |

Dihapus: `PRODPLAN_ESB_USER_<BRAND>`, `PRODPLAN_ESB_PASSWORD_<BRAND>_FILE`, `PRODPLAN_ESB_PASSWORD_PB_SECRET_FILE`,
`PRODPLAN_SOURCE_LOCATIONS` (cabang sumber transfer kini dari `/branch/list/user` pengguna). `check-encryption.sh`
gagal bila env/secret itu muncul lagi di render compose.

**Folder stack.** Path compose (`../../secrets/*.txt`) relatif terhadap worktree yang menjalankan `docker compose`.
Secret, CA dev, dan `infra/compose/.env` harus milik stack yang sedang berjalan (runbook multi-env-local §2).

**Generate secret + sertifikat dev** (Git Bash, root worktree; idempoten):

```bash
APP_ENV=dev bash scripts/gen-secrets.sh
```

Membuat `pg_password_prodplan`, `prodplan_assert_key`, `odoo_api_key_esb_provisioner` (`disabled`), `tls_prodplan_*`,
`tls_insight_web_pp_*`, `tls_prodplan_odoo_*`. **Peringatan:** service PKI baru (`prodplan-odoo`) tidak tercakup
nameConstraints intermediate dev lama, jadi `gen-pki.sh` mengarsipkan `secrets/pki-ca`, membuat CA dev baru, dan
menerbitkan ulang **semua** leaf. Setelah itu seluruh stack wajib `up -d --force-recreate` (runbook internal-pki
§service-baru). Staging/production: intermediate wajib diterbitkan ulang Superadmin (§9).

**Flag minimal dev dengan staging ESB** (`infra/compose/.env`):

```bash
PRODPLAN_ENABLED=true
PRODPLAN_WRITE_ENABLED=false          # tahap 1. Tahap 2 (tulis ke STAGING): true, hanya setelah uji tahap 1 lolos
PRODPLAN_ESB_BASE_URL=https://stg7.esb.co.id/core-stg
PRODPLAN_ESB_COMPANY_CODES=pb=<companyCode PB di ESB staging>
INSIGHT_PRODPLAN_ENABLED=true
```

### 2.1 Penghapusan secret akun service lama (§30.17.10)

File `secrets/esb_prodplan_password_pb.txt` (dan file yang dulu ditunjuk `PRODPLAN_ESB_PASSWORD_PB_SECRET_FILE`, mis.
salinan password staging) **tidak dipakai lagi**. Isinya sama dengan password admin `esb_core_password_pb` milik
esb-extract (R-S17). `gen-secrets.sh` hanya memberi PERINGATAN; penghapusan dilakukan operator, **setelah** §12 langkah 1
(pembatalan rencana memakai akun lama) selesai, per environment (dev, stg, gladi efn-prod):

```bash
# root worktree stack itu. Jangan menampilkan isi file.
[ -f secrets/esb_prodplan_password_pb.txt ] && shred -u secrets/esb_prodplan_password_pb.txt
# .env lokal: hapus baris PRODPLAN_ESB_USER_*, PRODPLAN_ESB_PASSWORD_PB_SECRET_FILE, PRODPLAN_SOURCE_LOCATIONS
sed -i -E '/^(PRODPLAN_ESB_USER_[A-Z0-9]+|PRODPLAN_ESB_PASSWORD_[A-Z0-9_]+|PRODPLAN_SOURCE_LOCATIONS)=/d' infra/compose/.env
```

**Jangan** menghapus `secrets/esb_core_password_*.txt` (masih dipakai esb-extract). Rotasi password admin ESB = keputusan
IT klien (dicatat, bukan syarat rilis).

## 3. Urutan boot (dev lokal, ESB staging)

Git Bash: perintah `exec` dengan path container `/docker-entrypoint-initdb.d/...` butuh `MSYS_NO_PATHCONV=1` agar path
tidak diubah menjadi path Windows.

```bash
cd infra/compose
F=(-f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.prodplan.yml)
# + -f docker-compose.prodplan-odoo.yml bila provisioning Odoo ikut (§11)
P=(--profile core --profile data)          # + --profile observability bila Prometheus ikut

# 0) Validasi render (wajib bersih)
docker compose "${F[@]}" "${P[@]}" config -q

# 1) PostgreSQL ikut network prodplan-db + secret role (container di-recreate; koneksi service lain putus sebentar)
docker compose "${F[@]}" "${P[@]}" up -d postgres

# 2) Role efn_prodplan (password dari secret) lalu DB/skema/grant (idempoten; volume baru menjalankan semua file init
#    otomatis). 24-* (A1) pada DB yang sudah berisi rencana: ikuti §12 (gagal dengan sengaja bila masih ada yang terbuka).
MSYS_NO_PATHCONV=1 docker compose "${F[@]}" exec -T postgres bash /docker-entrypoint-initdb.d/00-roles.sh
MSYS_NO_PATHCONV=1 docker compose "${F[@]}" exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -f /docker-entrypoint-initdb.d/23-prodplan-g66.sql
MSYS_NO_PATHCONV=1 docker compose "${F[@]}" exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -f /docker-entrypoint-initdb.d/24-prodplan-esb-login.sql

# 3) Build + jalankan prodplan dan sidecar; insight-web di-recreate (network/env/secret baru) bersama kedua sidecar-nya.
#    prodplan + insight-web WAJIB versi A1 bersamaan (ppa1 -> ppa2 tidak kompatibel, §30.17.16).
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

# Tanpa core dump (§30.17.4): harus "0"
docker compose "${F[@]}" exec prodplan sh -c 'ulimit -c'

# Grant DB sesuai §30.6 (efn_prodplan: DML; audit_event hanya SELECT/INSERT) + tabel aktor A1
MSYS_NO_PATHCONV=1 docker compose "${F[@]}" exec -T postgres psql -U postgres -d efn_prodplan -c '\dp prodplan.*'

# Login: buka /produksi/masuk, masuk dengan akun ESB staging. Log hanya boleh memuat event pp_login dengan user_mask
# (mis. "ad***n"), TIDAK boleh memuat password, token, sid, cookie, username utuh, atau nama lengkap.
docker compose "${F[@]}" logs --tail=100 prodplan | grep -E 'pp_login|pp_logout|pp_session_evicted'
```

Gerbang konfigurasi (dari root worktree). `check-encryption.sh` merender juga varian **prod** yang membaca
`infra/compose/.env`; selama `.env` lokal berisi flag `true` untuk uji dev, paksa default lewat env shell:

```bash
PRODPLAN_ENABLED=false PRODPLAN_WRITE_ENABLED=false INSIGHT_PRODPLAN_ENABLED=false PRODPLAN_ODOO_PROVISION_ENABLED=false \
  bash scripts/check-encryption.sh
```

## 5. Mematikan

- **Idle (disarankan):** `PRODPLAN_ENABLED=false` (dan `INSIGHT_PRODPLAN_ENABLED=false`) di `.env`, lalu
  `docker compose "${F[@]}" "${P[@]}" up -d prodplan insight-web` (+ `--force-recreate insight-web-tls insight-web-pp-tls`).
  `/v1/*` 503 `disabled`, tanpa panggilan ESB/PG selain readiness. Outbox `pending` tidak dikirim. Semua sesi hilang.
- **Hentikan tulis saja:** `PRODPLAN_WRITE_ENABLED=false` + `up -d prodplan`. Rekonsiliasi GET tetap jalan (dengan token
  sesi hidup aktor pemicu). Semua pengguna login ulang (restart).
- **Matikan provisioning Odoo saja:** §11.5.
- **Lepas overlay sepenuhnya:**
  ```bash
  docker compose "${F[@]}" "${P[@]}" rm -sf prodplan prodplan-tls insight-web-pp-tls
  F0=(-f docker-compose.yml -f docker-compose.dev.yml)
  docker compose "${F0[@]}" "${P[@]}" up -d --force-recreate postgres insight-web insight-web-tls odoo-rpc-gateway
  MSYS_NO_PATHCONV=1 docker compose "${F0[@]}" exec -T postgres psql -U postgres -c 'ALTER ROLE efn_prodplan NOLOGIN'
  ```
  DB `efn_prodplan` tetap (retensi §30.10/§30.17.13); hapus hanya dengan keputusan pemilik data. Dokumen yang sudah
  terkirim ke ESB tidak ikut terhapus.

## 6. Rotasi secret

| Secret | Langkah | Dampak |
|---|---|---|
| `pg_password_prodplan` | tulis nilai baru (`openssl rand -base64 36`) -> `00-roles.sh` (ALTER ROLE) -> `up -d --force-recreate prodplan prodplan-tls` | prodplan reconnect; **semua pengguna login ulang** |
| `prodplan_assert_key` | `openssl rand -hex 32 > secrets/prodplan_assert_key.txt` -> recreate **insight-web (+2 sidecar) dan prodplan bersamaan** | asersi `ppa2` yang sedang terbang (≤ 60 dtk) ditolak 401; semua pengguna login ulang |
| `odoo_api_key_esb_provisioner` | §11.3 langkah 2–3 (key baru) -> `up -d --force-recreate prodplan prodplan-tls` -> hapus key lama di Odoo setelah `prodplan_odoo_provision_total{result="ok"}` naik | provisioning gagal sampai restart; semua pengguna login ulang. Frekuensi **30 hari** |
| `tls_prodplan_*`, `tls_insight_web_pp_*`, `tls_prodplan_odoo_*` | otomatis < 30 hari sisa (`gen-pki.sh`); manual: `bash scripts/gen-pki.sh prodplan insight-web-pp prodplan-odoo` -> recreate sidecar / prodplan | putus sesaat (prodplan: login ulang) |

## 7. Alert

Service bukan jalur transaksi: alert `warning`/`info`, jam kerja. Job `prodplan` dikecualikan dari `EfnServiceDown`.

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
   SELECT id, plan_id, seq, doc_kind, status, marker, esb_doc_number, attempts, last_http_status, last_error_code,
          run_as_sub, updated_at
     FROM prodplan.outbox WHERE status IN ('unknown','failed') ORDER BY updated_at;
   ```
2. `unknown`: tunggu rekonsiliasi (worker mencari marker `pp:…` di ESB, grace `PRODPLAN_RECONCILE_GRACE_SECONDS`) —
   **butuh sesi hidup aktor `run_as`**; bila `last_error_code='needs_login'`, aktor itu harus login lagi (§30.17.9).
3. `failed` dengan `not_found_after_timeout`: cek manual di UI ESB bahwa dokumen dengan marker itu memang tidak ada,
   baru aktor `run_as` menekan **Kirim ulang** di halaman Posting (`POST /v1/outbox/{id}/resend`, marker sama).
4. `failed` validasi 4xx: perbaiki penyebab (lokasi/produk/BOM), batalkan rencana, buat ulang. **Jangan** UPDATE baris
   outbox lewat SQL dan jangan membuat dokumen pengganti manual di ESB sebelum langkah 1–3 (dokumen ganda = stok
   bergerak dua kali).

### EfnProdplanDeleteFailed

ESB menolak DELETE saat rencana dibatalkan (goods delivery sudah ada / stok hasil sudah terpakai). Tidak diulang
otomatis. Admin gudang/produksi menangani dokumen di ESB (nomor di `esb_doc_number`), lalu catat tindakannya di tiket.
Baris tetap `delete_failed` sebagai jejak audit.

### EfnProdplanLoginFailures

`invalid_credentials` + `login_locked` naik > 20 dalam 15 menit. Indikasi tebak password akun ESB lewat
`/produksi/masuk` (R-S24), atau satu pengguna berulang memakai password lama.
1. Log (tanpa password/username utuh): `docker compose ... logs --since=30m prodplan | grep pp_login` — lihat sebaran
   `user_mask` dan `result`. Satu `user_mask` = kemungkinan salah password/kunci; banyak `user_mask` = serangan.
2. Halaman hanya lewat WireGuard: identifikasi peer WireGuard aktif di jendela waktu itu (log Traefik `clientIp`).
3. Serangan: matikan sementara `INSIGHT_PRODPLAN_ENABLED=false` (halaman tertutup, `/v1/session` tidak terjangkau),
   cabut peer WireGuard terkait, beri tahu IT klien (akun ESB bisa dikunci kebijakan ESB sendiri, Q-PP18).
4. Lockout aplikasi per username (5 gagal -> 15 menit) bisa dipakai mengunci orang lain (diterima, §30.17.5); tunggu 15
   menit atau restart prodplan (menghapus penghitung **dan semua sesi**).

### EfnProdplanNeedsLogin

Ada baris outbox `needs_login` > 30 menit: sesi ESB aktor pemicu berakhir (maks 1 jam) atau prodplan di-restart; dokumen
sisanya belum dikirim (R-O16).
1. Siapa yang harus login (read-only):
   ```sql
   SELECT o.plan_id, o.seq, o.doc_kind, o.status, o.attempts, a.display_name AS run_as, o.updated_at
     FROM prodplan.outbox o
     LEFT JOIN prodplan.actor a ON a.id = coalesce(o.run_as_sub, o.created_by_sub)
    WHERE o.status = 'needs_login' OR o.last_error_code = 'needs_login' ORDER BY o.updated_at;
   ```
2. Hubungi orang itu: login lagi di `/produksi/masuk`, buka rencana, tekan **Lanjutkan posting**. Orang lain (dengan
   cabang + izin dokumen yang cukup) boleh melanjutkan **kecuali** ada baris `unknown`/`sent` atau `needs_login` dengan
   `attempts > 0` -> hanya aktor asli (`resume_requires_original_user`, Q-PP16).
3. Jangan UPDATE outbox lewat SQL. Posting panjang berulang terpotong = turunkan ukuran rencana atau tinjau Q-PP15.

### EfnProdplanOdooProvisionFailing

`info`. `prodplan_odoo_provision_total{result="failed"}` naik tanpa satu pun `ok` selama 6 jam (hanya bila
`PRODPLAN_ODOO_PROVISION_ENABLED=true`). Login ESB tidak terdampak; identitas untuk migrasi Odoo tertunda.
1. Log prodplan `odoo_provision_failed {actor_id, http_status, reason_class}`.
2. `X-EFN-Gateway-Deny` / log gateway `why=`: `cn` (sertifikat `prodplan-odoo` hilang/CA berganti -> §11.2), `path`
   (haproxy belum memuat `p_esb_identity`), `auth`/`too_large`. 503 `gateway_unavailable` = Odoo/antrean penuh.
3. 401 dari Odoo = API key salah/kedaluwarsa/`disabled` (§11.3). 404 = modul `efn_esb_identity` belum terpasang atau
   metode belum di `RPC_ALLOWLIST`. Galat Odoo `esb_provision_no_company` = cabang ESB belum dipetakan ke company
   (`efn.insight.esb.branch`).
4. Tidak bisa diperbaiki segera: matikan provisioning (§11.5); identitas terkumpul lagi pada login berikutnya.

## 8. Troubleshooting

| Gejala | Penyebab umum | Tindakan |
|---|---|---|
| prodplan exit 2, log `prodplan_config_error` | `PRODPLAN_ESB_BASE_URL` di luar dua URL; `PRODPLAN_ESB_COMPANY_CODES` kosong untuk brand aktif; `reason=esb_role_rule` (aturan peran tak sah / memuat `authorize`); `PRODPLAN_ODOO_RPC_URL` diubah; `PG_SSLMODE` bukan verify-full; `reason=prod_write_locked` (write=true + base production) | Perbaiki `.env`. `prod_write_locked` = perilaku yang disengaja (L-250), bukan bug |
| `/readyz` 503, log PG `password authentication failed` | `00-roles.sh` belum dijalankan setelah overlay dipasang (role masih NOLOGIN) | §3 langkah 2 |
| PG `permission denied` / `relation does not exist` (mis. `prodplan.actor`) | `23-*` / `24-*` belum dijalankan | §3 langkah 2, §12 |
| `24-prodplan-esb-login.sql` gagal `RAISE EXCEPTION` | masih ada rencana `draft/submitted/approved` atau outbox non-terminal | §12 langkah 1 (disengaja) |
| PG `no pg_hba.conf entry` | postgres belum di-recreate dengan overlay (tidak di `prodplan-db`) atau `pg_hba.conf` lama | `up -d --force-recreate postgres` |
| insight-web: prodplan tidak terjangkau, sidecar log `bad certificate` / `unknown authority` | CA dev dibuat ulang tetapi container lama masih memegang bundle lama; CN salah | `up -d --force-recreate` stack / sidecar |
| Login: "Username atau password ESB salah." padahal benar | `flagActive` false di ESB, akun dikunci ESB, staging vs production tertukar (`PRODPLAN_ESB_BASE_URL`) | cek akun di ESB; cek URL |
| Login: "Akun ESB ini tidak memiliki izin modul Produksi." | `companyCode` tidak cocok `PRODPLAN_ESB_COMPANY_CODES`; izin ESB tidak memenuhi `PRODPLAN_ESB_ROLE_*`; tanpa cabang | cocokkan companyCode; minta IT klien menambah hak (Q-PP17) |
| Login: "ESB sedang tidak dapat dihubungi." | ESB 5xx/timeout, egress putus, atau 200 sesi penuh (`session_capacity`) | cek egress (baris DNS); `prodplan_sessions_active` |
| Semua pengguna tiba-tiba harus login lagi | prodplan restart (deploy, rotasi, OOM) — L-260 | perilaku yang disengaja; cek `docker inspect` OOMKilled bila tak terencana |
| Halaman `/produksi` ke `/pp-belum-aktif` | `INSIGHT_PRODPLAN_ENABLED` bukan `true` | aktifkan flag |
| DNS/egress ke `stg7.esb.co.id` gagal | Docker Desktop tanpa internet / proxy korporat | uji `docker compose exec prodplan python -c "import socket;print(socket.gethostbyname('stg7.esb.co.id'))"` |

## 9. Rantai rilis & tindak lanjut (belum dibangun)

- Image `prodplan` di-build, dipindai Trivy, dan ditandatangani `build-push.yml`, tetapi **belum** masuk rantai rilis:
  `IMAGE_PRODPLAN` belum ada di `deploy.yml`/`efn-deploy`/`deploy.sh`, dan `deploy.sh` tidak memasang overlay
  `prodplan` / `prodplan-odoo` (pola esb-extract G41). K25-06: tidak ada deploy ke VPS saat ini.
- Deploy VPS berikutnya (kapan pun) butuh GitHub Environment `PG_PASSWORD_PRODPLAN` dan `PRODPLAN_ASSERT_KEY`
  (generator `random`/`hex32`; deploy.sh menolak nilai kosong), meskipun overlay tidak dipasang.
- Intermediate staging/production wajib diterbitkan ulang (nameConstraints memuat `prodplan`, `insight-web-pp`,
  **`prodplan-odoo`**) sebelum deploy pertama yang memuat G66/A1; tanpa itu `deploy.sh` berhenti di langkah PKI untuk
  semua service. Prosedur: `docs/runbooks/internal-pki.md#service-baru` (`make pki-intermediate ENV=staging|production`).
- Allowlist egress tingkat jaringan (nftables subnet `<base>.25.0/24` -> IP ESB:443) belum ada; saat ini hanya di kode.
- `docs/runbooks/internal-pki.md` belum punya bagian "Gelombang 66" (isi sama dengan §2 dan §11.2).

## 10. Daftar periksa sebelum tulis ke ESB production (tetap terkunci di kode, §30.17.12)

Semua wajib tercentang **sebelum** PR terpisah yang mengubah `PROD_WRITE_UNLOCKED` (dengan review security):

- [ ] ADR-051 berstatus **Accepted**; Q-PP2 (penyimpangan §3.4 diterima pemilik proyek) tertulis.
- [ ] Q-PP17: setiap planner & kepala gudang PB punya akun ESB production **perorangan non-admin** (hak §30.17.6, tanpa
      authorize); PR pembuka menambah `PRODPLAN_ESB_ADMIN_ROLE_IDS` (Q-PP19) + penolakan `admin_account_write_forbidden`.
- [ ] Q-PP16: penyaringan data per pengguna/cabang di ESB diuji dengan akun non-admin.
- [ ] Q-PP3: mode costing production dikonfirmasi (`PRODPLAN_ESB_MFG_MODE`, `PRODPLAN_ESB_DOC_ACCESS`); mode `standard`
      sudah diuji di staging.
- [ ] Uji E2E tahap 2 di staging lolos dengan **dua akun ESB** (SoD): transfer -> authorize manusia -> produksi ->
      pembatalan (DELETE), termasuk timeout setelah kirim (`unknown` -> rekonsiliasi tanpa POST kedua) dan sesi habis di
      tengah posting (`needs_login` -> Lanjutkan posting).
- [ ] Rantai rilis (§9) selesai: `IMAGE_PRODPLAN` bertanda tangan, overlay dipasang deploy.sh, flag di deploy-validate
      (`EFN_ALLOW_FEATURE_ON` Superadmin untuk `PRODPLAN_WRITE_ENABLED`).
- [ ] Allowlist egress nftables aktif; alert `efn-prodplan` terbukti terkirim (uji Alertmanager).
- [ ] Entri `docs/compliance/PDP_DATA_INVENTORY.md` (aktor = username ESB dinormalisasi + nama tampilan, retensi
      §30.17.13; identitas Odoo Q-ESBU4).
- [ ] Restore test menyertakan DB `efn_prodplan` (otomatis: `scripts/restore-test.sh` membandingkan semua DB hidup).

## 11. Provisioning identitas ESB ke Odoo (overlay `prodplan-odoo`, §30.17.11)

Setelah login ESB sukses, thread latar prodplan memanggil satu method Odoo agar daftar pengguna + cerminan peran siap
untuk migrasi (shadow user **diarsipkan**, tanpa password/email, hanya `base.group_user`, tanpa grup PP — R-S26). Default
**mati**; tidak pernah memengaruhi login. ESB staging -> **hanya** stack lokal dev/stg/gladi (render production dengan ESB
staging + flag `true` ditolak `check-encryption.sh`, dan render production wajib default `false`).

Jalur: prodplan --mTLS (CN `prodplan-odoo`, verifikasi CA internal + hostname `odoo-rpc-gateway`)--> `odoo-rpc-gateway:8443`
(haproxy: CN itu **hanya** `POST /json/2/efn.esb.identity/efn_esb_provision`, body ≤ 16 KiB, backend `odoo_forecast`)
--mTLS--> `odoo-tls` -> Odoo (bearer API key `efn_esb_provisioner`). Network `prodplan-rpc` (.26) beranggota tepat
{prodplan, odoo-rpc-gateway}; tanpa port host.

### 11.1 Prasyarat

1. Modul Odoo `efn_esb_identity` terpasang (efn-odoo-engineer; README modul) dan method ada di `RPC_ALLOWLIST` (efn_core).
2. Cabang ESB PB dipetakan ke company (`efn.insight.esb.branch`), selain itu Odoo membalas `esb_provision_no_company`.

### 11.2 Sertifikat klien `prodplan-odoo`

Dev/CI: ikut `APP_ENV=dev bash scripts/gen-secrets.sh` (service PKI baru -> CA dev dibuat ulang, §2). Leaf saja (CA sudah
mencakup `prodplan-odoo`): `bash scripts/gen-pki.sh prodplan-odoo`. Staging/production: intermediate diterbitkan ulang
Superadmin dulu (§9), lalu leaf terbit otomatis saat deploy. Verifikasi tanpa menampilkan kunci:

```bash
openssl x509 -in secrets/tls_prodplan_odoo_crt.txt -noout -subject -ext extendedKeyUsage -enddate
# subject CN = prodplan-odoo ; Extended Key Usage: TLS Web Client Authentication (saja)
```

### 11.3 User teknis & API key `efn_esb_provisioner`

Dilakukan Superadmin di Odoo (pola `forecast-worker.md` §3):

1. User `efn_esb_provisioner` (dibuat modul `efn_esb_identity` atau manual: *Settings → Users → New*), **Internal User**,
   grup **hanya** `group_esb_provisioner` (terdaftar di `efn_role.TECHNICAL_GROUP_XMLIDS`), company yang diizinkan sesuai
   README modul. **Bukan** superuser, **bukan** ERP manager, tanpa grup Inventory/POS/Settings.
2. API key dari sesi user itu sendiri: set password acak sekali pakai, login di jendela privat, *Preferences → Account
   Security → New API Key*, nama `efn-esb-provisioner-<YYYYMMDD>`, durasi **30 hari**. Setelah itu set password acak baru
   (jangan disimpan).
3. Tulis key ke secret **tanpa menampilkannya**:

   ```bash
   # dev / gladi lokal (root worktree stack itu):
   read -rs KEY && printf '%s' "$KEY" > secrets/odoo_api_key_esb_provisioner.txt && unset KEY
   chmod 644 secrets/odoo_api_key_esb_provisioner.txt
   ```

4. Rotasi: ulangi langkah 2–3 sebelum 30 hari, recreate prodplan (§6), hapus key lama di *Account Security*. Dugaan bocor:
   hapus key lama **dulu**. Belum ada pengingat otomatis (pola `EfnForecastApiKeyExpiringSoon` = tindak lanjut).

### 11.4 Menyalakan (hanya stack lokal dengan ESB staging)

```bash
cd infra/compose
F=(-f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.prodplan.yml -f docker-compose.prodplan-odoo.yml)
P=(--profile core --profile data)
# .env: PRODPLAN_ODOO_PROVISION_ENABLED=true
docker compose "${F[@]}" "${P[@]}" config -q
# odoo-rpc-gateway di-recreate (network prodplan-rpc + haproxy.cfg baru); kanal online putus sesaat
docker compose "${F[@]}" "${P[@]}" up -d --force-recreate odoo-rpc-gateway
docker compose "${F[@]}" "${P[@]}" up -d --force-recreate prodplan prodplan-tls   # semua pengguna login ulang
```

Uji: login ESB di `/produksi/masuk`, lalu metrik `prodplan_odoo_provision_total{result="ok"}` naik dan log tanpa
`odoo_provision_failed`. Filter gateway (sertifikat harus sudah ada): `bash infra/odoo-rpc-gateway/test-gateway.sh`
(bagian `prodplan-odoo`).

### 11.5 Mematikan provisioning

- **Cepat:** `.env` `PRODPLAN_ODOO_PROVISION_ENABLED=false` -> `up -d prodplan prodplan-tls` (tanpa koneksi ke Odoo;
  semua pengguna login ulang).
- **Penuh (cabut jalur):** jalankan `up -d --force-recreate prodplan prodplan-tls odoo-rpc-gateway` **tanpa**
  `-f docker-compose.prodplan-odoo.yml` -> prodplan kehilangan network `prodplan-rpc`, API key, dan sertifikat klien; flag
  dipaku `false`. Opsional: `printf disabled > secrets/odoo_api_key_esb_provisioner.txt` dan hapus API key di Odoo.
- Identitas yang sudah tercatat di Odoo tetap (arsip); penghapusan/anonimisasi = keputusan DPO (Q-ESBU4).

## 12. Migrasi skema A1 `24-prodplan-esb-login.sql` (sekali per environment)

Migrasi mengubah arti kolom `*_sub` (kini id `prodplan.actor`, bukan uid Odoo) dan **membatalkan diri** (`RAISE
EXCEPTION`) bila masih ada rencana `draft|submitted|approved` atau outbox non-terminal — disengaja agar tidak ada
rencana lama yang dilanjutkan dengan identitas berbeda. Urutan (§30.17.16):

1. **Masih di rilis lama** (UI login Odoo, akun service lama masih terpasang): planner membatalkan semua rencana terbuka
   di `/produksi` (**Batalkan**). Dokumen yang sudah `confirmed` di ESB staging dihapus worker dengan akun lama; tunggu
   sampai outbox terminal. Cek (read-only), harus 0 baris:
   ```sql
   SELECT 'plan' AS t, id::text, status FROM prodplan.plan WHERE status IN ('draft','submitted','approved')
   UNION ALL
   SELECT 'outbox', id::text, status FROM prodplan.outbox
    WHERE status IN ('pending','sending','sent','unknown','delete_pending','deleting');
   ```
   Kondisi pasti = isi `24-prodplan-esb-login.sql` (pemilik: engineer prodplan). Baris `delete_failed` = tangani manual
   di ESB (§7 `EfnProdplanDeleteFailed`). **Jangan** UPDATE status lewat SQL tanpa keputusan pemilik proyek.
2. Hentikan prodplan: `docker compose "${F[@]}" "${P[@]}" stop prodplan prodplan-tls`.
3. Jalankan migrasi (§3 langkah 2, baris `24-prodplan-esb-login.sql`). Gagal `RAISE EXCEPTION` -> kembali ke langkah 1
   (start rilis lama dulu). Apakah menjalankan ulang 24-* setelah berhasil aman saat ada rencana baru = lihat header file
   itu (pemilik: engineer prodplan); bila ragu, jangan jalankan ulang.
4. Pasang rilis A1 **prodplan + insight-web bersamaan** (§3 langkah 3); flag tetap `false` sampai keduanya hidup.
5. Hapus secret & env lama (§2.1).
6. Uji login ESB staging (§4), hitung rencana, posting transfer; approve/SoD menunggu akun ESB kedua (Q-PP17).
