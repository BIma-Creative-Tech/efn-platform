# ADR-010: AI di Luar Jalur Transaksi; Inter-company PO/SO Bersifat Deterministik

- **Status:** Proposed
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** PO + IT Lead
- **Referensi review:** D-10, R-T06, R-B09
- **Terkait:** ADR-001 (multi-company), ADR-008, ADR-014

> **Ringkasan manajemen.** AI dipakai untuk membaca foto harga dan menjawab pertanyaan data manajemen, tetapi **tidak** pernah memutuskan transaksi (membuat PO/SO, memotong stok, menagih). Pembuatan PO/SO antar-PT dilakukan oleh aturan otomatis Odoo yang hasilnya selalu sama dan bisa diaudit. Bila server AI mati, kasir dan checkout tetap berjalan.

## Konteks
- TSD §4.2: *AI tidak berjalan di sistem live.*
- FSD §1 langkah 2: *"[Auto-Action] Agentic AI mendeteksi permintaan ini lintas-PT. AI men-generate Purchase Order (PO) di Odoo F&B dan Sales Order (SO) di Odoo Supermarket."*
- PO/SO lintas PT adalah dokumen akuntansi & pajak (R-L03, R-L04); keluaran LLM tidak deterministik dan tidak dapat diaudit seperti aturan.

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu |
|---|---|---|---|
| A. **Automasi deterministik**: OCA `purchase_sale_inter_company` (PO PT-A → SO PT-B otomatis) + replenishment rule | Terverifikasi tersedia di OCA 19.0 (lihat `docs/06`); audit trail; tanpa GPU | Aturan harus dikonfigurasi per pasangan company | Konfigurasi 1–2 minggu |
| B. Agentic AI membuat PO/SO (FSD) | "Pintar" | Halusinasi → dokumen pajak salah; tidak bisa dijelaskan ke auditor; AI menjadi SPOF transaksi | Butuh GPU + guardrail + review manusia — kehilangan manfaat |
| C. AI sebagai **saran** (forecast kebutuhan CK) yang dikonfirmasi manusia | Nilai tambah analitik | Bukan jalur transaksi | Fase 5+, opsional |

## Keputusan
Opsi A untuk transaksi; opsi C diperbolehkan sebagai fitur analitik non-kritis di fase lanjut. Aturan arsitektur: tidak ada service transaksional (POS, checkout, inter-company, payment) yang memanggil Ollama/OCR secara sinkron.

## Konsekuensi
- **Positif:** auditability, ketersediaan transaksi tidak bergantung GPU/LLM.
- **Negatif:** "Agentic AI" dalam pitch bisnis menyempit ke OCR & Text-to-SQL.
- **Dampak dokumen:** FSD §1 langkah 2 (`docs/05` K-01), Blueprint (narasi AI).

## Risiko terkait
R-T06 (Text-to-SQL salah), R-S05 (injection), R-L04 (transfer pricing harus konsisten — aturan, bukan AI), R-B09.

## Pertanyaan terbuka untuk klien
- **Q-3** metode transfer pricing; **Q-35** produksi CK berbasis forecast atau pesanan; **Q-48** pengguna Text-to-SQL (**dijawab 1 Nov 2026**: staf internal di Odoo per PT, data operasional tanpa GL — ADR-038).

## Kapan ditinjau ulang
Tidak dijadwalkan. Hanya bila ada regulasi/teknologi yang memungkinkan AI keputusan dapat diaudit setara aturan.
