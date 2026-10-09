# Indeks Architecture Decision Records (ADR) — EFN Platform

> **Pembaruan 6 Okt 2026.** Klien memutuskan: (1) kunci hanya dipegang **Superadmin** dan (2) **mTLS sesuai rekomendasi** — keduanya dicatat di ADR-022 sebagai "Diputuskan klien 6 Okt 2026" (ADR-022 sendiri tetap Proposed untuk bagian lain); (3) **pajak PB1 per kota & include/exclude dibuat configurable** → ADR-027; (4) **payment gateway belum ditentukan** → ADR-026 (abstraksi), ADR-016 ditunda; (5) atas pertanyaan "router WireGuard — tidak ada solusi open source?", ADR-023 direvisi: default **klien WireGuard di perangkat (Rp0 hardware)** untuk outlet ≤ 3 perangkat, **OpenWrt (±Rp0,4–0,9 jt)** untuk outlet besar. **UU PDP (DPO, retensi, transfer lintas negara) belum diputuskan** — default usulan dipakai sementara (`docs/compliance/`). Total kini **30 ADR**, semua masih Proposed; belum ada yang Accepted penuh.
>
> **Pembaruan 6 Okt 2026 (gelombang 8).** ADR-030: MinIO community sudah diarsipkan upstream (April 2026) dan build kami (Okt 2025) masih mengandung ≥ 4 celah High yang hanya ditambal di produk komersial. Rekomendasi: build fork komunitas **SILO** dari source (±2–3 hari, Rp0) + filter pengaman di depan storage, dan uji coba SeaweedFS sebagai jalur keluar. Butuh tanda tangan IT + Security **sebelum staging publik**.
>
> **Pembaruan 6 Okt 2026 (gelombang 10).** **ADR-031 — diputuskan klien:** tidak ada modul Odoo pihak ketiga yang dipasang (OCA, Cybrosys, Apps Store); keduanya hanya **referensi desain**, gap dibangun sebagai `efn_*` LGPL-3. ADR-015 bagian "OCA dulu" **digantikan sebagian**. Dampak: audit log, inter-company, dan laporan keuangan/konsolidasi dibangun sendiri atau dipindah ke Metabase — **±10–16 minggu-engineer (±Rp50–145 jt)**, Fase 1 berisiko mundur 2–4 minggu tanpa engineer tambahan. Rincian pelaksanaan (Q-V1..Q-V6) masih butuh tanda tangan. Total kini **31 ADR**.
>
> **Pembaruan 6 Okt 2026 (gelombang 12).** Status implementasi dicatat di ADR-022 (E12 dipersempit ke satu endpoint metrik ocr-worker; hop baru s3-gateway, Loki→Alertmanager mTLS, sink audit) dan ADR-030 (SILO + s3-gateway jalan di dev; celah SEC-41 terkonfirmasi lalu diperbaiki dan diuji). Belum ada ADR yang Accepted. Daftar gabungan keputusan klien yang masih terbuka: bagian **"Keputusan klien yang masih terbuka"** di bawah. Ringkasan satu halaman untuk manajemen: `docs/STATUS_MANAJEMEN.md`.
>
> **Pembaruan 7 Okt 2026 (gelombang 14, Fase 1).** Fitur poin di kasir sudah selesai dan diuji di lingkungan pengembangan. Reviewer keamanan (r7) memberi **GO bersyarat** untuk mengaktifkan poin di **satu outlet pilot** dengan 7 syarat (bagian "Syarat GO pilot loyalty" di bawah). Tabel keputusan terbuka ditambah **P-1..P-10** (pertanyaan pilot dari fit-gap Odoo), **Q-13** (nilai & batas poin oleh Finance), dan **Q-P12..Q-P14** (UU PDP, inventaris v4). Belum ada ADR baru; belum ada ADR yang Accepted.
>
> **Pembaruan 9 Okt 2026 (gelombang 15, Fase 2 F&B).** Layar dapur (KDS) selesai dibangun dan diuji di lingkungan pengembangan. Reviewer keamanan (r8) memberi **GO bersyarat** untuk **satu outlet F&B pilot** dengan 8 syarat (bagian "Syarat GO pilot KDS" di bawah); perbaikan teknis Gelombang 15 sudah menutup sebagian besar syarat di sisi kode, sisanya menunggu staging dan keputusan/tindakan klien. **Dua ADR baru (Proposed):** ADR-032 (QR order dari ponsel: kiosk dulu, Super-App + BFF bila wajib ponsel) dan ADR-033 (akun tablet dapur). Tabel keputusan terbuka ditambah **P-11..P-18** (pilot F&B), **Q-QR1..Q-QR5**, **Q-KD1..Q-KD4**, dan **Q-P15..Q-P16** (inventaris PDP v5). Total kini **33 ADR**; belum ada yang Accepted penuh.
>
> **Pembaruan 9 Okt 2026 (gelombang 17, Fase 3 penjualan online).** Kontrak penjualan online Super-App ditulis (`docs/04 §10`, v5.0): pilot PT Retail, ambil di 1 toko, bayar sekali (provider `mock`), Odoo tetap sumber harga & pajak dan tidak terbuka ke internet. **ADR baru (Proposed): ADR-034** — kanal online lewat BFF + 10 fungsi Odoo khusus melalui jalur internal terfilter (bukan toko online bawaan Odoo). **ADR-019** mendapat catatan revisi: A1 (jurnal titipan manual) default pilot, A2 (`account_payment_interco` CE 19) kandidat PoC setelah opini pajak, C bila gateway mendukung split. Tabel keputusan terbuka ditambah **Q-ON1..Q-ON13**. Perkiraan ±6–7 minggu kalender (paralel). Total kini **34 ADR**; belum ada yang Accepted penuh.
>
> **Ringkasan manajemen (awal).** Ada 25 keputusan arsitektur yang menunggu tanda tangan klien. Semuanya berstatus **Proposed**: tim boleh mulai mengikuti rekomendasi, tetapi tidak boleh mengunci pilihan yang mahal dibalik sebelum disetujui. **ADR-022 (enkripsi) dan ADR-025 (model tim agent) berasal dari instruksi klien 5 Okt 2026** — prinsipnya sudah wajib; yang ditandatangani adalah caranya. Tujuh keputusan di **Gelombang 1** memblokir Fase 1 dan perlu ditandatangani dalam 2 minggu pertama; ADR-022/023/024 (keamanan) ditandatangani paralel karena memblokir staging publik. Keputusan pajak dan legal (MGM, unified cart) butuh konsultan eksternal sehingga harus **dimulai** sekarang.

Status: `Proposed` = belum disetujui klien · `Accepted` = disetujui (catat nama & tanggal) · `Superseded by ADR-xxx`.
Sumber: `docs/01_REVIEW_RISK_GAP_ANALYSIS.md` (D-xx, R-xxx, Q-xx). Template: `ADR-000-template.md`.

## Indeks

| ADR | Judul | D-xx / §  | Status | Pemilik keputusan (klien) | Risiko utama |
|---|---|---|---|---|---|
| [001](ADR-001-single-odoo-multicompany.md) | Satu DB Odoo CE 19 multi-company | D-09 (+D-10) | Proposed | PO + Finance + IT | R-T01, R-T08, R-T11 |
| [002](ADR-002-traefik-edge-nestjs-bff.md) | Traefik edge + NestJS BFF | D-01 | Proposed | IT | R-S03, R-S04 |
| [003](ADR-003-etl-batch-15-menit.md) | ETL batch incremental 15 menit | D-02 | Proposed | IT + Finance | R-O03, R-T11 |
| [004](ADR-004-topologi-4-node-compose.md) | 4 node VPS + Docker Compose | D-03 | Proposed | IT + Finance + Legal/DPO | R-T07, R-T08, R-L01 |
| [005](ADR-005-timeline-12-bulan-rephasing.md) | Re-phasing ~12 bulan | D-04 | Proposed | PO + Direksi + Finance | R-B01 |
| [006](ADR-006-reward-price-hunter-poin.md) | Reward Price Hunter = poin | D-05 | Proposed | PO + Finance | R-B05 |
| [007](ADR-007-price-hunter-postgresql-sot.md) | Price Hunter di PostgreSQL, CH turunan | D-06 | Proposed | IT | R-B05, R-L01 |
| [008](ADR-008-ocr-pipeline-dua-tahap-async.md) | OCR 2 tahap asinkron | D-07 | Proposed | IT + PO + Finance | R-T05 |
| [009](ADR-009-parameter-mgm-2-tier.md) | Parameter & struktur MGM 2-tier | D-08 | Proposed | **Finance + Legal** + PO | R-B03, R-L02 |
| [010](ADR-010-ai-di-luar-jalur-transaksi.md) | AI di luar jalur transaksi | D-10 | Proposed | PO + IT | R-T06 |
| [011](ADR-011-whatsapp-cloud-api-resmi.md) | WhatsApp Cloud API resmi | D-11 | Proposed | IT + Finance + Legal | R-S04, R-B02 |
| [012](ADR-012-rabbitmq-message-broker.md) | RabbitMQ | D-12 | Proposed | IT | R-T10 |
| [013](ADR-013-scope-pos-fnb-ce19.md) | Scope POS F&B pada CE 19 (KDS custom) | D-13 | Proposed | PO + Ops F&B + IT | R-T03 |
| [014](ADR-014-hosting-ai-jaringan-privat.md) | AI di jaringan privat (bukan air-gapped) | D-14 | Proposed | IT + Legal/DPO | R-L01 |
| [015](ADR-015-penamaan-modul-efn-oca-first.md) | Modul `efn_*`, OCA/CE dulu | D-15 | Proposed — **sebagian digantikan ADR-031** (penamaan `efn_*` tetap) | IT | R-T03, R-T04 |
| [016](ADR-016-satu-payment-gateway-utama.md) | Satu payment gateway — **pemilihan vendor ditunda** (Xendit = kandidat) | D-16 | Proposed — vendor ditunda (klien 6 Okt) | Finance + Legal + IT | R-S09, R-L05 |
| [017](ADR-017-otp-saat-pos-offline.md) | OTP saat POS offline: opsi (a) nonaktif | §3.2 | Proposed | PO + Ops Outlet + Finance | R-T02 |
| [018](ADR-018-kitting-manufacture-bom.md) | Kitting = Manufacture BoM, bukan Phantom | §3.1 | Proposed | PO + Ops CK + Finance | R-T01 |
| [019](ADR-019-unified-cart-merchant-of-record.md) | Unified cart: agen penagih, penjual per PT | §3.3 | Proposed | **Finance + Tax + Legal** + PO | R-L03, R-T10 |
| [020](ADR-020-loyalty-ledger-platform-postgresql.md) | Loyalty ledger tunggal di PG platform (BFF) | D-05, §3.3 | Proposed | Finance + IT + PO | R-B03, R-S10 |
| [021](ADR-021-baseline-odoo-ce19-vs-18.md) | Baseline Odoo CE 19 (fallback 18) | R-T04 | Proposed | IT | R-T04 |
| [022](ADR-022-enkripsi-in-transit-at-rest.md) | Enkripsi in transit & at rest (PKI internal, TLS verify-full, PII AES-GCM, LUKS) | SEC-02/05/11/16 | Proposed — **instruksi klien 5 Okt 2026, wajib**; **diputuskan 6 Okt: kustodi = Superadmin, mTLS = rekomendasi** | IT + Security + Legal/DPO | R-L01, R-S01, R-T08 |
| [023](ADR-023-akses-odoo-via-wireguard.md) | Akses Odoo hanya via WireGuard + 2FA; outlet: klien WG di perangkat / OpenWrt (open source) | SEC-01 | Proposed (rev. 6 Okt) | IT + Ops Outlet + Finance | R-S03, R-T02 |
| [024](ADR-024-least-privilege-kredensial-per-service.md) | Least privilege: kredensial per service | SEC-02 | Proposed | IT + Security | R-S01, R-S10 |
| [025](ADR-025-model-operasi-tim-agent.md) | Model operasi tim agent (lead + functional-first) | D-15 | Proposed — **instruksi klien 5 Okt 2026** | Athera + PO | R-B09, R-T12 |
| [026](ADR-026-payment-gateway-agnostic.md) | Abstraksi payment gateway-agnostic; QRIS POS via BFF; mock aktif, Xendit/Midtrans nonaktif | D-16 | Proposed — prinsip "vendor belum ditentukan" dari klien 6 Okt | Finance + Legal + IT | R-S09, R-L05, R-T10 |
| [027](ADR-027-pajak-configurable-pb1-per-kota.md) | Pajak configurable: PB1 per kota via fiscal position, include/exclude per pajak, tanpa tarif di kode | Q-22, R-L03 | Proposed — prinsip "configurable" dari klien 6 Okt | Finance + Tax + Ops F&B | R-L03 |
| [028](ADR-028-captcha-altcha-self-hosted.md) | Captcha OTP = ALTCHA self-hosted (tanpa pihak ketiga) | SEC-06 | Proposed | IT + Legal/DPO | R-L01, R-B02 |
| [029](ADR-029-identitas-member-opak.md) | Identitas member opak: JWT `sub` = UUID | R-L01 | Proposed | IT + Legal/DPO | R-L01 |
| [030](ADR-030-sumber-image-object-storage.md) | Object storage: MinIO community diarsipkan → build SILO (fork) dari source + filter L7; PoC SeaweedFS sebagai jalur keluar | R-S02, R-S08 (usulan R-S13) | Proposed — jangka pendek **diterapkan di dev** (SILO + s3-gateway, SEC-41 fixed) | IT + Security (Finance bila opsi berbayar) | R-S02, R-S08, R-T08, R-L01 |
| [031](ADR-031-tanpa-dependency-modul-odoo-pihak-ketiga.md) | Tanpa dependency modul Odoo pihak ketiga; OCA & Cybrosys sebagai referensi; gap = `efn_*` LGPL-3 (clean implementation) | D-15, R-T04, R-L08 | **Diputuskan klien 6 Okt 2026** (prinsip); rincian Q-V1..Q-V6 Proposed | Klien; rincian: IT + Finance + Legal | R-T04, R-L08, R-B09, R-S10, usulan R-T13 |
| [032](ADR-032-qr-order-dari-ponsel.md) | QR order dari ponsel: pilot = kiosk; bila wajib ponsel = Super-App + BFF (E-27); `pos_self_order` mobile ditolak (ADR-023) | F-49, Q-23/P-17 | Proposed — menunggu Q-QR1 (Q-23) | PO + Ops F&B + Finance + IT/Security | R-S03, R-B01, R-L03, R-L01 |
| [033](ADR-033-akun-perangkat-kds.md) | Akun perangkat KDS: user internal tersandbox (pilot) vs user *share* (sebelum outlet ke-2) | SEC-74, SEC-77 | Proposed | IT + Security (+ Ops F&B) | R-S03, R-S10, R-S12, R-T04 |
| [034](ADR-034-kanal-online-bff-json2-terfilter.md) | Kanal penjualan online: BFF + `efn_online_order` (10 metode) lewat `/json/2` terfilter (`odoo-rpc-gateway` mTLS); `website_sale` ditolak; katalog pull delta 5 menit | F-55..F-74, §3.3 | Proposed | IT + Security; PO (lingkup); Finance + Tax (bagian ADR-019) | R-T10, R-S03, R-S09, R-O05, R-L03, usulan R-T15 |

Catatan pemetaan: D-09 dipetakan ke ADR-001 (sudah ada) sehingga ADR-009 dipakai untuk D-08. D-10 dibahas di ADR-010 dan juga disinggung ADR-001.

## Urutan tanda tangan yang tersisa (per 6 Okt 2026)
Belum ada ADR berstatus Accepted. Sudah dijawab klien: Q-E1 (kustodi = Superadmin), Q-E4 (mTLS), prinsip pajak configurable, penundaan vendor payment, **prinsip tanpa dependency modul Odoo pihak ketiga (ADR-031)**. Urutan berikut menggantikan prioritas di bawah bila berbeda.

| Urutan | ADR / dokumen | Yang masih perlu ditandatangani | Pemutus |
|---|---|---|---|
| 1 | ADR-001, 021, 018, 017, 004, 005, 013 | Gelombang 1 (tidak berubah) — memblokir Fase 1 | lihat tabel indeks |
| 2 | ADR-022 | Opsi PKI A1; Q-E1a/b/c (jumlah Superadmin ≥ 2, Shamir 2-dari-3, lokasi salinan offline); Q-E2 LUKS/AES-NI; Q-E3 PII Odoo; pengecualian E1–E10 | IT + Security |
| 3 | ADR-023 | Pola B-a/B-b per outlet; Q-W1 jumlah perangkat per outlet; Q-W4 model Android POS; Q-55 model router | IT + Ops Outlet + Finance |
| 4 | ADR-024, ADR-028, ADR-029 | Teknis, biaya Rp0 — dapat ditandatangani sekaligus | IT (+ DPO untuk 028/029) |
| 4a | **ADR-030** | Q-OS1 (terima fork komunitas tanpa SLA), Q-OS3 (object lock COMPLIANCE), Q-OS2 PoC SeaweedFS; **memblokir staging publik** karena CVE High terbuka di build saat ini — tandatangani bersama ADR-024 | IT + Security |
| 4b | **ADR-031** (rincian) | Q-V2 lisensi LGPL-3; Q-V4 anggaran/jadwal (+10–16 minggu-engineer); Q-V3 laporan keuangan per PT; Q-V1/Q-V5/Q-V6 ruang lingkup referensi — **memblokir mulai `efn_audit` & `efn_intercompany` (Fase 1)** | IT + Finance + Legal |
| 4c | **ADR-032, ADR-033** (baru 9 Okt) | Q-QR1 (kiosk vs ponsel) dan Q-KD1/Q-KD3 (akun tablet dapur) — **sebelum pilot F&B** | PO + Ops F&B + IT/Security |
| 5 | **Paket UU PDP**: `docs/compliance/*` (DPO, retensi, Q-P1..P8), `PDP_TRANSFER_LINTAS_NEGARA.md` (Q-T1..T6) | Penetapan DPO lebih dulu; tanpa DPO, retensi tetap default usulan | DPO + Legal |
| 6 | ADR-027 | Daftar kota/tarif & kebijakan include/exclude (konsultan pajak) — sebelum go-live F&B | Finance + Tax |
| 7 | ADR-026 → ADR-016 | Prinsip abstraksi sekarang; pemilihan vendor dengan kriteria ADR-026 sebelum Fase 3 (KYB tiap PT) | Finance + Legal + IT |
| 8 | ADR-019 (+ catatan revisi 9 Okt) | Opini pajak & legal (lead time panjang); A1 vs A2 vs C; Q-ON9 | Finance + Tax + Legal |
| 8a | **ADR-034** (baru 9 Okt) | Arsitektur kanal online (BFF + json2 terfilter); masa berlaku & pemegang rotasi API key kanal; Q-ON1/Q-ON2/Q-ON4/Q-ON12 — **sebelum pilot online** | IT + Security + PO |
| 9 | ADR-025 | Formalitas | Athera + PO |
| 10 | Gelombang 2 & 3 lainnya | Seperti di bawah | — |

## Syarat GO pilot loyalty (review keamanan r7, 7 Okt 2026)

Sumber: `docs/reviews/SECURITY_REVIEW_2026-10-07-r7.md` §"Rekomendasi go/no-go". Rekomendasi reviewer: **GO bersyarat** untuk satu outlet PT Retail; alternatif lebih hati-hati: poin didapat (earn) dulu, penukaran (redeem) 1–2 minggu kemudian bila tidak ada anomali kritis.

| # | Syarat sebelum fitur poin dinyalakan | Siapa | Status 7 Okt | Butuh keputusan klien? |
|---|---|---|---|---|
| G-1 | Staging: skema `06-*.sql` + modul Odoo versi 13c terpasang; uji `call_kw` **dan `/json/2`** ke klien BFF ditolak | devsecops + QA | **Belum** (staging belum ada) | Ya, tidak langsung: pengadaan server (ADR-004) |
| G-2 | Finance menetapkan batas poin final (per order, harian per member, per PT/jam, per kasir/jam), semuanya > 0 di production | **Finance** | **Belum** (placeholder 5.000 / 2.000 / 50.000 / 10.000 poin) | **Ya — Q-13** |
| G-3 | Hak akses: `base.group_system` hanya Superadmin; akun perangkat kasir **tanpa** grup POS Manager | **IT klien** + Ops Outlet | **Belum** (keputusan klien 6 Okt sudah menetapkan prinsip "hanya Superadmin"; daftar nama belum) | Ya — daftar nama Superadmin (Q-E1a) |
| G-4 | Operasional: supervisor meninjau "Review Poin" + outbox `hold` setiap hari; on-call menerima alert kritis; dead-man rekonsiliasi diamati sekali di staging | **Ops Outlet** + devsecops | **Belum** | Ya — siapa supervisor & on-call |
| G-5 | Inventaris UU PDP v4 | architect | **Selesai** (`docs/compliance/PDP_DATA_INVENTORY.md` v4, Gelombang 14) | DPO meninjau (Q-P12..Q-P14) |
| G-6 | CI: `EFN_TOUR_MIN_TESTS` 9 → 15 | devsecops | **Selesai** (per 9 Okt kini `20`, `ci.yml:317`) | Tidak |
| G-7 | SOP: retur tanpa struk wajib supervisor sampai SEC-68 / Q-27 diputuskan | **Ops Outlet** | **Belum** | Ya — P-3 (Q-27) |

Kondisi berhenti (NO-GO): alert `earn_ledger_orphan`, `earn_ledger_mismatch`, `ledger_unknown_source`, `restore_exceeds_redeem`, atau `earn_limit_company_hour` tanpa penjelasan → matikan flag poin di config POS (kasir tetap jalan tanpa poin), ikuti `docs/runbooks/loyalty-anomaly.md`.

## Syarat GO pilot KDS (review keamanan r8, 9 Okt 2026)

Sumber: `docs/reviews/SECURITY_REVIEW_2026-10-09-r8.md` §"Rekomendasi go/no-go". Rekomendasi reviewer: **GO bersyarat** untuk mengaktifkan `efn_pos_kds` di **satu outlet F&B pilot**. Status di bawah berasal dari laporan agent Gelombang 15 (odoo-engineer, devsecops) dan **belum di-review ulang** oleh security-reviewer (r9). Syarat GO loyalty (G-1..G-7) tetap berlaku bila outlet F&B juga menyalakan poin.

| # | Syarat sebelum KDS dinyalakan | Siapa | Status 9 Okt (G15) | Butuh keputusan klien? |
|---|---|---|---|---|
| K-1 | Semua stasiun pilot mode **`kds_printer`** (printer dapur tetap mencetak); mode `kds` tidak dipakai di pilot | Ops F&B + IT (konfigurasi) | Kode SEC-76 **selesai** (alert log `EFN_KDS_TICKET_FAILED`, tanda order, cetak ulang + notifikasi kasir; tour baru). Rule alert Loki **belum** (devsecops). Syarat konfigurasi tetap berlaku | Tidak (konfigurasi) |
| K-2 | **Self-order terkunci**: query `self_ordering_mode NOT IN ('nothing','kiosk')` = 0 baris di DB pilot; QR meja ponsel tidak aktif sebelum ADR-032 diputuskan | odoo-engineer + devsecops | Kunci teknis **selesai** (`efn_setup_pos` 19.0.1.5.0: `ir.default`, constraint, normalisasi, 4 test). Query pra-go-live tetap dijalankan di DB pilot. Rate limit Traefik `/pos-self` **belum** | **Ya — Q-QR1 (Q-23)** |
| K-3 | **Akun tablet dapur**: satu user per layar, grup hanya `base.group_user` + `group_kds_device` (verifikasi query), 2FA aktif, tablet = peer WireGuard sendiri, browser mode kiosk, login tidak dipakai untuk hal lain | odoo-engineer + **IT klien** + Ops F&B | Allowlist grup (termasuk lewat form Grup) + sandbox model + uji runtime **selesai** (`efn_pos_kds` 19.0.1.1.0). 2FA, peer WG, mode kiosk = tindakan IT saat instalasi | **Ya — Q-KD1, Q-KD3** (ADR-033) |
| K-4 | **Uji staging**: upgrade modul, tour lengkap, `/efn_kds/<id>` dari peer non-perangkat → 404, uji runtime akun perangkat | devsecops + QA | **Belum** (staging belum ada). Versi kini `efn_pos_kds` 19.0.1.1.0 + `efn_setup_pos` 19.0.1.5.0, tour **20/20** di dev; upgrade `-u` dari DB lama belum diuji | Ya, tidak langsung: server staging (ADR-004) |
| K-5 | **Pergantian perangkat**: rotasi token kanal setiap tablet diganti/dicabut/hilang + cabut peer WireGuard | Ops F&B + IT | Rotasi token **otomatis** saat perangkat/stasiun berubah + cron 7 hari (SEC-77). SOP cabut peer WG **belum** ditulis untuk outlet | Ya — Q-KD3 (siapa pemegang perangkat) |
| K-6 | **SOP kasir**: tidak menulis nama/HP pelanggan di catatan dapur; masuk pelatihan pilot | **Ops F&B** | **Belum** (materi pelatihan) | Ya — Q-P15(b) |
| K-7 | **Log**: uji regresi redaksi Promtail + redaksi parameter token aktif sebelum staging menerima log nyata | devsecops | **Selesai di kode/CI** (job `promtail-redaction` di `ci-gate`, redaksi query string). Query deteksi wajib dijalankan di staging sebelum menerima log | Tidak |
| K-8 | **Dokumen**: inventaris PDP v5 memuat tiket KDS; keputusan Q-19 (stasiun, target menit, retensi) dan Q-24 (perangkat/printer) dicatat | architect + **Ops F&B** | Inventaris **v5 selesai** (9 Okt). Q-19 / Q-24 **belum** (default PLACEHOLDER) | **Ya — P-13, P-18** |

Kondisi berhenti (NO-GO / kembali ke printer saja): layar menampilkan data pelanggan, perangkat dapat membuka stasiun/PT lain, atau log `EFN_KDS_TICKET_FAILED` berulang → ubah mode stasiun ke `printer` (perilaku CE standar); POS tetap jalan.

## Keputusan klien yang masih terbuka — Fase 3 penjualan online (baru, Gelombang 17, 9 Okt 2026)
Sumber: `docs/08_ODOO_FIT_GAP.md` §8.7 (functional G17), `docs/04 §10.1`, ADR-034, ADR-019 (catatan revisi). Semua memblokir **pilot online**, bukan pengembangan; kolom default = yang dipakai tim sekarang (keputusan lead 9 Okt, configurable).

| Prioritas | ID | Pertanyaan | Pemutus | Default bila tidak dijawab | Memblokir |
|---|---|---|---|---|---|
| **A** | Q-ON1 | PT & outlet pilot online, jam ambil, jumlah SKU online | PO + Ops | PT Retail, toko pilot Fase 1 | data kanal/outlet |
| **A** | Q-ON4 | Reservasi stok saat checkout atau cek saja? Batas bayar? Stok kurang setelah bayar: batal + refund penuh / refund baris / hubungi pelanggan? | PO + Ops + Finance | cek saja, 15 menit, batal + refund penuh porsi PT | saga, UX |
| **A** | Q-ON8 | Faktur online dibuat saat bayar atau saat diambil? | Finance + Tax | saat bayar | E-28 `invoice_on_paid` |
| **A** | Q-ON9 (+ Q-1, Q-2, Q-4) | Dana titipan: MDR ditanggung siapa, bukti potong, periode settlement Holding → PT, jurnal Holding manual (A1) atau otomatis (A2)? | Finance + Tax + Legal | A1 manual harian; akun PLACEHOLDER | ADR-019; go-live online |
| **A** | Q-ON6 | Pembulatan Rupiah: presisi IDR global ke 1 atau pembulatan kas per faktur? | Finance | pembulatan kas Rp1 | `amount_payable` bulat |
| B | Q-ON2 | F&B online: SO + jembatan KDS (E-29a) atau `pos.order` lewat E-27 (E-29b)? Kapan? | PO + Ops F&B + IT | F&B online mati | E-29, ADR-032 |
| B | Q-ON3 (= Q-39/Q-40) | Pesan-antar: zona, ongkir, subsidi, slot & kapasitas, 3PL | PO + Ops + Finance | mati | delivery |
| B | Q-ON5 | Harga tampil termasuk pajak? Label PB1/PPN di bukti order? | Finance + Tax | termasuk pajak + label | tampilan |
| B | Q-ON7 | Pelanggan online butuh faktur atas nama/NPWP sendiri? Data apa yang boleh disimpan di Odoo? | Finance + DPO | tidak (partner generik) | PII di Odoo |
| B | Q-ON10 | Promo online cukup pricelist atau perlu kupon/beli X gratis Y? | PO + Marketing | pricelist saja | E-28 (assert total) |
| B | Q-ON11 | Refund: siapa menyetujui, batas waktu, retur setelah diambil, hanya ke metode asal, dampak poin | Ops + Finance | CS menyetujui, ≤ 2×24 j, ke metode asal; tanpa poin | CLI refund, UI CS |
| B | Q-ON12 (+ Q-8) | Katalog per PT atau per brand dalam PT? Pricelist per PT atau per outlet? | PO + Ops | per PT, satu pricelist | kanal |
| B | Q-ON13 | Pesanan tidak diambil: lama ditahan, batal otomatis dengan/tanpa refund? | Ops + Finance | ditahan sampai tutup toko H+1, tanpa batal otomatis | cron E-28 |
| B | (IT) | Masa berlaku API key kanal online (PLACEHOLDER 90 hari) & pemegang rotasi | IT + Security | 90 hari, Superadmin | runbook rotasi |
| C | (Finance) | Poin online: earn saat diambil? redeem di checkout online? | Finance + PO | ditunda (tidak ada poin online di pilot) | §10.14 |

## Keputusan klien yang masih terbuka (gabungan, per 9 Okt 2026 — Gelombang 15)

### Pilot POS F&B + KDS (baru, Gelombang 15)
Sumber: `docs/08_ODOO_FIT_GAP.md` §7.6 (functional G14), review r8, ADR-032, ADR-033. Semua memblokir **outlet pilot F&B**, bukan pengembangan.

| Prioritas | ID | Pertanyaan | Pemutus | Default bila tidak dijawab | Memblokir |
|---|---|---|---|---|---|
| **A** | P-11 | Outlet F&B pilot: alamat/kota, jam buka, jumlah meja & lantai, jumlah kasir/KDS/kiosk | PO + Ops F&B | — | Semua persiapan; PB1 kota; pola WireGuard B-a/B-b |
| **A** | P-13 (Q-19) | Stasiun dapur & kategori menu per stasiun; target menit per stasiun; perlu stasiun *expo/pass*?; retensi tiket | Ops F&B | Demo: Dapur Panas + Bar; warn 10 / late 15 menit; retensi 90 h (PLACEHOLDER) | K-8; konfigurasi stasiun |
| **A** | P-16 (Q-22 + konsultan pajak) | Tarif PB1 kota pilot, service charge (ada? %? masuk DPP PB1? takeaway kena SC?), harga menu include/exclude | Finance + Tax | Configurable (ADR-027), belum ada nilai | Go-live F&B (struk & pajak) |
| **A** | P-17 (Q-23) = **Q-QR1** | QR order: kiosk cukup, atau wajib ponsel pelanggan? | PO + Ops F&B | Kiosk saja (ADR-032 opsi A) | K-2; pengadaan kiosk; penjadwalan E-27 (±3–4 minggu-engineer) |
| **A** | P-18 (Q-24/Q-56) | Model printer dapur & struk (Epson ePOS? sertifikat otomatis?), layar KDS (tablet/monitor + OS), kebijakan cetak cadangan | IT + Ops F&B | Epson ePOS; `kds_printer` (cetak selalu) | K-1, K-8; pengadaan perangkat |
| **A** | Q-KD1 / Q-KD3 | Terima akun tablet internal tersandbox untuk pilot (ADR-033)? Siapa pemegang 2FA tablet & yang boleh login ulang? | IT + Security + Ops F&B | Ya / supervisor outlet | K-3, K-5 |
| B | P-12 (Q-18) | Layanan: dine-in, takeaway, delivery telepon/ojol? takeaway beda pajak/SC? | PO + Ops F&B | Dine-in + takeaway; delivery lewat Super-App (nanti) | Preset & fiscal position |
| B | P-14 (Q-20) | Split/gabung/pindah meja, course, open bill — peran siapa? | Ops F&B | Standar CE (semua kasir) | Hak akses kasir |
| B | P-15 (Q-21) | Daftar modifier; mana yang memakai bahan (harga tambahan, stok)? menu paket pilihan vs tetap | Ops F&B + CK | Modifier = atribut tanpa stok; berbahan = add-on/combo | Master menu |
| B | Q-QR2..Q-QR5 | Bila ponsel: bayar di kasir/online; tamu vs login; QR dinamis vs statis + PIN; jumlah & model kiosk | PO + Finance + Ops F&B | Bayar di kasir; tamu; QR dinamis; 1 kiosk | E-27 (bila Q-QR1 = ponsel) |
| B | Q-KD2 / Q-KD4 | Akan ada user portal Odoo? (efek 2FA opsi share); rotasi token 7 hari cukup? | IT | Tidak ada / 7 hari | ADR-033 opsi B (sebelum outlet ke-2) |

### UU PDP — tambahan inventaris v5 (baru, Gelombang 15)
| Prioritas | ID | Pertanyaan | Pemutus | Default bila tidak dijawab | Memblokir |
|---|---|---|---|---|---|
| B | Q-P15 | Retensi tiket KDS (90 h vs lebih pendek) dan SOP catatan dapur vs masking teknis (±0,5–1 hari) | DPO + Ops F&B | 90 h + SOP | K-6 (SOP) |
| B | Q-P16 | Near-miss redaksi log dev: setujui klasifikasi "tidak wajib notifikasi"; hapus baris dev sekarang (delete API) atau tunggu retensi ±7 Nov; pemilik register insiden | DPO | Tunggu retensi; register sementara di `PDP_DATA_INVENTORY.md` §6 | — |

## Keputusan klien yang masih terbuka (gabungan, per 7 Okt 2026 — Gelombang 14)

Sudah dijawab: Q-E1 (kustodi = Superadmin), Q-E4 (mTLS), prinsip pajak configurable, vendor payment ditunda, prinsip tanpa modul Odoo pihak ketiga (ADR-031). Kolom "Default bila tidak dijawab" = yang dipakai tim sekarang.

### Pilot POS retail & loyalty (baru, Gelombang 14)
Sumber: `docs/08_ODOO_FIT_GAP.md` §6.6 (functional G13), review r7. Semua memblokir **outlet pilot**, bukan pengembangan.

| Prioritas | ID | Pertanyaan | Pemutus | Default bila tidak dijawab | Memblokir |
|---|---|---|---|---|---|
| **A** | **Q-13** (+ P-7) | Nilai poin, minimum tukar, maks % order, masa berlaku, kategori tanpa poin, dasar perolehan, perlakuan retur berpoin, saldo negatif/utang poin, **dan batas anti-fraud** (per order, harian per member, per PT/jam, per kasir/jam) | **Finance** + PO | 1 poin/Rp10.000, 1 poin = Rp100, min 50, maks 50 %, 365 hari; batas 5.000 / 2.000 / 50.000 / 10.000 poin (placeholder) | Syarat GO G-2; liabilitas poin di neraca |
| **A** | P-1 | Toko pilot mana (alamat, jam buka, jumlah kasir per shift, jumlah SKU aktif)? | PO + Ops | — | Semua persiapan pilot; kapasitas katalog offline (Q-7) |
| **A** | P-3 (Q-27) | Peran kasir: refund & retur tanpa struk wajib supervisor? void baris? toleransi selisih kas (Rp)? setoran tunai? satu sesi per shift? persetujuan inline + jejak atau cukup PIN supervisor? | Ops Outlet + Finance | Standar Odoo (supervisor login PIN, tanpa jejak "disetujui oleh"); SOP retur tanpa struk wajib supervisor (G-7) | Syarat GO G-7; modul opsional G13-2/3/5/6 (±1–3 minggu-engineer bila diminta) |
| **A** | P-8 | Konsultan pajak: PPN atas penukaran poin (potongan harga mengurangi DPP **atau** alat bayar)? | Finance + Tax | Potongan harga per kelompok pajak (desain saat ini) | Pembukuan pajak redeem; ubah perlakuan setelah go-live = koreksi faktur |
| **A** | P-9 (Q-5/Q-12) | Sumber data migrasi (sistem lama), bukti consent member lama, kasir boleh daftarkan member? | PO + DPO | Member tanpa bukti consent → marketing `false`; Q-K1 | Impor member & saldo awal; **lihat Q-P13** |
| B | P-2 (Q-25) | Timbangan: merek/model, label prepack atau terhubung ke kasir, format label | Ops Outlet | Nomenklatur EFN Retail (prefix 21 berat / 23 harga) | Produk timbang |
| B | P-4 (Q-28) | Daftar promo pilot konkret, harga member (pricelist % atau tetap), promo ber-OTP mana | PO + Marketing | Pricelist member 5 % (demo) | Konfigurasi promo |
| B | P-5 (Q-29) | Metode bayar pilot (tunai, EDC bank apa, QRIS), split payment, pembulatan tunai, refund wajib ke metode asal? | Finance + Ops | Tunai + EDC manual; refund tidak dibatasi ke metode asal (standar CE) | G13-4 (opsional) |
| B | P-6 (Q-55/Q-56) | Internet toko (utama + cadangan 4G), perangkat kasir, printer, laci kas, scanner, layar pelanggan | IT + Ops | — | Pengadaan perangkat outlet; mode offline |
| B | P-10 (Q-34) | Produk fresh perlu lot/kedaluwarsa di kasir? | Ops | Tidak (lot di gudang saja) | Alur kasir produk fresh |

### UU PDP — tambahan inventaris v4 (baru, Gelombang 14)
| Prioritas | ID | Pertanyaan | Pemutus | Default bila tidak dijawab | Memblokir |
|---|---|---|---|---|---|
| **A** | **Q-P13** | Nomor referensi data lama (`source_ref`) saat impor saldo poin wajib opak, bukan nomor pelanggan sistem lama? | DPO + Finance | **Rekomendasi: wajib opak** (±0,5 hari); tanpa keputusan, id lama tersimpan permanen setelah akun dihapus | **Impor saldo pertama** (entri tidak bisa diubah) |
| B | Q-P12 | Masa simpan daftar tolak akun terhapus (`erasure_suppression`) + terima risiko uji keanggotaan oleh pemegang kunci | DPO | 90 hari | — |
| B | Q-P14 | Retensi outbox poin Odoo (kini tanpa batas) & riwayat query/akun Metabase | DPO + Finance | tanpa batas / selama dipakai | — |

### Umum (dari Gelombang 12, diperbarui)

| Prioritas | ID | Pertanyaan | Sumber | Pemutus | Default bila tidak dijawab | Memblokir |
|---|---|---|---|---|---|---|
| **A** | Q-P1 | Penetapan DPO + kontak publik | PDP_DATA_INVENTORY | Direksi | Legal sebagai DPO sementara | Semua keputusan retensi, notifikasi breach |
| **A** | Q-E1a/b/c | Jumlah Superadmin (≥ 2), Shamir 2-dari-3, lokasi salinan offline kedua | ADR-022 | Direksi + IT | 1 orang = bus factor 1 | Staging publik (rotasi/restore darurat) |
| **A** | Q-OS1 / Q-OS3 | Terima SILO tanpa SLA; object lock `pg-backup` COMPLIANCE 15 h | ADR-030 | IT + Security | SILO + GOVERNANCE + filter | Staging publik |
| **A** | Q-V2 / Q-V4 | Lisensi `efn_*` LGPL-3; anggaran ±Rp50–145 jt + mundur 2–4 mgg **atau** engineer kontrak | ADR-031 | IT + Finance + Legal | LGPL-3; jadwal berisiko mundur | Fase 1 (`efn_intercompany`) |
| **A** | Gelombang 1 | ADR-001, 021, 018, 017, 004, 005, 013 | indeks | lihat indeks | rekomendasi ADR | Fase 1 |
| B | Q-OS2 / Q-OS4 / Q-OS5 | PoC SeaweedFS 5 h; filestore Odoo lokal; target backup off-site | ADR-030 | IT | ya / lokal / belum ada | Go-live (off-site wajib) |
| B | Q-E2 / Q-E3 | LUKS + AES-NI di pengadaan VPS; enkripsi kolom PII Odoo | ADR-022 | IT + DPO | wajib / tidak | Pengadaan server |
| B | Q-V1 / Q-V3 / Q-V5 / Q-V6 | Sandbox referensi; laporan keuangan per PT (Metabase vs Odoo); cakupan "pihak ketiga"; vendor referensi lain | ADR-031 | IT + Finance | tidak / — / hanya addon / OCA+Cybrosys | Fase 1 laporan keuangan |
| B | Q-W1 / Q-W2 / Q-W3 / Q-W4 / Q-55 | Jumlah perangkat per outlet, pemasang & SLA, user Odoo eksternal, model Android POS, model router | ADR-023 | IT + Ops Outlet | klien WG di perangkat | Pilot outlet |
| B | Q-P2..Q-P8 | Retensi transaksi 10 th, usia, transfer, data keuangan, tombstone, CH 24 bln vs 5 th, lokasi 90 h | PDP_DATA_INVENTORY | DPO + Legal | default usulan | Go-live publik |
| B | **Q-P9** | Retensi log audit (730 h, min 365; log kontrol/purge/akses tanpa batas; salinan ClickHouse TTL 10 th — v4: nilai pribadi sudah tidak disalin) | PDP_DATA_INVENTORY | DPO + auditor | 730/365/tanpa batas/10 th | — |
| B | **Q-P10** (baru) | Anggota grup "Auditor Data Pribadi Lintas PT" | PDP_DATA_INVENTORY | DPO + Direksi | tidak ada anggota | Audit lintas PT |
| B | **Q-P11** (baru, SEC-38) | Akun tidak aktif / nomor HP daur ulang (anonimisasi otomatis N bulan) | PDP_DATA_INVENTORY | DPO + PO | tidak ada kebijakan (risiko residu) | — |
| B | Q-T1..Q-T6 | Dasar transfer Meta/Cloudflare, WA Local Storage, mode Cloudflare, lokasi data payment, kunci tidak di GitHub, DPIA | PDP_TRANSFER_LINTAS_NEGARA | DPO + Legal | proxy penuh CF | Go-live publik |
| B | **Q-K1** (baru) | **Kasir boleh membuat pelanggan baru di POS?** Saat ini kasir tanpa grup Contact Creation tidak bisa onboarding pelanggan; bila boleh, HP member yang sudah ada tetap terkunci (SEC-07/44 terbukti) | QA G11, functional E-6 | PO + Ops Outlet | belum ditetapkan (konfigurasi grup) | SOP kasir pilot |
| C | Q-3 | Metode transfer pricing antar-PT & siapa berwenang | docs/01 | Finance + Tax | — | `efn_intercompany` |
| C | Q-4 | Periode & mekanisme settlement poin lintas-brand | docs/01 | Finance | — | ADR-020 fase 2 |
| C | Q-6 | Auditor eksternal & format konsolidasi (juga menentukan minimum retensi log audit) | docs/01 | Finance | — | Q-V3, Q-P9 |
| C | Q-8 | Satu katalog induk atau per PT; pemilik data produk | docs/01 | PO + Ops | — | Master data Fase 1 |
| C | Q-22 | Service charge & PB1 include/exclude per outlet/daerah | docs/01, ADR-027 | Finance + Tax | configurable | Go-live F&B |
| C | Q-49 | Retensi DWH & anonimisasi analitik | docs/01 | DPO + Finance | 5 th mart | (= Q-P7/Q-P9) |

## Urutan prioritas tanda tangan (awal, 5 Okt 2026)

### Gelombang 1 — minggu 1–2 Fase 0 (memblokir Fase 1–2)
| Urutan | ADR | Alasan prioritas |
|---|---|---|
| 1 | ADR-001 | Fondasi data seluruh PT; mahal dibalik. |
| 2 | ADR-021 | R-T04 meminta keputusan versi di minggu 1; menentukan semua modul. |
| 3 | ADR-018 | R-T01 skor 20; master data CK & outlet bergantung padanya. |
| 4 | ADR-017 | R-T02 skor 20; desain `efn_pos_otp` & SOP kasir. |
| 5 | ADR-004 | Pengadaan server & lokasi data (PDP) — lead time vendor. |
| 6 | ADR-005 | Anggaran & jadwal; sponsor harus menyetujui. |
| 7 | ADR-013 | Ekspektasi fitur POS F&B (KDS custom, booking ditunda). |

### Gelombang 1-S — keamanan, paralel dengan Gelombang 1 (memblokir staging publik)
| Urutan | ADR | Alasan prioritas |
|---|---|---|
| S1 | ADR-022 | **Instruksi klien** — prinsip sudah berlaku; tanda tangan untuk opsi PKI, cakupan PII, pemegang brankas kunci (Q-E1), syarat LUKS/AES-NI di pengadaan (terkait ADR-004). |
| S2 | ADR-023 | SEC-01 High; butuh anggaran router outlet & SOP buka sesi POS (terkait ADR-017). |
| S3 | ADR-024 | SEC-02 High; murni teknis, tanpa biaya lisensi. |
| S4 | ADR-025 | **Instruksi klien** — formalitas proses kerja tim agent. |

Dokumen kepatuhan UU PDP (`docs/compliance/`) bukan ADR, tetapi butuh review **DPO + Legal** sebelum go-live (penetapan DPO, retensi, perlakuan poin saat hapus akun).

### Gelombang 2 — sebelum akhir Fase 0 (bulan 1)
ADR-002, ADR-003, ADR-012, ADR-015, ADR-010, ADR-014, ADR-020, ADR-011 (akun Meta/BSP & verifikasi bisnis butuh waktu).

### Gelombang 3 — mulai sekarang, wajib selesai sebelum fase terkait
| ADR | Batas | Catatan |
|---|---|---|
| ADR-016 | Sebelum fase 3 (idealnya bulan 3) | Perbandingan penawaran Xendit vs Midtrans; KYB tiap PT. |
| ADR-019 | Sebelum fase 3 | **Butuh opini konsultan pajak & legal** — lead time panjang, mulai segera. |
| ADR-006, ADR-007, ADR-008 | Sebelum fase 4 | Nilai poin (Q-13) diperlukan lebih awal untuk loyalty dasar fase 2. |
| ADR-009 | Sebelum fase 4 | **Simulasi Finance + legal sign-off MGM** — mulai segera. |

## Cara menandatangani
Ubah baris status di ADR menjadi `Accepted — <nama>, <jabatan>, <tanggal>` dan perbarui tabel di atas. Bila klien memilih opsi lain, ubah bagian Keputusan dan catat alasannya; jangan menghapus opsi yang ditolak.
