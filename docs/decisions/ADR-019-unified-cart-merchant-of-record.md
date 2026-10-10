# ADR-019: Unified Cart Lintas PT — Satu Pembayaran, Order & Dokumen Pajak per PT, PT Holding sebagai Agen Penagih

- **Status:** Proposed — **wajib opini konsultan pajak & legal sebelum Accepted**
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** Finance + Tax + Legal (struktur), PO (UX checkout)
- **Referensi review:** §3.3, R-T10, R-L03, R-L04, R-O05, R-S09
- **Terkait:** ADR-001, ADR-016, ADR-020, ADR-026 (gateway-agnostic; split settlement multi-PT = kriteria pemilihan vendor), ADR-027 (pajak configurable), ADR-034 (kanal online BFF + json2), `docs/02 §3.4`, `docs/04 §10`

> **Ringkasan manajemen.** Pelanggan bisa mengisi satu keranjang berisi barang supermarket (PT Retail, kena PPN) dan makanan (PT F&B, kena PB1), lalu membayar **sekali**. Di belakang layar sistem memecahnya menjadi pesanan, struk, dan pajak **per PT**. Uang diterima satu akun gateway milik PT Holding yang bertindak sebagai **agen penagih** dan meneruskan dana ke masing-masing PT; PT Holding **tidak** menjadi penjual. Struktur ini harus dikonfirmasi konsultan pajak karena menyangkut PPN, PB1, dan faktur.

## Konteks
- FSD-1.1 (Documentation Suite): unified cart satu checkout untuk item dari beberapa PT, tanpa pembahasan pajak/settlement.
- `docs/01 §3.3`: pajak berbeda (PB1 daerah vs PPN pusat), settlement gateway, refund parsial, poin lintas-brand sebagai liabilitas.
- `docs/02 §3.4` menyebut *"Merchant of Record: PT Holding"* — istilah ini perlu dipertajam: pemilik akun merchant gateway ≠ penjual barang (seller of record).

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu (asumsi) |
|---|---|---|---|
| A. **Holding = pemegang akun gateway & agen penagih; tiap PT tetap penjual (SO, invoice, pajak per PT); dana diteruskan via settlement inter-company harian/periodik** | Satu pembayaran; satu kontrak gateway; pajak tetap di PT penjual; refund per PT terlacak | Dana transit di Holding = utang ke PT (perlu perjanjian agen & rekonsiliasi harian); opini pajak diperlukan | +2–3 minggu (split order, jurnal titipan, rekonsiliasi); biaya konsultan pajak |
| B. Holding sebagai **seller of record** (membeli dari PT lalu menjual ke pelanggan) | Satu struk bagi pelanggan | Holding menjadi wajib pungut PPN & berpotensi subjek PB1 restoran → perubahan izin & pajak signifikan; transfer pricing (R-L04) | Biaya kepatuhan & legal tinggi; tidak direkomendasikan |
| C. **Split settlement di gateway** (sub-akun per PT, satu pembayaran) | Dana langsung ke rekening PT penjual; paling bersih secara pajak | Ketersediaan & syarat fitur split untuk multi-PT **belum terverifikasi**; KYB tiap PT di gateway | Tergantung vendor; validasi kontrak |
| D. Dua tagihan terpisah di UI (bayar 2×) | Paling sederhana secara hukum | UX buruk; konversi turun | Rp0 tambahan |

## Keputusan
Opsi **A** sebagai default; **C** dipilih bila gateway (ADR-016) terbukti mendukung split ke sub-akun per PT dengan refund parsial per sub-akun — keduanya mempertahankan prinsip *penjual = PT pemilik barang*. Desain wajib:
1. BFF memecah cart per `company_code` (`RTL`, `FNB`) & fulfilment → satu SO Odoo per PT, pajak per company (`l10n_id`, PB1 dikonfigurasi di PT F&B).
2. Satu payment intent; webhook (HMAC + idempotency + replay window) → saga per SO (confirm, reserve, dispatch 3PL); kompensasi per SO.
3. Refund parsial selalu dikaitkan ke SO/PT asal; tidak boleh mengembalikan dana dari porsi PT lain.
4. Jurnal: penerimaan di Holding dicatat sebagai **dana titipan/utang ke PT**, bukan pendapatan; settlement ke PT dengan netting fee gateway proporsional.

## Konsekuensi
- **Positif:** UX satu pembayaran; kepatuhan pajak per PT terjaga.
- **Negatif:** rekonsiliasi harian Holding ↔ PT; perjanjian agen antar-entitas; fase 3 bergantung opini pajak.
- **Dampak dokumen:** FSD-1.1 dan `docs/02 §3.4` (istilah "Merchant of Record" → "pemegang akun gateway / agen penagih").

## Risiko terkait
R-L03 (PB1 vs PPN, skor 16), R-L04 (transfer pricing), R-T10 (saga, skor 16), R-O05 (refund), R-S09 (webhook).

## Pertanyaan terbuka untuk klien
- **Q-1** apakah ada PT holding yang dapat menjadi pemegang akun gateway?
- **Q-2** CoA seragam; **Q-4** settlement lintas PT; **Q-22** PB1 include/exclude; **Q-39/Q-40** ongkir & slot per PT.
- Konsultan pajak: perlakuan dana titipan agen penagih dan dokumen yang diterima pelanggan.

## Kapan ditinjau ulang
Setelah opini pajak/legal diterima (sebelum fase 3), dan setelah 100 order online pertama (rekonsiliasi 100% cocok).

## Catatan revisi 2026-10-09 (Gelombang 17 — Fase 3) — status tetap Proposed
Sumber: `docs/08_ODOO_FIT_GAP.md` §8.5 (F-70, temuan #40–#41), kontrak `docs/04 §10`, ADR-034. **Bukan keputusan klien**; opini pajak & legal tetap syarat Accepted.

Opsi A dipecah menjadi dua cara pencatatan; keduanya mempertahankan *penjual = PT pemilik barang* dan tidak mengubah kontrak BFF:

| Varian | Cara | Status | Biaya / waktu |
|---|---|---|---|
| **A1 — default pilot** | Di PT penjual: faktur saat paid + `account.payment` pada jurnal bank "Titipan Online – Holding" (akun **Piutang dana titipan Holding**) yang dikonfigurasi di kanal Odoo (`journal_titipan_id`). Di Holding: jurnal umum harian dari laporan settlement BFF (impor CSV standar) *Bank/Piutang gateway / Utang titipan PT*. Settlement Holding → PT manual. **Tidak ada kode akun di kode** (CoA Q-2 PLACEHOLDER). | Bisa sekarang (konfigurasi + E-28) | termasuk E-28; pembukuan Holding manual harian (beban Finance ±15–30 menit/hari, asumsi) |
| **A2 — kandidat PoC** | Modul CE 19 **`account_payment_interco`** (standar Odoo, bukan pihak ketiga; tidak auto-install): saat faktur PT dibayar oleh `payment.transaction`/`account.payment` milik Holding, modul membuat jurnal kliring otomatis di kedua company (temuan #41). | **PoC setelah opini pajak**; belum terverifikasi lintas company (`check_company`, faktur tetap milik PT) | PoC ±3–5 hari engineer Odoo; bila lolos, hanya metode privat `_efn_online_register_payment` E-28 yang berubah |
| **C — bila gateway mendukung split** | Dana langsung ke sub-akun per PT; `journal_titipan_id` diganti jurnal bank PT; baris Holding hilang. | Menunggu ADR-016 (kriteria vendor) | konfigurasi + adapter gateway; kontrak sudah menyediakan `portions` per PT di payment intent (`docs/04 §10.7`) |

Penegasan desain wajib (melengkapi bagian Keputusan): (5) payment intent online milik Holding (`company_code=EFN`) **membawa porsi per PT/SO**; Σ porsi = nominal intent; (6) refund parsial selalu menyebut bagian (`part_id`/SO) dan ≤ porsi bagian itu (R-O05); (7) jalur F&B online (SO + jembatan KDS vs `pos.order` lewat E-27) diputuskan bersama ADR-032 lewat **Q-ON2** — kontrak kanal tetap sama; (8) MDR, bukti potong, dan periode settlement = **Q-ON9** (+ Q-4) untuk konsultan pajak.

## Catatan 2026-10-10 — keputusan pemilik proyek atas Q-ON9 (status tetap Proposed)
Sumber: sesi tanya-jawab pemilik proyek 10 Okt 2026 (`docs/decisions/README.md` bagian "Keputusan pemilik proyek 10 Okt 2026 (sesi tanya-jawab)").

| Hal | Keputusan | Dampak |
|---|---|---|
| MDR (biaya gateway) | **Ditanggung PT penjual**, proporsional terhadap porsi PT dalam satu pembayaran | Laporan settlement BFF wajib memuat MDR per porsi; jurnal di PT penjual = beban MDR. Rumus alokasi pembulatan (sisa Rupiah ke porsi terbesar) ditetapkan Finance — **belum** di kontrak §10.7 |
| Periode settlement Holding → PT | **Harian** | Sesuai default A1 |
| Pencatatan Holding | **A1 manual/impor CSV** (default pilot). **A2** (`account_payment_interco`) baru dipertimbangkan **setelah opini konsultan pajak** | Tidak ada perubahan kode; beban Finance ±15–30 menit/hari (asumsi) |
| Faktur online (Q-ON8) | Dibuat **saat bayar** (sesuai yang dibangun) | Tidak ada perubahan |
| Settlement poin lintas brand (Q-4) | **Bulanan** via jurnal inter-company (PT penerbit menanggung nilai redeem di PT lain; netting Finance pusat) | Masuk paket W-15 (`efn_intercompany` + laporan BFF) |

**Status tetap Proposed:** opini konsultan pajak & legal atas struktur agen penagih (dan bukti potong) masih menjadi syarat Accepted. Redeem poin online sebagai **alat bayar** (P-8 / keputusan Finance 10 Okt) menambah satu "porsi" pembayaran non-tunai yang tidak melewati gateway — perlakuannya di laporan settlement ikut dikonfirmasi konsultan pajak.
