# efn_privacy

Penghapusan data pelanggan (UU PDP 27/2022) di Odoo atas permintaan dari Super-App: Odoo **menarik** daftar erasure dari BFF, menganonimkan data pelanggan, lalu melapor selesai (`docs/04 §8.4`, `§8.8`).

- **Fit-gap:** F-29 / E-16 (`docs/08 §4.2`). Kontrak: `docs/04 §1` (rumus `member_hash`), §8.4, §8.6 (kepemilikan pepper: Superadmin), §8.8. Desain DSAR: `docs/compliance/PDP_DSAR_DESIGN.md`.
- **Risiko dimitigasi:** data pribadi tertinggal di ERP setelah member menghapus akun; kebocoran nomor HP (BFF hanya mengirim hash, bukan nomor); jejak audit lama (chatter/auditlog) yang menyimpan nomor lama.
- **Enkripsi in transit:** `GET`/`POST` ke BFF lewat `efn.bff.client` (HTTPS + CA internal, service key dari file secret).

## Isi
- `res.partner.efn_member_hash` — `Char`, compute **stored + indexed**, `groups="base.group_system"`, readonly, tidak dicopy, **tidak** dimuat ke POS. Nilai = `hex(sha256(pepper_hex + member_ref))` dengan `member_ref` dari `phone` (+`mobile` bila ada) ternormalisasi `^62…` (`efn_core.partner_member_ref`). Nomor kosong/asing/tidak valid → kosong.
- `res.partner.efn_erasure_request_id` — penanda request yang menganonimkan partner (admin saja).
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
   - `res.partner` dengan hash cocok — **termasuk arsip & lintas company** (sudo, `active_test=False`): nama → `Pelanggan dihapus <8 char request>`, kosongkan `phone`, `mobile`, `email`, `street`, `street2`, `city`, `zip`, `state_id`, `vat`, `website`, `function`, `comment`, `image_1920`, `barcode`; arsipkan (kecuali partner milik `res.users` — di-log); **tidak di-unlink** (FK `pos.order`/`account.move`).
   - `pos.order.email`/`mobile` (salinan kontak stored) dikosongkan; order & dokumen akuntansi **dipertahankan** dengan partner teranonim (retensi pembukuan, menunggu DPO).
   - Chatter partner (`mail.message` + `mail.tracking.value`) dihapus; `email_from` pesan yang ditulis partner di dokumen lain dikosongkan. Write anonimisasi memakai `tracking_disable`.
   - OCA `auditlog.log` (+lines) untuk `res.partner` tsb dihapus bila terpasang (aturan `efn_setup_audit` mencatat HP lama, termasuk write anonimisasi ini).
   - `privacy.log` standar (`privacy_lookup`): nama `Member`, email `erased@efn.invalid` (harus mengandung `@`, lihat temuan `docs/08 §2` no. 9), detail = id request + jumlah — tanpa hash/HP.
   - **Commit**, lalu `POST /v1/pos/privacy/erasures/:request_id/complete {store:"odoo", records:n}`; `n` = jumlah partner bertanda request ini (termasuk run sebelumnya) → **idempoten**: rerun atau complete yang gagal mengirim angka yang sama; `n = 0` tetap complete.
3. Log hanya memuat `request_id` & jumlah (tanpa HP, email, hash, pepper).

Permintaan manual non-member (nama/email) tetap memakai wizard standar **`privacy_lookup`** (tidak dikustom).

## Uji manual
1. Pasang secret `member_hash_pepper` (turunan dari `otp_pepper` yang sama dengan BFF), restart/biarkan, jalankan server action hitung ulang.
2. Buat partner HP `0812…`; bandingkan `efn_member_hash` (mode developer, admin) dengan `member_hash` di `platform.member` BFF.
3. Ajukan `DELETE /v1/me` di Super-App (dev: grace 0), tunggu cron (atau jalankan manual) → partner teranonim & diarsipkan, order POS tetap ada tanpa email/HP, `privacy.log` bertambah, `dsar_request.stores_pending` tanpa `odoo`.

## Test otomatis
`tests/test_member_hash.py`: **test vector bersama BFF** (pepper_hex `46df3d2f…061f` dari `otp_pepper` uji `test-otp-pepper-v1`, `6281234567890` → `8930b421…6653`, sama dengan `services/api-gateway/src/config/app-config.spec.ts`) untuk format `0812-…`, `+62 …`, `62…`, `0062 …`; validasi file pepper; recompute saat `phone` berubah / nomor asing / kosong; pepper hilang tidak memblokir simpan; index & grup; tidak dimuat POS; backfill admin-only.
`tests/test_erasure.py` (TestPoSCommon, BFF di-mock): anonimisasi partner aktif + arsip di company lain, `pos.order.email/mobile` kosong, chatter hilang, `privacy.log` placeholder, `complete` dengan jumlah benar, log tanpa PII; rerun idempoten (angka sama, tanpa log ganda); hash tak dikenal → complete 0; auditlog dihapus (skip bila tidak terpasang); kegagalan satu request tidak menghentikan lainnya; format `{items}`; pepper hilang → cron gagal tanpa memanggil BFF; BFF tak terjangkau → cron gagal.

## Batasan / belum terverifikasi
- Belum pernah di-install/dijalankan.
- `@api.depends` dengan callable (untuk `mobile` opsional), `env.add_to_compute` + `flush_recordset` untuk backfill, `threading.current_thread().testing` / `registry.in_test_mode()` (guard commit) diasumsikan sama dengan 17/18.
- Field `auditlog.log.model_model` (OCA 18.0) diasumsikan ada di 19.0 (fallback `model_id.model`).
- Kontak anak (`child_ids`), rekening bank partner (`bank_ids`), `ref`, dan lampiran (`ir.attachment`) partner tidak dianonimkan — menunggu keputusan DPO. Faktur ber-NPWP/perorangan dipertahankan (DPO). Opsi `efn.privacy.partner_retention_days` (anonimisasi partner POS-only lama) **belum** dibuat — hanya setelah DPO setuju.
- Instalasi pertama menghitung hash untuk semua partner; tanpa pepper semua kosong sampai server action dijalankan.
- `efn_member_hash` ikut tercatat auditlog `res.partner` bila field berubah (nilai hash, bukan HP); dihapus saat erasure.
