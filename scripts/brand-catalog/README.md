# Katalog brand: scrape situs resmi → produk DEMO di Odoo

Tujuan: mengisi katalog dev/demo Super-App per brand dengan nama, deskripsi, kategori, dan foto produk dari situs resmi
brand. Gambar tidak di-hotlink (CSP `img-src 'self'`); semua masuk ke Odoo (`image_1920`) lalu tampil lewat BFF
(`image_sha`). Odoo tetap sumber kebenaran katalog.

**Izin:** Erajaya (EFN) memegang hak jual dan pemakaian brand di Indonesia (konfirmasi tim Erajaya, 20 Okt 2026).
Data hasil scrape **tidak di-commit** (`data/brand-catalog/` ada di `.gitignore`). Simpan selama dipakai untuk dev/demo
saja, lalu hapus.

## Sumber & hasil (20 Okt 2026)

| Brand | Sumber | Hasil | PT / kanal |
|---|---|---|---|
| Paris Baguette | `parisbaguette.co.id` WordPress REST `/wp-json/wp/v2/product` + `/media` | 101 produk, 101 gambar, 13 kategori | `PB` |
| Bacha Coffee | `bachacoffee.com` endpoint JSON listing situs (`/api/BachaProductApi/GetBachaProductsByCategory`), `Crawl-delay: 5` dihormati | 533 SKU (loose/packed coffee, accessories, gourmet, gift set), 533 gambar | `BCR` (butik). Menu kafe `BCF` tidak dipublikasikan |
| GrandLucky | `grandlucky.co.id` | **Tidak ada katalog publik** (WordPress, REST API dimatikan). Profil brand + outlet (`brand_info.json`) | `GL`: isi lewat CSV master data |
| GrandLucky (23 Okt 2026) | PDF promo publik `doc.grandlucky.co.id/catalog_blast/Christmas_2025.pdf` (tautan "latest catalog" di beranda): 11 halaman gambar tanpa teks → render pdfjs → OCR tesseract eng+ind → potong foto per kartu | **88 produk DEMO, 85 foto, 15 kategori**; 43 harga katalog terbaca (2 OCR sepakat) → `reference_price_idr` | `GL` |

## Pakai

```bash
python scripts/brand-catalog/scrape_parisbaguette.py
python scripts/brand-catalog/scrape_bacha.py            # jeda 5 s per request; gambar yang sudah ada dilewati
python scripts/brand-catalog/scrape_grandlucky.py       # info brand saja
python scripts/brand-catalog/scrape_grandlucky.py --from-csv ekspor_gl.csv --images-dir foto_gl/   # katalog GL internal

# Import (upsert idempoten per default_code <PREFIX>-<source_id>; gambar di-resize maks 1024 px di host)
python scripts/brand-catalog/import_odoo.py --brand parisbaguette --dry-run          # validasi lalu rollback
python scripts/brand-catalog/import_odoo.py --brand all --db efn_odoo               # container efn-odoo-1 (odoo.conf)
python scripts/brand-catalog/import_odoo.py --brand bacha --target run --db efn_catalog   # DB uji terpisah
```

Opsi: `--limit N`, `--no-images`, `--verify-only`, `--archive-missing`, `--keep-payload`. Ringkasan import tersimpan di
`data/brand-catalog/<brand>/last_import.json`. Template CSV GL: `templates/grandlucky_products_template.csv`.

Prasyarat: Python host + Pillow; Docker. DB target harus sudah memasang modul **G22** (`efn_online_order` ≥ 19.0.1.7.0,
`efn_setup_online` ≥ 19.0.1.6.0). DB lama gagal dengan `column res_partner.efn_online_invoice_buyer does not exist`.

## Harga

Situs brand **tidak** memuat harga IDR (Bacha hanya USD global, disimpan di `extra.reference_price_usd_global` sebagai
referensi dan tidak diimpor). GL: kolom CSV opsional `reference_price_idr` (harga promo katalog PDF, hasil OCR) dipakai
sebagai harga DEMO bila `price_idr` kosong; produk tetap bertag **DEMO harga** karena harga promo musiman bukan master
harga. Harga OCR yang dua bacaannya berbeda dibiarkan kosong (placeholder kategori GL: Non Food 45.000, Food 30.000,
Fresh 40.000). Import mengisi **harga placeholder per kategori** (`BRANDS[...]["prices"]` di
`import_odoo.py`). Harga yang sudah diubah manual di Odoo dipertahankan (`price_kept`). **Wajib diganti dari master
harga resmi sebelum demo ke pihak luar.**

## Batas / belum

- Produk masuk Odoo + kanal online PT. Kanal `PB`/`BCR`/`BCF` di demo **nonaktif**, jadi produk baru tampil di Super-App
  setelah kanal diaktifkan dan diberi API key (runbook `odoo-online-api-key.md` §1b) dan `brand_code` kanal diisi.
- Bacha: satu SKU per varian (Whole/Ground) = satu produk. Ketersediaan butik Indonesia belum difilter (situs global).
- GL dari PDF promo: nama hasil OCR + koreksi manual salah baca yang jelas; ukuran/varian kadang terpotong. Buah/daging
  dijual per 100 gr/kg di katalog, di demo = 1 unit. Pipeline OCR (render, tesseract, potong foto, review) ada di
  scratch, bukan di repo: hasil akhirnya CSV template biasa (`--from-csv`). Ganti dengan ekspor master data GL saat ada.
- Logo dan font berlisensi tidak diambil; minta file resmi dari tim brand.
