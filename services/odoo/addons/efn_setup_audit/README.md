# efn_setup_audit

Modul konfigurasi untuk OCA `auditlog` (data + satu patch kompatibilitas, lihat di bawah) (OCA/server-tools 19.0.2.0.0, AGPL-3). Lisensi modul ini **AGPL-3** karena bergantung pada modul AGPL.

- **Risiko dimitigasi:** R-S10 (insider mengubah harga/stok), R-B06 (fraud promo kasir). **ADR:** ADR-015, ADR-020.
- **Fit-gap:** `docs/08_ODOO_FIT_GAP.md` F-18.

## Aturan yang dibuat (state *Confirmed* saat install)
| Model | Create | Write | Delete | Tipe | Catatan |
|---|---|---|---|---|---|
| `product.pricelist` | ✓ | ✓ | ✓ | full | harga member/promo/zona |
| `product.pricelist.item` | ✓ | ✓ | ✓ | full | aturan harga |
| `product.template` | ✓ | ✓ | ✓ | full | `list_price`, `standard_price`, master data |
| `loyalty.program` / `loyalty.reward` / `loyalty.rule` | ✓ | ✓ | ✓ | full | promo mekanik POS; flag `efn_requires_otp` ikut tercatat |
| `stock.quant` | – | ✓ | ✓ | fast | `quantity` & `reserved_quantity` dikecualikan (lihat di bawah) |
| `res.partner` | – | ✓ | ✓ | fast | nomor HP = `member_ref` OTP (SEC-07); volume write perlu diukur di PoC |

`stock.quant`: setiap pergerakan stok mengubah `quantity`; mencatatnya akan membanjiri log (R-T11). Yang diaudit adalah **stock opname manual** (`inventory_quantity`, `inventory_date`, penghapusan). Riwayat mutasi tetap ada di `stock.move.line` (standar).

## Usulan tambahan (belum dibuat, butuh persetujuan)
`account.move` (posting/ubah jurnal), `res.users`/`res.groups` (perubahan hak akses), `pos.payment.method`, `account.tax`. Volume perlu diukur di PoC.

## Keputusan klien yang dibutuhkan
- Retensi log audit (UU PDP & kebutuhan auditor, Q-6/Q-49). Manifest OCA memuat data cron; mekanisme & parameter retensi (autovacuum) di 19.0 **belum terverifikasi** — cek saat PoC.
- Siapa yang boleh melihat log (default OCA: grup *Auditlog Manager*).

## Cara install
```bash
scripts/fetch-oca.sh            # mengambil auditlog ke services/odoo/oca
odoo -d <db> -i efn_setup_audit
```

## Batasan
- Ter-install dan teruji tidak langsung (test efn_* berjalan dengan aturan aktif) di Odoo 19.0-20260926 (Gelombang 7). Nama field/xmlid diverifikasi terhadap source 19.0 (OCA `auditlog/models/auditlog_rule.py`, `stock/models/stock_quant.py`); xmlid `ir.model`/`ir.model.fields` mengikuti konvensi `model_<model>` / `field_<model>__<field>`.
- Constraint OCA: satu aturan per model. Install gagal bila sudah ada aturan manual untuk model yang sama.
- `log_read` sengaja mati (volume). Bila klien meminta jejak *akses* data pribadi (PDP), perlu aturan terpisah untuk `res.partner` dengan evaluasi beban.

## Patch kompatibilitas `patches.py` (Gelombang 7, runtime Odoo 19.0-20260926)
OCA auditlog 19.0.2.0.0 (`ThrowAwayCache`, mode `full`) menukar `transaction.field_dirty`, tetapi ORM 19.0 menyimpan referensi lama di `Environment._field_dirty` (`cached_property`). Akibatnya membuat produk (dan model lain ber-aturan `full`) saat ada record lain yang belum di-flush gagal dengan `AssertionError: Could not find all values of account.account(...) to flush them` — di produksi berarti create produk bisa gagal. `patches.py` membuang memo `_field_dirty` di semua env saat blok masuk/keluar. Tidak mengubah apa yang dicatat. Hapus bila upstream OCA/server-tools sudah memperbaikinya (cek saat menaikkan pin `scripts/fetch-oca.sh`).
