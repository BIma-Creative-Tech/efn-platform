---
description: Simpan status sesi efn-lead sebelum /clear agar sesi berikutnya tidak perlu penjelasan ulang
---

Perbarui bagian `## Status sesi` di `docs/agent-reports/efn-lead.md` (buat file/bagian bila belum ada; konvensi laporan agent di CLAUDE.md). Maksimal 25 baris:

- **Selesai sesi ini:** butir + cara verifikasinya (perintah + hasil). Yang belum terverifikasi ditulis jujur.
- **Sedang dikerjakan:** branch, file, dan sisa pekerjaan. Sebut PR bila sudah dibuka.
- **Langkah berikutnya:** berurutan dan konkret (paket kerja G-xx, agent yang akan dipakai).
- **Blocker / pertanyaan untuk user:** bernomor, masing-masing dengan rekomendasi default.
- **Kandidat keputusan:** keputusan user di sesi ini yang mengubah kode/kontrak → tulis sebagai kandidat ADR atau revisi `docs/04` (CLAUDE.md §K25). **Jangan** menulis ADR otomatis; ADR baru berstatus `Proposed` dan dibuat lewat `efn-architect` bila user setuju.

Ganti blok "Status sesi" sebelumnya, jangan menumpuk; simpan ringkasan 3 sesi terakhir di sub-bagian "Riwayat" (satu baris per sesi).
Fakta yang tahan lama (gotcha perintah, quirk lingkungan) dipindahkan ke runbook (`docs/runbooks/`) atau memory, bukan ditumpuk di sini.
Jangan menulis secret, nomor HP tanpa mask, atau data pribadi.

Lalu jawab satu baris: "Siap /clear. Sesi berikutnya cukup ketik: /lanjut".
