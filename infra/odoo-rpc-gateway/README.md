# odoo-rpc-gateway — filter L7 BFF → Odoo `/json/2` (penjualan online)

- **Tujuan:** satu-satunya jalur BFF (`api-gateway`) ke Odoo untuk kanal online dan QR order meja. Hanya `POST` ke **20 path persis** (10 path §10.4.3 + `efn_online_counter_refund_authorize`, L-60 +
  `efn_online_set_invoice_party`, G21 §13.5 faktur atas nama pembeli, L-63 + 3 path QR G22 §14.A.5) docs/04; semua bentuk lain ditolak sebelum menyentuh Odoo. Odoo tetap tidak terjangkau dari internet
  maupun langsung dari BFF (`odoo-tls --allow-cn` tidak memuat `api-gateway`).
- **ADR:** ADR-034 (Proposed — kanal online = BFF + `/json/2` terfilter), ADR-022 (enkripsi: mTLS dua hop, verify-full
  CA internal), ADR-023 (Odoo privat), ADR-024 (kredensial per kanal), pola ADR-030 (s3-gateway).
- **Risiko dimitigasi:** R-S03 (permukaan RPC Odoo: ORM generik `search_read`/`write` tidak terjangkau dengan API key
  yang bocor dari BFF), R-T15 (alert & runbook API key), penyalahgunaan sesi/header proxy.
- **Komponen:** HAProxy LTS `3.2.25-alpine` (digest sama dengan s3-gateway), non-root, read-only, `cap_drop: ALL`,
  memori 64M, hanya network `internal`, tanpa label Traefik/port host. `haproxy.cfg` (aturan + alasan),
  `entrypoint.sh` (PEM crt+key ke tmpfs, validasi `ODOO_DB` fail-closed), `test-gateway.sh` (uji nyata).
- **Aturan (ringkas):** mTLS masuk (CA internal, EKU clientAuth, CN persis `api-gateway`) · HTTP/1.1 origin-form ·
  `POST` saja · path mentah = salah satu dari 20 path (tolak `%`, `;`, `//`, segmen titik, query, fragment) · allowlist
  header (`host, content-type, content-length, authorization, accept, accept-encoding, user-agent, connection,
  traceparent, tracestate, x-request-id`; method override/upgrade/transfer-encoding/x-original-url/x-openerp-* ditolak) ·
  Host `odoo-rpc-gateway[:8443]` · `Content-Type: application/json` · `Authorization: bearer <token>` tepat satu ·
  `Content-Length` ≤ 256 KB tanpa chunked · `X-Odoo-Database` dipaksa `${ODOO_DB}` · `Cookie`, `X-Forwarded-*`,
  `Forwarded`, `X-Real-IP`, `Accept-Language`, `Sec-Fetch-*` dibuang · `Set-Cookie` respons dibuang · timeout 30 s ·
  log tanpa body/header/query (`why=<alasan>`, `%Tw` = waktu antre), penolakan membawa `X-EFN-Gateway-Deny`.
- **Kapasitas (SEC-96 / L-16b):** maks. `ODOO_RPC_MAXCONN` (default 4, 1..16, divalidasi entrypoint & deploy-validate)
  koneksi paralel ke Odoo = worker yang disisihkan untuk online; antrean maks. 16 request × 2 s di gateway, lalu `503`
  + `X-EFN-Gateway-Error: unavailable` (`why=busy`, tidak memicu alert penolakan). Sizing worker:
  `docs/runbooks/online-orders.md#sizing-worker`. Uji: `test-gateway-load.sh` (10 Okt: 300 req / 200 paralel → 150
  dilayani, antre maks. 90–123 ms, 150 × 503 seketika, gateway tetap healthy).
- **Catatan protokol:** Odoo (werkzeug) menjawab `HTTP/1.0`; gateway meneruskannya apa adanya. Klien yang menurunkan
  versi request berikutnya ke HTTP/1.0 (curl mode paralel) ditolak `why=target`; BFF (undici) selalu HTTP/1.1.
- **Kontrak untuk BFF:** kirim hanya header di allowlist (undici `fetch` default lolos: `accept-language`/`sec-fetch-mode`
  dibuang gateway). Header baru = ubah allowlist di sini (koordinasi devsecops). Respons dengan header
  `X-EFN-Gateway-Deny` = bug klien (jangan retry).
- **Verifikasi (G17, 9 Okt 2026, stack dev nyata):** `bash infra/odoo-rpc-gateway/test-gateway.sh` → 97/97 lolos:
  tanpa sertifikat / CA asing / sertifikat EKU server saja / HTTP polos / BFF langsung ke `odoo:8443` → gagal handshake;
  CN `traefik` → 403; GET/PUT/DELETE/PATCH/OPTIONS/HEAD → 405; 23 path lain + 14 varian encoded (`%61`, `%2F`, `%2e%2e`,
  `%252e`, `%00`, `%c0%ae`, …) + query/fragment/absolute-form/HTTP 1.0 → ditolak; 22 kasus header → ditolak; body
  300 KB → 413; 11 path sah + variasi header yang dibuang → sampai Odoo (401 dari Odoo karena token uji).
  Ditemukan & diperbaiki saat uji: regex `^bearer [..]` (spasi memisah argumen HAProxy = pola OR) meloloskan
  `Authorization: Basic`. CI: `haproxy -c` + shellcheck + `check-encryption.sh` (aturan G17).
- **G21 (19 Okt 2026, docs/04 §13.5):** path ke-12 `efn.online.order/efn_online_set_invoice_party` (BFF meneruskan
  `invoice_request` sekali ke Odoo; profil pembeli hanya di Odoo sebagai partner terlindungi `efn_pii`, L-63). Tidak ada
  header/aturan baru: body (NPWP/NIK/nama/alamat) tidak pernah dilog gateway (log tanpa body/header/query). Uji nyata
  bertambah 5 kasus (1 positif + model salah, sufiks, `res.partner/create`, `res.partner/search_read` ditolak) →
  102 kasus; **belum dijalankan** terhadap stack nyata (lead menjalankan `test-gateway.sh`). Odoo harus memuat metode
  yang sama di `RPC_ALLOWLIST` (efn-odoo-engineer C); tanpa itu Odoo menjawab 403/404 dan kasus positif tetap
  `pass` (status dari Odoo).
- **G23 (21 Okt 2026, docs/04 §15.3/§15.4/§15.8):** path 16–20 untuk reservasi meja (W-35) dan slot pre-order (W-36):

  | # | Path | Pemanggil | Sifat |
  |---|---|---|---|
  | 16 | `/json/2/efn.table.booking/efn_booking_availability` | BFF, user teknis `efn_booking_pb`/`efn_booking_bcf` | baca |
  | 17 | `/json/2/efn.table.booking/efn_booking_create` | idem | tulis, idempoten `booking:create:<ref>` |
  | 18 | `/json/2/efn.table.booking/efn_booking_cancel` | idem | tulis, idempoten `booking:cancel:<ref>` |
  | 19 | `/json/2/efn.table.booking/efn_booking_status` | idem | baca (rekonsiliasi) |
  | 20 | `/json/2/efn.online.channel/efn_online_slots` | BFF, user teknis kanal online | baca |

  Tidak ada header/aturan baru. Uji positif `test-gateway.sh` bertambah 5 kasus; **belum dijalankan** terhadap stack
  nyata (Odoo dev belum memasang `efn_table_booking`/`efn_online_slot`; kasus positif tetap `pass` karena status dari Odoo).
- **G22 (19 Okt 2026, docs/04 §14.A.5, §14.E.2):** path 13–15 untuk QR order dari ponsel (W-07, E-27):

  | # | Path | Pemanggil | Sifat |
  |---|---|---|---|
  | 13 | `/json/2/efn.pos.qr.order/efn_qr_menu` | BFF, user teknis `efn_qr_pb`/`efn_qr_bcf` | baca (overlay menu per config) |
  | 14 | `/json/2/efn.pos.qr.order/efn_qr_submit` | idem | tulis, idempoten `fnb:submit:<submission_id>` |
  | 15 | `/json/2/efn.pos.qr.order/efn_qr_status` | idem | baca (rekonsiliasi status) |

  API key per PT: secret `odoo_api_key_qr_pb` / `odoo_api_key_qr_bcf` (BFF saja, rotasi `efn-rotate-online-key qr-pb|qr-bcf`).
  Tidak ada header/aturan baru; batas body 256 KB tetap (kiriman QR ≤ 16 KB di BFF). Konkurensi QR memakai semaphore
  json2 **publik** BFF (L-20) + `ODOO_RPC_MAXCONN` yang sama di gateway — QR ikut berbagi worker yang disisihkan untuk
  online; pantau `%Tw` dan `why=busy` saat pilot. `odoo-tls --allow-cn` tidak berubah. Uji nyata bertambah 12 kasus
  (3 positif + model salah, sufiks, huruf besar, `search_read`, `pos.order/create`, `pos.order/sync_from_ui`,
  `pos.config/efn_qr_session_open` (RPC POS, bukan BFF), garis miring akhir, encoded) → 114 kasus; **belum dijalankan**
  terhadap stack nyata. **Catatan nama:** docs/08 §13.1b (fit-gap) memakai `efn.pos.table.session.efn_qr_catalog` /
  `efn_qr_order_submit` / `efn_qr_order_status`; gateway mengikuti kontrak docs/04 §14.A.5 (sumber kebenaran). Bila modul
  `efn_pos_bff_order` (L-70) memakai nama lain, kontrak diralat lead lebih dulu, lalu tiga baris `p_allowed` dan
  kasus uji di sini diubah bersamaan dengan `RPC_ALLOWLIST` Odoo.
- **G44 (18 Nov 2026, docs/04 §28.2, ADR-046 Proposed): klien kedua `forecast-worker`.** Path #25–#29, **hanya** untuk
  CN `forecast-worker` (allowlist per CN: CN `api-gateway` tetap hanya 24 path di atas dan ditolak `why=path` ke path
  forecast; CN `forecast-worker` ditolak ke path BFF):

  | # | Path | Pemanggil | Sifat |
  |---|---|---|---|
  | 25 | `/json/2/efn.forecast.calendar/efn_forecast_calendar_upsert` | forecast-worker, user teknis `efn_forecast_<pt>` | tulis, idempoten `(company, date, kind, name)` |
  | 26 | `/json/2/efn.forecast.run/efn_forecast_run_open` | idem | tulis, idempoten `run_id` |
  | 27 | `/json/2/efn.forecast.daily/efn_forecast_ingest` | idem | tulis, idempoten `(run_id, chunk_no)` |
  | 28 | `/json/2/efn.demand.forecast.line/efn_minmax_ingest` | idem | tulis, idempoten `(run_id, chunk_no)` |
  | 29 | `/json/2/efn.forecast.run/efn_forecast_run_close` | idem | tulis, no-op bila sudah ditutup |

  Batas body path forecast **512 KiB** (§28.2; path lain tetap 256 KiB / bukti transfer 2,5 MiB). Backend terpisah
  `odoo_forecast` dengan `maxconn 1` + antrean maks. 4 × 10 s → batch tidak memakai slot `ODOO_RPC_MAXCONN` kanal
  online, tetapi memakai **1 worker HTTP Odoo tambahan** selama batch (sizing: `docs/runbooks/forecast-worker.md#sizing`).
  Hop keluar tetap mTLS dengan sertifikat `odoo-rpc-gateway`; `odoo-tls --allow-cn` tidak berubah. Network: compose
  overlay `docker-compose.forecast.yml` menambahkan gateway ke network `forecast-rpc` (hanya gateway + forecast-worker).
  Uji nyata `test-gateway.sh` bertambah 27 kasus (5 positif, 5 BFF→path forecast, path/encoded/query/header/method
  forecast, body 300 KB lolos / 600 KB 413, BFF 600 KB 413, forecast langsung ke `odoo-tls` gagal handshake);
  **belum dijalankan** terhadap stack nyata (modul Odoo G44 belum ada; kasus positif `pass` karena status dari Odoo).
  Odoo wajib memuat 5 metode yang sama di `RPC_ALLOWLIST` (efn-odoo-engineer).
- **G50 (20 Nov 2026, docs/04 §28.7, ADR-047 Proposed): kampanye supermarket.** Tiga path baru, allowlist per CN:

  | # | Path | Klien (CN) / user teknis | Sifat |
  |---|---|---|---|
  | 30 | `/json/2/efn.campaign/efn_campaign_claim` | api-gateway, `efn_campaign_<pt>` | tulis (`queued -> claimed`), respons memuat kode voucher |
  | 31 | `/json/2/efn.campaign/efn_campaign_ack` | idem | tulis, idempoten |
  | 32 | `/json/2/efn.campaign/efn_campaign_proposal_upsert` | **segment-worker** (klien ketiga), `efn_segment_<pt>` | tulis ringkasan tanpa data member, idempoten `(run_id, segment_code)` |

  CN `segment-worker` hanya #32 (ditolak `why=path` ke path BFF/forecast); CN `api-gateway` ditolak ke #32. segment-worker
  memakai backend `odoo_forecast` (maxconn 1, antrean 4) bersama forecast-worker: jadwal tidak bertabrakan (forecast
  23:30, segmen 02:15 WIB). Keputusan devsecops G50: user teknis Odoo **terpisah** per service (`efn_campaign_<pt>` untuk
  BFF, `efn_segment_<pt>` untuk segment-worker; secret `odoo_api_key_campaign_<pt>` vs `odoo_api_key_segment_<pt>`),
  keduanya `group_campaign_bot` satu company (docs/04 §7.3 kredensial per service). Network: overlay
  `docker-compose.segment.yml` menambahkan gateway ke `segment-rpc` (hanya gateway + segment-worker). Odoo wajib memuat
  ketiga metode di `RPC_ALLOWLIST` (`efn_core/tests/test_rpc_surface.py`). Kasus uji `test-gateway.sh` untuk #30-#32
  **belum ditambahkan** (backlog; pola kasus G44).
- **Belum / batas:**
  - Tidak ada metrik HAProxy ke Prometheus (log akses → Loki; alert `EfnOdooRpcGatewayDenied`). Kandidat: PROMEX di
    loopback + sidecar mTLS.
  - Isi body tidak diperiksa (validasi argumen & pembersihan context = E-28 Odoo, §10.4.4).
  - Rate limit tidak ada (BFF + forecast-worker; circuit breaker di BFF, backend forecast maxconn 1). Uji beban belum.
  - Antar-mesin (K25-02, stack data di host lain): gateway hanya listen di network Docker host inti; jalur WireGuard
    ke `odoo-rpc-gateway:8443` dari mesin data belum dibangun (lihat runbook forecast-worker §7).
  - Uji nyata belum masuk CI (butuh stack + Odoo); dijalankan manual/staging.
