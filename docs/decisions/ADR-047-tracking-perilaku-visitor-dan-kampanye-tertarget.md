# ADR-047: Tracking perilaku visitor (device ID first-party + consent `tracking`), segmen, dan kampanye tertarget

- **Status:** Proposed — belum disetujui klien. G46 boleh dibangun mengikuti ADR ini dengan
  `BEHAVIOR_TRACKING_ENABLED=false` dan `PROMO_CAMPAIGN_ENABLED=false` default; **production OFF sampai DPO** menyetujui
  dasar pemrosesan, teks banner consent, DPIA tracking, dan retensi (Q-SM16, Q-SM17, Q-SM26).
- **Tanggal:** 2026-11-18 (Gelombang 0 Supermarket)
- **Pemutus:** pemilik proyek (tujuan bisnis), DPO/Legal (consent, profiling, retensi), Marketing (approval kampanye,
  cap), Finance (anggaran voucher), IT + Security
- **Referensi review:** R-L01 (UU PDP), R-B05 (Price Hunter sebagai sumber harga pasar), R-S04 (enumeration); risiko
  baru **R-L13** (PDP tracking device ID / profiling), **R-B17** (spam & anggaran promo); Q-SM16, Q-SM17, Q-SM24, Q-SM25,
  Q-SM26 (`docs/04` §28.12)
- **Terkait:** ADR-003 (ETL 15 menit; tanpa tulis API → ClickHouse), ADR-007 (Price Hunter di PG), ADR-022, ADR-024,
  ADR-029 (member opak), ADR-044 (WA unofficial), ADR-048 (email), `docs/04` §18 (promo push), `PDP_CONSENT.md`,
  `PDP_DSAR_DESIGN.md`

> **Ringkasan manajemen.** Aplikasi dan web mencatat perilaku belanja (produk yang dilihat dan berapa lama, wishlist,
> keranjang yang ditinggal, pencarian tanpa hasil, minta dikabari bila stok ada) **hanya bila pengunjung menyetujui**
> lewat banner. Identitas yang dipakai adalah kode acak perangkat, yang **disambungkan ke akun hanya saat login dan bila
> persetujuan "pelacakan" aktif**. Dari data itu sistem menebak alasan tidak membeli (stok habis, harga lebih mahal dari
> pasar/kebiasaan) dan **mengusulkan** segmen + voucher; **marketing yang memutuskan** kampanye. Data mentah dihapus
> setelah 90 hari, ringkasan anonim disimpan 2 tahun, dan semuanya ikut terhapus bila member menghapus akun. Mati di
> production sampai DPO menyetujui. Biaya ±5–7 minggu-engineer (BFF, web/app, data, Odoo), Rp0 lisensi.

## 1. Konteks

### 1.1 Kebutuhan (keputusan pemilik proyek 18 Nov 2026)
- Event: `product_view` + durasi, wishlist, cart/abandonment, pencarian tanpa hasil, notify-me.
- Device ID anonim, ditautkan saat login (perlu banner consent). Alasan tidak beli diinferensi dari OOS saat dilihat dan
  harga vs Price Hunter/kebiasaan, plus price alert member. Retensi mentah 90 hari, agregat anonim 2 tahun, ikut hak hapus.
- Promo personal: sistem mengusulkan segmen + voucher, marketing menyetujui. Kanal push, inbox/voucher in-app, email,
  WhatsApp unofficial (ADR-044). Frekuensi & cap anggaran configurable, **default nonaktif**.

### 1.2 Pertentangan dengan yang berlaku
| Sumber | Bunyi | Pertentangan / konsekuensi |
|---|---|---|
| `CLAUDE.md` / ADR-003 | "ClickHouse hanya turunan via ETL incremental 15 menit dari replica … Tidak ada tulis langsung API → ClickHouse." | Brief menyebut "RabbitMQ `behavior.event` → ClickHouse". Agar patuh, consumer menulis ke **tabel staging PostgreSQL** (retensi 7 hari), ETL membawa ke ClickHouse (§3.3). Tidak ada pengecualian ADR-003 baru. |
| `consent.service.ts` | `CONSENT_PURPOSES = ['location','marketing','privacy']` | Purpose baru **`tracking`** (default tidak diberikan). Teks consent baru = versi baru, butuh persetujuan Legal. |
| UU PDP (penafsiran architect) | Pengenal perangkat yang dapat ditautkan ke orang = data pribadi; profiling untuk pemasaran butuh dasar sah + hak keberatan atas keputusan otomatis (Pasal 10 — nomor pasal **belum diverifikasi Legal untuk konteks ini**). | Device ID tidak dianggap "anonim" secara hukum begitu bisa ditautkan; ADR ini memperlakukannya sebagai **pseudonim** (data pribadi). |
| `docs/04` §18 promo push | Push promo ke semua member ber-consent `marketing` | Kampanye tertarget menambah dimensi segmen; consent `marketing` tetap syarat kirim, consent `tracking` syarat *membangun* segmen dari perilaku. |

## 2. Opsi yang dipertimbangkan

### 2.1 Identitas & consent
| # | Opsi | Kelebihan | Kekurangan | Biaya / waktu |
|---|---|---|---|---|
| **A** | **Device ID acak first-party hanya setelah consent `tracking`; tanpa consent → tanpa ID, hanya penghitung agregat tanpa pengenal** | Paling aman PDP; jelas bagi DPO | Data pengunjung yang menolak hilang dari funnel per-orang | **±5–7 minggu-engineer total** (BFF ±2, web+app ±1–1,5, data ±1,5–2, Odoo kampanye ±1–1,5) |
| B | Device ID untuk semua pengunjung (kepentingan sah), consent hanya untuk penautan ke akun | Funnel lengkap | Dasar "kepentingan sah" untuk profiling pemasaran lemah; risiko temuan DPO/regulator | Sama A, risiko hukum lebih tinggi |
| C | Fingerprint perangkat (tanpa cookie) | Tahan hapus cookie | Invasif, sulit di-erasure, ditolak prinsip minimisasi | **Ditolak** |
| D | Analitik pihak ketiga (GA/Firebase/Mixpanel) | Cepat | Transfer lintas negara, data di vendor, bertentangan prinsip self-hosted | **Ditolak** |

### 2.2 Jalur data event
| # | Opsi | Catatan | Keputusan |
|---|---|---|---|
| **a** | BFF → RabbitMQ `behavior.event` → consumer → PG staging `behavior.event_raw` (7 hari) → ETL 15 menit → ClickHouse `fact_visitor_event` (TTL 90 hari) | Patuh ADR-003; PG menahan lonjakan singkat | **Dipilih** |
| b | Consumer menulis langsung ke ClickHouse | Lebih ringan untuk PG, tapi melanggar prinsip; butuh ADR pengecualian | Ditolak (bisa ditinjau bila volume > ±5 juta event/hari) |
| c | Event disimpan permanen di PG | Membebani DB transaksi | Ditolak |

## 3. Keputusan (direkomendasikan)

### 3.1 Consent & device ID
1. Purpose consent baru **`tracking`** di BFF (append-only seperti purpose lain; versi teks + `text_sha256`).
2. **Banner consent** di superapp-web dan Super-App mobile pada kunjungan pertama: "Izinkan pelacakan aktivitas untuk
   rekomendasi & promo" (teks final Legal). Menolak = pilihan setara (tombol sama menonjol), dapat diubah di Profil.
3. **Device ID** = UUIDv4 dibangkitkan klien (`crypto.randomUUID`) **hanya setelah consent**: web cookie first-party
   `efn_did` (`Secure; SameSite=Lax; Path=/; Max-Age` 180 hari, **bukan** HttpOnly agar SDK klien bisa membaca — tidak
   memberi otorisasi apa pun), app = SecureStore. Server **tidak menyimpan device ID mentah**: `device_key =
   HMAC-SHA256(behavior_device_pepper, device_id)`.
4. **Penautan saat login**: bila member login dan consent `tracking` (akun) aktif, BFF mencatat
   `behavior.device_link(member_id, device_key, linked_at, last_seen_at)`. Consent dicabut → tautan dihapus, event
   berikutnya ditolak (klien juga menghapus `efn_did`).
5. Tanpa consent: klien **tidak** mengirim `POST /v1/events`; BFF hanya menaikkan penghitung agregat server-side yang
   sudah ada di jalur katalog (mis. `product_view_count` per produk×outlet×jam, `search_no_result` per istilah yang
   sudah disanitasi) **tanpa pengenal apa pun**.

### 3.2 Event (tanpa PII)
- Jenis: `product_view` (durasi detik, bucket ≤ 600), `wishlist_add/remove`, `cart_add/remove`, `checkout_start`,
  `cart_abandon` (diturunkan server: keranjang tanpa checkout 24 jam, bukan dikirim klien), `search` (hasil 0 →
  `search_no_result`), `notify_me_request`, `price_alert_set`.
- Field allowlist; **tanpa** nama, HP, email, alamat, IP, user agent mentah, lokasi GPS. Istilah pencarian: NFKC,
  lowercase, maks 64 karakter, rangkaian ≥ 6 digit dan pola email **dibuang** (diganti `[redacted]`).
- **Pengayaan server** (tidak dipercaya dari klien): `stock_status` produk saat dilihat (dari cache katalog BFF),
  `price_idr` saat itu, `outlet_code`, `channel`. Inferensi "terlalu mahal" dihitung di mart (harga vs median harga
  pasar Price Hunter tervalidasi 30 hari dan vs median harga beli member pada kategori yang sama).
- Rinci skema & batas: `docs/04` §28.6.

### 3.3 Penyimpanan & retensi
| Lokasi | Isi | Retensi | Erasure |
|---|---|---|---|
| PG `behavior.event_raw` (partisi harian) | event tervalidasi + `subject_key` | **7 hari** (drop partisi) | delete by `subject_key` |
| PG `behavior.device_link` | member ↔ `device_key` | sampai cabut consent / erasure / 180 hari tanpa aktivitas | hapus baris |
| ClickHouse `fact_visitor_event` | event + `subject_key` (pseudonim) | **TTL 90 hari** | `DELETE` ringan by `subject_key` (job erasure ETL) |
| ClickHouse `agg_visitor_*` | agregat **tanpa** `subject_key` (produk×outlet×hari, funnel, istilah pencarian dengan ≥ 10 subjek) | **2 tahun** | tidak perlu (anonim) — ambang k dikonfirmasi DPO (Q-SM17) |
| ClickHouse/BFF `segment_member` snapshot | `segment_run_id`, `subject_key` | 30 hari | delete by `subject_key` |

`subject_key = HMAC-SHA256(behavior_subject_pepper, member_id)` bila tertaut, selain itu `device_key`. Pepper hanya di
BFF dan ETL-erasure (bukan di forecast/insight). Erasure akun (`src/privacy`) memanggil hook behavior: hapus tautan,
event staging, publikasi `privacy.erasure.behavior` (atau job ETL harian membaca daftar `subject_key` terhapus).

### 3.4 Segmen & kampanye
```mermaid
sequenceDiagram
  autonumber
  participant CH as ClickHouse (mart)
  participant S as segment-worker (efn-data)
  participant B as BFF
  participant O as Odoo efn_campaign
  participant M as Marketing
  S->>CH: hitung segmen harian (viewed-not-bought, wishlist-idle, OOS-viewed, price-sensitive, search-no-result)
  S->>B: POST /internal/v1/segments/{run_id}/members (subject_key, mTLS)
  S->>O: efn_campaign_proposal_upsert (json2): ukuran segmen, kriteria, usulan voucher — tanpa data member
  M->>O: setujui / ubah / tolak (4-eyes bila anggaran > ambang)
  B->>O: efn_campaign_claim (pull dari outbox Odoo) → kampanye approved + kode voucher sale_loyalty
  B->>B: segmen → member, cek consent marketing, cap frekuensi/anggaran (bila aktif), flag kanal
  B->>B: inbox/voucher in-app; publish notify.push / notify.email / notify.wa_unofficial
```
- **Odoo tidak publish langsung ke RabbitMQ** (belum ada modul efn yang melakukannya, fit-gap G45 S-09): kampanye
  disetujui dan markdown "kirim ke member" ditulis ke **outbox Odoo** (`efn.campaign.outbox`), BFF mengklaimnya lewat
  `/json/2` (pola `efn_promo_push_claim`, `docs/04` §18.1) lalu BFF yang menerbitkan `promo.campaign.requested`.
- Odoo **tidak menerima data member** (rekomendasi architect): hanya ringkasan segmen dan kode voucher (`loyalty.program`
  tipe kupon CE, **kode tanpa partner** — POS CE menerima kupon dengan partner kosong, fit-gap S-17). Penugasan kode ke
  member di BFF (`platform.member_voucher`). ⚠ Fit-gap G45 §5 mengusulkan `loyalty.card.partner_id` per member (wizard
  CE mode `selected`); itu memasukkan identitas member ke Odoo dan butuh partner per member. Rekomendasi architect tetap
  kode tanpa partner; keputusan akhir oleh efn-lead bersama efn-odoo-functional sebelum G46 (dicatat di laporan).
- Cap frekuensi (`CAMPAIGN_MAX_PER_MEMBER_PER_WEEK`) dan anggaran (`budget_idr` per kampanye, Σ nilai voucher) =
  **configurable, default nonaktif** (keputusan pemilik proyek). Catatan architect: karena nonaktif, pengaman yang tersisa
  adalah approval marketing per kampanye + batas teknis kanal (ADR-044 §3 no. 9) — dicatat sebagai R-B17.
- Markdown near-expiry dengan opsi "kirim ke member" (G45) masuk jalur yang sama sebagai kampanye bertipe `markdown`
  (segmen = member ber-consent yang pernah membeli/wishlist kategori itu di outlet terkait).

## 4. Konsekuensi

### 4.1 Positif
- Promo tepat sasaran tanpa pihak ketiga; data tetap di infrastruktur EFN.
- Patuh prinsip ETL (PG → CH) dan minimisasi (tanpa PII di event, Odoo tanpa data member).

### 4.2 Negatif / utang
- Funnel tidak lengkap karena pengunjung tanpa consent tidak dilacak per orang (diterima demi PDP).
- PG platform menanggung tulis event (estimasi simulasi: 5 outlet × ±2.000 member aktif × ±50 event/hari ≈ 0,5 juta
  baris/hari — angka asumsi, diukur di G48); batch insert consumer + partisi harian.
- Erasure di ClickHouse memakai DELETE ringan (biaya mutasi); dijalankan batch harian, bukan per permintaan.

### 4.3 Dampak ke dokumen
- `docs/04` §28.6–§28.9; `PDP_DATA_INVENTORY.md` §2.8.12; `PDP_CONSENT.md` (purpose `tracking`) dan `DPIA_DRAFT.md`
  (bagian tracking) — **belum** diperbarui, tugas architect setelah DPO memberi arah.

## 5. Risiko terkait
| ID | Ringkas | Mitigasi |
|---|---|---|
| **R-L13** (baru) | Tracking/profiling tanpa dasar sah atau melebihi tujuan; device ID dianggap anonim padahal tertaut | Consent `tracking` sebelum ID dibuat, tanpa PII di payload, pepper, retensi 90 h, erasure, OFF prod sampai DPO + DPIA |
| **R-B17** (baru) | Promo berlebihan / anggaran voucher bocor | Approval marketing, 4-eyes di atas ambang, cap configurable, batas teknis kanal, laporan kampanye |
| R-L01 | PDP umum | Inventaris §2.8.12, DSAR export memuat wishlist/alert/tautan device |
| R-S04 | Penyalahgunaan endpoint publik | Rate limit per device_key + IP, batas batch, tanpa respons yang membocorkan status member |

## 6. Pertanyaan terbuka (dijawab klien)
- **Q-SM16** (DPO) Dasar pemrosesan tracking = consent `tracking` (rekomendasi) dan teks banner; apakah penghitung
  agregat tanpa pengenal (§3.1 no. 5) boleh tanpa consent.
- **Q-SM17** (DPO) Retensi mentah 90 h / staging 7 h / agregat 2 th; ambang k-anonim agregat (usulan ≥ 10 subjek).
- **Q-SM24** (Marketing + Finance) Nilai awal cap frekuensi & anggaran bila diaktifkan; ambang 4-eyes kampanye.
- **Q-SM26** (DPO) Inferensi "alasan tidak beli" & segmen = profiling: perlu pemberitahuan khusus dan hak keberatan?
  (usulan: halaman "Kenapa saya mendapat promo ini" + opsi keluar dari personalisasi = cabut `tracking`).

## 7. Kapan ditinjau ulang
- Volume event > ±5 juta/hari (evaluasi opsi 2.2 b dengan ADR pengecualian).
- DPO menolak consent sebagai dasar atau meminta retensi lebih pendek.
- Sebelum aktivasi production; paling lambat **31 Mar 2027**.
