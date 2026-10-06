# Lisensi Komponen

Tujuan BR-01 adalah **zero license fee**, bukan tanpa kewajiban lisensi. Tabel ini adalah inventaris kewajiban komponen yang **di-deploy**.

| Komponen | Lisensi | Kewajiban utama |
|---|---|---|
| Odoo Community 19 | LGPL-3.0 | Modifikasi inti harus dibagikan; addons terpisah bebas pilih lisensi kompatibel. **Satu-satunya basis Odoo yang di-deploy.** |
| Addons `efn_*` | **LGPL-3.0** (ADR-031; konfirmasi Q-V2) | Milik EFN, dipakai internal. Potongan kode yang diambil dari Odoo CE tetap LGPL-3 dengan keterangan asal. Modul AGPL lama (`efn_setup_audit`, bergantung OCA `auditlog`) diganti `efn_audit`. |
| PostgreSQL | PostgreSQL License | Tidak ada kewajiban berarti. |
| Redis 7.4 | RSALv2 / SSPL (dual) | Boleh dipakai internal; tidak boleh dijual sebagai layanan Redis. Alternatif: Valkey (BSD). |
| RabbitMQ | MPL-2.0 | Tidak ada kewajiban untuk pemakaian. |
| MinIO / SILO (fork, ADR-030) | AGPL-3.0 | Pemakaian internal tanpa modifikasi aman. |
| ClickHouse | Apache-2.0 | Bebas. |
| Metabase OSS | AGPL-3.0 | Embedding publik butuh lisensi komersial; embedding internal (static) boleh. |
| Ollama | MIT | Bebas. Model (Qwen2.5-VL: Apache-2.0; Llama 3.2: Llama Community License, batasan >700 juta MAU). |
| Traefik | MIT | Bebas. |
| Next.js, NestJS, React, Expo | MIT | Bebas. |
| Prometheus, Loki, Grafana, Promtail | Apache-2.0 / AGPL-3.0 (Grafana, Loki) | Pemakaian internal aman. |
| Uptime Kuma | MIT | Bebas. |
| Tesseract | Apache-2.0 | Bebas. |

## Modul Odoo pihak ketiga — **tidak di-deploy** (ADR-031, keputusan klien 6 Okt 2026)

OCA, Cybrosys, dan vendor Apps Store **tidak** dipasang di image, `addons_path`, CI, maupun skrip build. Mereka hanya **referensi desain**. (Sebelumnya OCA addons AGPL-3/LGPL-3 tercantum sebagai komponen; baris itu dihapus.)

| Sumber referensi | Lisensi yang ditemui | Aturan pemakaian sebagai referensi |
|---|---|---|
| OCA (github.com/OCA, branch 19.0/18.0) | AGPL-3.0 (mis. `auditlog`, inter-company, `mis_builder`, `account_financial_report`), LGPL-3.0 (mis. `date_range`, `queue_job`) | Boleh dibaca (struktur data, alur, UX). Dilarang menyalin kode/XML/JS/aset. |
| Cybrosys publik (github.com/CybroOdoo/CybroAddons) | **Per modul** (repo tanpa lisensi induk): LGPL-3.0 atau AGPL-3.0 | Sama seperti OCA; cek `__manifest__.py` tiap modul. |
| Cybrosys berbayar / vendor Apps Store | OPL-1 (proprietary) | **Hanya** deskripsi & screenshot publik. Dilarang membeli/mengunduh untuk membaca kodenya. |

Setiap modul `efn_*` yang memakai referensi wajib memuat bagian **"Referensi"** di README (vendor, modul, versi, lisensi, yang dipelajari, tanggal, pernyataan tidak ada kode yang disalin). Bila kode vendor ternyata tersalin: AGPL → modul wajib AGPL-3 + atribusi (default: ditulis ulang); LGPL → wajib atribusi hak cipta (default: ditulis ulang); OPL-1 → pelanggaran lisensi, hapus dan tulis ulang. Rincian: `docs/decisions/ADR-031-tanpa-dependency-modul-odoo-pihak-ketiga.md`. Bukan opini hukum.

Larangan ini berlaku untuk **addon Odoo**. Pustaka Python/JS (mis. `xlsxwriter`) dan komponen di tabel atas tetap boleh, di-pin dan dipindai (R-S02) — menunggu konfirmasi Q-V5.

**Biaya operasional yang bukan lisensi** (harus masuk TCO): WhatsApp Cloud API per pesan, payment gateway per transaksi, komisi 3PL, server/GPU, domain & Cloudflare, Apple Developer Program, Google Play Console, SMS fallback.
