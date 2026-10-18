# ADR-022: Enkripsi In Transit & At Rest di Setiap Transaksi Data

- **Status:** Proposed — **instruksi klien 5 Okt 2026: WAJIB.** Prinsip "enkripsi di setiap transaksi data" tidak menunggu tanda tangan; yang perlu ditandatangani adalah *cara* (opsi PKI, cakupan kolom PII, matriks kunci, dan anggaran dampak performa).
  - **Diputuskan klien 6 Okt 2026:** (1) **Kustodi kunci = hanya peran Superadmin** (menjawab Q-E1, lihat "Kustodi kunci"); (2) **mTLS sesuai rekomendasi** — wajib untuk hop tanpa kredensial aplikasi (menjawab Q-E4, Keputusan 2; kontrak `docs/04 §8.5`).
  - **Masih Proposed:** opsi PKI A1, ~~cakupan PII Odoo (Q-E3), syarat LUKS/AES-NI pengadaan (Q-E2)~~ (diputuskan 10 Okt, lihat butir berikut), pengecualian E1–E13 (E12 dipersempit ke satu endpoint, Gelombang 12), rekomendasi mitigasi bus factor di bawah.
  - **Diputuskan pemilik proyek 10 Okt 2026:** **Q-E1a/b/c** = tetap **1 orang Superadmin** + **salinan offline tersegel di brankas Direksi** (Shamir 2-dari-3 tidak dipilih); risiko bus factor **diterima dan dicatat** (R-B11). **Q-E2** = LUKS + AES-NI **wajib** di pengadaan. **Q-E3** = kolom PII Odoo **dienkripsi** (pola AES-GCM key ring seperti BFF) — pekerjaan baru W-08 (terkait profil pembeli Q-P21 dan HP member). Opsi PKI A1 dan pengecualian E1–E13 tetap Proposed.
  - **Status implementasi (6 Okt 2026, Gelombang 11):** PKI 3 tingkat, TLS verify-full semua hop, mTLS + allowlist CN di sidecar/observability, kolom PII AES-GCM, SSE-S3, WAL-G libsodium, s3-gateway terbukti di **stack dev** (smoke E2E 108/108, negatif enkripsi `g` 19/19). LUKS, ClickHouse encrypted disk di host produksi, dan rotasi intermediate di staging **belum** terverifikasi (belum ada staging).
- **Tanggal:** 2026-10-05 (revisi 2026-10-06)
- **Pemutus (klien):** IT (Head IT) + Security + Legal/DPO (untuk cakupan PII)
- **Referensi review:** R-L01, R-S01, R-S03, R-T08; `docs/reviews/SECURITY_REVIEW_2026-10-05.md` SEC-02, SEC-05, SEC-11, SEC-16, SEC-18
- **Terkait:** `docs/04_SERVICE_CONTRACTS.md` §7 (kontrak teknis yang mengikat), ADR-004 (topologi 4 node), ADR-014 (AI di jaringan privat), ADR-023 (akses Odoo via WireGuard), ADR-024 (kredensial per service), `docs/runbooks/tls-cert-expiry.md`, `docs/runbooks/secret-rotation.md`

> **Ringkasan manajemen.** Atas instruksi klien, semua data yang berpindah antar-komponen platform dienkripsi (TLS dengan sertifikat yang diverifikasi), dan semua data yang tersimpan — database, foto, backup, disk server, gudang data analitik — juga dienkripsi. Data paling sensitif (nomor HP, IP) dienkripsi lagi di level aplikasi sehingga tetap aman walau salinan database bocor. Kami memilih CA internal sederhana berbasis skrip (tanpa server tambahan) karena platform hanya 4 node. Biaya lisensi: **Rp0** (semua open source); biaya: ±3–4 minggu kerja devsecops + 1 minggu BFF, overhead CPU diperkirakan 3–8%. Tidak ada pengecualian yang berlaku tanpa dicatat di tabel "Pengecualian terdaftar" ADR ini.

## Konteks
- Instruksi klien (5 Okt 2026): **enkripsi di setiap transaksi data**. `CLAUDE.md` kini menjadikannya prinsip yang tidak boleh dilanggar.
- Kondisi scaffold Fase 0 (security review): semua hop internal plaintext (`redis://`, `amqp://`, `http://minio:9000`, `http://api-gateway:3000`), Redis tanpa auth (SEC-05), nomor HP plaintext di `platform.price_hunter_report.user_id` dan di payload event (SEC-11), backup WAL-G sudah terenkripsi libsodium tetapi bucket MinIO tidak SSE, disk host belum LUKS.
- `docs/02 §5` lama: *"Odoo, PostgreSQL, Redis, … berada di network Docker internal tanpa published port"* — isolasi jaringan dianggap cukup. Tidak lagi: satu container terkompromi di network `data` dapat menyadap/menyuntik trafik plaintext (lihat skenario SEC-02/SEC-05).
- Kontrak teknis sudah ditetapkan lead di `docs/04 §7` (v2). ADR ini memberi **alasan, opsi, manajemen kunci, dan mekanisme pengecualian** untuk kontrak tersebut.

## Lingkup: setiap hop dan setiap penyimpanan

### Hop in transit
| # | Dari → ke | Mekanisme | Verifikasi |
|---|---|---|---|
| T1 | Browser/app → Cloudflare | TLS publik (CF) | CA publik |
| T2 | Cloudflare → Traefik | TLS **Full (strict)** + Authenticated Origin Pulls (mTLS CF) | Sertifikat origin ACME/CF Origin CA |
| T3 | Perangkat outlet/staf → Traefik (via WireGuard, ADR-023) | WireGuard **+** TLS (sertifikat ACME DNS-01 publik agar browser POS tidak perlu CA internal) | CA publik |
| T4 | Traefik → Next.js / BFF / Odoo / Metabase / Grafana | HTTPS upstream, `serversTransport.rootCAs` = CA internal | verify-full (nama service) |
| T5 | Next.js → BFF, Odoo → BFF | HTTPS (`NODE_EXTRA_CA_CERTS`, `EFN_CA_FILE`) | verify-full |
| T6 | Aplikasi → PostgreSQL | `sslmode=verify-full` | CA internal |
| T7 | Aplikasi → Redis | `rediss://` + ACL user | CA internal |
| T8 | Aplikasi → RabbitMQ | `amqps://:5671` | CA internal |
| T9 | Aplikasi / WAL-G → MinIO | HTTPS | CA internal |
| T10 | ETL / Metabase / Text-to-SQL → ClickHouse | HTTPS 8443 / native TLS 9440 | CA internal |
| T11 | OCR worker → Ollama | HTTPS via sidecar ghostunnel | CA internal |
| T12 | Prometheus → exporter/target; Promtail → Loki; Grafana → Prometheus/Loki/Alertmanager | HTTPS (`web.config.file` exporter-toolkit; ghostunnel untuk yang tidak mendukung, mis. cAdvisor) | CA internal |
| T13 | BFF → WhatsApp Cloud API, payment gateway, 3PL; Alertmanager → webhook | TLS publik | CA publik (system store) |
| T14 | Antar-node (4 node) & admin SSH | WireGuard mesh (**lapisan kedua**, bukan pengganti TLS) | kunci WG per peer |
| T15 | Replikasi PostgreSQL (fase HA) | TLS `verify-full` | CA internal |

### Penyimpanan at rest
| # | Penyimpanan | Mekanisme |
|---|---|---|
| S1 | Disk semua node (volume Docker: PG, Redis, RabbitMQ, MinIO, ClickHouse, Loki, Odoo filestore) | **LUKS2** (aes-xts-plain64, 512-bit) |
| S2 | Kolom PII platform (`user_enc`, `member_ref_enc`, `ip_enc`, dan kolom PII baru) | **AES-256-GCM di aplikasi** (format `docs/04 §7.2`) |
| S3 | Objek MinIO `market-scout`, `pg-backup` | **SSE-S3** (MinIO KMS single key `minio_kms_key`) + versioning/object lock di `pg-backup` |
| S4 | Backup PostgreSQL (base + WAL) | **WAL-G libsodium** (`walg_libsodium_key`) — terenkripsi sebelum keluar host |
| S5 | ClickHouse | **Encrypted disk** (`<type>encrypted</type>`, AES-128/256-CTR) di atas LUKS untuk tabel mart yang memuat pseudonim (`competitor_prices`, `loyalty_events`) |
| S6 | Backup off-site (wajib sebelum go-live, R-T08) | Sudah terenkripsi oleh S3/S4; transport TLS |
| S7 | Secret di host (`/opt/efn/secrets`) | Di atas LUKS; dir 0700; deploy via stdin (sudah) |

## Opsi yang dipertimbangkan

### A. PKI internal
| Opsi | Kelebihan | Kekurangan | Biaya / waktu |
|---|---|---|---|
| **A1. CA sendiri berbasis skrip** (`openssl`/`step` CLI tanpa server): root offline + intermediate; sertifikat leaf diterbitkan saat deploy | Tidak ada service baru (tidak ada SPOF/target serangan baru), mudah diaudit, cocok untuk ±25 service di 4 node; sudah dimulai (`scripts/gen-pki.sh`) | Rotasi leaf bergantung jadwal deploy/timer; tidak ada ACME/revocation otomatis (diatasi masa berlaku pendek) | 3–5 hari devsecops; Rp0 |
| A2. `step-ca` (Smallstep) sebagai service | ACME internal, renewal otomatis, masa berlaku sangat pendek (24 jam) | Service baru yang harus HA (bila mati, renewal gagal → seluruh platform berhenti), perlu provisioner & agent renewal per container | 1,5–2 minggu; Rp0 (OSS) + beban ops |
| A3. HashiCorp Vault PKI | Juga bisa jadi KMS untuk kunci PII, audit log kuat | Paling berat: unseal, HA (Raft 3 node), backup Vault; lisensi BSL sejak 2023 (OpenBao sebagai fork OSS); butuh keahlian khusus | 3–4 minggu + 1 FTE parsial ops |

### B. Aplikasi tanpa TLS native (Odoo HTTP, Next.js standalone, Ollama, cAdvisor)
| Opsi | Kelebihan | Kekurangan |
|---|---|---|
| **B1. Sidecar `ghostunnel`** dengan `network_mode: service:<app>`; app listen `127.0.0.1` saja | Plaintext hanya di loopback namespace yang sama; image kecil, mendukung mTLS & reload sertifikat | Satu container tambahan per app; healthcheck harus lewat TLS atau loopback |
| B2. Reverse proxy nginx/Caddy per app | Familiar | Konfigurasi lebih banyak, image lebih besar |
| B3. Service mesh (Linkerd/Istio) | mTLS otomatis | Butuh Kubernetes — bertentangan dengan ADR-004 (Compose) |

### C. Enkripsi kolom PII
| Opsi | Kelebihan | Kekurangan |
|---|---|---|
| **C1. AES-256-GCM di aplikasi (BFF)** | Kunci tidak pernah masuk ke PostgreSQL → dump DB, backup, role read-only, log query tidak membuka PII; AAD mengikat ciphertext ke tabel/kolom (tidak bisa dipindah ke kolom lain) | Tidak bisa `WHERE`/`ORDER BY` atas plaintext → pencarian memakai `member_hash` (sudah); setiap service yang butuh plaintext harus memegang kunci |
| C2. `pgcrypto` (`pgp_sym_encrypt`) | Mudah dari SQL | Kunci dikirim di statement SQL → terlihat di `pg_stat_statements`, log slow query (SEC-18), dan oleh superuser; tidak melindungi dari DBA/kompromi DB |
| C3. TDE | Transparan bagi aplikasi | **Tidak ada di PostgreSQL community 16**; hanya fork (Percona `pg_tde` untuk PG17, EDB, Cybertec) → mengganti distribusi DB; hanya melindungi file di disk (setara LUKS), bukan akses SQL |

### D. Antar-node
WireGuard mesh (dipilih, kernel-native, sudah di desain ADR-004) vs IPsec (lebih kompleks) vs hanya TLS (tidak menutup protokol non-TLS seperti SSH/Docker Swarm overlay). WireGuard adalah **lapisan kedua**: TLS tetap wajib di dalam tunnel (defense in depth; WireGuard berhenti di node, bukan di container).

## Keputusan
1. **PKI = A1** (CA sendiri berbasis skrip), karena paling sederhana untuk 4 node dan tidak menambah komponen yang bisa mematikan seluruh platform:
   - **Root CA** ECDSA P-256, masa berlaku 10 tahun, kunci **offline** (2 salinan terenkripsi di brankas, dipegang **peran Superadmin** — keputusan klien 6 Okt 2026, menggantikan usulan awal "Head IT + Security"). Hanya dipakai untuk menandatangani intermediate.
   - **Intermediate CA** per lingkungan (staging, prod), 2 tahun, `pathlen:0`, `nameConstraints` permitted DNS = nama service compose + `.efn.internal` (bila CA bocor, tidak bisa dipakai memalsukan domain publik). Kunci di GitHub Environment secret → dikirim ke host saat deploy (jalur sama dengan secret lain).
   - **Leaf** per service: ECDSA P-256, SAN = nama service compose (+ alias), **masa berlaku 90 hari**, diterbitkan ulang oleh `scripts/deploy.sh` setiap deploy dan oleh timer systemd bulanan bila sisa < 30 hari. Alert `EfnCertExpiringSoon` (runbook `tls-cert-expiry.md`) sebagai jaring pengaman.
   - Klien memverifikasi terhadap **bundle CA** (`efn_ca_crt` = root + intermediate). Rotasi intermediate dilakukan dengan bundle berisi lama + baru selama masa transisi.
   - Ditinjau ulang ke step-ca/cert-manager saat migrasi ke Swarm/k3s (Fase 6) atau > 10 node.
2. **TLS verify-full di semua hop T4–T12, T15**; TLS 1.2 minimum (1.3 bila kedua sisi mendukung), cipher AEAD saja. Server-auth TLS + kredensial per service (ADR-024) sebagai default; **[Diputuskan klien 6 Okt 2026 — Q-E4 "sesuai rekomendasi"]** **mTLS** wajib untuk hop yang tidak punya kredensial aplikasi (exporter metrik, sidecar ghostunnel → app) dan opsional untuk lainnya (ditinjau Fase 2). Tidak ada fallback diam-diam ke plaintext; flag `EFN_ALLOW_PLAINTEXT=true` ditolak di produksi (`docs/04 §7.1`).
3. **Sidecar ghostunnel (B1)** untuk aplikasi tanpa TLS native. Syarat: aplikasi di-bind ke `127.0.0.1` (Odoo `http_interface = 127.0.0.1`, Next.js `HOSTNAME=127.0.0.1`, Ollama `OLLAMA_HOST=127.0.0.1:11435`, sidecar mendengar `:11434` sehingga klien tetap `https://ollama:11434`) sehingga port plaintext tidak terjangkau dari network Docker. Pola ini **bukan pengecualian** karena plaintext tidak pernah meninggalkan network namespace.
4. **WireGuard** antar-node dan untuk outlet (ADR-023).
5. **Kolom PII = C1** (AES-256-GCM di aplikasi, format & AAD `docs/04 §7.2`), dengan *blind index* `member_hash` (HMAC/SHA-256 berpepper) untuk pencarian/dedup. Hanya BFF yang memegang `pii_encryption_key`; OCR worker, ETL, Metabase tidak.
   - **Batas cakupan (bukan pengecualian):** PII di **database Odoo** (`res.partner.phone/email/street`, `pos.order.partner_id`) tidak dienkripsi per kolom karena Odoo harus mencari/menampilkannya; dilindungi oleh LUKS (S1), TLS (T6), backup terenkripsi (S4), record rule multi-company, 2FA (ADR-023), dan minimisasi (Odoo hanya menyimpan nomor HP member yang dibutuhkan transaksi). Ditinjau bila DPO menilai perlu.
6. **At rest**: LUKS2 semua node (S1), MinIO SSE-S3 (S3), WAL-G libsodium (S4, dipertahankan), ClickHouse encrypted disk (S5), retensi sesuai `docs/compliance/PDP_DATA_INVENTORY.md`.

## Manajemen kunci & rotasi
| Kunci / secret | Lokasi | Pemegang (service) | Rotasi | Cara rotasi | Dampak bila hilang |
|---|---|---|---|---|---|
| Root CA key | Offline (brankas, 2 salinan) | — | 10 tahun / bila bocor | Ceremony manual, terbitkan intermediate baru | Tidak bisa menerbitkan intermediate baru (buat root baru + distribusi bundle) |
| Intermediate CA key | GitHub Env secret → host saat deploy | `deploy.sh` saja (bukan container) | 2 tahun | Bundle lama+baru, terbitkan ulang leaf | Sama dengan deploy ulang semua leaf |
| Leaf TLS `tls_<service>_key` | `/run/secrets` | service ybs | 90 hari (otomatis) | Diterbitkan ulang saat deploy/timer; ghostunnel/Traefik reload, app lain restart rolling | Tidak ada (diterbitkan ulang) |
| `pii_encryption_key` | GitHub Env → `/run/secrets` | BFF saja | Tahunan atau bila bocor | **Key ring berversi**: byte versi `0x01`, `0x02`…; BFF mendekripsi semua versi yang ada, mengenkripsi dengan versi terbaru; job re-encrypt batch lalu versi lama dicabut. Secret berbentuk daftar (`pii_encryption_keys`: `ver:base64` per baris) — **usulan perubahan kontrak**, lihat laporan | **Kehilangan data PII permanen** → salinan offline di brankas wajib |
| `minio_kms_key` | `/run/secrets` (MinIO) | MinIO | Tahunan | `mc admin kms key` / re-encrypt objek (tulis ulang) | Objek tidak dapat dibaca → salinan offline wajib |
| `walg_libsodium_key` | `/run/secrets` (postgres) + brankas | WAL-G | Tidak dirotasi rutin; rotasi = full backup baru dengan kunci baru, simpan kunci lama selama retensi backup | Backup tidak dapat dipulihkan |
| ClickHouse disk key | `/run/secrets` → `from_env`/`key_hex` | ClickHouse | Tahunan | ClickHouse mendukung beberapa kunci (`current_key_id`); data lama tetap terbaca, merge menulis ulang | Mart tidak terbaca → rebuild dari PostgreSQL via ETL (CH adalah turunan) |
| LUKS key per node | Keyslot passphrase (brankas) + keyfile/Tang untuk unlock boot | Host | Bila bocor / staf keluar | `cryptsetup luksAddKey/luksKillSlot` | Disk tidak terbuka → restore dari backup |
| WireGuard private key | `/etc/wireguard` per node/outlet | Host/router | Tahunan / perangkat hilang | Ganti peer, hapus public key lama di hub | Peer tidak terhubung |
| `otp_pepper`, `member_hash_pepper` | `/run/secrets` | BFF (otp), BFF+ETL (member_hash turunan, SEC-16) | **Tidak dirotasi rutin** (mengubah semua hash) | Prosedur migrasi `secret-rotation.md` | Hash tidak cocok → ledger tidak terhubung ke member |

Aturan umum: setiap kunci yang kehilangannya berarti kehilangan data (PII, MinIO KMS, WAL-G, LUKS, root CA) **wajib** punya salinan offline terenkripsi di brankas. Kunci tidak pernah ada di repo, log, atau image (gitleaks + Trivy secret scan sudah ada).

## Kustodi kunci (Q-E1)

### Keputusan — Diputuskan klien 6 Okt 2026
Pemegang kunci untuk **root CA (offline), `pii_encryption_key*`, `walg_libsodium_key`, `minio_kms_key`, `clickhouse_disk_key`, passphrase LUKS, `otp_pepper`/`member_hash_pepper`** adalah **hanya peran Superadmin**. Penerapan teknis mengikuti `docs/04 §8.6`: GitHub Environment `production` hanya dikelola/di-approve Superadmin; `/opt/efn/secrets` milik root (0700) dengan sudo hanya untuk grup `efn-superadmin`; root CA key tidak pernah berada di server. Prosedur rinci: `docs/runbooks/key-custody.md` (milik devsecops).

Yang diputuskan klien adalah **siapa** (peran Superadmin). Jumlah orang dalam peran itu dan cara penyimpanan salinan **belum** diputuskan — bagian berikut adalah rekomendasi.

### Risiko kustodi satu peran (bus factor) — rekomendasi; **diputuskan 10 Okt 2026: 1 Superadmin + salinan tersegel di brankas Direksi, risiko diterima**
> **Keputusan pemilik proyek 10 Okt 2026 (Q-E1a/b/c):** peran Superadmin diisi **satu orang**; salinan offline tersegel disimpan di **brankas Direksi** (satu lokasi). Mitigasi 1 (≥ 2 orang) dan 3 (Shamir) **tidak dipilih**; mitigasi 2 (salinan tersegel + log pembukaan segel), 4 (serah terima + uji pemulihan 2×/tahun), dan 5 (break-glass) **tetap berlaku** (uji pemulihan disaksikan Direksi/Internal Audit karena tidak ada Superadmin kedua) dan menjadi syarat staging publik. Risiko sisa dicatat sebagai **R-B11** (`docs/01` §4.1). Tinjau ulang bila Superadmin cuti panjang/keluar, atau sebelum go-live publik.
| Risiko | Skenario | Dampak |
|---|---|---|
| R-K1 Bus factor = 1 | Peran Superadmin hanya diisi satu orang yang sakit/keluar/tidak dapat dihubungi | Tidak bisa rotasi kunci, restore backup (WAL-G), membuka disk LUKS setelah reboot, menerbitkan intermediate; **kehilangan kunci PII/WAL-G/MinIO KMS = kehilangan data permanen** |
| R-K2 Penyalahgunaan oleh satu orang | Satu orang memegang semua kunci tanpa kontrol kedua | Akses PII/backup tanpa jejak yang dapat diverifikasi pihak lain (risiko UU PDP, R-L01) |
| R-K3 Salinan tunggal | Satu brankas / satu laptop | Kebakaran, pencurian, kerusakan media |

**Mitigasi yang disarankan (untuk tanda tangan klien):**
1. **Minimal 2 orang** berperan Superadmin (mis. Head IT + satu direksi/pejabat yang ditunjuk), tidak bepergian bersamaan; GitHub Environment `production` dengan *required reviewers* ≥ 1 selain pemicu deploy.
2. **Salinan offline tersegel** untuk kunci yang kehilangannya = kehilangan data: dicetak/disimpan di media terenkripsi (mis. USB terenkripsi + kertas) dalam amplop tersegel bernomor, di **dua lokasi fisik** berbeda; log pembukaan segel.
3. **Shamir Secret Sharing** (mis. `ssss` atau `age` + pemecah share; skema **2-dari-3**) khusus untuk **root CA key** dan **`pii_encryption_key*`**: share dipegang 2 Superadmin + 1 pihak tepercaya (notaris/komisaris/brankas bank). Tidak ada satu orang pun yang dapat merekonstruksi sendirian, dan hilangnya satu share tidak menghilangkan kunci.
4. **Prosedur pewarisan/serah terima**: saat Superadmin keluar — rotasi kunci yang dapat dirotasi (PII key ring, LUKS keyslot, WireGuard, secret GitHub), cabut akses sudo/GitHub, buat share Shamir baru; dicatat berita acara. Uji pemulihan (restore backup + rekonstruksi share) **minimal 2× setahun** oleh orang yang berbeda.
5. **Break-glass**: akun darurat tersegel dengan alarm saat dipakai (alert + notifikasi ke semua Superadmin).

Biaya: Rp0 lisensi (alat open source); ±2–3 hari devsecops untuk runbook + uji; biaya brankas/notaris (bila dipakai) di luar platform.

## Dampak performa & biaya (perkiraan, belum diukur)
| Area | Overhead | Mitigasi |
|---|---|---|
| TLS antar-service | Handshake ECDSA ~1 ms; throughput AES-GCM dengan AES-NI hampir gratis; CPU total +1–3% | Connection pooling/keep-alive (pg pool, ioredis, amqplib satu koneksi, undici keep-alive); session resumption |
| PostgreSQL TLS | +2–5% latency query pendek | Pool; Odoo `db_maxconn` tetap |
| WireGuard | 3–10% throughput antar-node | Kernel module; MTU 1420 |
| AES-GCM kolom PII | Mikrodetik per baris | Hanya kolom PII, bukan seluruh baris |
| LUKS | 1–5% I/O dengan AES-NI | Pastikan VPS mengekspos flag `aes` (cek `grep aes /proc/cpuinfo`) — **syarat pengadaan** (ADR-004) |
| ClickHouse encrypted disk | 5–15% CPU baca kolom terenkripsi | Hanya tabel berpseudonim; mart agregat di disk biasa (di atas LUKS) |
| MinIO SSE | <5% | — |
| **Biaya tenaga** | devsecops 3–4 minggu (PKI, sidecar, TLS semua service, LUKS runbook, CH disk); BFF 1 minggu (PII crypto, key ring, re-encrypt job); QA 3–5 hari (test TLS negatif) | Masuk Gelombang 3 Fase 0 |
| **Biaya lisensi / infra** | Rp0 lisensi; +~5% CPU → biasanya masih dalam sizing ADR-004; router WireGuard outlet dihitung di ADR-023 | — |

## Konsekuensi
- **Positif:** memenuhi instruksi klien dan kewajiban "langkah teknis pelindungan" UU PDP; kompromi satu container tidak lagi berarti penyadapan seluruh trafik; dump/backup bocor tidak membuka nomor HP; menutup sebagian SEC-02/05/11/16/18.
- **Negatif / utang teknis:** lebih banyak secret (per-service cert, kunci) → otomatisasi `deploy.sh` & `secrets.list` wajib; debugging lebih sulit (tidak bisa `tcpdump` plaintext) → log terstruktur; kunci yang hilang = data hilang → disiplin brankas; CA berbasis skrip tidak punya revocation (diterima karena masa berlaku 90 hari).
- **Dampak dokumen:** `docs/02 §5` (zona TLS/WireGuard) — diperbarui; `docs/03` bagian arsitektur keamanan — diperbarui; `docs/04 §7` adalah kontrak teknisnya; runbook baru yang diusulkan untuk devsecops: `pki-issue-rotate.md`, `luks-unlock.md`, `pii-key-rotation.md`.

## Pengecualian terdaftar
Satu-satunya jalan untuk hop/penyimpanan tanpa enkripsi. Diisi oleh **efn-devsecops-engineer** (pengusul) dan disetujui lead + security reviewer, lalu ditandatangani klien. Pengecualian tanpa baris di tabel ini = pelanggaran yang memblokir merge.

Sumber: laporan `efn-devsecops-engineer` Gelombang 3 §G3.3, Gelombang 5 §G5.3, Gelombang 9 §G9.1 dan Gelombang 11 §G11.4 (dicatat oleh efn-qa-engineer, Gelombang 4 dan 6; pembaruan architect Gelombang 12, 6 Okt 2026: E12 dipersempit ke ocr-worker `/metrics` saja, catatan hop s3-gateway/Loki→Alertmanager/sink audit). Status: **diusulkan** kecuali dinyatakan lain — menunggu persetujuan lead, security reviewer, dan klien. Tanggal tinjau = ≤ 6 bulan dari 2026-10-05 (E11–E13: dari 2026-10-06).

| ID | Hop / penyimpanan | Alasan teknis | Kontrol kompensasi | Pemilik | Kedaluwarsa / tinjau | Disetujui (lead, security, klien) |
|---|---|---|---|---|---|---|
| E1 | Plaintext **loopback** di network namespace app ber-sidecar: Odoo `127.0.0.1:8069/8072`, Next.js `:3001`, Metabase `:3000`, Ollama `:11435`, cAdvisor `:8080`, docker-socket-proxy `:2375` (sidecar ghostunnel ↔ app) | App tanpa TLS native; pola sidecar §7.1 / Keputusan 3 (dicatat untuk transparansi, secara definisi bukan hop jaringan) | App bind 127.0.0.1 (tidak terjangkau container lain); ghostunnel TLS ≥ 1.2 dengan sertifikat CA internal; `render_odoo_conf.py`/compose menolak bind non-loopback tanpa `EFN_ALLOW_PLAINTEXT` (ditolak di produksi); docker-socket-proxy hanya mTLS CN allowlist. **G5:** sisi jaringan SEMUA sidecar kini **mTLS + `--allow-cn`** (Odoo/Next.js: `traefik`; Ollama: `ocr-worker`; + `blackbox-exporter`, lihat E13); `--disable-authentication` ditolak `check-encryption.sh` | efn-devsecops-engineer | 2027-04-05 (atau saat app mendukung TLS native, mis. Next.js custom server) | Diusulkan |
| E2 | Healthcheck Docker HTTP loopback (odoo, superapp-web, metabase, cadvisor, docker-socket-proxy, status ghostunnel `http://127.0.0.1:606x`) | Probe liveness di namespace sendiri, bukan transaksi data; tidak membawa PII/secret | Hanya 127.0.0.1; `scripts/check-encryption.sh` hanya mengizinkan URL http ke 127.0.0.1/localhost | efn-devsecops-engineer | 2027-04-05 | Diusulkan |
| E3 | Healthcheck ClickHouse `wget --no-check-certificate https://127.0.0.1:8443/ping` | busybox wget di image tidak punya opsi CA | Loopback, tanpa data; verifikasi TLS penuh oleh `clickhouse-init` (native TLS strict) dan job Prometheus `probe-internal-tls` | efn-devsecops-engineer | 2027-04-05 | Diusulkan |
| E4 | PostgreSQL socket unix `local all all trust` di dalam container postgres | Entrypoint init, WAL-G backup-push, ops `docker exec` | Bukan TCP; socket tidak dibagikan ke container lain; superuser ditolak lewat TCP (`pg_hba.conf`); akses `docker exec` = akses root host | efn-devsecops-engineer | 2027-04-05 | Diusulkan |
| E5 | Distribusi Erlang RabbitMQ (25672) + epmd plaintext | Dipakai CLI `rabbitmqctl` / `rabbitmq-diagnostics` (healthcheck) | Bind 127.0.0.1 (`inet_dist_use_interface`, `ERL_EPMD_ADDRESS`); cookie Erlang di volume container | efn-devsecops-engineer | 2027-04-05 | Diusulkan |
| E6 | Loki gRPC/memberlist, Promtail HTTP/gRPC | Komponen internal single binary | Bind 127.0.0.1; push Promtail → Loki tetap HTTPS terverifikasi; log sudah diredaksi (nomor HP, Bearer) | efn-devsecops-engineer | 2027-04-05 | Diusulkan |
| E7 | CI job `odoo-test`: PostgreSQL service tanpa TLS | DB efemeral di runner CI, data sintetis (nomor fiktif) | Hanya di runner GitHub; tidak ada data nyata/secret produksi; job integrasi TLS penuh diusulkan di `docs/testing/TEST_STRATEGY.md` | efn-qa-engineer + efn-devsecops-engineer | 2027-04-05 | Diusulkan |
| E8 | Dev: sertifikat publik Traefik = default self-signed | Laptop tanpa domain publik/ACME | Dev only; hop internal dev tetap TLS PKI (`gen-pki.sh`); prod memakai ACME | efn-devsecops-engineer | 2027-04-05 | Diusulkan |
| E9 | Cloudflare edge → origin | Pengaturan di dashboard Cloudflare, tidak bisa dicek dari repo | Wajib mode **Full (strict)** + Authenticated Origin Pulls; firewall origin 443 hanya dari Cloudflare + WireGuard (`docs/runbooks/origin-firewall.md`); probe `probe-https` memantau sertifikat publik | efn-devsecops-engineer (ops) | 2027-04-05 | Diusulkan |
| E10 | Healthcheck Docker dinonaktifkan untuk prometheus, alertmanager, grafana, loki, exporter | Endpoint hanya TLS dan image tanpa klien ber-CA (trade-off observability, bukan hop plaintext) | Metrik `up` + alert `EfnServiceDown`, `probe-internal-tls`, Uptime Kuma | efn-devsecops-engineer | 2027-04-05 | Diusulkan |
| E11 | ClickHouse `/metrics` (8443) **server-TLS saja**, tanpa sertifikat klien | Verifikasi sertifikat klien Poco berlaku global per port; ETL/Metabase memakai password tanpa sertifikat di port yang sama | Network `data` saja; hanya metrik server agregat tanpa PII; rencana fase 2: `predefined_query_handler` FORMAT Prometheus + user baca-saja | efn-devsecops-engineer | 2027-04-06 | Diusulkan |
| E12 | **Dipersempit (Gelombang 12):** kini hanya ocr-worker `/metrics` :9100 — mTLS tanpa allowlist CN/SAN (`prometheus-client` hanya `client_auth_required`, `efn_ocr/metrics.py:7-9`). Komponen lain **tertutup**: Loki → `127.0.0.1:3101` + sidecar ghostunnel `loki-tls` `--allow-cn=promtail,grafana,prometheus`; redis_exporter, RabbitMQ metrics 15691, Traefik metrics :8082 → loopback + ghostunnel `--allow-cn=prometheus` (G9, SEC-35 Fixed r4; uji runtime: sertifikat ocr-worker ditolak `bad certificate` di keempatnya, `docs/04 §8.14`); BFF `/metrics` :9464 CN `prometheus` (G5) | Keterbatasan `prometheus-client` | EKU clientAuth hanya untuk 8 klien (traefik, prometheus, grafana, promtail, ocr-worker, blackbox-exporter, cadvisor, loki); endpoint hanya metrik antrean/OCR tanpa PII; network `data`. **Penutupan penuh (±0,5 hari devsecops):** pola yang sama — worker bind `127.0.0.1` + sidecar ghostunnel `--allow-cn=prometheus` | efn-devsecops-engineer + efn-data-ai-engineer | 2027-04-06 | Diusulkan (sisa 1 endpoint) |
| E13 | `blackbox-exporter` diizinkan CN di sidecar Odoo/Next.js/Metabase/Ollama (probe kesehatan/handshake), melebihi daftar klien §8.5 | Probe ketersediaan & masa berlaku sertifikat tanpa melewati Traefik | Blackbox hanya dapat dipicu Prometheus (web-config SAN `prometheus`), target statis, read-only; bila dicabut: hapus `--allow-cn=blackbox-exporter` + target probe terkait | efn-devsecops-engineer | 2027-04-06 | **Disetujui lead** (docs/04 §8.10, 6 Okt 2026); menunggu security reviewer & klien |
| E14 | **Kolom PII Odoo (HP member `res.partner`) belum dienkripsi kolom selama pilot retail** (PP-3, 10 Okt 2026) | Enkripsi kolom PII Odoo (W-08, 3–5 minggu) dijadwalkan G19; pilot retail Grand Lucky tidak menunggu | Disk LUKS + AES-NI (Q-E2), TLS verify-full semua hop, Odoo tidak publik (ADR-023), ir.rule multi-company, audit akses efn_audit, HP di log dimasker; data online pembeli (Q-P21) BELUM boleh disimpan sampai W-08 selesai | efn-odoo-engineer (W-08) | W-08 selesai (G19) | **Disetujui pemilik proyek** (PP-3) |

### Catatan hop baru Gelombang 8–11 (bukan pengecualian — dicatat agar lingkup T/S lengkap)
| Hop / komponen | Mekanisme | Verifikasi | Status bukti |
|---|---|---|---|
| T9a Klien (BFF, ocr-worker, WAL-G, Prometheus) → **`s3-gateway`** (HAProxy filter L7, ADR-030) | HTTPS sertifikat `tls_s3_gateway_*` dari CA internal; access key acak per klien (`docs/04 §8.12`) | verify-full di klien | Runtime G9/G11 (smoke `g` 19/19, termasuk 24 varian encoded SEC-41) |
| T9b `s3-gateway` → storage `minio` (SILO) | Re-encrypt TLS `ssl verify required ca-file … verifyhost minio`; storage hanya di network internal `objstore` (tidak ter-resolve dari `data`) | verify-full | Runtime G9. Ini **bukan** pola E1: hop tetap TLS antar-container |
| T12a Loki ruler → Alertmanager | HTTPS **mTLS**, sertifikat `loki` ber-EKU server+client; Alertmanager `web-config-alertmanager.yml` allowlist `prometheus`, `grafana`, `loki` | verify-full + CN allowlist | Runtime G11 (alert sintetis + 3 alert nyata dari Odoo) — `docs/04 §8.15` |
| T12b Odoo stdout → Promtail → Loki (stream `efn_audit_sink`) | Baris log container dibaca Promtail lewat docker-socket-proxy (mTLS, E1) lalu push HTTPS mTLS ke `loki-tls` | verify-full + CN allowlist | Runtime G11. Payload sink tanpa nilai lama/baru dan tanpa PII |
| S3a Root object storage | `minio_root_user` + password acak dari secret (SEC-48) | — | Runtime G11 |
| S8 (baru) Salinan audit append-only di ClickHouse | Encrypted disk (S5) + user ETL tanpa `ALTER/DELETE`, `row_hash` berantai | — | **Sedang dibangun data-AI; belum terverifikasi** |

### Sisa pekerjaan (bukan pengecualian; belum memenuhi Keputusan di atas)
| # | Pekerjaan | Kondisi saat ini | Pemilik | Target |
|---|---|---|---|---|
| W1 | ~~Rantai PKI root offline → intermediate per lingkungan → leaf 90 hari~~ **SELESAI (kode, G5)** | `scripts/gen-pki.sh` 3 tingkat (`root` offline, `intermediate <env>` + nameConstraints, `leaf` 90 hari diterbitkan di host oleh `deploy.sh`); `efn_ca_crt` = bundle intermediate+root; CI memverifikasi rantai, leaf ≤ 90 hari, EKU, penolakan leaf di luar nameConstraints | efn-devsecops-engineer | Verifikasi runtime di staging |
| W2 | ~~ADR-023 entrypoint `wg-https` + ACME DNS-01~~ **SELESAI (kode, G5)** | Router `erp.`/`bi.`/`ops.`/`status.` hanya di entrypoint `wg-https` (`EFN_WG_BIND:443`), resolver `letsencrypt-dns` (secret `acme_dns_api_token`; deploy menolak placeholder); `internal-only` = outlet 10.88/16 + staf 10.89/16, `internal-ops` = staf saja | efn-devsecops-engineer | Verifikasi pilot outlet (UAT G8) |
| W3 | `superapp-web` prod `replicas: 1` | Sidecar ghostunnel (`network_mode: service:`) hanya menempel ke satu container → Super-App tanpa skala horizontal / HA | efn-frontend-engineer (TLS native Next.js) atau devsecops (pasangan service ganda) | Sebelum go-live publik |

Kriteria minimal agar pengecualian dapat diterima: (1) tidak ada data pribadi atau secret yang melintas/tersimpan; (2) trafik terbatas pada network Docker khusus atau loopback; (3) ada tanggal kedaluwarsa ≤ 6 bulan; (4) dipantau (alert/test) agar tidak meluas.

## Risiko terkait
R-L01 (UU PDP), R-S01 (secret), R-S03 (Odoo terekspos), R-T08 (backup/DR — kunci hilang = backup hilang), R-T07 (sertifikat kedaluwarsa = outage; dimitigasi alert + rotasi otomatis).

## Pertanyaan terbuka untuk klien
- ~~**Q-E1** Siapa pemegang brankas kunci?~~ **Dijawab 6 Okt 2026: hanya peran Superadmin.** Turunan yang masih terbuka: **Q-E1a** berapa orang mengisi peran Superadmin (rekomendasi ≥ 2)? **Q-E1b** setuju Shamir 2-dari-3 untuk root CA & kunci PII, dan siapa pemegang share ketiga? **Q-E1c** lokasi salinan offline kedua? **→ Dijawab 10 Okt 2026:** Q-E1a = 1 orang; Q-E1b = tidak (tanpa Shamir); Q-E1c = brankas Direksi (salinan tersegel). Lihat "Kustodi kunci".
- **Q-E2** Apakah vendor VPS (ADR-004) mendukung LUKS dengan unlock jarak jauh (konsol/KVM, dropbear-initramfs, atau Tang di node lain)? Syarat pengadaan: CPU dengan AES-NI.
- **Q-E3** Apakah DPO meminta enkripsi per kolom juga untuk PII di database Odoo (berdampak pada pencarian pelanggan di POS)? Default: tidak (lihat Keputusan 5).
- ~~**Q-E4** cakupan mTLS~~ **Dijawab 6 Okt 2026: sesuai rekomendasi** — wajib untuk hop tanpa kredensial aplikasi; hop berkredensial (PostgreSQL, Redis, RabbitMQ, MinIO, ClickHouse, BFF API) server-TLS + password per service, mTLS opsional fase 2 (`docs/04 §8.5`).

## Kapan ditinjau ulang
Saat migrasi orkestrasi (Swarm/k3s, Fase 6) → pertimbangkan step-ca/cert-manager dan Vault/OpenBao sebagai KMS; bila jumlah node > 10; setelah insiden kunci; atau tahunan bersama audit PDP.
