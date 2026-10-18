# ADR-027: Pajak Configurable — PB1 per Kota/Outlet & Harga Include/Exclude Pajak, Tanpa Tarif di Kode

- **Status:** Proposed — prinsip yang **diputuskan klien 6 Okt 2026**: *PB1 per kota dan harga menu include/exclude pajak dibuat configurable*. Mekanisme di bawah adalah rekomendasi; **tarif & aturan tampilan ditetapkan konsultan pajak**.
- **Tanggal:** 2026-10-06
- **Pemutus (klien):** Finance + Tax (konsultan pajak) + Ops F&B
- **Referensi review:** R-L03 (PB1 vs PPN, skor 16), Q-22 (PB1 include/exclude), Q-2 (CoA)
- **Terkait:** ADR-001 (satu DB multi-company), ADR-013 (POS F&B), ADR-019 (unified cart, pajak per PT), `docs/08` F-02, F-07, F-08

> **Ringkasan manajemen.** Tarif pajak restoran (PB1) berbeda per kota, dan ada outlet yang ingin harga menu sudah termasuk pajak sementara yang lain belum. Semua itu diatur di **pengaturan standar Odoo** (pajak per perusahaan, *fiscal position* per outlet, tanda "harga termasuk pajak" per pajak) — **tidak ada angka tarif yang ditulis di program**. Saat Perda kota berubah, Finance cukup mengubah pengaturan, tanpa developer. Super-App menampilkan pajak yang dihitung Odoo, bukan angka tetap. Biaya: konfigurasi ±2–3 hari functional + ±2 hari BFF; Rp0 lisensi; konsultan pajak menetapkan tarif & aturan.

## Konteks
- `docs/08` F-02 & temuan §2.2 (terverifikasi 19.0): **PB1 tidak ada di `l10n_id`**; PPN default `tax_ST4` (12% × 11/12). Demo `efn_setup_company` memakai PB1 10% sebagai PLACEHOLDER.
- PB1 adalah pajak daerah; sejak UU 1/2022 (HKPD) disebut PBJT atas makanan/minuman dengan tarif maksimum yang ditetapkan Perda kabupaten/kota (**verifikasi konsultan pajak**). Satu PT F&B bisa punya outlet di beberapa kota → tarif dan kas daerah tujuan setoran berbeda per outlet.
- Kebijakan harga menu (termasuk/belum termasuk pajak) bisa berbeda per brand/outlet (Q-22).
- Odoo CE 19 menyediakan semua kebutuhan secara standar (**terverifikasi** dari source 19.0, 6 Okt 2026):
  - `account.tax.price_include_override` (`tax_included` / `tax_excluded`) — *"Overrides the Company's default on whether the price … includes this tax"*; default company `res.company.account_price_include` (`docs/08` F-02).
  - `account.tax.original_tax_ids` ("Replaces", relasi `account_tax_alternatives`) dan `account.tax.fiscal_position_ids` — di 19.0 pemetaan pajak fiscal position **pindah ke pajak tujuan** (model `account.fiscal.position.tax` tidak ada lagi; `account.fiscal.position.tax_ids` + `tax_map` terhitung).
  - `account.fiscal.position`: `auto_apply`, `country_id`, `state_ids`, `zip_from/zip_to`.
  - `pos.config.default_fiscal_position_id`, `pos.config.fiscal_position_ids` (*"useful for restaurants with onsite and take-away services that imply specific tax rates"*), `pos.config.iface_tax_included` (`subtotal` = tampil tanpa pajak / `total` = tampil dengan pajak).

## Opsi yang dipertimbangkan
| Opsi | Kelebihan | Kekurangan | Biaya |
|---|---|---|---|
| **A. Pajak per kota + fiscal position per outlet (`pos.config.default_fiscal_position_id`)** | Standar CE; katalog produk tetap satu (pajak "PB1 standar" di produk) dan dipetakan ke "PB1 Kota X" per outlet; akun utang pajak per kota → laporan setoran per Pemda; dine-in/take-away via `fiscal_position_ids` | Satu fiscal position per kota (+ varian take-away) per PT; perlu disiplin master data | 2–3 hari functional |
| B. Pajak berbeda di setiap produk per kota | Tanpa fiscal position | Produk diduplikasi per kota atau pajak ganda di produk → salah hitung; tidak skala | Mahal dipelihara |
| C. Satu company per kota | Isolasi penuh | Bertentangan ADR-001 (PT ≠ cabang), CoA & konsolidasi rumit | Sangat mahal |
| D. Hitung pajak di BFF/kode custom | Fleksibel | Tarif di kode = melanggar prinsip; dua sumber kebenaran pajak; audit pajak sulit | Utang teknis |

## Keputusan (rekomendasi)
**Opsi A**, dikonfigurasi oleh efn-odoo-functional di modul `efn_setup_*` (data demo/placeholder saja; nilai produksi diisi Finance lewat UI):
1. **Pajak per company**: PT RTL/FARM memakai PPN `l10n_id`; PT F&B: pajak induk "PB1 (standar)" di produk + satu `account.tax` per kota ("PB1 Kota X", `original_tax_ids` = PB1 standar, `fiscal_position_ids` = posisi kota X), masing-masing dengan **akun utang pajak per kota** (repartition line) dan `tax_group` PB1 agar struk & laporan terkelompok.
2. **Fiscal position per kota/outlet**: `pos.config.default_fiscal_position_id` = posisi kota outlet; varian take-away/dine-in bila konsultan pajak membedakan, lewat `pos.config.fiscal_position_ids`. Untuk Super-App/online, fiscal position ditentukan dari **outlet pemenuhan**, bukan alamat pelanggan (PB1 terutang di lokasi restoran — verifikasi pajak) → `auto_apply` dimatikan untuk posisi PB1.
3. **Include/exclude per pajak**: `price_include_override` di tiap pajak kota; default company `account_price_include` per PT. Tampilan POS `iface_tax_included` per outlet. Konsekuensi yang harus diuji PoC: bila satu produk dijual di outlet "include" dan outlet "exclude" dengan pemetaan pajak include→exclude, Odoo dapat menyesuaikan harga satuan — perilaku pasti di POS 19 **[B] belum terverifikasi**; bila tidak sesuai harapan, pakai pricelist per outlet.
4. **Tidak ada tarif, persentase, atau aturan include/exclude di kode** (`efn_*`, BFF, Super-App, ETL). Data demo bertanda PLACEHOLDER. Usulan ke QA: cek CI yang menolak literal tarif pajak di kode `efn_*`/BFF (heuristik grep) — opsional.
5. **Super-App (BFF)**: harga, label pajak ("termasuk PB1" / "+ PB1 x%"), dan total pajak berasal dari **Odoo** (hasil perhitungan pajak Odoo atas produk + fiscal position outlet), di-cache BFF dengan TTL pendek; checkout membuat SO per PT (ADR-019) dan total akhir = total SO Odoo. Super-App tidak pernah menghitung pajak sendiri. Kontrak endpoint katalog/quote → **usulan ke lead** (`docs/04` tidak diubah di sini).
6. **Perubahan tarif**: Finance membuat pajak baru dengan tanggal berlaku (arsipkan yang lama, jangan ubah tarif pajak yang sudah dipakai transaksi) — dicatat `auditlog` (F-17).

## Peran konsultan pajak (wajib sebelum go-live F&B)
- Menetapkan tarif PBJT/PB1 per kota outlet sesuai Perda dan tanggal berlaku.
- Menentukan aturan tampilan harga (include/exclude) yang sah per brand/kota & apakah struk wajib menampilkan PB1 terpisah.
- Perlakuan take-away/delivery/Super-App, barang kemasan yang dijual PT F&B (PPN vs PB1), service charge (bila ada), dan pajak atas diskon/poin (ADR-020).
- Mapping akun utang pajak per Pemda & format laporan setoran.

## Konsekuensi
- **Positif:** perubahan Perda tanpa rilis kode; satu katalog; laporan setoran per kota; konsisten POS ↔ Super-App.
- **Negatif:** jumlah fiscal position bertambah per kota × varian; kesalahan master data langsung berdampak pajak → checklist & audit; BFF bergantung pada Odoo untuk quote (Odoo down → checkout Super-App tidak bisa menghitung total; perilaku: tolak checkout, bukan menebak pajak).
- **Dampak dokumen:** `docs/08` F-02 (efn-odoo-functional: tambahkan fiscal position per kota & varian take-away); kontrak BFF katalog/quote (`docs/04`, lead).

## Risiko terkait
R-L03, R-O05 (refund harus memakai pajak transaksi asal), R-T10.

## Pertanyaan terbuka untuk klien
- **Q-22** Kebijakan include/exclude per brand/outlet (kini configurable — butuh daftar awal).
- **Q-TX1** Daftar kota outlet saat go-live & 12 bulan + tarif berlaku (konsultan pajak).
- **Q-TX2** Apakah take-away/delivery diperlakukan berbeda?
- **Q-TX3** Service charge: ada? dikenai PB1?

## Yang belum terverifikasi
Nama & tarif PBJT/PB1 per Perda; penyesuaian harga otomatis saat pemetaan include→exclude di POS 19; tampilan label pajak di struk POS per fiscal position (PoC sandbox).

## Kapan ditinjau ulang
Setiap perubahan Perda/UU pajak daerah; saat membuka kota baru; setelah opini konsultan pajak.

## Catatan 2026-10-10 — nilai awal dari pemilik proyek (status tetap Proposed; ⚠ konfirmasi konsultan pajak)
Sumber: sesi tanya-jawab 10 Okt 2026 (P-12, P-16, Q-ON5, Q-8/Q-22; `docs/decisions/README.md`). Semua nilai diisi sebagai **konfigurasi** `efn_setup_*` / UI Finance — tetap tidak ada tarif di kode.

| Parameter | Nilai awal | Catatan |
|---|---|---|
| PB1 (PBJT makan-minum) | **10 %** | Cek tarif Perda kota tiap outlet (Q-TX1) |
| Service charge (SC) | **5 %, dine-in saja** | Takeaway / 3PL / pickup **tanpa SC** |
| SC masuk DPP PB1 | **Ya** | Urutan pajak: SC dihitung dulu, PB1 atas (harga + SC) — perlu `include_base_amount` pada SC (**[B]** dibuktikan di PoC POS 19) |
| Harga menu F&B | **Belum termasuk pajak (exclude)** | `price_include_override = tax_excluded` pada PB1/SC; POS `iface_tax_included = subtotal` |
| Layanan non-dine-in | **Tanpa PB1 dan tanpa SC** (jawaban pemilik proyek: "selain dine-in tidak dikenakan pajak") | Fiscal position per layanan (preset POS takeaway/delivery/pickup → peta PB1→tanpa pajak). **⚠ WAJIB konfirmasi konsultan pajak:** di bawah UU 1/2022 (HKPD), PBJT makanan/minuman umumnya juga terutang atas penjualan dibawa pulang oleh restoran; pengecualian bergantung Perda kota. Risiko **R-L09** |
| Retail online (Q-ON5) | Harga **termasuk pajak** + label | Tidak berubah |
| F&B online (Q-ON5) | Tampilkan **PB1+SC sesuai outlet (exclude)** dengan rincian sebelum bayar | Kontrak §10 perlu rincian pajak per baris/porsi; perhatikan: bila konsultan menyatakan takeaway kena PBJT, tampilan online F&B ikut berubah |
| Konfigurasi per outlet/kota | PB1 & SC **configurable per outlet/kota** (Q-22) | Sesuai keputusan ADR ini |

Dampak SC: SC bukan pajak negara/daerah tetapi pendapatan outlet; di Odoo dimodelkan sebagai `account.tax` jenis persentase dengan akun pendapatan SC (bukan utang pajak) atau sebagai produk biaya otomatis — pilihan diputuskan efn-odoo-functional di fit-gap paket W-14. Pembulatan mengikuti presisi IDR Rp1 global (Q-ON6, paket W-03).
