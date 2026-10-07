# Runbook: perubahan hak akses user Odoo & traceback di respons JSON-RPC

## 1. Ubah grup/hak user lewat UI atau RPC, BUKAN `odoo shell`
Temuan QA gelombang 11: `has_group()` di-cache per worker. Perubahan grup lewat `odoo shell` (proses terpisah) **tidak**
meng-invalidasi cache worker HTTP yang sedang berjalan, sehingga user tetap punya/kehilangan akses lama sampai worker
di-restart. Lewat UI (Settings → Users) atau RPC `res.users.write` dari sesi admin, Odoo mengirim sinyal invalidasi
registry ke semua worker.
- Prosedur: Settings → Users & Companies → Users → tab Access Rights (admin `base.group_system`, lewat `erp.` di
  WireGuard staf). Perubahan tercatat di `efn.audit.log` bila aturan audit `res.users` aktif.
- Bila terpaksa memakai `odoo shell` (break-glass), restart Odoo sesudahnya: `$DC restart odoo`. Bila container
  odoo dibuat ulang (`$DC up`), recreate juga sidecar `odoo-tls` dan `odoo-ws-tls` (ikut namespace).
- Pencabutan akses karena insiden: cabut lewat UI, nonaktifkan user (`active=False`), lalu restart Odoo agar cache
  lama hilang. Lihat [security-incident.md](security-incident.md).

## 2. Traceback di respons error JSON-RPC (`data.debug`) — sisa SEC-22
Odoo 19 (`odoo/http.py`, `serialize_exception`) selalu menyertakan `error.data.debug` = traceback Python lengkap di
respons JSON-RPC (`/web/dataset/call_kw`, `/web/action/*`) untuk user yang login; dipakai dialog error web client.
**Tidak ada opsi konfigurasi** yang mematikannya (`log_level`, `--dev`, `list_db`, `proxy_mode` tidak berpengaruh;
diperiksa di image `odoo:19.0` yang dipin, 7 Okt 2026).

Status kontrol (gelombang 12):
- Hanya terjangkau dari `wg-https` (IP WireGuard outlet/staf) oleh user terautentikasi. `/jsonrpc`, `/xmlrpc`,
  `/json/2` diblok total di Traefik (router `odoo-rpc-block`). Odoo tidak pernah di internet (ADR-023).
- Filter di Traefik **tidak** dipasang: Traefik v3 tidak punya middleware penulisan ulang body bawaan; plugin pihak
  ketiga (Yaegi, kode tak terverifikasi di proxy internet-facing) bertentangan dengan kebijakan supply chain
  (SEC-47) dan mengganggu respons terkompresi/streaming.
- **Diterapkan (gelombang 12b):** `efn_core/models/ir_http.py` meng-override `ir.http._handle_error` (titik resmi
  per-database). Untuk dispatcher `jsonrpc` dan `json2`, user bukan `base.group_system` (termasuk kasir POS, portal,
  publik, tanpa sesi) mendapat `debug: ""`; pesan exception bisnis (`UserError`/`ValidationError`/`AccessError`/
  `MissingError`/`RedirectWarning`/`SessionExpired`/HTTPException) tetap utuh; exception tak terduga diganti pesan
  generik (`name`=`Exception`, tanpa argumen). Admin sistem (uid sesi, atau API key untuk `/json/2`) tetap mendapat
  traceback. Log server tidak berubah. Batasan: request tanpa database (`_serve_nodb`) tidak tertangani — produksi
  memakai satu DB + dbfilter. Risiko residu ditutup untuk jalur ber-database.
- Diagnosis tetap lewat log server: Loki `{service="odoo"} |= "Traceback"`.
