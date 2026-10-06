# Security & Compliance Re-review (r4): Gelombang 7–10 (2026-10-06)

Reviewer: `efn-security-reviewer` (read-only terhadap kode). Re-review atas `SECURITY_REVIEW_2026-10-06-r3.md` setelah Gelombang 7–10: runtime stack dev, SILO + filter L7 `s3-gateway`, access key acak, SEC-31/39 (migrasi 04), R6 (receipt DSAR), step-up sekali pakai, wrapper deploy `efn-deploy`, smoke E2E nightly, dan modul baru `efn_audit` (pengganti OCA auditlog, ADR-031).
Cakupan yang dibaca: `services/odoo/addons/{efn_audit,efn_setup_audit,efn_privacy,efn_pos_otp_gate}`, `services/api-gateway/src/{privacy,auth/altcha*,config,payments,common/route-pii-audit.spec.ts}`, `services/superapp-web/{app/api/me,app/api/dsar,app/akun/penghapusan,lib/session.ts,lib/dsar-receipt.ts}`, `infra/postgres/init/04-platform-v3-6.sql`, `infra/s3-gateway/*`, `infra/minio/Dockerfile`, `infra/compose/docker-compose.yml`, `infra/observability/loki.yml`, `scripts/{host/efn-deploy,deploy.sh,gen-pki.sh,check-odoo-deps.sh,smoke/run-e2e-smoke.sh}`, `.github/workflows/{deploy,e2e-nightly,ci,security}.yml`, `docs/04` §8.12–8.14, ADR-030, ADR-031 (via README modul), `docs/testing/E2E_SMOKE_REPORT.md`, dan laporan agent Gelombang 7–10.
Metode: membaca kode dan konfigurasi. **Tidak ada yang dijalankan**, dan sesi reviewer ini tidak punya akses shell sehingga `git diff 24975d6..HEAD` tidak bisa dipakai. Perubahan sejak r3 diidentifikasi dari isi file + laporan agent. Perilaku runtime yang belum pasti ditandai **PLAUSIBLE**.

## Ringkasan eksekutif (untuk manajemen)

1. **Status 40 temuan lama: 31 Fixed, 7 Partial, 2 Open, 0 Regressed.** Kedua Medium r3 yang menyangkut UU PDP sudah tertutup. SEC-31: data yang sudah dihapus tidak bisa lagi ditautkan ke nomor HP. SEC-32: unduh data pribadi sudah berfungsi. Hak akses database aplikasi kini minimum (SEC-39), Loki hanya bisa diakses klien sah (SEC-35), dan hapus akun memakai verifikasi sekali pakai.
2. **Tidak ada temuan Critical/High.** Ada **8 temuan baru: 3 Medium, 2 Low, 3 Info.** Ketiga Medium:
   - (a) **Filter pengaman object storage dapat diakali dengan penulisan alamat yang di-encode** (PLAUSIBLE). Akibatnya celah SILO yang belum ditambal (pelepasan kunci retensi backup) terbuka lagi bagi container internal yang terkompromi.
   - (b) **Wrapper deploy belum membatasi isi variabel yang dikirim.** Pemegang kunci SSH deploy masih bisa menjalankan image apa pun beserta secret produksi, dan bisa mengganti CA internal.
   - (c) **Log audit Odoo bisa dihapus admin lewat pengaturan retensi** (tanpa batas minimum), dan jejak penghapusannya ikut terhapus. Ini melemahkan kontrol R-S10 (penyalahgunaan oleh orang dalam).
3. **Yang sudah baik:** `efn_audit` dirancang rapi. Nilai PII di-mask sebelum masuk DB, ada denylist password/token/TOTP, log tidak bisa diubah lewat ORM/RPC, record rule multi-company tersedia, dan overhead kecil. Receipt DSAR aman dari enumerasi. Storage terisolasi di network sendiri. Build SILO memverifikasi commit SHA.
4. **Rekomendasi:** SEC-41 dan SEC-43 diselesaikan sebelum staging publik. SEC-42 sebelum outlet pilot. SEC-33 tetap menjadi syarat sebelum vendor payment diaktifkan.

## Status temuan lama (SEC-01..SEC-40)

| ID | Sev. awal | Status r4 | Bukti utama (file:line) | Catatan sisa |
|---|---|---|---|---|
| SEC-01 | High | **Fixed** | tidak berubah sejak r3 (router `erp.` hanya `wg-https`) | — |
| SEC-02 | High | **Fixed** | + `04-platform-v3-6.sql:49-75` (DML minimum `efn_bff`) | — |
| SEC-03 | High | **Fixed** (PLAUSIBLE runtime) | tidak berubah | uji `req.ip` staging |
| SEC-04 | Medium | **Fixed** | — | — |
| SEC-05 | Medium | **Fixed** | — | — |
| SEC-06 | Medium | **Fixed** | — | biaya PoW → SEC-34 |
| SEC-07 | Medium | **Fixed** (catatan SEC-44) | `efn_pos_otp_gate/models/res_partner.py:63-107` (HP partner ber-riwayat OTP/consume hanya bisa diubah POS Manager/System; constraint jaring pengaman), test `tests/test_partner_phone_lock.py` | jalur `sudo()` dengan uid kasir lolos → SEC-44 |
| SEC-08 | Medium | **Fixed** | — | — |
| SEC-09 | Medium | **Fixed** | — | — |
| SEC-10 | Medium | **Fixed** | — | pin digest base image → SEC-47 |
| SEC-11 | Medium | **Partial** | consent/DSAR/erasure/retensi (r3) + receipt R6 | `PDP_DATA_INVENTORY.md:78-79` masih usang (Redis plaintext), `docs/runbooks/security-incident.md` belum ada, ekspor besar & koreksi/ganti nomor belum, DPO |
| SEC-12..19 | Low/Info | **Fixed** | — | — |
| SEC-20 | Medium | **Fixed** | — | — |
| SEC-21 | Medium | **Fixed** | — | — |
| SEC-22 | Low | **Fixed** | — | — |
| SEC-23 | Low | **Fixed** | — | trust anchor dari env → SEC-42 |
| SEC-24 | Low | **Fixed** | — | dokumen inventaris usang (SEC-11) |
| SEC-25 | Low | **Fixed** | `deploy.sh:224` (`RABBITMQ_STRICT_PERMISSIONS` default `true`), `deploy.yml:185` | — |
| SEC-26..28 | Low | **Fixed** | — | SEC-28 PLAUSIBLE runtime |
| SEC-29 | Info | **Partial** | `secrets.loader.ts:50-52` (kunci PII, material TLS, access key MinIO dibaca langsung) | `JWT_SECRET`, `OTP_PEPPER`, `ALTCHA_HMAC_KEY`, dll. masih disalin ke `process.env` (`:65-72`, `:78-88`) |
| SEC-30 | Info | **Partial** | `security.yml:37` semgrep dipin digest | fallback passlib plaintext `render_odoo_conf.py:115-119` |
| SEC-31 | Medium | **Fixed** | `erasure.service.ts:142-189` (`randomTombstone`, forfeit `source_ref` NULL + idempotency UUID acak, `dsar_request` lain → `member_hash` NULL), `:196-209` (`member_hash` NULL saat `completed`), `04-platform-v3-6.sql:77-118` (CHECK + backfill + ganti tombstone lama), Odoo `res_partner.py:49-59` (hanya `efn_erased`), `efn_privacy_erasure.py:51,203-213,254-297` (nama tanpa id, penanda sementara), migrasi `19.0.1.1.0/pre-migrate.py` | residu terdokumentasi: korelasi waktu `erased_at` ↔ `scheduled_at`. Log aplikasi Odoo `efn_privacy_erasure.py:180` memuat `request_id` (tanpa hash, Loki 30 hari), dapat diterima |
| SEC-32 | Medium | **Fixed** | `superapp-web/app/api/me/data/route.ts:40-48` (cookie `efn_stepup` → `x-step-up-token`) | — |
| SEC-33 | Medium | **Open** (tidak dapat dieksploitasi selama adapter eksperimental) | `payments.service.ts:220-235` (claim sebelum intent ditemukan, tanpa `getStatus` sebelum `paid`), `:274` (cek nominal dilewati bila `amount` null), `:306-313` (`amount` milik intent) | blocker sebelum vendor aktif |
| SEC-34 | Low | **Partial** | `app-config.ts:604-606` (default 500 000) | belum ada pengikatan ke IP dan kenaikan adaptif; 500k ≈ 25–50 ms native |
| SEC-35 | Low | **Fixed** | `loki.yml:1-13` (loopback 3101), `docker-compose.yml:1440-1457` (`loki-tls` `--allow-cn=promtail/grafana/prometheus`), `:327,:537,:1673,:1756` (exporter lain `--allow-cn=prometheus`), `docs/04` §8.14 | — |
| SEC-36 | Low | **Partial** | `scripts/host/efn-deploy` (forced command, cosign `verify-blob`, salinan root) | env tidak diikat tanda tangan → SEC-42; kunci intermediate tetap di GitHub Environment |
| SEC-37 | Low (PLAUSIBLE) | **Partial** | `docs/runbooks/acme-dns-token.md`, `scripts/secrets.list:61-62` | token tetap di Traefik (`docker-compose.yml:289-291`); cakupan token tidak dapat diverifikasi dari repo; pemisahan lego belum dibangun |
| SEC-38 | Low (PLAUSIBLE) | **Open** | tidak ada perubahan | keputusan bisnis/DPO |
| SEC-39 | Info | **Fixed** | `04-platform-v3-6.sql:28-75` (`efn_platform_owner` NOLOGIN, `REASSIGN OWNED`, `efn_bff` DML per tabel tanpa DDL/TRIGGER), `03-platform-v3.sql:10` | `UPDATE(ip_enc)` consent tetap (`04:71`), superuser/DBA tetap bisa (diakui) |
| SEC-40 | Info | **Partial** | Fixed: step-up sekali pakai untuk DELETE (`dsar.service.ts:88-94`, `privacy.controller.ts:55-58`, Redis `SET NX EX`), race foto (`erasure.service.ts:128-137`, hapus foto setelah commit), `disabled` diterima (`deploy.sh:56`), staging memakai `isProductionDeployment` (`payments.service.ts:246`) | inventaris PDP usang, runbook insiden, penjaga `gen-pki.sh root` |

**Rekap:** Fixed 31 · Partial 7 (SEC-11, 29, 30, 34, 36, 37, 40) · Open 2 (SEC-33, 38) · Regressed 0.

## Temuan baru

| ID | Severity | Judul | Lokasi utama | Risiko |
|---|---|---|---|---|
| SEC-41 | Medium (PLAUSIBLE) | Aturan query `s3-gateway` dapat diakali dengan percent-encoding/`;` pada nama parameter. SN-2026-015 (`PutObjectRetention`), `?select`, dan presigned write terbuka lagi | `infra/s3-gateway/haproxy.cfg:63-66,77-81` | R-T08, R-S02, R-S06 |
| SEC-42 | Medium | `efn-deploy` hanya memverifikasi bundel skrip. `IMAGE_*` (registry bebas), trust anchor PKI (`EFN_PKI_ROOT_CRT`, `EFN_PKI_EXTRA_TRUST`), dan semua secret dari stdin tidak diverifikasi. Rilis lama bertanda tangan bisa diputar ulang | `scripts/host/efn-deploy:101-130`, `scripts/deploy.sh:47-50,88-90,132-143`, `scripts/gen-pki.sh:308` | R-S07, R-S08, R-S01 |
| SEC-43 | Medium | Log audit `efn_audit` dapat dihapus massal oleh admin lewat retensi tanpa batas minimum, dan jejak `purge` ikut terhapus. Jalur server action/uninstall (PLAUSIBLE) juga tersedia. Tidak ada salinan tamper-evident di luar Odoo | `efn_audit/models/efn_audit_log.py:146-182`, `efn_audit_rule.py:115-120,159-162`, `efn_setup_audit/data/ir_config_parameter.xml` | R-S10, R-B06 |
| SEC-44 | Low (PLAUSIBLE) | Kunci HP member (SEC-07) dilewati untuk `write` via `sudo()` dengan uid kasir: lapis 1 dilewati tetapi id tetap ditandai "sudah dicek", sehingga lapis 2 ikut dilewati | `efn_pos_otp_gate/models/res_partner.py:68-79,101-103` | R-B06, SEC-07 |
| SEC-45 | Low | PII di log audit: `vat` (NPWP = NIK untuk orang pribadi) dan nama partner tidak di-mask. Masking HP/email bergantung pada data aturan yang bisa diedit admin. Log partner tanpa company terlihat oleh auditor semua PT | `efn_setup_audit/data/efn_audit_rule_data.xml:106-117`, `efn_audit/models/base.py:163-166,189`, `efn_audit_security.xml:9,15` | R-L01 |
| SEC-46 | Info | Batas cakupan hook audit (by design): `_write`/SQL langsung, upgrade modul (`registry.ready=False`), `company_id` log = company sesudah write | `efn_audit/models/base.py:73-79,190` | R-S10 |
| SEC-47 | Info | Supply chain: base image tanpa digest (`odoo:19.0`, `alpine:3.24`, `node:22.12-alpine`, `python:3.12-slim`, `postgres:16.6-bookworm`). Gerbang `check-odoo-deps.sh` hanya memeriksa `depends` | `services/odoo/Dockerfile:4`, `infra/minio/Dockerfile:27`, `scripts/check-odoo-deps.sh:24-30` | R-S08 |
| SEC-48 | Info | Catatan kecil BFF/frontend/CI (audit PII-URL berbasis daftar statis, kunci receipt, runner E2E self-hosted, identitas tag) | lihat detail | — |

---

## Detail temuan baru

### SEC-41: Medium (PLAUSIBLE): Filter query `s3-gateway` dapat diakali lewat encoding nama parameter
**Lokasi:** `infra/s3-gateway/haproxy.cfg:63` (`q_cfg query -m reg -i (^|&)(retention|...)(=|&|$)`), `:64` (`q_select`), `:65` (`q_sts`), `:66` (`q_presigned`), dan aturan deny `:77-81`. Uji negatif `scripts/smoke/run-e2e-smoke.sh:293-306` hanya memakai bentuk literal.
**Kondisi:** sample `query` HAProxy adalah query string **mentah** (tidak di-decode), dan regex hanya mengenal pemisah `&`. Di sisi storage (kode MinIO/SILO, router `minio/mux` turunan gorilla/mux), pencocokan `Queries("retention", "")` melakukan `url.QueryUnescape` atas nama kunci, dan versi gorilla/mux yang umum juga memisah pada `;`. Deteksi presigned (`X-Amz-Credential`) membaca `r.Form`, yang juga sudah di-decode. Akibatnya:
- `PUT /pg-backup/<obj>?rete%6Etion` (atau `?x=1;retention`) lolos filter, sedangkan storage merutekannya ke `PutObjectRetention`.
- Pola yang sama berlaku untuk `?%73elect&select-type=2` (S3 Select), `?legal-ho%6Cd`, `?x-amz-cr%65dential=…` (presigned write) dan `?%61ction=` (STS).
**Skenario:** ocr-worker (pemroses gambar tak tepercaya) atau BFF terkompromi dan memakai kredensial S3 sah miliknya. Ia mengirim `PUT /pg-backup/basebackups_005/…?rete%6Etion` dengan body `<Retention><Mode>GOVERNANCE</Mode><RetainUntilDate>(masa lalu)</RetainUntilDate></Retention>`. Menurut advisory SN-2026-015 (belum difix di SILO, ADR-030 baris 57), principal terautentikasi mana pun bisa melakukannya tanpa izin IAM. Dampaknya: proteksi anti-ransomware `pg-backup` hilang, atau objek dikunci COMPLIANCE sampai tahun jauh sehingga storage penuh dan foto PII tidak bisa dihapus (UU PDP). Inilah kelas serangan yang menjadi alasan s3-gateway dipasang.
**Status verifikasi:** perilaku decode di router SILO **belum diuji** (PLAUSIBLE kuat, berdasarkan perilaku gorilla/mux `findFirstQueryKey` dan `r.Form`). Uji cepat: kirim varian encoded lewat `s3gw()` di smoke `g`. Hasil yang diharapkan saat ini `AccessDenied`/`200` dari storage (lolos filter), bukan `403` dari HAProxy.
**Rekomendasi:**
1. Tolak semua karakter `%` dan `;` di query string untuk method tulis (aplikasi tidak memerlukannya pada nama parameter). Contoh: `acl q_enc query -m reg (%|;)` lalu `http-request deny if q_enc m_write`. Untuk GET/HEAD, tolak `%` hanya di **nama** kunci: `query -m reg (^|[&;])[^=&;]*%`.
2. Atau cocokkan ACL pada query yang sudah di-decode (`query,url_dec`) **dan** dengan pemisah `[&;]`. Opsi 1 + 2 lebih aman.
3. Tambahkan uji negatif varian encoded, `;`, dan huruf besar ke smoke `g` dan CI `haproxy -c`.
4. Sampai SN-2026-015 difix, pertimbangkan Q-OS3 (COMPLIANCE) dan bucket backup off-site (Q-OS5).

### SEC-42: Medium: Wrapper `efn-deploy` tidak mengikat isi env ke rilis bertanda tangan
**Lokasi:** `scripts/host/efn-deploy:17-24` (klaim: "Pemegang kunci SSH deploy saja tidak bisa menjalankan kode/skrip apa pun selain rilis yang ditandatangani"), `:101-125` (`env.b64` hanya divalidasi nama + base64), `scripts/deploy.sh:47-50` (`IMAGE_*` cukup cocok `DIGEST_RE` dengan registry **apa pun**; `cosign verify` hanya di runner GitHub, `deploy.yml:100-122`), `:88-90,132-143` (`EFN_PKI_ROOT_CRT` dari env menjadi trust anchor), `gen-pki.sh:308` (`EFN_PKI_EXTRA_TRUST` ditambahkan ke bundle `efn_ca_crt`), `:98-108` (semua secret ditimpa dari env).
**Kondisi & skenario:** penyerang yang memegang `DEPLOY_SSH_KEY` (plus jalur jaringan ke host) menyusun arsip berisi `release.tar.gz` + bundle **asli** dari deploy sebelumnya (keduanya tidak rahasia, misalnya dari log/artefak runner atau `/opt/efn/releases`) ditambah `env.b64` buatannya sendiri:
- `IMAGE_ODOO=evil.example/odoo@sha256:<digest>`: image penyerang berjalan sebagai service `odoo` dengan secret DB, `member_hash_pepper`, dan akses network `data`. Pola yang sama berlaku untuk `IMAGE_POSTGRES` (seluruh data) dan `IMAGE_API_GATEWAY` (network `egress` untuk eksfiltrasi).
- `EFN_PKI_ROOT_CRT` + `EFN_PKI_INTERMEDIATE_*` milik penyerang (atau root sah + `EFN_PKI_EXTRA_TRUST` CA penyerang): semua service mempercayai CA penyerang, sehingga mTLS internal bisa di-MITM.
- Rilis lama yang bertanda tangan sah dapat diputar ulang (downgrade ke versi rentan), karena tidak ada pemeriksaan monoton.
Artinya kunci SSH deploy ≈ kendali penuh atas workload dan data, walau bukan shell root langsung. Kontrol nyata yang tersisa: approval GitHub Environment dan kerahasiaan kunci.
**Rekomendasi:**
1. Workflow menulis `manifest.json` (7 digest image + versi/tag + `git sha`) **ke dalam** `release.tar.gz` yang ditandatangani. Wrapper atau `deploy.sh` mengambil `IMAGE_*` dari manifest, bukan dari env, lalu menjalankan `cosign verify` per image di host dengan identitas `build-push.yml@<ref>` dan prefix registry `ghcr.io/<org>/` yang dikunci di `/etc/efn/deploy.conf`.
2. Pin fingerprint SHA-256 root CA (dan daftar fingerprint CA lama yang diizinkan untuk `EXTRA_TRUST`) di `/etc/efn/deploy.conf`. Tolak bila berbeda. Verifikasi bahwa intermediate ditandatangani root yang dipin.
3. Tolak rilis dengan versi lebih lama dari `/var/lib/efn/last-release` kecuali ada flag lokal Superadmin.
4. Secret tetap dari env (tidak bisa ditandatangani). Catat ini sebagai risiko residu di `key-custody.md` dan tambahkan alert auditd/log `efn-deploy` ke Loki.

### SEC-43: Medium: Log audit dapat dimusnahkan lewat retensi (immutability hanya pada jalur ORM)
**Lokasi:** `efn_audit/models/efn_audit_log.py:146-153` (`efn.audit.retention_days` diterima berapa pun ≥ 0, misalnya `1`), `:155-182` (GC menghapus semua log, **termasuk** log `operation='purge'` dan log perubahan parameter/aturan, karena `rule_id` False jatuh ke retensi global), `efn_audit_rule.py:115-120,159-162` (`retention_days` per aturan minimal 0/1 hari), `efn_setup_audit/data/ir_config_parameter.xml:5-9` (`noupdate`, admin bebas mengubah).
**Kondisi:** `write`/`unlink` log memang ditolak untuk semua user termasuk `sudo()` (`:82-90`, `:201-209`), dan penanda `cr.cache` tidak bisa diisi dari RPC: tidak ada method publik maupun button yang memanggil `_efn_audit_purge_*`, dan method berawalan `_` tidak bisa dipanggil lewat RPC. Ini benar. Namun admin (`base.group_system`, otomatis Manager audit) bisa:
1. **(terverifikasi dari kode)** set `efn.audit.retention_days = 1`. Autovacuum harian menghapus semua log lebih tua dari 1 hari. Log perubahan parameter itu sendiri dan log `purge` ikut terhapus pada jalan berikutnya, sehingga dalam ±2 hari tidak ada jejak bahwa log pernah dihapus. Varian per-aturan: `retention_days = 1` pada aturan harga/promo.
2. **(PLAUSIBLE)** membuat server action Python. `safe_eval` Odoo hanya melarang atribut dunder, sehingga kode seperti `env['efn.audit.log'].search([])._efn_audit_purge_logs('x')` atau `env.cr.execute(...)` kemungkinan bisa dijalankan. Model `ir.actions.server` tidak diaudit.
3. Uninstall `efn_audit` (tabel ikut di-drop).
**Skenario (R-S10):** admin/insider mengubah harga atau promo untuk fraud, lalu menyetel retensi 1 hari. Dua hari kemudian auditor tidak menemukan apa pun, termasuk bukti bahwa log telah dipurge.
**Rekomendasi:**
1. Batas bawah hard-coded di kode (misalnya `MIN_RETENTION_DAYS = 365`, atau nilai DPO/auditor) di `_efn_audit_global_retention` dan `_check_retention`. Nilai di bawahnya ditolak atau diperlakukan sebagai minimum.
2. Kecualikan dari GC: log `operation='purge'`, log `model_name in ('efn.audit.rule','ir.config_parameter')`. Alternatifnya, beri retensi terpisah yang lebih panjang.
3. Tambahkan aturan audit untuk `ir.actions.server` (field `code`, `state`), `base.automation`, dan `ir.module.module` (`state`). Kirim alert (Prometheus/Loki) saat `efn.audit.retention_days` atau aturan audit berubah.
4. Tamper-evident (sudah tercatat sebagai fase 1): salin log ke penyimpanan append-only di luar kendali admin Odoo (ETL → ClickHouse dengan user tanpa DELETE, atau Loki) dan/atau hash berantai per baris.
5. Batasi `base.group_system` ke Superadmin (sejalan dengan keputusan klien 6 Okt).
6. Perbaiki klaim README "immutable termasuk admin" menjadi "immutable terhadap ORM/RPC. Admin sistem tetap dapat memengaruhi lewat retensi/kode, dimitigasi oleh butir 1–4".

### SEC-44: Low (PLAUSIBLE): Kunci HP member dilewati untuk `write` via `sudo()` dengan uid kasir
**Lokasi:** `efn_pos_otp_gate/models/res_partner.py:68` (`if not self.env.su:` → pemeriksaan lapis 1 dilewati untuk env sudo), `:74-76` (id partner **tetap** dimasukkan ke `CHECKED_KEY`), `:101-103` (lapis 2 melewati id yang ada di `CHECKED_KEY`). Docstring `:29-33` menyatakan "`sudo()` dengan uid kasir ikut ditolak = fail-closed". Test `test_safety_net_constraint_uses_real_user` (`tests/test_partner_phone_lock.py:156-161`) memanggil constraint secara langsung, bukan lewat `write`, sehingga jalur ini tidak teruji.
**Skenario:** kode server apa pun yang menulis `phone` partner dengan `sudo()` atas input pengguna kasir. Contohnya modul standar/kustom yang meng-upsert pelanggan saat sinkron POS atau self-order. Lewat jalur itu, HP member ber-riwayat OTP dapat diganti ke nomor kasir, sehingga OTP promo member (R-B06) diterima kasir. Saat ini tidak ditemukan jalur seperti itu di addons `efn_*`. Dampak bergantung pada modul standar yang terpasang, sehingga statusnya PLAUSIBLE.
**Rekomendasi:** hapus syarat `not self.env.su` dan selalu putuskan berdasarkan `self.env.user` (uid asli). OdooBot/superuser tetap lolos lewat `_is_superuser()`, jadi cron erasure tidak terdampak. Alternatif: tambahkan id ke `CHECKED_KEY` hanya bila lapis 1 benar-benar memeriksa. Tambahkan test `partner.with_user(cashier).sudo().write({'phone': ...})` → `AccessError`.

### SEC-45: Low: Minimisasi PII di log audit belum lengkap
**Lokasi:** `efn_setup_audit/data/efn_audit_rule_data.xml:106-117` (res.partner melacak `name`, `vat` tanpa masking; hanya `phone`/`email` di `masked_field_ids`), `efn_audit/models/base.py:189` (`res_name = display_name`, yaitu nama lengkap), `:163-166` (masking hanya bila field ada di `rule.masked`, data `noupdate` yang dapat diedit admin), `efn_audit_security.xml:9,15` (log dengan `company_id` False terlihat oleh semua auditor). Partner pelanggan umumnya tanpa company, sehingga terlihat lintas PT.
**Kondisi & dampak:** NPWP orang pribadi kini = NIK (data pribadi yang dapat mengidentifikasi langsung) dan tersimpan utuh di log selama 730 hari, terbaca oleh auditor semua PT. Bila admin menghapus `phone` dari daftar masking (atau membuat aturan baru untuk model lain yang memuat HP), nomor lengkap tersimpan tanpa peringatan. Erasure memang membersihkan log partner (`efn_privacy_erasure.py:250`), tetapi selama subjek masih aktif data itu ada di log.
**Rekomendasi:** masking wajib hard-coded berdasarkan pola nama field (`phone|mobile|email|vat|nik|npwp|ktp`), seperti denylist, yang berlaku walau tidak dipilih di aturan. `vat` → `****(n)` atau 4 digit terakhir. Pertimbangkan `res_name` = `res.partner,<id>` untuk model partner. Retensi log partner lebih pendek (misalnya 1 tahun, keputusan DPO). Catat log audit di `PDP_DATA_INVENTORY.md` (aktivitas pemrosesan baru, retensi, siapa yang dapat membaca).

### SEC-46: Info: Batas cakupan hook audit
- Penulisan lewat `_write`, SQL langsung, atau saat `registry.ready=False` (install/upgrade, termasuk data XML yang mengubah harga) tidak dicatat (`base.py:73-79`, README "Batasan"). Ini by design. Mitigasi: upgrade modul hanya lewat pipeline deploy bertanda tangan, dan perubahan data XML ditinjau di PR.
- `company_id` log = company record **sesudah** write (`base.py:190`). Memindahkan produk dari PT A ke PT B membuat jejak perubahan itu tidak terlihat oleh auditor PT A. Pertimbangkan mencatat company lama dan baru, atau menduplikasi log ke kedua company.
- `with_user(SUPERUSER_ID)` tercatat sebagai OdooBot (uid asli hilang). Bila jalur itu dipakai kode kustom, sertakan uid pemicu di context terpercaya (bukan dari RPC).
- Performa sudah diukur di test (0 query tambahan untuk model tanpa aturan, +2 query untuk batch write). Belum ada PoC beban staging. `_efn_audit_purge_records` memakai `=like` pada `old_ref`/`new_ref` tanpa index (`efn_audit_log.py:118-120`), sehingga setiap erasure melakukan scan penuh tabel baris log. Tambahkan index trigram/btree prefix bila volume besar.

### SEC-47: Info: Supply chain build
- Base image tanpa digest: `services/odoo/Dockerfile:4` (`odoo:19.0`, tag bergerak; image resmi Odoo diperbarui harian), `infra/minio/Dockerfile:27` (`alpine:3.24`, padahal stage build sudah dipin digest), `api-gateway`/`superapp-web` (`node:22.12-alpine`), `ai-ocr-worker`/`etl` (`python:3.12-slim`), `infra/backup/postgres` (`postgres:16.6-bookworm`). Image akhir sudah di-sign dan di-scan (R-S08), tetapi build tidak reprodusibel. Pin `@sha256:` dan biarkan Dependabot `docker` yang memperbarui.
- SILO: verifikasi `git rev-parse HEAD` = commit yang dipin sudah benar (`infra/minio/Dockerfile:19-25`), dan modul Go diverifikasi `go.sum` + sumdb default. Risiko residu: satu maintainer utama (ADR-030, R-S13 usulan).
- `scripts/check-odoo-deps.sh:24-30` hanya memeriksa `depends` manifest `efn_*`. Yang tidak diperiksa: `external_dependencies`/`requirements.txt` (pip), modul non-`efn_*` yang diletakkan di `addons/` (bisa di-install manual karena ada di `addons_path`), dan kode pihak ketiga yang disalin ke dalam folder bernama `efn_*`. Daftar CE berasal dari isi image base (`ci.yml:231`), yang juga tidak dipin. Tambahkan: tolak direktori di `services/odoo/addons` yang tidak berawalan `efn_`, dan periksa `requirements.txt` terhadap allowlist. Gerbang ini kontrol kebijakan, bukan kontrol keamanan kuat.

### SEC-48: Info: Catatan kecil
- **Audit PII-di-URL** (`common/route-pii-audit.spec.ts:20-32`): daftar controller statis. Controller baru tidak otomatis ikut, dan pembacaan `req.query` lewat `@Req()` tidak terdeteksi. Saat ini 11 controller tercakup (sesuai `grep @Controller`) dan tidak ada `req.query`. Sarankan iterasi otomatis atas `DiscoveryService`/semua modul.
- **Receipt DSAR** (`dsar-receipt.service.ts`, `app-config.ts:644-651`): HMAC-SHA256 dengan kunci HKDF(`jwt_secret`, info `efn:dsar_receipt:v1`). Pemisahan domain kriptografis **memadai**: output HKDF independen dari pemakaian `jwt_secret` mentah untuk HS256, dan format pesan berbeda. Konsekuensinya operasional: rotasi `jwt_secret` membatalkan semua receipt 30 hari, dan kompromi `jwt_secret` juga mengungkap kunci receipt (dampak kecil: status tanpa PII). Verifikasi HMAC waktu-konstan dilakukan **sebelum** query DB, semua kegagalan dijawab 404 generik, ada rate limit per IP (HMAC), dan id wajib UUID. Enumerasi tidak mungkin. Opsional: secret `dsar_receipt_key` terpisah.
- **Frontend R6:** cookie `efn_dsar_receipt` HttpOnly, Secure (production), SameSite=Strict, Path `/api/dsar`, umur ≤ sisa `exp` (`session.ts:81-100`, `dsar-receipt.ts:27-34`). Route `/api/dsar/requests/[id]` memakai Fetch Metadata, validasi UUID, field allowlist, dan `no-store`. Halaman `/akun/penghapusan/[id]` dirender untuk UUID apa pun tanpa memeriksa keberadaan request (tidak ada oracle) dan memakai `robots: noindex`. Satu cookie per peramban: penghapusan kedua di perangkat yang sama menimpa receipt pertama (keterbatasan UX).
- **Step-up:** DELETE sekali pakai (benar). Ekspor tetap boleh dipakai berulang selama 10 menit, dibatasi rate per jam. Ini dapat diterima.
- **ALTCHA:** implementasi tetap benar. Lihat SEC-34 untuk biaya.
- **`e2e-nightly.yml`:** `permissions: contents: read`, tanpa secret GitHub (secret dummy `gen-secrets.sh`), input `scenarios` divalidasi regex, aksi dipin SHA, `persist-credentials: false`. Benar. Bila `vars.E2E_RUNNER` = self-hosted, pakai runner **ephemeral** khusus (bukan host staging/produksi), karena job menjalankan `docker` + `sudo` dan sisa state antar-run dapat dipersistensi.
- **`deploy.yml`:** identitas tanda tangan rilis mengikuti ref pemicu. Siapa pun yang bisa mendorong tag `v*` mendapat tanda tangan yang valid untuk produksi. Gerbang nyatanya adalah approval Environment `production`. Pastikan ruleset proteksi tag `v*` aktif (key-custody §30).
- **Root MinIO** `MINIO_ROOT_USER` default `efnadmin` (`docker-compose.yml:573`) tidak acak. Risikonya rendah karena root hanya dapat dijangkau dari network `objstore` (s3-gateway, minio-init), tetapi sebaiknya ikut diacak seperti access key lain (§8.12).

---

## Kontrol yang sudah benar (untuk manajemen)
- **`efn_audit` (desain inti):** hook di `_inherit="base"` tanpa monkeypatch registry, sehingga melihat `vals` final setelah override lain (termasuk kunci HP SEC-07). Kegagalan menulis log membatalkan transaksi (fail-closed). Nilai PII di-mask **sebelum** masuk DB. Denylist global `password/secret/token/api_key/totp/signature` + binary + `efn_member_hash` tidak bisa dibatalkan oleh aturan. Aturan audit sendiri selalu diaudit. `write`/`unlink` log ditolak untuk semua user termasuk `sudo()`, dan penanda purge ada di `cr.cache` (bukan context, tidak dapat diisi RPC). Tidak ada grup dengan hak create/write/unlink log. Record rule multi-company untuk log dan baris. `user_id` = uid asli + flag `is_sudo`. `user_id ondelete=restrict`. Tidak ada SQL ke tabel yang diaudit.
- **Erasure Odoo:** log partner dihapus dan ref di log model lain dikosongkan saat erasure. Log `purge` dan `privacy.log` tanpa id/hash. `efn_member_hash` dipastikan kosong (gagal → rollback). Penanda idempotensi sementara tanpa id partner.
- **SEC-31 platform:** tombstone 32 byte acak tanpa turunan request, forfeit tanpa `request_id`, `member_hash` di `dsar_request` hanya hidup selama `processing` (CHECK constraint di DB), dan migrasi tombstone lama transaksional dan idempoten.
- **SEC-39:** `efn_platform_owner` NOLOGIN sebagai pemilik, `efn_bff` hanya DML per tabel/kolom, tanpa DELETE pada ledger/consent/dsar/payment.
- **Object storage:** storage hanya di network `objstore` (internal). Klien lewat s3-gateway TLS dengan re-encrypt `verify required` + `verifyhost`. HAProxy non-root, read-only, tanpa capability, PEM hanya di tmpfs. Log akses tanpa query string (`%HPO`). Access key per klien acak dari secret. Build SILO dari source dengan verifikasi commit SHA (bukan image pihak ketiga).
- **Deploy:** forced command + sudoers tanpa argumen, verifikasi cosign keyless dengan identitas dari konfigurasi milik root, batas ukuran, tolak symlink/device dan path di luar allowlist, eksekusi dari salinan milik root. Menutup sebagian besar SEC-36; sisanya SEC-42.
- **Loki/exporter:** allowlist CN pada semua endpoint observability. E12 ditutup.
- **Frontend/BFF DSAR:** ekspor kini meneruskan step-up. Receipt opak tanpa PII dan tidak pernah dikirim ke JavaScript. Status publik tanpa oracle.

## UU PDP: status

| Kewajiban | Status r4 | Bukti / sisa |
|---|---|---|
| Consent server-side | **Implementasi** | append-only kini kuat terhadap BFF (SEC-39) |
| Hak akses (ekspor) | **Implementasi** | SEC-32 fixed; ekspor besar (202) belum |
| Hak hapus lintas store | **Implementasi, anonimisasi tuntas** | SEC-31 fixed; status pasca-hapus via receipt (R6) |
| Koreksi / ganti nomor | **Belum** | — |
| Retensi | **Implementasi** (menunggu DPO) | + log audit 730 hari (SEC-43/45) |
| Log audit berisi PII | **Baru, perlu dicatat di RoPA** | SEC-45 |
| Notifikasi insiden 3×24 jam | **Draf kebijakan**; runbook teknis belum | `security-incident.md` belum ada |
| Inventaris data / RoPA | **Draf**, usang | `PDP_DATA_INVENTORY.md:78-79`; tambahkan `efn.audit.log` |
| DPO, kebijakan privasi final, transfer lintas negara | **Belum diputuskan** | — |

## Prioritas perbaikan
1. **Sebelum staging publik:** SEC-41 (normalisasi/penolakan query encoded di s3-gateway + uji negatif) dan SEC-43 (retensi minimum hard-coded, purge log tidak ikut GC, audit `ir.actions.server`).
2. **Sebelum outlet pilot / hardening ops:** SEC-42 (digest image + root CA dari manifest bertanda tangan/pin di host, anti-downgrade), sisa SEC-37 (token DNS terbatas, diverifikasi Superadmin), SEC-44 (satu baris + test).
3. **Sebelum vendor payment diaktifkan:** SEC-33 seluruhnya (tetap).
4. **Backlog terjadwal:** SEC-45 (masking wajib berbasis pola + RoPA), SEC-11 (inventaris, runbook insiden), SEC-34, SEC-29, sisa SEC-30, SEC-38 (DPO), SEC-46..48. Verifikasi runtime untuk item PLAUSIBLE: SEC-41 (uji varian encoded terhadap SILO), SEC-43 butir 2 (server action), SEC-03, SEC-28.
