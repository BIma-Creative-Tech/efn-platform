# ADR-025: Model Operasi Tim Agent — Lead Agent, Spesialis, Functional-First

- **Status:** Proposed — **instruksi klien 5 Okt 2026** (sudah berlaku di `CLAUDE.md`)
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** Konsultan/arsitek Athera (pemilik proses pengembangan) + PO
- **Referensi review:** D-15 (OCA/CE dulu), R-B09 (kustomisasi berlebihan), R-T12 (kualitas kode)
- **Terkait:** `docs/09_AGENT_OPERATING_MODEL.md` (detail operasional), `.claude/agents/*.md`, ADR-015

> **Ringkasan manajemen.** Pekerjaan dikerjakan tim agent AI dengan satu koordinator (`efn-lead`) yang menjadi satu-satunya lawan bicara user. Setiap kebutuhan Odoo dianalisis dulu oleh konsultan fungsional (konfigurasi standar sebelum kode baru), kontrak antar-service ditetapkan sebelum kode ditulis, dan setiap gelombang ditutup dengan QA dan security review. Tujuannya: lebih sedikit kode custom, lebih sedikit ketidakcocokan antar-service, dan satu laporan yang jelas untuk user.

## Konteks
Gelombang 1–2 Fase 0 dijalankan dengan agent spesialis paralel yang dikoordinasi sesi utama. Masalah yang muncul: ketidakcocokan integrasi (nama env, prefix path, argumen queue — laporan QA), modul Odoo custom ditulis sebelum fit-gap standar (risiko R-B09), dan temuan keamanan baru terlihat di akhir. Klien menginstruksikan: tim dipimpin `efn-lead`, dan `efn-odoo-functional` mengonfigurasi standar Odoo sebelum modul baru.

## Opsi yang dipertimbangkan
| Opsi | Kelebihan | Kekurangan |
|---|---|---|
| **A. Lead + spesialis + functional-first + kontrak dulu** | Satu pintu ke user; kontrak mencegah drift; kode custom minimum | Lead menjadi bottleneck; lebih banyak langkah per fitur |
| B. Agent spesialis dipanggil langsung oleh user | Cepat untuk tugas kecil | User harus merekonsiliasi; drift kontrak |
| C. Satu agent generalis | Sederhana | Konteks terlalu besar, kualitas turun, tanpa review independen |

## Keputusan
Opsi **A**: (1) user hanya berinteraksi dengan `efn-lead`; (2) **kontrak dulu** — `docs/04_SERVICE_CONTRACTS.md` dimiliki lead dan diperbarui sebelum agent paralel menulis kode yang saling memanggil; (3) **functional-first** — tidak ada tugas ke `efn-odoo-engineer` tanpa fit-gap `efn-odoo-functional` (`docs/08_ODOO_FIT_GAP.md`) yang menyatakan gap; (4) **batas file** eksplisit per paket kerja; (5) setiap gelombang ditutup QA lalu security review (read-only); temuan High/Critical masuk gelombang perbaikan berikutnya; (6) setiap agent menulis laporan di `docs/agent-reports/<agent>.md`. Detail di `docs/09_AGENT_OPERATING_MODEL.md`.

## Konsekuensi
- **Positif:** jejak keputusan jelas (ADR, kontrak, laporan), kode custom Odoo terkendali, keamanan dicek tiap gelombang.
- **Negatif:** latensi per fitur lebih panjang; ketergantungan pada kualitas brief lead; tidak ada runtime di mesin penulis → verifikasi tetap di CI (`CLAUDE.md`).
- **Dampak dokumen:** `CLAUDE.md` (sudah), `.claude/agents/efn-lead.md`, `efn-odoo-functional.md` (sudah).

## Risiko terkait
R-B09, R-T12, R-T03.

## Pertanyaan terbuka untuk klien
- Apakah persetujuan ADR/kontrak oleh manusia (Athera/PO) wajib sebelum lead memulai gelombang yang mengubah kontrak, atau cukup pemberitahuan?

## Kapan ditinjau ulang
Akhir Fase 0, atau saat tim developer manusia bergabung (peran agent → asisten reviewer/pair).
