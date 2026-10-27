# efn_agri — Farming Hub & QC gate (G28)

**Tujuan.** BRD §5.4, K25-13: hasil tani masuk lewat collection center, diperiksa QC dengan standar mutu yang
**diatur user** (input manual, tanpa alat ukur otomatis), dan stok tidak bisa dipakai sebelum lolos QC.

## Alur
1. **Komoditas & standar mutu** (Farming Hub > Konfigurasi): parameter ukur (angka min/max atau lulus/gagal, wajib
   lolos atau tidak) + grade A/B/C/Reject dengan batas per parameter dan harga beli; harga dasar (floor price).
2. **Mitra tani**: vendor + kode mitra, kelompok, asal daerah, luas lahan, komoditas, kontrak.
3. **Prediksi panen** → *Buat PO* dengan harga dasar.
4. **Gudang collection center**: tombol *Aktifkan gerbang QC hasil tani* → penerimaan 3 langkah CE
   (Input → Quality Control → Stock); transfer QC jadi gerbang.
5. **Inspeksi QC** pada transfer QC: isi pengukuran → grade otomatis (boleh diubah dengan alasan) → *Selesaikan QC*
   (hanya Inspektur QC):
   - harga baris PO = harga grade (tagihan vendor mengikuti grade);
   - qty reject di-scrap dengan alasan *Reject QC*; parameter wajib gagal / grade Reject = seluruh qty ditolak;
   - lot diberi traceability (kode mitra, daerah, tanggal panen, grade, sertifikat QC).
6. Transfer QC baru bisa divalidasi setelah semua produk komoditas diinspeksi.
7. Cetak **Sertifikat QC** (PDF) dan **Label Traceability** ber-QR dari lot.

Data pribadi petani tetap di `res.partner` (terenkripsi bila `efn_pii` aktif); payload traceability
(`stock.lot._efn_trace_payload`) hanya berisi kode mitra & daerah.

**Referensi desain:** OCA `quality_control_oca` (dibaca, tidak disalin; ADR-031). `quality` Odoo = Enterprise.

**Belum:** PWA QC Intake mobile (sementara pakai web client responsif), portal petani mandiri (ADR-035), halaman
traceability publik di Super-App lewat BFF (payload sudah ada), integrasi alat ukur (sengaja tidak, K25-13).
