# Runbook: token DNS ACME (DNS-01) dengan cakupan minimum — SEC-37

Konteks: entrypoint `wg-https` (ADR-023: `erp.`, `bi.`, `ops.`, `status.`) memakai sertifikat Let's Encrypt lewat
DNS-01. Risiko: R-S03. Temuan: SEC-37 (review r3, Low/PLAUSIBLE).

**Status gelombang 12: token KELUAR dari Traefik (§3 diterapkan).** Service `acme-dns` (lego resmi, image dipin
digest, skrip `infra/acme/acme-dns.sh`) adalah satu-satunya pemegang secret `acme_dns_api_token`: tanpa port, hanya
network `egress`, non-root 65532, read-only, `cap_drop: ALL`. Traefik tidak lagi me-mount token maupun resolver
`letsencrypt-dns` (dicek CI: langkah "Traefik tidak memegang token DNS"). Kontrol §1 (delegasi zona + token terbatas)
**tetap wajib**: pemisahan mengurangi dampak RCE Traefik, cakupan token mengurangi dampak kompromi `acme-dns`/host.

## 1. Wajib sebelum staging publik: delegasi `_acme-challenge` ke zona khusus

Tujuan: token hanya bisa mengubah record TXT tantangan ACME, bukan `api.`/`app.`/MX.

1. Buat zona DNS terpisah khusus ACME, mis. `acme.<domain>` sebagai zona Cloudflare sendiri (subdomain zone, butuh
   paket yang mendukung) **atau** domain murah terpisah (mis. `efn-acme.net`). Catat pemiliknya = Superadmin.
2. Di zona utama, buat CNAME statis (dibuat sekali oleh Superadmin dengan akun manusia, bukan token):
   ```
   _acme-challenge.erp.<domain>     CNAME  erp.acme.<domain>.
   _acme-challenge.bi.<domain>      CNAME  bi.acme.<domain>.
   _acme-challenge.ops.<domain>     CNAME  ops.acme.<domain>.
   _acme-challenge.status.<domain>  CNAME  status.acme.<domain>.
   ```
   lego (service `acme-dns`) mengikuti CNAME `_acme-challenge` secara default (jangan set
   `LEGO_DISABLE_CNAME_SUPPORT=true`).
3. Token Cloudflare (`My Profile → API Tokens → Create Custom Token`):
   - Permissions: `Zone → DNS → Edit` **dan** `Zone → Zone → Read`.
   - Zone Resources: `Include → Specific zone → acme.<domain>` (BUKAN "All zones", BUKAN zona utama).
   - Client IP Address Filtering: IP egress publik host (container `acme-dns` keluar lewat network `egress` = NAT host) saja.
   - TTL: tanggal kedaluwarsa (mis. 12 bulan) + pengingat rotasi di kalender Superadmin.
4. Simpan ke GitHub Environment `ACME_DNS_API_TOKEN` (hanya Superadmin). `deploy.sh` menolak nilai placeholder.
5. Verifikasi cakupan (dari mesin Superadmin, bukan host):
   ```bash
   curl -s -H "Authorization: Bearer $TOKEN" https://api.cloudflare.com/client/v4/user/tokens/verify | jq .result.status
   # Harus GAGAL (403/permission) mengubah zona utama:
   curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     "https://api.cloudflare.com/client/v4/zones/<ZONE_ID_UTAMA>/dns_records" \
     -d '{"type":"TXT","name":"sec37-test","content":"x","ttl":60}' | jq .success   # false
   ```

Bila delegasi belum mungkin: minimal token dibatasi ke **satu** zona utama, hanya `DNS:Edit`, dengan IP filtering,
dan risiko residu dicatat di risk register (lead).

## 2. Nama host internal di Certificate Transparency (Info)

Sertifikat per host (`erp.`, `bi.`, ...) tercatat publik di CT log. Opsi: satu sertifikat wildcard `*.int.<domain>`
(host menjadi `erp.int.<domain>` dst.) sehingga hanya `*.int` yang terlihat. Butuh perubahan label router Traefik +
DNS internal + konfigurasi POS outlet → keputusan lead/architect (ADR-023), belum diterapkan.

## 3. Arsitektur (diterapkan gelombang 12): token keluar dari Traefik

```
acme-dns (lego, egress saja, tanpa port, token)  --DNS-01-->  API DNS + Let's Encrypt
   |  /data  = volume acme-dns-data  (0700: kunci akun ACME + sertifikat asli; HANYA acme-dns)
   |  /certs = volume acme-dns-certs (0750 grup 0): efn-wg-<sha16>.crt/.key (0640) + acme-dns.yml
   v
Traefik: acme-dns-certs di-mount READ-ONLY SEBAGAI /etc/traefik/dynamic (direktori provider file yang di-watch);
         konfigurasi statis di subdirektori base/ (infra/traefik/dynamic) dan dev/ (override dev).
```

- `acme-dns-init` (one-shot, root dengan CAP_CHOWN+FOWNER saja, `network_mode: none`): chown volume ke 65532:0 dan
  membuat titik mount `base/`, `dev/` (Docker tidak bisa membuat titik mount di volume read-only). Traefik
  `depends_on` init ini.
- `acme-dns` (`infra/acme/acme-dns.sh`): `lego run` tiap 12 jam (v5: terbit bila belum ada, perpanjang bila jatuh
  tempo/ARI; gagal → coba lagi 1 jam). Satu sertifikat SAN untuk keempat host (`--force-cert-domains`, EC256).
  Sertifikat baru → salin dengan nama berversi + tulis ulang `acme-dns.yml` atomik (tmp + mv) → Traefik memuat ulang
  tanpa restart; file versi lama dihapus.
- Alasan teknis (diuji, bukan asumsi): (a) watch provider file Traefik (fsnotify) **tidak rekursif** — file di
  subdirektori volume tidak memicu reload (uji gagal), maka volume = direktori puncak; (b) Traefik tidak memuat ulang
  isi sertifikat bila path-nya sama, maka nama file berversi; (c) kunci 0640 grup 0 karena Traefik = root tanpa
  CAP_DAC_* (dibaca lewat bit grup), `acme-dns` non-root.
- Token: `<ACME_DNS_TOKEN_ENV>_FILE=/run/secrets/acme_dns_api_token` (lego membaca `_FILE`; diuji). Tidak pernah di
  env/argv/log.
- Dev/CI: token `disabled` → `acme-dns` idle dan sehat (tanpa request ACME). `deploy.sh` menolak `disabled` di
  staging/prod.
- Konfigurasi deploy (GitHub Environment vars): `ACME_EMAIL`, `ACME_DNS_PROVIDER` (default `cloudflare`),
  `ACME_DNS_TOKEN_ENV` (default `CF_DNS_API_TOKEN`), `ACME_SERVER` (kosong = LE produksi; hanya URL LE produksi/staging
  yang diterima `deploy.sh`).

## 4. Uji

- **Otomatis (CI job `infra`, juga lokal):** `bash scripts/test-acme-dns.sh` — Pebble + pebble-challtestsrv (server
  ACME/DNS uji open source Let's Encrypt, image dipin digest) di network Docker `--internal` sekali pakai, tanpa
  domain/token/internet. Membuktikan: penerbitan DNS-01, Traefik (image & digest compose, read-only, cap_drop ALL,
  tanpa token) menyajikan sertifikat via SNI, perpanjangan paksa → Traefik menyajikan sertifikat baru **tanpa restart**,
  versi lama dihapus, izin kunci 0640 65532:0, `/data` 0700 dan akun ACME tidak di volume certs, mode nonaktif tidak
  menghubungi CA, lego membaca `CF_DNS_API_TOKEN_FILE`, konfigurasi `base/` tetap dimuat. Hasil gel. 12: 10/10 PASS.
- CI juga memeriksa compose prod: Traefik tanpa secret/env token, `acme-dns` tanpa port dan hanya network `egress`.
- **Uji perdana staging (Superadmin, sebelum produksi):** set `ACME_SERVER=https://acme-staging-v02.api.letsencrypt.org/directory`
  → deploy → `$DC logs acme-dns` harus menunjukkan `sertifikat dipasang untuk Traefik` →
  `openssl s_client -connect <IP WG>:443 -servername erp.<domain> </dev/null | openssl x509 -noout -issuer` = issuer
  `(STAGING)`. Lalu kosongkan `ACME_SERVER`, hapus volume data agar akun/sertifikat produksi terbit bersih:
  `$DC stop acme-dns && docker volume rm efn_acme-dns-data && $DC up -d acme-dns` (volume certs dibiarkan; sertifikat
  staging tetap tersaji sampai sertifikat produksi menggantikannya — beberapa menit).
- Belum diuji dengan Cloudflare/Let's Encrypt nyata (tidak ada domain/token di lingkungan penulis).

## 5. Operasi service acme-dns

- Status: `$DC ps acme-dns` (healthcheck: sukses `lego run` < 25 jam, atau mode nonaktif). Log:
  `$DC logs --tail=100 acme-dns` (tanpa token).
- Alert: `EfnWgCertProbeFailed` (probe `traefik:8443` SNI `erp.` gagal diverifikasi CA publik ≥ 1 jam — mis.
  penerbitan pertama gagal) dan `EfnCertExpiringSoon/Critical` (job `probe-wg-tls`, [tls-cert-expiry.md](tls-cert-expiry.md)).
- Perpanjang paksa: `$DC run --rm -e ACME_ONESHOT=1 -e ACME_EXTRA_ARGS="--renew-force --no-random-sleep" acme-dns`
  (rate limit LE: 5 sertifikat duplikat/minggu).
- Penyebab gagal umum: token salah cakupan (403 dari API DNS — cek §1), CNAME `_acme-challenge` belum ada/salah,
  egress UDP 53 ke resolver propagasi (1.1.1.1/9.9.9.9) diblok firewall, rate limit LE.
- Migrasi dari Traefik DNS-01 (sekali): setelah deploy pertama gel. 12 berhasil, `acme-dns.json` lama di volume
  `traefik-acme` tidak dipakai lagi (boleh dihapus; `acme.json` HTTP-01 host publik tetap).

## 6. Rotasi / insiden

- Rotasi terjadwal: buat token baru (cakupan sama) → perbarui secret Environment → deploy → cabut token lama.
- Dugaan kompromi host atau container `acme-dns` (Traefik tidak lagi memegang token): cabut token di Cloudflare **segera**, periksa audit log Cloudflare (perubahan DNS) dan CT
  log (crt.sh) untuk sertifikat yang tidak dikenal pada domain, terbitkan token baru setelah host bersih.
