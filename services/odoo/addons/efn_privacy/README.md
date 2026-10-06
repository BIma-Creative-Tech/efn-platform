# efn_privacy

Penghapusan data pelanggan (UU PDP 27/2022) di Odoo atas permintaan dari Super-App: Odoo **menarik** daftar erasure dari BFF, menganonimkan data pelanggan, lalu melapor selesai (`docs/04 §8.4`, `§8.8`).

- **Fit-gap:** F-29 / E-16 (`docs/08 §4.2`). Kontrak: `docs/04 §1` (rumus `member_hash`), §8.4, §8.6 (kepemilikan pepper: Superadmin), §8.8. Desain DSAR: `docs/compliance/PDP_DSAR_DESIGN.md`.
- **Risiko dimitigasi:** data pribadi tertinggal di ERP setelah member menghapus akun; kebocoran nomor HP (BFF hanya mengirim hash, bukan nomor); jejak audit lama (chatter/log `efn_audit`) yang menyimpan nomor lama (ter-mask) dan nama.
- **Enkripsi in transit:** `GET`/`POST` ke BFF lewat `efn.bff.client` (HTTPS + CA internal, service key dari file secret).

## Isi
- `res.partner.efn_member_hash` — `Char`, compute **stored + indexed**, `groups="base.group_system"`, readonly, tidak dicopy, **tidak** dimuat ke POS. Nilai = `hex(sha256(pepper_hex + member_ref))` dengan `member_ref` dari `phone` (+`mobile` bila ada) ternormalisasi `^62…` (`efn_core.partner_member_ref`). Nomor kosong/asing/tidak valid → kosong.
- `res.partner.efn_erased` — flag boolean "dianonimkan oleh erasure UU PDP" (admin saja). **Tidak** ada request_id/member_hash yang disimpan (SEC-31, lihat bagian di bawah).
- `efn.privacy.erasure.pending` — penanda SEMENTARA idempotensi `complete` (request_id + jumlah, tanpa id partner), dihapus setelah complete diterima BFF.
- Server action **"EFN: hitung ulang member hash (admin)"** (`_efn_recompute_member_hash`, hanya `base.group_system`) — backfill setelah pepper dipasang / rotasi.
- Cron **"EFN Privacy: proses permintaan hapus data (UU PDP)"** tiap 15 menit (OdooBot) — `efn.privacy.erasure._cron_pull_erasures`.

## Pepper
| Env | Default | Isi |
|---|---|---|
| `EFN_MEMBER_HASH_PEPPER_FILE` | `/run/secrets/member_hash_pepper` | `pepper_hex` = hex(HKDF-SHA256(`otp_pepper`, salt "", info `efn:member_hash:v1`, 32 B)) — 64 hex. Odoo **tidak** pernah menerima `otp_pepper` |

Dibaca dari file, divalidasi 64-hex, di-cache per proses dengan kunci (path, mtime, size) → rotasi file terbaca tanpa restart (lalu jalankan server action). Tidak pernah di-log/disimpan di DB. **Pepper tak terbaca:** simpan partner/POS tetap jalan (hash kosong, WARNING sekali per proses — sengaja bukan ERROR agar instalasi/CI tanpa secret tidak gagal); cron erasure **gagal keras** (ERROR + `UserError`) **tanpa** memanggil BFF.

## Cron erasure
1. `GET /v1/pos/privacy/erasures?store=odoo` → `[{request_id, member_hash}]` (juga menerima `{items:[…]}`). Item tak valid dilewati; maksimal `efn.privacy.erasure_batch` (default 100) per run.
2. Per request (savepoint sendiri; satu gagal tidak menghentikan yang lain):
   - `res.partner` dengan hash cocok — **termasuk arsip & lintas company** (sudo, `active_test=False`): nama → `Pelanggan dihapus` (tanpa potongan id), `efn_erased = True`, kosongkan `phone`, `mobile`, `email`, `street`, `street2`, `city`, `zip`, `state_id`, `vat`, `website`, `function`, `comment`, `image_1920`, `barcode`; arsipkan (kecuali partner milik `res.users` — di-log); **tidak di-unlink** (FK `pos.order`/`account.move`).
   - `pos.order.email`/`mobile` (salinan kontak stored) dikosongkan; order & dokumen akuntansi **dipertahankan** dengan partner teranonim (retensi pembukuan, menunggu DPO).
   - Chatter partner (`mail.message` + `mail.tracking.value`) dihapus; `email_from` pesan yang ditulis partner di dokumen lain dikosongkan. Write anonimisasi memakai `tracking_disable`.
   - Log `efn.audit.log` (+baris) untuk `res.partner` tsb dihapus lewat `efn_audit` `_efn_audit_purge_records("res.partner", ids)` (aturan `efn_setup_audit` mencatat HP/email lama ter-mask + nama, termasuk write anonimisasi ini); ref di baris log model lain yang menunjuk partner dikosongkan; satu log `purge` tanpa id/nama. `efn_audit` dependency wajib (ADR-031, tanpa OCA).
   - `privacy.log` standar (`privacy_lookup`): nama `Member`, email `erased@efn.invalid` (harus mengandung `@`, lihat temuan `docs/08 §2` no. 9), detail = jumlah saja — tanpa request_id, hash, HP (SEC-31).
   - **Commit**, lalu `POST /v1/pos/privacy/erasures/:request_id/complete {store:"odoo", records:n}`; `n` = jumlah di penanda sementara (termasuk run sebelumnya) → **idempoten**: complete yang gagal lalu diulang mengirim angka yang sama; `n = 0` tetap complete. Complete 2xx → penanda dihapus.
3. Log hanya memuat `request_id` & jumlah (tanpa HP, email, hash, pepper).

Permintaan manual non-member (nama/email) tetap memakai wizard standar **`privacy_lookup`** (tidak dikustom).

## SEC-31 — anonimisasi tanpa tautan (Gelombang 9, docs/04 §8.13)
Tujuan: setelah erasure selesai, tidak ada nilai di Odoo yang menautkan partner teranonim ke `platform.dsar_request` (request_id) atau ke `member_hash` (yang bisa dibalik ke nomor HP oleh pemegang pepper).

| Data | Sebelum | Sekarang |
|---|---|---|
| `res.partner.efn_erasure_request_id` | request_id penuh, permanen | **dihapus** (migrasi 19.0.1.1.0 men-NULL-kan lalu ORM men-drop kolom); diganti flag boolean `efn_erased` (admin saja) |
| Nama partner | `Pelanggan dihapus <8 char request>` | `Pelanggan dihapus` (juga `complete_name` & `account_move.invoice_partner_display_name` lama dibersihkan migrasi) |
| `res.partner.efn_member_hash` | kosong karena HP dikosongkan | sama, kini **diverifikasi eksplisit** (gagal → savepoint rollback, request tidak di-complete) |
| `privacy.log.execution_details` | memuat request_id | `EFN erasure (UU PDP): n partner dianonimkan …` (migrasi menghapus request_id dari log lama) |
| Idempotensi `complete` | hitung partner ber-`efn_erasure_request_id` | **penanda sementara** `efn.privacy.erasure.pending` (`request_id`, `records`; TANPA id partner/hash/HP) |

**Desain idempotensi (dipilih: penanda sementara, bukan nilai turunan di `privacy.log`).** Nilai apa pun yang diturunkan secara deterministik dari request_id (hash/HMAC) tetap bisa ditautkan oleh siapa pun yang tahu request_id, jadi tidak lebih aman daripada request_id itu sendiri. Penanda dibuat di savepoint yang sama dengan anonimisasi (atomik), jumlahnya ditambah bila run berikutnya menemukan partner baru, dan **dihapus begitu BFF menjawab 2xx untuk `complete`**. Selama request belum selesai, penanda hanya berisi request_id + angka; tidak pernah id partner. Sisa penanda (request dibatalkan di BFF setelah anonimisasi) dibersihkan autovacuum setelah 30 hari. Model tanpa `company_id` (pengecualian konvensi): data teknis proses lintas company; ACL baca saja untuk `base.group_system`, ditulis cron (sudo).

Sisa tautan yang diterima: korelasi waktu (`write_date` partner ≈ `create_date` `privacy.log`) hanya menunjukkan "partner ini dianonimkan pada waktu X", bukan permintaan/nomor mana. Log aplikasi memuat request_id + jumlah (tanpa id partner), mengikuti retensi log.


## Uji manual
1. Pasang secret `member_hash_pepper` (turunan dari `otp_pepper` yang sama dengan BFF), restart/biarkan, jalankan server action hitung ulang.
2. Buat partner HP `0812…`; bandingkan `efn_member_hash` (mode developer, admin) dengan `member_hash` di `platform.member` BFF.
3. Ajukan `DELETE /v1/me` di Super-App (dev: grace 0), tunggu cron (atau jalankan manual) → partner teranonim & diarsipkan, order POS tetap ada tanpa email/HP, `privacy.log` bertambah, `dsar_request.stores_pending` tanpa `odoo`.

## Test otomatis
`tests/test_member_hash.py`: **test vector bersama BFF** (pepper_hex `46df3d2f…061f` dari `otp_pepper` uji `test-otp-pepper-v1`, `6281234567890` → `8930b421…6653`, sama dengan `services/api-gateway/src/config/app-config.spec.ts`) untuk format `0812-…`, `+62 …`, `62…`, `0062 …`; validasi file pepper; recompute saat `phone` berubah / nomor asing / kosong; pepper hilang tidak memblokir simpan; index & grup; tidak dimuat POS; backfill admin-only.
`tests/test_erasure.py` (TestPoSCommon, BFF di-mock): anonimisasi partner aktif + arsip di company lain, `pos.order.email/mobile` kosong, chatter hilang, `privacy.log` placeholder, `complete` dengan jumlah benar, log tanpa PII; retry complete idempoten via penanda sementara (angka sama, tanpa log ganda, penanda hilang setelah sukses), SEC-31: tidak ada request_id/potongan id/member_hash tersisa di `res_partner`, `privacy_log`, penanda, `mail_message`, `pos_order`; complete ditolak → penanda tetap; autovacuum penanda > 30 hari; penanda hanya admin; hash tak dikenal → complete 0; log efn_audit partner dihapus (log partner lain tetap, log purge tanpa id/nama); kegagalan satu request tidak menghentikan lainnya; format `{items}`; pepper hilang → cron gagal tanpa memanggil BFF; BFF tak terjangkau → cron gagal.

## Batasan / belum terverifikasi
- Test otomatis dijalankan di CI lokal (Docker, image `efn-odoo:ci`); belum diuji di staging dengan BFF nyata selain smoke E2E dev.
- `@api.depends` dengan callable (untuk `mobile` opsional), `env.add_to_compute` + `flush_recordset` untuk backfill, `threading.current_thread().testing` / `registry.in_test_mode()` (guard commit) diasumsikan sama dengan 17/18.
- Kontak anak (`child_ids`), rekening bank partner (`bank_ids`), `ref`, dan lampiran (`ir.attachment`) partner tidak dianonimkan — menunggu keputusan DPO. Faktur ber-NPWP/perorangan dipertahankan (DPO). Opsi `efn.privacy.partner_retention_days` (anonimisasi partner POS-only lama) **belum** dibuat — hanya setelah DPO setuju.
- Instalasi pertama menghitung hash untuk semua partner; tanpa pepper semua kosong sampai server action dijalankan.
- `efn_member_hash` masuk denylist global `efn_audit` (tidak pernah dicatat).
