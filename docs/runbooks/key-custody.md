# Prosedur: Kustodi kunci (peran Superadmin)

Kontrak: `docs/04_SERVICE_CONTRACTS.md` §8.6 (keputusan user: **kunci hanya dipegang Superadmin**), ADR-022 (manajemen kunci). Alat: `make pki-root`, `make pki-intermediate`, `scripts/deploy.sh`, `scripts/derive-secrets.sh`.

> **Ringkas untuk manajemen.** Kunci yang bila hilang/bocor berarti kehilangan data atau pemalsuan identitas sistem hanya dipegang peran **Superadmin**. Server dan pipeline CI tidak pernah memegang kunci induk (root CA). Butir bertanda *Rekomendasi* adalah saran konsultan, **bukan keputusan** — perlu disetujui klien.

## 1. Kunci di bawah kustodi Superadmin

| Kunci | Bentuk | Lokasi kerja | Salinan cadangan | Bila hilang |
|---|---|---|---|---|
| Root CA (`root.key`) | ECDSA P-256, terenkripsi passphrase | **Offline** (media terenkripsi Superadmin). Tidak pernah di server/CI | 2 salinan tersegel (*rekomendasi*) | Tidak bisa menerbitkan intermediate → root baru + distribusi bundle |
| Intermediate CA per lingkungan | ECDSA P-256 | GitHub Environment `EFN_PKI_INTERMEDIATE_KEY` → tmpfs host **hanya selama deploy** (`/dev/shm`, dihancurkan) | Tidak perlu (terbitkan ulang dari root) | Terbitkan intermediate baru |
| `pii_encryption_key*` | 32 byte | GitHub Environment → `/opt/efn/secrets` → BFF | **Wajib** offline | Data PII tidak terbaca permanen |
| `walg_libsodium_key` | 32 byte | idem → postgres | **Wajib** offline | Backup tidak bisa dipulihkan |
| `minio_kms_key` | 32 byte | idem → MinIO | **Wajib** offline | Objek MinIO tidak terbaca |
| `clickhouse_disk_key` | hex | idem → ClickHouse | Disarankan | Rebuild DWH dari PostgreSQL |
| `otp_pepper` → `member_hash_pepper` | random; turunan HKDF | `otp_pepper` di GitHub Environment; `member_hash_pepper` **diturunkan di host** (`derive-secrets.sh`), tidak disimpan di GitHub | **Wajib** offline (`otp_pepper`) | Semua `member_hash` berubah → ledger/DSAR tidak tersambung |
| Passphrase LUKS per node | passphrase | Keyslot disk | **Wajib** offline | Disk tidak terbuka → restore |

Secret operasional lain (password service, token WhatsApp, kunci API payment, token DNS ACME) dikelola lewat GitHub Environment yang sama; perubahannya juga butuh approval Superadmin (§3).

## 2. Peran

- **Superadmin**: satu-satunya yang (a) mengelola secret & reviewer GitHub Environment `production` (dan `staging`), (b) anggota grup host `efn-superadmin` dengan sudo, (c) memegang root CA offline & salinan tersegel, (d) menyetujui deploy produksi.
- **Ops/IT (non-superadmin)**: akses SSH tanpa sudo dan **bukan** anggota grup `docker` (grup docker setara root). Melihat log lewat Grafana/Loki (`ops.`), bukan host.
- *Rekomendasi (bukan keputusan)*: **minimal 2 orang** berperan Superadmin (mencegah *single point of failure* manusia; satu orang sakit/keluar = kunci tidak dapat diakses). Akses ke salinan tersegel memakai prinsip dua orang.

## 3. GitHub

1. Settings → Environments → `production`: **Required reviewers = Superadmin** (semua orang Superadmin; centang "Prevent self-review"). **Deployment branches and tags = Selected**: hanya tag `v*` (SEC-30). `staging`: hanya branch `main`.
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
| `IMAGE_*` | dikirim lewat env (tidak lagi di allowlist); di manifest bukan `${EFN_IMAGE_PREFIX}<svc>@sha256:<64 hex>`; `cosign verify` **di host** gagal untuk identitas `build-push.yml@<signer_ref>` |
| Trust anchor | `EFN_PKI_ROOT_CRT` ≠ tepat 1 sertifikat atau sidik jari ≠ `EFN_PKI_ROOT_SHA256`; root di manifest ≠ pin; intermediate tidak diterbitkan root yang dipin / bukan CA; setiap sertifikat `EFN_PKI_EXTRA_TRUST` tidak ada di `EFN_PKI_EXTRA_TRUST_SHA256` **dan** manifest |
| Lingkungan | manifest `app_env` ≠ `EFN_APP_ENV`; env `APP_ENV` ≠ `EFN_APP_ENV`; production bukan `X.Y.Z` dari `refs/tags/vX.Y.Z` |
| Anti-replay/downgrade | `seq` < `seq` terakhir di `/var/lib/efn/last-release` (root 0600); `seq` sama tetapi isi berbeda; production: versi semver turun. Rilis identik (sha256 sama) boleh di-deploy ulang |

Tidak ada yang ditulis ke `/opt/efn` sebelum semua cek lolos. Uji regresi: `scripts/host/test-efn-deploy.sh`
(cosign palsu, 32 kasus; CI job `infra`).

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
3. Salin `~/efn-pki-root` ke 2 media terenkripsi, segel, simpan terpisah (*rekomendasi*: brankas kantor + bank). Catat sidik jari SHA-256 root di register kunci.
4. Intermediate berlaku 2 tahun (alert metrik `efn_ca_crt_0`); terbitkan ulang ≥ 60 hari sebelum kedaluwarsa. Menambah service baru di `PKI_SERVICES` juga butuh intermediate baru (nameConstraints).

## 6. Break-glass (Superadmin tidak tersedia, insiden produksi)

Hanya bila ada insiden aktif yang mengancam layanan/data dan **tidak ada** Superadmin yang dapat dihubungi dalam waktu yang ditetapkan klien.
1. Dua pejabat yang ditunjuk klien (mis. Direktur + Head IT) membuka salinan tersegel bersama-sama; catat waktu, alasan, saksi di register.
2. Gunakan akses minimum (mis. hanya `walg_libsodium_key` untuk restore).
3. Setelah insiden: rotasi semua secret yang tersentuh ([secret-rotation.md](secret-rotation.md)), segel ulang, review dalam 5 hari kerja.
4. Akun break-glass GitHub (bila ada) memakai 2FA hardware key, disimpan di salinan tersegel, diaudit lewat GitHub audit log.

## 7. Pewarisan / pergantian Superadmin
Superadmin baru ditambahkan **sebelum** yang lama keluar: tambah ke grup host + reviewer Environment + admin repo; serah terima salinan tersegel (berita acara); cabut akses yang lama (grup, sudo, GitHub, WireGuard peer); rotasi passphrase root CA (`openssl pkey -in root.key -aes-256-cbc -out root.key.new`) dan secret yang pernah dilihat.
