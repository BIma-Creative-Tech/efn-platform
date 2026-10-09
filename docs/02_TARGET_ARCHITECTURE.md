# ARSITEKTUR TARGET EFN (KONSOLIDASI)

Dokumen ini adalah satu-satunya acuan arsitektur. Jika bertentangan dengan 6 dokumen asli, dokumen ini yang menang sampai ADR baru mengubahnya. Alasan tiap pilihan ada di `01_REVIEW_RISK_GAP_ANALYSIS.md` Bagian 2 dan 6.

Revisi 2026-10-05 (v2): enkripsi di setiap hop & penyimpanan (ADR-022), Odoo hanya via WireGuard + 2FA (ADR-023), kredensial per service (ADR-024), event tanpa nomor HP mentah (`docs/04 §4`). Lihat §5.

Revisi 2026-10-06 (v3, keputusan klien 6 Okt): kustodi kunci hanya Superadmin & mTLS sesuai rekomendasi (ADR-022); konektivitas outlet open source — klien WireGuard di perangkat / OpenWrt (ADR-023); payment gateway-agnostic, vendor belum ditentukan (ADR-026, `docs/04 §8.3`); pajak PB1 per kota & include/exclude configurable di Odoo, Super-App menampilkan pajak dari Odoo via BFF (ADR-027); captcha ALTCHA self-hosted (ADR-028); JWT `sub` = UUID member (ADR-029); privasi/DSAR (`docs/04 §8.4`, `docs/compliance/`).

Revisi 2026-10-06 (v4, Gelombang 12): object storage = **SILO** (fork komunitas MinIO, dibangun dari source) di belakang filter L7 **`s3-gateway`** (ADR-030); **tanpa modul Odoo pihak ketiga** — OCA/Cybrosys hanya referensi, gap = `efn_*` (ADR-031): inter-company = `efn_intercompany` (rencana Fase 1), audit = `efn_audit` + sink Loki 400 hari + salinan ClickHouse append-only (sedang dibangun); mTLS Loki→Alertmanager. Lihat §5.5.

Revisi 2026-10-09 (v5, Gelombang 17): penjualan online Super-App = BFF + modul `efn_online_order` lewat `/json/2` terfilter (`odoo-rpc-gateway`, mTLS), tanpa `website_sale` (ADR-034, Proposed); kontrak `docs/04 §10`; diagram §3.4 diperbarui.

## 1. Context Diagram

```mermaid
flowchart LR
  subgraph Pengguna
    C[Pelanggan<br/>Super-App Web / Mobile]
    K[Kasir & Kitchen<br/>POS, KDS, Kiosk]
    W[Staf Warehouse & QC<br/>QC Intake PWA]
    M[Manajemen<br/>Metabase, AI Analyst]
  end

  subgraph EFN[Platform EFN]
    E[Edge: Cloudflare + Traefik]
    F[Next.js Web]
    B[NestJS BFF & Workers]
    O[Odoo CE 19 Multi-Company]
    D[(PostgreSQL)]
    R[(Redis)]
    Q[(RabbitMQ)]
    GW[s3-gateway<br/>filter L7]
    S[(Object storage<br/>SILO)]
    CH[(ClickHouse)]
    MB[Metabase]
    AI[Ollama + OCR Worker]
  end

  subgraph Eksternal
    WA[WhatsApp Cloud API]
    PG[Payment Gateway<br/>vendor belum ditentukan - ADR-026]
    L3[3PL: GoSend, GrabExpress, Lalamove]
    FM[Food Marketplace<br/>GoFood / GrabFood - fase lanjut]
    TAX[Coretax / e-Faktur]
  end

  C --> E --> F --> B
  K -- WireGuard --> E --> O
  W --> E --> F
  M -- WireGuard --> E --> MB
  M -- WireGuard --> E --> AI
  B --> O
  B --> D
  B --> R
  B --> Q
  B --> GW --> S
  O --> D
  Q --> AI
  AI --> GW
  D -. ETL 15 mnt .-> CH --> MB
  B --> WA
  B <--> PG
  B <--> L3
  O -. fase lanjut .-> FM
  O -. export .-> TAX
```

## 2. Layered Architecture

```mermaid
flowchart TB
  subgraph L1[Layer 1: Edge & Security]
    CF[Cloudflare CDN/WAF]
    TR[Traefik v3<br/>TLS, routing per brand, rate limit]
    CS[CrowdSec bouncer]
  end
  subgraph L2[Layer 2: Experience]
    NX[Next.js Super-App & Brand Sites<br/>multi-tenant by domain]
    EX[Expo Mobile App]
    PWA[QC Intake PWA]
    POS[Odoo POS: Retail, F&B, KDS, Kiosk, Customer Display]
  end
  subgraph L3[Layer 3: Orchestration - NestJS]
    AUTH[Auth & OTP]
    CART[Cart & Checkout per-PT]
    MGM[MGM & Loyalty Ledger]
    PH[Price Hunter API]
    CONN[Payment & 3PL Connectors]
    NOTIF[Notification Service]
  end
  subgraph L4[Layer 4: System of Record - Odoo CE 19]
    INV[Inventory & MRP Kitting]
    ACC[Accounting multi-company + l10n_id]
    IC[efn_intercompany<br/>rencana Fase 1, ADR-031]
    PRC[Pricelist & Promo]
    AUD[efn_audit<br/>log audit ter-mask]
    AGRI[efn_agri QC & Farmer]
  end
  subgraph L5[Layer 5: Data & Messaging]
    PGO[(PostgreSQL odoo)]
    PGP[(PostgreSQL platform)]
    RD[(Redis)]
    MQ[(RabbitMQ)]
    S3G[s3-gateway HAProxy]
    MN[(SILO object storage<br/>network objstore)]
  end
  subgraph L6[Layer 6: Analytics & AI]
    ETL[ETL Python incremental]
    CHX[(ClickHouse marts)]
    MBX[Metabase]
    OCR[OCR Worker: Tesseract/PaddleOCR -> VLM]
    T2S[Text-to-SQL read-only]
  end
  subgraph L7[Layer 7: Platform Ops]
    OBS[Prometheus, Grafana, Loki + ruler, Alertmanager]
    BK[WAL-G -> s3-gateway -> SILO pg-backup]
    CI[GitHub Actions -> GHCR -> Deploy]
  end
  L1 --> L2 --> L3 --> L4 --> L5
  L3 --> L5
  L5 --> L6
  L7 -.-> L1 & L2 & L3 & L4 & L5 & L6
```

## 3. Alur Kritis

### 3.1 OTP-Gated Promo di POS (online)

```mermaid
sequenceDiagram
  participant Kasir as POS (Odoo)
  participant Odoo
  participant BFF as NestJS
  participant Redis
  participant WA as WhatsApp Cloud API
  participant Cust as Pelanggan
  Kasir->>Odoo: Pilih "Gunakan Poin/MGM" (pelanggan = partner order)
  Note over Odoo: member_ref diambil server dari HP partner_id, bukan diketik kasir (kontrak v2, SEC-07)
  Odoo->>BFF: POST /v1/pos/otp/request {member_ref, order_ref, amount} (HTTPS verify-full, service key)
  BFF->>Redis: HSET otp:{challenge_id} HMAC(kode), member_hash TTL 180s (rediss, ACL)
  BFF->>WA: Template "kode OTP"
  WA-->>Cust: OTP
  Cust-->>Kasir: Sebutkan / input di customer display
  Kasir->>Odoo: Input OTP
  Odoo->>BFF: POST /otp/verify
  BFF->>Redis: GET & compare; attempts++ (max 3)
  BFF-->>Odoo: {ok:true, discount_token}
  Odoo->>Odoo: Apply promo, lock token ke order
  Note over Kasir,Odoo: Jika POS offline: tombol promo nonaktif (ADR-017)
```

### 3.2 Price Hunter (asynchronous)

```mermaid
sequenceDiagram
  participant App
  participant BFF as NestJS
  participant S3 as s3-gateway → SILO
  participant MQ as RabbitMQ
  participant W as OCR Worker
  participant PG as PostgreSQL
  participant Odoo
  App->>BFF: POST /market-scout/report (foto, nama, harga, geo)
  BFF->>BFF: Validasi MIME, re-encode, pHash dedup, rate limit
  BFF->>BFF: Cek consent lokasi, bulatkan geo 4 desimal
  BFF->>S3: Simpan foto (HTTPS, SSE-S3)
  BFF->>PG: INSERT report status=queued, user_hash, user_enc (AES-256-GCM)
  BFF->>MQ: publish ocr.requested v2 {report_id, user_hash, ...} tanpa HP mentah
  BFF-->>App: 202 {report_id}
  W->>MQ: consume
  W->>W: PaddleOCR -> jika confidence<0.8 -> VLM via Ollama
  W->>W: Fuzzy match nama >75%, harga match
  W->>PG: UPDATE status=valid|rejected, extracted json
  alt valid
    W->>MQ: publish loyalty.earn.requested v2 {member_hash, source_ref=report_id} (AMQP user_id=efn-ocr)
    Note over MQ,PG: BFF verifikasi publisher + report valid + hash cocok -> INSERT loyalty_ledger (member_ref_enc disalin dari user_enc)
    W->>MQ: publish notify.push v2 {member_hash, source, source_ref}
  else rejected
    W->>MQ: publish notify.push v2 "Foto kurang jelas"
  end
  Note over MQ,PG: BFF notification mendekripsi user_enc dari baris sumber, cek hash, kirim WA
  Note over PG: ETL 15 menit -> ClickHouse competitor_prices
```

### 3.3 Kitting yang benar (Manufacture BoM, bukan Phantom)

```mermaid
flowchart LR
  A[PT Retail WH<br/>Ayam Utuh kg, Tepung kg] -- inter-company SO/PO<br/>efn_intercompany - ADR-031 --> B[PT F&B Central Kitchen<br/>receipt bahan]
  B -- MO: BoM Manufacture<br/>0.2kg ayam + 0.05kg tepung = 1 Porsi --> C[Stok: Paket Ayam Geprek<br/>storable, UoM Porsi]
  C -- internal transfer --> D[Outlet F&B<br/>stok Paket]
  D -- POS jual menu --> E[Potong 1 Porsi Paket<br/>tanpa BoM di outlet]
  F[Menu combo = Phantom BoM<br/>Paket Geprek + Nasi + Es Teh] -.-> E
```

### 3.4 Checkout Unified Cart lintas PT

Revisi 2026-10-09 (Gelombang 17): alur mengikuti kontrak `docs/04 §10` (v5.0) dan ADR-034 (Proposed). Pilot = kanal PT Retail saja, ambil di toko; F&B online, delivery/3PL, dan reservasi stok **belum** aktif (menunggu Q-ON2/Q-ON3/Q-ON4) — tetapi struktur sudah per kanal sehingga kanal F&B kelak ditambahkan tanpa mengubah kontrak. Ini **bukan** keputusan klien; ADR-019 & ADR-034 masih Proposed.

```mermaid
flowchart TB
  APP[Super-App Next.js] -- "cookie sesi, Bearer OTP" --> BFF[NestJS BFF]
  BFF -- "katalog: pull delta 5 menit<br/>(salinan BFF + gambar di object storage)" --> GWO
  BFF -- "cart Redis per member<br/>(tanpa harga)" --> BFF
  BFF -- "quote / place per kanal<br/>/json/2 + API key per PT" --> GWO[odoo-rpc-gateway<br/>mTLS, allowlist 10 metode]
  GWO --> O1[Odoo E-28 kanal RTL<br/>SO PT Retail, PPN dari FP outlet]
  GWO -.-> O2[Odoo kanal FNB - nonaktif<br/>Q-ON2: SO+KDS atau pos.order E-27]
  BFF -- "1 payment intent = Σ amount_payable<br/>porsi per PT - ADR-019/026" --> PAY[Payment gateway<br/>pemegang akun: Holding sebagai agen penagih<br/>provider mock - ADR-016 ditunda]
  PAY -- "webhook paid (HMAC + replay + idempotensi)" --> BFF
  BFF -- "order.paid (RabbitMQ) -> mark_paid per SO" --> GWO
  O1 -- "konfirmasi + faktur + pembayaran jurnal titipan (A1)" --> O1
  O1 -- "event ready / picked_up / cancelled<br/>HTTPS + service key + HMAC" --> BFF
  BFF -- "notify.push (WA Cloud API)" --> APP
  BFF -- "stok kurang / batal / bayar terlambat" --> COMP[Kompensasi per SO:<br/>batal + refund porsi PT itu saja]
  PAY -. "settlement dana titipan (manual A1; A2/C kelak)" .-> ICB[Holding -> PT Retail / PT F&B]
```

Pelanggan mengambil barang dengan **kode ambil** (rahasia: terenkripsi di BFF, hash KDF di Odoo). Delivery & dispatch 3PL (diagram lama) menjadi fase lanjut (Q-39/Q-40/Q-ON3).

## 4. Environments

| Env | Tujuan | Infra | Deploy |
|---|---|---|---|
| `dev` | Laptop developer | `docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile core up` | manual |
| `staging` | Integrasi, UAT, DAST | 1 VPS, Compose, data sintetis | otomatis dari `main` |
| `prod` | Live | 4 node, Compose/Swarm | manual approval dari tag `v*` |

## 5. Keamanan Jaringan & Enkripsi (v2, 5 Okt 2026)

Prinsip: **enkripsi di setiap transaksi data** (instruksi klien; ADR-022), Odoo hanya via WireGuard (ADR-023), kredensial per service (ADR-024). Kontrak teknis yang mengikat: `docs/04_SERVICE_CONTRACTS.md` §7.

### 5.1 Zona & hop terenkripsi

```mermaid
flowchart LR
  subgraph INET[Zona Internet]
    C[Pelanggan<br/>browser / app]
  end
  subgraph OUT[Zona Outlet & Staf]
    POS[POS / KDS / printer<br/>LAN outlet]
    RT["Klien WireGuard di perangkat, default ≤3 perangkat<br/>atau router OpenWrt + 4G - ADR-023"]
    LP[Laptop staf<br/>klien WireGuard]
    POS --- RT
  end
  CF[Cloudflare<br/>WAF/CDN]
  subgraph EDGE[Node edge]
    TRP[Traefik :443 publik<br/>app., api.]
    TRW[Traefik :443 wg-https<br/>erp., ops., bi.]
    WG[(Hub WireGuard)]
  end
  subgraph APP[Zona aplikasi - network Docker, TLS verify-full CA internal]
    NX[Next.js + ghostunnel]
    BFF[NestJS BFF HTTPS]
    OD[Odoo + ghostunnel]
    OCR[OCR worker]
    ETL[ETL]
    OL[Ollama + ghostunnel]
    MB[Metabase / Grafana]
  end
  subgraph DATA[Zona data - LUKS di semua node]
    PG[(PostgreSQL TLS<br/>kolom PII AES-GCM)]
    RD[(Redis rediss + ACL)]
    MQ[(RabbitMQ amqps)]
    GW[s3-gateway<br/>TLS + filter L7]
    CH[(ClickHouse TLS<br/>encrypted disk)]
    BK[(WAL-G libsodium -> pg-backup)]
  end
  subgraph OBJ[Network objstore - internal]
    S3[(SILO HTTPS + SSE-S3<br/>object lock pg-backup)]
  end
  C -- TLS publik --> CF -- TLS Full strict + AOP --> TRP
  RT -- WireGuard --> WG
  LP -- WireGuard --> WG
  WG -- TLS ACME DNS-01 --> TRW
  TRP -- HTTPS CA internal --> NX & BFF
  TRW -- HTTPS CA internal --> OD & MB
  NX -- HTTPS --> BFF
  OD -- HTTPS service key --> BFF
  BFF -- TLS --> PG & RD & MQ & GW
  OD -- TLS --> PG
  OCR -- TLS --> MQ & GW & PG
  OCR -- mTLS --> OL
  ETL -- TLS --> PG & CH
  MB -- TLS --> CH
  PG -- HTTPS --> BK
  BK -- HTTPS --> GW
  GW -- TLS re-encrypt verify --> S3
  BFF -- TLS publik --> EXT[WhatsApp Cloud API<br/>Payment, 3PL]
```

| Zona | Siapa | Kontrol |
|---|---|---|
| Internet | Pelanggan | Hanya `app.`/`api.` lewat Cloudflare (Full strict + Authenticated Origin Pulls); firewall origin 443 hanya dari rentang Cloudflare + WireGuard. `/v1/pos/*`, `/internal/*`, `/metrics` tidak dirutekan di host publik (SEC-14). |
| Outlet & staf | POS, KDS, back-office, manajemen | **WireGuard** (klien resmi di perangkat untuk outlet ≤ 3 perangkat; router OpenWrt untuk outlet besar; klien laptop untuk staf — ADR-023 rev. 6 Okt 2026) → entrypoint `wg-https` di IP WireGuard; peer outlet hanya ke `:443` edge. User internal Odoo wajib 2FA (`auth_totp.policy = employee_required`). |
| Aplikasi | Container service | Semua hop HTTPS/TLS **verify-full** terhadap CA internal (`/run/secrets/efn_ca_crt`); app tanpa TLS native dibungkus sidecar **ghostunnel** (app listen `127.0.0.1`). Tidak ada fallback plaintext. |
| Data | PostgreSQL, Redis, RabbitMQ, s3-gateway → SILO, ClickHouse | TLS wajib; disk **LUKS2**; kolom PII AES-256-GCM (kunci hanya di BFF); SSE-S3; ClickHouse encrypted disk; backup WAL-G libsodium + object lock. Storage hanya di network `objstore` (klien tidak bisa menjangkaunya langsung); access key per klien acak (ADR-030, `docs/04 §8.12–8.14`). |
| Antar-node | 4 node (ADR-004) | **WireGuard mesh** sebagai lapisan kedua; SSH hanya via WireGuard, key-only. |

Pengecualian enkripsi hanya lewat tabel "Pengecualian terdaftar" di ADR-022.

### 5.2 Kredensial per service (ADR-024, ringkas dari `docs/04 §7.3`)

| Service | PostgreSQL | RabbitMQ | MinIO | Redis | ClickHouse | Kunci kripto |
|---|---|---|---|---|---|---|
| Odoo | `efn_odoo` (owner DB `efn_odoo`) | — | — | — | — | `odoo_bff_api_key` (klien) |
| BFF | `efn_bff` (owner skema `platform`) | `efn-bff` | `efn-bff` (Put/Delete `market-scout/reports/*`) | `efn-bff` (ACL prefix) | — | `pii_encryption_key`, `otp_pepper`, `jwt_secret`, `discount_token_secret` |
| OCR worker | `efn_ocr` (kolom terbatas) | `efn-ocr` | `efn-ocr` (Get) | — | — | — (tidak memegang kunci PII) |
| ETL | `efn_etl` (grant kolom, tanpa PII terenkripsi) | — | — | — | `efn_etl` (usulan) | `member_hash_pepper` turunan (bukan `otp_pepper`, SEC-16) |
| Metabase | `efn_metabase` (DB `metabase`) | — | — | — | read-only view (usulan) | — |
| Text-to-SQL | — | — | — | — | `efn_text2sql` view whitelist | — |
| Exporter | `efn_exporter` (`pg_monitor`) | monitoring | — | read-only | — | — |
| Bootstrap/backup | `postgres` (socket lokal) | admin (init) | root acak (`minio-init`, satu-satunya yang ke storage langsung), user backup | — | — | `walg_libsodium_key`, `minio_kms_key` |

Catatan v4: kolom MinIO = **access key acak** dari secret `minio_{bff,ocr,backup,prometheus}_access_key` (nama di tabel adalah peran, bukan access key); semua lewat `s3-gateway`. Pemilik skema `platform` = role NOLOGIN `efn_platform_owner`; `efn_bff` hanya DML minimum (SEC-39).

### 5.3 Event v2 — tanpa nomor HP mentah di antrean
- Payload `ocr.requested`, `loyalty.earn.requested`, `notify.push` hanya membawa `user_hash`/`member_hash` + `source`/`source_ref` (`docs/04 §4`, `schema_version: 2`).
- Penerima notifikasi diselesaikan BFF dengan mendekripsi `user_enc`/`member_ref_enc` dari baris sumber dan memverifikasi hash-nya.
- Publisher diautentikasi lewat properti AMQP `user_id` = user RabbitMQ (SEC-08); BFF memvalidasi semantik per `source`.
- Konsekuensi: DLQ, outbox, dan log broker tidak lagi berisi PII langsung (pseudonim tetap data pribadi — retensi di `docs/compliance/PDP_DATA_INVENTORY.md`).

### 5.4 Lainnya
- Odoo: `list_db = False`, `/web/database/*` diblok, `proxy_mode = True`, `/xmlrpc` & `/jsonrpc` hanya dari subnet service internal.
- Secrets: GitHub Environments (CI) → file di `/run/secrets` via Docker secrets. Tidak ada `.env` berisi secret di repo. Kunci yang kehilangannya = kehilangan data (PII, MinIO KMS, WAL-G, LUKS, root CA) wajib punya salinan offline di brankas (ADR-022).
- Kepatuhan UU PDP: `docs/compliance/` (inventaris data/RoPA v3, DSAR, consent, respons breach). Insiden keamanan: `docs/runbooks/security-incident.md`.

### 5.5 Jejak audit Odoo & object storage (v4, Gelombang 9–12)

```mermaid
flowchart LR
  subgraph ODOO[Odoo CE 19 - hanya CE + efn_*]
    U[User / admin Odoo] --> ORM[ORM create/write/unlink]
    ORM --> AH[efn_audit hook<br/>masking PII sebelum simpan]
    AH --> AL[(efn.audit.log<br/>730 h, min 365 hard-coded<br/>read-only, record rule per PT)]
    AH -- postcommit, tanpa nilai/PII --> SO[stdout: EFN_AUDIT_SINK json<br/>model kontrol + purge]
  end
  SO --> PT[Promtail] -- mTLS --> LK[(Loki stream efn_audit_sink<br/>400 hari)]
  LK -- ruler, mTLS --> AM[Alertmanager<br/>EfnAuditControlChange<br/>EfnAuditUnexpectedPurge]
  AL -. ETL append-only, row_hash berantai<br/>SEDANG DIBANGUN .-> CHA[(ClickHouse salinan audit<br/>user tanpa ALTER/DELETE)]
  AM --> OC[On-call -> runbook security-incident]
```

| Lapisan | Melindungi dari | Batas yang diakui |
|---|---|---|
| `efn.audit.log` (ORM read-only, retensi minimum di kode, log kontrol/purge tidak di-GC) | Pengubahan/penghapusan log lewat UI/RPC, retensi diturunkan | Admin sistem dengan server action/SQL/uninstall/akses DB tetap bisa → deteksi, bukan pencegahan (SEC-43/46) |
| Sink Loki 400 hari + alert | Penghapusan jejak di Odoo tanpa ketahuan | Loki bukan WORM; admin host dapat mematikan Promtail (terlihat sebagai target hilang) |
| Salinan ClickHouse append-only + rekonsiliasi id | Penghapusan di luar GC terdeteksi; integritas berantai | Belum selesai; konflik dengan erasure PII → Q-P9 |

Object storage: klien (BFF, ocr-worker, WAL-G, Prometheus) → `s3-gateway` (HAProxy, TLS, menolak retensi/legal-hold/object-lock, bypass-governance, unsigned trailer, header replikasi, `?select`, presigned, admin API, query/path ter-encode) → SILO di network internal `objstore`. Hanya `minio-init` (root acak) yang menjangkau storage langsung. Detail: ADR-030 "Status implementasi", `docs/04 §8.14`.

## 6. Konvensi Repo

```
efn-platform/
├── docs/                    # dokumen bisnis & arsitektur, ADR
├── infra/
│   ├── compose/             # docker-compose base + override dev/prod
│   ├── traefik/             # konfigurasi static & dynamic
│   ├── postgres/init/       # init DB platform
│   └── observability/       # prometheus, loki, promtail, grafana provisioning
├── services/
│   ├── odoo/                # Dockerfile, odoo.conf, addons/efn_*
│   ├── api-gateway/         # NestJS BFF
│   ├── superapp-web/        # Next.js
│   ├── ai-ocr-worker/       # Python worker (RabbitMQ -> OCR -> Odoo)
│   └── etl/                 # Python ETL PostgreSQL -> ClickHouse
├── scripts/                 # deploy, backup, restore
└── .github/                 # workflows, dependabot, CODEOWNERS
```
