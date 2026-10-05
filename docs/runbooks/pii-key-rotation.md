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

## Kunci bocor (darurat)
Jalankan langkah 1–5 segera (jendela jam, bukan hari), anggap ciphertext yang tersalin bersama kunci sebagai data bocor → [breach-response-72h.md](breach-response-72h.md). `member_hash`/`user_hash` (pseudonim) tidak terpengaruh kunci ini, tetapi bergantung pada `otp_pepper` ([secret-rotation.md](secret-rotation.md)).
