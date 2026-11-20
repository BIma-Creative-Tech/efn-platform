---
description: Review keamanan pra-rilis atas diff branch terhadap main, lewat efn-security-reviewer (gerbang sebelum PR/merge atau rilis)
argument-hint: [base ref, default origin/main] [label gelombang, mis. g47]
---

Argumen: $ARGUMENTS

1. Tentukan base (default `origin/main`) dan kumpulkan `git diff --stat <base>...HEAD` + daftar file. Jangan membaca seluruh diff di konteks utama.
2. Delegasikan ke agent `efn-security-reviewer` dengan daftar file itu dan checklist di bawah. Ia hanya menulis ke `docs/reviews/SECURITY_REVIEW_<YYYY-MM-DD>[_<label>].md`.
3. Bawa kembali ringkasan saja: tabel temuan (ID SEC-xx, severity, file:line) + "Wajib diperbaiki sebelum merge" (Critical/High) + "Sebaiknya segera" (Medium).

Checklist (tandai ✅ / ⚠️ / ❌ / n/a per item yang relevan dengan diff, dengan bukti file:line; tidak bisa diverifikasi = ⚠️ + apa yang harus dicek):

**Secret & PDP**
1. Tidak ada secret/token/kunci di kode, compose, fixture, contoh, atau log; secret hanya dari `/run/secrets/*`.
2. Nomor HP di log ter-mask (`62812****789`); tidak ada OTP, token, NIK, nomor rekening, foto bukti transfer, atau lokasi presisi di log.
3. Data pribadi baru tercatat di inventaris PDP dengan retensi; payload ke 9router/LLM tanpa PII (K25-01).
4. `NEXT_PUBLIC_*` / bundle Expo tidak berisi secret server.

**Enkripsi & jaringan (ADR-022)**
5. Koneksi antar-service baru TLS verify-full dengan CA internal; tidak ada fallback plaintext (`make lint-enc`).
6. Kredensial DB/broker/storage per service (least privilege); bukan superuser.
7. Odoo tidak terekspos ke internet; Super-App hanya lewat BFF.

**AuthN / AuthZ**
8. Endpoint non-publik butuh auth; peran admin dicek di server.
9. IDOR: member/PT tidak bisa membaca data member/PT lain dengan mengganti ID.
10. Odoo: model baru punya `company_id` + record rule multi-company; `sudo()` dibatasi dan dijustifikasi.
11. OTP/login: rate limit, tidak membocorkan nomor terdaftar, aman terhadap replay & timing.

**Input, webhook, async**
12. Input divalidasi (DTO/class-validator, schema); query parameterized; Text-to-SQL lewat guardrail.
13. Upload: magic bytes, re-encode, strip metadata, batas ukuran (pipeline Price Hunter).
14. Webhook: HMAC atas raw body + idempotency key + replay window.
15. Consumer RabbitMQ idempotent dan punya DLQ `<queue>.dlq`.
16. Error tidak membocorkan stack trace/detail internal.

**Container & supply chain**
17. Container non-root, base image dipin digest, port minimal, healthcheck.
18. Dependency baru dipin, lisensi bukan GPL/AGPL (NPM/Py), tanpa dependency modul Odoo pihak ketiga (ADR-031).
19. `uses:` GitHub Actions dipin SHA 40 karakter.

**Fitur berisiko yang wajib OFF di prod**
20. Flag `WA_UNOFFICIAL_ENABLED`, `MGM_ENABLED`, LLM cross-border, tracking (ADR-044/047/039) default `false` dan tidak dinyalakan di overlay prod.

Untuk rilis production, tambahkan juga: restore-test backup terbaru lulus, dan approval environment `production` aktif.
