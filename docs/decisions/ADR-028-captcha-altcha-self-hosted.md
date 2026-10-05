# ADR-028: Captcha OTP = ALTCHA (Open Source, Self-Hosted)

- **Status:** Proposed — pilihan ALTCHA sudah dipakai di kontrak `docs/04 §8.1` (keputusan lead, 6 Okt 2026); tanda tangan klien untuk pemilihan & alasan PDP.
- **Tanggal:** 2026-10-06
- **Pemutus (klien):** IT + Legal/DPO
- **Referensi review:** SEC-06 (Partially fixed — captcha belum), R-L01, R-B02 (biaya WA OTP)
- **Terkait:** ADR-011 (WhatsApp OTP berbayar per pesan), `docs/04 §8.1`, `docs/compliance/PDP_TRANSFER_LINTAS_NEGARA.md`

> **Ringkasan manajemen.** Agar bot tidak memicu ribuan OTP WhatsApp berbayar ke nomor korban, permintaan OTP wajib lolos "captcha". Kami memilih **ALTCHA**: open source, berjalan di server sendiri, tidak mengirim data pengunjung ke pihak ketiga (alasan UU PDP), tanpa teka-teki gambar (pengguna hanya menunggu sepersekian detik). Biaya Rp0 lisensi, ±2–3 hari kerja.

## Opsi
| Opsi | Data ke pihak ketiga | Lisensi / biaya | Kekuatan anti-bot | Catatan |
|---|---|---|---|---|
| **ALTCHA** (proof-of-work, HMAC) | **Tidak ada** (self-hosted) | Open source **MIT** (terverifikasi repo `altcha-org/altcha`, 6 Okt 2026; library server yang dipakai BFF dicek terpisah saat dependency ditambahkan), Rp0 | Menaikkan biaya per permintaan; bot ber-CPU besar tetap bisa | Dikombinasikan dengan rate limit per nomor/IP & budget global (sudah ada) |
| Cloudflare Turnstile | IP, sinyal browser ke Cloudflare (lintas negara) | Gratis | Lebih kuat (sinyal jaringan CF) | Menambah transfer data lintas negara; ketergantungan vendor |
| Google reCAPTCHA | Sinyal perilaku ke Google | Gratis s.d. kuota | Kuat | Transfer lintas negara + profil iklan — paling buruk untuk PDP |
| hCaptcha | Sinyal ke hCaptcha | Free/berbayar | Kuat | Transfer lintas negara |

## Keputusan (rekomendasi)
ALTCHA sesuai `docs/04 §8.1`: `GET /v1/auth/challenge` (HMAC secret `altcha_hmac_key`, TTL 5 menit, sekali pakai `altcha:used:<sig>` di Redis); `POST /v1/auth/otp/request` wajib `altcha` → tanpa/invalid `400 captcha_required`. Endpoint POS (service key) tanpa captcha. Tingkat kesulitan (`maxnumber`) configurable via env; dinaikkan saat serangan (alert budget OTP).

## Konsekuensi
Positif: menutup sisa SEC-06 tanpa pemroses baru; tidak menambah transfer lintas negara. Negatif: proof-of-work lebih lemah dari captcha berbasis reputasi; perangkat sangat lambat menunggu lebih lama. Mitigasi: rate limit berlapis tetap utama.

## Pertanyaan terbuka
- Bila serangan terbukti lolos ALTCHA: setuju fallback Turnstile **hanya** saat mode serangan (dengan pemberitahuan di kebijakan privasi)?

## Kapan ditinjau ulang
Bila biaya OTP WA anomali > 2× baseline sebulan, atau ALTCHA tidak dipelihara lagi.
