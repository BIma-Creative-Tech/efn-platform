# ADR-033: Akun Perangkat KDS — User Internal Tersandbox (Pilot) vs User *Share*

- **Status:** Proposed — **opsi A untuk pilot disetujui pemilik proyek 10 Okt 2026** (Q-KD1 = ya; Q-KD3 = supervisor outlet memegang 2FA dan boleh login ulang; Q-KD4 = rotasi token 7 hari tetap). **Q-KD2 = akan ada portal** (vendor, pelanggan, franchisee, karyawan) → keputusan A→B dan dampak `auth_totp.policy = all_required` dievaluasi di **ADR-035**. Perangkat KDS = monitor + **mini PC** (bukan tablet) dengan klien WireGuard (P-18). ADR tetap Proposed sampai keputusan A→B sebelum outlet ke-2.
- **Tanggal:** 2026-10-09
- **Pemutus (klien):** IT + Security (+ Ops F&B untuk SOP perangkat)
- **Referensi review:** SEC-74, SEC-77 (`docs/reviews/SECURITY_REVIEW_2026-10-09-r8.md` §SEC-74 "Opsi lebih kuat … sebaiknya lewat ADR kecil"); R-S03, R-S10, R-S12, R-L01, R-T04
- **Terkait:** ADR-013 (KDS custom), ADR-023 (WireGuard + 2FA), ADR-031 (tanpa modul pihak ketiga), ADR-032 (QR order), `services/odoo/addons/efn_pos_kds/README.md`, `docs/08` §7.3 "Akses perangkat"

> **Ringkasan manajemen.** Tiap layar dapur (KDS) login ke Odoo dengan akun sendiri. Saat ini akun itu adalah "user internal" yang dikurung ketat oleh kode buatan EFN: hanya bisa melihat tiket stasiunnya, tidak bisa membaca data pelanggan, produk, harga, atau karyawan (sudah diuji). Alternatif yang lebih kuat adalah akun bertipe "portal/eksternal", tetapi butuh ±3–5 hari kerja dan uji ulang, dan memaksa verifikasi 2 langkah untuk semua akun portal. Rekomendasi: **pakai akun internal tersandbox untuk pilot (sudah jadi, Rp0 tambahan)**, evaluasi akun portal sebelum KDS diperluas ke outlet lain.

## Konteks
- Spesifikasi `docs/08` §7.3: *"satu user internal per layar ('KDS <outlet> <stasiun>'), grup baru `efn_pos_kds.group_kds_device` … 2FA wajib (`auth_totp` `employee_required`) … perangkat = peer WireGuard"*.
- Review r8 SEC-74: user internal mewarisi seluruh ACL baca `base.group_user` (produk + `standard_price`, `hr.employee.public`, Discuss), constraint grup berbasis denylist dan bisa dilewati lewat form Grup. Skenario: tablet dapur dicuri/dipakai orang dapur → baca produk, harga pokok, direktori karyawan via `/web/dataset/call_kw` (dalam WireGuard).
- **Sudah diperbaiki di Gelombang 15** (`efn_pos_kds` 19.0.1.1.0, laporan odoo-engineer G15 §2; belum di-review ulang security):
  - allowlist grup (grup efektif ⊆ implied `base.group_user` ∪ {`group_kds_device`}), ditegakkan di `res.users` **dan** `res.groups` create/write;
  - sandbox model lewat override `ir.rule._compute_domain` untuk user perangkat: hanya `efn.kds.*`, diri sendiri, partner diri/company, dan model teknis (`ir.`/`bus.`/`base.`/…); model lain domain FALSE, termasuk modul yang dipasang belakangan (default deny);
  - uji runtime: sebagai user perangkat, iterasi semua model ber-ACL baca (12 modul EFN + ±105 modul CE) → model di luar allowlist 0 record, tidak ada nama pelanggan lewat many2one → lolos;
  - rotasi token kanal otomatis saat perangkat dicabut/diubah + cron 7 hari (SEC-77).
- Laporan odoo-engineer G15 §8: *"Akun perangkat sebagai user share (opsi lebih kuat r8) butuh ADR kecil."*

## Opsi yang dipertimbangkan

| Opsi | Ringkas | Kelebihan | Kekurangan | Biaya & waktu |
|---|---|---|---|---|
| **A. User internal + `group_kds_device` + allowlist grup + sandbox `ir.rule` (status G15)** | Desain sekarang | Sudah jadi dan diuji (337 unit, tour 20/20 termasuk layar KDS login perangkat di bawah sandbox); layar KDS memakai bundle `point_of_sale.base_app` yang memang ditujukan untuk user internal; 2FA lewat kebijakan `employee_required` yang sudah dipakai staf | Bergantung pada override hook ORM inti (`ir.rule._compute_domain`) → risiko saat upgrade minor 19.x (R-T04); allowlist `DEVICE_MODELS` harus ditambah bila layar butuh model baru; tetap ber-tipe internal sehingga backend `/odoo` terbuka (menu disembunyikan, data tersandbox) | **Rp0 tambahan.** Uji regresi tiap upgrade Odoo: ±0,5 hari |
| B. User *share* (portal, `share=True`) + grup KDS saja | Akun non-internal | Lapisan ganda: Odoo inti memperlakukan user share sebagai eksternal (tanpa ACL `base.group_user`), sandbox menjadi pertahanan kedua | 2FA untuk akun portal hanya bisa dipaksa dengan `auth_totp.policy = all_required` (terverifikasi ada di `auth_totp_mail` 19.0: `employee_required` / `all_required`) → **berlaku untuk semua user portal** (dampak bergantung Q-W3); bundle POS/bus/RPC layar KDS untuk user share **belum terverifikasi**; ACL & rule perlu ditulis ulang untuk grup portal; review security ulang | **±3–5 hari engineer** (±Rp3–6 jt indikatif) + uji ulang tour + review security |
| C. Tanpa akun: layar diautentikasi token stasiun di URL (`auth='public'`) | Paling sederhana di perangkat | — | **Ditolak**: route pra-autentikasi baru di Odoo (bertentangan dengan gerbang `ROUTE_ALLOWLIST` SEC-79 dan prinsip ADR-023), token di URL masuk log (SEC-73), tanpa 2FA | 1–2 hari, risiko tinggi |
| D. Akun bersama antar-stasiun / memakai akun kasir | Hemat akun | — | **Ditolak**: akun kasir ber-grup POS User (ditolak constraint), akun bersama menghapus jejak per perangkat dan rotasi token per stasiun | Rp0, tidak dapat diterima |

## Keputusan (rekomendasi)
**Opsi A untuk pilot satu outlet F&B**, dengan syarat GO r8 no. 3 (satu user per layar, grup efektif hanya `base.group_user` + `group_kds_device` diverifikasi via query, 2FA aktif, tablet = peer WireGuard sendiri, browser mode kiosk, login tidak dipakai untuk hal lain) dan SOP pergantian perangkat (rotasi token otomatis + cabut peer WG). **Opsi B dievaluasi lewat spike ±1 hari** (apakah layar KDS + bus berjalan untuk user share di 19.0) sebelum rollout ke outlet kedua; bila spike lolos dan Q-KD2 menunjukkan dampak `all_required` dapat diterima, B menjadi target. Alasan: A sudah memenuhi minimisasi data (uji runtime lolos) tanpa biaya tambahan; B menambah lapisan pertahanan tetapi biayanya (2FA portal, uji ulang) baru sepadan saat jumlah perangkat bertambah.

## Konsekuensi
- **Positif:** pilot tidak tertunda; data pelanggan, produk/harga pokok, dan karyawan tidak terbaca dari tablet (terbukti uji runtime pada set modul CI).
- **Negatif / utang yang diterima:**
  - override `ir.rule._compute_domain` = titik rapuh saat upgrade; wajib masuk checklist upgrade Odoo (uji `test_device_runtime_surface_no_customer_name` harus hijau);
  - akun perangkat dihitung sebagai user internal (CE tanpa lisensi per user → Rp0, tetapi menambah daftar user yang harus ditinjau IT);
  - upgrade DB lama (`-u` dari 19.0.1.0.0, pre-migrate noupdate) **belum** diuji di DB nyata (G15 §8).
- **Dampak ke dokumen lain:** `docs/08` §7.3 "Akses perangkat" dibaca bersama ADR ini (sandbox + allowlist menggantikan "record rule membatasi"); `PDP_DATA_INVENTORY.md` v5 §2.2b mencatat akun perangkat.

## Risiko terkait
R-S03 (akses Odoo), R-S10 (insider, tablet dipakai bergantian), R-S12 (perangkat tak terkelola), R-L01 (minimisasi data di dapur), R-T04 (upgrade 19.x vs override ORM).

## Pertanyaan terbuka untuk klien
| ID | Pertanyaan | Default bila tidak dijawab | Memblokir |
|---|---|---|---|
| Q-KD1 | Terima opsi A (user internal tersandbox) untuk pilot? | A | Pilot KDS (syarat GO no. 3) |
| Q-KD2 | Apakah akan ada user portal Odoo (supplier/mitra, Q-W3)? Bila ya, apakah 2FA wajib untuk mereka dapat diterima (efek samping opsi B)? | Tidak ada user portal | Opsi B |
| Q-KD3 | Tablet dapur = perangkat bersama dengan sesi persisten: siapa pemegang kode 2FA dan siapa yang boleh login ulang (supervisor dapur/IT)? | Supervisor outlet memegang 2FA; IT mencabut peer WG saat hilang | SOP perangkat pilot |
| Q-KD4 | Rotasi token kanal berkala: 7 hari (default) cukup? Layar memuat ulang beberapa detik saat rotasi (cron 20:00 UTC = 03:00 WIB) | 7 hari, 03:00 WIB | — |

## Kapan ditinjau ulang
- Sebelum KDS dipasang di outlet kedua (keputusan A → B).
- Setiap upgrade Odoo (minor 19.x atau ke 20) — bila uji sandbox gagal, B dipercepat.
- Bila layar KDS butuh model baru di luar allowlist.
- Bila review keamanan r9 menemukan celah pada sandbox.

## Belum terverifikasi
- Layar KDS (`point_of_sale.base_app`, bus, RPC `efn_kds_*`) berjalan untuk user share di 19.0.
- Perilaku `auth_totp` trusted device di tablet bersama (sesi persisten).
- Upgrade DB lama ke `efn_pos_kds` 19.0.1.1.0.
- Estimasi Rupiah (turunan tarif estimasi ADR-031).
