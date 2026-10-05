# Runbook: Sertifikat TLS akan kedaluwarsa

Alert: `EfnCertExpiringSoon` (< 14 hari), `EfnCertExpiryCritical` (< 3 hari). Sumber: blackbox-exporter `probe_ssl_earliest_cert_expiry` terhadap `infra/observability/targets/blackbox-https.yml` (domain diganti `TRAEFIK_DOMAIN` saat deploy).

## Dampak
Browser/app menolak `app.`, `api.`, `erp.`, `mart.`, `kitchen.` → Super-App, POS online (Odoo), dan integrasi webhook gagal.

## Diagnosis
1. Sertifikat mana yang dilihat publik? Bila Cloudflare proxy aktif, yang di-probe adalah **sertifikat edge Cloudflare** (dikelola Cloudflare). Sertifikat origin (Traefik/Let's Encrypt) diperiksa langsung:
   ```bash
   echo | openssl s_client -connect <ip-origin>:443 -servername api.<domain> 2>/dev/null | openssl x509 -noout -dates -issuer
   ```
2. Log ACME Traefik: `$DC logs traefik | grep -i -E 'acme|certificate|challenge'`.
3. Penyebab umum renewal Let's Encrypt gagal:
   - HTTP-01 butuh port 80 terjangkau dari internet → firewall/Cloudflare "Always Use HTTPS" memblokir `/.well-known/acme-challenge/`.
   - Rate limit Let's Encrypt (terlalu banyak percobaan) → tunggu, jangan restart berulang.
   - `traefik-acme` volume hilang/terkorupsi (`/acme/acme.json` harus mode 600).
   - Email ACME placeholder (`ops@example.com`) → set `vars.ACME_EMAIL`.

## Pemulihan
- Perbaiki penyebab lalu `$DC restart traefik` (renewal dicoba saat start dan berkala).
- Darurat: pasang sertifikat Cloudflare Origin CA sebagai sertifikat default Traefik lewat file dynamic config (`tls.stores.default.defaultCertificate`) dengan mode Cloudflare "Full (strict)".
- Pertimbangkan DNS-01 challenge (Cloudflare API token sebagai Docker secret baru) bila port 80 tidak bisa dibuka.
