# ADR-014: Hosting AI di Jaringan Privat tanpa Egress Internet (bukan "Air-Gapped")

- **Status:** Proposed
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** IT Lead + Legal/DPO (klaim privasi ke publik)
- **Referensi review:** D-14, R-L01, R-S05
- **Terkait:** ADR-008, ADR-010

> **Ringkasan manajemen.** Dokumen menyebut server AI "100% offline/air-gapped", padahal aplikasi harus mengirim foto ke server AI — artinya server itu terhubung ke jaringan. Istilah yang benar: **server AI di jaringan internal, tidak bisa mengakses internet, dan tidak bisa diakses dari internet**. Data pelanggan tetap tidak keluar ke pihak ketiga. Istilah ini penting agar klaim ke pelanggan/regulator akurat.

## Konteks
- TSD: *AI "100% offline/air-gapped"*.
- FSD §2: *"Node.js meneruskan foto ke Ollama Vision API lokal"* → ada koneksi jaringan.
- Air-gapped = terisolasi fisik total; tidak kompatibel dengan dipanggil worker/BFF.

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu |
|---|---|---|---|
| A. **Private network, no egress**: Ollama & OCR worker hanya di Docker network internal/WireGuard; firewall deny outbound; model di-pull lewat proses terkontrol (mirror/artefak) | Data tidak keluar; tetap bisa dipanggil via RabbitMQ | Update model butuh prosedur manual | Rp0; ~2 hari konfigurasi firewall & prosedur |
| B. Air-gapped sungguhan | Isolasi maksimal | Tidak bisa melayani Price Hunter/Text-to-SQL secara online | Fitur tidak berfungsi |
| C. API AI cloud (OpenAI/Gemini) | Akurasi & tanpa GPU | Data pelanggan keluar negeri (PDP: transfer lintas batas), biaya per call, bertentangan dengan arahan self-hosted | Biaya per token; kajian PDP |

## Keputusan
Opsi A. Container AI tanpa published port, network Docker `internal: true` atau aturan egress deny; komunikasi hanya via RabbitMQ (worker) dan endpoint internal Text-to-SQL di belakang autentikasi. Model weights diambil dari artefak terverifikasi (checksum) saat deploy.

## Konsekuensi
- **Positif:** klaim privasi akurat dan dapat dibuktikan (aturan firewall).
- **Negatif:** proses update model manual & terdokumentasi.
- **Dampak dokumen:** TSD "air-gapped" → "jaringan privat tanpa egress internet".

## Risiko terkait
R-L01 (PDP), R-S05 (Text-to-SQL injection — no-egress membatasi eksfiltrasi).

## Pertanyaan terbuka untuk klien
- **Q-50** lokasi hosting; apakah ada klaim "AI offline" yang sudah dikomunikasikan ke publik/investor yang perlu dikoreksi?

## Kapan ditinjau ulang
Bila ada kebutuhan memakai model cloud (perlu kajian PDP transfer data lintas negara).
