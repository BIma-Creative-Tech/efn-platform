# Runbook: Respons kegagalan pelindungan data pribadi (UU PDP, 3×24 jam) — bagian teknis

Dasar: UU 27/2022 Pasal 46 — pengendali wajib menyampaikan pemberitahuan tertulis kepada subjek data dan lembaga paling lambat **3×24 jam** sejak mengetahui kegagalan pelindungan data pribadi. Isi minimal: data pribadi yang terungkap, kapan dan bagaimana terungkap, upaya penanganan dan pemulihan. Keputusan hukum (apakah wajib lapor, isi surat) = DPO/legal; dokumen ini = langkah **teknis** IT/devsecops untuk memberi fakta tepat waktu.

## Jam 0–4: deteksi, triase, penahanan
1. **Buka insiden** (kanal ops + tiket rahasia). Tunjuk Incident Commander (IC) & pencatat. Catat waktu "mengetahui" = T0 (penting untuk tenggat 72 jam).
2. **Jangan hapus bukti.** Sebelum mengubah apa pun: snapshot log Loki (export kueri relevan), `docker compose logs --timestamps > /root/ir/<T0>/compose.log`, salin log Traefik, `wg show`, daftar container & image digest (`docker ps --no-trunc`), hash file (`sha256sum`). Simpan di direktori IR terenkripsi (age) di luar host yang terdampak.
3. **Tahan** sesuai vektor:
   | Vektor | Tindakan penahanan |
   |---|---|
   | Kredensial/secret bocor | Rotasi segera ([secret-rotation.md](secret-rotation.md)); kunci PII → [pii-key-rotation.md](pii-key-rotation.md); CA → [internal-pki.md](internal-pki.md) |
   | Container/aplikasi dikompromi | Isolasi: `docker network disconnect efn_egress <ctr>`; jangan `rm` — `docker pause` lalu `docker commit` untuk forensik; deploy ulang dari digest bertanda tangan |
   | Akses Odoo tidak sah | Cabut peer WireGuard, nonaktifkan user, paksa reset password + 2FA, tinjau `res.users.log` |
   | Backup/objek MinIO bocor | Cabut user MinIO terkait (`mc admin user disable`), periksa log akses MinIO, object lock mencegah penghapusan versi |
   | Perangkat POS/laptop hilang | Cabut peer WG, logout sesi Odoo, rotasi PIN POS |
4. **Pertahankan layanan inti** bila aman (POS offline mode tetap berjalan; fitur ber-OTP nonaktif saat offline).

## Jam 4–24: lingkup (data apa, berapa subjek)
Peta data pribadi platform (docs/04 §7.2) untuk menjawab "data apa yang terungkap":

| Penyimpanan | Data pribadi | Perlindungan | Pertanyaan lingkup |
|---|---|---|---|
| `platform.price_hunter_report` | `user_enc` (HP terenkripsi), `user_hash`, lokasi dibulatkan, foto (MinIO) | AES-256-GCM aplikasi, SSE-S3, TLS | Apakah `pii_encryption_key` ikut bocor? Bila tidak → ciphertext tidak terbaca |
| `platform.loyalty_ledger` | `member_ref_hash`, `member_ref_enc` | sama | idem |
| `platform.otp_audit` | `ip_enc`, subject hash | sama | idem |
| Redis | challenge OTP (HMAC), rate limit | TLS + ACL, TTL 180 s | Hampir tidak ada data pribadi persisten |
| Odoo DB | data pelanggan/karyawan semua PT | TLS, role `efn_odoo`, LUKS | Model & jumlah baris yang diakses (log PostgreSQL, Odoo) |
| ClickHouse | hash pseudonim, transaksi | TLS, disk terenkripsi, grant kolom | User mana yang query (`system.query_log`) |
| Backup WAL-G | seluruh DB | libsodium client-side + SSE-S3 + object lock | Apakah `walg_libsodium_key` bocor? |
| Log (Loki 30 hari) | HP di-mask/redaksi | TLS, `log_parameter_max_length=0` | Cari pola HP tidak ter-mask |

Kueri bantu (jalankan dari container dengan kredensial read-only / superuser lewat socket):
```sql
-- jumlah subjek unik per periode pada tabel terdampak
SELECT count(DISTINCT user_hash) FROM platform.price_hunter_report WHERE created_at BETWEEN $1 AND $2;
SELECT count(DISTINCT member_ref_hash) FROM platform.loyalty_ledger WHERE created_at BETWEEN $1 AND $2;
```
```sql
-- ClickHouse: siapa menjalankan apa
SELECT event_time, user, query_kind, substring(query, 1, 200) FROM system.query_log
WHERE event_time BETWEEN {t0:DateTime} AND {t1:DateTime} AND type = 'QueryFinish' ORDER BY event_time;
```
Untuk daftar subjek yang harus diberi tahu: BFF/admin mendekripsi `user_enc`/`member_ref_enc` untuk hash yang terdampak (akses tercatat, dua orang).

## Jam 24–60: pemulihan & bahan notifikasi
- Pulihkan dari backup bila data rusak/diubah ([backup-restore.md](backup-restore.md)); verifikasi integritas.
- Patch akar masalah, deploy lewat pipeline normal (signature + digest), tambah test regresi.
- Serahkan ke DPO/legal **fakta tertulis**: kronologi (UTC+7), kategori data, perkiraan jumlah subjek per PT (EFN/RTL/FNB/FARM — pengendali mungkin berbeda per PT), apakah data terenkripsi dan kuncinya aman, tindakan penahanan & pemulihan, kontak.
- Bila pemroses pihak ketiga terlibat (Meta/WhatsApp, payment gateway, hosting): kirim pemberitahuan ke mereka dan minta log.

## Jam 60–72: notifikasi (oleh DPO/legal) dan setelahnya
- Teknis mendukung pengiriman notifikasi ke subjek (template WA/email resmi) — **bukan** dari sistem yang masih terdampak.
- Post-mortem tanpa menyalahkan ≤ 10 hari kerja: akar masalah, deteksi (berapa lama sejak kejadian sampai T0), kontrol yang gagal/berhasil, tindakan (pemilik + tenggat). Perbarui risk register R-xxx dan ADR terkait.

## Latihan
Tabletop 2× setahun (skenario: kunci PII bocor lewat log CI; laptop ops berisi peer WG dicuri; ransomware pada host). Catat waktu tiap fase vs tenggat.
