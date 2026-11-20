---
description: Audit modul/service EFN terhadap konvensi CLAUDE.md (read-only); laporan ✅/⚠️/❌ + 5 perbaikan teratas
argument-hint: <path modul/service, mis. services/odoo/addons/efn_mgm_policy atau services/api-gateway/src/loyalty> [--fix]
---

Argumen: $ARGUMENTS

Audit hanya path yang diberikan (bukan seluruh repo). Bila path lebih dari satu atau besar, sapu filenya lewat subagent `Explore` dan bawa pulang temuannya saja.
Read-only kecuali `--fix`. Setiap butir: ✅ / ⚠️ / ❌ / n/a + catatan satu baris + file:line.

**Semua modul/service**
1. README ada: tujuan, ADR terkait (status `Proposed` disebut), risiko R-xxx, dan "apa yang belum".
2. Butir K25/K18 yang disentuh punya ADR atau revisi kontrak `docs/04` (CLAUDE.md §K25).
3. Test ada dan relevan dengan perilaku utama (bukan hanya smoke).
4. Tidak ada secret/PII di kode, fixture, contoh; log memask nomor HP.
5. Koneksi keluar TLS verify-full ke CA internal; kredensial dari `/run/secrets/*`.

**Odoo (`services/odoo/addons/efn_*`)**
6. Manifest: nama `efn_<domain>`, versi `19.0.x.y.z`, `license: LGPL-3`, depends hanya modul CE + `efn_*` (tanpa OCA/Cybrosys/Enterprise, ADR-031).
7. Setiap model baru: `company_id` + record rule multi-company; `ir.model.access.csv` lengkap per grup.
8. `sudo()` dibatasi dan diberi alasan; tidak membuka data PT lain.
9. Test `TransactionCase`/`HttpCase` dengan `@tagged(..., "efn")`.
10. Fit-gap `efn-odoo-functional` tercatat (`docs/fit-gap/` atau `docs/08`) untuk modul gap; referensi OCA/Cybrosys dicatat di README bila dipakai.

**NestJS (`services/api-gateway/src/<context>`)**
11. Satu folder per bounded context: module, controller, service, dto, spec.
12. Input lewat DTO + `class-validator` atau guard; error tanpa detail internal; endpoint publik di-rate-limit.
13. Queue `<domain>.<event>` + DLQ `<queue>.dlq`; consumer idempotent.
14. Webhook: HMAC raw body + idempotency + replay window.

**Python (`services/*` worker/ETL)**
15. `pytest` di `tests/`, lolos `ruff`; dependency dipin di `requirements.txt`.
16. ClickHouse hanya ditulis lewat ETL/jalur yang diizinkan ADR-003/ADR-040.

Output:
1. Tabel checklist.
2. 5 perbaikan teratas berdasarkan dampak ÷ usaha, masing-masing dengan estimasi S/M/L dan agent yang tepat (`efn-odoo-engineer`, `efn-bff-engineer`, dst).
3. Bila `--fix`: kerjakan hanya perbaikan ukuran S yang tidak mengubah perilaku (README, header ADR, `ir.model.access.csv` yang jelas kurang, tag test), di branch kerja (bukan `main`), lalu jalankan ulang cek. Perubahan perilaku: tanyakan dulu.
