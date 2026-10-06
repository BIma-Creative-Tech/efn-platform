# Laporan Agent: efn-architect — Paket Kerja Fase 0 "Fondasi & Keputusan"

- **Tanggal:** 2026-10-05
- **Lingkup tulis:** hanya `docs/`. Tidak ada kode aplikasi yang diubah.

## Ringkasan (untuk manajemen)
21 ADR sekarang tersedia (semua **Proposed**), plus koreksi FSD, audit modul Odoo CE 19 yang diverifikasi langsung ke GitHub, dan template TCO 3 tahun. Tujuh keputusan perlu ditandatangani dalam 2 minggu agar Fase 1 tidak terblokir. Dua keputusan (MGM dan keranjang lintas PT) butuh konsultan pajak/legal eksternal dan harus dimulai sekarang walaupun baru dipakai di bulan ke-6 sampai ke-8.

## File yang dibuat / diubah

| File | Keterangan |
|---|---|
| `docs/decisions/ADR-002` … `ADR-016` | D-01..D-16 (D-09 → ADR-001 yang sudah ada; ADR-009 dipakai untuk D-08) |
| `docs/decisions/ADR-017-otp-saat-pos-offline.md` | §3.2, opsi (a) |
| `docs/decisions/ADR-018-kitting-manufacture-bom.md` | §3.1 |
| `docs/decisions/ADR-019-unified-cart-merchant-of-record.md` | §3.3 — holding sebagai agen penagih, penjual tetap per PT |
| `docs/decisions/ADR-020-loyalty-ledger-platform-postgresql.md` | ledger tunggal di PG platform (docs/04 §3.4) |
| `docs/decisions/ADR-021-baseline-odoo-ce19-vs-18.md` | **Tambahan** (di luar permintaan): R-T04 meminta keputusan versi di minggu 1, dan audit menemukan OCA/pos 19.0 hanya punya 4 modul |
| `docs/decisions/README.md` | Indeks, pemilik keputusan (PO/Finance/Legal/IT), urutan tanda tangan 3 gelombang |
| `docs/05_FSD_CORRECTIONS.md` | K-01..K-07 (Asli → Masalah → Mermaid → Dampak kode) |
| `docs/06_ODOO_CE19_MODULE_AUDIT.md` | 27 kebutuhan, status terverifikasi/indikasi/belum |
| `docs/07_TCO_TEMPLATE.md` | TCO 3 tahun, ±Rp6,07–12,23 M (asumsi) |
| `docs/02_TARGET_ARCHITECTURE.md` (**diedit**) | (1) referensi `ADR-004` untuk OTP offline → `ADR-017`; (2) §3.2 worker tidak lagi `POST loyalty earn` ke Odoo, melainkan `loyalty.earn.requested` → ledger BFF (konsisten dengan docs/04 & ADR-020); (3) §3.4 label "Merchant of Record" → "agen penagih, penjual tetap tiap PT" (ADR-019) |

## Keputusan yang butuh tanda tangan klien (paling mendesak)
1. **ADR-001** satu DB multi-company — PO + Finance + IT.
2. **ADR-021** baseline Odoo 19 (gate PoC 2 minggu) — IT.
3. **ADR-018** kitting Manufacture BoM (R-T01, skor 20) — PO + Ops CK + Finance.
4. **ADR-017** OTP offline opsi (a) (R-T02, skor 20) — PO + Ops Outlet + Finance.
5. **ADR-004** 4 node + lokasi data Indonesia — IT + Finance + DPO.
6. **ADR-005** re-phasing 12 bulan — Direksi + Finance.
7. **ADR-013** KDS custom, booking ditunda — PO + Ops F&B.
Mulai sekarang (lead time eksternal): **ADR-019** (opini pajak PB1/PPN & agen penagih) dan **ADR-009** (simulasi Finance + legal sign-off MGM).

## Temuan teknis penting untuk agent lain
- **`scripts/fetch-oca.sh` tidak lengkap:** `mis_builder` 19.0 bergantung pada `report_xlsx` (OCA/reporting-engine) dan `date_range` (OCA/server-ux) yang tidak diambil → instalasi `mis_builder` akan gagal. → efn-odoo-engineer / efn-devsecops-engineer.
- `services/odoo/oca/README.md` keliru: `stock_picking_batch` adalah modul **CE**, bukan OCA; OCA/l10n-indonesia branch 19.0 **kosong**.
- KDS (`pos_preparation_display`) dan booking (`pos_appointment`) **404 di CE 19.0** dan tidak ada di OCA → `efn_pos_kds` custom.
- `purchase_sale_stock_inter_company` tidak ada di OCA/multi-company 19.0 → sinkron picking inter-company harus diuji di PoC.
- `payment_xendit` ada di CE 19.0; `payment_midtrans` tidak.
- `l10n_id_efaktur_coretax` dan `l10n_id/models/qris_transaction.py` ada di CE 19.0.
- Usulan tambahan skema ledger (ADR-020): kolom `company_code` dan `expires_at` — perlu diputuskan sebelum `infra/postgres/init/02-platform-loyalty.sql` final (efn-bff-engineer).

## Yang belum terverifikasi
- Status **Enterprise** (KDS, booking, inter-company rules, konsolidasi, IoT) hanya **indikasi** — repo `odoo/enterprise` privat.
- Tidak ada instalasi nyata: POS offline 19.0, koneksi printer dapur tanpa IoT, sinkron stok inter-company OCA, template pajak `l10n_id` (PB1), alur QRIS, FEFO di POS, customer display, timbangan.
- Fitur split settlement Xendit untuk multi-PT (ADR-016/019) — klaim vendor, belum dicek kontrak.
- Tarif WhatsApp dari **sumber sekunder** (BSP: Qontak, Gupshup), bukan halaman resmi Meta.
- Semua angka biaya infra, SDM, payment fee, GPU di TCO = asumsi; validasi dengan vendor/HR.
- Sintaks Mermaid tidak dirender/diuji (tidak ada tooling di mesin ini).

---

# Gelombang 3 — Enkripsi, akses Odoo, least privilege, model tim agent, UU PDP (2026-10-05)

Bekerja di bawah `efn-lead`. Batas file dipatuhi: `docs/decisions/**`, `docs/02`, `docs/03` (§2.1 arsitektur keamanan saja), `docs/compliance/**`, `docs/09`, laporan ini. `docs/04` **tidak** diubah (usulan di bawah).

## Ringkasan (untuk manajemen)
Instruksi "enkripsi di setiap transaksi data" kini punya keputusan lengkap (ADR-022): CA internal sederhana tanpa server tambahan, TLS terverifikasi di setiap hop, enkripsi nomor HP/IP di aplikasi, disk server, foto, backup, dan gudang data. Odoo ditutup dari internet dan hanya dapat diakses lewat WireGuard dengan 2FA (ADR-023). Setiap aplikasi mendapat akun sendiri (ADR-024). Dokumen UU PDP (inventaris data, hak subjek data, consent, respons kebocoran 72 jam) siap direview DPO/Legal. Biaya lisensi Rp0; biaya utama = tenaga ±4–5 minggu lintas agent dan router WireGuard per outlet.

## File dibuat / diubah
| File | Keterangan |
|---|---|
| `docs/decisions/ADR-022-enkripsi-in-transit-at-rest.md` (baru) | Ditandai instruksi klien wajib. 15 hop + 7 penyimpanan; PKI A1 (root offline, intermediate per env dengan nameConstraints, leaf 90 hari via `deploy.sh`) vs step-ca vs Vault; ghostunnel; AES-GCM vs pgcrypto vs TDE; matriks kunci & rotasi; dampak performa/biaya; tabel **Pengecualian terdaftar** (kosong) + kriteria |
| `docs/decisions/ADR-023-akses-odoo-via-wireguard.md` (baru) | Entrypoint Traefik `wg-https`; router outlet (B1) + klien laptop (B2); ACME DNS-01 untuk host internal; subnet outlet terisolasi; 2FA `auth_totp.policy=employee_required`; perilaku offline; dampak ke ADR-017 |
| `docs/decisions/ADR-024-least-privilege-kredensial-per-service.md` (baru) | Ringkas; melengkapi user ClickHouse & user teknis Odoo + API key |
| `docs/decisions/ADR-025-model-operasi-tim-agent.md` (baru) | Lead + spesialis + functional-first + kontrak dulu |
| `docs/09_AGENT_OPERATING_MODEL.md` (baru) | Mermaid alur lead → functional → engineer → QA → security; batas file per agent; DoD per gelombang; cara interaksi user (`claude --agent efn-lead`) |
| `docs/compliance/PDP_DATA_INVENTORY.md` (baru) | Data per tabel/kolom/bucket/queue/log/pihak ketiga, dasar pemrosesan, retensi usulan, status enkripsi |
| `docs/compliance/PDP_DSAR_DESIGN.md` (baru) | Akses/koreksi/hapus akun & poin, SLA, endpoint BFF usulan, tabel `dsar_request`, alur tombstone lintas store |
| `docs/compliance/PDP_CONSENT.md` (baru) | Consent lokasi & marketing, tabel `platform.consent` append-only, endpoint, perilaku "buang koordinat tanpa consent" |
| `docs/compliance/PDP_BREACH_RESPONSE.md` (baru) | Peran (IC, DPO, Legal…), linimasa 72 jam, penilaian wajib-lapor, template subjek & lembaga; teknis ditautkan ke `docs/runbooks/` |
| `docs/02_TARGET_ARCHITECTURE.md` (diedit) | Catatan revisi v2; context diagram (WireGuard); §3.1 member dari partner (SEC-07); §3.2 event v2 tanpa HP mentah; §5 ditulis ulang: diagram zona TLS/WireGuard, tabel zona, kredensial per service, event v2 |
| `docs/03_DEVSECOPS_PIPELINE.md` (diedit) | §2.1 baru "Arsitektur keamanan runtime" + gate pipeline usulan; daftar secret tidak disentuh |
| `docs/decisions/README.md` (diedit) | Indeks 022–025, Gelombang 1-S (keamanan) dalam urutan tanda tangan |
| `docs/decisions/ADR-004-...md` (diedit, 1 kalimat) | Alasan WAL-G vs pgBackRest diperbarui (MinIO kini HTTPS; WAL-G tetap dipilih) — permintaan devsecops |

## Keputusan yang butuh tanda tangan klien
1. **ADR-022** (instruksi klien, wajib): opsi PKI A1; pemegang brankas kunci (Q-E1); syarat pengadaan VPS: AES-NI + dukungan LUKS unlock jarak jauh (Q-E2); PII Odoo tidak dienkripsi per kolom (Q-E3); cakupan mTLS (Q-E4).
2. **ADR-023**: anggaran router WireGuard per outlet (±Rp1,5–3 jt, indikatif), siapa memasang (Q-W2), jumlah outlet (Q-W1), internet/4G outlet (Q-55), user Odoo eksternal (Q-W3).
3. **ADR-024**: pemegang kredensial break-glass.
4. **ADR-025**: apakah perubahan kontrak butuh persetujuan manusia sebelum gelombang.
5. **UU PDP (DPO + Legal + Finance)**: penetapan DPO (Q-P1), retensi 10 tahun dokumen transaksi & ledger (Q-P2), batas usia (Q-P3), transfer lintas negara Meta/Cloudflare + DPIA Price Hunter (Q-P4), poin hangus saat hapus akun, masa tenggang 7 hari & SLA 14 hari.

## Usulan perubahan kontrak `docs/04` (untuk lead)
1. **§7.2 key ring PII**: ganti secret tunggal `pii_encryption_key` dengan `pii_encryption_keys` (baris `ver:base64`, versi aktif = tertinggi) atau tambah env `PII_ACTIVE_KEY_VERSION`; byte versi ciphertext = versi kunci. Tanpa ini rotasi kunci PII tidak bisa tanpa downtime.
2. **§7.3 ClickHouse**: tambah user `efn_etl` (INSERT `efn_dwh`), `efn_text2sql` (SELECT view whitelist, `readonly=2`), `efn_metabase` (SELECT view), `efn_exporter`; secret per user. Tambah secret `member_hash_pepper` (turunan, untuk ETL — SEC-16) dan `minio_kms_key`, `clickhouse_disk_key`.
3. **§7.1 PKI**: jelaskan rantai root offline → intermediate per env → leaf 90 hari; `efn_ca_crt` = bundle root+intermediate; entrypoint Traefik `wg-https` + sertifikat ACME DNS-01 untuk host yang diakses manusia via WireGuard (`erp.`, `ops.`, `bi.`); app di balik ghostunnel wajib bind `127.0.0.1`; exporter metrik via `web.config.file` (mTLS).
4. **§4 event baru** `privacy.erasure.requested` / `privacy.erasure.completed` (payload di `PDP_DSAR_DESIGN.md` §6) — perlu keputusan bagaimana Odoo menemukan partner (hitung `member_hash` di Odoo vs BFF mengirim `partner_id`).
5. **§3 endpoint baru**: `/v1/me/data`, `/v1/me/reauth`, `PATCH /v1/me`, `/v1/me/phone-change`, `DELETE /v1/me`, `/v1/me/delete/cancel`, `/v1/me/requests`, `/v1/me/consents[/:purpose]`, `/internal/v1/dsar/*`; §3.3 Market Scout: koordinat dibuang tanpa consent aktif + dibulatkan 4 desimal di server.
6. **§5 tabel baru**: `platform.consent` (append-only, grant INSERT/SELECT saja) dan `platform.dsar_request`.
7. **§1 (pertimbangan)**: JWT `sub` = nomor HP berarti cookie & token membawa PII; usulkan `sub` = id member opak (UUID) dengan pemetaan di tabel member terenkripsi. Bukan blocker, tapi lebih murah diubah sekarang.
8. Kecil: kunci rate-limit Redis `otp:login:ip:<ip>` membawa IP mentah — pertimbangkan hash IP (pseudonim, TTL pendek sudah membatasi).
9. Header `docs/04` masih bertuliskan "(v1)" padahal berisi bagian v2.

## Temuan untuk agent lain
- **devsecops**: isi tabel "Pengecualian terdaftar" ADR-022 bila ada hop tanpa TLS (kandidat yang perlu dicek: cAdvisor → ghostunnel; exporter tanpa exporter-toolkit; healthcheck internal); runbook usulan `pki-issue-rotate.md`, `luks-unlock.md`, `pii-key-rotation.md`, `wireguard-peer-onboarding.md`, `outlet-tunnel-down.md`, `security-incident.md`; lifecycle MinIO 365 hari `market-scout`; alert `EfnOutletTunnelDown`; middleware `internal-only` tanpa `ipStrategy.depth` untuk entrypoint WireGuard.
- **efn-odoo-functional**: `auth_totp` + `auth_totp_mail` dengan `auth_totp.policy = employee_required` di `efn_setup_*`; konfirmasi alur buka sesi POS oleh user ber-2FA + pergantian kasir via PIN `pos_hr`; nilai field standar untuk opt-out marketing (mailing blacklist) sebelum ada kode consent di POS.
- **efn-bff-engineer**: key ring PII, job purge retensi (`otp_audit` 90 h, `outbox` 7 h, `processed_message` 30 h, anonimisasi `price_hunter_report` 12 bln), endpoint DSAR/consent setelah kontrak diperbarui.
- **efn-data-ai-engineer**: kolom `TTL` 24 bulan untuk pseudonim di ClickHouse; encrypted disk untuk `competitor_prices`/`loyalty_events`; consumer `privacy.erasure.requested`.

## Terverifikasi / belum terverifikasi
- **Terverifikasi (sumber primer, branch 19.0):** `addons/auth_totp` (LGPL-3, auto_install, 2FA memblokir RPC password → perlu API key) dan `addons/auth_totp_mail/models/res_config_settings.py` (`auth_totp_policy` → `ir.config_parameter` `auth_totp.policy`, nilai `employee_required`/`all_required`). Modul `auth_totp_mail_enforce` **tidak ada** di 19.0 (sudah digabung).
- **Belum terverifikasi:** nomor pasal UU PDP (5–13, 20, 46, 53, 56) dan tenggat 3×24 jam untuk hak akses — wajib dicek Legal; harga router & layanan zero-trust (indikatif); overhead performa (perkiraan, belum diukur); dukungan LUKS/AES-NI vendor VPS; perilaku POS 19 saat tunnel putus di tengah sesi (asumsi sama dengan offline biasa — uji di PoC); nama/struktur field Odoo di inventaris PDP (menunggu fit-gap); Mermaid tidak dirender.

---

# Gelombang 5 — Keputusan klien 6 Okt 2026: kustodi, WireGuard open source, payment, pajak, PDP (2026-10-06)

Bekerja di bawah `efn-lead`. Batas file dipatuhi: `docs/decisions/**`, `docs/compliance/**`, `docs/02`, `docs/07`, laporan ini. `docs/04` **tidak** diubah (usulan di bawah).

## Ringkasan (untuk manajemen)
Keputusan klien dicatat apa adanya: kunci hanya dipegang Superadmin dan mTLS sesuai rekomendasi (ADR-022, ditandai "Diputuskan klien 6 Okt 2026"; sisanya tetap Proposed). WireGuard sudah open source — biaya Rp1,5–3 jt sebelumnya adalah router; kini default **Rp0** (aplikasi WireGuard resmi di perangkat kasir) untuk outlet ≤ 3 perangkat dan **±Rp0,4–0,9 jt** (router OpenWrt) untuk outlet besar. Payment gateway dibuat netral vendor (ADR-026); pajak PB1 per kota & harga include/exclude diatur di Odoo, bukan di kode (ADR-027). Kebijakan UU PDP belum diputuskan klien; dokumen kepatuhan memakai default usulan dan kini selaras dengan kontrak.

## File dibuat / diubah
| File | Keterangan |
|---|---|
| `docs/decisions/ADR-022-enkripsi-in-transit-at-rest.md` (diedit) | Status: kustodi = Superadmin & mTLS = rekomendasi "Diputuskan klien 6 Okt 2026"; root CA dipegang peran Superadmin; bagian baru **Kustodi kunci**: risiko bus factor (R-K1..K3) + mitigasi **rekomendasi** (≥ 2 orang Superadmin, salinan offline tersegel di 2 lokasi, Shamir 2-dari-3 untuk root CA & kunci PII, prosedur pewarisan, uji pemulihan 2×/tahun, break-glass); Q-E1/Q-E4 ditandai terjawab, turunan Q-E1a/b/c |
| `docs/decisions/ADR-023-akses-odoo-via-wireguard.md` (ditulis ulang) | Opsi B-a klien resmi di perangkat (Rp0), B-b OpenWrt (Rp0 router lama / ±Rp0,4–0,9 jt baru), B-c Raspberry Pi/mini-PC (±Rp1,2–3,5 jt), pembanding MikroTik; manajemen peer D1 `wg`+`gen-peer.sh` (default) vs Headscale (BSD-3) vs NetBird (BSD-3 + AGPLv3 server); trade-off per perangkat (Android POS, KDS, kiosk, printer ePOS, EDC); tabel biaya per skenario outlet; kontrol Always-on VPN; split DNS; Q-W4/Q-W5 baru |
| `docs/decisions/ADR-026-payment-gateway-agnostic.md` (baru) | Abstraksi `PaymentGateway`, adapter mock/xendit/midtrans (nonaktif), POS QRIS via BFF + `efn_gateway`, tanpa data kartu, kriteria vendor berbobot (fee, split multi-PT, QRIS dinamis, sandbox, SLA, lokasi data, rekonsiliasi), dampak ADR-016/019 |
| `docs/decisions/ADR-027-pajak-configurable-pb1-per-kota.md` (baru) | Pajak per kota + fiscal position per outlet (`pos.config.default_fiscal_position_id`), `price_include_override`, `iface_tax_included`; tanpa tarif di kode; peran konsultan pajak; Super-App menampilkan pajak hasil Odoo via BFF |
| `docs/decisions/ADR-028-captcha-altcha-self-hosted.md` (baru) | ALTCHA (MIT, terverifikasi) vs Turnstile/reCAPTCHA/hCaptcha — alasan PDP |
| `docs/decisions/ADR-029-identitas-member-opak.md` (baru) | JWT `sub` = UUID `platform.member.id` |
| `docs/decisions/ADR-016-...md`, `ADR-019-...md` (diedit, header) | ADR-016: vendor ditunda, Xendit = kandidat; ADR-019: tautan ADR-026/027 |
| `docs/decisions/README.md` (diedit) | Pembaruan 6 Okt, indeks 026–029, **urutan tanda tangan yang tersisa** |
| `docs/compliance/PDP_DATA_INVENTORY.md` (ditulis ulang v2) | Tabel `member`, `consent` (member_id), `dsar_request`, `payment_intent`, `res.partner.efn_member_hash`, Redis (SEC-24, ALTCHA), retensi default §8.4 bertanda "menunggu DPO", GitHub = bukan data pelanggan; Q-P6..P8 |
| `docs/compliance/PDP_DSAR_DESIGN.md` (ditulis ulang v2) | Endpoint kontrak vs usulan, tabel kontrak, alur: platform → Odoo pull (`/v1/pos/privacy/erasures`) → ClickHouse via ETL + `privacy.erasure.completed`; retensi default |
| `docs/compliance/PDP_CONSENT.md` (diedit) | Catatan penyelarasan: kontrak §8.4 menang (purpose `location`/`marketing`, `member_id`, `version`) |
| `docs/compliance/PDP_TRANSFER_LINTAS_NEGARA.md` (baru) | Pemroses Meta, Cloudflare (dekripsi TLS di edge), payment (kelak), GitHub (kode saja), data mengalir, opsi mitigasi (Local Storage WA, DNS-only CF, DPA), default sementara, Q-T1..T6 |
| `docs/02_TARGET_ARCHITECTURE.md` (diedit) | Catatan revisi v3; label gateway "vendor belum ditentukan"; zona outlet: klien WG / OpenWrt |
| `docs/07_TCO_TEMPLATE.md` (diedit) | §3 J baru: biaya konektivitas WireGuard per opsi + contoh 20 outlet (Rp3,2–7,2 jt vs Rp30–60 jt asumsi lama); §E vendor belum ditentukan |

## Keputusan yang masih terbuka untuk klien
1. **ADR-022**: jumlah orang Superadmin (rekomendasi ≥ 2), Shamir 2-dari-3 & pemegang share ketiga, lokasi salinan offline kedua (Q-E1a/b/c); PKI A1; Q-E2; Q-E3.
2. **ADR-023**: pola per outlet (B-a/B-b) — butuh jumlah perangkat yang membuka Odoo per outlet (Q-W1), model Android POS (Q-W4), model router yang ada (Q-55).
3. **ADR-026/016**: siapa & kapan penawaran vendor; QRIS POS lewat gateway atau QRIS statis/EDC bank (Q-PG2).
4. **ADR-027**: daftar kota + tarif (konsultan pajak), include/exclude per brand (Q-22), take-away & service charge.
5. **UU PDP (belum diputuskan)**: DPO, retensi (Q-P1..P8), transfer lintas negara (Q-T1..T6).

## Usulan perubahan kontrak `docs/04` (untuk lead)
1. §8.2: `member.member_ref_enc` NOT NULL bertentangan dengan anonimisasi §8.4 → NULLABLE bila `deleted_at IS NOT NULL`.
2. §8.4: definisikan nilai `dsar_request.status` & `type`; tambah kolom `reason`; retensi `dsar_request` (usulan 5 tahun).
3. §8.4: tombstone `member_hash` di ledger/report setelah hapus akun — keputusan DPO (Q-P6).
4. §8.4 retensi foto 90 hari ≠ implementasi `minio-init.sh` 365 hari → tugas devsecops.
5. Endpoint usulan belum di kontrak: `PATCH /v1/me`, `POST /v1/me/phone-change`, `/internal/v1/dsar/*`.
6. ADR-027: endpoint BFF katalog/quote yang mengembalikan harga, label pajak, dan pajak hasil hitung Odoo (Super-App tidak menghitung pajak).
7. §8.4 consent: pertimbangkan `text_sha256`.
8. §7.4: tegaskan dua pola outlet (klien di perangkat `/32` vs router `/29`) dan blok `/json/2` (SEC-22).

## Temuan untuk agent lain
- **devsecops**: runbook `key-custody.md` memuat rekomendasi Shamir/salinan tersegel/pewarisan (bila disetujui); `wireguard-peer-onboarding.md` untuk B-a (Android Always-on VPN, Windows service) & B-b (OpenWrt); `gen-peer.sh --pubkey`; DNS klien `10.88.0.1` vs `hosts`; lifecycle foto 90 hari.
- **efn-odoo-functional**: `docs/08` F-02 tambah pajak per kota + fiscal position per outlet (+ varian take-away); F-21 menjadi Custom (`efn_gateway`) karena qris.online tidak dipakai default; PoC penyesuaian harga include→exclude di POS 19.
- **efn-bff-engineer**: adapter payment, ALTCHA, member UUID, DSAR (lihat kontrak §8).

## Terverifikasi / belum terverifikasi
- **Terverifikasi (sumber primer, 6 Okt 2026):** Headscale = BSD-3-Clause (LICENSE) dan ruang lingkup "single tailnet … personal use, or a small open-source organisation" (README); NetBird = BSD-3-Clause kecuali `management/`, `signal/`, `relay/` = AGPLv3 (README); wireguard-android = Apache-2.0 (COPYING); ALTCHA = MIT (repo); Odoo 19.0: `account.tax.price_include_override`, `original_tax_ids`, `fiscal_position_ids`; `account.fiscal.position` (`auto_apply`, `country_id`, `state_ids`, `zip_from/to`, `tax_ids`, tidak ada model `account.fiscal.position.tax`); `pos.config.default_fiscal_position_id`, `fiscal_position_ids`, `iface_tax_included`.
- **Belum terverifikasi:** semua harga perangkat (indikatif — validasi vendor); `wireguard.com` tidak dapat diakses dari lingkungan ini (daftar platform klien dari pengetahuan umum); dukungan OpenWrt untuk router outlet; Always-on VPN di Android POS bermerek; WA Cloud API Local Storage Indonesia (hanya sumber sekunder BSP); Cloudflare Data Localization Suite untuk Indonesia; tarif PBJT/PB1 per Perda; penyesuaian harga include→exclude di POS 19; nomor pasal UU PDP; Mermaid tidak dirender.

# Gelombang 8 — Sumber image & masa depan object storage (2026-10-06)

Pemicu: devsecops G7.3 no. 1 (image `minio/minio` & `minio/mc` hilang dari Docker Hub, Quay wajib login; build sementara dari tag source `RELEASE.2025-10-15T17-29-55Z`).

## Ringkasan (untuk manajemen)
MinIO community resmi ditinggalkan vendornya (repo diarsipkan April 2026). Build kami saat ini masih mengandung beberapa celah keamanan **High** yang hanya ditambal di produk berbayar MinIO. Rekomendasi: pakai **SILO**, fork komunitas yang aktif dan sudah menambal celah tersebut, tetap kami bangun dan tandatangani sendiri (±2–3 hari, Rp0). Tambahkan filter pengaman di depan storage (±2–3 hari) dan uji coba **SeaweedFS** 5 hari sebagai jalur keluar. Butuh tanda tangan IT + Security sebelum staging publik.

## File dibuat / diubah
| File | Perubahan |
|---|---|
| `docs/decisions/ADR-030-sumber-image-object-storage.md` | **Baru** (Proposed): kronologi status MinIO, tabel CVE yang tidak ditambal, 8 kandidat (status quo, SILO, SeaweedFS, Garage, Ceph RGW, Versity, AIStor, RustFS) × 10 kebutuhan, biaya/waktu, rekomendasi jangka pendek & menengah, pemicu tinjauan, daftar sumber & klaim belum terverifikasi |
| `docs/decisions/README.md` | Indeks ADR-030, catatan gelombang 8, urutan tanda tangan 4a |

## Keputusan yang butuh tanda tangan klien
- **ADR-030 Q-OS1** terima fork komunitas tanpa SLA (SILO) untuk Fase 0–1; **Q-OS2** anggaran PoC SeaweedFS 5 hari; **Q-OS3** object lock `pg-backup` → COMPLIANCE 15 hari selama SN-2026-015 belum difix (tidak bisa dibatalkan siapa pun); **Q-OS4** filestore Odoo tetap di disk lokal Fase 0–1; **Q-OS5** target backup off-site yang mendukung object lock.

## Temuan teknis penting untuk agent lain
- **devsecops (mendesak):** build `RELEASE.2025-10-15` rentan CVE-2026-41145 & -40344 (tulis objek tanpa secret, cukup access key ID), CVE-2026-34204 (rusak metadata SSE → objek backup tak terbaca), CVE-2026-39414 (DoS S3 Select), dan SN-2026-015 (principal mana pun bisa menghapus retensi GOVERNANCE `pg-backup`; belum difix di mana pun). Tindakan: (1) ganti sumber build ke `github.com/pgsty/silo` tag `RELEASE.2026-09-16T00-00-00Z` + `pgsty/mc` (cek versi Go di `go.mod`, kemungkinan > 1.24); (2) filter L7 di depan storage (daftar header/aksi di ADR-030 Keputusan 2); (3) access key acak sebagai secret; (4) uji upgrade in-place volume dev lama.
- **QA:** checklist kesesuaian di ADR-030 Keputusan 1 dipakai untuk SILO dan PoC SeaweedFS; tambah uji negatif: `PUT ?retention` + `x-amz-bypass-governance-retention` oleh efn-bff harus ditolak.
- **bff / data-ai:** jangan memakai admin API MinIO dari aplikasi; tetap S3 API standar. Perlu dicek apakah minio-js 8.x / minio-py 7.2.x mengirim `STREAMING-UNSIGNED-PAYLOAD-TRAILER` (menentukan filter L7).
- **lead:** usulan risiko baru **R-S13** (komponen inti ditinggalkan/berganti lisensi upstream) untuk `docs/01`; `docs/04 §7.3` perlu catatan access key = secret.
- **efn-odoo-functional:** bila filestore kelak ke object storage, fit-gap OCA `fs_attachment_s3`/`fs_storage` (ada di OCA/storage 19.0).

## Terverifikasi / belum terverifikasi
- **Terverifikasi (sumber primer, 6 Okt 2026):** `minio/minio` archived (`pushed_at 2026-04-24`), README "no longer maintained / source code only", tag terakhir `RELEASE.2025-10-15T17-29-55Z`; advisory GHSA untuk CVE-2026-41145/40344/34204/39414/42600/33322/33419 dengan fix hanya di AIStor; `minio/mc` archived 14 Jul 2026; `minio-js` aktif (Apache-2.0); `pgsty/silo` aktif, AGPL-3.0, rilis 2026-09-16, rename 2026-08-06; SILO Security Chronicle mencatat fix CVE di atas (2026-04-17 / 2026-06-18) dan SN-2026-015 belum difix; Garage: tanpa versioning/object lock/SSE/bucket policy, TLS S3 via reverse proxy; SeaweedFS (Apache-2.0, 4.48) mendokumentasikan SSE-S3/KMS/C, object lock, lifecycle tanpa transition, bucket policy; Ceph RGW SSE-S3 hanya via Vault; Versity v1.8.0 Apache-2.0; RustFS 1.0.0 (16 Sep 2026); AIStor Free = proprietary, standalone, tanpa hak modifikasi/redistribusi; OCA/storage 19.0 memuat `fs_attachment_s3`, `fs_storage`.
- **Belum terverifikasi:** SN-2026-015 berlaku di upstream (klaim SILO); kompatibilitas drop-in & upgrade in-place SILO; versi Go build SILO; perilaku header trailer di SDK/WAL-G; fitur SeaweedFS/Versity di stack EFN; tanggal pasti hilangnya Docker Hub/Quay anonim (sekunder + runtime); harga AIStor Enterprise; isi penuh lisensi AIStor Free; jumlah maintainer aktif SILO (disimpulkan dari daftar kontributor).

# Gelombang 10 — Tanpa dependency modul Odoo pihak ketiga (2026-10-06)

Pemicu: instruksi klien 6 Okt 2026 — *"pengembangan ini tidak ingin memiliki ketergantungan dengan OCA, tambahkan satu reference lagi dari Cybrosys, modul gap akan dikembangkan sendiri menyesuaikan kebutuhan, perlakukan vendor sebagai reference."*

## Ringkasan (untuk manajemen)
Odoo kini hanya berisi modul standar Community 19 dan modul buatan EFN (`efn_*`). OCA dan Cybrosys menjadi contoh desain, tidak dipasang dan kodenya tidak disalin. Akibatnya audit log, inter-company, dan laporan keuangan/konsolidasi harus dibangun sendiri atau dipindah ke Metabase: **±10–16 minggu-engineer (±Rp50–145 jt)**, Fase 1 berisiko mundur 2–4 minggu tanpa engineer tambahan. Keuntungannya: tidak ada lagi patch darurat atas kode pihak lain (seperti bug audit log OCA di Odoo 19), build tidak mengambil kode dari internet, dan lisensi seragam LGPL-3.

## File dibuat / diubah
| File | Perubahan |
|---|---|
| `docs/decisions/ADR-031-tanpa-dependency-modul-odoo-pihak-ketiga.md` | **Baru** — status "Diputuskan klien 6 Okt 2026" (prinsip), rincian Proposed. Konteks + 6 bukti risiko, 4 opsi, alur fit-gap baru (Mermaid), panduan lisensi & clean implementation (boleh/dilarang, proses dua peran, format bagian "Referensi" README, konsekuensi bila kode AGPL/LGPL/OPL-1 tersalin), 11 kebutuhan eks-OCA dengan estimasi, biaya/jadwal, dampak ke kode per agent, Q-V1..Q-V6, usulan R-T13 |
| `docs/decisions/ADR-015-penamaan-modul-efn-oca-first.md` | Header: *superseded-in-part* oleh ADR-031; penamaan `efn_*` tetap |
| `docs/decisions/ADR-021-baseline-odoo-ce19-vs-18.md` | Catatan: alasan "modul OCA kunci 19.0" gugur, keputusan CE 19 tetap; gate PoC tanpa OCA |
| `docs/06_ODOO_CE19_MODULE_AUDIT.md` | Ditulis ulang: kolom "Referensi (OCA / Cybrosys) — tidak dipasang", +3 baris (ekspor XLSX, laporan POS, filestore), §2 katalog referensi terverifikasi (pengganti daftar `fetch-oca.sh`), §3 backlog direvisi |
| `docs/01_REVIEW_RISK_GAP_ANALYSIS.md` | Catatan "Diperbarui ADR-031" di D-10, D-15, §3.4, §3.6, R-T04, R-L08, R-S10, tabel stack (2 baris) |
| `LICENSES.md` | OCA dihapus dari komponen yang di-deploy; `efn_*` = LGPL-3; bagian kebijakan referensi (OCA/Cybrosys/OPL-1); MinIO → SILO |
| `docs/decisions/README.md` | Catatan gelombang 10, indeks ADR-031, status ADR-015, urutan tanda tangan 4b |

## Keputusan yang butuh tanda tangan klien
- **Q-V2** lisensi semua `efn_*` = LGPL-3. **Q-V4** anggaran ±Rp50–145 jt + pilihan: Fase 1 mundur 2–4 minggu **atau** 1 Odoo engineer kontrak 2 bulan. **Q-V3 (Finance)** laporan keuangan per PT: (a) pivot + ekspor XLSX + Metabase (1 minggu) atau (b) `efn_account_reports` di Odoo (4–6 minggu). **Q-V1** boleh memasang modul referensi di sandbox studi terisolasi (default: tidak). **Q-V5** larangan hanya untuk addon Odoo, bukan pustaka Python/JS (rekomendasi). **Q-V6** vendor referensi selain OCA/Cybrosys.

## Temuan untuk agent lain (lewat efn-lead)
- **efn-odoo-functional:** revisi `docs/08` (kategori O dihapus → S/E/C; F-03, F-16, F-17, F-19, F-01, F-04, F-07, F-10) dan spesifikasi `efn_audit` + `efn_intercompany` di §4 dengan bagian Referensi; prinsip di header `docs/08` masih "CE → OCA → custom".
- **efn-odoo-engineer:** `efn_audit` menggantikan `efn_setup_audit` + `patches.py`; ganti rujukan `auditlog.log` di `efn_privacy` (model + test) dan `efn_pos_otp_gate/tests/test_partner_phone_lock.py`; README `efn_kitting`, `efn_setup_mrp`, `efn_setup_security` masih merujuk OCA.
- **efn-devsecops-engineer:** hapus `scripts/fetch-oca.sh`, `services/odoo/oca/`, langkah fetch + pengecualian `efn_setup_audit` di `.github/workflows/ci.yml` (baris ±209–240), cek `addons_path` di Dockerfile/`odoo.conf.tpl`; tambah gerbang CI daftar-putih `depends` (CE 19 + `efn_*`); evaluasi alat cek kemiripan kode lintas-repo.
- **efn-data-ai-engineer:** mart konsolidasi GL per company + eliminasi inter-company (menggantikan `mis_builder`).
- **lead:** dokumen yang masih menyebut OCA sebagai komponen dan belum diedit gelombang ini: `docs/02` (diagram "Inter-company OCA", alur CK), `docs/07` baris "Rp0 CE/OCA" (+ tambahan SDM), ADR-001/010/013/018, `docs/05`, `docs/09`, `docs/testing/*`. Usulan risiko **R-T13** (kapasitas & retensi Odoo engineer) untuk register.

## Terverifikasi / belum terverifikasi
- **Terverifikasi (sumber primer, 6 Okt 2026):** CybroAddons: branch 8.0–20.0 + `saas-19.4`, default 19.0, push 2026-10-05, lisensi repo `null`; manifest 19.0: `pos_kitchen_screen_odoo` 19.0.1.0.0 LGPL-3, `user_audit` 19.0.1.0.0 LGPL-3, `inter_company_synchronization` 19.0.1.0.0 AGPL-3 (README: SO/PO saja), `advanced_loyalty_management` 19.0.1.0.0 LGPL-3, `base_accounting_kit` 19.0.3.3.1 LGPL-3, `dynamic_accounts_report` 19.0.1.1.2 LGPL-3, `base_account_budget` 19.0.1.0.2 LGPL-3, `cybrosys_support_client` 19.0.1.0.1 LGPL-3, `pos_book_order` 19.0.1.0.0 AGPL-3, `pos_auto_lot_selection` 19.0.1.0.1 AGPL-3, `pos_automated_manufacturing` 19.0.1.0.0 LGPL-3, `advanced_pos_reports` 19.0.1.0.1 LGPL-3, `pos_analysis_report` 19.0.1.0.0 LGPL-3, `advanced_excel_reports` 19.0.1.0.0 LGPL-3, `excel_report_designer` 19.0.1.0.0 AGPL-3, `odoo_queue_manager` 19.0.1.0.0 AGPL-3 (antrean pelanggan); 404 di 19.0: `kitchen_screen`, `pos_restaurant_table_booking`, `account_reports_xlsx`. apps.odoo.com: `account_reports_xlsx` 19.0 OPL-1 USD 22,40; `pos_kitchen_screen_odoo` LGPL-3 versi 15–19. OCA 19.0: `auditlog` 19.0.2.0.0 AGPL-3 (belum naik versi), `account_financial_report` 19.0.0.0.26 AGPL-3, `queue_job` 19.0.2.1.5 LGPL-3. CE 19.0: `web/controllers/export.py` `ExcelExport` + `/web/export/xlsx`.
- **Belum terverifikasi:** daftar lengkap modul CybroAddons 19.0 (tree API terpotong); padanan Cybrosys untuk QC intake, multi-company share, inter-company invoice, booking meja; cakupan old/new value `user_audit`; panggilan jaringan `cybrosys_support_client`; lisensi OCA/pos 19.0, `base_multi_company`, `product_multi_company`, OCA/storage; apakah bug ORM auditlog sudah diperbaiki di commit 19.0 tanpa menaikkan versi; semua estimasi minggu-engineer (asumsi arsitek, perlu validasi Odoo engineer senior, Q-53); alat cek kemiripan kode; Mermaid tidak dirender.
