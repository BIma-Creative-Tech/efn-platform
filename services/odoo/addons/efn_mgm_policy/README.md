# efn_mgm_policy — kebijakan MGM per PT (K25-10)

**Tujuan.** Menyimpan parameter program member-get-member (MGM) per PT sebagai data yang diputuskan dengan empat mata,
supaya engine MGM (bagian `loyalty-service`, K25-05) kelak membaca angka dari Odoo, bukan dari kode/env.

**ADR & kontrak.** ADR-009 (struktur 2-tier → diperluas K25-10), K25-10 (L1 & L2 persen, L3+ flat, kedalaman maksimum
wajib), docs/04 §19. Risiko: R-B03 (margin), R-B06 (fraud), R-L02 (penjualan berjenjang), R-O05 (retur → clawback).

## Model `efn.mgm.policy`
| Field | Arti | Batas Odoo |
|---|---|---|
| `level1_pct_bp`, `level2_pct_bp` | % belanja bersih downline (basis poin, 100 bp = 1 %) | 0..2000, L2 ≤ L1 |
| `flat_points_level3` | poin tetap per transaksi, level 3 s.d. kedalaman maksimum | ≥ 0 |
| `max_depth` | kedalaman maksimum | **wajib 2..5** (CHECK DB; tidak ada tak terbatas) |
| `cap_points_per_referrer_month` | cap reward per referrer per bulan | ≥ 1 (wajib) |
| `min_order_idr` | minimal belanja bersih | ≥ 0 |
| `settle_hold_days` | settle setelah masa retur (clawback bila retur) | 1..60 |
| `relation_months` | masa relasi referral | 1..36 |
| `excluded_categ_ids` | kategori produk dikecualikan (turunan ikut) | — |

Tata kelola sama dengan `efn.loyalty.policy`: versi per PT dialokasikan server, pembuat/penyunting = *EFN / Loyalty
Manager*, penyetuju = *Accounting / Manager* company yang sama dan bukan penyunting, read-only setelah disetujui,
tidak bisa dihapus setelah diputuskan, record rule multi-company. Menu: *Poin EFN → Kebijakan MGM*.

Rumus acuan engine (`_efn_reward_points`): L1/L2 = floor(belanja × bp / 10.000 / nilai 1 poin); L3..max = flat;
di luar kedalaman atau di bawah minimal = 0. Cap bulanan & kategori dihitung engine (butuh konteks member/transaksi).

## Belum
- **Engine MGM, relasi referral, publikasi ke BFF/loyalty-service** belum ada: persetujuan tidak menyalakan apa pun.
- Semua angka default = PLACEHOLDER (ADR-009 Proposed). Finance menetapkan angka, Legal menetapkan struktur &
  kedalaman (Permendag 70/2019).
- Basis "belanja bersih" (setelah diskon & poin, tanpa pajak) mengikuti ADR-009 opsi A; definisi final = Finance.

## Test
`tests/test_mgm_policy.py` (tag `efn`): versi & empat mata, penyunting tidak boleh menyetujui, field sistem, batas
nilai (CHECK DB kedalaman 3 kasus + validasi 5 kasus), rumus reward, record rule multi-company.
