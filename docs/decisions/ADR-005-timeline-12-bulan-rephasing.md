# ADR-005: Re-phasing Roadmap menjadi ~12 Bulan dengan Go-Live Bertahap

- **Status:** **Accepted** — pemilik proyek, 2026-10-10 (sesi tanya-jawab via efn-lead, butir 5 "ADR Gelombang 1: Accepted semua"; nama & jabatan penandatangan dilengkapi klien di register keputusan). Sebelumnya: Proposed.
  - **Catatan penerimaan 10 Okt 2026:** pemilik proyek menerima jadwal mundur **2–4 minggu** akibat ADR-031 (Q-V4). Pekerjaan baru dari sesi 10 Okt (presisi IDR global, reservasi stok, QR ponsel E-27, F&B online, delivery, portal vendor, enkripsi kolom PII Odoo, dll.) belum masuk baseline 12 bulan — urutan & estimasi di `docs/10_RENCANA_GELOMBANG_18_PLUS.md`; dampak ke tanggal go-live wajib ditinjau ulang lead + PO.
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** PO + Direksi (sponsor), Finance (anggaran)
- **Referensi review:** D-04, §7, R-B01, R-B08, R-B09

> **Ringkasan manajemen.** Cakupan proyek setara 4–5 produk sekaligus. Jadwal 8–9 bulan di dokumen asli tidak realistis. Usulan: 12 bulan, tetapi nilai bisnis pertama (1 toko retail live) di bulan ke-4, lalu tiap fase menambah satu kemampuan yang dipakai nyata di outlet. Ini mengurangi risiko gagal total dengan menukar waktu, bukan kualitas.

## Konteks
- Charter: **8 bulan, 4 fase**.
- Cetak Biru: **9 bulan, 4 fase** dengan isi fase berbeda dari Charter.
- Cakupan: ERP multi-PT, POS retail + F&B (KDS/kiosk), Super-App web+mobile, brand apps, Farming Hub, Price Hunter, MGM, DWH, AI (R-B01, skor 25).

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Dampak biaya/waktu |
|---|---|---|---|
| A. **12 bulan, 7 fase (0–6) sesuai `docs/01 §7`** | Nilai pertama bulan ke-4; fondasi DevSecOps dulu; tiap fase dipakai produksi | Total lebih lama 3–4 bulan | +3–4 bulan biaya tim (lihat `docs/07_TCO_TEMPLATE.md`) |
| B. Tetap 8–9 bulan, cakupan penuh | Sesuai janji awal | Probabilitas tinggi go-live dengan desain salah (Phantom BoM, tanpa backup); rework | Biaya rework biasanya > biaya 3 bulan tambahan |
| C. 8–9 bulan, cakupan dipangkas (tanpa Farming Hub, AI Analyst, brand apps) | Tanggal tetap | Pilar bisnis tertunda ke proyek berikutnya | Sama dengan A untuk scope yang sama |

## Keputusan
Opsi A sebagai baseline perencanaan; opsi C dapat dipakai bila tanggal tidak bisa digeser (keputusan sponsor). Setiap fase punya kriteria selesai terukur (`docs/01 §7`). Pilot: 1 toko retail (fase 1) dan 1 outlet F&B (fase 2), parallel run 2 minggu.

## Konsekuensi
- **Positif:** risiko terdistribusi; feedback nyata sejak bulan ke-4; change management bertahap (R-B08).
- **Negatif:** sponsor harus menerima tanggal go-live penuh yang lebih lambat; anggaran SDM bertambah.
- **Dampak dokumen:** Charter & Cetak Biru (jadwal) diganti oleh `docs/01 §7` setelah ADR ini diterima.

## Risiko terkait
R-B01 (cakupan), R-B08 (change management), R-B09 (ketergantungan AI coding tanpa reviewer senior — wajib 1 Odoo engineer senior).

## Pertanyaan terbuka untuk klien
- **Q-37 / Q-38**: fitur Super-App & brand apps mana yang MVP.
- **Q-53**: kapasitas tim internal.
- Apakah ada tanggal bisnis yang tidak bisa digeser (event, pembukaan outlet)?

## Kapan ditinjau ulang
Di akhir setiap fase (gate review) dan bila salah satu kriteria selesai fase meleset > 4 minggu.
