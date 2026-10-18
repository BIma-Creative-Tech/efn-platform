# Prosedur: Rotasi kunci enkripsi PII (`pii_encryption_key`)

Kontrak: `docs/04_SERVICE_CONTRACTS.md` §7.2. Kolom: `price_hunter_report.user_enc`, `loyalty_ledger.member_ref_enc`, `otp_audit.ip_enc` (AES-256-GCM, format `bytea` = versi (1 byte) ‖ IV 12 ‖ ciphertext ‖ tag 16, AAD `efn:<tabel>.<kolom>:v1`). Pemilik kode: `efn-bff-engineer` (`services/api-gateway/src/common/pii/`). Pemilik prosedur & secret: devsecops.

Kapan: terjadwal 12 bulan, segera bila kunci dicurigai bocor, staf dengan akses secret keluar, atau backup tanpa enkripsi tersebar.

## Prinsip
- Key ring: byte versi pada ciphertext menentukan kunci untuk dekripsi. Kunci aktif (`PII_ACTIVE_KEY_VERSION`) hanya dipakai untuk enkripsi baru.
- Kunci lama **tidak dihapus** sampai job re-encrypt selesai dan diverifikasi, dan sampai semua backup yang berisi ciphertext versi lama melewati retensi (14 base backup + object lock) — atau arsipkan kunci lama offline berlabel versi.
- Kehilangan kunci = data PII terenkripsi tidak bisa dibaca (notifikasi pelanggan Price Hunter gagal, forensik IP hilang). Simpan salinan offline (brankas/HSM, dua orang).

## Langkah (v1 → v2)
1. **Buat kunci baru** (mesin admin): `openssl rand -base64 32` → GitHub Environment secret `PII_ENCRYPTION_KEY_V2`.
2. **Devsecops**: tambah baris `pii_encryption_key_v2 b64key32` ke `scripts/secrets.list`, secret top-level + mount ke `api-gateway` di `infra/compose/docker-compose.yml`, env `PII_ENCRYPTION_KEY_V2_FILE=/run/secrets/pii_encryption_key_v2`. **Belum** mengubah `PII_ACTIVE_KEY_VERSION` (tetap `1`). Deploy → BFF bisa mendekripsi v1 & v2 (koordinasi: BFF memuat key ring dari `PII_ENCRYPTION_KEY_V<n>_FILE`).
3. **Aktifkan v2**: ubah `PII_ACTIVE_KEY_VERSION: "2"` → deploy. Baris baru terenkripsi v2.
4. **Re-encrypt** (job BFF, idempoten, batch, di luar jam sibuk): `SELECT ... WHERE get_byte(<kolom>, 0) = 1` → dekripsi v1 → enkripsi v2 → `UPDATE ... WHERE id = $1 AND <kolom> = $old` (optimistic). Catat jumlah per tabel.
5. **Verifikasi**: `SELECT count(*) FROM platform.price_hunter_report WHERE get_byte(user_enc,0)=1;` (dan tabel lain) = 0. Uji fungsional: notifikasi Price Hunter, DSAR export.
6. **Pensiunkan v1**: base backup baru (`systemctl start efn-backup.service`), arsipkan kunci v1 offline berlabel tanggal, hapus secret `pii_encryption_key` dari compose/GitHub setelah retensi backup lewat (restore backup lama butuh kunci v1 — runbook backup-restore).

## Kunci PII Odoo (ADR-036 Accepted §0, docs/04 §12.5) — status G20: **dev/test saja**
Tiga kunci terpisah, masing-masing 32 byte acak base64, di-mount **hanya** ke service `odoo` (dicek
`scripts/check-encryption.sh` §6/§6b), dibuat `scripts/gen-secrets.sh` (tidak di `secrets.list` sampai AC ADR-036 lulus):

| Secret | Env Odoo | Fungsi | Hilang | Bocor |
|---|---|---|---|---|
| `odoo_pii_key_v<n>` | `EFN_ODOO_PII_KEY_V<n>_FILE` + `EFN_ODOO_PII_ACTIVE_KEY_VERSION` | key ring AES-256-GCM `efnpii2:` | **PII Odoo tidak terbaca permanen** | rotasi key ring (ADR-036 §4.12) + insiden |
| `odoo_pii_bidx_key` | `EFN_ODOO_PII_BIDX_KEY_FILE` | blind index HP/email/NPWP/NIK | pencarian exact gagal sampai dihitung ulang | HP bisa di-brute force dari dump (SEC-140) → ganti + hitung ulang |
| `odoo_pii_name_key` (G20) | `EFN_ODOO_PII_NAME_KEY_FILE` | token nama `efn_name_tokens` (§0.5) | pencarian nama gagal sampai dihitung ulang | ganti + hitung ulang token |

Rotasi `odoo_pii_bidx_key` / `odoo_pii_name_key` **hanya bila bocor** (ADR-036 §0.5, §4.4): (1) buat kunci baru
(`openssl rand -base64 32`), (2) ganti file secret (dev: `secrets/<nama>.txt`; staging/prod setelah kunci masuk
`secrets.list`: Superadmin, [key-custody.md](key-custody.md)), (3) `up -d --force-recreate odoo odoo-tls odoo-ws-tls`,
(4) jalankan job hitung ulang blind index/token `efn_pii` di luar jam operasional (nama aksi/cron persis: README modul
`efn_pii`, builder efn-odoo-engineer B G20), (5) verifikasi pencarian HP/email/nama mengembalikan partner uji.
Selama job berjalan pencarian bisa meleset beberapa menit (data tidak hilang). Menghapus/menimpa file kunci dev
(`gen-secrets.sh` tidak pernah menimpa) setara dengan langkah (1)–(2) tanpa job → pencarian gagal sampai (4).

## Kunci bocor (darurat)
Jalankan langkah 1–5 segera (jendela jam, bukan hari), anggap ciphertext yang tersalin bersama kunci sebagai data bocor → [breach-response-72h.md](breach-response-72h.md). `member_hash`/`user_hash` (pseudonim) tidak terpengaruh kunci ini, tetapi bergantung pada `otp_pepper` ([secret-rotation.md](secret-rotation.md)).
