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
> **Pembaruan 10 Okt 2026 (gelombang 17 penutup).** Fitur belanja online selesai dibangun dan diuji di lingkungan pengembangan (smoke penuh **267 lulus / 0 gagal**). Reviewer keamanan (r10, re-review akhir) menyatakan **GO untuk merge** ke `main`; fitur online **tetap mati di produksi** (`ONLINE_ENABLED=false`) sampai 6 syarat aktivasi terpenuhi (bagian "Syarat aktivasi produksi online" di bawah). **Keputusan pemilik proyek 10 Okt 2026:** (1) sizing pilot **opsi B** — `ODOO_WORKERS=6` tetap, `ODOO_RPC_MAXCONN=2` (POS 4 + online 2); (2) **bahasa id_ID Odoo tidak diaktifkan dulu**. **ADR-034 tetap Proposed** (keputusan pemilik proyek bersifat operasional, bukan tanda tangan arsitektur oleh IT + Security + PO). Risiko baru di register: **R-T15** (API key kanal kedaluwarsa) dan **R-S14** (SEC-111, pemisahan tugas refund lintas sistem = kontrol prosedural). UU PDP: inventaris **v6** + **Q-P17..Q-P21**. Total tetap **34 ADR**; belum ada yang Accepted penuh.
>
> **Pembaruan 10 Okt 2026 (Gelombang 18 — sesi tanya-jawab pemilik proyek).** Pemilik proyek menjawab **semua** pertanyaan terbuka (60 butir; bagian **"Keputusan pemilik proyek 10 Okt 2026 (sesi tanya-jawab)"**). **Accepted:** ADR-001, 004, 005, 013, 017, 018, 021 (Gelombang 1) dan **ADR-032 opsi B** (QR ponsel wajib → E-27 sebelum pilot F&B). Catatan keputusan (status tetap Proposed) di ADR-019 (Q-ON9), ADR-020 (poin = alat bayar), ADR-022 (1 Superadmin + brankas Direksi; LUKS wajib; PII Odoo terenkripsi), ADR-023, ADR-027 (PB1 10 % + SC 5 % dine-in; ⚠ PBJT bawa pulang), ADR-030, ADR-031 (Q-V1..V6), ADR-033, ADR-034. **ADR baru (Proposed): ADR-035** strategi portal (pelanggan/vendor/franchisee/karyawan). Jawaban memunculkan **28 paket kerja** (±52–79 minggu-engineer) — urutan usulan di `docs/10_RENCANA_GELOMBANG_18_PLUS.md`. Risiko baru: R-B11, R-L09, R-L10, R-T17, R-S15. UU PDP: inventaris **v7**. Total kini **35 ADR**, **8 Accepted**.
>
> **Ringkasan manajemen (awal).** Ada 25 keputusan arsitektur yang menunggu tanda tangan klien. Semuanya berstatus **Proposed**: tim boleh mulai mengikuti rekomendasi, tetapi tidak boleh mengunci pilihan yang mahal dibalik sebelum disetujui. **ADR-022 (enkripsi) dan ADR-025 (model tim agent) berasal dari instruksi klien 5 Okt 2026** — prinsipnya sudah wajib; yang ditandatangani adalah caranya. Tujuh keputusan di **Gelombang 1** memblokir Fase 1 dan perlu ditandatangani dalam 2 minggu pertama; ADR-022/023/024 (keamanan) ditandatangani paralel karena memblokir staging publik. Keputusan pajak dan legal (MGM, unified cart) butuh konsultan eksternal sehingga harus **dimulai** sekarang.

Status: `Proposed` = belum disetujui klien · `Accepted` = disetujui (catat nama & tanggal) · `Superseded by ADR-xxx`.
Sumber: `docs/01_REVIEW_RISK_GAP_ANALYSIS.md` (D-xx, R-xxx, Q-xx). Template: `ADR-000-template.md`.

## Indeks

| ADR | Judul | D-xx / §  | Status | Pemilik keputusan (klien) | Risiko utama |
|---|---|---|---|---|---|
| [001](ADR-001-single-odoo-multicompany.md) | Satu DB Odoo CE 19 multi-company | D-09 (+D-10) | **Accepted** — pemilik proyek, 10 Okt 2026 | PO + Finance + IT | R-T01, R-T08, R-T11 |
| [002](ADR-002-traefik-edge-nestjs-bff.md) | Traefik edge + NestJS BFF | D-01 | Proposed | IT | R-S03, R-S04 |
| [003](ADR-003-etl-batch-15-menit.md) | ETL batch incremental 15 menit | D-02 | Proposed | IT + Finance | R-O03, R-T11 |
| [004](ADR-004-topologi-4-node-compose.md) | 4 node VPS + Docker Compose | D-03 | **Accepted** — pemilik proyek, 10 Okt 2026 | IT + Finance + Legal/DPO | R-T07, R-T08, R-L01 |
| [005](ADR-005-timeline-12-bulan-rephasing.md) | Re-phasing ~12 bulan | D-04 | **Accepted** — pemilik proyek, 10 Okt 2026 | PO + Direksi + Finance | R-B01 |
| [006](ADR-006-reward-price-hunter-poin.md) | Reward Price Hunter = poin | D-05 | Proposed | PO + Finance | R-B05 |
| [007](ADR-007-price-hunter-postgresql-sot.md) | Price Hunter di PostgreSQL, CH turunan | D-06 | Proposed | IT | R-B05, R-L01 |
| [008](ADR-008-ocr-pipeline-dua-tahap-async.md) | OCR 2 tahap asinkron | D-07 | Proposed | IT + PO + Finance | R-T05 |
| [009](ADR-009-parameter-mgm-2-tier.md) | Parameter & struktur MGM 2-tier | D-08 | Proposed | **Finance + Legal** + PO | R-B03, R-L02 |
| [010](ADR-010-ai-di-luar-jalur-transaksi.md) | AI di luar jalur transaksi | D-10 | Proposed | PO + IT | R-T06 |
| [011](ADR-011-whatsapp-cloud-api-resmi.md) | WhatsApp Cloud API resmi | D-11 | Proposed | IT + Finance + Legal | R-S04, R-B02 |
| [012](ADR-012-rabbitmq-message-broker.md) | RabbitMQ | D-12 | Proposed | IT | R-T10 |
| [013](ADR-013-scope-pos-fnb-ce19.md) | Scope POS F&B pada CE 19 (KDS custom) | D-13 | **Accepted** — pemilik proyek, 10 Okt 2026 | PO + Ops F&B + IT | R-T03 |
| [014](ADR-014-hosting-ai-jaringan-privat.md) | AI di jaringan privat (bukan air-gapped) | D-14 | Proposed | IT + Legal/DPO | R-L01 |
| [015](ADR-015-penamaan-modul-efn-oca-first.md) | Modul `efn_*`, OCA/CE dulu | D-15 | Proposed — **sebagian digantikan ADR-031** (penamaan `efn_*` tetap) | IT | R-T03, R-T04 |
| [016](ADR-016-satu-payment-gateway-utama.md) | Satu payment gateway — **pemilihan vendor ditunda** (Xendit = kandidat) | D-16 | Proposed — vendor ditunda (klien 6 Okt) | Finance + Legal + IT | R-S09, R-L05 |
| [017](ADR-017-otp-saat-pos-offline.md) | OTP saat POS offline: opsi (a) nonaktif | §3.2 | **Accepted** — pemilik proyek, 10 Okt 2026 | PO + Ops Outlet + Finance | R-T02 |
| [018](ADR-018-kitting-manufacture-bom.md) | Kitting = Manufacture BoM, bukan Phantom | §3.1 | **Accepted** — pemilik proyek, 10 Okt 2026 | PO + Ops CK + Finance | R-T01 |
| [019](ADR-019-unified-cart-merchant-of-record.md) | Unified cart: agen penagih, penjual per PT | §3.3 | Proposed — Q-ON9 diputuskan 10 Okt (MDR PT penjual, harian, A1); menunggu opini pajak | **Finance + Tax + Legal** + PO | R-L03, R-T10 |
| [020](ADR-020-loyalty-ledger-platform-postgresql.md) | Loyalty ledger tunggal di PG platform (BFF) | D-05, §3.3 | Proposed — catatan 10 Okt: redeem = alat bayar (P-8), poin online, settlement bulanan (Q-4) | Finance + IT + PO | R-B03, R-S10 |
| [021](ADR-021-baseline-odoo-ce19-vs-18.md) | Baseline Odoo CE 19 (fallback 18) | R-T04 | **Accepted** — pemilik proyek, 10 Okt 2026 | IT | R-T04 |
| [022](ADR-022-enkripsi-in-transit-at-rest.md) | Enkripsi in transit & at rest (PKI internal, TLS verify-full, PII AES-GCM, LUKS) | SEC-02/05/11/16 | Proposed — **instruksi klien 5 Okt 2026, wajib**; **diputuskan 6 Okt: kustodi = Superadmin, mTLS = rekomendasi**; **10 Okt: 1 Superadmin + brankas Direksi (R-B11), LUKS wajib, PII Odoo terenkripsi** | IT + Security + Legal/DPO | R-L01, R-S01, R-T08 |
| [023](ADR-023-akses-odoo-via-wireguard.md) | Akses Odoo hanya via WireGuard + 2FA; outlet: klien WG di perangkat / OpenWrt (open source) | SEC-01 | Proposed (rev. 6 Okt) — Q-W diputuskan 10 Okt; usulan revisi terbatas portal vendor di ADR-035 | IT + Ops Outlet + Finance | R-S03, R-T02 |
| [024](ADR-024-least-privilege-kredensial-per-service.md) | Least privilege: kredensial per service | SEC-02 | Proposed | IT + Security | R-S01, R-S10 |
| [025](ADR-025-model-operasi-tim-agent.md) | Model operasi tim agent (lead + functional-first) | D-15 | Proposed — **instruksi klien 5 Okt 2026** | Athera + PO | R-B09, R-T12 |
| [026](ADR-026-payment-gateway-agnostic.md) | Abstraksi payment gateway-agnostic; QRIS POS via BFF; mock aktif, Xendit/Midtrans nonaktif | D-16 | Proposed — prinsip "vendor belum ditentukan" dari klien 6 Okt | Finance + Legal + IT | R-S09, R-L05, R-T10 |
| [027](ADR-027-pajak-configurable-pb1-per-kota.md) | Pajak configurable: PB1 per kota via fiscal position, include/exclude per pajak, tanpa tarif di kode | Q-22, R-L03 | Proposed — prinsip "configurable" dari klien 6 Okt; **nilai awal 10 Okt: PB1 10 % + SC 5 % dine-in, exclude** (⚠ PBJT non-dine-in, konsultan pajak) | Finance + Tax + Ops F&B | R-L03 |
| [028](ADR-028-captcha-altcha-self-hosted.md) | Captcha OTP = ALTCHA self-hosted (tanpa pihak ketiga) | SEC-06 | Proposed | IT + Legal/DPO | R-L01, R-B02 |
| [029](ADR-029-identitas-member-opak.md) | Identitas member opak: JWT `sub` = UUID | R-L01 | Proposed | IT + Legal/DPO | R-L01 |
| [030](ADR-030-sumber-image-object-storage.md) | Object storage: MinIO community diarsipkan → build SILO (fork) dari source + filter L7; PoC SeaweedFS sebagai jalur keluar | R-S02, R-S08 (usulan R-S13) | Proposed — jangka pendek **diterapkan di dev** (SILO + s3-gateway, SEC-41 fixed); **Q-OS1..5 dijawab 10 Okt** (SILO, GOVERNANCE, PoC SeaweedFS, off-site wajib) | IT + Security (Finance bila opsi berbayar) | R-S02, R-S08, R-T08, R-L01 |
| [031](ADR-031-tanpa-dependency-modul-odoo-pihak-ketiga.md) | Tanpa dependency modul Odoo pihak ketiga; OCA & Cybrosys sebagai referensi; gap = `efn_*` LGPL-3 (clean implementation) | D-15, R-T04, R-L08 | **Diputuskan klien 6 Okt 2026** (prinsip); rincian **Q-V1..Q-V6 diputuskan 10 Okt** (LGPL-3, mundur 2–4 mgg, laporan resmi per PT di Odoo) | Klien; rincian: IT + Finance + Legal | R-T04, R-L08, R-B09, R-S10, usulan R-T13 |
| [032](ADR-032-qr-order-dari-ponsel.md) | QR order dari ponsel: pilot = kiosk; bila wajib ponsel = Super-App + BFF (E-27); `pos_self_order` mobile ditolak (ADR-023) | F-49, Q-23/P-17 | **Accepted — opsi B** (pemilik proyek, 10 Okt 2026): QR ponsel wajib, bayar kasir + online, tamu, QR dinamis, kiosk > 1 | PO + Ops F&B + Finance + IT/Security | R-S03, R-B01, R-L03, R-L01 |
| [033](ADR-033-akun-perangkat-kds.md) | Akun perangkat KDS: user internal tersandbox (pilot) vs user *share* (sebelum outlet ke-2) | SEC-74, SEC-77 | Proposed — opsi A pilot disetujui 10 Okt (Q-KD1/3); A→B dievaluasi di ADR-035 | IT + Security (+ Ops F&B) | R-S03, R-S10, R-S12, R-T04 |
| [034](ADR-034-kanal-online-bff-json2-terfilter.md) | Kanal penjualan online: BFF + `efn_online_order` (10 metode) lewat `/json/2` terfilter (`odoo-rpc-gateway` mTLS); `website_sale` ditolak; katalog pull delta 5 menit | F-55..F-74, §3.3 | Proposed — implementasi G17 selesai & GO merge (r10, 10 Okt); online mati di prod; sizing opsi B diputuskan pemilik proyek 10 Okt (operasional, bukan tanda tangan ADR); Q-ON1..13 + API key 30 h rotasi otomatis diputuskan 10 Okt (perubahan desain → `docs/10`) | IT + Security; PO (lingkup); Finance + Tax (bagian ADR-019) | R-T10, R-S03, R-S09, R-O05, R-L03, R-T15, R-S14 |
| [035](ADR-035-strategi-portal-pelanggan-vendor-franchisee-karyawan.md) | Strategi portal: pelanggan via Super-App/BFF, **vendor via `portal-gateway` terpisah** (allowlist route portal, 2FA, WAF, rate limit, mTLS, `efn_portal_guard`), franchisee via laporan Metabase/BFF, karyawan via WireGuard di HP | Q-KD2, Q-W3 | Proposed (baru 10 Okt) | IT + Security + PO (+ Procurement, Legal/DPO) | R-S15, R-S03, R-S11, R-L01 |

Catatan pemetaan: D-09 dipetakan ke ADR-001 (sudah ada) sehingga ADR-009 dipakai untuk D-08. D-10 dibahas di ADR-010 dan juga disinggung ADR-001.

## Urutan tanda tangan yang tersisa (per 10 Okt 2026 — menggantikan versi 6 Okt)
**Accepted (10 Okt 2026):** ADR-001, 004, 005, 013, 017, 018, 021, 032 (opsi B). Sudah dijawab sebelumnya: Q-E1 (kustodi), Q-E4 (mTLS), prinsip pajak configurable, penundaan vendor payment, prinsip ADR-031. Sisa yang butuh **tanda tangan arsitektur** (bukan lagi jawaban bisnis):

| Urutan | ADR / dokumen | Yang masih perlu ditandatangani | Pemutus |
|---|---|---|---|
| ~~1~~ | ~~ADR-001, 021, 018, 017, 004, 005, 013~~ | **Accepted 10 Okt 2026** | — |
| 2 | ADR-022 | Opsi PKI A1; pengecualian E1–E13. (Q-E1a/b/c, Q-E2, Q-E3 **diputuskan 10 Okt**) | IT + Security |
| 3 | ADR-023 + **ADR-035** (baru) | Pola B-a/B-b per outlet (setelah data perangkat); **revisi terbatas portal vendor** (ADR-035) | IT + Security + PO |
| 4 | ADR-024, ADR-028, ADR-029 | Teknis, biaya Rp0 — dapat ditandatangani sekaligus | IT (+ DPO/Legal untuk 028/029) |
| 4a | ADR-030 | Jawaban Q-OS1..Q-OS5 **sudah ada (10 Okt)**; tinggal formalitas status oleh IT + Security | IT + Security |
| ~~4b~~ | ~~ADR-031 rincian~~ | **Diputuskan 10 Okt** (Q-V1..Q-V6) | — |
| 4c | ADR-033 | Opsi A pilot disetujui; keputusan A→B sebelum outlet ke-2 (bersama ADR-035) | IT + Security |
| 5 | Paket UU PDP | DPO sementara (Legal) **ditetapkan**; retensi **diputuskan** (inventaris v7); tersisa DPIA sebelum go-live publik, nomor pasal (Legal), tinjauan Q-P21 | DPO (Legal) |
| 6 | ADR-027 | Nilai awal **diputuskan 10 Okt**; tersisa opini konsultan pajak: tarif Perda kota, **PBJT non-dine-in** (R-L09) | Finance + Tax |
| 7 | ADR-026 → ADR-016 | Pemilihan vendor (kriteria + DPA Indonesia, Q-P20) | Finance + Legal + IT |
| 8 | ADR-019 | Opini pajak & legal; A2 setelah opini (Q-ON9 diputuskan 10 Okt) | Finance + Tax + Legal |
| 8a | ADR-034 | Tanda tangan arsitektur kanal online; jawaban bisnis Q-ON **sudah ada** (perubahan desain di `docs/10`) | IT + Security + PO |
| 8b | ADR-020 | Formal (catatan 10 Okt: redeem = alat bayar, poin online, settlement bulanan) | Finance + IT + PO |
| 9 | ADR-025 | Formalitas | Athera + PO |
| 10 | Gelombang 2 & 3 lainnya | Seperti di bawah | — |

## Syarat GO pilot loyalty (review keamanan r7, 7 Okt 2026)

Sumber: `docs/reviews/SECURITY_REVIEW_2026-10-07-r7.md` §"Rekomendasi go/no-go". Rekomendasi reviewer: **GO bersyarat** untuk satu outlet PT Retail; alternatif lebih hati-hati: poin didapat (earn) dulu, penukaran (redeem) 1–2 minggu kemudian bila tidak ada anomali kritis.

> **Pembaruan 10 Okt 2026 (jawaban pemilik proyek):** G-2 — Q-13 tetap placeholder; Finance tetap wajib mengisi nilai > 0 sebelum produksi. G-3 — Superadmin = **1 orang** (Q-E1a, risiko R-B11 diterima); nama tetap dibutuhkan. G-7 — P-3 dijawab: standar Odoo + SOP supervisor PIN. Tambahan sebelum pilot ritel: **W-01** (poin = alat bayar, P-8) dan **W-03** (presisi IDR Rp1) — mengubah perlakuan poin setelah go-live berarti koreksi faktur (`docs/10_RENCANA_GELOMBANG_18_PLUS.md`).

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

> **Pembaruan 10 Okt 2026 (jawaban pemilik proyek):** K-2 — Q-QR1 = **ponsel wajib** (ADR-032 Accepted opsi B): kunci `self_ordering_mode` **tetap** (E-27 tidak memakai mode `mobile`); QR ponsel aktif hanya setelah E-27 (W-07) lolos review security. K-3/K-5 — Q-KD1/Q-KD3 dijawab (akun tersandbox disetujui; supervisor outlet pegang 2FA); layar = **monitor + mini PC** (bukan tablet). K-6 — SOP catatan dapur disetujui (Q-P15). K-8 — P-13 (Bakery/Pastry + Bar Kopi, 5/10 menit, tanpa expo) dan P-18 (Epson ePOS + mini PC) dijawab. Tambahan sebelum pilot F&B: **W-07** (E-27), **W-14** (PB1 + SC; ⚠ konsultan pajak untuk non-dine-in).

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

## Keputusan pemilik proyek 10 Okt 2026 (Gelombang 17 penutup)

| # | Keputusan | Isi | Dampak / konsekuensi | Ditinjau ulang bila | Status ADR |
|---|---|---|---|---|---|
| PP-1 | **Sizing pilot online: opsi B** | Produksi pilot: `ODOO_WORKERS=6` tetap (Odoo 4 CPU / 6 GB) = **POS 4 + online 2**; gateway `ODOO_RPC_MAXCONN=2`; pool json2 BFF publik 1 + saga 1 (jumlah ≤ maxconn, ditegakkan `deploy-validate`). Opsi A (worker 9, maxconn 4) tidak dipilih | Biaya Rp0 (tanpa tambah RAM/CPU). Online volume rendah: katalog/quote bisa antre/`503` saat ramai — POS terlindungi. Memenuhi syarat aktivasi no. 3 bagian keputusan; **uji beban di staging dengan POS aktif tetap wajib** | Gejala salah sizing (`why=busy`/antre gateway > 1 s sering, POS lambat), volume online naik, atau outlet online ke-2 → opsi A (`online-orders.md#sizing-worker`) | ADR-034 tetap **Proposed** |
| PP-2 | **Bahasa id_ID Odoo tidak diaktifkan dulu** | Odoo tetap en_US; label pajak ke pelanggan tetap Bahasa Indonesia lewat konfigurasi `efn_setup_*` (G17-QA-04). BFF hanya mengirim `lang` yang terpasang | Mencegah regresi label pajak (terjemahan template akan menimpa label buatan EFN) dan error `Invalid language code`. Staf memakai antarmuka Odoo berbahasa Inggris | Klien meminta UI Odoo berbahasa Indonesia → aktifkan id_ID **sebelum** install `efn_setup_company` di DB baru, atau tambah penulisan terjemahan id_ID (efn-odoo-engineer, ±1–2 hari, belum diestimasi rinci) — rincian di laporan efn-odoo-functional G17 | — (bukan ADR) |
| PP-3 | **Pilot retail jalan; enkripsi kolom PII Odoo (W-08) menyusul di G19** | Pengecualian sementara ADR-022 untuk kolom PII Odoo (HP member) selama pilot retail: tetap LUKS + TLS + akses terbatas + audit; migrasi enkripsi in-place sebelum online/F&B memakai profil pembeli | Pilot retail tidak tertahan 3–5 minggu | W-08 selesai (G19) → pengecualian dicabut | ADR-022 (catatan pengecualian) |
| PP-4 | **F&B online (E-29a) tetap G20** | Bersama QR ponsel E-27, setelah opini PBJT bawa pulang, KDS outlet F&B berjalan | Tanpa engineer Odoo tambahan | Syarat terpenuhi lebih awal + engineer kedua tersedia | ADR-032 |
| PP-5 | **Bangun `efn_account_reports`** (laporan keuangan resmi per PT: neraca, laba rugi, arus kas) | W-16, G21+, 4–6 minggu-engineer; fit-gap fungsional dulu (CE standar sebagai dasar, OCA/Cybrosys referensi saja) | Q-V3 | Finance/auditor menetapkan format | ADR-031 |
| PP-6 | **Mulai G18** sesuai `docs/10_RENCANA_GELOMBANG_18_PLUS.md` | Satu PR | — | — | — |

## Keputusan pemilik proyek 10 Okt 2026 (sesi tanya-jawab)

Sumber: jawaban pemilik proyek atas **semua** pertanyaan terbuka (60 butir, disampaikan lewat efn-lead, 10 Okt 2026). Kolom **#** = nomor butir jawaban. **Status:** *Diputuskan* · *Diputuskan — perlu konfirmasi konsultan pajak* · *Diputuskan — perlu data klien*. Kolom **Paket** merujuk `docs/10_RENCANA_GELOMBANG_18_PLUS.md`. ⚠ = catatan lead/architect yang harus dibaca bersama jawaban. Pertanyaan asal di tabel-tabel di bawah ditandai ~~coret~~ + "terjawab 10 Okt".

### A. Tata kelola, keamanan, infrastruktur
| # | ID | Jawaban | Dampak | Status | Paket |
|---|---|---|---|---|---|
| 1 | Q-P1 | **DPO = Legal (sementara)** | Legal memutuskan retensi, register insiden (Q-P16), tinjauan Q-P21 | Diputuskan | — |
| 2 | Q-E1a/b/c | **1 Superadmin** + salinan offline tersegel di **brankas Direksi**; tanpa Shamir | Bus factor 1 **diterima** → **R-B11**; mitigasi tersisa: salinan tersegel + log segel, serah terima, break-glass, uji pemulihan disaksikan (ADR-022) | Diputuskan (risiko diterima) | W-28 |
| 3 | Q-OS1 / Q-OS3 | Terima **SILO** (tanpa SLA) + object lock **GOVERNANCE** + filter | ADR-030 (catatan); pemblokir staging publik terkait storage terjawab | Diputuskan | — |
| 4 | Q-V2 / Q-V4 | Lisensi `efn_*` **LGPL-3**; terima **jadwal mundur 2–4 minggu** | ADR-031 rincian, ADR-005 catatan | Diputuskan | — |
| 5 | Gelombang 1 | **ADR-001, 021, 018, 017, 004, 005, 013 = Accepted** | Fase 1 tidak lagi terblokir tanda tangan | Diputuskan (**Accepted**) | — |
| 49 | Q-OS2 / Q-OS4 / Q-OS5 | PoC SeaweedFS 5 h **sebelum produksi**; filestore Odoo lokal terenkripsi; **backup off-site terenkripsi wajib sebelum go-live** | Masuk kesiapan produksi | Diputuskan | W-28 |
| 50 | Q-E2 / Q-E3 | **LUKS + AES-NI wajib** di pengadaan; **kolom PII Odoo dienkripsi** (pola AES-GCM key ring) | **Pekerjaan baru** (terkait Q-P21 dan HP member); revisi ADR-022 Keputusan 5 | Diputuskan | **W-08** |
| 51 | Q-V1 / Q-V3 / Q-V5 / Q-V6 | Tanpa sandbox referensi; **laporan keuangan resmi per PT dari Odoo**, Metabase untuk analitik/konsolidasi; "pihak ketiga" = semua addon non-CE; referensi OCA + Cybrosys | ⚠ Q-V3 ditafsirkan `efn_account_reports` (±4–6 minggu, sudah dianggarkan ADR-031) — konfirmasi Finance | Diputuskan | W-16 |
| 52 | Q-W1..Q-W4, Q-55 | Klien WireGuard **di perangkat** (PC kasir, mini PC KDS, HP staf), dipasang **IT internal**; **tanpa user Odoo eksternal selain portal vendor** (ADR-035); OpenWrt opsional | ADR-023 catatan + revisi terbatas lewat ADR-035 | Diputuskan — perlu data klien (jumlah & model perangkat) | W-13 (K1) |
| 33 | (IT) API key kanal | **30 hari + rotasi otomatis** (timer host Superadmin, overlap ±1 jam, alert gagal) | **Pekerjaan baru**; R-T15 berubah (PLACEHOLDER 90 h → 30 h); `api_key_duration` grup teknis ≥ 30 h + margin | Diputuskan | **W-11** |
| 40 | Q-KD2 / Q-KD4 | **Akan ada portal**: pelanggan (faktur/riwayat), vendor (PO & tagihan), franchisee (laporan), karyawan luar kantor. Token KDS tetap **7 hari** | **ADR-035 baru (Proposed)**; ADR-033 opsi B dievaluasi di sana; risiko **R-S15** | Diputuskan (arah); arsitektur menunggu tanda tangan ADR-035 | **W-13** |

### B. Pilot POS ritel & loyalty
| # | ID | Jawaban | Dampak | Status | Paket |
|---|---|---|---|---|---|
| 6 | Q-13 (+ P-7) | Parameter poin **placeholder, tetap configurable** | Syarat GO G-2 tetap: Finance mengisi nilai > 0 sebelum produksi | Diputuskan — perlu data klien (nilai final Finance) | — |
| 7 | P-1 / Q-ON1 / P-11 | Patokan brand awal: **Grand Lucky** (ritel), **Paris Baguette** (F&B), **Bacha Coffee** (ritel + F&B) | Alamat, jam, kasir, SKU tetap dari klien | Diputuskan — perlu data klien | W-21 |
| 8 | (pemetaan) | Grand Lucky = **PT Retail**; Paris Baguette = **PT F&B**; Bacha = ritel + F&B | ADR-001 catatan | Diputuskan | W-21 |
| 9 | (Bacha) | **Dua PT, dua kasir**: butik di PT Retail (PPN), kafe di PT F&B (PB1), POS terpisah | Kanal & katalog terpisah; CK (PT F&B) → butik Bacha = transaksi antar-PT (TP, Q-3) | Diputuskan | W-21, W-15 |
| 10 | P-3 (Q-27) | Standar Odoo + **SOP supervisor (PIN)**; toleransi selisih kas **Rp0** dicatat; **satu sesi per shift** | G13-2/3/5/6 **tidak** dibangun; syarat G-7 = SOP | Diputuskan | — |
| 11 | **P-8** | Penukaran poin = **ALAT BAYAR**, DPP tetap | **PERUBAHAN DESAIN**: redeem menjadi *payment method* POS, bukan diskon per kelompok pajak; kontrak §9 v6 | Diputuskan — konfirmasi konsultan pajak **disarankan** | **W-01** |
| 12 | P-9 (Q-5/Q-12) + Q-K1 | Migrasi: **consent ulang via OTP** (tanpa bukti → marketing=false); **kasir boleh mendaftarkan member** (tetap OTP); HP member lama tetap terkunci | Konfigurasi grup kasir + alur consent | Diputuskan — perlu data klien (sumber migrasi) | W-25 |
| 13 | Q-P13 | `source_ref` impor poin **wajib opak** | Validator E-25 + README template (±0,5 hari) sebelum impor saldo pertama | Diputuskan | W-25 |
| 41 | P-2 (Q-25) | Label **prepack** (prefix 21/23), sebaiknya mencetak **GS1** | Merek/model timbangan dari klien | Diputuskan — perlu data klien | W-20 |
| 42 | P-4 (Q-28) | Mesin promo **standar Odoo** `pos_loyalty` (promo/kupon/beli X gratis Y) + pricelist member **5 %** (placeholder) + promo ber-OTP; tipe "poin" Odoo tidak dipakai (ADR-020) | Konfigurasi | Diputuskan — perlu data klien (daftar promo Marketing) | W-10 |
| 43 | P-5 (Q-29) | Tunai + **EDC manual** + **QRIS statis**; split boleh; pembulatan Rp1 (Q-ON6); **refund ke metode asal** | G13-4 menjadi wajib | Diputuskan | W-24 |
| 44 | P-10 (Q-34) | Label **GS1 (lot + kedaluwarsa)** di-scan kasir → lot tercatat; tanpa GS1 → **FEFO** otomatis | Fit-gap nomenklatur GS1 POS CE 19 | Diputuskan — perlu data klien (label supplier) | W-20 |
| 45 | P-6 (Q-55/Q-56) | Fiber + **4G cadangan** (failover), WireGuard di perangkat, PC kasir Linux/Windows, Epson ePOS, laci kas, scanner 2D (GS1/QR) | Model dari IT klien | Diputuskan — perlu data klien | — |

### C. Pilot F&B, KDS, QR order
| # | ID | Jawaban | Dampak | Status | Paket |
|---|---|---|---|---|---|
| 14 | P-13 (Q-19) | Per outlet: stasiun **Bakery/Pastry + Bar Kopi**; warn **5** / late **10** menit; **tanpa expo**; retensi tiket **90 h** (placeholder) | Konfigurasi `efn_setup_pos`; syarat K-8 (Q-19) terjawab | Diputuskan | W-22 |
| 15 | P-16 (Q-22) | **PB1 10 % + SC 5 % dine-in saja** (takeaway tanpa SC); **SC masuk DPP PB1**; harga menu **exclude** | ADR-027 catatan nilai awal; cek tarif PB1 kota outlet; online F&B harus konsisten | Diputuskan — perlu konfirmasi konsultan pajak (tarif Perda) | W-14 |
| 37 | P-12 (Q-18) | Dine-in (PB1 + SC); takeaway/3PL/pickup **tanpa SC**; "selain dine-in tidak dikenakan pajak" → fiscal position per layanan **tanpa PB1** untuk non-dine-in | ⚠ **WAJIB konfirmasi konsultan pajak**: UU HKPD — PBJT makan-minum umumnya juga berlaku untuk takeaway; cek Perda kota → **R-L09** | Diputuskan — **perlu konfirmasi konsultan pajak** | W-14 |
| 16 | P-17 / **Q-QR1** | **WAJIB QR ponsel** | **ADR-032 Accepted (opsi B)**; E-27 dibangun sebelum pilot F&B (±3–4 minggu-engineer) | Diputuskan | **W-07** |
| 16 | Q-QR2 | Bayar **keduanya**: di kasir (sekarang) + online di ponsel (setelah vendor payment, A-4) | E-27 tahap 1 = kasir; tahap 2 = online | Diputuskan — perlu data klien (vendor payment) | W-07 |
| 16 | Q-QR3 | **Tamu**; login OTP opsional (untuk poin) | Default tanpa PII | Diputuskan | W-07 |
| 16 | Q-QR4 | **QR dinamis per sesi meja** | Sesuai draf ADR-032 | Diputuskan | W-07 |
| 16 | Q-QR5 | Kiosk tetap, jumlah per outlet **configurable (> 1)** | Tiap kiosk = peer WG | Diputuskan — perlu data klien (jumlah/model) | W-07 |
| 17 | P-18 (Q-24/Q-56) | **Epson ePOS** (dapur & struk) + layar KDS = **monitor + mini PC** (klien WG di mini PC); cetak cadangan ke printer dapur | Runbook perangkat; syarat K-8 (Q-24) terjawab | Diputuskan — perlu data klien (model) | W-23 |
| 17 | Q-KD1 / Q-KD3 | Akun KDS tersandbox **disetujui**; **supervisor outlet** pegang 2FA & boleh login ulang | ADR-033 opsi A untuk pilot; syarat K-3/K-5 terjawab | Diputuskan | — |
| 38 | P-14 (Q-20) | Split/gabung/pindah meja, course, open bill = **semua kasir** (standar CE) | Tidak ada pekerjaan | Diputuskan | — |
| 39 | P-15 (Q-21) | **Campuran**: tanpa bahan = atribut; berbahan & berharga = add-on/combo potong stok | Master menu | Diputuskan — perlu data klien (daftar modifier Ops F&B) | W-27 |

### D. Penjualan online (Q-ON) & poin online
| # | ID | Jawaban | Dampak | Status | Paket |
|---|---|---|---|---|---|
| 18 | **Q-ON4** | **Reservasi stok** saat checkout; tunggu bayar **1 jam**; release otomatis bila tidak dibayar | **PERUBAHAN DESAIN**: E-28 reservasi + BFF `ONLINE_PAYMENT_TIMEOUT_SECONDS=3600`, Odoo `payment_timeout ≥ 70 mnt`, kontrak §10.6, smoke | Diputuskan | **W-02** |
| 19 | **Q-ON6** | Presisi IDR **global ke Rp1** (0 desimal) | **PERUBAHAN BESAR**: POS + akuntansi semua PT; fit-gap + regresi penuh; cash rounding kanal mungkin tidak perlu → **R-T17** | Diputuskan | **W-03** |
| 20 | Q-ON8 | Faktur **saat bayar** | Sesuai yang dibangun | Diputuskan | — |
| 21 | Q-ON9 (+ Q-1/Q-2) | **MDR ditanggung PT penjual** (proporsional porsi); settlement Holding → PT **harian**; jurnal Holding **A1 manual/impor**; A2 setelah opini pajak | ADR-019 catatan; laporan settlement memuat MDR per porsi | Diputuskan — A2 perlu opini konsultan pajak | W-26 |
| 25 | Q-ON2 | F&B online **bersamaan pilot ritel**, via **SO + jembatan KDS (E-29a)** | **Pekerjaan baru** E-29a | Diputuskan | **W-04** |
| 26 | Q-ON3 (= Q-39/Q-40) | **Pickup + 3PL + kurir internal** | **Pekerjaan besar**: zona, ongkir, slot, integrasi 3PL; vendor 3PL dipilih klien | Diputuskan — perlu data klien (vendor 3PL) | **W-05** |
| 27 | Q-ON5 | Ritel: harga **termasuk pajak** + label. F&B: tampilkan **PB1 + SC sesuai outlet (exclude)** dengan rincian sebelum bayar | Kontrak §10 rincian pajak F&B | Diputuskan — F&B ikut konfirmasi P-12 | W-04 |
| 28 | Q-ON7 | Faktur atas nama/NPWP pembeli **opsional** di checkout | PII masuk Odoo (Q-P21); revisi kontrak §10 + PDP → **R-L10** | Diputuskan | **W-06** |
| 29 | Q-ON10 | Promo online **lengkap** (pricelist, kupon/kode, otomatis, beli X gratis Y) memakai **mesin standar** `sale_loyalty`/`pos_loyalty`, bukan modul baru; tipe "poin" Odoo tidak dipakai | Integrasi E-28 (quote menerapkan program, beku di `quote_hash`), ±1–2 minggu, fit-gap dulu | Diputuskan | **W-09** |
| 30 | Q-ON11 | Approver CS (≠ pembuat), ≤ 2×24 jam setelah diambil, ke metode asal, tanpa poin | Sesuai yang dibangun | Diputuskan | — |
| 31 | Q-ON12 (+ Q-8) | Katalog + pricelist **per brand** (kanal Grand Lucky, Paris Baguette, Bacha butik, Bacha kafe); override per outlet nanti | Kanal per brand | Diputuskan | **W-21** |
| 32 | Q-ON13 | Tahan s.d. tutup toko **H+1**, lalu CS (tanpa batal otomatis) | Sesuai yang dibangun | Diputuskan | — |
| 34 | (Finance) poin online | **EARN + REDEEM online**; redeem online = alat bayar (konsisten P-8) | Kontrak §9/§10, BFF checkout, E-28 payment line poin, frontend | Diputuskan | **W-12** |
| 60 | Q-8 / Q-22 | Katalog **per brand** (pemilik data = merchandising tiap brand); PB1 & SC **configurable per outlet/kota** (ADR-027), nilai awal P-16 | — | Diputuskan | W-21, W-14 |

### E. UU PDP & retensi
| # | ID | Jawaban | Dampak | Status | Paket |
|---|---|---|---|---|---|
| 22 | Q-P17 | Angka usulan **disetujui**: tautan akun 730 h, event 180 h, keuangan 10 th, kode ambil maks 7 h, cart 7 h + **hapus saat erasure** | Default tetap; `DEL cart:<member_id>` di erasure (±1 jam) | Diputuskan | W-19 |
| 23 | Q-P18 | Penundaan hapus akun sah, **maks 14 hari** | Eskalasi/alert + CS menyelesaikan; setelah 14 h erasure jalan | Diputuskan | **W-18** |
| 24 | Q-P20 | Vendor payment **wajib DPA** + pemrosesan/penyimpanan **di Indonesia**; data pembayar minimum, tanpa PAN | Kriteria wajib pemilihan vendor (ADR-026) | Diputuskan — perlu data klien (vendor) | — |
| 35 | Q-P19 | Retensi audit CS **10 tahun** (`ONLINE_ADMIN_AUDIT_RETENTION_DAYS` 400 → **3650**) + SOP alasan tanpa nama pelanggan | Ubah default env | Diputuskan | W-19 |
| 36 | Q-P21 | **Profil lengkap** pembeli di Odoo (nama, NPWP, alamat, HP, email) **bila pelanggan meminta faktur** | ⚠ Bertentangan dengan minimisasi — disimpan **hanya atas permintaan**; terenkripsi (Q-E3); erasure menganonimkan partner pembeli kecuali data wajib faktur 10 th; revisi kontrak §10; review keamanan; **ditandai tinjauan Legal (DPO sementara)** → R-L10 | Diputuskan — tinjauan Legal | W-06, W-08 |
| 46 | Q-P12 | `erasure_suppression` **365 hari** (default 90 → 365) | Ubah default env (batas config 730) | Diputuskan | W-19 |
| 47 | Q-P14 | Outbox poin Odoo & riwayat query/akun Metabase **10 tahun** | GC outbox Odoo (3650 h) | Diputuskan | W-19 |
| 48 | Q-P15 / Q-P16 | Tiket KDS **90 h** + SOP catatan dapur tanpa nama; log dev lama **dibiarkan kedaluwarsa** (±7 Nov, tidak wajib notifikasi); register insiden dipegang **Legal** | Syarat K-6 = SOP | Diputuskan | — |
| 53 | Q-P2..Q-P8 | Retensi usulan: transaksi **10 th**; usia member **min 17 th**; tombstone acak; ClickHouse mart **5 th teranonimkan**; lokasi Price Hunter **90 h** | ⚠ Q-P7 vs Q-6: ditafsirkan baris 10 th, kolom pseudonim dianonimkan setelah 5 th; job NULL lokasi 90 h baru; pernyataan usia 17 th di pendaftaran | Diputuskan | W-19 |
| 54 | Q-P9 / Q-P10 | Log audit Odoo **730 h** (min 365); log kontrol/purge/akses **tanpa batas**; salinan ClickHouse **10 th**; grup auditor lintas PT = **Legal + Internal Audit** | Konfigurasi grup | Diputuskan — perlu data klien (nama anggota) | — |
| 55 | Q-P11 | Anonimisasi member tidak aktif **24 bulan**, notifikasi **30 h** sebelumnya; saldo poin hangus sesuai syarat program (**perlu persetujuan Finance**) | **Pekerjaan baru** (job BFF + Odoo + notifikasi) | Diputuskan — perlu persetujuan Finance | **W-17** |
| 56 | Q-T1..Q-T6 | WA Local Storage region terdekat bila tersedia; **Cloudflare proxy penuh**; payment di Indonesia; kunci tidak di GitHub; **DPIA wajib sebelum go-live publik** | `PDP_TRANSFER_LINTAS_NEGARA.md` | Diputuskan | W-28 |
| 59 | Q-6 / Q-49 | **DWH 10 tahun** (ubah dari mart 5 th); auditor eksternal & format konsolidasi masih dari Finance | TTL ClickHouse | Diputuskan — perlu data klien (auditor) | W-19, W-16 |

### F. Keuangan antar-PT
| # | ID | Jawaban | Dampak | Status | Paket |
|---|---|---|---|---|---|
| 57 | Q-3 | Transfer pricing **cost-plus** (markup ditetapkan Finance pusat) untuk transaksi **antar-PT**. **Central Kitchen bukan PT** — CK ada di PT F&B → CK → outlet F&B = transfer internal (tanpa TP). TP berlaku mis. Holding ↔ PT, CK (PT F&B) → butik Bacha (PT Retail) | `efn_intercompany` + harga transfer; R-L04 | Diputuskan — perlu konfirmasi konsultan pajak (TP) + markup Finance | W-15 |
| 58 | Q-4 | Settlement poin lintas brand **bulanan** via jurnal inter-company (PT penerbit menanggung nilai redeem di PT lain; netting Finance pusat) | ADR-020/019 catatan | Diputuskan | W-15 |

### G. Masih menunggu data / opini klien (setelah 10 Okt 2026)
| Item | Pemilik | Memblokir |
|---|---|---|
| Alamat, jam buka, jumlah kasir, SKU, meja/lantai outlet **Grand Lucky**, **Paris Baguette**, **Bacha** (butik & kafe) | PO + Ops | Semua pilot; PB1 kota; pola WireGuard per outlet |
| **Model perangkat**: PC kasir, mini PC KDS, printer Epson, scanner 2D, timbangan, router/4G, kiosk (jumlah & model) | IT klien | W-20, W-23, ADR-023 |
| **Daftar promo** (Marketing) dan **daftar modifier** (Ops F&B) | Marketing, Ops F&B | W-10, W-27 |
| Nama **Sales Manager** Odoo dan **approver refund** (orang berbeda), saksi review akses | Superadmin + Finance | Syarat aktivasi online A-5 |
| Nilai & batas poin final (Q-13) | Finance | Syarat GO G-2 |
| Anggota grup auditor lintas PT (nama Legal + Internal Audit) | Legal | Q-P10 |
| **Auditor eksternal** & format konsolidasi | Finance | W-16 |
| **Vendor payment** (A-4, DPA Indonesia) dan **vendor 3PL** | Finance + Legal + IT | Aktivasi online, W-05, W-07 tahap 2, W-26 |
| Opini **konsultan pajak**: **P-8** (poin alat bayar), **P-12 / takeaway PBJT**, tarif PB1 kota outlet, **Q-3 TP**, ADR-019 (agen penagih, A2) | Finance + Tax | W-01 (konfirmasi), W-14, W-04, W-15 |
| Markup TP per pasangan PT; dasar earn bila sebagian dibayar poin; syarat hangus poin (Q-P11) | Finance | W-15, W-01, W-17 |
| Q-PT1..Q-PT6 (ADR-035: vendor yang butuh portal, fungsi vendor, franchisee, HP dinas/BYOD, domain, pentest) | IT + Procurement + PO | W-13 |

## Syarat aktivasi produksi online (review r10 re-review akhir, 10 Okt 2026)

Sumber: `docs/reviews/SECURITY_REVIEW_2026-10-09_r10.md` §"Rekomendasi merge". **GO merge ke `main`**; `ONLINE_ENABLED=true` di produksi **NO-GO** sampai semua butir di bawah terpenuhi. Teknis: `deploy-validate` menolak online prod tanpa provider payment nyata dan API key kanal nyata.

> **Pembaruan 10 Okt 2026 (jawaban pemilik proyek):** A-4 — kriteria vendor ditambah DPA + pemrosesan di Indonesia (Q-P20); vendor belum dipilih. A-5 — nama Sales Manager & approver masih ditunggu. A-7 — Q-ON1/4/6/8/9 dan Q-P17..Q-P21 **dijawab**, tetapi Q-ON4 (reservasi 1 jam) dan Q-ON6 (presisi IDR Rp1) **mengubah desain**, sehingga A-7 baru terpenuhi setelah **W-02** dan **W-03**. Syarat tambahan aktivasi: **W-11** (API key 30 hari + rotasi otomatis), **W-18** (batas penundaan hapus akun 14 hari), **W-19** (default retensi baru).

| # | Syarat | Pemilik | Status 10 Okt | Butuh keputusan klien? |
|---|---|---|---|---|
| A-1 | CI `odoo-test` hijau dengan `sale_stock` + `pos_sale` + `efn_online_pos_sale` (signature `_load_pos_data_domain`/`load_sale_order_from_pos` 19.0) dan test guard `stock.move` | Tim teknis (odoo-engineer + devsecops) | Menunggu CI PR #36 | Tidak |
| A-2 | Smoke/UAT: picking SO online tidak bisa divalidasi di luar wizard (ubah tipe/pindah move); POS menolak settle SO online | QA | Jalur Odoo lulus di smoke dev (267/0); **jalur POS (`efn_online_pos_sale`) belum diuji di stack dev** | Tidak |
| A-3 | Sizing `ODOO_WORKERS ≥ worker POS + ODOO_RPC_MAXCONN` diputuskan, lalu **uji beban stok/quote di staging dengan POS aktif** | Keputusan: **pemilik proyek** (selesai, PP-1); uji: devsecops + QA | Keputusan **selesai** (opsi B); konfigurasi prod sedang diterapkan devsecops; uji beban **menunggu staging** | Ya, tidak langsung: server staging (ADR-004) |
| A-4 | Adapter vendor payment memakai `refundId` sebagai idempotency key refund (L-17, ADR-016) | **Finance + Legal + IT** (pilih vendor), lalu bff-engineer | **Belum** — vendor belum dipilih | **Ya — ADR-016/026** (+ DPA, Q-P20) |
| A-5 | Governance SEC-111/SEC-112: larangan rangkap Sales Manager Odoo ↔ `efn-cs-approver` di runbook + review akses bulanan; `settle-manual` hanya approver/Finance | Runbook: architect (**selesai**); pelaksanaan: **Superadmin + Finance**; kode SEC-112: bff-engineer | Aturan & prosedur **tertulis** (`online-orders.md#sec-111`, `key-custody.md` §2, R-S14). SEC-112 **diperbaiki** (`32686c3`, approver ≠ pembuat, smoke lulus) — belum dibaca ulang reviewer. Register peran + review pertama **belum** (butuh daftar orang) | **Ya** — siapa Sales Manager, siapa approver (orang berbeda), saksi review (Finance/Internal Audit) |
| A-6 | SEC-110 (tolak tulis field kunci move/picking SO online) dijadwalkan; boleh menyusul aktivasi bila alert `handover_no_picking` aktif | odoo-engineer + devsecops | Sedang dikerjakan di working tree (belum di-commit/di-review per penulisan ini) | Tidak |
| A-7 | *(tambahan architect, non-teknis)* Keputusan bisnis minimum pilot: Q-ON1, Q-ON4, Q-ON8, Q-ON9, Q-ON6 (prioritas A) + paket DPO Q-P17..Q-P21 | **PO + Ops + Finance + Tax + Legal/DPO** | **Belum** (default lead dipakai) | **Ya** |

Kondisi berhenti setelah aktivasi: alert `EfnOnlineAmountMismatch`, `EfnOnlineCsDualRole`, anomali `review: handover_no_picking` berulang, atau refund tanpa approver → `ONLINE_ENABLED=false` (runbook `online-orders.md`); POS tetap berjalan.

## ~~Keputusan klien yang masih terbuka — Fase 3 penjualan online (baru, Gelombang 17, 9 Okt 2026)~~ — **semua butir terjawab 10 Okt 2026**
> Seluruh pertanyaan di bagian ini dijawab pemilik proyek 10 Okt 2026 — lihat **"Keputusan pemilik proyek 10 Okt 2026 (sesi tanya-jawab)"** di atas. Tabel dipertahankan sebagai jejak; yang masih menunggu data/opini klien ada di subbagian G bagian itu.
Sumber: `docs/08_ODOO_FIT_GAP.md` §8.7 (functional G17), `docs/04 §10.1`, ADR-034, ADR-019 (catatan revisi). Semua memblokir **pilot online**, bukan pengembangan; kolom default = yang dipakai tim sekarang (keputusan lead 9 Okt, configurable).

| Prioritas | ID | Pertanyaan | Pemutus | Default bila tidak dijawab | Memblokir |
|---|---|---|---|---|---|
| **A** | ~~Q-ON1~~ terjawab 10 Okt | PT & outlet pilot online, jam ambil, jumlah SKU online | PO + Ops | PT Retail, toko pilot Fase 1 | data kanal/outlet |
| **A** | ~~Q-ON4~~ terjawab 10 Okt | Reservasi stok saat checkout atau cek saja? Batas bayar? Stok kurang setelah bayar: batal + refund penuh / refund baris / hubungi pelanggan? | PO + Ops + Finance | cek saja, 15 menit, batal + refund penuh porsi PT | saga, UX |
| **A** | ~~Q-ON8~~ terjawab 10 Okt | Faktur online dibuat saat bayar atau saat diambil? | Finance + Tax | saat bayar | E-28 `invoice_on_paid` |
| **A** | ~~Q-ON9 (+ Q-1, Q-2, Q-4)~~ terjawab 10 Okt | Dana titipan: MDR ditanggung siapa, bukti potong, periode settlement Holding → PT, jurnal Holding manual (A1) atau otomatis (A2)? | Finance + Tax + Legal | A1 manual harian; akun PLACEHOLDER | ADR-019; go-live online |
| **A** | ~~Q-ON6~~ terjawab 10 Okt | Pembulatan Rupiah: presisi IDR global ke 1 atau pembulatan kas per faktur? | Finance | pembulatan kas Rp1 | `amount_payable` bulat |
| B | ~~Q-ON2~~ terjawab 10 Okt | F&B online: SO + jembatan KDS (E-29a) atau `pos.order` lewat E-27 (E-29b)? Kapan? | PO + Ops F&B + IT | F&B online mati | E-29, ADR-032 |
| B | ~~Q-ON3 (= Q-39/Q-40)~~ terjawab 10 Okt | Pesan-antar: zona, ongkir, subsidi, slot & kapasitas, 3PL | PO + Ops + Finance | mati | delivery |
| B | ~~Q-ON5~~ terjawab 10 Okt | Harga tampil termasuk pajak? Label PB1/PPN di bukti order? | Finance + Tax | termasuk pajak + label | tampilan |
| B | ~~Q-ON7~~ terjawab 10 Okt | Pelanggan online butuh faktur atas nama/NPWP sendiri? Data apa yang boleh disimpan di Odoo? | Finance + DPO | tidak (partner generik) | PII di Odoo |
| B | ~~Q-ON10~~ terjawab 10 Okt | Promo online cukup pricelist atau perlu kupon/beli X gratis Y? | PO + Marketing | pricelist saja | E-28 (assert total) |
| B | ~~Q-ON11~~ terjawab 10 Okt | Refund: siapa menyetujui, batas waktu, retur setelah diambil, hanya ke metode asal, dampak poin | Ops + Finance | CS menyetujui, ≤ 2×24 j, ke metode asal; tanpa poin | CLI refund, UI CS |
| B | ~~Q-ON12 (+ Q-8)~~ terjawab 10 Okt | Katalog per PT atau per brand dalam PT? Pricelist per PT atau per outlet? | PO + Ops | per PT, satu pricelist | kanal |
| B | ~~Q-ON13~~ terjawab 10 Okt | Pesanan tidak diambil: lama ditahan, batal otomatis dengan/tanpa refund? | Ops + Finance | ditahan sampai tutup toko H+1, tanpa batal otomatis | cron E-28 |
| B | ~~(IT)~~ terjawab 10 Okt | Masa berlaku API key kanal online (PLACEHOLDER 90 hari) & pemegang rotasi | IT + Security | 90 hari, Superadmin | runbook rotasi |
| C | ~~(Finance)~~ terjawab 10 Okt | Poin online: earn saat diambil? redeem di checkout online? | Finance + PO | ditunda (tidak ada poin online di pilot) | §10.14 |
| ~~B~~ | ~~(IT) sizing~~ | ~~`ODOO_WORKERS` vs `ODOO_RPC_MAXCONN`~~ | — | **Diputuskan 10 Okt (PP-1): opsi B** | — |

### UU PDP — tambahan inventaris v6 (baru, Gelombang 17 penutup, 10 Okt 2026)
Rincian: `docs/compliance/PDP_DATA_INVENTORY.md` §2.1b, §2.2c, §4.

| Prioritas | ID | Pertanyaan | Pemutus | Default bila tidak dijawab | Memblokir |
|---|---|---|---|---|---|
| **A** | ~~Q-P17~~ terjawab 10 Okt | Paket retensi online (L-5): tautan pesanan ↔ akun 730 h, event 180 h, baris keuangan 10 th, kode ambil maks 7 h, keranjang 7 h (+ hapus keranjang saat erasure) | DPO (+ Finance untuk 10 th) | Angka usulan berjalan | Aktivasi online prod (A-7) |
| **A** | ~~Q-P18~~ terjawab 10 Okt | Penundaan hapus akun selama ada pesanan dibayar yang belum selesai (L-4, `409 open_online_orders`): sah? batas maksimum? | DPO + Legal | Tunda sampai pesanan selesai/dibatalkan CS | A-7 |
| B | ~~Q-P19~~ terjawab 10 Okt | Retensi `online_admin_audit` (kini tak terbatas) + SOP alasan CS/chatter tanpa nama pelanggan | DPO + Finance | Tak terbatas + SOP | — |
| **A** | ~~Q-P20~~ terjawab 10 Okt | Transfer data ke payment gateway: DPA, lokasi, data pembayar yang diminta vendor | DPO + Legal + Finance | — (vendor belum dipilih) | A-4 |
| B | ~~Q-P21~~ terjawab 10 Okt | PII pembeli di Odoo bila faktur atas nama/NPWP pembeli (Q-ON7) | DPO + Finance + Tax | Tidak (partner generik) | Revisi kontrak §10 bila "ya" |

## ~~Keputusan klien yang masih terbuka (gabungan, per 9 Okt 2026 — Gelombang 15)~~ — **semua butir terjawab 10 Okt 2026**
> Seluruh pertanyaan di bagian ini dijawab pemilik proyek 10 Okt 2026 — lihat **"Keputusan pemilik proyek 10 Okt 2026 (sesi tanya-jawab)"** di atas. Tabel dipertahankan sebagai jejak; yang masih menunggu data/opini klien ada di subbagian G bagian itu.

### Pilot POS F&B + KDS (baru, Gelombang 15)
Sumber: `docs/08_ODOO_FIT_GAP.md` §7.6 (functional G14), review r8, ADR-032, ADR-033. Semua memblokir **outlet pilot F&B**, bukan pengembangan.

| Prioritas | ID | Pertanyaan | Pemutus | Default bila tidak dijawab | Memblokir |
|---|---|---|---|---|---|
| **A** | ~~P-11~~ terjawab 10 Okt | Outlet F&B pilot: alamat/kota, jam buka, jumlah meja & lantai, jumlah kasir/KDS/kiosk | PO + Ops F&B | — | Semua persiapan; PB1 kota; pola WireGuard B-a/B-b |
| **A** | ~~P-13 (Q-19)~~ terjawab 10 Okt | Stasiun dapur & kategori menu per stasiun; target menit per stasiun; perlu stasiun *expo/pass*?; retensi tiket | Ops F&B | Demo: Dapur Panas + Bar; warn 10 / late 15 menit; retensi 90 h (PLACEHOLDER) | K-8; konfigurasi stasiun |
| **A** | ~~P-16 (Q-22 + konsultan pajak)~~ terjawab 10 Okt | Tarif PB1 kota pilot, service charge (ada? %? masuk DPP PB1? takeaway kena SC?), harga menu include/exclude | Finance + Tax | Configurable (ADR-027), belum ada nilai | Go-live F&B (struk & pajak) |
| **A** | ~~P-17 (Q-23) = **Q-QR1**~~ terjawab 10 Okt | QR order: kiosk cukup, atau wajib ponsel pelanggan? | PO + Ops F&B | Kiosk saja (ADR-032 opsi A) | K-2; pengadaan kiosk; penjadwalan E-27 (±3–4 minggu-engineer) |
| **A** | ~~P-18 (Q-24/Q-56)~~ terjawab 10 Okt | Model printer dapur & struk (Epson ePOS? sertifikat otomatis?), layar KDS (tablet/monitor + OS), kebijakan cetak cadangan | IT + Ops F&B | Epson ePOS; `kds_printer` (cetak selalu) | K-1, K-8; pengadaan perangkat |
| **A** | ~~Q-KD1 / Q-KD3~~ terjawab 10 Okt | Terima akun tablet internal tersandbox untuk pilot (ADR-033)? Siapa pemegang 2FA tablet & yang boleh login ulang? | IT + Security + Ops F&B | Ya / supervisor outlet | K-3, K-5 |
| B | ~~P-12 (Q-18)~~ terjawab 10 Okt | Layanan: dine-in, takeaway, delivery telepon/ojol? takeaway beda pajak/SC? | PO + Ops F&B | Dine-in + takeaway; delivery lewat Super-App (nanti) | Preset & fiscal position |
| B | ~~P-14 (Q-20)~~ terjawab 10 Okt | Split/gabung/pindah meja, course, open bill — peran siapa? | Ops F&B | Standar CE (semua kasir) | Hak akses kasir |
| B | ~~P-15 (Q-21)~~ terjawab 10 Okt | Daftar modifier; mana yang memakai bahan (harga tambahan, stok)? menu paket pilihan vs tetap | Ops F&B + CK | Modifier = atribut tanpa stok; berbahan = add-on/combo | Master menu |
| B | ~~Q-QR2..Q-QR5~~ terjawab 10 Okt | Bila ponsel: bayar di kasir/online; tamu vs login; QR dinamis vs statis + PIN; jumlah & model kiosk | PO + Finance + Ops F&B | Bayar di kasir; tamu; QR dinamis; 1 kiosk | E-27 (bila Q-QR1 = ponsel) |
| B | ~~Q-KD2 / Q-KD4~~ terjawab 10 Okt | Akan ada user portal Odoo? (efek 2FA opsi share); rotasi token 7 hari cukup? | IT | Tidak ada / 7 hari | ADR-033 opsi B (sebelum outlet ke-2) |

### UU PDP — tambahan inventaris v5 (baru, Gelombang 15)
| Prioritas | ID | Pertanyaan | Pemutus | Default bila tidak dijawab | Memblokir |
|---|---|---|---|---|---|
| B | ~~Q-P15~~ terjawab 10 Okt | Retensi tiket KDS (90 h vs lebih pendek) dan SOP catatan dapur vs masking teknis (±0,5–1 hari) | DPO + Ops F&B | 90 h + SOP | K-6 (SOP) |
| B | ~~Q-P16~~ terjawab 10 Okt | Near-miss redaksi log dev: setujui klasifikasi "tidak wajib notifikasi"; hapus baris dev sekarang (delete API) atau tunggu retensi ±7 Nov; pemilik register insiden | DPO | Tunggu retensi; register sementara di `PDP_DATA_INVENTORY.md` §6 | — |

## ~~Keputusan klien yang masih terbuka (gabungan, per 7 Okt 2026 — Gelombang 14)~~ — **semua butir terjawab 10 Okt 2026**
> Seluruh pertanyaan di bagian ini dijawab pemilik proyek 10 Okt 2026 — lihat **"Keputusan pemilik proyek 10 Okt 2026 (sesi tanya-jawab)"** di atas. Tabel dipertahankan sebagai jejak; yang masih menunggu data/opini klien ada di subbagian G bagian itu.

Sudah dijawab: Q-E1 (kustodi = Superadmin), Q-E4 (mTLS), prinsip pajak configurable, vendor payment ditunda, prinsip tanpa modul Odoo pihak ketiga (ADR-031). Kolom "Default bila tidak dijawab" = yang dipakai tim sekarang.

### Pilot POS retail & loyalty (baru, Gelombang 14)
Sumber: `docs/08_ODOO_FIT_GAP.md` §6.6 (functional G13), review r7. Semua memblokir **outlet pilot**, bukan pengembangan.

| Prioritas | ID | Pertanyaan | Pemutus | Default bila tidak dijawab | Memblokir |
|---|---|---|---|---|---|
| **A** | ~~**Q-13** (+ P-7)~~ terjawab 10 Okt | Nilai poin, minimum tukar, maks % order, masa berlaku, kategori tanpa poin, dasar perolehan, perlakuan retur berpoin, saldo negatif/utang poin, **dan batas anti-fraud** (per order, harian per member, per PT/jam, per kasir/jam) | **Finance** + PO | 1 poin/Rp10.000, 1 poin = Rp100, min 50, maks 50 %, 365 hari; batas 5.000 / 2.000 / 50.000 / 10.000 poin (placeholder) | Syarat GO G-2; liabilitas poin di neraca |
| **A** | ~~P-1~~ terjawab 10 Okt | Toko pilot mana (alamat, jam buka, jumlah kasir per shift, jumlah SKU aktif)? | PO + Ops | — | Semua persiapan pilot; kapasitas katalog offline (Q-7) |
| **A** | ~~P-3 (Q-27)~~ terjawab 10 Okt | Peran kasir: refund & retur tanpa struk wajib supervisor? void baris? toleransi selisih kas (Rp)? setoran tunai? satu sesi per shift? persetujuan inline + jejak atau cukup PIN supervisor? | Ops Outlet + Finance | Standar Odoo (supervisor login PIN, tanpa jejak "disetujui oleh"); SOP retur tanpa struk wajib supervisor (G-7) | Syarat GO G-7; modul opsional G13-2/3/5/6 (±1–3 minggu-engineer bila diminta) |
| **A** | ~~P-8~~ terjawab 10 Okt | Konsultan pajak: PPN atas penukaran poin (potongan harga mengurangi DPP **atau** alat bayar)? | Finance + Tax | Potongan harga per kelompok pajak (desain saat ini) | Pembukuan pajak redeem; ubah perlakuan setelah go-live = koreksi faktur |
| **A** | ~~P-9 (Q-5/Q-12)~~ terjawab 10 Okt | Sumber data migrasi (sistem lama), bukti consent member lama, kasir boleh daftarkan member? | PO + DPO | Member tanpa bukti consent → marketing `false`; Q-K1 | Impor member & saldo awal; **lihat Q-P13** |
| B | ~~P-2 (Q-25)~~ terjawab 10 Okt | Timbangan: merek/model, label prepack atau terhubung ke kasir, format label | Ops Outlet | Nomenklatur EFN Retail (prefix 21 berat / 23 harga) | Produk timbang |
| B | ~~P-4 (Q-28)~~ terjawab 10 Okt | Daftar promo pilot konkret, harga member (pricelist % atau tetap), promo ber-OTP mana | PO + Marketing | Pricelist member 5 % (demo) | Konfigurasi promo |
| B | ~~P-5 (Q-29)~~ terjawab 10 Okt | Metode bayar pilot (tunai, EDC bank apa, QRIS), split payment, pembulatan tunai, refund wajib ke metode asal? | Finance + Ops | Tunai + EDC manual; refund tidak dibatasi ke metode asal (standar CE) | G13-4 (opsional) |
| B | ~~P-6 (Q-55/Q-56)~~ terjawab 10 Okt | Internet toko (utama + cadangan 4G), perangkat kasir, printer, laci kas, scanner, layar pelanggan | IT + Ops | — | Pengadaan perangkat outlet; mode offline |
| B | ~~P-10 (Q-34)~~ terjawab 10 Okt | Produk fresh perlu lot/kedaluwarsa di kasir? | Ops | Tidak (lot di gudang saja) | Alur kasir produk fresh |

### UU PDP — tambahan inventaris v4 (baru, Gelombang 14)
| Prioritas | ID | Pertanyaan | Pemutus | Default bila tidak dijawab | Memblokir |
|---|---|---|---|---|---|
| **A** | ~~**Q-P13**~~ terjawab 10 Okt | Nomor referensi data lama (`source_ref`) saat impor saldo poin wajib opak, bukan nomor pelanggan sistem lama? | DPO + Finance | **Rekomendasi: wajib opak** (±0,5 hari); tanpa keputusan, id lama tersimpan permanen setelah akun dihapus | **Impor saldo pertama** (entri tidak bisa diubah) |
| B | ~~Q-P12~~ terjawab 10 Okt | Masa simpan daftar tolak akun terhapus (`erasure_suppression`) + terima risiko uji keanggotaan oleh pemegang kunci | DPO | 90 hari | — |
| B | ~~Q-P14~~ terjawab 10 Okt | Retensi outbox poin Odoo (kini tanpa batas) & riwayat query/akun Metabase | DPO + Finance | tanpa batas / selama dipakai | — |

### Umum (dari Gelombang 12, diperbarui)

| Prioritas | ID | Pertanyaan | Sumber | Pemutus | Default bila tidak dijawab | Memblokir |
|---|---|---|---|---|---|---|
| **A** | ~~Q-P1~~ terjawab 10 Okt | Penetapan DPO + kontak publik | PDP_DATA_INVENTORY | Direksi | Legal sebagai DPO sementara | Semua keputusan retensi, notifikasi breach |
| **A** | ~~Q-E1a/b/c~~ terjawab 10 Okt | Jumlah Superadmin (≥ 2), Shamir 2-dari-3, lokasi salinan offline kedua | ADR-022 | Direksi + IT | 1 orang = bus factor 1 | Staging publik (rotasi/restore darurat) |
| **A** | ~~Q-OS1 / Q-OS3~~ terjawab 10 Okt | Terima SILO tanpa SLA; object lock `pg-backup` COMPLIANCE 15 h | ADR-030 | IT + Security | SILO + GOVERNANCE + filter | Staging publik |
| **A** | ~~Q-V2 / Q-V4~~ terjawab 10 Okt | Lisensi `efn_*` LGPL-3; anggaran ±Rp50–145 jt + mundur 2–4 mgg **atau** engineer kontrak | ADR-031 | IT + Finance + Legal | LGPL-3; jadwal berisiko mundur | Fase 1 (`efn_intercompany`) |
| **A** | ~~Gelombang 1~~ terjawab 10 Okt | ADR-001, 021, 018, 017, 004, 005, 013 | indeks | lihat indeks | rekomendasi ADR | Fase 1 |
| B | ~~Q-OS2 / Q-OS4 / Q-OS5~~ terjawab 10 Okt | PoC SeaweedFS 5 h; filestore Odoo lokal; target backup off-site | ADR-030 | IT | ya / lokal / belum ada | Go-live (off-site wajib) |
| B | ~~Q-E2 / Q-E3~~ terjawab 10 Okt | LUKS + AES-NI di pengadaan VPS; enkripsi kolom PII Odoo | ADR-022 | IT + DPO | wajib / tidak | Pengadaan server |
| B | ~~Q-V1 / Q-V3 / Q-V5 / Q-V6~~ terjawab 10 Okt | Sandbox referensi; laporan keuangan per PT (Metabase vs Odoo); cakupan "pihak ketiga"; vendor referensi lain | ADR-031 | IT + Finance | tidak / — / hanya addon / OCA+Cybrosys | Fase 1 laporan keuangan |
| B | ~~Q-W1 / Q-W2 / Q-W3 / Q-W4 / Q-55~~ terjawab 10 Okt | Jumlah perangkat per outlet, pemasang & SLA, user Odoo eksternal, model Android POS, model router | ADR-023 | IT + Ops Outlet | klien WG di perangkat | Pilot outlet |
| B | ~~Q-P2..Q-P8~~ terjawab 10 Okt | Retensi transaksi 10 th, usia, transfer, data keuangan, tombstone, CH 24 bln vs 5 th, lokasi 90 h | PDP_DATA_INVENTORY | DPO + Legal | default usulan | Go-live publik |
| B | ~~**Q-P9**~~ terjawab 10 Okt | Retensi log audit (730 h, min 365; log kontrol/purge/akses tanpa batas; salinan ClickHouse TTL 10 th — v4: nilai pribadi sudah tidak disalin) | PDP_DATA_INVENTORY | DPO + auditor | 730/365/tanpa batas/10 th | — |
| B | ~~**Q-P10** (baru)~~ terjawab 10 Okt | Anggota grup "Auditor Data Pribadi Lintas PT" | PDP_DATA_INVENTORY | DPO + Direksi | tidak ada anggota | Audit lintas PT |
| B | ~~**Q-P11** (baru, SEC-38)~~ terjawab 10 Okt | Akun tidak aktif / nomor HP daur ulang (anonimisasi otomatis N bulan) | PDP_DATA_INVENTORY | DPO + PO | tidak ada kebijakan (risiko residu) | — |
| B | ~~Q-T1..Q-T6~~ terjawab 10 Okt | Dasar transfer Meta/Cloudflare, WA Local Storage, mode Cloudflare, lokasi data payment, kunci tidak di GitHub, DPIA | PDP_TRANSFER_LINTAS_NEGARA | DPO + Legal | proxy penuh CF | Go-live publik |
| B | ~~**Q-K1** (baru)~~ terjawab 10 Okt | **Kasir boleh membuat pelanggan baru di POS?** Saat ini kasir tanpa grup Contact Creation tidak bisa onboarding pelanggan; bila boleh, HP member yang sudah ada tetap terkunci (SEC-07/44 terbukti) | QA G11, functional E-6 | PO + Ops Outlet | belum ditetapkan (konfigurasi grup) | SOP kasir pilot |
| C | ~~Q-3~~ terjawab 10 Okt | Metode transfer pricing antar-PT & siapa berwenang | docs/01 | Finance + Tax | — | `efn_intercompany` |
| C | ~~Q-4~~ terjawab 10 Okt | Periode & mekanisme settlement poin lintas-brand | docs/01 | Finance | — | ADR-020 fase 2 |
| C | ~~Q-6~~ terjawab 10 Okt | Auditor eksternal & format konsolidasi (juga menentukan minimum retensi log audit) | docs/01 | Finance | — | Q-V3, Q-P9 |
| C | ~~Q-8~~ terjawab 10 Okt | Satu katalog induk atau per PT; pemilik data produk | docs/01 | PO + Ops | — | Master data Fase 1 |
| C | ~~Q-22~~ terjawab 10 Okt | Service charge & PB1 include/exclude per outlet/daerah | docs/01, ADR-027 | Finance + Tax | configurable | Go-live F&B |
| C | ~~Q-49~~ terjawab 10 Okt | Retensi DWH & anonimisasi analitik | docs/01 | DPO + Finance | 5 th mart | (= Q-P7/Q-P9) |

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
