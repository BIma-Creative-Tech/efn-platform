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
- **Belum terverifikasi (G10):** daftar lengkap modul CybroAddons 19.0 (tree API terpotong); padanan Cybrosys untuk QC intake, multi-company share, inter-company invoice, booking meja; cakupan old/new value `user_audit`; panggilan jaringan `cybrosys_support_client`; lisensi OCA/pos 19.0, `base_multi_company`, `product_multi_company`, OCA/storage; apakah bug ORM auditlog sudah diperbaiki di commit 19.0 tanpa menaikkan versi; semua estimasi minggu-engineer (asumsi arsitek, perlu validasi Odoo engineer senior, Q-53); alat cek kemiripan kode; Mermaid tidak dirender.

# Gelombang 12 — Dokumentasi kepatuhan (sisa SEC-11/SEC-40), runbook insiden, status ADR (2026-10-06)

Basis: `d61faf0`, review r4 (SEC-11 partial, SEC-38 open, SEC-40 partial), `docs/04` v3.8, laporan Gelombang 8–11.

## Ringkasan (untuk manajemen)
Dokumen kepatuhan kini mengikuti kondisi sistem yang sebenarnya: daftar data pribadi (RoPA) mencatat log audit Odoo, cache tanpa nomor HP, dan penyimpanan baru. Ada runbook insiden keamanan yang menghubungkan alarm otomatis dengan langkah penanganan dan kewajiban lapor 3×24 jam UU PDP. Ringkasan satu halaman untuk manajemen tersedia di `docs/STATUS_MANAJEMEN.md`. Keputusan terpenting yang masih terbuka: penunjukan DPO, jumlah Superadmin, persetujuan SILO, dan anggaran modul buatan sendiri.

## File dibuat / diubah
| File | Perubahan |
|---|---|
| `docs/compliance/PDP_DATA_INVENTORY.md` | **v3**: kolom dasar pemrosesan + akses per item; `member` (baris dihapus saat erasure), `consent` (ip_enc NULL, baris yatim), `dsar_request` (CHECK hash aktif), tombstone acak, `payment_intent` (data pribadi tidak langsung), retensi aktual dari `app-config.ts` (outbox/processed **14/14 h**, v2 salah 7/30), Redis key HMAC + challenge login terenkripsi (SEC-24 ditutup), `efn.audit.log` (masking, 730/min 365, log kontrol tanpa batas, grup auditor lintas PT), sink Loki 400 h, salinan audit ClickHouse (sedang dibangun), bukti smoke log bebas HP, SILO/s3-gateway, WAL-G 14 full. Q-P9..Q-P11 baru |
| `docs/runbooks/security-incident.md` | **Baru**: peran, alur Mermaid, sumber deteksi (alert nyata dari `alert-rules.yml` + `loki-rules`), severity S1–S3, containment per vektor (tautan runbook yang ada), forensik (Loki, sink, `efn.audit.log`, ClickHouse, PITR), notifikasi PDP, celah jujur |
| `docs/compliance/PDP_BREACH_RESPONSE.md` | Tautan ke runbook baru |
| `docs/decisions/ADR-022-…md` | Status implementasi; **E12 dipersempit** (bukan ditutup penuh, lihat bawah); tabel hop baru T9a/T9b s3-gateway, T12a Loki→Alertmanager mTLS, T12b sink audit, S3a root acak, S8 salinan audit |
| `docs/decisions/ADR-030-…md` | Bagian "Status implementasi" (SILO, s3-gateway, SEC-41 terkonfirmasi lalu fixed, access key/root acak, sisa) |
| `docs/decisions/README.md` | Catatan G12; tabel gabungan **keputusan klien terbuka** (Q-P*, Q-E*, Q-OS*, Q-V*, Q-W*, Q-T*, Q-3/4/6/8/22/49, Q-K1 kasir buat pelanggan) dengan default & yang diblokir |
| `docs/02_TARGET_ARCHITECTURE.md` | v4: diagram konteks/layer/zona dengan s3-gateway + SILO (network `objstore`), `efn_intercompany`/`efn_audit` menggantikan OCA, §5.5 baru (alur audit + sink + ClickHouse), catatan kredensial |
| `docs/STATUS_MANAJEMEN.md` | **Baru**: ringkasan satu halaman non-teknis |

## Temuan saat menulis (koreksi atas brief)
- **E12 tidak tertutup penuh**: ocr-worker `/metrics` masih mTLS tanpa allowlist CN (`services/ai-ocr-worker/efn_ocr/metrics.py:7-9`). E12 dipersempit ke satu endpoint, penutupan ±0,5 hari devsecops (sidecar ghostunnel `--allow-cn=prometheus`).
- **Alert burst 403 s3-gateway belum ada**: runbook memakai query LogQL manual. Usulan alert `EfnS3GatewayDeniedBurst` untuk devsecops (label service Promtail perlu dicek).
- Konflik desain salinan audit ClickHouse append-only vs erasure PII → Q-P9 (usulan: hanya baris non-pribadi, atau DELETE terkontrol yang dicatat).
- `consent`/`dsar_request` belum punya job retensi (tidak mendesak; retensi 5 th).

## Usulan perubahan `docs/04` (milik lead)
1. §8.4 retensi: tambah `outbox`/`processed_message` 14 h (kode), log audit Odoo 730 h (min 365, hard-coded), stream Loki `efn_audit_sink` 400 h, Loki umum 30 h, WAL-G 14 full.
2. §8.5 matriks mTLS: baris loki → alertmanager (sudah di §8.15, pindahkan ke matriks) dan catatan ocr-worker `/metrics` tanpa allowlist CN (E12 sisa).
3. §8.11 baris tombstone `sha256("erased:"||request_id)` sudah usang → beri coret/rujuk §8.13 agar tidak dibaca sebagai kontrak.
4. §8.8 "auditlog milik partner" → `efn.audit.log` (`_efn_audit_purge_records`).
5. Kontrak salinan audit ClickHouse (nama tabel, user tanpa ALTER/DELETE, `row_hash`, rekonsiliasi) setelah data-AI selesai.
6. Prefix Redis lengkap (`otp:stepup-used:*`, `dsar:receipt-ip:*`, `altcha:ip:*`) + aturan "IP di key wajib HMAC".
7. Usulan runbook index: tambah `security-incident.md` di `docs/runbooks/README.md` (di luar batas file saya).

## Keputusan yang butuh tanda tangan klien
Lihat `docs/decisions/README.md` bagian baru. Baru di gelombang ini: **Q-P9** retensi log audit, **Q-P10** anggota Auditor Data Pribadi Lintas PT, **Q-P11** akun dorman / HP daur ulang (SEC-38), **Q-K1** kasir boleh membuat pelanggan.

## Terverifikasi / belum terverifikasi
- **Terverifikasi (membaca kode/konfigurasi repo):** prefix Redis & `redisKeyHash` (`auth.service.ts`, `dsar.service.ts`, `dsar-receipt.service.ts`), field `m` terenkripsi (`otp.service.ts:64-66`), retensi BFF (`app-config.ts:671-691`, `retention.service.ts`), erasure menghapus `member` + `consent.ip_enc` NULL (`erasure.service.ts:127,180,188`), `RETAIN_FULL=14`, ILM 90 h (`minio-init.sh`), Loki 720h/9600h, daftar alert (`alert-rules.yml`, `loki-rules/efn-audit-sink.yml`), ocr-worker metrics tanpa CN allowlist.
- **Dari laporan agent (tidak saya jalankan):** smoke 108/108, log bebas HP, SEC-41 runtime, sink audit nyata, upgrade in-place SILO.
- **Belum terverifikasi:** kode salinan audit ClickHouse (data-AI); label Promtail untuk s3-gateway di query LogQL; nomor pasal UU PDP; semua hal staging/produksi (LUKS, `log_parameter_max_length`, efn-deploy di host); Mermaid tidak dirender.

# Gelombang 14 — Inventaris PDP v4 (data G13/G13c), keputusan pilot, status manajemen (2026-10-07)

Basis: `0e8a1ff`, review r7 (syarat GO no. 5, SEC-65 & SEC-11 Partial), `docs/04` §9 v4.0–4.3, laporan agent G13/13b/13c. Hanya dokumen; tidak ada kode diubah, tidak ada commit.

## Ringkasan (untuk manajemen)
Daftar data pribadi kini mencakup semua data fitur poin di kasir, sehingga syarat GO pilot no. 5 terpenuhi. Indeks keputusan memuat 7 syarat GO beserta pemiliknya dan 10 pertanyaan pilot. Ringkasan manajemen diperbarui ke status Fase 1. Penghambat utama pilot adalah server staging dan batas poin dari Finance. Ada satu temuan baru yang harus diputuskan **sebelum impor saldo poin lama** (Q-P13).

## File diubah
| File | Perubahan |
|---|---|
| `docs/compliance/PDP_DATA_INVENTORY.md` | **v4**: §2.1a (`loyalty_reservation` 30 h, `loyalty_pos_earn` 400 h, `erasure_suppression` tag HMAC 90 h + risiko uji keanggotaan, entri ledger `pos_*`/`migration_opening`, endpoint migrasi), §2.2a (`efn.pos.otp.challenge.purpose`, `efn.pos.loyalty.reservation` 400 h, `efn.pos.loyalty.outbox` tanpa payload/tanpa GC, `efn.audit.log` kontrol akses SEC-49), §2.4 (`fact_pos_payment`, stok, price index, `fact_gl_line` tanpa partner/tanpa TTL, salinan audit ter-redaksi + `row_hash`, TTL 10 th), §2.6 (stream Loki `efn_loyalty`, `efn_audit_anchor`, `host-journal` 400 h; Metabase), kolom Erasure, matriks retensi, Q-P12..Q-P14 |
| `docs/decisions/README.md` | Catatan G14; tabel **Syarat GO pilot loyalty** (G-1..G-7, pemilik, status); keputusan terbuka: Q-13 + P-1..P-10, Q-P12..Q-P14; Q-P9 diperbarui |
| `docs/STATUS_MANAJEMEN.md` | Ditulis ulang untuk Fase 1: apa yang jalan, status 7 syarat GO, risiko, 8 kebutuhan dari manajemen, biaya |

## Temuan saat menulis (untuk lead)
1. **(usulan Low, R-L01) `source_ref` migrasi = id sistem lama** (template `06_saldo_poin_awal_bff.csv`). Nilai ini masuk ke `loyalty_ledger.source_ref` + `idempotency_key`. Erasure tidak menyentuhnya, dan `efn_bff` memang tidak punya UPDATE pada kolom itu. ETL menyalinnya ke ClickHouse `loyalty_events`, lalu nilai yang sama juga dicatat di Loki `EFN_LOYALTY_MIGRATION` (400 h). Akibatnya, bila sistem lama masih menyimpan pemetaan id ↔ HP, jaminan SEC-31 "tidak dapat ditautkan" gugur untuk member hasil migrasi.
   - Usulan: validator E-25 + README template mewajibkan `source_ref` opak (HMAC dengan kunci batch yang dimusnahkan setelah rekonsiliasi). Biaya ±0,5 hari.
   - Harus diputuskan sebelum impor pertama, karena ledger tidak bisa diubah (Q-P13).
   - Diteruskan ke security-reviewer untuk diklasifikasi.
2. **Outbox Odoo `efn.pos.loyalty.outbox` tanpa retensi** (tidak ada `@api.autovacuum`). Usulan: hapus baris selesai setelah 400 h (Q-P14), ±0,5 hari odoo-engineer.
3. **Metabase menyimpan kredensial koneksi ClickHouse tanpa enkripsi aplikasi** selama `MB_ENCRYPTION_SECRET_KEY` belum diset (SEC-64 Open). Saat ini hanya terlindung LUKS/WAL-G.
4. Komentar `erasure.service.ts:179` rusak (dua komentar tergabung) dan menyebut reservasi "berisi member_ref_enc". Ini bertentangan dengan skema `05-*.sql`. Perlu dirapikan oleh bff-engineer (kosmetik).
5. Template migrasi menulis `source = migration` / `migration:<source_ref>`, sedangkan kode memakai `migration_opening`. README template perlu disamakan (functional).
6. Salinan audit ClickHouse ternyata sudah me-redaksi nilai pribadi (`audit.py` `line_copy_row`/`is_log_personal`). Konflik Q-P9(c) dari v3 tertutup secara desain. Residu: uid karyawan disimpan 10 th, dan `line_hash` atas nilai berentropi rendah bisa dikonfirmasi lewat tebakan.

## Keputusan yang butuh tanda tangan klien (baru/diperbarui)
- **Q-13** batas poin & parameter: Finance (syarat GO G-2).
- **P-1..P-10**: toko pilot, Q-27 peran kasir, PPN poin, sumber migrasi, dan seterusnya.
- **Q-P13** `source_ref` opak: DPO + Finance, sebelum impor saldo.
- **Q-P12** retensi `erasure_suppression`.
- **Q-P14** retensi outbox Odoo & Metabase.
- Daftar nama Superadmin (G-3).
- Supervisor peninjau harian & on-call (G-4).

## Terverifikasi / belum terverifikasi
- **Terverifikasi dengan membaca repo:**
  - skema `05/06-*.sql` (kolom, grant, retensi);
  - `app-config.ts:781,896,919,923` (default 90/24 jam/30/400);
  - `erasure.service.ts:166-181,242-252`;
  - `erasure-suppression.ts`;
  - `loyalty-anomaly.ts` (field tanpa PII);
  - model Odoo `efn.pos.loyalty.reservation` (autovacuum 400/min 30) dan `outbox` (tanpa GC, payload tidak disimpan), ACL `ir.model.access.csv`;
  - `efn.pos.otp.challenge.purpose` (30 h);
  - `loki.yml` (4 stream 9600h);
  - `03-gl-audit.sql` (tanpa `partner_id`), `04-fase1-marts.sql`, `clickhouse-init.sh` (TTL 5 th / audit 10 th / GL tanpa TTL), `metabase-access.sql`;
  - `services/etl/efn_etl/audit.py` (redaksi);
  - `jobs.py:123` (`source_ref` disalin).
- **Dari laporan agent (tidak saya jalankan):** smoke 153/153, erasure G13c (d), uji 497 Metabase, log bebas HP.
- **Belum terverifikasi:**
  - retensi riwayat query Metabase OSS v0.52.4;
  - job retensi reservasi/earn di runtime;
  - `host-journal` di host nyata;
  - nomor pasal UU PDP;
  - apakah sistem lama klien memakai id yang dapat dipetakan ke HP (bergantung P-9).

# Gelombang 15 — Inventaris PDP v5 (F&B/KDS, recon, near-miss), ADR-032/033, syarat GO KDS (2026-10-09)

Basis: `39f6074`, review r8 (SEC-72, SEC-74, SEC-75, SEC-77, SEC-79, syarat GO KDS 1–8), laporan G14/G15 odoo-engineer, devsecops, data-AI, odoo-functional, `docs/08` §7. Hanya dokumen; tidak ada kode diubah, tidak ada commit.

## Ringkasan (untuk manajemen)
Daftar data pribadi kini mencakup layar dapur dan hasil pencocokan poin. Keduanya tanpa identitas pelanggan; satu-satunya celah adalah catatan dapur yang diketik bebas oleh kasir, dan celah ini ditangani dengan SOP. Near-miss log dev (nomor fiktif) dicatat di register baru. Dua keputusan baru menunggu klien: QR order dari ponsel (ADR-032, rekomendasi kiosk dulu) dan model akun tablet dapur (ADR-033, rekomendasi akun internal tersandbox untuk pilot). Indeks keputusan memuat 8 syarat GO KDS beserta statusnya.

## File dibuat / diubah
| File | Perubahan |
|---|---|
| `docs/compliance/PDP_DATA_INVENTORY.md` | **v5**: ringkasan v5; §1 kategori operasional dapur / akun perangkat / hasil rekonsiliasi; §2.2b baru (`efn.kds.ticket(.line)` 90 h tanpa PII/harga + risiko `internal_note`/`note` teks bebas, `efn.kds.station`, akun perangkat tersandbox, `pos.session.efn_kds_listener_user_ids`, flag `pos.order`, kunci self-order); §2.4 `loyalty_recon_run/finding` (400 h, sampel id numerik) + kolom sink/jangkar `audit_recon_run`; §2.6 redaksi G15 + uji CI, koreksi klaim "0 baris HP", `EFN_KDS_TICKET_FAILED`, `etl_recon`, `mac` sink sudah di-wire; §2.7 tablet KDS & kiosk; §3 matriks; **Q-P15, Q-P16**; §5; **§6 register near-miss** (NM-2026-01 = SEC-72) |
| `docs/decisions/ADR-032-qr-order-dari-ponsel.md` | **Baru, Proposed**: A kiosk (pilot) / B Super-App + BFF E-27 (draf kontrak API + RPC + queue `fnb.order.status_changed`) / C `pos_self_order` mobile ditolak (ADR-023) / D Wi-Fi tamu ditolak; Q-QR1..Q-QR5 |
| `docs/decisions/ADR-033-akun-perangkat-kds.md` | **Baru, Proposed**: A user internal + allowlist + sandbox `ir.rule` (pilot) / B user share (spike sebelum outlet ke-2) / C token URL ditolak / D akun bersama ditolak; Q-KD1..Q-KD4 |
| `docs/decisions/README.md` | Catatan G15; indeks ADR-032/033 (total 33); urutan tanda tangan 4c; **Syarat GO pilot KDS K-1..K-8** + status G15; keputusan terbuka P-11..P-18, Q-QR, Q-KD, Q-P15/16; G-6 → selesai (`ci.yml:317` = 20) |
| `docs/STATUS_MANAJEMEN.md` | Ditulis ulang per 9 Okt: Fase 1 + Fase 2 (KDS siap pilot bersyarat), tabel 8 syarat, risiko, kebutuhan manajemen, biaya |

## Temuan saat menulis (untuk lead)
1. **Catatan dapur = data kesehatan potensial.** Catatan seperti "alergi kacang" yang dikaitkan dengan orang dapat tergolong data spesifik (kesehatan). Mitigasi saat ini hanya SOP. Masking teknis memakai `audit_policy.mask_free_text` (±0,5–1 hari odoo-engineer) tidak menangkap nama orang; tercatat di Q-P15.
2. **Tidak ada stream Loki `efn_kds`.** `EFN_KDS_TICKET_FAILED` masuk stream umum 30 h dan rule alert belum ada. Inventaris mencatat keadaan sebenarnya (devsecops G16).
3. **ETL tiket KDS → ClickHouse belum ada.** Bila dibuat, `internal_note`/`note` tidak boleh disalin (dicatat di §2.2b).
4. **Q23 asli docs/01** ("siapa yang menutup bill QRIS di meja") berbeda dari Q-23 versi `docs/08` P-17. ADR-032 memakai versi P-17 dan menyebut perbedaannya.
5. `pos.session.efn_kds_listener_user_ids` (uid kasir) tidak dipurge dan ikut umur `pos.session`. Dampaknya kecil (data karyawan, akses manager); dicatat di inventaris.
6. Status syarat K-1..K-8 diambil dari laporan agent G15 dan **belum** di-review ulang security (r9).

## Keputusan yang butuh tanda tangan klien
- **ADR-032 / Q-QR1 (= Q-23, P-17)**: kiosk vs ponsel; Q-QR2..5 bila ponsel.
- **ADR-033 / Q-KD1, Q-KD3**: akun tablet internal tersandbox untuk pilot; pemegang 2FA. Q-KD2/Q-KD4.
- **P-11..P-18**: outlet F&B, stasiun & target menit (Q-19), PB1/SC (P-16), perangkat & printer (Q-24).
- **Q-P15** retensi tiket KDS + SOP catatan; **Q-P16** near-miss (klasifikasi, hapus baris dev, pemilik register).
- Yang lama tetap terbuka: Q-13, Q-P1, Q-P12..Q-P14, server staging.

## Terverifikasi / belum terverifikasi
- **Terverifikasi dengan membaca repo:**
  - `efn_kds_ticket.py` (field, `DEFAULT_RETENTION_DAYS=90`, autovacuum berdasarkan `sent_at`, `NOTE_MAX=500`);
  - `ir.model.access.csv` KDS;
  - `pos_config.py` (`efn_kds_listener_user_ids` ber-`groups` manager);
  - manifest `efn_pos_kds` 19.0.1.1.0 / `efn_setup_pos` 19.0.1.5.0;
  - `05-loyalty-recon.sql` (TTL 400 h, kolom), `metabase-access.sql`, `etl-access.sql`;
  - `promtail.yml` (`etl_recon`, tidak ada `efn_kds`);
  - `ci.yml` (`EFN_TOUR_MIN_TESTS: "20"`, job `promtail-redaction` di `ci-gate`);
  - tidak ada referensi KDS di `efn_privacy`/ETL.
- **Sumber primer:** `odoo/odoo@19.0` `addons/auth_totp_mail/models/res_config_settings.py` → `auth_totp.policy` ∈ {`employee_required`, `all_required`} (dipakai di ADR-033 opsi B).
- **Dari laporan agent (tidak saya jalankan):** 337 unit / tour 20/20, uji runtime sandbox, dry-run & CI redaksi, siklus ETL recon, hitungan near-miss.
- **Belum terverifikasi:**
  - layar KDS untuk user share di 19.0;
  - kiosk 19.0 meminta nama/HP dan menulis `last_order_preparation_change`;
  - upgrade `-u` dari DB lama;
  - harga kiosk/tablet dan estimasi Rupiah E-27 (turunan tarif ADR-031);
  - nomor pasal UU PDP (Pasal 46 dikutip dari r8);
  - Mermaid tidak dirender.

# Gelombang 17 — Kontrak v5.0 Fase 3 (penjualan online Super-App), ADR-034, revisi ADR-019 (2026-10-09)

## Ringkasan (untuk manajemen)
Kontrak penjualan online ditulis lengkap di `docs/04 §10`. Pilot: PT Retail, ambil di 1 toko, bayar sekali lewat QRIS (provider masih `mock`). Odoo tetap sumber harga, pajak, stok, dan faktur, dan tetap tidak terbuka ke internet: BFF hanya memanggil 10 fungsi khusus Odoo lewat jalur internal yang menolak semua fungsi lain. Pekerjaan dibagi ke 5 agent dengan batas file yang jelas. Perkiraan ±6–7 minggu kalender bila paralel (Odoo E-28 ±4–5 minggu adalah jalur kritis). Belum ada keputusan klien atas Q-ON1..Q-ON13; semua angka PLACEHOLDER dan configurable.

## File dibuat / diubah
| File | Isi |
|---|---|
| `docs/04_SERVICE_CONTRACTS.md` | Judul → v5.0; **§10 baru** (10.1 prinsip & default pilot, 10.2 identitas, 10.3 API pelanggan: katalog/stok/gambar, cart Redis, quote, checkout `Idempotency-Key` + `accept_total`, status/daftar/batal, kode ambil, peta error K-ON5; 10.4 transport json2 + `odoo-rpc-gateway` + 10 metode + **delta E-28** §10.4.4; 10.5 event Odoo → BFF K-ON1 (service key + HMAC + replay + `seq`); 10.6 saga: state order & bagian (Mermaid), sequence, kunci idempotensi K-ON3, timeout/bayar terlambat/rekonsiliasi, kompensasi & refund per bagian; 10.7 payment `purpose`/porsi K-ON4/`refund()`; 10.8 tabel BFF + retensi + DSAR; 10.9 queue; 10.10 kredensial; 10.11 metrik & alert; 10.12 keamanan; 10.13 env; 10.14 ditunda/belum terverifikasi; **10.15 Paket kerja G17**). §1–§9 hanya ditambah rujukan (akhir §7.3, §8.3). |
| `docs/decisions/ADR-034-kanal-online-bff-json2-terfilter.md` | **Baru, Proposed**: kanal (website_sale / BFF+E-28 / ORM generik / portal), transport (xmlrpc-jsonrpc / json2 / controller custom), katalog (proxy / pull / push); keputusan B + json2 + pull; konsekuensi, biaya, usulan R-T15 |
| `docs/decisions/ADR-019-unified-cart-merchant-of-record.md` | Catatan revisi 9 Okt: A1 default pilot, A2 `account_payment_interco` PoC setelah opini pajak, C bila gateway split; desain wajib 5–8 (porsi, refund per bagian, Q-ON2, Q-ON9). Status tetap Proposed |
| `docs/decisions/README.md` | Catatan G17, indeks ADR-034 (total 34), urutan tanda tangan 8/8a, **tabel Q-ON1..Q-ON13** + pertanyaan IT (masa key) & Finance (poin online) |
| `docs/02_TARGET_ARCHITECTURE.md` | Baris revisi v5; diagram §3.4 diganti sesuai kontrak (tanpa klaim keputusan klien) |

## Keputusan teknis kunci (usulan architect, dalam batas keputusan lead)
1. **Bagian per kanal** (`channel_code`), bukan per `company_code`: F&B/brand kelak = kanal baru tanpa breaking change. `checkout_ref` = `order_id`, `platform_order_ref` = `part_id` (satu per SO).
2. **Router internal `odoo-rpc-gateway`** (HAProxy, pola `s3-gateway`): mTLS CN `api-gateway`, POST ke **10 path persis** (bukan wildcard `efn.online.*` — wildcard akan meloloskan `action_efn_ready` dll.), DB header dipaksa, cookie dibuang. 3 metode UI staf tetap lewat web client WireGuard.
3. **Kode ambil**: dibuat BFF, AES-GCM di BFF (harus bisa ditampilkan ulang), **hash KDF lambat di Odoo** (mengubah spec E-28 Char(8) polos), endpoint terpisah `GET /v1/orders/:id/pickup-code` agar polling status tidak membawanya.
4. **Event Odoo → BFF = service key + HMAC body (secret baru `odoo_online_event_hmac`)** agar replay window bermakna; `seq` per order untuk menolak event basi; 404/409 final.
5. **Bayar terlambat tidak memanggil `mark_paid`** (mencegah race dengan pembatalan); langsung refund gateway penuh.
6. **Checkout sinkron sampai place + intent** (pelanggan menunggu ≤ ±10 s), sisanya async lewat 4 queue baru: `order.paid`, `order.unpaid_closed`, `order.compensate`, `order.refund.requested` (+ `notify.push` yang ada); timeout lewat sweeper DB (RabbitMQ tanpa delayed plugin).
7. Delta E-28 lain: error `UserError("efn_online:<kode>")`, cancel ref tak dikenal → `absent`, context dibersihkan kecuali `lang`, `payment_timeout_minutes` default 25 (BFF 15 + margin).
8. Gateway interface + `refund()` (mock saja; vendor → `manual_required`) dan `portions` (siap ADR-019 opsi C).

## Hal yang butuh keputusan lead
- **L-1** Setujui delta E-28 §10.4.4 (terutama hash kode ambil & HMAC event) — mengubah spesifikasi functional docs/08 §8.3; functional perlu diberi tahu.
- **L-2** Secret baru `odoo_online_event_hmac` vs cukup service key yang ada (tanpa replay window bermakna).
- **L-3** Refund CS pilot lewat **CLI BFF** (tanpa UI) — cukup untuk pilot?
- **L-4** DSAR: `DELETE /v1/me` ditolak `409 open_online_orders` selama ada pesanan dibayar yang belum selesai (alternatif: tetap hapus, pelanggan kehilangan akses kode ambil).
- **L-5** Retensi usulan (tautan member 730 hari, event 180 hari, baris keuangan 10 th) → masuk paket DPO; inventaris PDP versi berikut dijadwalkan setelah implementasi.
- **L-6** Usulan risiko **R-T15** (API key kanal kedaluwarsa → checkout berhenti) ke register docs/01; R-T13 di docs/01 terpakai dua kali (perlu dinomori ulang oleh pemilik register).
- **L-7** Paket kerja: frontend dapat mulai paralel dengan mock BFF; BFF memerlukan mock Odoo json2 di Jest sampai E-28 tersedia.

## Keputusan yang butuh tanda tangan klien
ADR-034 (arsitektur kanal; IT + Security + PO), ADR-019 revisi (A1/A2/C; Finance + Tax + Legal), Q-ON1..Q-ON13 (tabel di `docs/decisions/README.md`), masa berlaku & pemegang rotasi API key kanal (IT), poin online (Finance).

## Terverifikasi / belum terverifikasi
- **Terverifikasi dengan membaca repo:** `services/api-gateway/src/payments/**` (transisi monoton, `expired→paid` diterima, nominal beda tidak diubah, `simulate` dev-only, adapter vendor eksperimental), `03-platform-v3.sql` (DDL `payment_intent`, CHECK `company_code`/`method`), `02-platform-loyalty.sql` (`outbox`), `common/amqp/queues.ts` (queue & argumen DLQ), `infra/rabbitmq/rabbitmq-entrypoint.sh` (definitions broker), `docker-compose.yml` (`odoo-tls --allow-cn=traefik,blackbox-exporter`; router Traefik `api` publik kecuali `/v1/pos`, `/internal`, `/metrics`, webhook POST), `infra/s3-gateway/README.md` (pola filter L7 + pelajaran SEC-41), `superapp-web/lib/request-guard.ts` (`isSameOrigin`), `lib/mock-catalog.ts`/`CartDemo` (masih mock).
- **Dari docs/08 §8 (functional, tidak saya ulang verifikasi di source):** temuan #36–#45, bentuk 10 metode E-28, `api_key_duration` default 1 hari.
- **Belum terverifikasi:** status HTTP error `/json/2` per exception di 19.0; perlu/tidaknya `X-Odoo-Database`; CryptContext/pbkdf2 dari addon untuk hash kode ambil; `x-delivery-limit` pada versi RabbitMQ yang dipin; deprecation XML-RPC/JSON-RPC di 19.0 (dari dokumentasi Odoo, bukan source); estimasi minggu-engineer BFF/frontend/devsecops (asumsi architect); Mermaid tidak dirender. Tidak ada kode/test yang dijalankan.

# Gelombang 17 penutup — Inventaris PDP v6 (belanja online), status manajemen, governance SEC-111, keputusan 10 Okt (2026-10-10)

## Ringkasan (untuk manajemen)
Daftar data pribadi kini mencakup belanja online. Tambahan data pribadinya kecil: riwayat pesanan yang ditautkan ke akun lewat kode acak. Odoo tidak menerima identitas pembeli, dan kode ambil dihapus setelah pesanan selesai. Masa simpan masih usulan dan butuh keputusan DPO (Q-P17..Q-P21). Aturan "pembatal pesanan di Odoo ≠ penyetuju refund" kini tertulis lengkap dengan prosedur review akses bulanan, dan dicatat sebagai risiko R-S14. Keputusan pemilik proyek 10 Okt (kapasitas opsi B, bahasa id_ID ditunda) dicatat; ADR-034 tetap Proposed.

## File diubah
| File | Isi |
|---|---|
| `docs/compliance/PDP_DATA_INVENTORY.md` | **v6**: ringkasan v6, penanda ⚠ DPO; §1 lima kategori online; **§2.1b** tabel BFF online (tautan `member_id` 730 h, baris keuangan 10 th, `pickup_code_enc` K + AAD per bagian, `qr_string` 1 h, `online_refund.initiated_by` = identitas karyawan, `online_admin_audit` tanpa retensi, event 180 h, katalog non-PII); **§2.2c** Odoo online tanpa PII (partner generik, `pickup_code_hash` KDF dikosongkan saat final, alasan batal di chatter, wizard tanpa simpan kode); §2.3 bucket `catalog`; §2.6 Redis `cart:*`/`oq:*`/`ocat:stk:*`/rate limit IP ter-HMAC, queue `order.*`, Loki `efn_online` (30 h) & `host-efn-online-cs` (400 h), log gateway; §2.7 browser, staf toko, Meta (template online tanpa parameter), payment gateway; §3 matriks; **Q-P17..Q-P21**; §5; §6 **INFO-2026-01** (kutipan fixture di laporan agent, bukan PII/secret) |
| `docs/STATUS_MANAJEMEN.md` | Ditulis ulang per 10 Okt: Fase 3 (fitur, smoke 267/0, review GO merge, online mati di prod), keputusan PP-1/PP-2, 7 syarat aktivasi + pemilik, Q-ON1..Q-ON13, risiko, permintaan ke manajemen, biaya, langkah berikutnya |
| `docs/runbooks/online-orders.md` | §CLI CS: approver bukan Sales Manager; **bagian baru `#sec-111`**: aturan 1–5, register peran di luar repo, review akses bulanan (perintah `getent`/`comm` + SQL read-only Sales Manager efektif via grup implied), tindak lanjut pelanggaran, retensi berkas review 13 bulan. (Bagian sizing diperbarui paralel oleh devsecops, tidak saya ubah.) |
| `docs/runbooks/key-custody.md` | §2 Peran: operator/approver CS online, larangan rangkap Sales Manager ↔ `efn-cs-approver`, Superadmin sebaiknya bukan approver rutin |
| `docs/01_REVIEW_RISK_GAP_ANALYSIS.md` | **R-T15** (API key kanal kedaluwarsa, L-6); **R-S14** (SEC-111, kontrol prosedural, backlog peta `odoo_uid ↔ akun host`); R-T13 ganda (React Native) dinomori ulang **R-T16** (tidak ada rujukan lain) |
| `docs/decisions/README.md` | Catatan 10 Okt; baris ADR-034 (tetap Proposed); bagian **Keputusan pemilik proyek 10 Okt** (PP-1 sizing B, PP-2 id_ID ditunda); **Syarat aktivasi produksi online A-1..A-7** + pemilik; tabel UU PDP v6 Q-P17..Q-P21; baris sizing di tabel Fase 3 ditandai diputuskan |

## Keputusan yang dicatat
- **Pemilik proyek 10 Okt 2026:** PP-1 sizing opsi B (`ODOO_WORKERS=6`, `ODOO_RPC_MAXCONN=2`, pool 1 + 1); PP-2 id_ID Odoo tidak diaktifkan. Keduanya keputusan operasional; **ADR-034 tetap Proposed**.

## Butuh keputusan DPO / klien
- **DPO/Legal:**
  - **Q-P17** paket retensi L-5;
  - **Q-P18** penundaan hapus akun L-4 (sah? batas maksimum?);
  - **Q-P19** retensi `online_admin_audit` + SOP alasan tanpa nama pelanggan;
  - **Q-P20** DPA/lokasi/data pembayar payment gateway;
  - **Q-P21** PII pembeli di Odoo bila Q-ON7 = ya.
- **Klien (Superadmin/IT + Finance):** nama Sales Manager Odoo dan approver refund (orang berbeda), saksi review akses bulanan, register peran (di luar repo).
- **Finance + Legal + IT:** vendor payment (A-4).
- **PO/Ops/Finance/Tax:** Q-ON prioritas A.

## Temuan baru (untuk lead)
1. `online_admin_audit` tidak punya job retensi (tak terbatas): masuk Q-P19. Bila DPO memilih angka, kerjanya ±0,5 hari bff-engineer.
2. Erasure tidak menghapus key Redis `cart:<member_id>`. Key tertinggal ≤ 7 hari dengan UUID akun yang sudah dihapus, tanpa identitas lain. Usulan: `DEL` di erasure, ±1 jam bff-engineer (dicatat di Q-P17).
3. Staf Odoo masih bisa **mengetik** PII secara manual di SO online (chatter/catatan). Kontrolnya hanya SOP.

## Terverifikasi / belum terverifikasi
- **Dibaca di repo:**
  - `07-platform-online.sql`;
  - `online.repository.ts` (`retention`, `memberOpenPaidParts`);
  - `online-jobs.service.ts`;
  - `erasure.service.ts` (langkah 6b, SEC-100);
  - `online-privacy.service.ts` (isi export, batas 1.000);
  - `cart-store.ts` (TTL 7 hari, key);
  - `pickup-code.ts` (AAD v2);
  - `notification/templates.ts` (template online tanpa parameter);
  - `haproxy.cfg` (`log-format`);
  - `promtail-journal.yml` (job `host-journal`) dan `loki.yml` (retensi);
  - README `efn_online_order` dan `efn_online_common`/`sale_order` (alasan batal ke chatter);
  - alert `EfnOnlineApiKey*` di rules Prometheus;
  - `.gitleaksignore` dan commit `5e496ef`/`fb1cc9f`.
- **Belum terverifikasi:**
  - nama tabel `res_groups_implied_rel` di DB Odoo 19.0 EFN (ditandai di runbook, wajib dicek di staging);
  - wrapper dan jejak journald di host nyata;
  - job retensi pada data berumur nyata;
  - perbaikan SEC-112 (`32686c3`) dan SEC-110 (working tree, belum di-commit) belum dibaca ulang reviewer;
  - nomor pasal UU PDP;
  - estimasi Q-ON7 ±1–2 minggu (kasar);
  - tidak ada kode/test yang saya jalankan.

---

# Gelombang 18 — Pencatatan keputusan pemilik proyek 10 Okt 2026 (2026-10-10)

- **Lingkup tulis:** hanya `docs/`. Tidak ada kode yang diubah; tidak ada commit/push.

## Ringkasan (untuk manajemen)
Pemilik proyek menjawab semua pertanyaan terbuka (60 butir). Semua jawaban dicatat per ID beserta dampaknya. **8 ADR kini Accepted** (Gelombang 1 + ADR-032 opsi B). Satu ADR baru (**ADR-035**, portal) menunggu tanda tangan. Ada **5 risiko baru**, dan inventaris UU PDP naik ke **v7**. Jawaban memunculkan **28 paket kerja** (±52–79 minggu-engineer). Urutan yang direkomendasikan: pilot ritel dulu (G18–G19), F&B (G20), lalu produksi penuh (G21+).

## File dibuat / diubah
| File | Perubahan |
|---|---|
| `docs/decisions/README.md` | Pembaruan G18 di kepala; indeks: 8 Accepted, catatan 10 Okt di 019/020/022/023/027/030/031/033/034, baris ADR-035; urutan tanda tangan per 10 Okt; bagian baru **"Keputusan pemilik proyek 10 Okt 2026 (sesi tanya-jawab)"** (A–F per ID + G "masih menunggu data klien"); semua tabel pertanyaan lama dicoret "terjawab 10 Okt"; catatan pembaruan di syarat GO loyalty/KDS/aktivasi online |
| `ADR-001, 004, 005, 013, 017, 018, 021` | Status **Accepted** (pemilik proyek, 2026-10-10) + catatan penerimaan (001, 004, 005, 013) |
| `ADR-032` | **Accepted — opsi B**; bagian keputusan Q-QR1..Q-QR5 |
| `ADR-019`, `ADR-027` | Catatan 10 Okt (Q-ON9; nilai awal PB1/SC + ⚠ PBJT non-dine-in); status tetap Proposed |
| `ADR-020`, `ADR-022`, `ADR-023`, `ADR-030`, `ADR-031`, `ADR-033`, `ADR-034` | Catatan keputusan 10 Okt di kepala (status tidak dinaikkan kecuali rincian ADR-031 = "diputuskan") |
| `ADR-035-strategi-portal-pelanggan-vendor-franchisee-karyawan.md` | **Baru (Proposed)** |
| `docs/01_REVIEW_RISK_GAP_ANALYSIS.md` | Risiko baru **R-B11, R-T17, R-L09, R-L10, R-S15**; R-T15 & R-L04 diperbarui |
| `docs/compliance/PDP_DATA_INVENTORY.md` | **v7**: §0a keputusan DPO/retensi + status kode, kategori baru (rencana), §2.2d PII baru di Odoo & portal, §2.7 transfer, matriks §3, §4 dijawab, §5/§6 |
| `docs/10_RENCANA_GELOMBANG_18_PLUS.md` | **Baru**: 28 paket kerja (W-01..W-28), estimasi, urutan gelombang, alasan |
| `docs/STATUS_MANAJEMEN.md` | Ditulis ulang per keputusan 10 Okt |

## Keputusan yang butuh tanda tangan / konfirmasi
- **ADR-035** (IT + Security + PO), termasuk revisi terbatas ADR-023.
- Formalitas status ADR-030 (IT + Security). Jawaban bisnis sudah ada.
- **Penafsiran yang harus dikonfirmasi lead/Finance:**
  - Q-P7 vs Q-6: baris DWH disimpan 10 th, kolom pseudonim 5 th.
  - Q-V3 = `efn_account_reports` (opsi b).
  - Q-P18: setelah 14 hari erasure tetap jalan.
- **Keputusan lead/PO:**
  - apakah pilot ritel menunggu W-08 (enkripsi PII Odoo) atau berjalan dengan pengecualian sementara;
  - apakah F&B online benar-benar dijalankan bersamaan dengan pilot ritel.

## Temuan saat menulis
1. `ONLINE_ADMIN_AUDIT_RETENTION_DAYS` **sudah** punya job (default 400, maks 3650), sehingga inventaris v6 yang menyebut "tak terbatas" sudah usang. v7 sudah mengoreksinya.
2. Batas atas `ONLINE_PAYMENT_TIMEOUT_SECONDS` di `app-config.ts` = 3600, pas untuk keputusan 1 jam. Bila kelak butuh margin di atas 60 menit, batas config harus dinaikkan.
3. `ERASURE_SUPPRESSION_DAYS` maks 730, jadi 365 tidak perlu perubahan validasi.
4. Q-P21 + Q-E3 bertabrakan dengan pencarian standar Odoo (NPWP/HP terenkripsi tidak bisa dicari teks bebas). Butuh *blind index* dan fit-gap e-Faktur sebelum W-06.
5. P-12 "selain dine-in tidak dikenakan pajak" berisiko terhadap UU HKPD (R-L09). Usulan default konservatif menunggu Finance.
6. Dengan portal vendor, biaya 2FA `all_required` (penghalang ADR-033 opsi B) hilang. Ada risiko baru: user *share* KDS bisa login di host portal. Penangkalnya `efn_portal_guard` berbasis grup.

## Terverifikasi / belum terverifikasi
- **Dibaca di repo:**
  - `app-config.ts` (default retensi & timeout);
  - `online-jobs.service.ts` (job audit admin);
  - `clickhouse-init.sh` (TTL `RETENTION_YEARS`);
  - `docs/04` §9.3 (redeem = baris diskon);
  - `docs/08` §6.5–6.6, §7.6, §8.6–8.7;
  - ADR terkait.
- **Belum terverifikasi:**
  - semua estimasi (perkiraan kasar, tanpa fit-gap);
  - route portal vendor CE 19.0;
  - `auth_totp.policy=all_required` untuk portal di runtime;
  - Coraza/OWASP CRS di HAProxy repo;
  - fitur langganan/embedding Metabase OSS v0.58;
  - TTL kolom ClickHouse pada tabel yang ada;
  - pemodelan service charge sebagai pajak dengan `include_base_amount` di POS 19;
  - aturan PBJT takeaway per Perda (wajib konsultan);
  - nomor pasal UU PDP.

## Gelombang 18 — lanjutan: kontrak privasi L-21, runbook erasure paksa, status PDP v7 (2026-10-10)
Sumber: laporan efn-bff-engineer dan efn-data-ai-engineer, bagian Gelombang 18. Hanya dokumen; tidak ada commit.

| File | Perubahan |
|---|---|
| `docs/04_SERVICE_CONTRACTS.md` | Header v5.1. Isi kontrak baru: L-21 di §10.17, yaitu (a) hapus akun 409 → `?confirm_open_orders=true` → 202 `erasure_deadline`, tunda maks 14 h, eskalasi hari 3/10, lalu erasure paksa; (b) `age_confirmed` 17+; (c) `source_ref` opak; (d) migrasi 08 + env; (e) teks pelanggan menunggu Legal. Penempatan: §3.2 (`age_confirmed`), §8.9 (alur hapus akun), §9.6 setelah K-7 (`source_ref`). §10.8.3: judul "diputuskan 10 Okt", cart dihapus saat erasure, alur DSAR baru. L-4 ditandai "diganti L-21a" |
| `docs/runbooks/online-orders.md` | Bagian baru `#erasure-paksa`: sinyal hari 3/10/14, `online-erasure-pending` → `online-refund --full` (approver menyetujui) / `online-resolve` (cancel hanya approver) / serah terima setelah verifikasi kanal lain. Kode ambil tidak pernah ditampilkan ke CS. Catatan: subcommand wrapper belum ada |
| `docs/compliance/PDP_DATA_INVENTORY.md` | §0a "Status kode" diperbarui: Q-P3, P7/6/49, P8, P12, P13, P14 (Metabase), P17 (koreksi: hapus cart sudah sejak G17), P18, P19 = sudah di kode G18. Baris §2.1/§2.4/§3, ringkasan v7, §5 ikut diperbarui. Teks pelanggan ditandai menunggu Legal |
| `docs/decisions/README.md` | Koreksi baris Q-P17 (hapus cart sudah ada) |

**Belum:**
- Outbox Odoo GC 10 th dan W-17.
- Subcommand wrapper `erasure-pending` (devsecops).
- Smoke E2E BFF G18.
- Pernyataan usia untuk pendaftaran via kasir POS.
- `.env` dev lokal masih `CH_MART_RETENTION_YEARS=5`.

# Gelombang 19 — ADR-036 enkripsi kolom PII Odoo (W-08) (2026-10-18)
Branch `chore/wave-19`. Hanya dokumen, tanpa commit (commit oleh lead). ADR-022, `docs/04`, inventaris PDP, dan dokumen bisnis asli **tidak** diubah; perubahannya ditulis sebagai usulan di ADR-036.

## Ringkasan (untuk manajemen)
HP, email, NPWP/NIK, dan alamat pelanggan di Odoo akan dienkripsi oleh Odoo sebelum masuk database, dengan pola kunci yang sama seperti BFF. Database, backup, atau akun baca yang bocor tidak lagi membuka data itu. Kasir hanya bisa mencari pelanggan dengan HP/email/NPWP **lengkap**. Biaya ±4–6 minggu-engineer (±Rp20–54 jt), lisensi Rp0, didahului uji coba teknis 3 hari. Rekomendasi: pasang sejak DB produksi pertama dibuat, sehingga pengecualian E14 tidak perlu dipakai.

## File dibuat / diubah
| File | Perubahan |
|---|---|
| `docs/decisions/ADR-036-enkripsi-kolom-pii-odoo.md` | **Baru (Proposed).** Konteks + fakta CE 19.0 terverifikasi; model ancaman; 5 opsi (disk saja, pgcrypto, ORM, TDE, vault BFF) dengan biaya; keputusan opsi C; cakupan S1; format `efnpii1:` + blob §7.2; key ring `odoo_pii_key_v<n>`; blind index HKDF/HMAC `odoo_pii_bidx_key`; perilaku pencarian/POS; mode terkunci; yang tidak didukung; Coretax; laporan; ETL; backup; rotasi; migrasi in-place (+ query verifikasi, VACUUM FULL/REINDEX, residu backup); erasure; skema log `EFN_PII_KEYRING`; usulan revisi ADR-022 (Keputusan 5, tabel kunci, kustodi, E14); usulan kontrak K-36-1..K-36-8; keputusan lead L-36-1..6; spesifikasi engineer (PoC gerbang, 3 modul, 11 test); Q-PII1..6; usulan R-T18 |
| `docs/decisions/README.md` | Catatan pembaruan 18 Okt; baris indeks ADR-036; baris urutan tanda tangan 2a |
| `docs/agent-reports/efn-architect.md` | Bagian ini |

## Keputusan yang butuh lead / PO
- **L-36-1 jadwal:** PP-3 tetap. Bila DB produksi dibuat setelah W-08 merge, W-08 dipasang sejak awal. Bila pilot mulai lebih dulu: E14 versi baru (larangan W-06, rehearsal migrasi) berakhir pada salah satu dari: migrasi terverifikasi, outlet ke-2, W-06, atau 31 Jan 2027, mana yang lebih dulu.
- L-36-2 cakupan S1.
- L-36-3 partner terlindungi tidak dipreload ke POS.
- L-36-4 `pos.order.email/mobile` tidak disalin.
- L-36-5 estimasi 3–5 → 4–6 minggu.
- L-36-6 gerbang PoC.

## Keputusan yang butuh tanda tangan klien
ADR-036 (IT + Security; Legal/DPO; Finance + Tax; PO + Ops) dan Q-PII1..Q-PII6:
- nama tetap plaintext;
- salinan kontak order POS dihapus;
- kasir mencari dengan HP lengkap;
- retensi backup off-site;
- kapan tahap 2 (vendor/karyawan);
- penyimpanan XML Coretax.

## Temuan untuk agent lain (lewat lead)
- **Fit-gap W-08 belum ada di `docs/08`** (G18 tidak mencakupnya walau direncanakan). Functional perlu mengerjakannya sebelum engineer (ADR-036 §8.1).
- Odoo 19.0 menyimpan **salinan plaintext** yang tidak disebut di `docs/10`: `email_normalized` (mail), `phone_sanitized` + indeks ekspresi (phone_validation), `pos.order.email/mobile` (compute stored). `vat` ber-index btree. `l10n_id_efaktur_coretax` menambah `l10n_id_nik` dan `l10n_id_buyer_document_number`. Field `mobile` tidak ada di `res.partner` 19.0.
- `member_hash_pepper` juga dipegang ETL, jadi tidak boleh dipakai sebagai kunci blind index Odoo.

## Terverifikasi / belum terverifikasi
- **Terverifikasi** (sumber primer `github.com/odoo/odoo` branch 19.0): field `res.partner` base, `_rec_names_search`, `_compute_same_vat_partner_id`, `email_normalized` stored (mail.thread.blacklist), `phone_sanitized` stored + SQL `REGEXP_REPLACE` (mail.thread.phone), `_load_pos_data_fields` / `get_new_partner` POS, `pos.order.email/mobile`, manifest + field partner `l10n_id_efaktur_coretax`, `cryptography==42.0.8` di requirements.
- **Terverifikasi di repo:** format/AAD/key ring BFF (`common/pii/*.ts`), `efn_member_hash` (sha256 + pepper, groups system), grant ETL tanpa `res_partner`, Dockerfile Odoo tanpa `cryptography` eksplisit.
- **Belum terverifikasi:**
  - hook ORM 19.0 yang menangkap semua jalur tulis (PoC);
  - domain pencarian klien POS;
  - cara generator Coretax membaca partner;
  - tracking field partner;
  - `cryptography` di image resmi;
  - durasi `VACUUM FULL`;
  - overhead dekripsi;
  - retensi off-site.

# Gelombang 20 (perencanaan) — ADR-036 Accepted, ADR-001 6 entitas, rencana G20+, draf DPIA (2026-10-18)
Branch `chore/wave-20`. Hanya dokumen, tanpa commit. Dokumen bisnis asli dan `docs/04` (milik lead) **tidak** diubah; usulan kontrak ditulis di ADR-036 §0.9 dan `docs/10` §A.2.

## Ringkasan (untuk manajemen)
- **Enkripsi data pribadi di Odoo disetujui** dengan cakupan yang lebih luas: pelanggan, vendor perorangan, karyawan, dan portal vendor; nama dan alamat ikut dienkripsi.
- **Pencarian** tetap bisa dengan nama lengkap, HP lengkap, dan awalan kata ≥ 3 huruf. Pencarian potongan di tengah kata dan urutan abjad tidak didukung.
- **Biaya hasil verifikasi** ±9–11,5 minggu-engineer, bukan 7–9. Selesai ±5–6 minggu dengan 2 engineer Odoo.
- **Struktur 6 perusahaan** dicatat di ADR-001. Biayanya murah sekarang (±2–3,5 minggu) dan mahal setelah ada data produksi.
- **Rencana G20:** struktur perusahaan, enkripsi pelanggan, dan bayar di kasir. Restoran mundur ke G22.
- **Draf DPIA** siap diisi DPO.

## File dibuat / diubah
| File | Perubahan |
|---|---|
| `docs/decisions/ADR-036-enkripsi-kolom-pii-odoo.md` | Status **Accepted 18 Okt**. Ringkasan baru. **§0 baru** (menang atas §1–§14): dasar keputusan; cakupan S1+ (kelas P/V/A/K, protect-by-default, pengecualian K-a); tabel field per model (termasuk `email_normalized` terenkripsi + blind index, `pos.order` selalu terenkripsi, `account.move.invoice_partner_display_name`, `hr.employee`/`hr.version`, XML Coretax); SEC-138 → `efnpii2:` terikat id (rebind pasca-insert); kunci ketiga `odoo_pii_name_key`; desain token nama (normalisasi, f/w/p, HMAC-128, padding 16, kolom + GIN, fallback tabel, ukuran, kebocoran diterima, tanpa trigram); dampak per area; `efn_region` + view ETL; XML Coretax terenkripsi; ralat ADR-022 & K-36 (+K-36-9..11); status L-36; **AC-1..AC-14** (L-39 a–d, SEC-136/137/138/140); estimasi ±9–11,5; Q-PII7..12; risiko R-T18/R-T19/R-O07. Penanda "⚠ Diralat" di §4.1, 4.2, 4.4, 4.5, 4.7, 4.8, 4.10, 4.11, 5, 6, 7, 8.3, 9, 12, 14 |
| `docs/decisions/ADR-022-enkripsi-in-transit-at-rest.md` | Status: revisi 18 Okt. Keputusan 5 "Batas cakupan" dicoret → teks baru. Tabel kunci +3 baris. E14 direvisi. Q-E3 ditutup. ADR tetap Proposed |
| `docs/decisions/ADR-001-single-odoo-multicompany.md` | Catatan revisi 18 Okt: 6 entitas sebagai company root (bukti cabang Odoo 19); TP; pajak; kode kanal per PT; member/poin (penerbit, FIFO, akrual, breakage); ETL/konsolidasi; W-32; Q-AD1-1..7. Status tetap Accepted |
| `docs/decisions/ADR-019-…md`, `ADR-020-…md` | Catatan 18 Okt (header) |
| `docs/10_RENCANA_GELOMBANG_18_PLUS.md` | **Bagian A baru**: status G18 (PR #60 merged) / G19 (PR #62 menunggu merge); paket baru W-29..W-34 + kontrak usulan W-29/W-30; paket berubah (E-38a/b, W-14b, W-24b, W-17, W-05, W-06, W-13, W-15, W-16, W-19, W-23, W-28, F&B → G22); estimasi per gelombang; graf dependensi; urutan G20–G23+; tabel "tidak bisa sebelum staging"; keputusan yang dibutuhkan |
| `docs/compliance/DPIA_DRAFT.md` | **Baru**: kerangka Pasal 34 (teks diverifikasi), pemicu a–g, 14 kegiatan, minimisasi, 22 risiko (inheren/residu), 10 mitigasi terbuka, residu, konsultasi, isian DPO D-1..D-11 |
| `docs/decisions/README.md` | Catatan 18 Okt; baris indeks 001/022/036; urutan tanda tangan 2a dicoret; Q-PII7..12, Q-AD1, Q-DPIA di tabel E |

## Estimasi & urutan yang direkomendasikan
- **E-38 penuh ±9–11,5 minggu-engineer** (E-38a 7,25–9,25; E-38b 1,75–2,25).
  - Angka lead 7–9 hanya tercapai bila XML Coretax tetap plaintext, data karyawan ditunda, dan laporan kota hanya per outlet. Ketiganya meralat keputusan pemilik proyek.
- **G20 (±17–22 minggu-engineer, ±5 minggu kalender):** W-32 (pertama), E-38a (paralel), W-30, W-33, W-14b, W-24b, teks pelanggan, fit-gap W-29/W-31/W-34/W-23.
- **G21:** E-38b, W-29, W-31, W-34, W-06, W-23, W-26.
- **G22:** restoran (W-07, W-04, W-27, W-12, W-17).
- **G23+:** W-15, W-16, W-05, W-13.
- **Sisa total ±57–79 minggu-engineer.**

## Keputusan yang butuh tanda tangan / konfirmasi klien
- **Q-PII7..12** (DPO/Finance/PO): nama karyawan plaintext, login portal opak, rekening bank tahap 1b, kode wilayah kab/kota, urutan non-abjad, XML terenkripsi.
- **Q-AD1-1..7** (Finance/Tax/Legal/PO): harga FARM & antar-brand, PPN FARM, NITKU outlet, produk lintas entitas, pengendali data member, nilai tukar poin seragam, kode final.
- **Q-DPIA-1..4** + isian DPO D-1..D-11; 18-38 grup auditor.
- **Lead/PO:** L-G20-1 (urutan G20, F&B ke G22), L-G20-2 (terima estimasi E-38 ±9–11,5).

## Usulan untuk agent lain (lewat lead)
- **Lead:** `docs/04` — K-36-1..11 (ADR-036 §0.9), kode entitas/kanal (§1, §11.3), kontrak W-29/W-30 (`docs/10` §A.2); masukkan **R-T18** ke register `docs/01` + usulan R-T19, R-O07.
- **efn-odoo-functional:** fit-gap lanjutan E-38 (ADR-036 §0.15 & §0.12 baris 1), W-32, W-29, W-31, W-34, W-30 (faktur ganda pos_sale), W-23.
- **efn-data-ai-engineer:** view `efn_etl.partner_region`, `dim_region`, `dim_company` 6 entitas.
- **architect (berikut):** inventaris PDP **v8** (cakupan ADR-036 S1+, XML Coretax, HR, region, off-site 35 h/12 bln).

## Terverifikasi / belum terverifikasi
- **Terverifikasi (sumber primer `raw.githubusercontent.com/odoo/odoo/19.0`, 18 Okt):**
  - `res.partner`: `_order = "complete_name ASC, id DESC"`, `_rec_names_search`, `name` (index), `complete_name` (compute stored index, depends), `display_name` depends, `_commercial_fields`/`_synced_commercial_fields = ['vat']`, `ADDRESS_FIELDS`;
  - `res.company`: `parent_id`/`child_ids` "Branches", "The company hierarchy cannot be changed", delegasi `currency_id`; `account` company: template COA dari induk, lock date `child_of`;
  - `account.move.invoice_partner_display_name` stored + `_rec_names_search = ['name','partner_id.name','ref']`;
  - `hr.employee` `_inherits hr.version`, `private_phone/email`, `emergency_*`; `hr.version` field privat (`tracking=True`, `groups hr.group_hr_user`);
  - POS `get_new_partner`, `_load_pos_data_domain`/`fields`, `get_limited_partners_loading` (SQL `ORDER BY order_count DESC, name`);
  - Coretax `efaktur_document.py` (`ir.qweb._render`, `ir.attachment` `raw`, `message_post`, route unduh).
- **Terverifikasi (pasal.id):** UU 27/2022 Pasal 34 ayat (1)–(3), Pasal 20 ayat (1)–(2) huruf a–f (ringkas).
- **Belum terverifikasi:**
  - flush compute `complete_name` dengan `EfnPiiChar`;
  - kinerja GIN token pada 200 rb–1 jt baris;
  - UX cari POS 19 (otomatis ke server?);
  - `filter_domain` view faktur/PO;
  - titik override Coretax;
  - `auth_signup` reset via email;
  - `mail.mail` auto_delete struk;
  - layar pelanggan POS CE 19 (W-23);
  - sumber/lisensi kode wilayah Kemendagri;
  - status PP pelaksana UU PDP;
  - semua estimasi = perkiraan architect;
  - Mermaid tidak dirender.

# Gelombang 22 — inventaris PDP v8, RoPA/DPIA anonimisasi & notifikasi hangus, runbook QR meja, status rencana, register risiko (2026-10-19)
Branch `chore/wave-22`, tanpa commit. Dasar: kontrak `docs/04` §14 + L-70..L-74 (milik lead, **tidak diubah**). Dokumen bisnis asli tidak disentuh.

## Ringkasan (untuk manajemen)
- **Daftar data pribadi sudah diperbarui** untuk fitur G20–G22. Sebagian besar fitur baru tidak menyimpan nama atau HP pelanggan.
- **Poin hangus dan penghapusan akun member tidak aktif** dinilai sebagai keputusan otomatis yang merugikan secara ekonomi. Keduanya tetap **mati di produksi** sampai DPO/Legal menetapkan dasar hukum, teks syarat, dan jalur keberatan. Ada 8 pertanyaan baru untuk DPO.
- **Panduan operasional QR meja** siap untuk outlet, CS, dan IT. Fitur QR sendiri masih dibangun.
- **Register risiko bertambah 9 risiko.** Yang tertinggi (skor 12): hangus massal tanpa pemberitahuan sah, dan akun akuntansi PLACEHOLDER yang belum ditinjau Finance.

## File dibuat / diubah
| File | Perubahan |
|---|---|
| `docs/compliance/PDP_DATA_INVENTORY.md` | **v8**: status/tanggal/terkait; ringkasan manajemen v8; **§0b** (18-14 meralat Q-P11 24 → 12 bln, 18-7/18-8/18-30..33/18-35/18-40, Q-PII9/10, L-63, L-68, L-70/71, L-73); §1 +7 kategori; catatan §2.2d (kini dibangun); **§2.8 baru**: ringkasan N1–N12 (dasar Pasal 20(2), retensi usulan, status, ⚠ DPO) + rincian BFF (§2.8.1), Odoo (§2.8.2), ClickHouse/ETL (§2.8.3), cache/log (§2.8.4), pihak ketiga (§2.8.5); §3 +15 baris retensi; §4 **Q-P22..Q-P29**; §5 +6 butir belum terverifikasi; §6 catatan G18–G22 (tanpa insiden tercatat) |
| `docs/compliance/DPIA_DRAFT.md` | Revisi 19 Okt: ringkasan tambahan; pemicu a & g diperluas; **P15–P18** (QR meja, poin online, hangus + notifikasi, anonimisasi retensi); **DR-23..DR-28**; M-4 diperbarui + **M-11..M-13**; isian **D-12..D-17**; pemicu tinjau ulang; **§13 baru**: RoPA R-22-A (anonimisasi karena retensi, D-22-14) & R-22-B (aktivitas, notifikasi WA T−30, hangus, D-22-16), alur Mermaid, kebutuhan & proporsionalitas, residu, kapan ditinjau |
| `docs/runbooks/fnb-qr-orders.md` | **Baru**: aturan wajib (token tidak disalin/difoto, tamu anonim, catatan tanpa nama), siklus sesi (Mermaid), A1 cetak ulang, A2 cabut sesi, A3 pesanan nyasar, A4 spam, A5 konfirmasi pelayan, A6 gangguan (outlet); B1–B3 CS (pertanyaan, poin QR, privasi); C1 gangguan BFF/Odoo/gateway, C2 kill switch (per POS/PT/rotasi darurat kunci token/poin), C3 investigasi anomali, C4 rekonsiliasi harian; batas jujur |
| `docs/runbooks/README.md` | Baris prosedur QR meja |
| `docs/10_RENCANA_GELOMBANG_18_PLUS.md` | Tanggal revisi; A.0 status 19 Okt; **A.1** menjadi "Status G18–G22": G19 #62, G20 #63, G21 #64 **merged** (isi paket, syarat aktivasi, sisa), G22 berjalan (selesai: kontrak §14, fit-gap §13, `efn_setup_role`, demo menu; berjalan: W-07/W-04/W-12/W-17) |
| `docs/01_REVIEW_RISK_GAP_ANALYSIS.md` | Register: **R-B12** (spam QR, 9), **R-B13** (fraud kasir bayar-di-kasir/retur, 9), **R-T18** (hook enkripsi ORM terlewati — kini 8 setelah gerbang CI `odoo-test-pii`), **R-T19** (frekuensi token, 4, diterima), **R-T20** (ketergantungan QR/poin online pada BFF→Odoo, 6), **R-L11** (hangus massal tanpa pemberitahuan sah, 12), **R-S16** (peran/SoD tanpa pemeriksa kedua, 8), **R-O07** (keluhan pencarian → ekspor CSV, 6), **R-O08** (akun PLACEHOLDER G20–G22, 12); catatan pembaruan R-L10 |

## Keputusan yang butuh tanda tangan klien
- **L-73 (masih terbuka):**
  - D-22-12 definisi "aktif" (Finance + Legal; DPIA D-16);
  - **D-22-14 anonimisasi** (DPO + Legal; Q-P22, D-12);
  - **D-22-16 dasar & kategori template WA** (Legal + Meta; Q-P25, D-13);
  - Q-F22-1..6 (termasuk **Q-F22-4 teks syarat program**, yang memblokir aktivasi job hangus).
- **DPO, baru:**
  - Q-P22 pasal & masa tenggang anonimisasi;
  - Q-P23 kode wilayah (age-out, ambang sel, SEC-162);
  - Q-P24 erasure partner pembeli faktur (L-68);
  - Q-P25 dasar notifikasi & hangus saat notifikasi gagal;
  - Q-P26 isi template & bukti pemberitahuan;
  - Q-P27 jalur keberatan Pasal 10(1);
  - Q-P28 retensi HR/rekening & erasure karyawan;
  - Q-P29 paket retensi QR (termasuk catatan baris 90 h).
- **Finance:**
  - checklist akun PLACEHOLDER per PT sebelum aktivasi (R-O08);
  - pemulihan poin hangus atas keberatan (Q-P27).
- **Ops F&B:** Q-QR6 (mode konfirmasi; default `waiter`).

## Temuan untuk lead (tidak saya ubah, `docs/04` milik lead)
1. **Batas kiriman per sesi tidak selaras:** BFF `FNB_MAX_SUBMISSIONS_PER_SESSION=20` (§14.A.7) vs Odoo `efn_qr_max_submissions` default 10 (docs/08 §13.1b). Runbook memakai "yang lebih ketat berlaku".
2. **Istilah berbeda docs/08 §13.1b vs kontrak §14:**
   - `efn_qr_kitchen_mode confirm|auto` vs `efn_qr_confirm_mode waiter|auto`;
   - path `/internal/v1/fnb/table-sessions` vs `/v1/pos/fnb/table-sessions`;
   - body buka sesi `{config_ref, table_ref}` vs `{config_id, table_id, order_uuid}` (L-70 membuat `order_uuid` saat buka sesi kurang relevan).

   Lead perlu menetapkan satu versi untuk builder.
3. **Retensi Odoo `efn.pos.table.session` belum ada di spesifikasi.** Usulan: 400 h, atau ikut `pos.order` (Q-P29). Catatan baris QR ikut `pos.order` 10 th → usulan dikosongkan setelah 90 h.
4. **Bukti pemberitahuan hangus ikut terhapus saat anonimisasi** (§14.D.7 menghapus `loyalty_expiry_notice`). Bila DPO menginginkan bukti, perlu catatan tanpa identitas (±0,5 hari BFF, Q-P26).
5. **Template WA hangus membawa jumlah poin ke Meta.** Ini lebih banyak dari template online yang tanpa parameter. Perlu persetujuan DPO (Q-P26). Opsi tanpa jumlah poin biayanya Rp0.
6. **Belum ada alert** untuk sesi QR `open` > 4 jam, kiriman `submitting` macet, dan earn QR tertunda (usulan devsecops/data-ai, runbook C4).

## Terverifikasi / belum terverifikasi
- **Terverifikasi (pasal.id, 19 Okt 2026, teks — bukan sumber resmi JDIH):**
  - UU 27/2022 Pasal 16 ayat (2) huruf g;
  - Pasal 43 ayat (1) huruf a–d;
  - Pasal 44 ayat (1) huruf a–d + Penjelasan "memusnahkan";
  - Pasal 10 ayat (1) (sebagian kutipan + parafrase).
- **Dibaca dari repo:**
  - kontrak §11–§14;
  - docs/08 §13;
  - migrasi `09-platform-g20.sql`, `10-platform-g21.sql`;
  - `online.repository.ts` (retensi kode bayar);
  - README `efn_pii`, `efn_pii_hr`, `efn_region`, `efn_online_pos_counter`, `efn_pos_refund_origin` (retensi persetujuan), `efn_role` (pengecualian SoD), `efn_online_order` (partner pembeli);
  - `ci.yml` (job `odoo-test-pii` ada di `needs` gerbang akhir);
  - review r14 (SEC-154..163);
  - log git (#62–#64 di riwayat branch).
- **Belum terverifikasi:**
  - migrasi `11-platform-g22.sql`, `efn_pos_bff_order`, `efn_online_kds_bridge`, frontend `/meja` belum ada → isi G22 di inventaris/runbook mengikuti kontrak;
  - nama tombol POS;
  - kategori template WA (Meta);
  - `auto_delete` `mail.mail` struk POS 19;
  - status W-26/W-19 tambahan G21 dan W-14b/teks 18-36 G20 (tidak dicek terpisah);
  - penerapan pasal ke anonimisasi = penafsiran architect;
  - architect tidak menjalankan kode/test;
  - Mermaid tidak dirender.

# G22 r15 — sinkron dokumen setelah security review r15 & QA G22 (2026-10-19)
Branch `chore/wave-22`, tanpa commit, **hanya `docs/**`**. Sumber: `docs/reviews/SECURITY_REVIEW_2026-10-19_r15.md`, `docs/agent-reports/efn-qa-engineer.md` §G22, kontrak bersama r15 K1–K3 dari lead.

## Ringkasan (untuk manajemen)
- Isi kontrak QR meja kini sama dengan yang dibangun: 10 kiriman per sesi, satu event tutup membawa semua order meja, dan modifier menu ikut tampil.
- **Poin dari pesanan QR tetap mati.** Poin ini baru boleh dinyalakan setelah tiga celah r15 (refund, dobel dengan kasir, meja menyerap tamu berikutnya) terbukti tertutup, dan klien memutuskan cara staf mengoreksi klaim poin yang salah (G23). Pemesanan QR tanpa poin boleh dipilot.
- **Penukaran poin online tanpa verifikasi ulang** diterima sementara (poin online juga mati di produksi). Verifikasi ulang dibangun di G23.
- Peran Sales Manager Online yang juga bisa memvalidasi stok **diterima**, dengan syarat diperiksa di review akses bulanan.

## File diubah
| File | Perubahan |
|---|---|
| `docs/04_SERVICE_CONTRACTS.md` | **v9.1**. Catatan sinkron di awal §14. Diagram A.1 (tanpa `order_uuid` di buka sesi, order baru per kiriman, `orders[]`). A.2 keterikatan & token di memori (L-78). A.3: `lines` hasil submit + `attribute_value_ids`/`attributes_text`; daftar kiriman + `attributes_text`; filter catatan identik SEC-168 (NFKC → buang zero-width/bidi → aturan angka). A.4: body buka sesi tanpa `order_uuid`, kunci `fnb_open:<cfg>:<tbl>:<n>`, `print_no` monoton `n ≤ 99999999` (SEC-167), `reissue` diteruskan, syarat jam sesi ≥ TTL. Event: body 16 KB, `submission.order_uuid` (K1), `close.orders[]` ≤ 100 `{order_uuid, eligible_amount neto, company_code, pos_member_attached}` (K2), aturan lekat order SEC-166, rate limit sesudah guard (SEC-173), BFF tidak earn QR untuk `pos_member_attached:true` (SEC-165). Tabel K3 refund vs close (SEC-164). A.5: modul `efn_pos_bff_order`, grup `efn_pos_bff_order.group_qr_bff`, `order_uuid` di #14/#15, validasi order baru per kiriman. A.7: batas 10, baris event. A.8: `fnb_submission.order_uuid`. C.1 no. 4: step-up diralat menjadi rencana G23. D.4/D.8: `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT` default `true`. E.6: nama modul. **L-82** (a–d) setelah L-81 |
| `docs/01_REVIEW_RISK_GAP_ANALYSIS.md` | Pembaruan r15 untuk R-B12, R-B13, R-T20, R-L11, R-S16. **R-B14 baru** (pencurian/penguncian poin QR: SEC-164/165/166/169; 9, OFF sampai syarat), **R-B15 baru** (redeem online tanpa step-up SEC-170; 6, residu diterima sementara) |
| `docs/runbooks/fnb-qr-orders.md` | Header v9.1 + risiko R-B14/R-T20. Batas kiriman 10 (catatan "20 vs 10" dihapus). **§1.1** syarat jam sesi Odoo ≥ TTL token. B2 status poin QR mati + `pos_member_attached`. C1 refund neto K3. C2 posisi default poin. **Bagian D baru**: 7 syarat aktivasi `FNB_QR_POINTS_ENABLED`. Batas jujur |
| `docs/runbooks/loyalty-inactivity-expiry.md` | Baris kontrol SEC-171, syarat consent di langkah aktivasi 1, batas/usulan metrik `skipped_no_consent` |
| `docs/agent-reports/efn-architect.md` | Bagian ini |

**Butir 5 (indeks review):** `docs/reviews/` **tidak punya** README/daftar, dan tidak ada indeks lain yang mendaftar r11–r14 (`docs/decisions/README.md` hanya mengutip r7/r8/r10 di bagian go/no-go). Tidak ada yang perlu ditautkan; saya tidak membuat indeks baru karena di luar brief. Usulan: `docs/reviews/README.md` berisi tabel r1–r15 (tanggal, gelombang, verdict, pemblokir aktivasi), ±30 menit.

## Keputusan yang butuh tanda tangan klien / pemilik
- **SEC-169 (G23):** mekanisme koreksi klaim poin QR. Pilihan: reset dari POS, OTP kasir mengalahkan klaim, batas perangkat/waktu, atau indikator. Pemilik: Ops F&B + Finance. Biaya ±2–4 hari Odoo+BFF+FE (perkiraan architect). Ini syarat runbook D no. 5.
- **SEC-170 (G23):** apakah klien ingin step-up untuk redeem online besar, dan berapa ambangnya. Biaya ±1–2 hari BFF + 0,5 hari FE. Wajib dijawab sebelum poin online aktif.
- **SEC-171:** nilai `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT` saat aktivasi. Ini keputusan Legal/DPO (Q-P25, D-22-16) dan bergantung pada kategori Meta.
- **SEC-172:** penerimaan L-82 c perlu diketahui Internal Audit (review akses bulanan). Opsi aturan SoD report-only ±0,5 hari.

## Temuan saat menulis (untuk lead)
1. Batas `orders[]` kini **100** dan body **16 KB** (kontrak bersama). QA mencatat kode BFF saat ini ≤ 50 / 8 KB, jadi builder harus menyamakannya di kedua sisi.
2. K3 menetapkan **hasil** (total earn = neto, tanpa hilang/dobel). Mekanismenya bisa Odoo menahan reverse sampai close `done`, BFF `409 earn_pending` berbasis `order_uuid` K1, atau reverse tertunda di BFF. Mekanisme ini belum saya lihat di kode dan perlu dicatat di README `src/fnb` / `efn_pos_bff_order`. Catatan: order pelayan/jangkar (bukan kiriman QR) tidak dikenal BFF lewat K1 sebelum close. Bila mekanisme hanya mengandalkan K1, jendela kedua SEC-164 tetap terbuka untuk order jenis itu.
3. DPIA §13 / PDP inventory belum menyebut filter catatan SEC-168 sebagai "upaya terbaik" dan saklar consent SEC-171. Usulan pembaruan kecil gelombang berikutnya (tidak saya ubah, di luar brief).
4. R-B14 merujuk runbook §D. Skor pasca-mitigasi 4 adalah usulan architect.

## Terverifikasi / belum terverifikasi
- **Dibaca:** review r15 (utuh); QA G22 §G22.1–G22.5; `docs/04` §14 (A–E, §14.7); `docs/01` §4.1–4.4; kedua runbook; daftar `docs/reviews/`; grep `services/api-gateway/src` untuk `earn_pending`/`pos_member_attached`/`ONLINE_REDEEM_STEPUP` (README orders mengonfirmasi step-up belum dibangun).
- **Belum terverifikasi:** implementasi K1–K3, SEC-164..168, SEC-171, SEC-173 (paralel, tidak dibaca); `print_no` monoton di Odoo; batas 100/16 KB di kode; penerusan env `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT` di compose/deploy-validate; semua estimasi = perkiraan architect; Mermaid tidak dirender; tidak ada test yang dijalankan.
