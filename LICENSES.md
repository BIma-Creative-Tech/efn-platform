# Lisensi Komponen

Tujuan BR-01 adalah **zero license fee**, bukan tanpa kewajiban lisensi. Tabel ini adalah inventaris kewajiban.

| Komponen | Lisensi | Kewajiban utama |
|---|---|---|
| Odoo Community 19 | LGPL-3.0 | Modifikasi inti harus dibagikan; addons terpisah bebas pilih lisensi kompatibel. |
| OCA addons | AGPL-3.0 / LGPL-3.0 | AGPL: jika EFN mendistribusikan/menyediakan ke pihak ketiga sebagai layanan, source harus tersedia. Pemakaian internal aman. |
| Addons `efn_*` | AGPL-3.0 (pilihan kami) | Konsisten dengan OCA; hanya dipakai internal. |
| PostgreSQL | PostgreSQL License | Tidak ada kewajiban berarti. |
| Redis 7.4 | RSALv2 / SSPL (dual) | Boleh dipakai internal; tidak boleh dijual sebagai layanan Redis. Alternatif: Valkey (BSD). |
| RabbitMQ | MPL-2.0 | Tidak ada kewajiban untuk pemakaian. |
| MinIO | AGPL-3.0 | Sama seperti OCA; pemakaian internal aman. |
| ClickHouse | Apache-2.0 | Bebas. |
| Metabase OSS | AGPL-3.0 | Embedding publik butuh lisensi komersial; embedding internal (static) boleh. |
| Ollama | MIT | Bebas. Model (Qwen2.5-VL: Apache-2.0; Llama 3.2: Llama Community License, batasan >700 juta MAU). |
| Traefik | MIT | Bebas. |
| Next.js, NestJS, React, Expo | MIT | Bebas. |
| Prometheus, Loki, Grafana, Promtail | Apache-2.0 / AGPL-3.0 (Grafana, Loki) | Pemakaian internal aman. |
| Uptime Kuma | MIT | Bebas. |
| Tesseract | Apache-2.0 | Bebas. |

**Biaya operasional yang bukan lisensi** (harus masuk TCO): WhatsApp Cloud API per pesan, payment gateway per transaksi, komisi 3PL, server/GPU, domain & Cloudflare, Apple Developer Program, Google Play Console, SMS fallback.
