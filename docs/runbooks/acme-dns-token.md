# Runbook: token DNS ACME (DNS-01) dengan cakupan minimum — SEC-37

Konteks: entrypoint `wg-https` (ADR-023: `erp.`, `bi.`, `ops.`, `status.`) memakai sertifikat Let's Encrypt lewat
DNS-01 (`certificatesResolvers.letsencrypt-dns` di `infra/traefik/traefik.yml`). Token API DNS (secret
`acme_dns_api_token`) di-mount ke **Traefik**, yaitu komponen yang menghadap internet. Bila token dapat mengedit zona
utama, RCE di Traefik = pembajakan DNS `api.`/`app.` (phishing, intersepsi OTP). Risiko: R-S03. Temuan: SEC-37
(review r3, Low/PLAUSIBLE). Status gelombang 9: **kontrol prosedural + desain**, belum ada perubahan arsitektur.

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
   Lego (pustaka ACME di Traefik) mengikuti CNAME `_acme-challenge` secara default (jangan set
   `LEGO_DISABLE_CNAME_SUPPORT=true`).
3. Token Cloudflare (`My Profile → API Tokens → Create Custom Token`):
   - Permissions: `Zone → DNS → Edit` **dan** `Zone → Zone → Read`.
   - Zone Resources: `Include → Specific zone → acme.<domain>` (BUKAN "All zones", BUKAN zona utama).
   - Client IP Address Filtering: IP egress publik host Traefik saja.
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

## 3. Desain lanjutan (belum diterapkan): token keluar dari Traefik

Usulan: komponen ACME terpisah yang tidak menghadap internet — container `acme-dns-01` (lego CLI, image di-pin
digest) di network `egress` saja, tanpa port, menjalankan `lego renew` terjadwal dan menulis sertifikat ke volume
`wg-certs`; Traefik membaca sertifikat itu lewat provider file (`tls.certificates`) dan **tidak** lagi me-mount
`acme_dns_api_token`. Catatan teknis yang harus diuji sebelum diterapkan: (a) Traefik memuat ulang sertifikat
provider file hanya saat file konfigurasi dinamis berubah → hook pasca-renew harus menyentuh file dinamis;
(b) kunci privat sertifikat ada di volume bersama (mode 0600, uid Traefik); (c) alert kedaluwarsa memakai
`probe-https` yang sudah ada. Estimasi ±1–2 hari devsecops. Dengan delegasi §1, risiko yang tersisa setelah RCE Traefik
terbatas pada penerbitan sertifikat untuk nama yang didelegasikan (bukan pembajakan `api.`/`app.`).

## 4. Rotasi / insiden

- Rotasi terjadwal: buat token baru (cakupan sama) → perbarui secret Environment → deploy → cabut token lama.
- Dugaan kompromi Traefik: cabut token di Cloudflare **segera**, periksa audit log Cloudflare (perubahan DNS) dan CT
  log (crt.sh) untuk sertifikat yang tidak dikenal pada domain, terbitkan token baru setelah host bersih.
