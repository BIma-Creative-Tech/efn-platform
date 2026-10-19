# Runbook: API key Odoo user teknis QR meja (QR-PB / QR-BCF, rotasi otomatis 30 hari)

Dasar: docs/04 §14.A.5 (RPC #13–15 lewat `odoo-rpc-gateway`), §14.A.9 (secret), §14.7 L-70/L-72 (D-22-7: satu user
teknis QR per PT), L-74 (grup teknis `efn_pos_bff_order.group_qr_bff` tidak boleh diberi peran), ADR-032 (Accepted
opsi B), ADR-024 (rotasi dua kunci), pola W-11 / R-T15 ([odoo-online-api-key.md](odoo-online-api-key.md)).
Audiens: on-call IT EFN + Superadmin.

> **Ringkasan manajemen.** Saat tamu memesan dari ponsel di meja, server Super-App meneruskan pesanan ke Odoo memakai
> satu "kunci API" khusus per PT restoran (Paris Baguette, Bacha Coffee F&B). Kunci itu hanya bisa memanggil tiga fungsi
> QR (menu, kirim pesanan, cek status) dan berganti otomatis tiap ±20 hari. Bila penggantian gagal, kunci lama tetap
> dipakai dan alert muncul; masih ada ±10 hari sebelum pemesanan QR PT itu berhenti. Kasir, POS, dan pemesanan lewat
> pelayan tidak terdampak.

## Fakta teknis

| Kode skrip | PT | User teknis Odoo | Secret Docker (`host:disabled`) | Env BFF | Overlay compose |
|---|---|---|---|---|---|
| `QR-PB` | PT Paris Baguette | `efn_qr_pb` | `odoo_api_key_qr_pb` | `ODOO_API_KEY_QR_PB_FILE` | `docker-compose.online-qr-pb.yml` |
| `QR-BCF` | PT Bacha Coffee F&B | `efn_qr_bcf` | `odoo_api_key_qr_bcf` | `ODOO_API_KEY_QR_BCF_FILE` | `docker-compose.online-qr-bcf.yml` |

- User teknis: **Internal User** + grup `efn_pos_bff_order.group_qr_bff` saja, **satu company** (company dengan
  `efn_brand_code` = kode PT), **tanpa** grup POS (`point_of_sale.group_pos_user`), tanpa `base.group_system`/
  `base.group_erp_manager`, dan **bukan** user kanal online (`efn.online.channel.user_ids`). Skrip menolak sebelum
  membuat key: `group_missing` (modul belum terpasang), `user_not_qr`, `user_privileged`, `user_company_mismatch`,
  `user_shared`.
- Grup `group_qr_bff` **wajib** punya `api_key_duration` = 35 hari (30 + margin 5; pola `group_online_channel`). Tanpa
  itu Odoo membatasi key user non-admin ke 1 hari → rotasi gagal "key baru tidak dibuat". (Konfigurasi milik
  efn-odoo-engineer/efn-odoo-functional; cek sebelum langkah 1.)
- Jalur: BFF → `odoo-rpc-gateway` (mTLS, 15 path persis; QR = path 13–15 `efn.pos.qr.order/efn_qr_{menu,submit,status}`)
  → `odoo-tls` → Odoo `/json/2` dengan `Authorization: bearer <key>`. Key tidak pernah lewat internet.
- Secret hanya di-mount ke `api-gateway` dan **hanya** saat `FNB_QR_ENABLED=true` dan PT ada di `FNB_QR_COMPANIES`
  (deploy.sh menyertakan overlay). `check-encryption.sh` menolak secret QR di service lain / PT selain pb, bcf.
- Verifikasi key oleh skrip: `POST /json/2/efn.pos.qr.order/efn_qr_status {"config_id":0,"submission_refs":[]}` dari
  setiap replika BFF. **200 atau 422** = key sah (422 = `UserError efn_qr:*` karena config 0 tidak ada, artinya auth
  bearer + akses metode lolos). 401/403/5xx = gagal → rollback. Untuk kanal online, 422 tetap dianggap gagal.
- Nama key di Odoo: `efn-qr-<PT>-<UTC>` (mis. `efn-qr-PB-20261020T031500Z`). Nilai key tidak pernah dicetak.

## 1. Pemasangan key pertama (per PT, Superadmin) {#pemasangan}

Prasyarat: rilis G22 ter-deploy (overlay + baris `secrets.list`; deploy.sh membuat placeholder
`/opt/efn/secrets/odoo_api_key_qr_<pt>.txt`), modul `efn_pos_bff_order` terpasang, user teknis per PT dibuat sesuai
tabel, `efn-rotate-online-key` versi G22 dipasang ulang (sha256 di-review — skrip lama menolak kode `QR-*`).

```bash
install -o root -g root -m 0755 scripts/host/efn-rotate-online-key /usr/local/sbin/efn-rotate-online-key
sha256sum /usr/local/sbin/efn-rotate-online-key           # cocokkan dengan PR G22
sudo efn-rotate-online-key --channel=QR-PB --bootstrap     # overlay ikut sementara; secret harus masih 'disabled'
sudo efn-rotate-online-key --channel=QR-PB --status        # id, nama, kedaluwarsa (TANPA nilai key)
```

Lalu aktifkan QR lewat config rilis (GitHub variables Environment): `FNB_QR_ENABLED=true`, `FNB_QR_COMPANIES=PB`
(atau `PB,BCF`), `FNB_QR_BASE_URL_PB=https://<domain tenant>` → workflow Deploy. deploy.sh **gagal** bila key PT di
daftar masih placeholder atau URL bukan https (deploy-validate). Timer harian `efn-rotate-online-key.timer` (`--all`)
merotasi `QR-<PT>` otomatis karena `.env` deploy memuat `FNB_QR_ENABLED=true` (bila `/etc/efn/online-key-rotation.conf`
mengisi `CHANNELS` manual, tambahkan `QR-PB`/`QR-BCF` di sana).

Dev: `secrets/odoo_api_key_qr_pb.txt` berisi key nyata user `efn_qr_pb` (buat dari Odoo dev: user → Preferences →
Account Security → New API Key, durasi ≤ 35 hari), `.env` `FNB_QR_ENABLED=true`, compose
`-f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.online-qr-pb.yml`.

## 2. Rotasi otomatis & manual {#rotasi}

Sama dengan kanal online ([odoo-online-api-key.md §2](odoo-online-api-key.md#rotasi)): key baru dibuat
`with_user(efn_qr_<pt>)`, ditulis atomik ke file secret, BFF di-recreate, diverifikasi di setiap replika, key lama dicabut
setelah overlap (default 1 jam) dan dibuktikan ditolak (401). Rotasi paksa (dugaan bocor):

```bash
sudo efn-rotate-online-key --channel=QR-PB --force --overlap-seconds=0
```

## 3. Alert {#alert}

Metrik & alert sama dengan kanal online, label `channel="QR-PB"` / `"QR-BCF"`:
`EfnOnlineApiKeyExpiringSoon` / `ExpiryCritical` / `RotationFailed` / `RotationNotRunning` / `ExtraKeys` →
ikuti [odoo-online-api-key.md §3–§5](odoo-online-api-key.md#metrik-masa-berlaku). Penyebab khusus QR:

| Gejala di journald (`service=host-efn-rotate-online-key`) | Tindakan |
|---|---|
| `odoo list: group_missing` | Modul `efn_pos_bff_order` belum terpasang/di-upgrade di `efn_odoo`. |
| `odoo list: user_company_mismatch` | User teknis punya > 1 company atau company bukan PT kode itu → perbaiki di Odoo. |
| `odoo list: user_privileged` | Cabut grup POS/admin dari user teknis (L-74: akun teknis tidak boleh diberi peran). |
| `odoo list: user_shared` | User QR juga terdaftar di kanal online → buat user QR terpisah. |
| `VERIFIKASI GAGAL ... status=403` | User tidak punya akses metode (grup salah) atau Odoo belum memuat `RPC_ALLOWLIST` QR. |
| `VERIFIKASI GAGAL ... deny` / status dari gateway | Path QR tidak ada di `infra/odoo-rpc-gateway/haproxy.cfg` (rilis lama). |

Selama QR di-pilot, BFF menjawab `503 ordering_unavailable` bila Odoo menolak key (401/403) dan menaikkan alert BFF;
tamu diarahkan memesan ke pelayan.

## 4. Menghentikan QR satu PT {#hentikan}

1. Config rilis: hapus PT dari `FNB_QR_COMPANIES` (atau `FNB_QR_ENABLED=false`) → Deploy (overlay & key tidak di-mount).
2. Odoo: cabut semua key user `efn_qr_<pt>` (Preferences → API Keys) atau arsipkan user.
3. Host: `rm /var/lib/efn/online-key/qr_<pt>.*` lalu `sudo efn-rotate-online-key --all --status` (metrik lama hilang,
   `EfnOnlineApiKeyRotationNotRunning` tidak menyala).

## Batas / belum

- Uji skrip memakai docker/Odoo/BFF palsu (`scripts/host/test-efn-rotate-online-key.sh`, kasus 90–110). Uji nyata
  terhadap Odoo + `efn_pos_bff_order` belum dijalankan (modul ditulis paralel di G22).
- Nama model/metode QR mengikuti kontrak §14.A.5 (`efn.pos.qr.order`). Bila efn-odoo-engineer memakai nama lain
  (docs/08 §13.1b menyebut `efn.pos.table.session`), kontrak, gateway, dan path verifikasi skrip ini diubah bersamaan.
