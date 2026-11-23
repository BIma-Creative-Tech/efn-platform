# ADR-049: Port kode `odoo-platform` milik pemilik proyek menjadi modul `efn_*` (klarifikasi ADR-031)

- **Status:** Diputuskan pemilik proyek 23 Nov 2026 (opsi "port & adaptasi"). Butir lisensi/kepemilikan di §Konsekuensi
  tetap perlu dikonfirmasi tertulis oleh pemilik proyek sebelum rilis production.
- **Tanggal:** 2026-11-23
- **Pemutus:** Pemilik proyek (instruksi via efn-lead, 23 Nov 2026)
- **Referensi review:** D-15, R-T03, R-L08
- **Terkait:** ADR-031 (tanpa dependency pihak ketiga), ADR-015 (penamaan `efn_*`), `docs/fit-gap/G54_G55_G61_ODOO_SETUP.md`

> **Ringkasan manajemen.** Fitur accounting dan tampilan home yang sudah dibangun pemilik proyek di repo `odoo-platform`
> (dipakai Levi's/EAL) boleh dipindahkan ke EFN dengan cara **menyalin lalu menyesuaikan** kodenya menjadi modul EFN sendiri
> (`efn_*`). EFN tetap tidak memasang modul dari luar: yang dipasang hanya Odoo Community dan modul `efn_*`. Ini mempercepat
> aset tetap, kas kecil, rekonsiliasi bank, PPh, dan home console dibanding menulis ulang dari nol (±2–3x lebih cepat).

## Konteks
- Permintaan pemilik proyek 23 Nov 2026: lengkapi menu Accounting EFN memakai fitur yang sudah dibangun untuk Levi's di
  `E:\Projects\Odoo\platform\addons\` dan tampilan menu `core/custom_home_console`.
- ADR-031 Q-V5 mendefinisikan "pihak ketiga" = **semua addon non-CE**. Modul `odoo-platform` bukan CE, sehingga tanpa ADR ini
  modul tersebut hanya boleh menjadi referensi desain (tulis ulang).
- Fakta repo (dibaca 23 Nov 2026): remote `github.com/sarangrumah/odoo-platform`, manifest `author: Custom Platform`,
  lisensi LGPL-3 per modul, tanpa LICENSE di root. Modul accounting bergantung pada `custom_core`, `custom_pdp_*`,
  `custom_approval_engine`; tidak ada dependency OCA/Enterprise. `custom_home_console` membawa 309 ikon PNG, banyak di
  antaranya ikon aplikasi Odoo Enterprise (lisensi proprietary).

## Opsi yang dipertimbangkan
1. **Referensi saja, tulis ulang** (perlakuan sama dengan OCA/Cybrosys) — paling bersih secara provenance; 2–3x lebih lambat.
2. **Pasang modul `odoo-platform` apa adanya** — melanggar ADR-031 (dependency non-CE + `custom_core`).
3. **Port & adaptasi menjadi `efn_*`** — kode milik pemilik proyek disalin, diganti nama, dependency diganti ke modul EFN.

## Keputusan
**Opsi 3.** Aturan port:
- Modul hasil port bernama `efn_<domain>`, LGPL-3, `company_id` + record rule multi-company, `ir.model.access.csv` lengkap,
  test ikut diport dan harus hijau.
- Dependency diganti: `custom_core` → `efn_core`; `custom_pdp_audit` → `efn_audit`; `custom_pdp_core`/masking → `efn_pii`;
  `custom_approval_engine` → pola 4-eyes yang sudah ada (mis. `efn_loyalty_accounting`) atau dihapus bila tidak dibutuhkan.
- Fitur yang sudah ada di EFN tidak diport ulang (mis. Group Reporting/konsolidasi = `efn_account_reports`; e-Faktur Coretax =
  `l10n_id_efaktur_coretax` CE + `efn_l10n_id_coretax_rounding`).
- README tiap modul memuat **provenance**: modul asal, commit `odoo-platform`, apa yang diubah.
- **Dilarang** ikut tersalin: ikon/aset Odoo Enterprise, branding atau data klien lain (Erajaya, Levi's, EAL, ARKA), chart of
  accounts klien lain (`l10n_erajaya`), konfigurasi tenant.
- ADR-031 tetap berlaku untuk OCA, Cybrosys, Apps Store, dan kode pihak lain mana pun.

## Konsekuensi
- Positif: aset tetap, petty cash, rekonsiliasi bank, PPh, deferred/recurring, home console tersedia lebih cepat; satu rezim
  lisensi LGPL-3 tetap terjaga; tidak ada dependency runtime ke repo lain.
- Negatif / utang teknis: EFN memelihara fork kode tersebut (perbaikan di `odoo-platform` tidak mengalir otomatis); tes asal
  ditulis untuk konteks Levi's sehingga perlu disesuaikan.
- **Kepemilikan:** ADR ini berasumsi kode `odoo-platform` adalah milik pemilik proyek EFN (bukan hasil kerja yang hak ciptanya
  dimiliki klien Erajaya/Levi's/EAL berdasarkan kontrak). Konfirmasi tertulis diperlukan sebelum production (Legal).
- Dampak ke dokumen lain: `docs/06_ODOO_CE19_MODULE_AUDIT.md` dan `docs/08_ODOO_FIT_GAP.md` diperbarui saat modul port masuk.
