# TEMPLATE TCO 3 TAHUN — EFN PLATFORM

- **Status:** Draft untuk diskusi manajemen (Fase 0). **Semua angka adalah asumsi, validasi dengan vendor** sebelum masuk business case.
- **Tanggal:** 2026-10-05 (revisi 2026-10-06: §3 J konektivitas outlet open source, ADR-023; payment gateway belum ditentukan, ADR-026)
- **Referensi:** R-B02 (ekspektasi "zero cost"), D-03/ADR-004, ADR-008, ADR-011, ADR-016, ADR-019, ADR-023, ADR-026, `docs/01 §6.3, §7`
- Satuan: **Rp juta** kecuali disebut lain. Kurs asumsi USD 1 = Rp16.000.

> **Ringkasan manajemen.** Biaya lisensi software memang **Rp0** (Odoo Community, OCA, dan seluruh stack open source). Tetapi platform tetap punya biaya operasional: server, WhatsApp per pesan, fee payment gateway, kurir, akun app store, dan terutama **tim engineer**. Estimasi kasar 3 tahun (di luar biaya variabel transaksi, GPU, dan perangkat outlet): **±Rp6,1–12,2 miliar**, dengan ±80% berasal dari SDM. Angka ini untuk membingkai diskusi anggaran, bukan penawaran.

## 1. Prinsip pemisahan

| Kategori | Sifat | Masuk total TCO di bawah? |
|---|---|---|
| A. Lisensi software | Rp0 (CE/OCA/open source). Enterprise **tidak** dihitung (ADR-013, ADR-021) | Ya (Rp0) |
| B. Infrastruktur | Tetap bulanan | Ya |
| C. GPU AI | Opsional (ADR-008) | Tidak — skenario terpisah |
| D. Pesan WhatsApp | Variabel per pesan | Ya (skenario volume) |
| E. Fee payment gateway | Variabel, proporsional GMV online | Tidak — biaya transaksi (dihitung di P&L penjualan) |
| F. 3PL / subsidi ongkir | Variabel per order | Tidak — kebijakan komersial |
| G. App store, domain | Tetap tahunan | Ya |
| H. SDM build & run | Tetap bulanan | Ya |
| I. Jasa pihak ketiga (pentest, legal, pajak, training) | Proyek | Ya |
| J. Perangkat outlet (POS, printer, tablet, 4G backup, konektivitas WireGuard — lihat §3 J) | Capex | Tidak — dihitung per outlet |
| K. Kontinjensi | 10% (low) – 15% (high) | Ya |

## 2. Asumsi volume (isi ulang dengan data klien)

| Parameter | Tahun 1 | Tahun 2 | Tahun 3 | Sumber jawaban |
|---|---|---|---|---|
| Bulan aktif OTP/notifikasi | 6 (mulai fase 2) | 12 | 12 | ADR-005 |
| Pesan WA berbayar / bulan (OTP POS + login + utility di luar window 24 jam) | 25.000 – 60.000 | 40.000 – 100.000 | ×1,3 dari Th2 | Q-17, Q-16 |
| Bulan aktif checkout online | 6 (fase 3) | 12 | 12 | ADR-005 |
| GMV online / bulan | Rp300 jt – 1 M | Rp1 – 3 M | Rp2 – 5 M | business plan |
| Jumlah brand domain | 5 | 5 | 5 | Q-37 |

## 3. Rincian per kategori

### B. Infrastruktur (asumsi provider VPS/bare-metal Indonesia; validasi dengan vendor)

| Item | Spesifikasi (`docs/01 §6.3`) | Rp juta / bulan | Rp juta / tahun |
|---|---|---|---|
| `edge-app-01` | 8 vCPU, 32 GB, NVMe 200 GB | 2,0 – 4,0 | 24 – 48 |
| `erp-db-01` | 8 vCPU, 32 GB, NVMe 500 GB | 2,5 – 5,0 | 30 – 60 |
| `data-ai-01` (tanpa GPU) | 8 vCPU, 64 GB, NVMe 1 TB | 4,0 – 7,0 | 48 – 84 |
| `db-replica-backup-01` | 4 vCPU, 16 GB, 2 TB | 1,5 – 3,0 | 18 – 36 |
| **Subtotal 4 node produksi** | | **10 – 19** | **120 – 228** |
| Staging (1 VPS) | 8 vCPU, 16–32 GB | 1,0 – 2,0 | 12 – 24 |
| Backup offsite (object storage 2–5 TB) | di luar DC utama | 0,5 – 1,5 | 6 – 18 |
| Cloudflare (Free–Pro) + domain 5 brand (SSL Let's Encrypt Rp0) | | — | 1 – 8 |
| **Total infra Tahun 1** | | | **139 – 278** |
| Tahun 2 | asumsi +15% pertumbuhan | | 160 – 320 |
| Tahun 3 | + 1 node HA PostgreSQL (Patroni, R-T07) | | 184 – 368 |

### C. GPU opsional (ADR-008) — skenario terpisah
| Opsi | Biaya (asumsi) | Pemicu |
|---|---|---|
| Sewa server GPU 16 GB VRAM | Rp7 – 15 jt/bulan → 84 – 180 jt/tahun | > 1.000 foto/hari atau p95 > 60 detik |
| Beli kartu kelas 16 GB + colocation | Capex Rp25 – 60 jt/kartu + colocation | Volume stabil jangka panjang |

### D. WhatsApp Cloud API (ADR-011)
Tarif acuan: authentication & utility Indonesia **±Rp356,65/pesan**, marketing **±Rp586,33/pesan**, belum PPN 11% (sumber sekunder BSP, Okt 2026: help-center Qontak, Gupshup IDR Jan 2026; tarif resmi di developers.facebook.com — **validasi**). Utility di dalam window layanan pelanggan 24 jam disebut gratis; diskon volume utility/authentication hingga −25% (sumber sekunder). Markup BSP tidak termasuk.

Rumus: `pesan_berbayar_per_bulan × Rp396 (termasuk PPN) × bulan_aktif`.

| Tahun | Low | High |
|---|---|---|
| 1 (6 bulan) | 25.000 × 396 × 6 = **59** | 60.000 × 396 × 6 = **143** |
| 2 | 40.000 × 396 × 12 = **190** | 100.000 × 396 × 12 = **475** |
| 3 | ×1,3 = **247** | ×1,3 = **618** |

Penghematan: OTP hanya untuk aksi berisiko (bukan setiap transaksi), sesi login lebih panjang, notifikasi via push app (gratis) sebelum WA.

### E. Payment gateway (ADR-016) — biaya transaksi, di luar total
Vendor **belum ditentukan** (keputusan klien 6 Okt 2026; platform dibuat gateway-agnostic, ADR-026). Tarif asumsi (validasi penawaran minimal 2 vendor, mis. Xendit/Midtrans): QRIS ±0,7% · VA ±Rp4.000–5.000/trx · e-wallet ±1,5–2% · kartu ±2,9% + Rp2.000. Blended asumsi 1,0–1,8%.

| Tahun | Rumus | Low | High |
|---|---|---|---|
| 1 (6 bulan) | GMV × blended × 6 | 300 × 1,0% × 6 = 18 | 1.000 × 1,8% × 6 = 108 |
| 2 | × 12 | 1.000 × 1,0% × 12 = 120 | 3.000 × 1,8% × 12 = 648 |
| 3 | × 12 | 2.000 × 1,0% × 12 = 240 | 5.000 × 1,8% × 12 = 1.080 |

MDR EDC/QRIS di POS outlet sudah ada hari ini → bukan biaya baru platform.

### F. 3PL & marketplace — di luar total
- GoSend/GrabExpress/Lalamove: tarif per order dibebankan ke pelanggan; biaya platform = **subsidi ongkir** (kebijakan komersial, Q-39). Rumus: `order_online × subsidi_rata2`.
- GoFood/GrabFood/ShopeeFood: komisi marketplace ±20%+ (R-B02) dan program partner berbayar (R-B04) — fase 6.

### G. App store & domain
| Item | Biaya | Rp juta/tahun |
|---|---|---|
| Apple Developer Program | USD 99/tahun | ±1,6 |
| Google Play Console | USD 25 sekali | ±0,4 (tahun 1) |
| **Dibulatkan** | | **2 / tahun** |
EAS Build (Expo) berbayar opsional; self-build via CI = Rp0 (biaya runner macOS GitHub, validasi).

### H. SDM (asumsi rentang gaji/kontrak Jakarta; validasi HR/vendor)

| Peran | FTE Th1 (build) | Rp jt/bln per FTE | FTE Th2–3 (run + enhancement) |
|---|---|---|---|
| Arsitek solusi | 0,5 | 20 – 40 | — (ad hoc) |
| Odoo engineer senior (multi-company, MRP) | 1 | 25 – 40 | 1 |
| Odoo engineer mid | 1 | 15 – 25 | 1 |
| Backend/fullstack NestJS | 2 | 15 – 30 | 1 |
| Frontend/mobile (Next.js/Expo) | 1 | 15 – 25 | 1 |
| Data/AI engineer | 1 | 18 – 35 | 0,5 |
| DevSecOps/SRE | 1 | 20 – 35 | 1 |
| QA | 1 | 10 – 18 | 0,5 |
| **Total / bulan** | | **143 – 258** | **104 – 182** |
| **Total / tahun** | | **1.716 – 3.096** | **1.248 – 2.178** |

Product Owner, super-user outlet, dan staf internal klien **tidak** dihitung.

### I. Jasa pihak ketiga
| Item | Th1 | Th2 | Th3 |
|---|---|---|---|
| Pentest eksternal (R-S11) | 50 – 150 | 50 – 150 | 50 – 150 |
| Legal (UU PDP, MGM/Permendag) & konsultan pajak (PB1/PPN, Coretax, transfer pricing, ADR-019) | 75 – 250 | 25 – 75 | 25 – 75 |
| Training & change management | 25 – 75 | 10 – 25 | 10 – 25 |

### J. Perangkat outlet — konektivitas WireGuard ke Odoo (ADR-023, revisi 6 Okt 2026) — capex per outlet, di luar total
WireGuard dan semua opsi di bawah (kecuali baris pembanding) **open source, lisensi Rp0**; yang dihitung hanya perangkat keras & tenaga. Harga **indikatif — validasi vendor**.

| Opsi | Kapan dipakai | Perangkat keras / outlet | Tenaga pasang | Rp juta / outlet (sekali) |
|---|---|---|---|---|
| **B-a** Klien WireGuard resmi di perangkat (Windows/Android/Linux) — **default** | ≤ 3 perangkat membuka Odoo | Rp0 | ±30 menit/perangkat | **0** |
| **B-b** OpenWrt di router yang sudah ada (bila didukung) | > 3 perangkat, router lama kompatibel | Rp0 | ±1 jam | **0** |
| **B-b** Router baru berbasis OpenWrt | > 3 perangkat / KDS / kiosk / printer jaringan | ±Rp400–900 rb (dual-WAN untuk 4G: ±Rp1–2 jt) | ±1 jam | **0,4 – 0,9** (1 – 2 bila dual-WAN) |
| B-c Raspberry Pi / mini-PC + Debian/OpenWrt | Router ISP tidak bisa diganti | ±Rp1,2–3,5 jt | ±2 jam | 1,2 – 3,5 |
| Pembanding: router komersial (MikroTik, *bukan* OSS) — angka versi lama | — | Rp0,7–3 jt | ±1 jam | 0,7 – 3 |

Rumus anggaran: `Σ outlet × biaya opsi` (+ backup 4G bila Q-55 memintanya: modem/router LTE + paket data bulanan, terpisah). Contoh 20 outlet (12 B-a, 8 B-b router baru): 12 × 0 + 8 × 0,4–0,9 = **Rp3,2 – 7,2 jt**, dibanding Rp30 – 60 jt pada asumsi lama.

## 4. Ringkasan TCO (Rp juta; di luar C, E, F, J)

| Komponen | Th1 low | Th1 high | Th2 low | Th2 high | Th3 low | Th3 high |
|---|---|---|---|---|---|---|
| A. Lisensi | 0 | 0 | 0 | 0 | 0 | 0 |
| B. Infrastruktur | 139 | 278 | 160 | 320 | 184 | 368 |
| D. WhatsApp | 59 | 143 | 190 | 475 | 247 | 618 |
| G. App store | 2 | 2 | 2 | 2 | 2 | 2 |
| H. SDM | 1.716 | 3.096 | 1.248 | 2.178 | 1.248 | 2.178 |
| I. Pentest | 50 | 150 | 50 | 150 | 50 | 150 |
| I. Legal & pajak | 75 | 250 | 25 | 75 | 25 | 75 |
| I. Training | 25 | 75 | 10 | 25 | 10 | 25 |
| **Subtotal** | **2.066** | **3.994** | **1.685** | **3.225** | **1.766** | **3.416** |
| K. Kontinjensi (10% / 15%) | 207 | 599 | 169 | 484 | 177 | 512 |
| **Total** | **±2.273** | **±4.593** | **±1.854** | **±3.709** | **±1.943** | **±3.928** |

**Total 3 tahun: ±Rp6,07 – 12,23 miliar.** Tambahan bila GPU dipakai sejak Th2: +Rp168 – 360 jt (2 tahun sewa).

## 5. Cara memakai template
1. Ganti asumsi di §2 dengan jawaban workshop (Q-16, Q-17, Q-37, Q-39, Q-50, Q-53).
2. Minta minimal 2 penawaran untuk: VPS/bare-metal (lokasi Indonesia), BSP WhatsApp, payment gateway.
3. Tandai baris yang sudah tervalidasi vendor dengan tanggal & nama vendor; sisanya tetap "asumsi".
4. Tinjau ulang di setiap gate fase (ADR-005).
