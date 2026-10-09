# odoo-rpc-gateway — filter L7 BFF → Odoo `/json/2` (penjualan online)

- **Tujuan:** satu-satunya jalur BFF (`api-gateway`) ke Odoo untuk kanal online. Hanya `POST` ke **10 path persis**
  docs/04 §10.4.3; semua bentuk lain ditolak sebelum menyentuh Odoo. Odoo tetap tidak terjangkau dari internet
  maupun langsung dari BFF (`odoo-tls --allow-cn` tidak memuat `api-gateway`).
- **ADR:** ADR-034 (Proposed — kanal online = BFF + `/json/2` terfilter), ADR-022 (enkripsi: mTLS dua hop, verify-full
  CA internal), ADR-023 (Odoo privat), ADR-024 (kredensial per kanal), pola ADR-030 (s3-gateway).
- **Risiko dimitigasi:** R-S03 (permukaan RPC Odoo: ORM generik `search_read`/`write` tidak terjangkau dengan API key
  yang bocor dari BFF), R-T15 (alert & runbook API key), penyalahgunaan sesi/header proxy.
- **Komponen:** HAProxy LTS `3.2.25-alpine` (digest sama dengan s3-gateway), non-root, read-only, `cap_drop: ALL`,
  memori 64M, hanya network `internal`, tanpa label Traefik/port host. `haproxy.cfg` (aturan + alasan),
  `entrypoint.sh` (PEM crt+key ke tmpfs, validasi `ODOO_DB` fail-closed), `test-gateway.sh` (uji nyata).
- **Aturan (ringkas):** mTLS masuk (CA internal, EKU clientAuth, CN persis `api-gateway`) · HTTP/1.1 origin-form ·
  `POST` saja · path mentah = salah satu dari 10 path (tolak `%`, `;`, `//`, segmen titik, query, fragment) · allowlist
  header (`host, content-type, content-length, authorization, accept, accept-encoding, user-agent, connection,
  traceparent, tracestate, x-request-id`; method override/upgrade/transfer-encoding/x-original-url/x-openerp-* ditolak) ·
  Host `odoo-rpc-gateway[:8443]` · `Content-Type: application/json` · `Authorization: bearer <token>` tepat satu ·
  `Content-Length` ≤ 256 KB tanpa chunked · `X-Odoo-Database` dipaksa `${ODOO_DB}` · `Cookie`, `X-Forwarded-*`,
  `Forwarded`, `X-Real-IP`, `Accept-Language`, `Sec-Fetch-*` dibuang · `Set-Cookie` respons dibuang · timeout 30 s ·
  maks 32 koneksi ke Odoo · log tanpa body/header/query (`why=<alasan>`), penolakan membawa `X-EFN-Gateway-Deny`.
- **Kontrak untuk BFF:** kirim hanya header di allowlist (undici `fetch` default lolos: `accept-language`/`sec-fetch-mode`
  dibuang gateway). Header baru = ubah allowlist di sini (koordinasi devsecops). Respons dengan header
  `X-EFN-Gateway-Deny` = bug klien (jangan retry).
- **Verifikasi (G17, 9 Okt 2026, stack dev nyata):** `bash infra/odoo-rpc-gateway/test-gateway.sh` → 97/97 lolos:
  tanpa sertifikat / CA asing / sertifikat EKU server saja / HTTP polos / BFF langsung ke `odoo:8443` → gagal handshake;
  CN `traefik` → 403; GET/PUT/DELETE/PATCH/OPTIONS/HEAD → 405; 23 path lain + 14 varian encoded (`%61`, `%2F`, `%2e%2e`,
  `%252e`, `%00`, `%c0%ae`, …) + query/fragment/absolute-form/HTTP 1.0 → ditolak; 22 kasus header → ditolak; body
  300 KB → 413; 10 path sah + variasi header yang dibuang → sampai Odoo (401 dari Odoo karena token uji).
  Ditemukan & diperbaiki saat uji: regex `^bearer [..]` (spasi memisah argumen HAProxy = pola OR) meloloskan
  `Authorization: Basic`. CI: `haproxy -c` + shellcheck + `check-encryption.sh` (aturan G17).
- **Belum / batas:**
  - Tidak ada metrik HAProxy ke Prometheus (log akses → Loki; alert `EfnOdooRpcGatewayDenied`). Kandidat: PROMEX di
    loopback + sidecar mTLS.
  - Isi body tidak diperiksa (validasi argumen & pembersihan context = E-28 Odoo, §10.4.4).
  - Rate limit tidak ada (BFF satu-satunya klien; circuit breaker di BFF). Uji beban belum.
  - Uji nyata belum masuk CI (butuh stack + Odoo); dijalankan manual/staging.
