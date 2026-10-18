# ADR-020: Loyalty Ledger Tunggal di PostgreSQL Platform Milik BFF (bukan di Odoo)

- **Status:** Proposed
  - **Catatan 10 Okt 2026 (pemilik proyek; status tetap Proposed):** (1) **redeem poin = alat bayar** (P-8): di POS menjadi *payment method* "Poin EFN", bukan baris diskon per kelompok pajak — DPP tidak berkurang (⚠ konfirmasi konsultan pajak tetap disarankan); (2) **poin online earn + redeem** (redeem online juga alat bayar); (3) settlement poin lintas brand **bulanan** via jurnal inter-company (Q-4); (4) parameter poin tetap placeholder configurable (Q-13). Ledger tetap di PG platform (ADR ini tidak berubah). Paket kerja W-01, W-12, W-15 di `docs/10_RENCANA_GELOMBANG_18_PLUS.md`.
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** Finance (liabilitas poin & settlement antar-PT) + IT Lead + PO
- **Referensi review:** D-05, §3.3 (poin lintas-brand = liabilitas), R-B03, R-B05, R-B06, R-S10, R-L01
- **Terkait:** ADR-006, ADR-009, ADR-017, ADR-019, `docs/04 §3.4, §5`

> **Ringkasan manajemen.** Semua poin pelanggan (belanja, Price Hunter, MGM) dicatat di **satu buku besar poin** di database platform (bukan di Odoo). Kasir dan aplikasi membaca saldo yang sama. Setiap periode, ringkasan poin yang diterbitkan dan ditukar diposting ke akuntansi Odoo per PT sebagai kewajiban, sehingga Finance bisa menagih antar-PT bila poin dari brand A ditukar di brand B.

## Konteks
- `docs/04 §3.4`: *"Ledger tunggal di `platform.loyalty_ledger` (D-05: satu ledger poin). Odoo tidak menyimpan saldo poin sendiri untuk member platform."*
- `docs/02 §3.2` (sebelum dikoreksi) menggambarkan worker OCR memanggil *"POST loyalty earn"* ke Odoo — bertentangan dengan `docs/04`. ADR ini menyelesaikan konflik tersebut (docs/02 diselaraskan).
- Odoo CE 19 memiliki `loyalty` (*"discounts, gift card, eWallets and loyalty programs"*) dan `pos_loyalty` (LGPL-3) — terverifikasi ada.
- Sumber poin berasal dari luar Odoo (Price Hunter, MGM di BFF) maupun dari POS/checkout.

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu |
|---|---|---|---|
| A. **Ledger append-only di `platform.loyalty_ledger` (BFF)**; POS membaca/menulis via `/v1/pos/*`; jurnal periodik ke Odoo per PT | Satu sumber kebenaran untuk semua kanal; idempotency key UNIQUE; clawback = entri negatif; tidak membebani Odoo | Saldo poin tidak tersedia saat POS offline (konsisten dengan ADR-017); integrasi jurnal ke Odoo perlu dibuat | Termasuk scope BFF fase 2; jurnal periodik +1 minggu |
| B. Ledger di Odoo `loyalty`/`pos_loyalty` (CE) | Native di POS, UI siap | Super-App/MGM/Price Hunter harus menulis ke Odoo via RPC untuk tiap event; model kartu loyalty per program/company menyulitkan poin lintas-PT; beban ke Odoo (R-T11) | +2–3 minggu adaptasi; risiko performa |
| C. Dua ledger tersinkron (Odoo + platform) | Masing-masing kanal "native" | Dual-write, selisih saldo, dua angka liabilitas | Tertinggi, rawan bug |

## Keputusan
Opsi A. Aturan:
1. Skema per `docs/04 §5`: `loyalty_ledger(member_ref_hash, delta_points, reason, source, source_ref, idempotency_key UNIQUE, created_at, …)` — saldo = SUM; tidak ada UPDATE/DELETE (append-only; koreksi = entri baru). Tambahan yang diusulkan: kolom `company_code` (PT penerbit/penebus) dan `expires_at` untuk kebijakan kedaluwarsa.
2. Semua penulisan lewat event idempotent (`loyalty.earn.requested`, dst.) atau endpoint BFF; Odoo **tidak** menyimpan saldo member.
3. `loyalty`/`pos_loyalty` CE **tidak** dipakai untuk poin member. Boleh dipakai untuk promo mekanik toko yang bukan liabilitas poin (buy X get Y, diskon) — keputusan PO.
4. Akuntansi: ETL/BFF menghasilkan ringkasan harian/bulanan per PT (diterbitkan, ditukar, kedaluwarsa) → jurnal di Odoo (akun liabilitas poin) + tagihan inter-company untuk penukaran lintas PT.
5. Audit: akses tulis manual ke ledger hanya via endpoint admin dengan 4-eyes & log (R-S10).

## Konsekuensi
- **Positif:** satu angka liabilitas; fraud dapat di-clawback; skala independen dari Odoo.
- **Negatif:** POS bergantung BFF untuk saldo (offline = tidak bisa redeem); perlu modul jurnal periodik.
- **Dampak dokumen:** `docs/02 §3.2` diselaraskan (worker → `loyalty.earn.requested` → BFF); Cetak Biru/Blueprint yang menyebut poin di Odoo tidak berlaku.

## Risiko terkait
R-B03, R-B05, R-B06, R-S10 (insider), R-L01 (data member di-hash), R-L03 (perlakuan pajak poin).

## Pertanyaan terbuka untuk klien
- **Q-4** periode & mekanisme settlement poin lintas PT (netting bulanan?).
- **Q-12** identitas pelanggan tunggal (nomor HP) & pelanggan tanpa HP.
- **Q-13** nilai poin, kedaluwarsa, minimum redeem, transferable.

## Kapan ditinjau ulang
Bila volume ledger > 50 juta baris (pertimbangkan snapshot saldo/materialized balance) atau bila kebijakan akuntansi poin berubah.
