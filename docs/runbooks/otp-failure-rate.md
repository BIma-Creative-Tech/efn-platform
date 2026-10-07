# Runbook: Kegagalan OTP / WhatsApp tinggi

Alert: `EfnOtpVerifyFailureRateHigh` (> 40% verifikasi gagal, min. 20 percobaan/15 menit), `EfnOtpSendFailureRateHigh` (> 10% kirim WhatsApp gagal).

> **Status metrik: DIUSULKAN.** BFF belum mengekspos metrik ini; rule sudah aktif tetapi tidak akan pernah firing sampai BFF menambahkan (prom-client di `/metrics`):
> - `efn_otp_request_total{purpose="pos_promo|login", outcome="sent|delivery_failed|rate_limited"}`
> - `efn_otp_verify_total{purpose, outcome="verified|invalid|expired|locked"}`
> - `efn_whatsapp_send_total{outcome="success|failure"}`
> Label tidak boleh memuat nomor HP / member_ref (kardinalitas & UU PDP).

## Dampak
- Pengiriman gagal: pelanggan tidak menerima OTP → promo ber-OTP di POS dan login Super-App gagal. Kasir tetap bisa menjual tanpa promo.
- Verifikasi gagal tinggi: OTP terlambat sampai (kedaluwarsa 180 s), salah ketik massal, atau **brute force**.

## Diagnosis
1. Loki: `{service="api-gateway"} |= "otp"` — nomor HP sudah di-mask di log (Promtail juga meredaksi).
2. WhatsApp Cloud API:
   - Token kedaluwarsa/dicabut (HTTP 401/190) → [secret-rotation.md](secret-rotation.md) bagian `whatsapp_token`.
   - Template `efn_otp` ditolak/di-pause Meta, quality rating turun, atau limit tier pesan tercapai → cek WhatsApp Manager.
   - `WHATSAPP_ENABLED=false` di prod secara tidak sengaja (OTP hanya ke console) → cek `vars.WHATSAPP_ENABLED` environment.
   - Egress: api-gateway harus di network `egress`; uji `docker exec <api> node -e "fetch('https://graph.facebook.com').then(r=>console.log(r.status))"`.
3. Brute force: lonjakan `outcome="invalid"` dari sedikit IP/pos_config → audit `platform.otp_audit` (subject = hash):
   ```sql
   SELECT date_trunc('minute', created_at) m, outcome, count(*) FROM platform.otp_audit
   WHERE created_at > now() - interval '1 hour' GROUP BY 1,2 ORDER BY 1 DESC;
   ```
4. Jam server: `timedatectl` (NTP) — OTP berbasis TTL Redis, tetapi discount token JWT memakai `exp`.

## Pemulihan
- Token: rotasi `whatsapp_token`, deploy ulang.
- Abuse: perketat `ratelimit-api` (Traefik) atau blokir IP di Cloudflare; rate limit per member_ref di BFF tetap berlaku.
- Jangan menonaktifkan OTP gate di Odoo tanpa persetujuan bisnis (`efn.pos.otp_required_for_promo`).

## ALTCHA level 3

Alert: `EfnAltchaMaxLevelSpike` — `efn_altcha_challenge_total{level="3"}` naik > 50 dalam 15 menit (SEC-34, ALTCHA
adaptif BFF, `services/api-gateway/src/auth/README.md`). Level 3 = biaya proof-of-work maksimum: rate limit per IP
atau anggaran OTP global (`OTP_GLOBAL_HOURLY_BUDGET`) hampir habis. Metrik tanpa IP.

1. Cek anggaran global & biaya: `sum(increase(efn_otp_request_total[1h]))`, WhatsApp Manager (OTP pumping = biaya per
   pesan).
2. Loki: `{service="api-gateway"} |= "altcha"`; IP sumber hanya di access log Traefik (`{service="traefik"}`,
   path `/v1/auth/`). IP = data pribadi: jangan disalin ke tiket publik.
3. Serangan terkonfirmasi: aturan WAF/rate limit Cloudflare untuk path OTP; turunkan `OTP_GLOBAL_HOURLY_BUDGET`
   sementara. Dugaan kompromi akun → [security-incident.md](security-incident.md).
4. Lonjakan sah (kampanye marketing) → catat di tiket, tanpa tindakan.
