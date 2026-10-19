# efn_l10n_id_coretax_rounding (19.0.1.0.0) — E-40 PPN rumus Coretax, selisih Rp1 per faktur

> **Ringkasan manajemen.** Odoo standar menghitung PPN 11 % × DPP per dokumen, sedangkan Coretax menghitung
> 12 % × DPP 11/12 **per baris**, sehingga PPN di e-Faktur bisa beda ±Rp1 dari jurnal. Modul ini membukukan selisih
> itu otomatis ke akun *Selisih Pembulatan Pajak* milik PT masing-masing, satu jurnal kecil per faktur. Total tagihan
> dan piutang pelanggan tidak berubah; laporan PPN Keluaran sama dengan isi XML Coretax.

- **Spesifikasi:** `docs/08_ODOO_FIT_GAP.md` §11.2 (E-40, butir 1–6); kontrak `docs/04_SERVICE_CONTRACTS.md` §13.4;
  keputusan 18-5 (`docs/decisions/README.md`). ADR-027 (pajak dari Odoo, tanpa tarif di kode), ADR-031 (tanpa modul
  pihak ketiga).
- **Risiko dimitigasi:** selisih PPN Keluaran jurnal vs SPT/Coretax (Q-F18-2, docs/08 §9.1 "terbukti 8.122 vs
  8.123"); R-L09 tidak terkait (PB1).
- **Depends:** `l10n_id_efaktur_coretax` (CE), `efn_core`. Tidak ada model baru (hanya field di `res.company`,
  `res.config.settings`, `account.move`) → tidak ada ACL/record rule baru; dokumen yang dibuat = `account.move` standar
  (record rule multi-company CE).

## Cara kerja
1. **Pengaturan per PT** (*Pengaturan → Akuntansi → Pembulatan PPN Coretax (EFN)*, grup *Accounting / Manager*):
   `efn_tax_rounding_account_id` (beban; wajib), `efn_tax_rounding_gain_account_id` (opsional, kosong = akun yang
   sama), `efn_tax_rounding_journal_id` (Miscellaneous). **PLACEHOLDER**: modul tidak membuat akun; Finance membuat
   akun baru per PT (bukan akun selisih kas `l10n_id_99900001/2`). Constraint: akun milik company (atau induknya),
   tipe beban (lebih: pendapatan/beban), jurnal `general` company yang sama. Setiap perubahan → log
   `EFN_CORETAX_ROUNDING_CONFIG companies=… uid=… fields=…` (tanpa nilai).
2. **Hitung:** untuk setiap baris faktur yang ber-pajak PPN (grup `default_tax_group`, `l10n_id_tax_group_non_luxury_goods`,
   `l10n_id_tax_group_luxury_goods`; baris dengan pajak grup 0 %/bebas dilewati) modul **memanggil**
   `account.move.line._l10n_id_coretax_build_invoice_line_vals` dan menjumlahkan `VAT`. Odoo = Σ baris pajak grup yang
   sama. `d = Coretax − Odoo`.
3. **Jurnal penyesuaian** (`move_type=entry`, jurnal PT, tanggal = tanggal akuntansi faktur, `ref EFN-CTX-RND <faktur>`):
   `d > 0` Dr Selisih Pembulatan Pajak / Cr akun PPN Keluaran; `d < 0` Dr PPN Keluaran / Cr akun selisih (lebih). Baris
   PPN membawa `tax_tag_ids` baris pajak faktur (baris PPN terbesar) → laporan pajak berbasis tag = Σ VAT XML (CE 19:
   tanda tag ditentukan formula laporan, bukan per baris). Tautan dua arah: `efn_coretax_adjustment_move_id` /
   `efn_coretax_source_move_id`, nilai `efn_coretax_rounding_diff` (tab *Other Info → Electronic Tax*).
4. **Pemicu:** (a) setelah posting `out_invoice` yang `l10n_id_coretax_efaktur_available` — *best effort*: galat
   (akun belum diatur, pengaman, periode terkunci) dicatat `EFN_CORETAX_ROUNDING … not adjusted at posting` dan
   posting faktur tetap jalan (alur kasir/online tidak terblokir); (b) sebelum XML dibuat
   (`l10n_id_efaktur_coretax.document._generate_xml`: *Download e-Faktur*, *Regenerate*, download dokumen tanpa
   lampiran) — **ketat**: galat → `UserError`, XML tidak dibuat.
5. **Idempoten:** selisih sama → tidak ada jurnal baru. Berubah (mis. kode transaksi diganti sebelum ekspor) → jurnal
   lama di-reverse (tanggal jurnal lama) + jurnal baru. Faktur *Reset to Draft* → jurnal penyesuaian di-reverse; posting
   ulang → jurnal baru.
6. **Pengaman:** `|d| ≤ jumlah baris × Rp1`; di luar itu `UserError` "bukan pembulatan" (contoh: kode 01 dengan PPN
   11 % → selisih Rp1.000).
7. **Periode terkunci:** tanggal ≤ `fiscalyear_lock_date` / `tax_lock_date` / `sale_lock_date` / `hard_lock_date`
   → `UserError` (tidak pernah memindah jurnal ke tanggal lain). Pengecualian kunci per user (`account.lock_exception`)
   tidak dipakai (konservatif).

## Di luar cakupan / batasan
- `out_refund` (nota kredit Coretax) **[B]** — perilaku `l10n_id_efaktur_coretax` untuk retur belum diverifikasi (§11.2
  butir 5). Kode transaksi **07/08** (PPN tidak dipungut/dibebaskan) dan baris pajak 0 %/bebas tidak dijembatani
  (VAT XML bukan PPN terutang di jurnal). Faktur bukan IDR dilewati.
- Faktur dengan baris subtotal negatif (diskon/promo `sale_loyalty`) tidak bisa diekspor Coretax 19.0
  (`ValidationError` builder) → dilewati di posting; ekspor tetap ditolak Odoo sendiri.
- B2C POS/online **digunggung** (18-7) tidak diekspor per baris → tidak terdampak (asumsi, konfirmasi konsultan
  pajak di register Finance + Tax).
- Audit `efn_audit` untuk field `res.company` belum ada aturan `efn.audit.rule` (milik `efn_setup_audit`, di luar paket
  G21) — saat ini hanya log `EFN_CORETAX_ROUNDING_CONFIG`.
- Jurnal penyesuaian yang di-draft/dibatalkan manual dianggap tidak ada → sinkron berikut membuat jurnal baru.
- Modul perlu ditambahkan ke `EFN_TEST_MODULES` CI (lead/devsecops).

## Uji manual (DB dev, data uji)
1. Install modul; PT Grand Lucky: buat akun *Selisih Pembulatan Pajak* (beban) → Pengaturan → isi akun + jurnal Misc.
2. Pelanggan PKP (NPWP 16 digit, negara Indonesia). Faktur PPN **include**: 12.990×3, 7.500, 1.499×7, 25.000 → Post.
   Total 81.963; tab *Electronic Tax* menampilkan jurnal penyesuaian Rp1 (Dr Selisih / Cr PPN Keluaran).
3. *Download e-Faktur* dua kali / *Regenerate* → tetap satu jurnal penyesuaian; Σ VAT XML = 8.123 = saldo PPN Keluaran.
4. Faktur PPN exclude 100.000 → tidak ada jurnal. Kode 01 + PPN 11 % → posting jalan, *Download* ditolak (pengaman).

## Test
`tests/test_coretax_rounding.py` (tag `efn`, `AccountTestInvoicingCommon.setup_country('id')`): include 8.122 vs 8.123
→ Rp1 (akun, tag, piutang tetap, saldo PPN = Σ VAT), exclude tanpa selisih, kode 01 (OtherTaxBase = TaxBase), pengaman,
idempoten ekspor + regenerate + ganti kode transaksi, selisih negatif (akun lebih; compute di-mock), multi-PT akun
masing-masing, akun belum diatur + periode pajak terkunci, reset ke draft, constraint pengaturan. Kelas di-skip (dengan
alasan) bila DB uji tidak bisa memakai IDR Rp1.

## Referensi (ADR-031)
Tidak ada modul OCA/Cybrosys 19.0 yang diverifikasi untuk kasus ini (`OCA/l10n-indonesia` belum diperiksa). Ide dari CE:
strategi akun selisih `account.cash.rounding`. Source CE 19.0 dibaca (WebFetch 2026-10-19):
`l10n_id_efaktur_coretax/models/{account_move.py, account_move_line.py, efaktur_document.py, res_partner.py}`,
`views/account_move.xml`, `tests/test_l10n_id_efaktur_coretax.py`; `account/models/{account_account_tag.py,
account_tax.py, res_currency.py}`; `account/views/res_config_settings_views.xml`. Tidak ada kode yang disalin.
