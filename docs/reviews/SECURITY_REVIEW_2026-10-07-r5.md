# Security & Compliance Re-review (r5): Gelombang 11–12 (2026-10-07)

Reviewer: `efn-security-reviewer` (read-only terhadap kode). Re-review atas `SECURITY_REVIEW_2026-10-06-r4.md` setelah Gelombang 11 (SEC-41..45, sink audit Loki, efn-deploy bermanifest), Gelombang 12 (ACME terpisah, E12, ALTCHA v2, SEC-29, salinan audit ClickHouse, mart GL, VLM, tour POS, `origin_user_id`) dan 12b (redaksi traceback JSON-RPC).
Cakupan yang dibaca: `services/odoo/addons/{efn_core/models/ir_http.py, efn_audit/*, efn_setup_audit/data, efn_pos_otp_gate/models/res_partner.py + manifest}`, `services/odoo/{Dockerfile, odoo.conf.tpl, render_odoo_conf.py}`, `services/api-gateway/src/{auth/altcha.service.ts, auth/auth.service.ts, config/*, payments/payments.service.ts}`, `services/etl/efn_etl/{audit.py, audit_job.py, adapters.py, gl.py}`, `services/ai-ocr-worker/efn_ocr/imaging.py`, `infra/{acme/*, s3-gateway/haproxy.cfg, compose/docker-compose{,.prod}.yml, observability/{loki.yml, promtail.yml, promtail-journal.yml, loki-rules/*, alert-rules.yml}, clickhouse/access/*}`, `scripts/{host/efn-deploy, deploy.sh, check-odoo-deps.sh, gen-pki.sh}`, `.github/workflows/build-push.yml`, `docs/04` §8.12–8.18, `docs/testing/E2E_SMOKE_REPORT.md` (G11–G12), laporan agent G11/G12/G12b.
Metode: membaca kode dan konfigurasi. **Tidak ada yang dijalankan.** Perilaku runtime yang belum pasti ditandai **PLAUSIBLE**.

## Ringkasan eksekutif (untuk manajemen)

1. **Status 48 temuan lama: 42 Fixed, 4 Partial, 2 Open, 0 Regressed.** Seluruh Medium r4 tertutup di kode: celah filter object storage (SEC-41), wrapper deploy kini memverifikasi image dan CA dari manifest bertanda tangan (SEC-42), dan log audit Odoo tidak lagi bisa dimusnahkan lewat retensi (SEC-43, kini ada salinan di Loki dan ClickHouse).
2. **Tidak ada temuan Critical/High.** Ada **9 temuan baru: 2 Medium, 4 Low, 3 Info.** Kedua Medium:
   - (a) **Perubahan hak akses Odoo tidak tercatat di log audit.** Contohnya pemberian grup admin/auditor lintas PT dan perubahan record rule multi-company. Admin dapat membuka data PT A bagi user PT B, atau memberi dirinya akses data pribadi lintas PT, tanpa jejak maupun alert (SEC-49).
   - (b) **Pemegang kunci SSH deploy masih bisa membuka Odoo/BI/ops ke internet.** Caranya lewat variabel jaringan yang tidak ditandatangani (IP bind WireGuard, CIDR `0.0.0.0/0`). Beberapa variabel juga tidak divalidasi sehingga bisa menyisipkan baris ke `.env` (SEC-50).
3. **Yang sudah baik:** redaksi traceback Odoo memakai titik ekstensi resmi dan fail-closed. `origin_user_id` hanya dari sumber tepercaya. Sink audit tanpa PII. Rantai hash ClickHouse terbukti mendeteksi perubahan (uji QA). User ETL tidak bisa DELETE. Secret BFF tidak lagi masuk `process.env`. ALTCHA kini terikat IP. Image produksi Odoo tanpa Chrome. Semua image dipin digest.
4. **Rekomendasi:** SEC-50 diselesaikan sebelum host staging/produksi dipasang. SEC-49 sebelum outlet pilot. SEC-33 tetap syarat sebelum vendor payment diaktifkan. SEC-43 butir 5 (batasi `base.group_system` ke Superadmin) perlu keputusan klien sekarang, karena hampir semua sisa risiko audit bergantung padanya.

## Status temuan lama (SEC-01..SEC-48)

| ID | Sev. awal | Status r5 | Bukti utama (file:line) | Catatan sisa |
|---|---|---|---|---|
| SEC-01..10 | High/Medium | **Fixed** | tidak berubah sejak r4; SEC-07 lihat SEC-44 | SEC-03 PLAUSIBLE runtime |
| SEC-11 | Medium | **Partial** | `docs/compliance/PDP_DATA_INVENTORY.md` v3 (audit log, sink, salinan CH), `docs/runbooks/security-incident.md` (baru) | DPO, koreksi/ganti nomor, ekspor besar, Q-P9..P11 |
| SEC-12..21 | Low/Medium | **Fixed** | — | — |
| SEC-22 | Low | **Fixed** | `efn_core/models/ir_http.py:61-82` (post-process `_handle_error`, hanya `jsonrpc`/`json2`), `:102-115` (admin dari `session.uid`, fail-closed), `:79-81` (gagal parse → generik) | jalur tanpa DB (`_serve_nodb`) di luar cakupan; dimitigasi `dbfilter = ^efn_.*$` + satu DB (`odoo.conf.tpl:13`). Lihat SEC-55 |
| SEC-23..28 | Low | **Fixed** | — | SEC-28 PLAUSIBLE runtime |
| SEC-29 | Info | **Fixed** | `api-gateway/src/config/app-config.ts:230-261` (`secretValue`: `_FILE` → `/run/secrets` → literal hanya `APP_ENV=dev`, buffer di-nol-kan), `no-secret-env-write.spec.ts:55-72`; `grep process.env.*SECRET|KEY|TOKEN|PASSWORD` di `services/` = 0 | gerbang statis tidak mendeteksi alias (`const e = process.env; e.X = …`). Dapat diterima |
| SEC-30 | Info | **Fixed** | `render_odoo_conf.py:114-120` (passlib hilang → FATAL), `services/odoo/Dockerfile:22` (build memverifikasi `passlib.context`) | — |
| SEC-31, 32 | Medium | **Fixed** | — | — |
| SEC-33 | Medium | **Open** | `payments.service.ts:221` (claim sebelum intent ditemukan), `:274` (cek nominal dilewati bila `amount` null) | tidak berubah; blocker sebelum vendor aktif |
| SEC-34 | Low | **Fixed** (BFF) | `auth/altcha.service.ts:178-182` (HMAC domain `efn-altcha:v2` + IP), `:86-92` (IPv4 utuh, IPv6 /64), `auth.service.ts:68-89` (level adaptif dari counter yang ada), cap `app-config.ts:705` | efek samping CGNAT → SEC-54 |
| SEC-35 | Low | **Fixed** | — | — |
| SEC-36 | Low | **Fixed** | `scripts/host/efn-deploy:99-103` (verify-blob), `:127-174` (manifest, anti-downgrade), `:241-251` (`cosign verify` per image di host) | sisa env non-rahasia → SEC-50 |
| SEC-37 | Low (PLAUSIBLE) | **Fixed** (kode; LE/Cloudflare nyata belum) | `docker-compose.yml:294-296` (Traefik tanpa token), `:374-413` (`acme-dns`: tanpa port, hanya `egress`, `cap_drop ALL`, token via `_FILE`) | cakupan token tetap diverifikasi Superadmin (runbook §1). Efek samping baru → SEC-53 |
| SEC-38 | Low (PLAUSIBLE) | **Open** | keputusan klien Q-P11 tercatat | DPO |
| SEC-39, 40 | Info | **Fixed** | SEC-40: inventaris v3, runbook insiden, `gen-pki.sh:111` (root tidak ditimpa) | — |
| SEC-41 | Medium | **Fixed** (runtime terverifikasi devsecops) | `infra/s3-gateway/haproxy.cfg:81-87` (`;` ditolak, `%` di nama kunci ditolak, `%` apa pun untuk method tulis, daftar blok di `query,url_dec -i [&;]`), `:88-92,104,110` (path `url_dec`, dot-segment, `//`) | — |
| SEC-42 | Medium | **Fixed** | `efn-deploy:127-146` (skema, env, versi/ref), `:150-174` (anti-replay `seq` + semver + izin sekali pakai), `:210-230` (root/EXTRA_TRUST dipin + intermediate diverifikasi), `:241-251` (image hanya dari manifest, prefix dikunci, cosign di host), `deploy.sh` lapis 2 | secret env tetap tidak bertanda tangan (diakui, key-custody §4b). Env non-rahasia yang mengatur eksposur jaringan → SEC-50 |
| SEC-43 | Medium | **Partial** | `efn_audit/models/audit_policy.py:22,35-50` (minimum 365 hari), `efn_audit_log.py:41,189-199,210-242` (log purge & model kontrol tidak pernah di-GC), `audit_policy.py:58-88` (model kontrol selalu diaudit), `base.py:113-127` + `promtail.yml:51-63` + `loki-rules/efn-audit-sink.yml` (sink 400 hari + alert), salinan ClickHouse (`services/etl/efn_etl/audit*.py`) | butir 5 (`base.group_system` hanya Superadmin) belum: `efn_setup_security` tidak membatasi. Sisa admin-trust → SEC-51/52 |
| SEC-44 | Low | **Fixed** | `efn_pos_otp_gate/models/res_partner.py:72-84` (tanpa pengecualian `env.su`; penanda `cr.cache` hanya setelah cek lolos), test +3; QA G11.4 lewat RPC nyata | — |
| SEC-45 | Low | **Fixed** | `audit_policy.py:101-156` (masking wajib berbasis pola + nama orang), `base.py:262-267,294` (`res_name` model orang = `model,id`), `efn_audit_security.xml:15,21` (log pribadi tanpa company hanya grup Auditor Data Pribadi) | pola tidak mencakup M2O ke model orang/alamat → SEC-56 |
| SEC-46 | Info | **Fixed** (batas by design) | `efn_audit_log.py:80-102` (`origin_user_id`, `prev_company_id`), `base.py:64-104`, index `efn_audit_log.py:266-267` | cron tanpa helper tetap tanpa uid pemicu (README) |
| SEC-47 | Info | **Partial** | semua `FROM` (7 Dockerfile) dan 25 `image:` compose dipin `@sha256` | `scripts/check-odoo-deps.sh:24` masih hanya `efn_*/__manifest__.py` `depends`; direktori non-`efn_` di `addons/` dan `requirements.txt` tidak diperiksa |
| SEC-48 | Info | **Partial** | root MinIO acak (`minio_root_user`) Fixed | `common/route-pii-audit.spec.ts:20` masih daftar controller statis |

**Rekap:** Fixed 42 · Partial 4 (SEC-11, 43, 47, 48) · Open 2 (SEC-33, 38) · Regressed 0.

## Temuan baru

| ID | Severity | Judul | Lokasi utama | Risiko |
|---|---|---|---|---|
| SEC-49 | Medium | Perubahan model keamanan Odoo (keanggotaan grup, `res.groups`, `ir.rule`, `ir.model.access`, API key) tidak diaudit dan tidak masuk sink | `efn_audit/models/audit_policy.py:58-85`, `efn_setup_audit/data/efn_audit_rule_data.xml`, `docs/runbooks/odoo-access-changes.md:9` | R-S10, R-L01, multi-company |
| SEC-50 | Medium | Env deploy non-rahasia tanpa tanda tangan dan validasi: (a) WG bind/CIDR membuka `erp./bi./ops.` ke internet; (b) nilai multi-baris menyisipkan kunci ke `.env`; (c) kontrol keamanan bisa dimatikan | `scripts/host/efn-deploy:178-200`, `scripts/deploy.sh:63-74,226-230,233-281` | R-S07, R-S01, R-S10 |
| SEC-51 | Low (PLAUSIBLE) | Stream tamper-evident `efn_audit_sink` dapat disusupi baris palsu (substring/newline di pesan log Odoo) sehingga muncul alert critical palsu dan atribusi palsu. Catatan purge (`retensi:`/`erasure:`) dapat dipalsukan admin lewat server action | `infra/observability/promtail.yml:51-63`, `loki-rules/efn-audit-sink.yml:31`, `efn_audit_log.py:141-154,189-199` | R-S10 |
| SEC-52 | Low | Batas salinan audit ClickHouse: tanpa dead-man alert; penghapusan < 10 menit tidak terdeteksi; jangkar Loki 30 hari dan manual; pemegang kredensial `efn_etl` dapat menyisipkan rantai/duplikat (PLAUSIBLE); anggaran erasure dari log purge yang dapat dipalsukan | `services/etl/efn_etl/audit_job.py:159-192,217-252`, `audit.py:286-302,361-419`, `adapters.py:141-150,179-185`, `alert-rules.yml`, `loki.yml:41-52` | R-S10 |
| SEC-53 | Low | `acme-dns` (pemegang token, network egress) memiliki direktori provider file Traefik sehingga dapat menulis router/service apa pun, bukan hanya sertifikat | `infra/acme/acme-dns-init.sh:10-14`, `acme-dns.sh:83-91`, `docker-compose.yml:303-304` | R-S01, ADR-023 |
| SEC-54 | Low | Rate limit & PoW per IPv4 menghukum pengguna CGNAT: satu penyerang di IP bersama dapat menaikkan level ALTCHA ke 3 dan menghabiskan kuota 20 OTP/15 menit untuk semua pengguna di IP itu. IPv4-mapped IPv6 meruntuhkan binding (PLAUSIBLE) | `auth/altcha.service.ts:69-92`, `auth.service.ts:68-99`, `app-config.ts:657,719` | R-B01 (ketersediaan login), UX |
| SEC-55 | Info | Redaksi traceback: batas yang tersisa (nodb, pesan `ValidationError` hasil IntegrityError, route `http`) | `efn_core/models/ir_http.py:25-26,42` | R-S03 |
| SEC-56 | Info | Masking pola nama field tidak mencakup M2O ke model orang di model non-orang, alamat, dan field bertipe `date` | `efn_audit/models/base.py:262-267`, `audit_policy.py:102-115`, `base.py:336` | R-L01 |
| SEC-57 | Info | Catatan kecil (promtail-journal, Chrome test stage, `web.assets_tests`, mart GL lintas PT, origin oleh admin, run_number) | lihat detail | — |

---

## Detail temuan baru

### SEC-49: Medium: Perubahan hak akses Odoo tidak diaudit
**Lokasi:** `efn_audit/models/audit_policy.py:58-85`. `CONTROL_SPECS` hanya berisi `ir.config_parameter` (`efn.audit.%`), `ir.actions.server`, `ir.cron`, `ir.module.module`, `base.automation`. `efn_setup_audit/data/efn_audit_rule_data.xml` tidak punya aturan untuk `res.users`, `res.groups`, `ir.rule`, `ir.model.access`, `res.users.apikeys`. Runbook `docs/runbooks/odoo-access-changes.md:9` menyatakan "Perubahan tercatat di `efn.audit.log` bila aturan audit `res.users` aktif", padahal aturan itu tidak pernah dikirim. Laporan Odoo engineer G11 "Belum/sisa" juga mengakuinya.
**Kondisi:** Odoo CE 19 tidak mencatat perubahan grup user atau record rule di chatter. Grup `group_auditor_personal` (`efn_audit_groups.xml:24-29`) adalah satu-satunya penjaga akses log data pribadi lintas PT, dan record rule multi-company adalah satu-satunya pemisah data PT A dan PT B dalam satu database.
**Skenario:**
1. Admin (`base.group_system`, implied Manager audit, `efn_audit_groups.xml:36`) menambahkan dirinya ke `group_auditor_personal` lewat Settings → Users. Ia lalu membaca seluruh log perubahan kontak pelanggan lintas PT, lalu mencabut lagi grupnya. Tidak ada `efn.audit.log`, tidak ada baris sink, tidak ada alert.
2. Admin/insider menonaktifkan atau mengubah `domain_force` record rule multi-company (`ir.rule`) untuk `pos.order`/`res.partner`, atau menambah baris `ir.model.access`. Akibatnya user PT B membaca data PT A (UU PDP: akses tanpa dasar; R-L01). Perubahan dikembalikan setelahnya tanpa jejak.
3. Persistensi: admin memberi `base.group_system` atau membuat API key untuk akun pihak ketiga.
**Rekomendasi:**
1. Tambahkan ke `CONTROL_SPECS`, sehingga selalu diaudit, tidak ikut GC, dan masuk sink:
   - `res.users`: `group_ids`, `active`, `login`, `company_ids`, `company_id`, `share`
   - `res.groups`: `user_ids`, `implied_ids`, `name`
   - `ir.rule`: semua field, termasuk `active`, `domain_force`, `groups`, `perm_*`
   - `ir.model.access`: semua field
   - `res.users.apikeys`: create/unlink tanpa nilai key, sudah tertutup denylist `api_key`/`key`. Perhatikan denylist hanya substring `api_key`; field `key` milik model ini harus dikecualikan eksplisit.
2. Catatan: `res.users` termasuk `PERSONAL_MODELS`, sehingga lognya `is_personal`. Untuk perubahan grup, pertimbangkan `company_id` = company user agar auditor PT melihatnya. Sink tidak memuat nilai, jadi aman untuk Loki.
3. Alert Loki khusus (critical) untuk `model` ∈ {`res.groups`, `ir.rule`, `ir.model.access`} dan untuk penambahan grup `base.group_system`/`group_auditor_personal`.
4. Perbaiki kalimat runbook `odoo-access-changes.md:9` agar tidak menjanjikan jejak yang belum ada.
5. Tetap: SEC-43 butir 5 (batasi `base.group_system` ke Superadmin).

### SEC-50: Medium: Env non-rahasia deploy tidak ditandatangani, sebagian tidak divalidasi
**Lokasi:** `scripts/host/efn-deploy:178-200` (allowlist memuat `EFN_PUBLIC_BIND`, `EFN_WG_BIND`, `EFN_WG_HTTPS_PORT`, `EFN_WG_OUTLET_CIDR`, `EFN_WG_STAFF_CIDR`, `COMPOSE_PROFILES`, `RABBITMQ_STRICT_PERMISSIONS`, `ETL_AUDIT_COPY_ENABLED`, `PG_SUPERUSER`, `OLLAMA_VISION_MODEL`, `WHATSAPP_*`. Baris `:194` hanya memvalidasi format base64, dan `:197` men-decode tanpa menolak karakter kontrol). `scripts/deploy.sh:63` (`CIDR_RE` menerima `0.0.0.0/0`), `:69,:73-74` (`EFN_WG_BIND` cukup berupa IPv4 yang ada di host mana pun, termasuk IP publik), `:233-281` (heredoc `.env`; `:245,249,252-255` menulis `PG_SUPERUSER`, `RABBITMQ_STRICT_PERMISSIONS`, `OLLAMA_VISION_MODEL`, `WHATSAPP_*` **tanpa validasi**), `:226-230` (`ETL_AUDIT_COPY_ENABLED=false` eksplisit selalu menang).
**Kondisi & skenario:** penyerang memegang `DEPLOY_SSH_KEY` dan memakai rilis bertanda tangan **asli** (tidak rahasia) dengan `env.b64` buatannya sendiri. Ini model ancaman yang sama dengan SEC-42.
- **(a) Eksposur internet (terverifikasi dari kode):** `EFN_WG_BIND=<IP publik host>`, `EFN_WG_HTTPS_PORT=8443`, dan `EFN_WG_STAFF_CIDR=0.0.0.0/0` lolos semua cek (`:69-74`; IP publik memang ada di `ip -4 addr`). Traefik mem-publish entrypoint `wg-https` (`docker-compose.yml:286`) di IP publik, dan middleware allowlist WG menerima semua alamat. Akibatnya `erp.` (Odoo), `bi.` (Metabase), `ops.` (Grafana) dapat diakses dari internet tanpa VPN. Port yang di-publish Docker umumnya melewati aturan ufw. Ini melanggar prinsip "Odoo tidak pernah diakses langsung dari internet". Sertifikat tetap valid (SAN yang sama).
- **(b) Injeksi `.env` (terverifikasi dari kode; dampak PLAUSIBLE):** nilai `RABBITMQ_STRICT_PERMISSIONS` yang di-decode menjadi `true\nIMAGE_ODOO=evil.example/odoo@sha256:<hex>\nNODE_ENV=development` ditulis apa adanya ke `.env` setelah baris `IMAGE_ODOO` dan `NODE_ENV`. Parser dotenv Compose memakai nilai terakhir. Selama `deploy.sh` berjalan, `IMAGE_*` yang di-export dari manifest menang (env shell > `.env`), sehingga deploy itu sendiri aman. Namun `.env` tetap tercemar, sehingga setiap `docker compose up`/recreate manual berikutnya (runbook insiden, restart operasional) menjalankan image penyerang. Ini membatalkan SEC-42 secara tertunda. Kunci yang tidak di-export (mis. `NODE_ENV`, `GRAFANA_ADMIN_USER`, `PII_RETIRED_VERSIONS`) langsung efektif.
- **(c) Mematikan kontrol:** `ETL_AUDIT_COPY_ENABLED=false` mematikan salinan tamper-evident tanpa alert (lihat SEC-52). `RABBITMQ_STRICT_PERMISSIONS=false` membatalkan SEC-25. `COMPOSE_PROFILES` tanpa `observability` mematikan Loki/alert.
**Rekomendasi:**
1. Pindahkan nilai topologi/keamanan ke `/etc/efn/deploy.conf` milik root (seperti `EFN_PKI_ROOT_SHA256`), lalu keluarkan dari allowlist env: `EFN_PUBLIC_BIND`, `EFN_WG_BIND`, `EFN_WG_HTTPS_PORT`, `EFN_WG_*_CIDR`, `TRAEFIK_DOMAIN`, `COMPOSE_PROFILES` minimum. Alternatifnya, masukkan ke manifest bertanda tangan per environment.
2. `deploy.sh`: `EFN_WG_BIND` wajib alamat di interface `wg0` (`ip -4 addr show dev wg0`) dan bukan IP publik. CIDR wajib di dalam RFC 1918 dengan prefix ≥ /8, dan tolak `0.0.0.0/0`.
3. `efn-deploy:197`: tolak nilai yang mengandung karakter kontrol (`[[ "$val" != *[[:cntrl:]]* ]]`). `deploy.sh`: validasi semua variabel yang ditulis ke `.env` (regex per variabel). Tulis `.env` dengan pemeriksaan "satu kunci satu baris" setelah ditulis.
4. Di production, tolak `ETL_AUDIT_COPY_ENABLED=false` dan `RABBITMQ_STRICT_PERMISSIONS=false` kecuali ada flag lokal Superadmin (pola `/etc/efn/allow-downgrade`). `COMPOSE_PROFILES` wajib memuat `core,data,observability`.
5. Tambahkan uji di `scripts/host/test-efn-deploy.sh`: nilai multi-baris ditolak, CIDR `0.0.0.0/0` ditolak, dan WG bind ke IP non-wg0 ditolak.

### SEC-51: Low (PLAUSIBLE): Pemalsuan baris sink audit & catatan purge
**Lokasi:** `infra/observability/promtail.yml:51-63` (selector `{service="odoo"} |= "odoo.addons.efn_audit.sink: EFN_AUDIT_SINK "` adalah pencocokan **substring** di mana pun dalam baris, lalu regex greedy `EFN_AUDIT_SINK (?P<sink>\{.*\})`), `loki-rules/efn-audit-sink.yml:15,30-31` (alert berdasarkan isi JSON, termasuk `note`), `odoo.conf.tpl:26` (`log_handler = :INFO`), `efn_audit_log.py:141-154` (`_efn_audit_write_purge_log` menerima `note` bebas), `:189-199` (`_efn_audit_purge_logs` tidak memeriksa umur log).
**Kondisi:**
1. Label `service="odoo"` memang tidak dapat dipalsukan container lain. Namun **Odoo sendiri** mencatat teks yang dikendalikan pengguna pada level INFO/WARNING. Contohnya login yang gagal (`Login failed for login:%s`, PLAUSIBLE untuk Odoo 19) dan pesan exception yang memuat nilai input. Login berisi `x odoo.addons.efn_audit.sink: EFN_AUDIT_SINK {"operation":"purge","model":"efn.audit.log","uid":2,"note":"manual"}` menghasilkan satu baris log yang lolos selector. Akibatnya stream 400 hari berisi event palsu dan `EfnAuditUnexpectedPurge` (critical) firing. Logging Python tidak meng-escape newline, sehingga login multi-baris bahkan dapat meniru seluruh prefiks baris Odoo. Penyerang cukup punya akses ke halaman login `erp.` (staf/outlet di WireGuard).
2. Admin dapat membuat server action yang memanggil `logs._efn_audit_purge_logs("retensi: {count} log")` atas log yang **belum** berumur 365 hari, atau `_efn_audit_write_purge_log("res.partner", "erasure: 500 log")`. Catatan `retensi:`/`erasure:` dikecualikan dari alert critical (`efn-audit-sink.yml:31`). Satu-satunya sinyal di Loki adalah warning `EfnAuditControlChange` saat server action dibuat.
**Dampak:** alert fatigue dan atribusi palsu dalam investigasi (butir 1). Purge oleh admin tampak sah di Loki (butir 2). Untuk log yang sudah tersalin ke ClickHouse, rekonsiliasi tetap menandai `unexplained` untuk butir 2 retensi, **kecuali** anggaran erasure dipalsukan untuk model orang (SEC-52 butir 5).
**Rekomendasi:**
1. Promtail: anchor ke format baris Odoo, mis. `^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} \d+ INFO \S+ odoo\.addons\.efn_audit\.sink: EFN_AUDIT_SINK (\{[^\n]*\})$`. Gunakan stage `json` yang mewajibkan `log_id` numerik, lalu buang baris yang tidak cocok skema.
2. Odoo: pasang `logging.Filter` global (di `efn_core`) yang meng-escape `\r`/`\n` pada argumen pesan log (bukan traceback). Ini menutup juga injeksi log ke stream lain (`EFN_AUDIT_RECON` di `service=etl`).
3. Lebih kuat: sink memakai HMAC per baris (kunci dari `/run/secrets`, tidak dimiliki admin Odoo). ETL memverifikasi dan mencocokkan `log_id` sink dengan `efn_audit_log`.
4. `_efn_audit_purge_logs`: tolak log yang lebih muda dari `effective_retention` (pemeriksaan umur di metode, bukan hanya di GC). Tulis catatan purge hanya dari jalur yang ditandai penanda `cr.cache` yang diset cron/erasure. Ini tetap dapat ditiru admin lewat kode, tetapi menaikkan biaya. Kontrol nyata tetap SEC-43 butir 5.

### SEC-52: Low: Batas tamper-evidence salinan audit ClickHouse
**Lokasi:** `services/etl/efn_etl/audit_job.py:159-192` (copy; `max_id` dari salinan), `:217-252` (recon dijalankan proses ETL yang sama), `:187-191` (`EFN_AUDIT_CHAIN_HEAD` di log), `audit.py:286-302` (`verify_chain` per `chain_company`), `:361-419` (anggaran retensi/erasure dari catatan purge), `adapters.py:141-150` (`heads`/`max_log_id` dari seluruh tabel), `:179-185` (`copied_range` tanpa deteksi duplikat `log_id`), `infra/observability/alert-rules.yml` (tidak ada alert kesegaran ETL/recon), `loki.yml:41` (stream `service=etl` 30 hari).
**Yang sudah benar (diverifikasi):**
- Kanonikalisasi JSON (`sort_keys`, tanpa spasi, datetime UTC mikrodetik, `False`/`""` → `null`) deterministik.
- Kolom yang sah berubah tidak di-hash.
- `row_hash = sha256(prev || payload_hash)` benar dan mengikat isi baris lewat `lines_digest`.
- Nilai pribadi tidak disalin, hanya hash-nya.
- User `efn_etl` tanpa ALTER DELETE/UPDATE/TRUNCATE/DROP/OPTIMIZE (`etl-access.sql:35-41`).
- QA membuktikan `UPDATE res_name` terdeteksi `modified` dan memicu alert critical.
**Batas yang ditemukan:**
1. **Dead-man tidak ada.** Bila ETL berhenti, `ETL_AUDIT_COPY_ENABLED=false` (SEC-50c), atau recon tidak pernah selesai, tidak ada alert. `EfnAuditCopyFailing` hanya terpicu bila ada baris "gagal".
2. **Jendela settle.** Log yang dihapus sebelum `ETL_AUDIT_SETTLE_SECONDS` (default 600 s) tidak pernah tersalin, dan gap id dianggap rollback. Contohnya perubahan harga dan penghapusan lognya dalam satu transaksi server action/SQL (dikonfirmasi QA G12.3). Ini by design, tetapi perlu dicatat sebagai batas kontrol.
3. **Jangkar eksternal lemah.** `EFN_AUDIT_CHAIN_HEAD` hanya ada di stream `service=etl` (retensi 30 hari) dan tidak dibandingkan otomatis.
4. **(PLAUSIBLE) Pemegang kredensial `efn_etl`** (atau kompromi container ETL) dapat INSERT baris yang valid secara hash. Rantai tidak berkunci, sehingga siapa pun yang bisa INSERT dapat membangun rantai sah di `chain_company` baru. Contoh: baris duplikat untuk `log_id` X dengan `create_date` lama di rantai 999 (valid sendiri karena `verify_chain` per rantai), ditambah hapus X di PostgreSQL dan satu log purge `retensi: 1 log` palsu di PG. `copied_range` mengembalikan dua baris untuk X, dan dict mengambil yang terakhir (urutan tidak dijamin), sehingga X dapat terklasifikasi `retention`. INSERT `log_id` sangat besar juga membuat `max_log_id` meloncat, sehingga recon melakukan loop ratusan juta jendela (DoS). Recon dijalankan oleh identitas yang sama dengan penulis.
5. **Anggaran erasure** dihitung dari catatan `erasure: N log` di PG. Catatan ini dapat ditulis admin lewat server action (SEC-51 butir 2). Log `res.partner` yang memuat jejak perubahan HP member (bukti SEC-07/R-B06) dapat dihapus lalu terklasifikasi `erasure` tanpa alert.
**Rekomendasi:**
1. Alert dead-man: absennya `EFN_AUDIT_RECON` > 26 jam atau `EFN_AUDIT_CHAIN_HEAD` > 1 jam di production (`absent_over_time`). Production menolak flag `false` (SEC-50).
2. Simpan `EFN_AUDIT_CHAIN_HEAD` di stream ber-retensi 400 hari (`retention_stream` `{service="etl"} |= "EFN_AUDIT_CHAIN_HEAD"` lewat label promtail). Recon membandingkan kepala hari ini dengan kepala kemarin di Loki, atau di tabel terpisah yang hanya bisa ditulis identitas lain.
3. Recon: periksa `count() > 1` per `log_id`, `chain_company` ∉ {0} ∪ id company yang dikenal, `chain_company ≠ coalesce(company_id,0)`, dan `max_log_id` hanya dari rantai yang valid. Jalankan recon dengan user ClickHouse terpisah tanpa INSERT ke tabel salinan (role `audit_copy_reader` + INSERT `audit_recon_run` saja).
4. Opsional: HMAC `row_hash` dengan kunci yang tidak dimiliki admin DB (secret khusus ETL). Ini menaikkan biaya pemalsuan oleh admin PG/CH, walau tetap tidak melindungi dari kompromi container ETL.
5. Anggaran erasure dicocokkan dengan jumlah `dsar_request` `completed` di BFF (atau `efn.privacy` di Odoo) pada periode yang sama. Selisih ditandai `unexplained`.
6. Dokumentasikan batas butir 2 di README ETL dan runbook `etl-audit-copy.md`. Tamper-evidence berlaku untuk log yang hidup ≥ settle.

### SEC-53: Low: `acme-dns` dapat menulis konfigurasi dinamis Traefik
**Lokasi:** `infra/acme/acme-dns-init.sh:12` (`chown 65532:0 /certs`: user `acme-dns` = pemilik direktori), `docker-compose.yml:303` (volume yang sama = `/etc/traefik/dynamic`, direktori provider file yang di-watch Traefik), `acme-dns.sh:83-91` (menulis `acme-dns.yml`).
**Kondisi:** provider file Traefik memuat **setiap** `*.yml` di direktori itu, termasuk `http.routers`/`services`/`middlewares`/`tls.options`, bukan hanya `tls.certificates`. Container `acme-dns` memproses respons jaringan (ACME, API DNS) di network `egress`.
**Skenario:** kompromi image/proses lego (supply chain atau bug parsing) → tulis `/certs/x.yml` berisi router `Host(\`api.<domain>\`) && PathPrefix(\`/web\`)` di entrypoint `websecure` ke service `odoo@docker`. Hasilnya Odoo terbuka ke internet tanpa allowlist WG, atau middleware allowlist ditimpa. Sebelum G12 token DNS yang terekspos; kini arah kepercayaannya terbalik, tetapi wewenangnya lebih luas dari yang dibutuhkan.
**Rekomendasi:**
1. Pisahkan penulisan konfigurasi dari proses ber-network. `acme-dns` hanya menulis PEM ke volume data. Sidecar kecil `acme-publish` (`network_mode: none`, root-owned script, read-only) memvalidasi bahwa file berisi tepat PEM sertifikat + kunci EC untuk SAN yang diharapkan, lalu menulis `acme-dns.yml` dari template tetap ke direktori Traefik.
2. Bila tetap satu container: Traefik `providers.file.filename` per file tidak di-watch untuk direktori. Alternatifnya, alert bila jumlah router `@file` di API/metrics Traefik berubah (`traefik_config_reloads_total` + daftar router).
3. Init: buat `base/` dan `dev/` root-owned di dalam direktori yang juga root-owned (sticky), agar `acme-dns` tidak dapat mengganti nama titik mount.

### SEC-54: Low: Rate limit dan ALTCHA adaptif per IPv4 di belakang CGNAT
**Lokasi:** `auth/auth.service.ts:68-89` (level dari `altcha:ip:<ip>` dan `otp:login:ip:<ip>`), `:97` (limit OTP per IP), `app-config.ts:657` (`OTP_RL_SOURCE_LIMIT` 20 per 900 s), `:719` (challenge 120/900 s), `altcha.service.ts:47,69-78` (level 1 pada 25 %), `:86-92` (binding).
**Kondisi:** operator seluler Indonesia memakai CGNAT IPv4 dengan banyak pelanggan per IP. Level 1 sudah aktif setelah 5 OTP per 15 menit dari satu IP, dan blokir penuh (429) terjadi setelah 20.
**Skenario:** penyerang di jaringan seluler yang sama memecahkan 20 ALTCHA (±0,25–4 s/solusi di desktop) dan meminta 20 OTP ke nomor acak. Semua pengguna di IP CGNAT itu tidak bisa login selama 15 menit, dan menerima PoW level 3 (±8–20 s di ponsel bawah) sebelum batas. Batas 20/IP sudah ada sebelum G12; SEC-34 menambahkan penalti PoW di jalur yang sama. Selain itu (PLAUSIBLE), bila `req.ip` berbentuk `::ffff:a.b.c.d`, `altchaIpBinding` memperlakukannya sebagai IPv6 dan memotong ke `0:0:0:0::/64`, sehingga semua klien IPv4 berbagi satu binding dan pengikatan IP tidak efektif.
**Rekomendasi:**
1. Normalisasi IPv4-mapped (`::ffff:`) ke IPv4 sebelum binding dan sebelum kunci rate limit, lalu tambahkan test.
2. Hitung level per IP dari **rasio kegagalan** (OTP verify gagal, challenge tanpa solusi), bukan jumlah permintaan sukses. Naikkan batas per IPv4 dan andalkan batas per-member serta budget global sebagai kontrol utama.
3. Pantau `count distinct member_hash per IP` untuk mengenali IP CGNAT, dan beri kuota lebih tinggi untuk IP tersebut. Dokumentasikan dampak di runbook `otp-failure-rate.md`.

### SEC-55: Info: Redaksi traceback, batas tersisa
- `efn_core/models/ir_http.py:25-26`: request tanpa DB tidak lewat `ir.http`. Di produksi dimitigasi oleh satu DB + `dbfilter = ^efn_.*$` + `list_db = False` (`odoo.conf.tpl:11-13`), sehingga monodb selalu terpilih. Tambahkan uji smoke `/web/database/*` dan `/json/2` tanpa header DB.
- `:42` menganggap `ValidationError` sebagai exception bisnis. `ValidationError` hasil konversi `IntegrityError` dapat memuat nama tabel/constraint dan **nilai kunci** (`Key (barcode)=(…) already exists`), termasuk nilai milik record company lain. Ini informasi minor dan dapat diterima, tetapi sebaiknya pesan `IntegrityError` diganti generik untuk non-admin.
- Route `type="http"` tidak disentuh. Odoo tidak menampilkan traceback HTML di luar `dev_mode` (dev_mode tidak diset di template).
- Kontrol yang benar: uid admin dari sesi (bukan `request.env.uid` yang bisa dinaikkan controller), fail-closed, tanpa monkeypatch `serialize_exception`, log server tetap lengkap.

### SEC-56: Info: Celah masking berbasis pola
- `base.py:336`: nilai M2O/M2M ditulis sebagai `display_name` relasi. Di model non-orang dengan field `partner_id`/`user_id` (mis. aturan `pos.order` yang kelak ditambah admin, `stock.quant.user_id` di aturan bawaan), nama pelanggan/staf tersimpan utuh. Baris itu tidak `is_masked`, sehingga log tidak `is_personal`. Salinan ClickHouse meredaksinya (`audit.py:169-177`), dan erasure Odoo mengosongkan ref ke `res.partner`.
- `audit_policy.py:102-115`: `street`, `street2`, `city`, `zip`, `function`, `comment` tidak cocok pola. `is_pii_field` hanya untuk tipe `char`/`text` (`:110`), sehingga `birthday`/`date_of_birth` (tipe `date`) tidak di-mask.
- **Rekomendasi:** M2O/M2M ke `PERSONAL_MODELS` disimpan sebagai `model,id` tanpa nama (seperti `res_name`), atau diberi `kind="name"`. Tambahkan `street|city|zip|birth|address` ke pola, dan pertimbangkan tipe `date` untuk nama yang cocok `birth`.

### SEC-57: Info: Catatan kecil
- **`promtail-journal`** (`docker-compose.prod.yml:158-185`): container root (tanpa capability) membaca seluruh `/var/log/journal`, lalu memfilter di relabel. Identifier `efn-deploy` dapat dipalsukan proses lokal mana pun (`logger -t efn-deploy`). Dampaknya rendah, karena user host adalah admin. Pertimbangkan `user: "0:<gid systemd-journal>"` non-root dengan `group_add`, dan filter `_UID=0` untuk `efn-deploy`.
- **Stage `test` Odoo** (`services/odoo/Dockerfile:52-69`): fingerprint kunci Google diverifikasi **ada**, tetapi seluruh file kunci di-dearmor. Bila file memuat kunci tambahan, kunci itu ikut dipercaya. Risikonya kecil (butuh MITM TLS ke `dl.google.com`). Produksi aman: `build-push.yml:72` memakai target default (stage terakhir `production`), dan image produksi diverifikasi tanpa Chrome.
- **`web.assets_tests`** (manifest `efn_pos_otp_gate:28-30`, `efn_pos_payment_gateway:31-33`): dimuat di produksi bila user membuka `?debug=tests` (perilaku standar Odoo). Tour hanya mengklik UI dengan hak user itu sendiri, dan mock BFF ada di sisi Python test (tidak ikut). Tidak melemahkan produksi.
- **Mart GL** (`metabase-access.sql:24-27`): `metabase_reader` membaca GL semua PT. Datanya bebas PII (partner tidak disalin; `gl.py` tidak membaca `name`/`ref` baris), tetapi pembatasan per PT di Metabase menunggu Q-6. Pastikan koleksi Metabase dibatasi grup sebelum dibuka ke manajer per-PT.
- **`origin_user_id`** (`base.py:89-104`): sumbernya tepercaya terhadap RPC/context (benar). Admin yang menjalankan server action dapat mengisi `env.cr.cache["efn_audit_origin_uid"]` dan memfitnah uid lain sebagai pemicu. `user_id` tetap uid asli, dan pembuatan server action tercatat (admin-trust, diakui).
- **`efn-deploy` `seq`** = `github.run_number`. *Re-run* workflow yang sama menghasilkan `seq` sama dengan arsip berbeda, sehingga ditolak (`:160`) kecuali diizinkan Superadmin. Ini benar secara keamanan. Catat di runbook rilis.
- **VLM** (`ai-ocr-worker/efn_ocr/imaging.py:68-79`): re-encode JPEG setelah dekode ber-batas (`MAX_IMAGE_PIXELS` 40 MP). Tidak ada permukaan baru.

---

## Kontrol yang sudah benar (untuk manajemen)
- **Redaksi error Odoo (SEC-22):** titik ekstensi resmi `ir.http._handle_error`, hanya JSON-RPC/`/json/2`, admin dideteksi dari sesi (bukan env yang bisa dinaikkan), gagal → respons generik. Pesan bisnis POS tetap utuh.
- **`efn_audit` 1.2.0:** retensi minimum hard-coded, log purge dan log model kontrol tidak pernah di-GC, aturan bawaan hanya bisa diperluas, aturan ber-xmlid tidak bisa dihapus, masking wajib berbasis pola, nama orang di-mask inisial, dan `res_name` model orang tanpa nama. Rule multi-company memisahkan log pribadi tanpa company ke grup khusus yang tidak diberikan ke siapa pun secara default. `origin_user_id` tidak dapat diisi lewat context RPC. Sink Loki dikirim setelah commit, tanpa nilai/PII, dan JSON-escaped (tidak bisa memecah baris dari dalam payload).
- **Salinan ClickHouse:** hash berantai deterministik, redaksi PII sebelum salin, user ETL tanpa DELETE/ALTER, dan rekonsiliasi lima kelas. Tamper nyata terdeteksi dan memicu alert (QA G12.3).
- **Kunci HP member (SEC-44):** keputusan selalu dari uid asli, termasuk di bawah `sudo()`; diuji lewat RPC nyata.
- **BFF:** tidak ada secret di `process.env`. Env literal secret ditolak di luar `APP_ENV=dev`. ALTCHA terikat IP dengan domain-separated HMAC, verifikasi waktu-konstan, dan sekali pakai.
- **Deploy:** manifest bertanda tangan, image dan prefix registry dikunci, `cosign verify` di host, root CA dipin, anti-replay/downgrade, dan jejak `efn-deploy` ke Loki 400 hari.
- **Infra:** filter s3-gateway kebal encoding (diuji 24 varian). Token DNS keluar dari Traefik. `/metrics` ocr-worker kini allowlist CN (E12 ditutup). Semua image dipin digest. Image produksi Odoo tanpa browser.
- **Data:** mart GL tanpa PII (partner, label, dan ref tidak disalin). VLM menerima gambar yang di-re-encode.

## UU PDP: status

| Kewajiban | Status r5 | Bukti / sisa |
|---|---|---|
| Consent server-side | **Implementasi** | — |
| Hak akses (ekspor) | **Implementasi** | ekspor besar belum |
| Hak hapus lintas store | **Implementasi** | + log audit Odoo di-purge; salinan CH hanya hash untuk data pribadi |
| Koreksi / ganti nomor | **Belum** | — |
| Retensi | **Implementasi** (menunggu DPO) | audit 730/min 365; Loki sink 400 hari; salinan CH TTL 10 tahun (Q-49) |
| Akuntabilitas akses data pribadi | **Sebagian** | pemberian grup Auditor Data Pribadi tidak tercatat (SEC-49) |
| Notifikasi insiden 3×24 jam | **Runbook ada** | `docs/runbooks/security-incident.md` |
| Inventaris data / RoPA | **v3, terkini** | `PDP_DATA_INVENTORY.md` |
| DPO, kebijakan privasi final, transfer lintas negara | **Belum diputuskan** | Q-P9..P11 |

## Prioritas perbaikan
1. **Sebelum host staging/produksi dipasang (`efn-deploy` di host):** SEC-50. Pin topologi jaringan di `deploy.conf`, tolak karakter kontrol, validasi semua nilai `.env`, dan tolak pematian kontrol di production.
2. **Sebelum outlet pilot:** SEC-49 (audit `res.users` grup, `res.groups`, `ir.rule`, `ir.model.access`, API key + alert), SEC-43 butir 5 (keputusan klien: `base.group_system` hanya Superadmin), dan SEC-52 butir 1 (dead-man alert salinan audit).
3. **Sebelum vendor payment diaktifkan:** SEC-33 (tetap).
4. **Hardening terjadwal:** SEC-51 (anchor promtail + escape newline log Odoo), SEC-52 butir 2–5, SEC-53 (pisahkan penulis config Traefik), SEC-54 (normalisasi `::ffff:`, level berbasis kegagalan), sisa SEC-47 (`check-odoo-deps` tolak direktori non-`efn_`) dan SEC-48.
5. **Backlog:** SEC-55..57, SEC-11 (DPO, koreksi nomor), SEC-38. Verifikasi runtime: SEC-51 (apakah login gagal Odoo 19 tercatat dengan teks login), SEC-54 (bentuk `req.ip` di belakang Next.js/Traefik), SEC-37 dengan LE staging nyata, dan `promtail-journal` di host Linux.
