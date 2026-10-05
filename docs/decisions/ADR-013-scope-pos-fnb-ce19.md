# ADR-013: Scope POS F&B Dibatasi pada Kemampuan Odoo CE 19 + OCA; KDS dan Booking Dibangun/Ditunda

- **Status:** Proposed
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** PO + Operasional F&B; IT Lead (estimasi custom)
- **Referensi review:** D-13, R-T03, R-T04, R-B01
- **Bukti:** `docs/06_ODOO_CE19_MODULE_AUDIT.md` (verifikasi sumber primer GitHub 2026-10-05)

> **Ringkasan manajemen.** BRD dan Cetak Biru meminta KDS (layar dapur), ODS, Kiosk, Booking meja, dan Waitlist. Hasil audit kode Odoo Community 19: **Kiosk, QR order, meja, split bill, printer dapur, loyalty/gift card tersedia gratis.** **Layar dapur (KDS) dan booking meja tidak ada di versi Community** (keduanya fitur Enterprise berbayar) dan juga belum ada di OCA. Pilihan: pakai printer dapur dulu (gratis, siap) dan bangun KDS sendiri sekitar 4–6 minggu, atau beli Enterprise (melanggar prinsip zero license).

## Konteks
- BRD & Cetak Biru: *KDS, ODS, Kiosk, Booking, Waitlist*. Charter tidak merinci.
- Prinsip proyek: CE 19 tanpa Enterprise (CLAUDE.md).
- Temuan audit (sumber: `raw.githubusercontent.com/odoo/odoo/19.0/addons/...`):
  - `pos_restaurant` (LGPL-3) — bill printing, bill splitting, *"Kitchen Order Printing: allows you to print orders updates to kitchen or bar printers"*.
  - `pos_self_order` (LGPL-3) — `self_ordering_mode`: `consultation` (QR menu), `mobile` (QR + ordering), `kiosk`. Opsi *pay after "Each Order"* berlabel *"(require Odoo Enterprise)"*.
  - `pos_preparation_display` → **404 di CE 19.0** (KDS = Enterprise).
  - `pos_appointment` → **404 di CE 19.0**, padahal `point_of_sale` memiliki toggle `module_pos_appointment = "Online Booking"` → indikasi Enterprise (repo Enterprise privat, tidak dapat diverifikasi langsung).
  - OCA/pos branch 19.0 hanya berisi 4 modul (`pos_edit_order_line`, `pos_partner_birthdate`, `pos_printing_qztray`, `pos_session_sequence`); branch 18.0 berisi 28 modul dan **tidak ada** KDS.

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu (asumsi) |
|---|---|---|---|
| A. **CE 19: printer dapur + kiosk/QR CE; KDS custom `efn_pos_kds` di fase 2; booking & waitlist ditunda (fase 6) atau via form sederhana** | Zero license; fitur inti siap; KDS sesuai kebutuhan stasiun EFN | KDS custom harus dirawat tiap upgrade Odoo | KDS: 4–6 minggu 1 engineer (OWL + bus/longpolling); printer dapur: konfigurasi |
| B. Odoo Enterprise untuk outlet F&B | KDS & booking matang | Lisensi per user/bulan (melanggar "zero license"); Enterprise di DB yang sama berlaku untuk seluruh DB | Biaya lisensi berulang (minta penawaran Odoo partner) |
| C. KDS open source pihak ketiga terintegrasi via API | Tidak menulis UI dari nol | Belum ada kandidat terverifikasi untuk Odoo 19; integrasi tetap dibutuhkan | Riset 1–2 minggu, belum pasti |
| D. Printer dapur saja (tanpa KDS) selamanya | Paling murah | Tidak ada status "siap saji"/bump, metrik waktu dapur | Rp0 + biaya printer |

## Keputusan
Opsi A. Fase 2 go-live outlet F&B memakai **kitchen order printer** (CE) dengan routing per kategori/stasiun; `efn_pos_kds` dibangun paralel dan menggantikan printer setelah stabil. Kiosk & QR order memakai `pos_self_order` CE (mode *pay after meal*, karena *each order* butuh Enterprise). Booking meja & waitlist **dikeluarkan dari MVP**.

## Konsekuensi
- **Positif:** tidak ada lisensi; MVP F&B tidak terblokir KDS.
- **Negatif:** utang perawatan `efn_pos_kds`; ekspektasi BRD (booking/waitlist) mundur.
- **Dampak dokumen:** BRD & Cetak Biru (daftar fitur POS F&B) diberi label fase.

## Risiko terkait
R-T03 (fitur Enterprise, skor 16), R-T04 (OCA 19 belum matang — lihat ADR-021), R-B01.

## Pertanyaan terbuka untuk klien
- **Q-18** jenis layanan per outlet; **Q-19** stasiun dapur & routing; **Q-20** table management; **Q-23** pembayaran di meja; **Q-24** printer & backup saat KDS mati; **Q-56** perangkat POS.
- Apakah booking meja wajib di tahun pertama?

## Kapan ditinjau ulang
Setelah PoC POS restaurant di sandbox CE 19 (minggu 1–2 fase 0), dan setiap rilis mayor Odoo/OCA.
