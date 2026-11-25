# efn_esb_identity — identitas pengguna ESB sebagai shadow user Odoo (G66b)

**Versi** 19.0.1.0.0 · **Lisensi** LGPL-3 · **Depends** `efn_insight` · **ADR** ADR-051 amandemen A1 (Proposed)
· **Kontrak** `docs/04_SERVICE_CONTRACTS.md` §30.17.11 · **Fit-gap** `docs/agent-reports/efn-odoo-functional.md`
"25 Nov 2026 — G66b".

## Tujuan

Planner dan kepala gudang PB masuk ke modul Produksi (`insight-web` `/produksi`) dengan akun ESB masing-masing.
Setiap login ESB yang berhasil dicatat di Odoo sebagai **user internal yang diarsipkan** (shadow user) agar
pengguna dan cermin perannya sudah ada saat cutover ESB → Odoo (G43). Shadow user **tidak bisa login** ke Odoo dengan
cara apa pun dan **tidak memegang grup PP**.

## Antarmuka (dipanggil `services/prodplan`, bukan browser)

`POST https://odoo-rpc-gateway:8443/json/2/efn.esb.identity/efn_esb_provision`
(mTLS CN `prodplan-odoo`, `Authorization: bearer <API key user teknis efn_esb_provisioner>`).

Body (kunci tingkat atas selain `payload` → HTTP 422 dari `signature.bind` controller json2):

```json
{"payload": {"esb_account": "pb", "username": "<username_norm>", "full_name": "<≤128>",
             "company_code": "PB", "role_id": 12, "pp_roles": ["pp_planner"], "branch_ids": [101, 102]}}
```

| Kunci | Aturan |
|---|---|
| (semua) | Wajib ada **tepat** 7 kunci ini. Kunci lain (termasuk `password`, `token`, `email`, `phone`) → `invalid_input`. JSON kanonik ≤ 16 KiB → selebihnya `payload_too_large` |
| `esb_account` | `bacha` \| `cup` \| `imd` \| `pb` \| `wtz` (`efn_insight` `ESB_BRANDS`) |
| `username` | string; di-trim + lowercase oleh Odoo; lalu `^[a-z0-9._@-]{1,64}$` |
| `full_name` | string ≤ 128 tanpa karakter kontrol; kosong → nama user = username |
| `company_code` | string ≤ 64 atau `null`; **hanya pengecekan silang** (log peringatan bila tidak cocok kode PT cabang), bukan kunci |
| `role_id` | int 0..2³²−1 atau `null` (disimpan 0) |
| `pp_roles` | list unik ⊆ {`pp_planner`, `pp_warehouse`} (boleh kosong) |
| `branch_ids` | list int 1..2³²−1, ≤ 200 (duplikat dibuang) |

Respons 200: `{"identity_id": <int>, "created": <bool>}` — tanpa PII.

| Galat | HTTP (json2 Odoo 19) | Arti |
|---|---|---|
| `efn_esb_identity:access_denied` (AccessError) | 403 | pemanggil bukan user teknis provisioner, punya grup tambahan, arsip, atau portal |
| `efn_esb_identity:invalid_input` (UserError) | 422 | payload tidak lolos validasi |
| `efn_esb_identity:payload_too_large` (UserError) | 422 | > 16 KiB |
| `efn_esb_identity:esb_provision_no_company` (UserError) | 422 | tidak ada cabang aktif `efn.insight.esb.branch (esb_account, branch_id)` |
| `efn_esb_identity:esb_provision_login_conflict` (UserError) | 422 | login `esb:<akun>:<username>` sudah dipakai user lain (tidak pernah ditautkan) |

## Perilaku

- **Model `efn.esb.identity`**: unik `(esb_account, esb_username)` dan unik `user_id`; `company_id` = PT pertama
  (id terkecil) dari cabang; record rule multi-company `company_id in company_ids`; ACL: Access Rights
  (`base.group_erp_manager`) baca, Superadmin (`base.group_system`) baca + tulis; **tidak ada** create/unlink untuk
  siapa pun (identitas hanya dibuat metode provisioning, tidak pernah dihapus). Grup provisioner **tanpa ACL**
  (bahkan baca), agar API key-nya tidak bisa mendaftar username lewat `search_read`.
- **Shadow user baru**: `active=False`, `group_ids = Command.set([base.group_user])` (+ `base.group_multi_company`
  bila cabang mencakup >1 PT, mis. Bacha BCF+BCR — CE juga menambahkannya), tanpa password, tanpa email,
  `company_ids` = semua PT cabang, context `no_reset_password`. `efn_insight_esb_branch_ids` tidak diisi.
- **Login berikutnya**: cermin (`esb_pp_roles`, `esb_branch_ids`, `esb_role_id`, `company_id`, `last_login_at`)
  ditimpa (tambah dan kurang). Selama `state != activated`, invarian shadow user ditegakkan ulang (arsip, grup
  minimal, PT dari cabang, nama) — unarchive manual sebelum aktivasi dibatalkan pada login berikutnya (fail safe).
  `revoked` → kembali `shadow`.
- **Cron harian** (01:15 WIB): `shadow` tanpa login > 90 hari → `revoked`, cermin dikosongkan; user tidak dihapus.
- **Aktivasi (Superadmin saja)** — urutan saat cutover:
  1. Di form user (arsip): ganti login dari `esb:...` ke format final, isi email kerja.
  2. Identitas ESB → tombol **Tandai diaktifkan (migrasi)** (`action_efn_activate`; cek: Superadmin, login bukan
     `esb:`, email terisi). Setelah ini provisioning hanya memperbarui cermin, tidak menyentuh `res.users`.
  3. Unarchive user (standar CE), tetapkan peran lewat Penetapan Peran `efn_role` berdasarkan cermin, kirim reset
     password (`auth_signup`, butuh SMTP), wajibkan TOTP.
- **User teknis** `efn_esb_provisioner` (data, `noupdate`): Internal User + `group_esb_provisioner`, satu company
  (company **aktif** dengan id terkecil; bukan `base.main_company` yang bisa diarsipkan, mis. di efn-prod), tanpa password, **tanpa API key** (dibuat Superadmin lewat runbook API key, secret
  `odoo_api_key_esb_provisioner`, rotasi 30 hari). Grup ini ada di `efn_role.TECHNICAL_GROUP_XMLIDS` (tidak bisa
  menjadi isi peran maupun menerima peran).
- **Log**: `esb_provision identity=<id> uid=<id> created=<bool> state=<s> roles=<n> branches=<n>` — tanpa
  username, nama, atau login.

## Risiko yang dimitigasi

| Risiko | Mitigasi |
|---|---|
| prodplan disusupi → membuat user berhak | user selalu arsip, hanya Internal User; grup PP tidak pernah diberikan (SEC-228); pemanggil fail closed bila punya grup lain |
| Pengambilalihan akun Odoo yang ada (nama/email sama) | tidak pernah menaut; login bentrok (case-insensitive) → ditolak |
| Kredensial ESB bocor ke Odoo | payload tertutup: `password`/`token`/kunci lain ditolak; tidak ada field untuk menyimpannya |
| "Bom waktu" unarchive massal user bergrup sensitif | cermin peran = data, bukan grup; aktivasi manual per user oleh Superadmin |
| Klien RPC menyetel context (`active_test`, `allowed_company_ids`) | context klien dibuang (`check_caller`) |
| Duplikat karena dua login bersamaan | `pg_advisory_xact_lock` per (akun, username) + constraint unik |
| Data pribadi di log (UU PDP) | log tanpa username/nama/login; display_name identitas tanpa username |

## Referensi desain (ADR-031)

`auth_ldap` (Odoo CE 19) `res.company.ldap._get_or_create_user`: konsep "cari dulu lalu buat", template grup
tetap, password tidak pernah disimpan. Tidak ada kode yang disalin; tidak ada dependency OCA/Cybrosys/Enterprise.

## Cara uji

```
odoo -d <db> -i efn_esb_identity --test-tags /efn_esb_identity   # tag efn, TransactionCase
```

Manual (dev): buat cabang di Settings → Users & Companies → Cabang ESB (Insight); buat API key untuk
`efn_esb_provisioner`; `curl --cacert ... -H "Authorization: bearer $KEY" -H 'Content-Type: application/json'
-d '{"payload": {...}}' https://odoo:8069/json/2/efn.esb.identity/efn_esb_provision` → `{"identity_id": ..,
"created": true}`; panggil lagi → `created: false`; Settings → Users (filter Diarsipkan) → user `esb:pb:<username>`
tanpa grup selain Internal User; login dengan password apa pun gagal.

## Asumsi / belum terverifikasi

- Bentuk body `/json/2` (`{"payload": {...}}` → kwarg `payload`) diverifikasi dari source `addons/rpc/controllers/json2.py`
  19.0 (WebFetch 25 Nov 2026); pemetaan UserError → 422 / AccessError → 403 mengikuti catatan repo (L-15), belum diuji
  lewat HTTP nyata untuk metode ini.
- `res.users.create` CE 19 menyinkronkan `active` ke partner (partner ikut arsip) — dari ringkasan source, tidak diuji.
- `authenticate(credential, user_agent_env)` sebagai metode model di 19.0 — dari source; diuji di test_14.
- Review akses `efn_role` mungkin melewati user arsip (fit-gap risiko 3) — belum dicek.
- `efn_audit` mencatat create `res.users` termasuk login `esb:<akun>:<username>` (username ESB tercatat di log audit).

## Belum dibangun

- Penautan manual identitas ke user yang sudah ada (fit-gap: hanya Superadmin) — tidak ada UI; ACL tanpa create.
- Rekonsiliasi daftar user ESB (esb-extract) dan impor batch saat cutover (Q-ESBU3).
- Penghapusan/anonimisasi identitas pasca-cutover (Q-ESBU4, keputusan DPO).
