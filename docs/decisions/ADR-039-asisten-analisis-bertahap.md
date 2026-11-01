# ADR-039: Asisten Analisis "Analisis bertahap" (AI agent pertama, K25-01)

- **Status:** Accepted — pemilik proyek (K25-01 "AI agent"; keputusan 1 Nov 2026), diterapkan G39. Ringkasan via 9router
  di **production ditahan** sampai DPO memutuskan transfer lintas negara (Q-T7, aliran P8).
- **Tanggal:** 2026-11-01 (Gelombang 39)
- **Terkait:** ADR-010 (AI di luar jalur transaksi), ADR-037 (gateway 9router), ADR-038 (Tanya Data), `docs/04` §24,
  R-S05 (injection), R-T06 (jawaban AI salah dipakai manajemen).

> **Ringkasan manajemen.** Di menu *Tanya Data* kini ada mode **Analisis bertahap**. AI memecah pertanyaan menjadi
> beberapa query (mis. angka utama, perbandingan periode lalu, rincian per outlet), menjalankannya hanya pada data PT
> pengguna, lalu menulis ringkasan tren dan anomali. Setiap langkah tetap menampilkan tabel dan SQL-nya sehingga angka
> bisa dicek. AI tidak mengubah apa pun di Odoo. Untuk menulis ringkasan, angka agregat dikirim ke AI; lewat 9router
> ini baru diizinkan di dev, production menunggu keputusan DPO.

## Konteks
- K25-01 menyebut tiga pemakai gateway LLM: OCR (G37), Text-to-SQL (G38), dan AI agent. Pemilik memilih asisten
  analisis di Odoo (bukan agent yang menyiapkan tindakan/PO, bukan chatbot member) untuk pengguna Tanya Data.
- G38 sengaja tidak mengirim hasil ke LLM. Ringkasan tren memerlukan angka → pemilik mengizinkan agregat tanpa PII ke
  9router di dev (1 Nov 2026).

## Keputusan
1. Agent = **alur tetap tiga tahap** (rencana ≤ 3 query → eksekusi → ringkasan), bukan loop alat bebas: jumlah panggilan
   LLM terbatas (2), waktu terbatas (≤ 200 s Odoo), mudah diaudit. Endpoint baru `POST /v1/analyze` di service
   `text2sql` (memakai ulang guard, semantic layer, row policy per PT).
2. Rencana memakai LLM SQL yang sama dengan G38 (tanpa data). Setiap SQL lewat `guard_sql` + row policy bertanda tangan.
3. Ringkasan memakai `TEXT2SQL_AGENT_SUMMARY_PROVIDER` (default `none`). 9router butuh izin **terpisah**
   `TEXT2SQL_AGENT_RESULTS_CROSS_BORDER_OK=true` (P8) dan ditolak di production (Q-T7). Hasil dibatasi 30 baris per
   langkah dan teks bebas diredaksi.
4. Agent hanya membaca (`efn_t2s`); tidak ada alat tulis/aksi Odoo. Output AI disimpan sebagai teks ter-escape dengan
   label "dibuat AI", tabel + SQL per langkah selalu tampil (R-T06).
5. Kill switch `TEXT2SQL_AGENT_ENABLED` default false.

## Konsekuensi
- **Positif:** K25-01 lengkap untuk tiga pemakai; isolasi PT tetap di database; biaya LLM per analisis terprediksi.
- **Negatif:** angka bisnis keluar negeri bila ringkasan 9router dipakai (P8); ringkasan bisa salah tafsir → tabel
  tetap acuan.
- **Belum:** agent yang menyiapkan tindakan (draf PO/aktivitas) dan ringkasan terjadwal — perlu keputusan terpisah.
