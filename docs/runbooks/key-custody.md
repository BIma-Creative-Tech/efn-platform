# Prosedur: Kustodi kunci (peran Superadmin)

Kontrak: `docs/04_SERVICE_CONTRACTS.md` §8.6 (keputusan user: **kunci hanya dipegang Superadmin**), ADR-022 (manajemen kunci). Alat: `make pki-root`, `make pki-intermediate`, `scripts/deploy.sh`, `scripts/derive-secrets.sh`.

> **Ringkas untuk manajemen.** Kunci yang bila hilang/bocor berarti kehilangan data atau pemalsuan identitas sistem hanya dipegang peran **Superadmin**. Server dan pipeline CI tidak pernah memegang kunci induk (root CA). Butir bertanda *Rekomendasi* adalah saran konsultan, **bukan keputusan** — perlu disetujui klien.

## 1. Kunci di bawah kustodi Superadmin

| Kunci | Bentuk | Lokasi kerja | Salinan cadangan | Bila hilang |
|---|---|---|---|---|
| Root CA (`root.key`) | ECDSA P-256, terenkripsi passphrase | **Offline** (media terenkripsi Superadmin). Tidak pernah di server/CI | Paket tersegel di **brankas Direksi** (§8, keputusan 10 Okt 2026) | Tidak bisa menerbitkan intermediate → root baru + distribusi bundle |
| Intermediate CA per lingkungan | ECDSA P-256 | GitHub Environment `EFN_PKI_INTERMEDIATE_KEY` → tmpfs host **hanya selama deploy** (`/dev/shm`, dihancurkan) | Tidak perlu (terbitkan ulang dari root) | Terbitkan intermediate baru |
| `pii_encryption_key*` | 32 byte | GitHub Environment → `/opt/efn/secrets` → BFF | **Wajib** offline | Data PII tidak terbaca permanen |
| `walg_libsodium_key` | 32 byte | idem → postgres | **Wajib** offline | Backup tidak bisa dipulihkan |
| `minio_kms_key` | 32 byte | idem → MinIO | **Wajib** offline | Objek MinIO tidak terbaca |
| `metabase_encryption_key` (G16b) | 32 byte base64 (44 karakter) | GitHub Environment → `/opt/efn/secrets` → **hanya** service metabase (`MB_ENCRYPTION_SECRET_KEY`, fail-closed) | Disarankan offline (kunci lama diarsipkan saat rotasi selama dump `metabase` lama masih dalam retensi) | Detail koneksi DB di Metabase tidak terbaca → `metabase-provision` membuat ulang koneksi ClickHouse; dashboard/kartu tetap (tidak terenkripsi) |
| `clickhouse_disk_key` | hex | idem → ClickHouse | Disarankan | Rebuild DWH dari PostgreSQL |
| `odoo_pii_key_v<n>` (G21 AC-13) | 32 byte base64 | **Dibuat Superadmin di host** → `/opt/efn/secrets` → **hanya** service odoo (overlay `docker-compose.pii.yml`). Tidak pernah di GitHub/CI ([§4e](#pii-odoo)) | **Wajib** — paket segel brankas Direksi (§8.1) | **PII Odoo (pelanggan, vendor perorangan, karyawan, NIK) tidak terbaca permanen** |
| `odoo_pii_bidx_key`, `odoo_pii_name_key` (G21 AC-13) | 32 byte base64, berbeda satu sama lain | idem | **Wajib** — paket segel (tanpa kunci ini pencarian gagal sampai dihitung ulang) | Pencarian HP/email/NPWP/NIK/nama gagal sampai blind index/token dihitung ulang (data tidak hilang) |
| `otp_pepper` → `member_hash_pepper` | random; turunan HKDF | `otp_pepper` di GitHub Environment; `member_hash_pepper` **diturunkan di host** (`derive-secrets.sh`), tidak disimpan di GitHub | **Wajib** offline (`otp_pepper`) | Semua `member_hash` berubah → ledger/DSAR tidak tersambung |
| Passphrase LUKS per node | passphrase | Keyslot disk | **Wajib** offline | Disk tidak terbuka → restore |

Secret operasional lain (password service, token WhatsApp, kunci API payment, token DNS ACME, G21 `loyalty_policy_signing_key` = HMAC publikasi kebijakan poin Odoo → BFF, hanya odoo + api-gateway) dikelola lewat GitHub Environment yang sama; perubahannya juga butuh approval Superadmin (§3).

## 2. Peran

- **Superadmin**: satu-satunya yang (a) mengelola secret & reviewer GitHub Environment `production` (dan `staging`), (b) anggota grup host `efn-superadmin` dengan sudo, (c) memegang root CA offline & salinan tersegel, (d) menyetujui deploy produksi.
- **Ops/IT (non-superadmin)**: akses SSH tanpa sudo dan **bukan** anggota grup `docker` (grup docker setara root). Melihat log lewat Grafana/Loki (`ops.`), bukan host.
- **Operator CS online (`efn-cs`) dan approver (`efn-cs-approver`)** (G17, SEC-107/111/112): akun host pribadi, sudo
  **hanya** untuk wrapper `/usr/local/sbin/efn-online-cs`, bukan anggota `docker`, satu orang satu grup. **Pemisahan
  tugas lintas sistem:** pemegang grup Odoo Sales Manager (`sales_team.group_sale_manager`) **tidak boleh** anggota
  `efn-cs-approver`. Superadmin memelihara register peran (di luar repo) dan menjalankan **review akses bulanan** dengan
  saksi Finance/Internal Audit — prosedur & perintah: [online-orders.md#sec-111](online-orders.md#sec-111). Superadmin
  sendiri sebaiknya tidak menjadi approver refund rutin (pemegang root dapat melewati wrapper); bila terpaksa, setiap
  persetujuan oleh Superadmin dicatat di register dan diperiksa Finance.
- **Diputuskan pemilik proyek 10 Okt 2026 (Q-E1a/b/c): 1 orang Superadmin**, salinan offline tersegel di **brankas Direksi**, tanpa Shamir; bus factor 1 **diterima** (risiko **R-B11**). Mitigasi = §8 (paket segel, log brankas, uji pemulihan disaksikan, prosedur bila Superadmin tidak tersedia). Pembukaan brankas tetap prinsip **dua orang** (Direksi + saksi).

## 3. GitHub

1. Settings → Environments → `production`: **Required reviewers = Superadmin** (TIDAK tersedia di repo privat paket gratis, dicek 9 Okt 2026 → pengganti: persetujuan out-of-band di host §4d) (semua orang Superadmin; centang "Prevent self-review"). **Deployment branches and tags = Selected**: hanya tag `v*` (SEC-30). `staging`: hanya branch `main`.
2. Environment secrets `production`/`staging` hanya bisa diubah admin repo → admin repo = Superadmin saja; developer lain role `Write`/`Maintain` (tanpa `Admin`).
3. Secret PKI per lingkungan: `EFN_PKI_INTERMEDIATE_KEY`, `EFN_PKI_INTERMEDIATE_CRT`, `EFN_PKI_ROOT_CRT` (+ `EFN_PKI_EXTRA_TRUST` hanya selama rotasi CA). Sertifikat leaf **tidak lagi** disimpan di GitHub (diterbitkan di host).
4. Variables (non-rahasia, wajib): `PAYMENT_PROVIDER` (eksplisit), `EFN_PUBLIC_BIND`, `TRAEFIK_DOMAIN`, `ACME_EMAIL`; opsional `EFN_WG_*`, `ACME_DNS_PROVIDER`, `ACME_DNS_TOKEN_ENV`.
5. Bila ada GitHub secret lama `TLS_*_CRT/KEY` dan `EFN_CA_CRT` dari gelombang 3: hapus setelah deploy pertama gelombang 5 sukses.

## 4. Host (sekali per node, oleh Superadmin)

```bash
groupadd --system efn-superadmin
usermod -aG efn-superadmin <superadmin-1>; usermod -aG efn-superadmin <superadmin-2>
echo '%efn-superadmin ALL=(ALL:ALL) ALL' > /etc/sudoers.d/efn-superadmin        # sudo dengan password
install -d -o root -g efn-superadmin -m 0750 /opt/efn/secrets
# /proc hidepid: argv/env proses lain tidak terlihat oleh user non-root (mitigasi fallback openssl di derive-secrets.sh)
echo 'proc /proc proc defaults,hidepid=2 0 0' >> /etc/fstab && mount -o remount,hidepid=2 /proc
```

### 4a. Jalur deploy CI terkunci (SEC-36, gelombang 9)

Sebelumnya: `deploy ALL=(root) NOPASSWD: /usr/bin/bash -s` + `/opt/efn/scripts` milik user deploy = shell root bagi
siapa pun yang memegang kunci SSH deploy. Sekarang kunci CI **hanya** bisa menjalankan wrapper root
`/usr/local/sbin/efn-deploy` (sumber: `scripts/host/efn-deploy`), yang **menolak** rilis tanpa tanda tangan cosign
keyless dari workflow `deploy.yml` pada ref yang diizinkan, lalu menjalankan `deploy.sh` dari salinan milik root.

```bash
# 1) cosign di host (versi = signer di cosign-installer deploy.yml; v2.6.1), verifikasi checksum
curl -fsSLo /tmp/cosign https://github.com/sigstore/cosign/releases/download/v2.6.1/cosign-linux-amd64
echo '064954c5d8c7e3b28188eee5b1727b31c411550bc5fefd41aa672d3c761d103a  /tmp/cosign' | sha256sum -c -
install -o root -g root -m 0755 /tmp/cosign /usr/local/bin/cosign

# 2) wrapper: SALIN DARI COMMIT YANG SUDAH DI-REVIEW, bandingkan sha256 dengan yang tercatat di PR
install -o root -g root -m 0755 scripts/host/efn-deploy /usr/local/sbin/efn-deploy
sha256sum /usr/local/sbin/efn-deploy

# 3) konfigurasi root-only: identitas penanda tangan, lingkungan, registry, PIN trust anchor (SEC-42).
#    production: tag v*; staging: main (EFN_SIGNER_IDENTITY_REGEXP ...@refs/heads/main$, EFN_APP_ENV=staging)
#    EFN_PKI_ROOT_SHA256 = sidik jari root CA dari register kunci (§5 butir 3), BUKAN disalin dari GitHub:
#      openssl x509 -in ~/efn-pki-root/root.crt -noout -fingerprint -sha256 | cut -d= -f2 | tr -d ':' | tr A-F a-f
install -d -o root -g root -m 0755 /etc/efn
cat > /etc/efn/deploy.conf <<'EOF'
COSIGN_BIN=/usr/local/bin/cosign
EFN_SIGNER_IDENTITY_REGEXP='^https://github\.com/<org>/<repo>/\.github/workflows/deploy\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$'
EFN_SIGNER_ISSUER=https://token.actions.githubusercontent.com
EFN_APP_ENV=production
EFN_GITHUB_REPO=<org>/<repo>
EFN_IMAGE_PREFIX=ghcr.io/<org-huruf-kecil>/
EFN_PKI_ROOT_SHA256=<64 hex>
EFN_PKI_EXTRA_TRUST_SHA256=''
# SEC-50 (gelombang 13): topologi host DIPIN di sini (stdin efn-deploy menolaknya). Lihat §4c.
TRAEFIK_DOMAIN=<domain>
EFN_PUBLIC_BIND=<IPv4 host untuk 80/443>
EFN_WG_BIND=10.88.0.1
EFN_WG_HTTPS_PORT=443
EFN_WG_OUTLET_CIDR=10.88.0.0/16
EFN_WG_STAFF_CIDR=10.89.0.0/16
COMPOSE_PROFILES=core,data,ai,observability,ops
EFN_ALLOW_CONTROL_OFF=''
EOF
chmod 0644 /etc/efn/deploy.conf

# 4) sudoers TANPA argumen (bukan bash -s) + user deploy keluar dari grup docker
echo 'deploy ALL=(root) NOPASSWD: /usr/local/sbin/efn-deploy' > /etc/sudoers.d/efn-deploy
chmod 0440 /etc/sudoers.d/efn-*
gpasswd -d deploy docker || true

# 5) kunci CI di ~deploy/.ssh/authorized_keys: forced command, tanpa forwarding/pty
#    restrict,command="sudo -n /usr/local/sbin/efn-deploy" ssh-ed25519 AAAA... efn-ci

# 6) /opt/efn milik root (sisa rsync lama sebagai user deploy)
chown -R root:root /opt/efn/scripts /opt/efn/compose /opt/efn/traefik /opt/efn/observability 2>/dev/null || true

# 7) audit setiap sudo dari user deploy
echo '-a always,exit -F arch=b64 -S execve -F path=/usr/local/sbin/efn-deploy -k efn_deploy' \
  > /etc/audit/rules.d/efn-deploy.rules && augenrules --load
```

Yang dijaga: pemegang kunci SSH deploy **saja** (tanpa akses tulis repo + approval Environment) tidak dapat
menjalankan perintah arbitrer sebagai root, tidak dapat menimpa `deploy.sh`, dan tidak dapat mengirim variabel env
di luar allowlist (`PATH`, `LD_PRELOAD`, `BASH_ENV` ditolak). Yang **tidak** dijaga (risiko residu, jujur):
penyerang dengan hak tulis repo + approval Environment (Superadmin) tetap bisa men-deploy apa pun — itu memang jalur
rilis yang sah; kompromi admin org GitHub tetap kritis (lihat juga intermediate PKI online). Pembaruan wrapper
sendiri tidak lewat CI (disengaja): setiap perubahan `scripts/host/efn-deploy` wajib dipasang ulang manual oleh
Superadmin.

Verifikasi setelah pasang: `ssh deploy@host 'id'` harus menjalankan efn-deploy (bukan `id`) dan gagal "input"
karena stdin kosong; `sudo -l -U deploy` hanya menampilkan `/usr/local/sbin/efn-deploy`; `id deploy` tanpa `docker`.

### 4b. Manifest rilis bertanda tangan, pin trust anchor, anti-downgrade (SEC-42, gelombang 11)

`deploy.yml` menulis `efn-release.json` **di dalam** `release.tar.gz` yang ditandatangani: 7 digest image (hasil
`cosign verify` di runner), versi (`X.Y.Z` / `sha-<7>`), `seq` = `github.run_number`, git sha, `signer_ref`, dan
sidik jari root CA + CA transisi. `efn-deploy` di host:

| Cek | Penolakan bila |
|---|---|
| `IMAGE_*` | dikirim lewat env (tidak lagi di allowlist); di manifest bukan `${EFN_IMAGE_PREFIX}<svc>@sha256:<64 hex>`; `cosign verify` **di host** gagal untuk identitas `build-push.yml@<signer_ref>` + SHA commit `git_sha` manifest + repo `EFN_GITHUB_REPO` (SEC-91) |
| Trust anchor | `EFN_PKI_ROOT_CRT` ≠ tepat 1 sertifikat atau sidik jari ≠ `EFN_PKI_ROOT_SHA256`; root di manifest ≠ pin; intermediate tidak diterbitkan root yang dipin / bukan CA; setiap sertifikat `EFN_PKI_EXTRA_TRUST` tidak ada di `EFN_PKI_EXTRA_TRUST_SHA256` **dan** manifest |
| Lingkungan | manifest `app_env` ≠ `EFN_APP_ENV`; `APP_ENV` dikirim lewat stdin (G13: `APP_ENV` = `EFN_APP_ENV`); production bukan `X.Y.Z` dari `refs/tags/vX.Y.Z` |
| Konfigurasi (G13, SEC-50) | lihat §4c |
| Anti-replay/downgrade | `seq` < `seq` terakhir di `/var/lib/efn/last-release` (root 0600); `seq` sama tetapi isi berbeda; production: versi semver turun. Rilis identik (sha256 sama) boleh di-deploy ulang |

Tidak ada yang ditulis ke `/opt/efn` sebelum semua cek lolos. Uji regresi: `scripts/host/test-efn-deploy.sh`
(cosign palsu, 87 kasus sejak G16c; CI job `infra`).

**Rollback ke rilis lama (sengaja):** jalankan workflow Deploy dengan tag lama → seq baru, versi lebih rendah →
ditolak. Superadmin di host mengizinkan **sekali** untuk arsip tertentu:
```bash
# sha256 release.tar.gz rilis yang akan dipakai: tercetak di log job "Bundel rilis" (baris sha256sum)
echo '<sha256>' > /etc/efn/allow-downgrade && chmod 0600 /etc/efn/allow-downgrade   # dihapus otomatis setelah dipakai
```
Rename `deploy.yml` mereset `run_number` → semua deploy ditolak sampai Superadmin menghapus
`/var/lib/efn/last-release` (catat di register perubahan).

**Rotasi root CA:** sebelum deploy transisi, tambahkan sidik jari root LAMA ke `EFN_PKI_EXTRA_TRUST_SHA256` dan ganti
`EFN_PKI_ROOT_SHA256` ke root BARU di setiap host (lihat [internal-pki.md](internal-pki.md) §Rotasi root).

**Risiko residu (jujur):** nilai **secret** dari env (password DB, pepper, token) tidak bisa ditandatangani tanpa
masuk log transparansi Rekor, jadi tetap dipercaya dari kanal SSH. Pemegang kunci SSH deploy dapat mengganti
secret (mis. password DB → layanan gagal start / data baru dienkripsi kunci penyerang) tetapi tidak dapat
menjalankan image/skrip lain atau memasang CA lain. Mitigasi: kunci SSH hanya di GitHub Environment ber-approval,
auditd `-k efn_deploy`, dan baris `logger -t efn-deploy` (journald) yang dikirim ke penyimpanan log terpisah;
tinjau `journalctl -t efn-deploy` setelah setiap deploy (alert Loki untuk journald host = backlog: Promtail saat
ini hanya membaca log container).

### 4c. Konfigurasi non-rahasia: topologi dipin di host, konfigurasi ditandatangani (SEC-50, gelombang 13)

Sebelum G13, pemegang kunci SSH deploy (dengan rilis bertanda tangan asli) dapat mengirim `EFN_WG_BIND=<IP publik>` +
`EFN_WG_STAFF_CIDR=0.0.0.0/0` (membuka `erp.`/`bi.`/`ops.` ke internet), menyisipkan baris ke `.env` lewat nilai
multi-baris, atau mematikan `RABBITMQ_STRICT_PERMISSIONS`/`ETL_AUDIT_COPY_ENABLED`. Sekarang setiap nilai punya SATU
sumber, dan stdin `efn-deploy` hanya membawa secret:

| Sumber | Variabel | Siapa yang mengubah |
|---|---|---|
| `/etc/efn/deploy.conf` (root) | `TRAEFIK_DOMAIN`, `EFN_PUBLIC_BIND`, `EFN_WG_BIND`, `EFN_WG_HTTPS_PORT`, `EFN_WG_OUTLET_CIDR`, `EFN_WG_STAFF_CIDR`, `COMPOSE_PROFILES`, `EFN_ALLOW_CONTROL_OFF` | Superadmin di host (catat di register perubahan) |
| Manifest `efn-release.json` → `config` (di dalam arsip bertanda tangan) | `ACME_*`, `PAYMENT_PROVIDER`, `PG_SUPERUSER`, `WHATSAPP_*`, `RABBITMQ_STRICT_PERMISSIONS`, retensi, `CONSENT_*`, `DSAR_DELETE_GRACE_DAYS`, `ETL_ENABLE_*`, `ETL_AUDIT_COPY_ENABLED`, `OLLAMA_VISION_MODEL`, `MIGRATION_ENABLED`, `METABASE_ADMIN_EMAIL` | GitHub Environment **vars** (proses PR/approval environment), ditandatangani `deploy.yml` |
| stdin `env.b64` | `GHCR_USER`, `GHCR_TOKEN`, `EFN_PKI_*`, secret di `scripts/secrets.list` | GitHub Environment **secrets** |

Aturan (`scripts/lib/deploy-validate.sh`, dipakai `efn-deploy` SEBELUM menulis `/opt/efn` dan lagi oleh `deploy.sh`):
- `EFN_WG_BIND` wajib IPv4 di `10.88.0.0/16` atau `10.89.0.0/16`, bukan `EFN_PUBLIC_BIND`; `deploy.sh` juga mewajibkan
  alamat itu ada di interface `wg0` (`ip -4 addr show dev wg0`).
- `EFN_WG_*_CIDR` wajib CIDR kanonik di dalam `10.88.0.0/15` dengan prefix ≥ /16 (`0.0.0.0/0`, `10.0.0.0/8`,
  `192.168.x` ditolak). `EFN_PUBLIC_BIND` bukan `0.0.0.0`/loopback/subnet WG.
- Semua nilai: regex per variabel, karakter kontrol (newline, CR, ESC, …) ditolak — PEM `EFN_PKI_*` hanya boleh newline.
  `.env` ditulis "satu kunci satu baris" dan jumlah barisnya diverifikasi ulang.
- Production menolak `RABBITMQ_STRICT_PERMISSIONS=false` dan `ETL_AUDIT_COPY_ENABLED=false`, kecuali nama variabel itu
  dicantumkan Superadmin di `EFN_ALLOW_CONTROL_OFF` (darurat; hapus lagi setelahnya). `COMPOSE_PROFILES` production
  wajib memuat `core,data,observability`. `PAYMENT_PROVIDER=mock` ditolak.
- Jalur manual `sudo -E bash deploy.sh`: nilai topologi dari env yang BERBEDA dengan pin `deploy.conf` → ditolak.

**Migrasi host lama (sekali):** tambahkan kunci topologi di atas ke `deploy.conf` (nilai = GitHub vars lama), pasang
ulang `efn-deploy` (sha256 dari PR), lalu deploy. GitHub vars `EFN_*`/`COMPOSE_PROFILES`/`TRAEFIK_DOMAIN` boleh
tetap ada (TRAEFIK_DOMAIN dipakai smoke test workflow) tetapi tidak lagi dikirim ke host. Rilis lama (schema 1) ditolak.
**Secret baru G13** di GitHub Environment: `EFN_AUDIT_SINK_KEY` (≥ 32 byte acak, [SA]), `METABASE_ADMIN_PASSWORD`,
`MIGRATION_API_KEY` (hex, berbeda dari `ODOO_BFF_API_KEY`). PKI: service baru `etl`, `metabase-provision` →
intermediate staging/prod diterbitkan ulang ([internal-pki.md](internal-pki.md#service-baru)).

### 4d. Persetujuan rilis production out-of-band (SEC-92, G16c)

Konteks: di repo privat paket gratis **Required reviewers Environment tidak tersedia** (dicek 9 Okt 2026 lewat
`gh api repos/bctech-adm/efn-platform/environments`: `production` hanya punya `branch_policy` = tag `v*`; `staging`
tanpa aturan apa pun). Tag `v*` bisa dibuat dari commit di luar `main` yang membawa `build-push.yml`/`deploy.yml` tanpa
gerbang; tanda tangannya tetap sah. Karena itu host production **tidak** menerima rilis hanya karena tanda tangan CI:
`efn-deploy` mewajibkan baris `<X.Y.Z> <git_sha>` di `/etc/efn/approved-release` (root, tidak bisa ditulis
group/other). Staging tidak memakai file ini.

Prosedur Superadmin per rilis (sebelum push tag, atau sebelum menyetujui deploy):
```bash
R=bctech-adm/efn-platform; V=1.2.3; SHA=<40 hex commit yang akan di-tag>
gh api "repos/$R/compare/$SHA...main" --jq .status          # harus "ahead" atau "identical" (commit ada di main)
bash scripts/verify-commit-gates.sh --sha "$SHA" --preset release --timeout-min 0 --absent-min 0   # CI + Security hijau
git tag -s "v$V" "$SHA" && git push origin "v$V"              # atau buat tag dari UI pada SHA itu
# di host production (root):
echo "$V $SHA" >> /etc/efn/approved-release && chmod 0644 /etc/efn/approved-release && chown root:root /etc/efn/approved-release
```
Baris tidak dihapus setelah dipakai (deploy ulang/rollback rilis yang sama tetap bisa; rollback ke versi lebih rendah
juga butuh `allow-downgrade` §4b). Hapus baris rilis yang ditarik. Penolakan tercatat di log `efn-deploy`
("persetujuan rilis production tidak ada/tidak cocok").

Rekomendasi admin repo (gratis, belum diterapkan, butuh hak admin): Environment `staging` → Deployment branches =
`main` saja (saat ini kosong: `workflow_dispatch` dari branch mana pun bisa memakai secret staging).

### 4e. Kunci PII Odoo produksi (G21 AC-13, docs/04 §13.2, ADR-036 §0) {#pii-odoo}

> **Ringkas untuk manajemen.** Tiga kunci yang mengenkripsi data pribadi di Odoo dibuat langsung di server oleh
> Superadmin, tidak pernah lewat GitHub/CI, dan satu salinannya disegel di brankas Direksi. Fitur ini **mati** sampai
> PO menyatakan aktivasi (L-59). Kehilangan kunci utama = data pribadi di Odoo tidak bisa dibaca selamanya, jadi
> salinan brankas wajib dibuat **sebelum** fitur dinyalakan.

Status di `scripts/secrets.list`: `host:disabled [SA]` → `deploy.sh` membuat file berisi `disabled` bila belum ada dan
**tidak pernah menimpanya**; `efn-deploy` menolak nama ini dari stdin (`ODOO_PII_*`, uji 110c–110e). Kunci di-mount
**hanya** ke `odoo` oleh `infra/compose/docker-compose.pii.yml` saat `EFN_ODOO_PII_ENABLED=true` (`check-encryption.sh`
render `prod-pii`). Staging dan production memakai kunci **berbeda** (jangan menyalin antar-lingkungan).

**Gerbang otomatis** (`scripts/lib/deploy-validate.sh` + `deploy.sh`, fail-closed):
| Kondisi | Hasil |
|---|---|
| production `EFN_ODOO_PII_ENABLED=true` tanpa `EFN_ODOO_PII_REQUIRED=true` | ditolak sebelum `/opt/efn` disentuh (tidak bisa dibuka `EFN_ALLOW_CONTROL_OFF`) |
| `EFN_ODOO_PII_ENABLED=true` + salah satu file kunci tidak ada / kosong / symlink / `disabled` / bukan 32 byte base64 kanonik / dua kunci sama | deploy gagal, overlay tidak dipasang |
| modul `efn_pii` berstatus `installed`/`to upgrade`/`to remove` di DB `efn_odoo` tetapi `EFN_ODOO_PII_ENABLED` bukan `true` | deploy gagal **sebelum** `up` (Odoo lama tidak di-recreate tanpa kunci) |

**Prosedur pembuatan (sekali per lingkungan, Superadmin + saksi Direksi untuk langkah 3):**
1. Prasyarat: keputusan aktivasi PO tercatat (L-59); jalankan di host target sebagai root (`sudo -i`), dengan
   `/opt/efn/secrets` = `root:efn-superadmin 0750` (§4).
2. Buat kunci di host (tanpa transit jaringan; nilai tidak pernah dicetak):
   ```bash
   cd /opt/efn/secrets
   for n in odoo_pii_key_v1 odoo_pii_bidx_key odoo_pii_name_key; do
     f="$n.txt"
     if [ -s "$f" ] && ! grep -qx disabled "$f"; then echo "$f SUDAH berisi kunci - JANGAN ditimpa (lihat pii-key-rotation.md)"; continue; fi
     ( umask 0277; openssl rand -base64 32 | tr -d '\n' > "$f.new" )
     chown root:efn-superadmin "$f.new" && chmod 0444 "$f.new" && mv -f "$f.new" "$f"
   done
   sha256sum odoo_pii_*.txt      # catat HASH (bukan nilai) di register kunci + berita acara
   ```
3. **Salinan tersegel SEBELUM aktivasi**: enkripsi ketiga file dengan passphrase sementara lalu pindahkan ke mesin
   offline (§5) dan masukkan ke paket A brankas (§8.2, §8.4 pembaruan paket):
   ```bash
   tar -C /opt/efn/secrets -cf - odoo_pii_key_v1.txt odoo_pii_bidx_key.txt odoo_pii_name_key.txt \
     | age -p -o /root/efn-pii-$(hostname)-$(date +%Y%m%d).tar.age      # alternatif: gpg --symmetric --cipher-algo AES256
   # salin ke media Superadmin (scp lewat WireGuard / USB), verifikasi sha256 di mesin offline, lalu di host:
   shred -u /root/efn-pii-*.tar.age
   ```
   Di mesin offline: dekripsi, `sha256sum -c` terhadap hash langkah 2, masukkan ke paket A, segel, log brankas (§8.3).
4. Aktivasi: GitHub Environment **vars** `EFN_ODOO_PII_ENABLED=true` dan `EFN_ODOO_PII_REQUIRED=true` → deploy (manifest
   bertanda tangan). Baru setelah itu install modul `efn_pii*` (runbook rilis Odoo, `deploy-odoo-modules.md`).
5. Verifikasi (host):
   ```bash
   cd /opt/efn/compose; DC="docker compose $(sed 's/^/-f /' .efn-compose-files | tr '\n' ' ')"
   $DC exec -T odoo sh -c 'ls /run/secrets | grep -c "^odoo_pii_"'            # 3
   for s in api-gateway etl ocr-worker metabase odoo-rpc-gateway; do
     $DC exec -T "$s" sh -c 'ls /run/secrets 2>/dev/null | grep -c "^odoo_pii_" || true'; done   # 0 semua
   $DC logs --since 10m odoo | grep -c 'EFN_PII_KEYRING.*CRITICAL' || true        # 0
   ```
   Uji fungsional: buat kontak perorangan uji di Odoo → kolom `phone` di DB berawalan `efnpii2:`; cari HP lengkap →
   ditemukan; hapus kontak uji.

**Larangan:** menjalankan `scripts/gen-secrets.sh` di host; menyalin kunci dev/CI; menimpa `odoo_pii_key_v1.txt` yang sudah
berisi kunci (data terenkripsi dengannya hilang permanen) — rotasi hanya lewat [pii-key-rotation.md](pii-key-rotation.md#kunci-pii-odoo).
Menonaktifkan (`EFN_ODOO_PII_ENABLED=false`) saat `efn_pii` terpasang **ditolak deploy** (by design); menonaktifkan PII
berarti uninstall/migrasi-balik modul (keputusan PO + ADR-036), bukan saklar.

### Mekanisme uid: kenapa file secret 0444 di dalam direktori 0750

`deploy.sh` (root) menulis: direktori `/opt/efn/secrets` = `root:efn-superadmin 0750`, file = `root:efn-superadmin 0444`.

- **Akses host** dibatasi oleh **direktori**: user di luar `root`/`efn-superadmin` tidak punya izin *traverse* (`x`) → tidak bisa membuka file apa pun di dalamnya, walau mode file 0444.
- **Akses container**: Compose (non-swarm) mewujudkan `secrets:` sebagai *bind mount* file host ke `/run/secrets/<nama>`. Bind mount dibuat oleh `dockerd` (root) — izin direktori induk tidak berlaku di dalam container; yang berlaku hanya mode/uid/gid file itu sendiri. Proses container berjalan dengan uid yang berbeda-beda (ghostunnel 65534, postgres 999, grafana 472, clickhouse 101, node 1000, dst.) dan beberapa entrypoint resmi turun hak via `gosu`/`su-exec` yang **membuang supplementary group**. Dengan 0440 + `group_add` sebagian service gagal membaca sertifikat/kuncinya → maka file dibuat 0444 (dapat dibaca uid mana pun **di dalam container yang me-mount-nya**).
- **Least privilege antar-container** tetap terjaga oleh daftar `secrets:` per service: container hanya melihat file yang di-mount ke dirinya (mis. hanya BFF yang melihat `pii_encryption_key`).
- `SECRET_FILE_MODE=0440` dapat dicoba di staging setelah semua image diverifikasi membaca secret tanpa supplementary group (tidak disarankan sebelum diuji).

### Audit akses (auditd, contoh)

```bash
apt install auditd
cat > /etc/audit/rules.d/efn-secrets.rules <<'EOF'
# Setiap baca/tulis/ubah atribut di direktori secret dan media PKI
-w /opt/efn/secrets -p rwa -k efn_secrets
-w /etc/wireguard -p rwa -k efn_wireguard
-w /etc/sudoers.d -p wa -k efn_sudoers
# Eksekusi docker exec/cp (cara lain membaca secret dari container)
-a always,exit -F arch=b64 -S execve -F path=/usr/bin/docker -k efn_docker_cli
EOF
augenrules --load
ausearch -k efn_secrets --start today -i | aureport -f -i --summary   # tinjau mingguan
```
Kirim log audit ke penyimpanan terpisah dari host (mis. Loki dengan retensi 1 tahun atau syslog remote). Akses yang tidak diawali deploy/approval = insiden ([breach-response-72h.md](breach-response-72h.md)).

## 5. Root CA offline & intermediate

1. Mesin Superadmin offline (laptop khusus tanpa jaringan, disk LUKS), repo checkout terverifikasi:
   ```bash
   make pki-root                                # ~/efn-pki-root/root.{key,crt}; passphrase >= 20 karakter
   make pki-intermediate ENV=staging            # ~/efn-pki-root/out/staging/
   make pki-intermediate ENV=production
   ```
   Skrip menolak berjalan di server (`/opt/efn` ada).
2. Unggah keluaran ke Environment terkait (perintah dicetak skrip), lalu `shred -u` kunci intermediate lokal.
3. Salin `~/efn-pki-root` ke media paket segel brankas Direksi (§8). Catat sidik jari SHA-256 root di register kunci.
4. Intermediate berlaku 2 tahun (alert metrik `efn_ca_crt_0`); terbitkan ulang ≥ 60 hari sebelum kedaluwarsa. Menambah service baru di `PKI_SERVICES` juga butuh intermediate baru (nameConstraints).

## 6. Break-glass (Superadmin tidak tersedia, insiden produksi)

Hanya bila ada insiden aktif yang mengancam layanan/data dan **tidak ada** Superadmin yang dapat dihubungi dalam waktu yang ditetapkan klien.
1. Ikuti §8.5 (Superadmin tidak tersedia): dua pejabat (Direksi pemegang brankas + saksi Internal Audit/Head IT) membuka paket segel bersama-sama; catat di log brankas (§8.3).
2. Gunakan akses minimum (mis. hanya `walg_libsodium_key` untuk restore).
3. Setelah insiden: rotasi semua secret yang tersentuh ([secret-rotation.md](secret-rotation.md)), segel ulang, review dalam 5 hari kerja.
4. Akun break-glass GitHub (bila ada) memakai 2FA hardware key, disimpan di salinan tersegel, diaudit lewat GitHub audit log.

## 7. Pewarisan / pergantian Superadmin
Superadmin baru ditambahkan **sebelum** yang lama keluar: tambah ke grup host + reviewer Environment + admin repo; serah terima salinan tersegel (berita acara); cabut akses yang lama (grup, sudo, GitHub, WireGuard peer); rotasi passphrase root CA (`openssl pkey -in root.key -aes-256-cbc -out root.key.new`) dan secret yang pernah dilihat.
Dengan 1 Superadmin (keputusan 10 Okt 2026) serah terima memakai paket brankas (§8): paket baru dibuat oleh Superadmin
baru (§8.4), paket lama dimusnahkan disaksikan.

## 8. Paket segel brankas Direksi — 1 Superadmin (W-28, R-B11) {#brankas-direksi}

Keputusan pemilik proyek 10 Okt 2026 (Q-E1a/b/c): **1 Superadmin**, salinan offline **tersegel di brankas Direksi**,
**tanpa Shamir**; bus factor 1 diterima (R-B11, ADR-022 §Kustodi). Bagian ini membuat mitigasi yang tersisa bisa
dijalankan oleh orang lain **hanya dengan runbook ini + isi brankas**. Butir *Rekomendasi* = saran konsultan, bukan
keputusan.

> **Ringkas untuk manajemen.** Semua kunci yang bila hilang berarti data hilang disimpan dalam satu paket terenkripsi
> bersegel di brankas Direksi. Kata sandi pembukanya ada di amplop terpisah. Setiap pembukaan brankas dicatat dan
> disaksikan. Dua kali setahun paket diuji dengan benar-benar memulihkan backup, disaksikan Direksi/Internal Audit.
> Bila Superadmin tidak tersedia, Direksi menunjuk pengganti sementara dan membuka paket bersama saksi.

### 8.1 Isi paket

**Paket A — media terenkripsi** (2 flashdisk berbeda merek berisi arsip yang sama, mitigasi media rusak):
arsip `efn-vault-<YYYYMMDD>.tar.age` dienkripsi **passphrase P-A** (≥ 6 kata diceware). Isi:

| Isi | Sumber | Catatan |
|---|---|---|
| `~/efn-pki-root/` (`root.key` terenkripsi **passphrase root**, `root.crt`) | mesin offline §5 | Passphrase root **tidak** di paket A (lapis kedua) |
| `pii_encryption_key*` (semua versi key ring yang masih dipakai data) | GitHub Environment `production` | Hilang = PII tidak terbaca permanen |
| `odoo_pii_key_v<n>` (semua versi yang masih dipakai data), `odoo_pii_bidx_key`, `odoo_pii_name_key` (G21 AC-13) | **host** `/opt/efn/secrets` ([§4e](#pii-odoo)), per lingkungan | Hilang key ring = PII Odoo tidak terbaca permanen; wajib masuk paket **sebelum** `EFN_ODOO_PII_ENABLED=true` |
| `walg_libsodium_key` (+ versi lama selama backup lama dalam retensi) | idem | Hilang = backup tidak bisa dipulihkan |
| `minio_kms_key`, `clickhouse_disk_key`, `metabase_encryption_key` | idem | |
| `otp_pepper`, `efn_audit_sink_key` | idem | `member_hash_pepper` diturunkan ulang dari `otp_pepper` |
| Passphrase pemulihan LUKS per node + `cryptsetup luksHeaderBackup` per node | host ([luks-disk.md](luks-disk.md)) | Tanpa header backup, header rusak = disk hilang |
| Kode pemulihan 2FA akun GitHub Superadmin/owner organisasi, kunci SSH break-glass host | GitHub / host | Akses ke Environment & host tanpa Superadmin |
| Ciphertext kanari PII + SHA-256 plaintext-nya (§8.5 butir 5) | dibuat saat pengemasan | Bukan data pribadi nyata |
| `register-kunci.sha256`: nama, versi, **SHA-256 tiap file kunci** (bukan nilai), sidik jari root CA, nomor segel | dibuat saat pengemasan | Juga dicetak di berita acara |

**Tidak** dimasukkan (dapat dibuat ulang tanpa kehilangan data): kunci intermediate CA (terbitkan ulang dari root),
sertifikat leaf, password DB/broker per service (rotasi lewat deploy), API key Odoo kanal online (rotasi otomatis 30
hari, [odoo-online-api-key.md](odoo-online-api-key.md)), token payment/WhatsApp/ACME (terbitkan ulang di vendor).

**Amplop B — passphrase** (kertas, ditulis tangan / dicetak dari mesin offline): P-A dan passphrase root CA, **disegel
terpisah** dengan nomor segel berbeda. Default (keputusan: satu lokasi): amplop B di brankas yang sama, kompartemen
terpisah. *Rekomendasi* (biaya ±Rp0–1 jt/tahun): simpan amplop B di lokasi kedua (direktur lain / safe deposit bank /
notaris) agar satu pembobolan brankas tidak membuka semuanya — perlu persetujuan Direksi.

### 8.2 Membuat & menyegel paket (Superadmin, mesin offline §5, disaksikan 1 anggota Direksi)

```bash
# mesin offline (LUKS, tanpa jaringan). Alat open source: age (alternatif: gpg --symmetric --cipher-algo AES256)
umask 077; mkdir -p ~/vault && cd ~/vault        # salin file kunci dari sumbernya (bukan lewat chat/email)
sha256sum -- * > register-kunci.sha256            # hash tiap file -> register (nilai TIDAK dicetak)
tar -cf - . | age -p -o /media/usb1/efn-vault-$(date +%Y%m%d).tar.age   # passphrase P-A (diketik, tidak disimpan)
cp /media/usb1/efn-vault-*.tar.age /media/usb2/ && sha256sum /media/usb*/efn-vault-*.tar.age
age -d /media/usb2/efn-vault-*.tar.age | tar -tf - > /dev/null && echo "arsip dapat dibuka"   # uji sebelum segel
cd ~ && find ~/vault -type f -exec shred -u {} + && rmdir ~/vault
```

1. Flashdisk 1+2 + cetakan `register-kunci.sha256` → **kantong segel tamper-evident bernomor seri** (Paket A). Amplop B
   → kantong segel kedua. Foto kedua kantong (nomor seri terlihat).
2. **Berita acara** (2 rangkap, tanda tangan Superadmin + saksi Direksi): tanggal, nomor segel A/B, SHA-256 arsip,
   daftar isi (nama + versi kunci, **tanpa nilai**), alasan (pembuatan awal / pembaruan / pasca break-glass).
3. Masukkan kedua kantong ke brankas Direksi, isi **log brankas** (§8.3). Rangkap kedua berita acara disimpan Internal
   Audit.

### 8.3 Log akses brankas

Buku log fisik di samping brankas + salinan di register Internal Audit (di luar repo). Setiap pembukaan:

| Tanggal & jam | Pembuka (nama, jabatan) | Saksi | Alasan (uji / pembaruan / break-glass / pemeriksaan) | No. segel sebelum | Utuh? | No. segel sesudah | Paraf |
|---|---|---|---|---|---|---|---|

- Pembukaan **selalu dua orang** (pemegang brankas Direksi + saksi). Superadmin sendirian tidak membuka brankas.
- **Pemeriksaan segel kuartalan** oleh Internal Audit (tanpa membuka kantong): nomor seri = log terakhir, kantong utuh.
  Nomor tidak cocok / kantong rusak = **insiden keamanan** ([security-incident.md](security-incident.md)): anggap semua
  kunci di paket bocor → rotasi semua yang dapat dirotasi ([secret-rotation.md](secret-rotation.md)), paket baru.

### 8.4 Pembaruan paket

Buat paket baru (§8.2) dan **musnahkan** paket lama disaksikan (hancurkan fisik flashdisk, catat di log) bila: kunci
di §8.1 dirotasi/ditambah (versi `pii_encryption_key` baru, node LUKS baru, rotasi `walg_libsodium_key` — versi lama
tetap disimpan selama backup lama dalam retensi), passphrase root diganti, pergantian Superadmin (§7), atau setelah
break-glass/uji yang membuka segel.

### 8.5 Uji pemulihan disaksikan (2×/tahun, R-B11) {#uji-pemulihan}

Tujuan: membuktikan paket **lengkap dan dapat dipakai oleh orang selain Superadmin**. *Rekomendasi*: langkah teknis
dijalankan staf IT lain (Superadmin hanya mengamati) — menguji mitigasi bus factor yang sebenarnya. Disaksikan Direksi
+ Internal Audit; hasil per butir (lulus/gagal) masuk berita acara.

1. Buka brankas (§8.3), periksa nomor segel, buka kantong A dan B.
2. Di mesin offline: dekripsi arsip dari flashdisk 1 atau 2 (bergantian tiap semester);
   `sha256sum -c register-kunci.sha256` → semua `OK`.
3. Root CA: `openssl pkey -in root.key -noout` dengan passphrase root; sidik jari `root.crt` = `EFN_PKI_ROOT_SHA256`
   di `/etc/efn/deploy.conf` host.
4. Backup: restore test ([backup-restore.md](backup-restore.md)) memakai `walg_libsodium_key` **dari paket** (bukan
   dari `/opt/efn/secrets`) ke container sementara → verifikasi lulus.
5. PII: dekripsi ciphertext kanari dengan `pii_encryption_key` paket → SHA-256 plaintext cocok. G21: `sha256sum` file
   `odoo_pii_*` paket = hash di register **dan** = `sha256sum /opt/efn/secrets/odoo_pii_*.txt` di host (dicocokkan di
   host oleh Superadmin; nilai tidak dibawa keluar). *Rekomendasi*: kanari Odoo (`efnpii2:` satu partner uji)
   didekripsi dengan kunci paket memakai `efn_pii/tools` di container sekali pakai.
6. LUKS: `cryptsetup luksOpen --test-passphrase <header-backup>` dengan passphrase paket, untuk tiap node.
7. GitHub: kode pemulihan masih berlaku (cek jumlah tersisa di pengaturan akun, tanpa memakainya).
8. Hancurkan salinan kerja di mesin offline (`shred`), segel ulang dengan **kantong baru** (nomor baru), log brankas,
   berita acara. Butir gagal → perbaiki ≤ 5 hari kerja + paket baru (§8.4).

### 8.6 Superadmin tidak tersedia {#superadmin-tidak-tersedia}

**Pemicu** (diputuskan Direksi, dicatat): insiden produksi P1 dan Superadmin tidak terjangkau dalam **4 jam** (selaras
RTO 4 jam, R-T08); sakit/cuti panjang > 2 minggu; atau berhenti/meninggal.

1. Direksi menunjuk **Superadmin sementara** (default Head IT) secara tertulis.
2. Buka brankas dua orang (§8.3); setiap pembukaan break-glass diberitahukan ke seluruh Direksi + Internal Audit pada
   hari yang sama (tidak ada pembukaan diam-diam).
3. Ambil **seminimal mungkin** (mis. hanya `walg_libsodium_key` untuk restore; kode pemulihan GitHub hanya bila perlu
   mengubah Environment). Akses host: kunci SSH break-glass → `sudo` (grup `efn-superadmin`).
4. Pulihkan layanan dengan runbook terkait ([backup-restore.md](backup-restore.md), [luks-disk.md](luks-disk.md),
   [internal-pki.md](internal-pki.md), [odoo-online-api-key.md](odoo-online-api-key.md)).
5. Setelah stabil (≤ 5 hari kerja): bila Superadmin lama tidak kembali → §7; rotasi semua kunci yang tersentuh dan
   yang pernah dilihat pemegang sementara; paket baru (§8.4); review insiden.

### 8.7 Opsi masa depan: Shamir (tidak diwajibkan)

Shamir *secret sharing* (mis. 2-dari-3: Superadmin, Direksi, Internal Audit/notaris): tidak ada satu pihak yang bisa
membuka paket sendiri, dan satu pihak hilang tidak menghilangkan kunci. Alat open source tersedia (mis. `ssss`).
**Tidak dipilih** pemilik proyek (10 Okt 2026). *Rekomendasi*: tinjau ulang sebelum go-live publik, saat Superadmin
cuti panjang, atau bila auditor eksternal mensyaratkan pemisahan kendali. Peralihan cukup mengganti amplop B
(passphrase P-A) dengan share Shamir — paket A tidak berubah.

### 8.8 Belum

- Nama Superadmin, pemegang brankas Direksi, dan saksi belum ditetapkan (G-3); kantong segel bernomor belum diadakan.
- Kunci SSH break-glass host dan ciphertext kanari belum dibuat (dibuat saat pengemasan pertama, setelah server ada).
- Uji pemulihan pertama: setelah staging/produksi tersedia (ADR-004).
