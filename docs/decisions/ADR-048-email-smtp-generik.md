# ADR-048: Email marketing & transaksional lewat adapter SMTP generik (TLS terverifikasi), Mailpit di dev

- **Status:** Proposed — belum disetujui klien. G46 boleh membangun dengan `EMAIL_ENABLED=false` default; production
  menunggu pemilihan penyedia SMTP + DPA (Q-SM19) dan rekaman SPF/DKIM/DMARC domain pengirim.
- **Tanggal:** 2026-11-18 (Gelombang 0 Supermarket)
- **Pemutus:** IT klien (penyedia, domain), DPO (lokasi pemrosesan & DPA), Marketing (template), Security
- **Referensi review:** R-L01, R-S01 (secret), R-B02 (OpEx); Q-SM19 (`docs/04` §28.12)
- **Terkait:** ADR-022 (TLS verify), ADR-024 (kredensial per service), ADR-044/047 (kanal kampanye), `docs/04` §28.9

> **Ringkasan manajemen.** Email (promo yang disetujui marketing, notifikasi stok tersedia, dan pemberitahuan
> transaksional bila diperlukan) dikirim lewat **SMTP standar** sehingga EFN bebas memilih penyedia (server sendiri,
> penyedia lokal, atau layanan email besar) tanpa mengubah kode. Koneksi selalu terenkripsi dan sertifikat server
> diperiksa. Di lingkungan pengembangan, email ditangkap Mailpit (tidak pernah terkirim ke orang sungguhan). Biaya bangun
> ±3–5 hari-engineer; biaya kirim tergantung penyedia (belum dipilih).

## 1. Konteks
- Keputusan pemilik proyek 18 Nov 2026: kanal promo personal termasuk email (SMTP generik TLS; Mailpit di dev).
- Platform belum punya kanal email (hanya push Expo dan WhatsApp Cloud API). Member mendaftar dengan HP; email opsional
  (tersimpan terenkripsi di BFF bila diisi).

| Sumber | Bunyi | Pertentangan |
|---|---|---|
| `CLAUDE.md` | "Semua koneksi antar-service TLS dengan verifikasi … Tidak ada fallback diam-diam ke plaintext." | SMTP sering dikonfigurasi STARTTLS opsional; adapter wajib menolak koneksi tanpa TLS. |
| `CLAUDE.md` | "Secret hanya dari `/run/secrets/*`." | Password SMTP dari file secret, bukan env. |

## 2. Opsi
| # | Opsi | Kelebihan | Kekurangan | Biaya / waktu |
|---|---|---|---|---|
| **A** | **Adapter SMTP generik (nodemailer atau setara) di BFF `src/notification/email`** | Vendor-agnostic; satu protokol; mudah diuji dengan Mailpit | Fitur lanjutan (webhook bounce, analytics) tergantung penyedia | **±3–5 hari-engineer**; OpEx per penyedia |
| B | API HTTP penyedia tertentu (SES/SendGrid/Mailgun) | Bounce/complaint webhook | Terkunci vendor; transfer lintas negara | ±3–5 hari + migrasi bila ganti |
| C | MTA sendiri (Postfix) sebagai relai keluar | Kontrol penuh | Reputasi IP, blacklist, operasi berat | ±1–2 minggu + operasi berulang |

## 3. Keputusan
1. **Opsi A.** Konsumen queue `notify.email` di BFF (konteks `src/notification/email`), atau proses terpisah dalam image
   BFF bila beban perlu dipisah (tetap kredensial RabbitMQ sendiri).
2. TLS: `EMAIL_SMTP_TLS_MODE=implicit` (465) atau `starttls` (587, **wajib** — `requireTLS`); verifikasi sertifikat
   `rejectUnauthorized=true` terhadap CA sistem (penyedia publik) atau `/run/secrets/efn_ca_crt` (relai internal,
   `EMAIL_SMTP_CA_FILE`); `servername` = host. Tidak ada mode plaintext; konfigurasi tanpa TLS → service gagal start.
3. Secret: `/run/secrets/email_smtp_password` (user di env `EMAIL_SMTP_USER`). Egress hanya ke host SMTP (allowlist).
4. Dev: Mailpit (container, UI hanya di jaringan dev) dengan TLS memakai sertifikat CA internal dev; tidak ada relai ke
   internet dari dev.
5. Template di kode (allowlist, seperti `notification/templates.ts`), parameter di-escape HTML; promo wajib footer
   berhenti berlangganan (tautan bertoken HMAC sekali pakai → cabut consent `marketing`) dan header
   `List-Unsubscribe`.
6. Penerima: alamat email didekripsi BFF per pesan saat kirim; payload queue hanya `member_hash` + `source/source_ref`
   (pola `notify.push` §4) — **tidak** membawa alamat email.
7. Log: alamat di-mask (`a***@d***.com`), tanpa isi pesan.

## 4. Konsekuensi
- Positif: bebas vendor; dev aman (Mailpit).
- Negatif: tanpa webhook bounce standar, alamat tidak valid dideteksi dari kode respons SMTP saja (5xx → tandai
  `email_status=invalid`, berhenti kirim); keterkiriman bergantung SPF/DKIM/DMARC domain (tugas IT klien).
- Dampak dokumen: `docs/04` §28.9; inventaris PDP §2.8.12.

## 5. Risiko terkait
R-L01 (alamat email = data pribadi; transfer ke penyedia), R-S01 (password SMTP), R-B17 (spam promo), R-B02 (OpEx).

## 6. Pertanyaan terbuka
- **Q-SM19** (IT klien + DPO) Penyedia SMTP (lokasi pemrosesan, DPA, batas kirim per hari), domain/subdomain pengirim,
  siapa mengelola DNS SPF/DKIM/DMARC.

## 7. Kapan ditinjau ulang
- Volume email > batas penyedia, atau kebutuhan webhook bounce/complaint (→ adapter API penyedia di samping SMTP).
- Paling lambat **30 Jun 2027**.
