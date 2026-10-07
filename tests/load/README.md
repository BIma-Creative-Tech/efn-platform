# tests/load — load test Fase 1 (k6)

**Tujuan:** mengukur latensi (p95/p99), error rate, dan bottleneck CPU/memori jalur Fase 1 di stack dev: login OTP
Super-App, gerbang OTP + loyalty POS (server Odoo → BFF), dan upload Market Scout. **Bukan** uji kapasitas produksi
(dev: BFF `nest start --watch`, satu host Docker Desktop, semua service berbagi CPU).
**ADR/risiko:** ADR-017 (redeem OTP), ADR-020 (ledger), R-T02/R-B01 (ketersediaan login & POS), SEC-34/SEC-54 (rate
limit per IP vs CGNAT).

## Isi
| File | Fungsi |
|---|---|
| `k6/efn-fase1.js` | Skenario `login`, `pos`, `scout`, `edge` (lihat header file). TLS terverifikasi (SSL_CERT_FILE). |
| `run.sh` | Pengaman dev-only → override → login 1 member uji → k6 (`grafana/k6:1.3.0@sha256:3ddc8b…`) → `docker stats` → kembalikan api-gateway. |
| `docker-compose.loadtest.yml` | Override DEV: budget global OTP per jam, biaya PoW ALTCHA, cap harian Market Scout. Batas per IP/per member TIDAK diubah. |
| `fixtures/tag.jpg` | Foto label harga sintetis 17 KB. |
| `results/` | Keluaran per run (diabaikan git). |

```bash
bash tests/load/run.sh                                     # 5 menit: login 20 VU, pos 30 VU, scout 5 VU, edge 20 rps
EFN_DURATION=1m EFN_VU_LOGIN=5 EFN_VU_POS=5 bash tests/load/run.sh   # singkat
```
Klien publik (login/scout) dikirim langsung ke `https://api-gateway:3000` dari network `internal` (subnet
`TRUSTED_PROXIES`, posisi yang sama dengan Traefik) dengan `X-Forwarded-For` acak per iterasi, sehingga rate limit per
IP BFF bekerja seperti banyak klien. Rate limit Traefik (`ratelimit-api` 30 rps/IP) diukur terpisah lewat `edge`.
Member uji: login `628120*`, POS `628999*`, scout `628998*` (data dev; dapat dihapus dengan DSAR/SQL dev).

## Hasil gelombang 13 (7 Okt 2026, 06:38–06:43 UTC, 5 menit, 55 VU + 20 rps edge)
| Endpoint | p50 | p95 | p99 | Error |
|---|---|---|---|---|
| login `POST /v1/auth/otp/request` (setelah PoW) | 126 ms | 195 ms | 246 ms | 0 % (74 070 req skenario login) |
| POS `POST /v1/pos/otp/request` | 102 ms | 158 ms | 202 ms | 0 % |
| POS `POST /v1/pos/loyalty/balance` | 74 ms | 118 ms | 152 ms | 0 % |
| POS `POST /v1/pos/loyalty/earn` | 108 ms | 170 ms | 221 ms | 0 % |
| Market Scout `POST /v1/market-scout/reports` (17 KB) | 280 ms | 413 ms | 518 ms | 0 % (5 105 laporan) |
| edge `GET /health` via Traefik | — | — | — | 0 % pada run terpisah 25 rps (lihat catatan) |

Total 200 771 request, ±669 req/s. Semua ambang k6 lolos kecuali `edge` pada run penuh (7,7 % 404 = 20–25 detik
pertama setelah api-gateway di-recreate: Traefik belum merutekan container baru; `run.sh` kini menunggu router aktif,
run ulang 25 rps: 0 % gagal).

Bottleneck (rata-rata / puncak CPU, `docker stats` tiap 10 s): **api-gateway 128 % / 147 %** (Node.js event loop
jenuh ±1 core; mode dev watch), **postgres 75 % / 166 %**, **rabbitmq 43 % / 315 %** (lonjakan publish
`notify.push`/`ocr.requested`), promtail puncak 200 % (volume log per request), ocr-worker 16 % (antrean
`ocr.requested` kembali 0 setelah uji; foto identik → jalur duplikat cepat). Memori api-gateway ±460 MiB (dev,
limit dev 1,5 GiB; limit prod 512 MiB → ukur ulang dengan image produksi sebelum menyimpulkan headroom).

Catatan operasional: load test menghabiskan budget OTP global per jam di Redis (`rl:otp:budget:*`); `run.sh`
menghapus counter itu setelah selesai (dev). Pada run pertama G13 penghapusan dilakukan manual 06:57 UTC setelah
smoke E2E mendapat 429; smoke ulang `a b g` 57/57 lulus.

## Belum
- Verify OTP sukses (kode hanya di log BFF dev) dan redeem reserve/commit (butuh OTP `pos_redeem`) tidak diukur.
- Uji dengan image produksi (`NODE_ENV=production`, compiled) dan host Linux staging; Traefik dengan Cloudflare.
- Biaya PoW ALTCHA produksi (500 000) di perangkat klien di luar cakupan (lihat `services/api-gateway/src/auth/README.md`).
