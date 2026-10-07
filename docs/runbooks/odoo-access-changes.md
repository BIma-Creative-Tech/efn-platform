# Runbook: perubahan hak akses user Odoo & traceback di respons JSON-RPC

## 1. Ubah grup/hak user lewat UI atau RPC, BUKAN `odoo shell`
Temuan QA gelombang 11: `has_group()` di-cache per worker. Perubahan grup lewat `odoo shell` (proses terpisah) **tidak**
meng-invalidasi cache worker HTTP yang sedang berjalan, sehingga user tetap punya/kehilangan akses lama sampai worker
di-restart. Lewat UI (Settings → Users) atau RPC `res.users.write` dari sesi admin, Odoo mengirim sinyal invalidasi
registry ke semua worker.
- Prosedur: Settings → Users & Companies → Users → tab Access Rights (admin `base.group_system`, lewat `erp.` di
  WireGuard staf).
- **Jejak audit (Gelombang 13, SEC-49):** `efn_audit` >= 19.0.1.3.0 SELALU mencatat (tanpa aturan admin, tidak ikut GC
  retensi) perubahan lewat ORM/UI/RPC pada: grup user (`res.users.group_ids` dan sisi grup `res.groups.user_ids`/
  `implied_ids`/`implied_by_ids`), `res.users` `active`/`login`/`company_ids`/`company_id`, `res.groups`, record rule
  (`ir.rule`), ACL (`ir.model.access`), serta pembuatan/pencabutan API key (`res.users.apikeys`, tanpa nilai key).
  Lihat di menu *Audit → Log* (model `res.users`/`res.groups`/`ir.rule`/`ir.model.access`/`res.users.apikeys`); grup
  tampil sebagai xmlid (`base.group_system`), user sebagai `res.users,<id>`. Setiap perubahan juga dikirim ke sink
  Loki (`{stream="efn_audit_sink"}`) dengan `audit_op` = `access_grant` / `access_revoke` / `access_rule_change` /
  `apikey_create` / `apikey_revoke` / `user_change` / `group_change`, dan `critical: true` bila grant mencakup
  `base.group_system`, `base.group_erp_manager`, `efn_audit.group_auditor_personal`, `efn_audit.group_manager`, atau
  perubahan record rule/ACL.
- **TIDAK tercatat** (gunakan hanya sebagai break-glass, catat manual di tiket insiden): perubahan lewat `odoo shell`
  dengan SQL langsung, `psql`, atau selama install/upgrade modul (data XML/CSV); penghapusan API key kedaluwarsa oleh
  autovacuum Odoo. Perubahan ORM di `odoo shell` (mis. `user.write({...})`) TETAP tercatat, tetapi log + baris sink baru
  ada setelah `env.cr.commit()`.
- Pemberian `base.group_system` / Auditor Data Pribadi Lintas PT yang sah: buat tiket dulu (pemohon, penyetuju,
  alasan, durasi), lalu cocokkan dengan alert sink `critical`. Alert tanpa tiket = insiden
  ([security-incident.md](security-incident.md)).
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

## 3. Membaca baris sink hak akses (Loki) — catatan devsecops (SEC-51)
- Format v2 (efn_audit 19.0.1.3.0): `<ts> <pid> INFO <db> odoo.addons.efn_audit.sink: EFN_AUDIT_SINK {json}`; JSON satu
  baris (newline di nilai selalu di-escape), kunci `v`=2, `audit_op`, `critical`, `mac`, `groups_added`/`groups_removed`
  (xmlid), `users_added`/`users_removed` (uid). Detail: `services/odoo/addons/efn_audit/README.md` (Format sink v2).
- Promtail **wajib** memilih baris berdasarkan format baris Odoo + nama logger, bukan substring di mana pun:
  `^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} \d+ INFO \S+ odoo\.addons\.efn_audit\.sink: EFN_AUDIT_SINK (\{[^\n]*\})$`,
  lalu stage `json` dan buang baris tanpa `log_id` numerik. Teks pengguna yang dicetak logger Odoo lain (mis. login
  gagal) tidak bisa memenuhi pola ini kecuali menyisipkan newline; untuk itu `mac` (HMAC-SHA256, kunci file secret
  `/run/secrets/efn_audit_sink_key`) diverifikasi di luar Odoo (ETL) — baris tanpa `mac` valid = tidak terverifikasi.
- `mac: null` berarti secret belum di-mount (dev/CI); Odoo mencetak WARNING `EFN audit sink: kunci HMAC tidak tersedia`
  sekali per proses.
