# Runbook: API key Odoo kanal online (pembuatan, rotasi, R-T15)

Dasar: docs/04 §10.4.1 & §10.10, ADR-034 (Proposed), ADR-024 (rotasi dua kunci), keputusan lead L-6 (risiko
**R-T15**: API key kanal online kedaluwarsa → checkout berhenti). Audiens: on-call IT EFN + Superadmin.

> **Ringkasan manajemen.** Super-App berbicara dengan Odoo memakai satu "kunci API" per PT (pilot: PT Retail,
> kanal `RTL`). Kunci ini sengaja punya masa berlaku (PLACEHOLDER 90 hari). Bila lupa diganti, pembayaran online
> berhenti (POS di toko tidak terdampak). Alert memberi peringatan 14 hari dan 3 hari sebelumnya; penggantian
> dilakukan tanpa downtime.

## Fakta teknis (Odoo CE 19, diverifikasi dari source image `efn/odoo:dev`, 9 Okt 2026)

- Key = 40 karakter hex acak (`res.users.apikeys._generate`), disimpan Odoo sebagai hash; teks asli hanya terlihat
  sekali saat dibuat.
- `/json/2` hanya menerima key **scope global** (`scope = None`); key ber-scope lain ditolak (401).
- User non-admin wajib memberi `expiration_date` ≤ `api_key_duration` grupnya (grup `efn_online_order.group_online_channel`,
  diatur `efn_setup_online`, PLACEHOLDER 90 hari). Membuat key lewat `sudo()`/admin **melewati** batas ini — jangan:
  selalu `with_user(<user teknis>)`.
- Satu key = satu user teknis = satu PT: `efn_online_rtl` (kelak `efn_online_fnb`). User teknis hanya `base.group_user`
  + `group_online_channel`, satu company, tanpa ACL dokumen.
- Secret Docker: `odoo_api_key_online_rtl` (`_fnb` disiapkan, tidak di-mount selama kanal mati). Dev/CI berisi
  placeholder `disabled`; BFF menolak placeholder (terlalu pendek) bila kanal online diaktifkan.

## 1. Membuat key pertama / key baru (dev & staging/prod)

Jalankan di host (staging/prod: `cd /opt/efn/compose`, `$DC` sesuai [README](README.md)). Keluaran **hanya** key, ditulis
langsung ke file 0600 — jangan copy-paste ke chat/tiket.

```bash
umask 077
EXP=$(date -u -d '+85 days' '+%Y-%m-%d %H:%M:%S')       # < api_key_duration grup (90) -> tidak ditolak
$DC run --rm --no-deps -T -e ODOO_DEV_MODE= odoo odoo shell -d "${ODOO_DB:-efn_odoo}" --no-http --log-level=error <<PY \
  | grep -E '^EFN_KEY [0-9a-f]{40}$' | cut -d' ' -f2 > /root/odoo_api_key_online_rtl.new
from datetime import datetime
u = env['res.users'].search([('login', '=', 'efn_online_rtl')], limit=1)
assert u and u.has_group('efn_online_order.group_online_channel'), 'user teknis efn_online_rtl belum ada'
k = env['res.users.apikeys'].with_user(u)._generate(None, 'efn-online-RTL-' + datetime.utcnow().strftime('%Y%m%d'),
                                                    datetime.strptime('$EXP', '%Y-%m-%d %H:%M:%S'))
env.cr.commit()
print('EFN_KEY', k)
PY
test "$(wc -c < /root/odoo_api_key_online_rtl.new)" -eq 41 || echo "GAGAL: key tidak terbentuk"
```

- **Dev:** `cp /root/odoo_api_key_online_rtl.new secrets/odoo_api_key_online_rtl.txt` (gantikan `disabled`), lalu
  `$DC up -d --force-recreate api-gateway`.
- **Staging/prod (Superadmin):** `gh secret set ODOO_API_KEY_ONLINE_RTL --env <env> < /root/odoo_api_key_online_rtl.new`,
  `shred -u /root/odoo_api_key_online_rtl.new`, jalankan workflow Deploy (deploy.sh menulis
  `/opt/efn/secrets/odoo_api_key_online_rtl.txt` dan me-recreate `api-gateway`).
- Catat masa berlaku untuk alert ([§3](#metrik-masa-berlaku)).

## 2. Rotasi (dua kunci, tanpa downtime) {#rotasi}

Alert `EfnOnlineApiKeyExpiringSoon` (< 14 hari, warning) / `EfnOnlineApiKeyExpiryCritical` (< 3 hari, critical).

1. Buat key **baru** (§1) — key lama tetap valid sampai kedaluwarsa/dicabut (Odoo mengizinkan banyak key per user).
2. Pasang key baru (GitHub Environment + Deploy / dev: file + recreate `api-gateway`).
3. Verifikasi: tidak ada `EfnOnlineOdooAuthFailure`; `efn_online_odoo_requests_total{outcome="ok"}` naik; log
   `odoo-rpc-gateway` menunjukkan status 200 (`why=-`).
4. Cabut key lama di Odoo (Settings → Users → `efn_online_rtl` → Account Security → API Keys → hapus key bernama
   `efn-online-RTL-<tanggal lama>`), atau shell: `env['res.users.apikeys'].search([('user_id.login','=','efn_online_rtl'),('name','=','<nama lama>')]).sudo()._remove()`.
5. Perbarui metrik masa berlaku (§3).

## 3. Metrik masa berlaku (R-T15) {#metrik-masa-berlaku}

Odoo tidak mengekspos masa berlaku key lewat `/json/2`, jadi masa berlaku dicatat sebagai metrik textfile
node-exporter (direktori `NODE_EXPORTER_TEXTFILE_DIR`, default `/var/lib/efn/node-exporter`) setiap kali key dibuat:

```bash
TS=$(date -u -d "$EXP" +%s)
printf '# HELP efn_odoo_api_key_expiry_timestamp_seconds Masa berlaku API key Odoo kanal online (runbook odoo-online-api-key)\n# TYPE efn_odoo_api_key_expiry_timestamp_seconds gauge\nefn_odoo_api_key_expiry_timestamp_seconds{channel="RTL"} %s\n' "$TS" \
  > /var/lib/efn/node-exporter/.efn_odoo_api_key.tmp && mv /var/lib/efn/node-exporter/.efn_odoo_api_key.tmp /var/lib/efn/node-exporter/efn_odoo_api_key.prom
```

Satu baris per kanal aktif (`channel="FNB"` saat kanal FNB dinyalakan). Bila kanal dipakai tetapi file ini tidak ada,
`EfnOnlineApiKeyExpiryUnknown` menyala.

## 4. `EfnOnlineOdooAuthFailure` (critical) {#efnonlineodooauthfailure}

Odoo menjawab 401/403 untuk kanal → key kedaluwarsa, dicabut, user teknis dinonaktifkan/berubah grup, atau secret salah.

1. `$DC logs --since 30m odoo-rpc-gateway | tail` — status 401 dengan `why=-` = ditolak **Odoo** (bukan filter gateway).
   Penolakan gateway (`why=auth|header|path|…`) → [online-orders.md](online-orders.md#efnodoorpcgatewaydenied).
2. Cek key di Odoo (shell): `env['res.users.apikeys'].sudo().search([('user_id.login','=','efn_online_rtl')]).mapped(lambda k: (k.name, k.expiration_date))`.
3. Buat key baru (§1) + pasang. Selama itu kill switch opsional: `ONLINE_ENABLED=false` (cart/quote/order → 503;
   order yang sudah dibayar diproses saat key pulih — saga idempoten).
4. Bila key dicabut tanpa perubahan terencana → perlakukan sebagai insiden ([security-incident.md](security-incident.md)).

## Batas / belum

- Pembuatan key belum diotomasi (sengaja manual: butuh shell admin Odoo). Kandidat: skrip host bertanda Superadmin.
- Masa berlaku dicatat manual (§3); kandidat: cron Odoo yang mengekspos masa berlaku ke log/metrik.
- `api_key_duration` 90 hari = PLACEHOLDER (Q-ON*), menunggu keputusan klien.
