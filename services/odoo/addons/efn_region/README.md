# efn_region (19.0.1.0.0) — wilayah kab/kota Kemendagri

ADR-036 Accepted §0.7 (laporan per kota lewat DWH), K-36-5/K-36-11, Q-PII10 default (kab/kota Kemendagri); ADR-031.
Risiko: R-L01 (alamat tetap terenkripsi; hanya kab/kota yang plaintext — kebocoran diterima ADR), R-O07.
Tidak bergantung pada `efn_pii` (boleh dipasang lebih dulu).

## Tujuan
Laporan penjualan per wilayah tanpa membuka alamat/kota/kode pos pelanggan (yang terenkripsi `efn_pii`).
- Model `efn.region`: `code` (Kemendagri `PP.KK`, `^\d{2}\.\d{2}$`, unik), `name`, `kind` (kabupaten/kota),
  `province_code` (compute), `state_id` (opsional), `active`, `company_id` (kosong = referensi bersama), `source`.
  Record rule multi-company: `company_id` kosong atau ∈ company user. ACL: baca `base.group_user`, kelola
  `base.group_system`.
- `res.partner.efn_region_id` (Many2one, **plaintext**, `ondelete=restrict`) di form partner (setelah NPWP).
- Menu: Kontak > Konfigurasi > Wilayah (Kab/Kota).

## Kontrak ETL (milik data-ai, bukan modul ini)
View `efn_etl.region` & `efn_etl.partner_region` dibuat skrip grant `services/etl/sql/odoo-readonly-grants.sql`.
Kolom yang tersedia persis: `efn_region(id, code, name, state_id, active, create_date, write_date)` (+ `kind`,
`province_code`, `company_id`, `source`) dan `res_partner.efn_region_id`. Diuji `test_etl_columns_exist`.

## Data & sumber (Q-PII10)
`data/efn.region.csv` hanya **12 contoh** (DKI, Jabar, Jatim, Banten, Bali) bertanda `source=CONTOH`. Data penuh
(±514 kab/kota) wajib dari sumber resmi Kemendagri (Kepmendagri tentang pemberian & pemutakhiran kode wilayah
administrasi pemerintahan, versi terbaru) — **lisensi/izin penggunaan & versi belum dikonfirmasi** (Finance + DPO).
Kode contoh ditulis dari pengetahuan umum penulis, **verifikasi terhadap Kepmendagri sebelum dipakai**.

Impor (shell Superadmin, CSV UTF-8 ber-header `code,name[,kind]`):
```python
env["efn.region"].sudo()._efn_region_import_csv(open("/tmp/kabkota.csv", "rb").read(), source="Kepmendagri <no/tahun>")
env.cr.commit()
```
Upsert per `code`; baris tidak valid dilewati (dihitung, isi tidak di-log). Alternatif: impor CSV standar Odoo (admin).

## Uji manual
1. Pasang modul → menu Wilayah berisi 12 contoh.
2. Form kontak → pilih "32.73 Kota Bandung"; `psql`: `SELECT efn_region_id FROM res_partner WHERE id=<id>` = id wilayah.
3. Coba kode `3171` → ditolak.

## Batasan / belum
- Tidak ada penurunan otomatis dari kode pos (butuh tabel kode pos → kab/kota, sumber tambahan).
- BFF `region_code` (W-05/W-06) → `efn_region_id`: pemetaan di jalur RPC terkait (bukan modul ini).
- Erasure UU PDP (`efn_privacy`) wajib mengosongkan `efn_region_id` (K-36-4) — di luar cakupan file builder ini.
- `state_id` tidak diisi data contoh (xmlid provinsi base tidak diverifikasi); importer belum memetakan provinsi.

## Referensi (ADR-031)
Tidak ada kode OCA/Cybrosys (OCA `l10n_id_*`/`base_location` tidak dipakai maupun disalin).
