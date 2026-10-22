# Runbook: API key Odoo user teknis reservasi meja (BOOKING-PB / BOOKING-BCF, rotasi otomatis 30 hari)

Dasar: docs/04 §15.3 (RPC #16–19 lewat `odoo-rpc-gateway`), §15.5 (event + secret), §15.9 L-85 (satu user teknis
reservasi per PT F&B), ADR-024 (rotasi dua kunci), pola W-11 / R-T15 ([odoo-online-api-key.md](odoo-online-api-key.md))
dan QR meja ([odoo-qr-api-key.md](odoo-qr-api-key.md)). Audiens: on-call IT EFN + Superadmin.

> **Ringkasan manajemen.** Saat pelanggan memesan meja dari Super-App, server meneruskan permintaan ke Odoo memakai
> satu "kunci API" khusus per PT restoran (Paris Baguette, Bacha Coffee F&B). Kunci itu hanya bisa memanggil empat
> fungsi reservasi (cek jam kosong, buat, batal, cek status) dan berganti otomatis tiap ±20 hari. Bila penggantian
> gagal, kunci lama tetap dipakai dan alert muncul; masih ada ±10 hari sebelum reservasi PT itu berhenti. Kasir, POS,
> pesanan online, dan QR meja tidak terdampak.

## Fakta teknis

| Kode skrip | PT | User teknis Odoo | Secret Docker (`host:disabled`) | Env BFF | Overlay compose |
|---|---|---|---|---|---|
| `BOOKING-PB` | PT Paris Baguette | `efn_booking_pb` | `odoo_api_key_booking_pb` | `ODOO_API_KEY_BOOKING_PB_FILE` | `docker-compose.online-booking-pb.yml` |
| `BOOKING-BCF` | PT Bacha Coffee F&B | `efn_booking_bcf` | `odoo_api_key_booking_bcf` | `ODOO_API_KEY_BOOKING_BCF_FILE` | `docker-compose.online-booking-bcf.yml` |

- User teknis: **Internal User** + grup `efn_table_booking.group_booking_bff` saja, **satu company** (company dengan
  `efn_brand_code` = kode PT), tanpa grup POS, tanpa `base.group_system`/`base.group_erp_manager`, dan **bukan** user
  kanal online. Odoo sendiri menolak RPC bila user punya grup lain (`_efn_booking_check_caller`, fail closed). Skrip
  menolak sebelum membuat key: `group_missing` (modul belum terpasang), `user_not_booking`, `user_privileged`,
  `user_company_mismatch`, `user_shared`.
- Grup `group_booking_bff` sudah membawa `api_key_duration` = 35 hari (`efn_table_booking/security`), jadi key 30 hari
  bisa dibuat.
- Jalur: BFF → `odoo-rpc-gateway` (mTLS, path persis; reservasi = path 16–19
  `efn.table.booking/efn_booking_{availability,create,cancel,status}`) → `odoo-tls` → Odoo `/json/2`. Key tidak pernah
  lewat internet.
- Secret hanya di-mount ke `api-gateway` dan **hanya** saat `BOOKING_ENABLED=true` dan PT ada di `BOOKING_COMPANIES`
  (deploy.sh menyertakan overlay). `check-encryption.sh` menolak secret booking di service lain / PT selain pb, bcf.
- Verifikasi key oleh skrip: `POST /json/2/efn.table.booking/efn_booking_status
  {"outlet_code":"VERIFY-KEY","platform_booking_refs":[]}` dari setiap replika BFF. **200 atau 403 dari Odoo** = key
  sah (outlet `VERIFY-KEY` tidak ada → `AccessError` seragam, artinya auth bearer + akses metode lolos). 403 dari
  **gateway** (header `X-EFN-Gateway-Deny`) dicatat sebagai `status=000` dan dianggap gagal; 401/5xx juga gagal →
  rollback. Untuk kanal online/QR, 403 tetap dianggap gagal.
- Nama key di Odoo: `efn-booking-<PT>-<UTC>`. Nilai key tidak pernah dicetak.
- Secret pendamping `odoo_booking_event_hmac` (`random`, dibuat deploy/gen-secrets): di-mount ke `odoo`
  (`EFN_BOOKING_EVENT_HMAC_FILE`, penanda tangan event) dan `api-gateway` (`ODOO_BOOKING_EVENT_HMAC_FILE`,
  pemverifikasi `POST /v1/pos/booking/events`). BFF menolak start bila nilainya sama dengan HMAC online/QR/service key.
  Rotasi: [secret-rotation.md](secret-rotation.md) (event tertunda di outbox dikirim ulang setelah kedua sisi memakai
  nilai baru).

## 1. Pemasangan key pertama (per PT, Superadmin) {#pemasangan}

Prasyarat: rilis G23 ter-deploy (overlay + baris `secrets.list`; deploy.sh membuat placeholder
`/opt/efn/secrets/odoo_api_key_booking_<pt>.txt`), modul `efn_table_booking` terpasang, user teknis per PT dibuat
sesuai tabel, pengaturan reservasi per outlet diisi (Point of Sale → Konfigurasi → Pengaturan reservasi), dan
`efn-rotate-online-key` versi G23 dipasang ulang (skrip lama menolak kode `BOOKING-*`).

```bash
install -o root -g root -m 0755 scripts/host/efn-rotate-online-key /usr/local/sbin/efn-rotate-online-key
sha256sum /usr/local/sbin/efn-rotate-online-key                # cocokkan dengan PR G23
sudo efn-rotate-online-key --channel=BOOKING-PB --bootstrap     # overlay ikut sementara; secret harus masih 'disabled'
sudo efn-rotate-online-key --channel=BOOKING-PB --status        # id, nama, kedaluwarsa (TANPA nilai key)
```

Lalu aktifkan reservasi lewat config rilis (GitHub variables Environment): `BOOKING_ENABLED=true`,
`BOOKING_COMPANIES=PB` (atau `PB,BCF`) → workflow Deploy. deploy.sh **gagal** bila key PT di daftar masih placeholder.
Timer harian `efn-rotate-online-key.timer` (`--all`) merotasi `BOOKING-<PT>` otomatis karena `.env` deploy memuat
`BOOKING_ENABLED=true` (bila `/etc/efn/online-key-rotation.conf` mengisi `CHANNELS` manual, tambahkan `BOOKING-PB`/
`BOOKING-BCF` di sana).

Dev: `secrets/odoo_api_key_booking_pb.txt` berisi key nyata user `efn_booking_pb` (Odoo dev: user → Preferences →
Account Security → New API Key, durasi ≤ 35 hari), `.env` `BOOKING_ENABLED=true`, compose
`-f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.online-booking-pb.yml`.

## 2. Rotasi otomatis & manual {#rotasi}

Sama dengan kanal online ([odoo-online-api-key.md §2](odoo-online-api-key.md#rotasi)). Rotasi paksa (dugaan bocor):

```bash
sudo efn-rotate-online-key --channel=BOOKING-PB --force --overlap-seconds=0
```

## 3. Alert {#alert}

Metrik & alert sama dengan kanal online, label `channel="BOOKING-PB"` / `"BOOKING-BCF"`. Penyebab khusus reservasi:

| Gejala di journald (`service=host-efn-rotate-online-key`) | Tindakan |
|---|---|
| `odoo list: group_missing` | Modul `efn_table_booking` belum terpasang/di-upgrade di `efn_odoo`. |
| `odoo list: user_not_booking` | User teknis tanpa grup `group_booking_bff`. |
| `odoo list: user_company_mismatch` | User teknis punya > 1 company atau company bukan PT kode itu. |
| `odoo list: user_privileged` | Cabut grup POS/admin dari user teknis. |
| `odoo list: user_shared` | User reservasi juga terdaftar di kanal online → buat user terpisah. |
| `VERIFIKASI GAGAL ... status=000` | Gateway menolak (path reservasi tidak ada di `haproxy.cfg`, rilis lama) atau jaringan/TLS. |
| `VERIFIKASI GAGAL ... status=401` | Key tidak dikenal Odoo (kedaluwarsa/dicabut). |

Selama reservasi di-pilot, BFF menjawab `503 booking_unavailable` bila Odoo menolak key (401/403); web/app
menampilkan "Reservasi belum tersedia" dan pelanggan diarahkan menghubungi gerai.

## 4. Menghentikan reservasi satu PT {#hentikan}

1. Config rilis: hapus PT dari `BOOKING_COMPANIES` (atau `BOOKING_ENABLED=false`) → Deploy (overlay & key tidak
   di-mount). Reservasi yang sudah ada tetap di Odoo dan dikelola staf.
2. Odoo: cabut semua key user `efn_booking_<pt>` atau arsipkan user.
3. Host: `rm /var/lib/efn/online-key/booking_<pt>.*` lalu `sudo efn-rotate-online-key --all --status`.

## Batas / belum

- Uji skrip memakai docker/Odoo/BFF palsu (`scripts/host/test-efn-rotate-online-key.sh`, kasus 111–126). Uji nyata
  terhadap Odoo + `efn_table_booking` di stack dev belum dijalankan (butuh user teknis + key dev).
- User teknis `efn_booking_pb`/`efn_booking_bcf` belum dibuat otomatis oleh modul setup; dibuat manual oleh
  efn-odoo-functional sesuai tabel di atas.
