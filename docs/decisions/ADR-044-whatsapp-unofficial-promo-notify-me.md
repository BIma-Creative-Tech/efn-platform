# ADR-044: WhatsApp tidak resmi (Baileys) terbatas untuk promo personal & notify-me supermarket — membatalkan sebagian D-11 / ADR-011

- **Status:** Proposed — belum disetujui klien. Pilihan kanal dibuat **eksplisit oleh pemilik proyek (18 Nov 2026)**
  walaupun D-11/ADR-011 melarang. G46 boleh membangun mengikuti ADR ini dengan `WA_UNOFFICIAL_ENABLED=false` default;
  **production tetap OFF** sampai ada **penerimaan risiko tertulis pemilik proyek + Legal** (Q-SM14) dan entri PDP
  disetujui DPO (Q-SM18).
- **Tanggal:** 2026-11-18 (Gelombang 0 Supermarket)
- **Pemutus:** pemilik proyek (penerimaan risiko bisnis), Legal (ToS Meta, UU PDP, UU ITE), DPO (transfer data ke
  WhatsApp/Meta), IT klien (nomor & perangkat terpisah), Security (isolasi container)
- **Referensi review:** D-11, R-S04, R-B02, R-L01; risiko baru **R-B16** (ban nomor/ToS), **R-S21** (supply chain &
  pengambilalihan sesi), **R-B17** (spam/anggaran promo); Q-SM14, Q-SM15, Q-SM18, Q-SM25 (`docs/04` §28.12)
- **Terkait:** ADR-011 (diubah sebagian), ADR-022 (enkripsi), ADR-024 (kredensial per service), ADR-047 (consent &
  segmen), ADR-048 (email), K18-01 di `CLAUDE.md`

> **Ringkasan manajemen.** Pemilik proyek memilih WhatsApp lewat library tidak resmi (Baileys) **hanya** untuk pesan
> promo personal dan "beri tahu saya bila stok ada" di bisnis supermarket, karena biayanya Rp0 per pesan. Risikonya
> nyata: **nomor bisa diblokir Meta kapan saja tanpa banding**, penggunaan ini melanggar ketentuan WhatsApp, dan tidak ada
> SLA. Agar risiko itu tidak merembet, kanal ini dipasang di **container terpisah dengan nomor terpisah**; OTP, pesanan,
> poin, dan semua pesan transaksional tetap lewat WhatsApp resmi. Mati secara default dan **tidak dinyalakan di
> production** sebelum pemilik proyek dan Legal menandatangani penerimaan risiko. Biaya bangun ±1,5–2,5 minggu-engineer;
> biaya berjalan nomor + perangkat ±Rp0,1–0,3 jt/bulan (perkiraan architect, belum divalidasi).

## 1. Konteks

### 1.1 Kebutuhan
- Rencana supermarket (keputusan pemilik proyek 18 Nov 2026, tabel "Requirement terkunci" baris *Promo personal*):
  kanal promo personal = push, inbox/voucher in-app, email, **WhatsApp via Baileys (hanya promo + notify-me)**.
- Volume promo personal (segmen hasil analitik perilaku, ADR-047) jauh lebih besar dari OTP; dengan Cloud API setiap
  pesan kategori MARKETING berbayar per pesan (ADR-011 §Opsi, R-B02).

### 1.2 Pertentangan dengan dokumen yang berlaku
| Sumber | Bunyi | Pertentangan |
|---|---|---|
| `CLAUDE.md` (sebelum revisi ini) | "WhatsApp **Cloud API resmi** saja. Dilarang Baileys / library unofficial." | Langsung bertentangan. Usulan revisi prinsip menjadi pengecualian terbatas (K18-01, §4.3). |
| `CLAUDE.md` Sumber kebenaran butir 5 | "Kesalahan konsep yang sudah diketahui: … Baileys." | Konteksnya Arsitektur v2 yang memakai Baileys untuk **OTP/semua notifikasi**; itu tetap kesalahan. Usulan penyesuaian kalimat (§4.3). |
| ADR-011 §Keputusan | "Baileys/library unofficial **dilarang** di semua environment." | Dibatalkan sebagian: hanya untuk promo & notify-me, terisolasi, OFF di prod sampai penerimaan risiko. |
| D-11 (`docs/01` §2) | WhatsApp = Cloud API resmi. | Tetap berlaku untuk OTP, pesanan, loyalty, transaksional. |
| Ketentuan WhatsApp (ToS) | Melarang akses otomatis/tidak sah ke layanan lewat klien tidak resmi (menurut pemahaman umum ToS; **teks ToS terbaru belum diverifikasi** architect). | Pelanggaran kontraktual dengan Meta → nomor diblokir; potensi klaim. |

## 2. Opsi yang dipertimbangkan

| # | Opsi | Kelebihan | Kekurangan | Biaya / waktu (perkiraan architect) |
|---|---|---|---|---|
| A | Tetap Cloud API untuk promo (template MARKETING) | Resmi, stabil, legal | Biaya per pesan marketing (lebih mahal dari authentication; tarif belum divalidasi untuk 2026) | Bangun ±0,5 minggu (adapter sudah ada); OpEx per pesan × volume promo |
| **B** | **Baileys di service terpisah `wa-unofficial`, nomor terpisah, hanya promo + notify-me, OFF prod sampai penerimaan risiko** | Rp0 per pesan; kegagalan/ban tidak menyentuh OTP & transaksi | Melanggar ToS; ban tanpa peringatan; tanpa SLA; library komunitas (RC, breaking change); sesi = kredensial akun WA | **±1,5–2,5 minggu-engineer** (service Node ±1–1,5, BFF fan-out ±0,5, devsecops ±0,5); nomor + perangkat ±Rp0,1–0,3 jt/bulan |
| C | Baileys di dalam BFF (`api-gateway`) | Tanpa container baru | Library tidak resmi + sesi WA di proses yang memegang kunci PII, ledger poin, payment → blast radius maksimal | Hemat ±3 hari — **ditolak** |
| D | Baileys untuk semua notifikasi termasuk OTP | Paling murah | Ban = OTP kasir berhenti (alasan asli D-11) | **Ditolak** |
| E | Tanpa WhatsApp untuk promo (push + inbox + email saja) | Nol risiko ToS | Jangkauan lebih rendah (member tanpa app) | Rp0 tambahan |

## 3. Keputusan (direkomendasikan, mengikuti pilihan pemilik proyek)

**Opsi B**, dengan batasan wajib berikut. Setiap batasan adalah syarat; melanggar satu berarti kembali ke larangan ADR-011.

1. **Cakupan pesan**: hanya kategori `promo` (kampanye disetujui marketing, ADR-047; markdown near-expiry "kirim ke
   member") dan `notify_restock` / `price_alert` (permintaan eksplisit member). Service **menolak** template lain
   (allowlist kategori di kode + test). OTP, status pesanan, pembayaran, poin, MGM, hangus poin, reservasi = Cloud API.
2. **Service terpisah** `services/wa-unofficial` (Node 22, TypeScript strict), container sendiri, user RabbitMQ sendiri
   (`efn-wa-unofficial`, hanya consume `notify.wa_unofficial`, publish hasil ke `notify.wa_unofficial.result`), tanpa
   akses PostgreSQL platform, Odoo, ClickHouse, Redis BFF. Tidak memegang kunci PII BFF: payload berisi nomor tujuan
   yang **sudah didekripsi BFF** untuk satu pesan (lihat §3.1).
3. **Nomor WhatsApp terpisah** dari nomor/WABA resmi (Q-SM15): bukan nomor yang terdaftar di Meta Business Cloud API,
   tidak ditampilkan sebagai kontak resmi, profil menyebut "Promo <brand>". Ban nomor ini tidak boleh berdampak ke WABA.
4. **Flag** `WA_UNOFFICIAL_ENABLED=false` default di semua environment; `deploy-validate` **menolak** `true` di
   `APP_ENV=production` kecuali `WA_UNOFFICIAL_RISK_ACCEPTANCE_REF` berisi nomor dokumen penerimaan risiko (pola gerbang
   `EFN_ALLOW_FEATURE_ON`, G22). Flag mati → BFF tidak publish `notify.wa_unofficial` sama sekali (kanal dilewati,
   bukan diantre).
5. **Kredensial sesi** (auth state Baileys) disimpan terenkripsi at rest: AES-256-GCM dengan kunci
   `/run/secrets/wa_unofficial_session_key` di volume sendiri; tidak pernah di log, tidak di backup umum tanpa enkripsi
   (ikut WAL-G/volume backup terenkripsi). Pairing QR/kode hanya lewat endpoint admin di jaringan WireGuard staf
   (`wg-https`, `internal-ops`), bukan internet.
6. **Egress** hanya ke domain WhatsApp (allowlist proxy egress; daftar host diverifikasi saat PoC — **belum
   terverifikasi**). Tidak ada egress lain (tidak ke npm saat runtime, tidak ke telemetry).
7. **Kegagalan tidak memengaruhi transaksi**: tidak ada jalur sinkron dari checkout/POS/OTP ke service ini. Ban/putus
   sesi → status `disconnected`/`banned`, consumer berhenti ack-reject ke DLQ dengan alasan, alert
   `EfnWaUnofficialDown`, BFF menandai kanal tidak tersedia dan **tidak** mengalihkan otomatis ke Cloud API (biaya) —
   pengalihan = keputusan marketing per kampanye.
8. **Consent**: hanya member dengan consent `marketing` aktif (promo) atau permintaan notify-me eksplisit + consent
   `marketing` (Q-SM25: apakah notify-me cukup dengan permintaan saja). Setiap pesan promo memuat cara berhenti
   ("balas STOP" diproses sebagai pencabutan consent `marketing` lewat `notify.wa_unofficial.result`).
9. **Batas teknis anti-ban** (bukan cap bisnis): `WA_UNOFFICIAL_RATE_PER_MIN` (default 10), `WA_UNOFFICIAL_MAX_PER_DAY`
   (default 300), `WA_UNOFFICIAL_MAX_PER_MEMBER_PER_DAY` (default 1), jitter acak 2–8 dtk; cap frekuensi & anggaran
   bisnis tetap di kampanye (ADR-047, default nonaktif sesuai keputusan pemilik proyek).
10. **Masking**: nomor di log/metric `62812****789`; isi pesan tidak di log; `message_id` WA disimpan di hasil hanya
    sebagai hash.
11. **Supply chain**: versi library di-pin exact (bukan caret) di `package.json` + lockfile; image dibangun di CI dengan
    SBOM + Trivy (R-S07/R-S08); pembaruan library hanya lewat PR yang dibaca manusia (changelog + diff) karena library
    menangani kredensial akun. Status upstream (per pencarian 18 Nov 2026, sumber sekunder): paket
    `@whiskeysockets/baileys` lisensi MIT, garis 7.x masih *release candidate* (rc13/rc14) dengan breaking change dari
    6.x — **belum diverifikasi dari repo/npm primer**.

### 3.1 Alur
```mermaid
sequenceDiagram
  autonumber
  participant O as Odoo efn_campaign / efn_expiry_monitor
  participant B as BFF (campaign/notify dispatcher)
  participant Q as RabbitMQ
  participant W as wa-unofficial (container terisolasi)
  participant WA as WhatsApp (nomor terpisah)
  B->>O: claim kampanye disetujui (json2, pull)
  B->>B: resolve segmen → member, cek consent marketing, cap, flag
  alt WA_UNOFFICIAL_ENABLED=true dan kanal dipilih
    B->>Q: notify.wa_unofficial {idempotency_key, to (didekripsi per pesan), template, params}
    Q->>W: consume (user efn-wa-unofficial)
    W->>W: allowlist kategori, rate limit, dedup idempotency_key
    W->>WA: kirim pesan
    W->>Q: notify.wa_unofficial.result {idempotency_key, status, to_hash}
    Q->>B: update status kampanye per member; "STOP" → cabut consent
  else flag mati / kanal down
    B->>B: kanal dilewati, catat skipped_reason
  end
```

## 4. Konsekuensi

### 4.1 Positif
- Promo personal & notify-me tanpa biaya per pesan.
- OTP dan transaksi tetap aman dari risiko ban (isolasi nomor + container + queue).

### 4.2 Negatif / utang yang diterima
- **Pelanggaran ToS WhatsApp disengaja** → risiko hukum/kontraktual ditanggung pemilik proyek (dicatat, Q-SM14).
- Nomor dapat diblokir permanen; nomor pengganti membutuhkan pairing ulang dan member melihat nomor baru (kepercayaan
  turun, potensi dianggap penipuan).
- Pemeliharaan library komunitas: perubahan protokol WhatsApp dapat membuat service mati berhari-hari tanpa perbaikan.
- Nomor tujuan didekripsi BFF dan melewati RabbitMQ ke service lain (TLS + kredensial per user); ini memperluas
  permukaan data pribadi dibanding `notify.push` yang hanya membawa `member_hash`. Alternatif (service mendekripsi
  sendiri) ditolak karena memberi kunci PII ke container berisiko tertinggi.
- Pesan dari nomor tidak resmi tidak punya centang bisnis/terverifikasi.

### 4.3 Dampak ke dokumen
- `CLAUDE.md`: prinsip WhatsApp + baris K18-01 — **diusulkan** (teks siap-tempel di
  `docs/agent-reports/efn-architect.md`, bagian Supermarket Gelombang 0); diterapkan efn-lead dengan persetujuan user.
  Sampai diterapkan, prinsip lama di `CLAUDE.md` (larangan Baileys) tetap berlaku untuk implementasi.
- ADR-011: catatan "diubah sebagian oleh ADR-044" (dilakukan).
- `docs/04` §28.8–§28.10 (queue `notify.wa_unofficial`, flag), `docs/01` R-B16/R-S21/R-B17, PDP inventaris §2.8.12.
- `docs/compliance/PDP_TRANSFER_LINTAS_NEGARA.md`: transfer nomor + isi pesan ke WhatsApp (Meta) — **belum** diperbarui
  (menunggu DPO, Q-SM18).

## 5. Risiko terkait
| ID | Ringkas | Mitigasi |
|---|---|---|
| **R-B16** (baru) | Nomor diblokir / ToS Meta / tanpa SLA | Nomor terpisah, batas teknis anti-ban, OFF prod sampai penerimaan risiko, kanal lain tetap (push/inbox/email) |
| **R-S21** (baru) | Library disusupi atau sesi dicuri → nomor diambil alih untuk penipuan atas nama brand | Pin exact + review manusia, SBOM/Trivy, sesi terenkripsi, egress allowlist, container tanpa akses data lain, rotasi (logout + pairing ulang) bila curiga |
| **R-B17** (baru) | Spam/kelebihan anggaran promo | Approval marketing per kampanye, batas teknis harian, cap bisnis configurable (default nonaktif — diterima pemilik proyek) |
| R-L01 | Data pribadi ke pihak ketiga tanpa dasar | Consent marketing, opt-out STOP, masking log, inventaris PDP §2.8.12, DPO Q-SM18 |
| R-B02 | OpEx WhatsApp | Justru diturunkan; risiko berpindah ke R-B16 |

## 6. Pertanyaan terbuka (dijawab klien)
- **Q-SM14** (Pemilik + Legal) Penerimaan risiko tertulis: pelanggaran ToS WhatsApp, kemungkinan ban permanen, tanpa SLA,
  dan potensi klaim. Siapa menandatangani; nomor dokumen diisi ke `WA_UNOFFICIAL_RISK_ACCEPTANCE_REF`.
- **Q-SM15** (IT klien/Marketing) Nomor & perangkat: nomor baru atas nama PT (bukan pribadi), SIM fisik di mana,
  siapa memegang pairing; nomor tidak pernah didaftarkan ke WABA resmi.
- **Q-SM18** (DPO) Dasar & catatan transfer nomor HP + isi promo ke WhatsApp (Meta, lintas negara) tanpa perjanjian
  pemrosesan data (Baileys tidak punya DPA).
- **Q-SM25** (DPO) Notify-me/price alert: cukup permintaan eksplisit member (pelaksanaan permintaan) atau wajib consent
  `marketing`? Default sementara: wajib `marketing` untuk kanal WA unofficial; push in-app cukup permintaan.

## 7. Kapan ditinjau ulang
- Nomor diblokir ≥ 2 kali dalam 90 hari → evaluasi kembali ke opsi A atau E.
- Meta menurunkan tarif marketing atau EFN memenuhi tier volume yang membuat opsi A sebanding.
- Library berhenti dipelihara (tanpa rilis > 6 bulan) atau ada CVE pada versi yang di-pin.
- Sebelum go-live supermarket, dan paling lambat **31 Mar 2027**.
