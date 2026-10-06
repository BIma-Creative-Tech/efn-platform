# Indeks Architecture Decision Records (ADR) — EFN Platform

> **Pembaruan 6 Okt 2026.** Klien memutuskan: (1) kunci hanya dipegang **Superadmin** dan (2) **mTLS sesuai rekomendasi** — keduanya dicatat di ADR-022 sebagai "Diputuskan klien 6 Okt 2026" (ADR-022 sendiri tetap Proposed untuk bagian lain); (3) **pajak PB1 per kota & include/exclude dibuat configurable** → ADR-027; (4) **payment gateway belum ditentukan** → ADR-026 (abstraksi), ADR-016 ditunda; (5) atas pertanyaan "router WireGuard — tidak ada solusi open source?", ADR-023 direvisi: default **klien WireGuard di perangkat (Rp0 hardware)** untuk outlet ≤ 3 perangkat, **OpenWrt (±Rp0,4–0,9 jt)** untuk outlet besar. **UU PDP (DPO, retensi, transfer lintas negara) belum diputuskan** — default usulan dipakai sementara (`docs/compliance/`). Total kini **30 ADR**, semua masih Proposed; belum ada yang Accepted penuh.
>
> **Pembaruan 6 Okt 2026 (gelombang 8).** ADR-030: MinIO community sudah diarsipkan upstream (April 2026) dan build kami (Okt 2025) masih mengandung ≥ 4 celah High yang hanya ditambal di produk komersial. Rekomendasi: build fork komunitas **SILO** dari source (±2–3 hari, Rp0) + filter pengaman di depan storage, dan uji coba SeaweedFS sebagai jalur keluar. Butuh tanda tangan IT + Security **sebelum staging publik**.
>
> **Pembaruan 6 Okt 2026 (gelombang 10).** **ADR-031 — diputuskan klien:** tidak ada modul Odoo pihak ketiga yang dipasang (OCA, Cybrosys, Apps Store); keduanya hanya **referensi desain**, gap dibangun sebagai `efn_*` LGPL-3. ADR-015 bagian "OCA dulu" **digantikan sebagian**. Dampak: audit log, inter-company, dan laporan keuangan/konsolidasi dibangun sendiri atau dipindah ke Metabase — **±10–16 minggu-engineer (±Rp50–145 jt)**, Fase 1 berisiko mundur 2–4 minggu tanpa engineer tambahan. Rincian pelaksanaan (Q-V1..Q-V6) masih butuh tanda tangan. Total kini **31 ADR**.
>
> **Pembaruan 6 Okt 2026 (gelombang 12).** Status implementasi dicatat di ADR-022 (E12 dipersempit ke satu endpoint metrik ocr-worker; hop baru s3-gateway, Loki→Alertmanager mTLS, sink audit) dan ADR-030 (SILO + s3-gateway jalan di dev; celah SEC-41 terkonfirmasi lalu diperbaiki dan diuji). Belum ada ADR yang Accepted. Daftar gabungan keputusan klien yang masih terbuka: bagian **"Keputusan klien yang masih terbuka"** di bawah. Ringkasan satu halaman untuk manajemen: `docs/STATUS_MANAJEMEN.md`.
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
| 5 | **Paket UU PDP**: `docs/compliance/*` (DPO, retensi, Q-P1..P8), `PDP_TRANSFER_LINTAS_NEGARA.md` (Q-T1..T6) | Penetapan DPO lebih dulu; tanpa DPO, retensi tetap default usulan | DPO + Legal |
| 6 | ADR-027 | Daftar kota/tarif & kebijakan include/exclude (konsultan pajak) — sebelum go-live F&B | Finance + Tax |
| 7 | ADR-026 → ADR-016 | Prinsip abstraksi sekarang; pemilihan vendor dengan kriteria ADR-026 sebelum Fase 3 (KYB tiap PT) | Finance + Legal + IT |
| 8 | ADR-019 | Opini pajak & legal (lead time panjang) | Finance + Tax + Legal |
| 9 | ADR-025 | Formalitas | Athera + PO |
| 10 | Gelombang 2 & 3 lainnya | Seperti di bawah | — |

## Keputusan klien yang masih terbuka (gabungan, per 6 Okt 2026 — Gelombang 12)

Sudah dijawab: Q-E1 (kustodi = Superadmin), Q-E4 (mTLS), prinsip pajak configurable, vendor payment ditunda, prinsip tanpa modul Odoo pihak ketiga (ADR-031). Kolom "Default bila tidak dijawab" = yang dipakai tim sekarang.

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
| B | **Q-P9** (baru) | Retensi log audit (730 h, min 365; log kontrol/purge tanpa batas; salinan ClickHouse simpan PII atau tidak) | PDP_DATA_INVENTORY | DPO + auditor | 730/365/tanpa batas | — |
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
