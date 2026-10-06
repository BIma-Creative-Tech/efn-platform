# efn_audit

Log audit nilai lama/baru per aturan model untuk Odoo CE 19 — pengganti OCA `auditlog` + `efn_setup_audit/patches.py` (Gelombang 10). Implementasi sendiri, **LGPL-3**, `depends: base, efn_core`.

- **Spesifikasi:** `docs/08_ODOO_FIT_GAP.md` §5.1 (F-17). **ADR:** ADR-031 (tanpa dependency modul pihak ketiga), ADR-020 (poin member di BFF, bukan Odoo).
- **Risiko dimitigasi:** R-S10 (insider mengubah harga/stok/parameter), R-B06 (fraud promo kasir), SEC-07 (perubahan HP member), bug OCA auditlog × ORM 19 (`ThrowAwayCache` → `AssertionError: Could not find all values … to flush them` saat membuat produk). **UU PDP:** HP/email disimpan ter-mask; log partner dihapus saat erasure (`efn_privacy`).
- **Aturan per model** ada di `efn_setup_audit` (data-only: harga, pricelist, promo loyalty, opname stok, kontak, parameter `efn.*`). Modul ini tidak bergantung `product`/`stock`/`loyalty`.

## Model
| Model | Isi | Catatan |
|---|---|---|
| `efn.audit.rule` | `name`, `model_id` (unik), `model_name`, `active`, `log_create/write/unlink`, `tracked_field_ids` (kosong = semua field memenuhi syarat), `excluded_field_ids`, `masked_field_ids`, `filter_domain`, `capture_unlink_values`, `retention_days` | Constraint: bukan `efn.audit.*`/transient/abstrak; field milik model; domain divalidasi (`safe_eval` + `_search`). Tanpa `company_id` (aturan per model untuk seluruh DB — pengecualian konvensi, sengaja) |
| `efn.audit.log` | `user_id` (uid asli walau `sudo()`), `is_sudo`, `operation` (`create/write/unlink/purge`), `model_id`, `model_name`, `res_id`, `res_name`, `company_id` (company record sesudah write / sebelum delete), `rule_id`, `line_ids`, `note` (purge) | Index `(model_name, res_id)` |
| `efn.audit.log.line` | `field_id`, `field_name`, `field_label`, `old_value`, `new_value`, `old_ref`/`new_ref` (`model,id[,id…]` untuk M2o/M2m), `is_masked`, `company_id` (related stored dari log) | |

"Field memenuhi syarat" = stored, bukan `one2many`/`binary`, bukan `id/display_name/create_*/write_*`, bukan `message_*`/`activity_*`/`website_message_ids`.
**Denylist global** (tidak pernah dicatat walau dipilih): nama mengandung `password`, `secret`, `token`, `api_key`, `totp`, `signature`; semua field `binary`; `res.partner.efn_member_hash`.
**Format nilai:** M2o → nama (+ ref), M2m → nama dipisah koma (maks 20) + ref id, selection → label, float → presisi field (bila ada `digits`), monetary → `repr`, date/datetime → ISO, html → teks polos, semua dipotong 2000 karakter.
**Masking** (`masked_field_ids`, sebelum masuk DB): nama field mengandung `phone`/`mobile` → `mask_phone` efn_core setelah normalisasi `62…` (`62812****890`); `email` → `a***@domain`; lainnya → `****(panjang)`. Nilai lengkap tidak pernah disimpan.

## Mekanisme hook (`models/base.py`)
Override `create`/`write`/`unlink` di `_inherit = "base"` (pola resmi Odoo, tanpa monkeypatch registry). Hook berada di dasar MRO sehingga melihat `vals` final setelah override modul lain (mis. kunci HP `efn_pos_otp_gate`) dan tidak perlu restart setelah install/upgrade.

Alur `write`: aturan dari `ormcache` (namedtuple + frozenset, bukan recordset) → keluar cepat bila tidak ada aturan / registry belum siap (install/upgrade) / transient/abstrak / model log → **irisan field dilacak ∩ vals sebelum membaca apa pun** (kosong → langsung `super`; contoh `stock.quant.write({'quantity': …})` di setiap mutasi stok) → `filtered_domain` → `read(load=False)` lama → `super().write` → `read` baru → hanya field berubah → satu `create` batch log + baris (sudo). `create` mencatat hanya field dilacak yang ada di `vals`; `unlink` menulis header (+ snapshot bila `capture_unlink_values`). Tidak ada `ThrowAwayCache`, penukaran cache, `invalidate_all`, atau SQL ke tabel model yang diaudit. Kegagalan menulis log **tidak ditelan** (transaksi gagal).
`efn.audit.rule` sendiri **selalu** diaudit (hard-coded). Cache aturan dibersihkan pada create/write/unlink aturan (`registry.clear_cache()` — disinyalkan ke worker lain), jadi aturan baru langsung aktif.

## Keamanan
- Grup (kategori *EFN Audit*): **Auditor** (`efn_audit.group_auditor`, baca log) dan **Manager** (`efn_audit.group_manager`, implies Auditor, kelola aturan). `base.group_system` → Manager.
- ACL: aturan — auditor `r`, manager `rwcu`; log & baris — auditor `r` saja. Tidak ada grup dengan create/write/unlink log.
- **Immutable:** `write`/`unlink` log & baris → `UserError` selalu (termasuk admin dan `sudo()`), kecuali di dalam metode privat `_efn_audit_purge_*` yang memasang penanda di `cr.cache` (bukan context — context bisa diisi klien RPC).
- Record rule multi-company (grup auditor): `['|', ('company_id', '=', False), ('company_id', 'in', company_ids)]` untuk log dan baris. Log tanpa company (parameter sistem, aturan, kontak tanpa company) terlihat oleh semua auditor.
- Batas: superuser PostgreSQL tetap bisa menghapus baris (mitigasi = least privilege DB, docs/04 §7.3). Tamper-evident (hash berantai / kirim ke ClickHouse) = opsi fase 1.
- `user_id` `ondelete="restrict"`: user yang punya log tidak bisa dihapus (arsipkan saja) — jejak tidak boleh hilang.

## Retensi & erasure
- `@api.autovacuum _gc_efn_audit_logs` (cron *Base: Auto-vacuum internal data*, harian): hari = `rule.retention_days` (>0) atau `ir.config_parameter` `efn.audit.retention_days` (tidak valid/hilang → 730; `0` = simpan selamanya). Maks 10.000 log per jalan; satu log `purge` dengan `note="retensi: n log"`.
- Erasure UU PDP: `env["efn.audit.log"]._efn_audit_purge_records(model_name, res_ids)` → hapus log record tsb, kosongkan `old/new_value` + `old/new_ref` di baris log model lain yang menunjuk record tsb, tulis satu log `purge` tanpa id/nama. Dipakai `efn_privacy`.

## Tampilan
Menu atas **Audit** (grup Auditor): *Log* (list/form + baris; search model/record/user/operasi/company/field; filter *Hari ini*, *7 hari* (default), *Harga*, *Promo*, *Pelanggan*, per operasi; group by model/user/tanggal) dan *Aturan* (grup Manager).

## Cara uji manual
1. `odoo -d <db> -i efn_setup_audit` (menarik `efn_audit`).
2. Ubah harga jual produk → *Audit → Log*: operasi Write, baris `list_price` lama/baru.
3. Ubah HP kontak → baris `phone` ter-mask (`62812****890`), *Disamarkan* tercentang.
4. Coba hapus log dari UI/RPC sebagai admin → ditolak.
5. Ubah parameter `efn.pos.otp_required_for_promo` → tercatat; parameter non-`efn.*` tidak.

## Test (`tests/`, tag `efn`)
`test_audit_hook.py`: create/write/unlink sesuai flag, hanya field di vals saat create, field tidak berubah → tanpa log, field tidak dilacak → tanpa `read` (mock), snapshot unlink, format M2o/M2m + ref, uid asli + `is_sudo`, aturan baru/arsip langsung berlaku tanpa restart, perubahan aturan selalu tercatat, `company_id` record, `filter_domain` `ir.config_parameter`, validasi aturan (domain salah, model log, transient, field model lain), masking HP/email + cek SQL nilai lengkap tidak ada di DB, denylist (`signature`, `password`, binary).
`test_audit_security.py`: non-auditor ditolak, auditor PT A tidak melihat log PT B (log & baris), auditor tidak bisa kelola aturan, admin = manager, log immutable untuk sudo/admin dan context palsu, autovacuum (global, per aturan, 0 = selamanya, parameter tidak valid → 730), purge erasure (log + ref + log purge tanpa PII), performa: model tanpa aturan **0 query tambahan**; write 50 partner ber-aturan = +2 query (batch).
Regresi bug flush OCA, `stock.quant` tanpa read, dan import 200 produk ada di `efn_setup_audit/tests/test_setup_audit.py`.

## Referensi
Dipelajari sebagai **referensi desain** (ADR-031); **tidak ada kode yang disalin** — implementasi ditulis dari spesifikasi `docs/08` §5.1 dan API Odoo CE 19:
- OCA/server-tools `auditlog` 19.0.2.0.0 (AGPL-3): konsep rule/log/line, pengecualian field, snapshot saat delete, autovacuum. Tidak dipakai: patch kelas registry di `_register_hook`, `ThrowAwayCache`, mode full/fast, log HTTP session/request.
- Cybrosys `user_audit` 19.0.1.0.0 (LGPL-3): ide filter per user. Tidak dipakai: log dari JS client, tanpa nilai lama/baru, log bisa dihapus lewat wizard.

## Batasan / belum terverifikasi
- Overhead diukur di test (container CI lokal): import 200 `product.template` dengan aturan semua field ≈ +22 query (+5%), waktu ±1,2 s vs 0,8 s (satu run, belum PoC beban 100 order POS / 1.000 produk di staging). Bila mahal: persempit `tracked_field_ids` produk ke harga/pajak/kategori/aktif.
- Penulisan selama install/upgrade modul (`registry.ready = False`) tidak dicatat (by design, seperti data XML).
- Aturan yang dibuat lalu di-rollback di savepoint yang sama dengan penulisan berikutnya bisa meninggalkan cache aturan basi sampai `reset_changes` registry (kasus tepi; FK `rule_id` tidak valid → transaksi gagal, bukan data hilang).
- `filter_domain` dievaluasi dengan `safe_eval` tanpa variabel (`uid`, `context_today` tidak tersedia).
- Enkripsi kolom untuk nilai lengkap PII (bila auditor/DPO mewajibkan) belum dikerjakan — menunggu keputusan klien.
- Action binding "Lihat log audit" di form model ber-aturan belum dibuat (opsional spesifikasi).
