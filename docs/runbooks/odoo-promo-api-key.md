# Runbook: API key Odoo user teknis promo Super-App (PROMO-PB, rotasi otomatis 30 hari)

Dasar: docs/04 §18.1–§18.2 (RPC #22–24 lewat `odoo-rpc-gateway`), ADR-024 (rotasi dua kunci), pola W-11 / R-T15
([odoo-online-api-key.md](odoo-online-api-key.md)) dan reservasi ([odoo-booking-api-key.md](odoo-booking-api-key.md)).
Audiens: on-call IT EFN + Superadmin.

> **Ringkasan manajemen.** Banner dan pop-up promo di Super-App diambil server dari Odoo memakai satu "kunci API"
> khusus per PT. Kunci itu hanya bisa membaca materi promo dan antrean push promo, tidak bisa membaca data pelanggan,
> dan berganti otomatis tiap ±20 hari. Bila penggantian gagal, kunci lama tetap dipakai dan alert muncul; masih ada
> ±10 hari sebelum promo berhenti tampil. Bila promo berhenti, Super-App kembali menampilkan hero biasa; belanja,
> kasir, reservasi, dan QR meja tidak terdampak.

## Fakta teknis

| Kode skrip | PT | User teknis Odoo | Secret Docker (`host:disabled`) | Env BFF | Overlay compose |
|---|---|---|---|---|---|
| `PROMO-PB` | PT Paris Baguette | `efn_promo_pb` | `odoo_api_key_promo_pb` | `ODOO_API_KEY_PROMO_PB_FILE` | `docker-compose.online-promo-pb.yml` |

- User teknis: **Internal User** + grup `efn_promo.group_promo_bff` saja, **satu company** (company dengan
  `efn_brand_code` = kode PT), tanpa grup POS/admin, dan **bukan** user kanal online. Odoo menolak RPC bila user punya
  grup lain (`_efn_promo_check_caller`, fail closed). Skrip menolak sebelum membuat key: `group_missing` (modul belum
  terpasang), `user_not_promo`, `user_privileged`, `user_company_mismatch`, `user_shared`.
- Grup `group_promo_bff` membawa `api_key_duration` = 35 hari, jadi key 30 hari bisa dibuat.
- Jalur: BFF → `odoo-rpc-gateway` (mTLS, path persis 22–24: `efn.promo.banner/efn_promo_feed`,
  `efn.promo.banner/efn_promo_image`, `efn.promo.popup/efn_promo_push_claim`) → `odoo-tls` → Odoo `/json/2`.
- Secret hanya di-mount ke `api-gateway` dan **hanya** saat `PROMO_ENABLED=true` dan PT ada di `PROMO_COMPANIES`
  (deploy.sh menyertakan overlay). `check-encryption.sh` menolak secret promo di service lain / PT tanpa overlay.
- Verifikasi key oleh skrip: `POST /json/2/efn.promo.banner/efn_promo_feed {}` dari setiap replika BFF. **Hanya 200**
  = key sah. 401/403/5xx/000 = gagal → rollback.
- Nama key di Odoo: `efn-promo-<PT>-<UTC>`. Nilai key tidak pernah dicetak.
- Materi promo satu PT tampil untuk semua brand; target brand diatur per materi di Odoo (*Promo Super-App*).

## 1. Pemasangan key pertama (Superadmin) {#pemasangan}

Prasyarat: rilis G27+ ter-deploy (overlay + baris `secrets.list`; deploy.sh membuat placeholder
`/opt/efn/secrets/odoo_api_key_promo_pb.txt`), modul `efn_promo` terpasang, user teknis `efn_promo_pb` dibuat sesuai
tabel, dan `efn-rotate-online-key` versi G27 dipasang ulang (skrip lama menolak kode `PROMO-*`).

```bash
install -o root -g root -m 0755 scripts/host/efn-rotate-online-key /usr/local/sbin/efn-rotate-online-key
sudo efn-rotate-online-key --channel=PROMO-PB --bootstrap     # overlay ikut sementara; secret harus masih 'disabled'
sudo efn-rotate-online-key --channel=PROMO-PB --status        # id, nama, kedaluwarsa (TANPA nilai key)
```

Lalu aktifkan lewat config rilis (GitHub variables Environment): `PROMO_ENABLED=true`, `PROMO_COMPANIES=PB` →
workflow Deploy. deploy.sh **gagal** bila key PT di daftar masih placeholder. Push promo (`PROMO_PUSH_ENABLED=true`)
butuh `PROMO_ENABLED=true`; pengiriman nyata butuh kredensial FCM/EAS di build aplikasi. Timer harian
`efn-rotate-online-key.timer` (`--all`) merotasi `PROMO-PB` otomatis karena `.env` deploy memuat `PROMO_ENABLED=true`.

Persetujuan Kebijakan Privasi (§18.3a) berjalan terpisah: `CONSENT_PRIVACY_VERSION` + `CONSENT_PRIVACY_TEXT_SHA256`
(teks disetujui DPO/Legal, sama persis dengan web `lib/consent.ts` dan app), lalu `PRIVACY_CONSENT_ENFORCED=true`
**setelah** APK/web versi baru terpasang. deploy-validate menolak `ENFORCED=true` tanpa versi teks aktif (semua login
akan gagal).

Dev: `secrets/odoo_api_key_promo_pb.txt` berisi key nyata user `efn_promo_pb`, `.env` `PROMO_ENABLED=true
PROMO_COMPANIES=PB`, compose dengan `-f docker-compose.online-promo-pb.yml`.

## 2. Rotasi otomatis & manual {#rotasi}

Sama dengan kanal online ([odoo-online-api-key.md §2](odoo-online-api-key.md#rotasi)). Rotasi paksa (dugaan bocor):

```bash
sudo efn-rotate-online-key --channel=PROMO-PB --force --overlap-seconds=0
```

## 3. Alert {#alert}

Metrik & alert sama dengan kanal online, label `channel="PROMO-PB"`. Penyebab khusus promo:

| Gejala di journald (`service=host-efn-rotate-online-key`) | Tindakan |
|---|---|
| `odoo list: group_missing` | Modul `efn_promo` belum terpasang/di-upgrade di `efn_odoo`. |
| `odoo list: user_not_promo` | User teknis tanpa grup `group_promo_bff`. |
| `odoo list: user_company_mismatch` | User teknis punya > 1 company atau company bukan PT kode itu. |
| `odoo list: user_privileged` | Cabut grup POS/admin dari user teknis. |
| `odoo list: user_shared` | User promo juga terdaftar di kanal online → buat user terpisah. |
| `VERIFIKASI GAGAL ... status=000` | Gateway menolak (path promo tidak ada di `haproxy.cfg`, rilis lama) atau jaringan/TLS. |
| `VERIFIKASI GAGAL ... status=401/403` | Key tidak dikenal Odoo, atau user teknis punya grup tambahan. |

## 4. Menghentikan promo satu PT {#hentikan}

1. Config rilis: hapus PT dari `PROMO_COMPANIES` (atau `PROMO_ENABLED=false`) → Deploy. Super-App kembali ke hero
   biasa; materi tetap di Odoo.
2. Odoo: cabut semua key user `efn_promo_<pt>` atau arsipkan user.
3. Host: `rm /var/lib/efn/online-key/promo_<pt>.*` lalu `sudo efn-rotate-online-key --all --status`.

## PT baru {#pt-baru}

Tambah kode di `EFN_PROMO_COMPANY_CODES` (`scripts/lib/deploy-validate.sh`), `ALLOWED_CHANNELS` (`PROMO-<PT>`) dan
regex `PROMO_COMPANIES` di `efn-rotate-online-key`, regex secret di `check-encryption.sh`, baris `secrets.list`, secret
top-level di `docker-compose.yml`, dan overlay `docker-compose.online-promo-<pt>.yml`.

## Batas / belum

- Uji skrip memakai docker/Odoo/BFF palsu (`scripts/host/test-efn-rotate-online-key.sh`, kasus 127–138). Uji nyata
  rotasi di stack dev belum dijalankan; feed promo dev sudah diverifikasi lewat gateway mTLS (docs/04 §18.5).
- User teknis `efn_promo_pb` dibuat manual (belum oleh modul setup).
