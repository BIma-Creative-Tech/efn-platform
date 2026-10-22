# Runbook: kunci token sesi meja QR (`fnb_table_token_key`) & HMAC event QR (`odoo_fnb_event_hmac`)

Dasar: docs/04 §14.A.2 (format token `fts1.`), §14.A.4 (event Odoo → BFF bertanda tangan), §14.A.9, ADR-032 (Accepted
opsi B), ADR-022 (enkripsi), [key-custody.md](key-custody.md). Audiens: Superadmin + on-call IT EFN.

> **Ringkasan manajemen.** QR yang dicetak di meja berisi "tiket" digital yang berlaku maksimal 4 jam. Tiket itu
> ditandatangani dengan kunci rahasia di server Super-App. Bila kunci diganti, semua QR yang sedang aktif tidak berlaku
> lagi dan pelayan cukup mencetak ulang QR meja. Penggantian dilakukan di luar jam layanan agar tidak ada tamu terganggu.

## Fakta teknis

| Secret | Jenis (`scripts/secrets.list`) | Di-mount ke | Env |
|---|---|---|---|
| `fnb_table_token_key` | `random` (GitHub Environment `FNB_TABLE_TOKEN_KEY`) | `api-gateway` saja | `FNB_TABLE_TOKEN_KEY_FILE` |
| `odoo_fnb_event_hmac` | `random` (GitHub Environment `ODOO_FNB_EVENT_HMAC`) | `odoo` + `api-gateway` | Odoo `EFN_FNB_EVENT_HMAC_FILE`, BFF `ODOO_FNB_EVENT_HMAC_FILE` |

- Token: `fts1.<b64url(session_id)>.<exp>.<b64url(HMAC-SHA256(K, ...))>`, `K = HKDF-SHA256(fnb_table_token_key,
  "efn:fnb_table_token:v1")`. BFF tidak menyimpan token; token dihitung ulang dari `session_id` + `exp`.
- Token ada di **fragment** URL QR (`/meja#t=...`) dan dikirim klien lewat header `X-Table-Session`. Lapisan log:
  Traefik tidak mencatat header `X-Table-Session` (`traefik.yml`, `defaultMode: drop` + eksplisit), Promtail meredaksi
  `fts1\.[A-Za-z0-9_.-]+` di mana pun (`promtail.yml` / `promtail-journal.yml` tahap (a0), uji
  `scripts/test-promtail-redaction.sh`).
- `check-encryption.sh` (CI) menolak `fnb_table_token_key` di service selain `api-gateway` dan `odoo_fnb_event_hmac`
  di luar `odoo` + `api-gateway`, serta env `*_FILE` yang tidak cocok.
- `FNB_TABLE_TOKEN_KEY_PREV_FILE` (kunci lama untuk verifikasi saja, kontrak §14.A.2) **belum di-wire** di compose:
  rotasi memakai jendela tanpa sesi terbuka (di bawah). Bila klien meminta rotasi tanpa jeda, tambahkan secret
  `fnb_table_token_key_prev` (secrets.list + compose + check-encryption) — perubahan repo, bukan konfigurasi.

## 1. Rotasi `fnb_table_token_key` {#rotasi-token}

Kapan: terjadwal (usulan 90 hari, menunggu kebijakan kunci klien), dugaan bocor, atau pergantian personel yang memegang
GitHub Environment.

1. Pilih jam **setelah semua outlet F&B tutup** (sesi meja ≤ 4 jam; tidak ada tamu memesan). Bila mendesak di jam
   layanan: umumkan ke supervisor outlet — semua QR meja yang terbuka menjadi `401 table_session_invalid` dan pelayan
   harus **Cetak ulang QR** (sesi lama dicabut `reissued`).
2. Buat nilai baru: `openssl rand -base64 36 | tr -d '\n'` → GitHub Environment secret `FNB_TABLE_TOKEN_KEY`
   (staging dulu, lalu production). Nilai tidak ditempel di chat/tiket.
3. Workflow Deploy. deploy.sh menulis file secret baru, tetapi compose **tidak** me-recreate container hanya karena isi
   file secret berubah (bind mount menunjuk inode lama). Di host (root):
   ```bash
   cd /opt/efn/compose && mapfile -t F < .efn-compose-files
   docker compose $(printf -- '-f %s ' "${F[@]}") up -d --no-build --no-deps --force-recreate api-gateway
   ```
4. Verifikasi: buka meja uji → Cetak QR → `GET /v1/fnb/session` dengan token baru = 200; token lama = 401.

## 2. Rotasi `odoo_fnb_event_hmac` {#rotasi-hmac}

Odoo (penanda tangan outbox) dan BFF (pemverifikasi) harus memakai nilai yang sama. Event yang ditandatangani dengan
kunci lama selama jeda ditolak BFF `401` → outbox Odoo `efn.pos.qr.event.outbox` mencoba ulang (1 m → 1 j), jadi
tidak ada event hilang asalkan kedua container sudah memakai kunci baru dalam jendela retry.

1. GitHub Environment secret `ODOO_FNB_EVENT_HMAC` = nilai baru (`openssl rand -base64 36`).
2. Deploy, lalu recreate **keduanya** berurutan cepat dengan perintah §1 langkah 3: `odoo odoo-tls odoo-ws-tls`
   (sidecar `network_mode: service:odoo` ikut) lalu `api-gateway`. Recreate Odoo = POS terputus beberapa detik
   (POS offline-capable); lakukan di luar jam ramai.
3. Verifikasi: log BFF tidak lagi menolak `POST /v1/pos/fnb/events` (status 401 berhenti), antrean outbox QR di Odoo
   kembali kosong.

## Batas / belum

- Kunci verifikasi lama (`_PREV`) belum di-wire (lihat di atas).
- Uji nyata rotasi terhadap BFF/Odoo G22 belum dijalankan (fitur ditulis paralel di G22); yang diuji CI: least
  privilege & env (`check-encryption.sh`), redaksi `fts1.` (`test-promtail-redaction.sh`), penerimaan secret dari
  stdin `efn-deploy` (`test-efn-deploy.sh` kasus 170).
