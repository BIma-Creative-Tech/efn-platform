# ADR-008: Pipeline OCR Dua Tahap (PaddleOCR/Tesseract → VLM bila Ragu), Asinkron

- **Status:** Proposed
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** IT Lead + PO (KPI) + Finance (GPU opsional)
- **Referensi review:** D-07, §3.5, R-T05, R-B05
- **Terkait:** ADR-012 (RabbitMQ), ADR-010, ADR-014

> **Ringkasan manajemen.** Membaca foto label harga dilakukan di belakang layar: pelanggan langsung mendapat "laporan diterima", lalu notifikasi hasil dalam ≤60 detik. Tahap pertama memakai OCR ringan di CPU (murah); model AI vision besar hanya dipakai untuk foto yang meragukan. Target "<5 detik" di BRD tidak realistis tanpa GPU (≈Rp7–15 jt/bulan tambahan) dan tidak diperlukan pelanggan.

## Konteks
- Blueprint & BRD: engine **Llama 3.2 Vision**; BRD §7.1 KPI *< 5 detik*.
- Arsitektur v2 & TSD: **LLaVA / Tesseract**.
- FSD §2 langkah 2: *"Node.js meneruskan foto ke Ollama Vision API lokal ... (estimasi 2-4 detik)"* — sinkron dalam request HTTP.
- Fakta: VLM 11B di CPU 30–120 detik/gambar; perlu GPU 12–16 GB VRAM untuk <5 detik (`docs/01 §3.5`).

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu (asumsi) |
|---|---|---|---|
| A. **Async 2 tahap: PaddleOCR (CPU) → VLM kecil (Qwen2.5-VL 3B/7B atau Llama 3.2 Vision 11B) hanya bila confidence < 0,8** | Murah, cepat untuk mayoritas foto, tahan beban puncak (antrean) | Dua komponen untuk dirawat; akurasi harus di-tune | CPU saja fase 4; GPU opsional bila >1.000 foto/hari; 3–4 minggu |
| B. VLM saja, async | Satu model, akurasi semantik tinggi | Tanpa GPU: 30–120 dtk/foto → antrean menumpuk | GPU hampir wajib (+Rp7–15 jt/bln sewa, atau capex Rp25–60 jt per kartu kelas 16 GB) |
| C. VLM sinkron (FSD) | Pelanggan menunggu hasil | Request HTTP timeout, tidak tahan lonjakan, AI menjadi jalur kritis API | GPU wajib; risiko outage API |
| D. Tesseract saja | Sangat ringan | Akurasi rendah pada label harga berwarna/miring | Banyak `flagged` → admin manual |

## Keputusan
Opsi A. Alur: BFF validasi & simpan foto → `INSERT` status `queued` → publish `ocr.requested` → `202`. Worker: PaddleOCR → (confidence < 0,8) VLM via Ollama → fuzzy match nama >75% + harga → status `valid|rejected|flagged`. Jika Ollama mati: status `flagged` (review manual), **bukan** error ke pelanggan. KPI diubah: *"hasil validasi diterima pelanggan ≤ 60 detik (p95)"*; *"auto-valid rate > 80%"* di fase 4, naik iteratif.

## Konsekuensi
- **Positif:** biaya GPU bisa ditunda; AI di luar jalur transaksi (ADR-010).
- **Negatif:** model & threshold perlu dataset label harga lokal untuk tuning (±200–500 foto berlabel).
- **Dampak dokumen:** BRD §7.1 KPI, FSD §2 langkah 2–3 (lihat `docs/05` K-02).

## Risiko terkait
R-T05 (latensi), R-B05 (fraud: pHash, geofence, cap harian), R-S06 (upload berbahaya).

## Pertanyaan terbuka untuk klien
- **Q-43** volume & cap; **Q-46** kebijakan banding; berapa estimasi laporan/hari di bulan pertama (menentukan perlu GPU atau tidak)?

## Kapan ditinjau ulang
Bila p95 waktu validasi > 60 detik selama 1 minggu atau volume > 1.000 foto/hari (pertimbangkan GPU).
