# Security Review (r9): G16 CI GitHub paket gratis + G16b Metabase enkripsi at rest (2026-10-09)

Reviewer: `efn-security-reviewer` (read-only terhadap kode). Lanjutan `SECURITY_REVIEW_2026-10-09-r8.md`.

Cakupan:
- Branch `chore/wave-16b-followups` vs `main`: `f04c816` (gate Trivy image di PR, alur PR manual) dan `1e9f20b` (Metabase v0.58 driver details, enkripsi at rest DB aplikasi).
- Commit G16 yang langsung masuk `main` sejak r8: `7c048b4`, `7c33351`, `62b1bf1`, `ef346a0`.

File yang dibaca:
- `.github/workflows/{ci,security,build-push,deploy,codeql,dast}.yml`, `.github/dependabot.yml`, `.trivyignore.yaml`, `.gitleaksignore`, `.hadolint.yaml`
- `infra/metabase/{Dockerfile,efn-entrypoint.sh,provision.py,README.md}`, `infra/compose/docker-compose.yml` (anchor `x-common`, metabase, metabase-tls, metabase-provision)
- `scripts/check-encryption.sh`, `scripts/secrets.list`, `scripts/gen-secrets.sh` (b64key32), `docs/runbooks/{ci-github-free-plan,key-custody}.md`
- `services/superapp-web/{proxy.ts,next.config.mjs,components/AltchaWidget.tsx,package.json,Dockerfile}`
- `services/api-gateway/{src/main.ts,package.json,Dockerfile}`, `src/config/app-config.ts` (trustProxy), `src/auth/altcha.service.ts`, `src/common/security/webhook-*`
- `services/ai-ocr-worker/efn_ocr/imaging.py` (Pillow 12)

Metode: reviewer hanya membaca kode dan konfigurasi, tidak menjalankan apa pun (tanpa Bash, tanpa `git diff`). Daftar perubahan per commit diturunkan dari pesan commit, komentar G16/G16b di file, dan laporan agent. Perilaku yang tidak bisa dipastikan dari kode ditandai **PLAUSIBLE**.

## Ringkasan eksekutif (untuk manajemen)

1. **Tidak ada temuan Critical/High.** Semua scanner keamanan (semgrep, Trivy fs/image, checkov, gitleaks, dependency-review, `npm audit`, `pip-audit`) masih **gagal keras** bila ada temuan. Penyesuaian ke paket gratis hanya memindahkan *tampilan* hasil ke log, bukan melemahkan gerbang. Identitas tanda tangan image (cosign) untuk deploy tetap terkunci ke `build-push.yml@main` / tag rilis.
2. **Enkripsi at rest Metabase sudah aktif dan fail-closed.** Tanpa kunci yang valid, container berhenti. Kunci tidak ada di env compose maupun log, dan dicek otomatis di CI. Temuan lama **SEC-64 kini Fixed**.
3. **Satu temuan Medium (SEC-80): gerbang keamanan di paket gratis tidak memblokir rilis.** Branch protection tidak tersedia, sebagian commit G16 di-push langsung ke `main`, dan pipeline build → tanda tangan → deploy staging **tidak menunggu** hasil CI/Security. Kode yang gagal scan tetap bisa dibangun, ditandatangani, lalu dideploy. Perbaikannya murah: gate "CI & Security hijau untuk commit ini" sebelum push/sign dan deploy.
4. Temuan lain: **5 Low dan 3 Info**, semuanya hardening pipeline CI dan dokumentasi:
   - izin PR build terlalu lebar;
   - image yang di-scan berbeda build dengan yang di-push;
   - SBOM ikut ke log transparansi publik;
   - alasan pengecualian Metabase tidak akurat.
5. **Rekomendasi:** perbaiki SEC-80 sebelum `STAGING_DEPLOY_ENABLED=true` atau tag rilis `v*` pertama. Sisanya dikerjakan bertahap.

## Temuan baru

| ID | Severity | Judul | Lokasi utama | Risiko | Status (G16c) |
|---|---|---|---|---|---|
| SEC-80 | **Medium** | Gerbang CI/Security tidak memblokir build, tanda tangan, maupun deploy. Tanpa branch protection, push langsung ke `main` lolos | `build-push.yml:12-27`, `deploy.yml:7-13,37-39`, `security.yml:1`, `ci-github-free-plan.md:14-16,106-108` | R-S07, R-S08 | **Fixed** (kode; run GitHub nyata menunggu push ke `main`) |
| SEC-81 | Low | Run PR `build-push` mendapat `packages: write`, `id-token: write`, `attestations: write` | `build-push.yml:18-26,46-51` | R-S08 | **Fixed** |
| SEC-82 | Low (PLAUSIBLE) | Trigger `workflow_run` deploy tidak memfilter `event == push` dan repo asal, padahal `build-push` kini juga jalan untuk PR | `deploy.yml:8-11,37-39,53,59` | R-S07 | **Fixed** |
| SEC-83 | Low (PLAUSIBLE) | Image yang di-scan Trivy dibangun terpisah dari image yang di-push dan ditandatangani (TOCTOU via cache) | `build-push.yml:97-120` vs `:148-160` | R-S08 | **Fixed** |
| SEC-84 | Low (PLAUSIBLE) | Attestation SBOM dikirim ke Rekor publik. Klaim runbook "SBOM tidak dipublikasikan" belum terbukti | `build-push.yml:162-192`, `ci-github-free-plan.md:29-32` | R-S10, kerahasiaan bisnis | **Fixed** (klaim runbook dikoreksi; SBOM tidak publik) |
| SEC-85 | Low | Pengecualian DS-0002 Metabase disetujui atas fakta keliru ("cap_drop ALL"). Service `metabase` tanpa `cap_drop` dan me-mount secret ClickHouse yang tidak dipakai | `.trivyignore.yaml:86-94`, `docker-compose.yml:1263-1293` | R-S03, least privilege | **Fixed** (persetujuan ulang DS-0002 menunggu pemilik proyek) |
| SEC-86 | Info | Cek `check-encryption.sh` untuk entrypoint Metabase bisa dilewati dengan `entrypoint: []` atau penggantian image | `scripts/check-encryption.sh:118-125` | ADR-022 | **Fixed** |
| SEC-87 | Info | README rotasi kunci Metabase keliru menyatakan kunci di argv tidak terlihat; kunci terlihat dari `ps` host | `infra/metabase/README.md:48-56` | ADR-022 | **Partial** (dokumen jujur + mitigasi; argv inheren di CLI Metabase) |
| SEC-88 | Info | Kebersihan CI: checkout tanpa `persist-credentials: false` sebelum `npm ci`/`pip install`, tool tidak dipin, label checkov "compose" menyesatkan | `ci.yml:33,62,89,136,177,223,96,181`, `security.yml:176,193` | R-S07 | **Fixed** |

**Rekap: 0 Critical · 0 High · 1 Medium · 5 Low · 3 Info.** Status temuan lama: SEC-64 → **Fixed**. Temuan lain tidak berubah sejak r8.

**Status setelah G16c (efn-devsecops-engineer, 9 Okt 2026):** 8 Fixed, 1 Partial (SEC-87). Bukti per temuan: bagian
*Status perbaikan G16c* di akhir dokumen. Belum di-commit (branch `chore/wave-16b-followups`); perilaku workflow baru
baru terbukti penuh setelah run GitHub pertama di `main`.

**Status setelah re-review G16c (efn-security-reviewer, 9 Okt 2026, commit `4996aa8`):** SEC-80 → **Partial**
(mekanisme gate benar dan gagal-tertutup, tetapi cakupan CI per commit hanya delta push: SEC-89), SEC-81..86 dan
SEC-88 terverifikasi **Fixed**, SEC-87 tetap **Partial**. Temuan baru: **1 Medium (SEC-89), 3 Low (SEC-90..92), 2 Info
(SEC-93..94)**. Detail: bagian *Re-review G16c* di akhir dokumen.

---

## Detail temuan

### SEC-80: Medium: Gerbang keamanan tidak memblokir rilis di paket gratis
**Fakta (dari kode dan runbook):**
- Runbook mengakui bahwa di repo privat paket gratis, branch protection/ruleset tidak dapat memaksa PR (`ci-github-free-plan.md:106-108`). Gerbangnya hanya disiplin manual.
- Commit G16 (`7c048b4`, `7c33351`, `62b1bf1`, `ef346a0`) masuk `main` **langsung**, tanpa PR. Artinya alur "tanpa push langsung" sudah tidak dipatuhi.
- `build-push.yml:12-15` berjalan pada setiap `push` ke `main` dan **tidak** bergantung pada workflow CI/Security. Satu-satunya gate di dalamnya adalah Trivy image.
- Setelah build sukses, `deploy.yml:8-11,37-39` langsung mendeploy staging (bila `STAGING_DEPLOY_ENABLED=true`). Tag `v*` langsung memicu deploy production (`:12-13`, dengan approval Environment).
- Komentar `security.yml:1` ("Memblokir merge") dan runbook `:14-16` ("tetap memblokir merge/push") tidak sesuai kenyataan. Workflow Security pada `push` ke `main` hanya **melaporkan setelah fakta**.

**Skenario:**
1. Commit berisi secret (gitleaks merah), injeksi (semgrep merah), dependency rentan (`npm audit` merah), atau test RPC-surface yang gagal di-push ke `main`, langsung atau lewat merge PR yang check-nya belum selesai/merah.
2. `Build & Push` tetap hijau karena hanya Trivy image yang dicek. Image ditandatangani dengan identitas `build-push.yml@refs/heads/main`, persis identitas yang dipercaya `deploy.yml:123` dan `efn-deploy` di host.
3. Deploy staging berjalan otomatis. Tag `v*` dari commit yang sama menghasilkan image production yang sah secara kriptografis.

Tanda tangan membuktikan *asal* image, bukan bahwa image *lolos gerbang*.

**Rekomendasi:**
1. Tambahkan job `gate` di `build-push.yml` (untuk `push`/`workflow_dispatch`/tag) yang menunggu dan memeriksa check-run commit `github.sha` lewat API (`checks: read`), lalu jadikan dependency (`needs`) bagi langkah push/sign. Wajib `success`:
   - `CI gate`;
   - `Secret scan (gitleaks)`, `SAST (semgrep)`, `Trivy filesystem`, `IaC (checkov)`.

   Alternatif: ubah pemicu `build-push` menjadi `workflow_run` atas CI + Security. Pemicu ini lebih rumit untuk dua workflow.
2. Lakukan pemeriksaan yang sama di `deploy.yml` job `resolve` untuk `GIT_SHA`/tag rilis, sebagai pertahanan berlapis untuk `workflow_dispatch`.
3. Perbaiki teks `security.yml:1` dan runbook `:14-16` menjadi "memblokir rilis (build/sign/deploy)", bukan "merge".
4. Opsional: hook `pre-push` lokal yang menolak push ke `main`. Jangka panjang: org + Team/ruleset.

### SEC-81: Low: Izin PR build terlalu lebar
**Lokasi:**
- `build-push.yml:18-26` menambah pemicu `pull_request`.
- Izin job `:46-51` (`packages: write`, `id-token: write`, `attestations: write`, `security-events: write`) berlaku juga untuk run PR.
- Langkah login/push/sign memang dibatasi `if: github.event_name != 'pull_request'` (`:76,150,163,180,183,199`), sehingga **tidak ada** push/sign di PR.

**Mengapa tetap temuan:**
- Pada run PR dari branch repo yang sama, token `packages: write` dan OIDC tersedia bagi semua langkah, termasuk action pihak ketiga (buildx, metadata, trivy-action).
- Isi PR juga dieksekusi di job tersebut lewat `docker build` dengan Dockerfile milik PR.
- Kolaborator dengan hak push branch (belum tentu hak merge) dapat memperoleh token OIDC `repo:…:pull_request` atau menulis ke GHCR. Contohnya menimpa tag `dev`/`sha-*` yang dipakai dev compose (`IMAGE_*:-ghcr.io/efn/*:dev`).
- Deploy tetap aman karena identitas cosign terkunci.

**Rekomendasi:**
- Pisahkan menjadi job `scan-pr` (hanya `contents: read`, build + Trivy) dengan `if: github.event_name == 'pull_request'`.
- Job `build` yang ada hanya untuk non-PR.
- Izin per job tidak bisa dibuat kondisional, jadi pemisahan job adalah caranya.

### SEC-82: Low (PLAUSIBLE): `workflow_run` deploy tidak memfilter sumber event
**Lokasi:**
- `deploy.yml:8-11` memakai `workflows: ["Build & Push Images"]` + `branches: [main]`.
- `resolve` `:37-39` hanya memeriksa `conclusion == 'success'` dan variable.
- Sejak `f04c816`, workflow sumber juga berjalan untuk `pull_request`. Filter `branches` pada `workflow_run` mencocokkan `head_branch` run sumber. Untuk PR dari **fork**, nilai itu adalah nama branch di fork, sehingga fork ber-branch `main` lolos filter. Pola ini sudah dikenal.

**Skenario:**
1. Fork (bila forking repo privat diizinkan dan workflow fork PR diaktifkan) membuat PR dari branch `main`. Build PR sukses, lalu deploy staging terpicu dengan secret Environment staging.
2. Tag dihitung dari `head_sha` fork (`:53,59`). Image tidak ada atau tidak bertanda tangan main, sehingga `cosign verify` gagal. **Tidak ada kebocoran secret**, karena checkout = kode default branch dan langkah sebelum SSH hanya verifikasi.
3. Varian yang lebih halus: penyerang memilih commit fork yang 7 heksa awalnya sama dengan build `main` lama (brute force ±2^28 hash). Akibatnya staging dideploy ulang dengan image `main` lama yang bertanda tangan sah (downgrade staging). Anti-replay `seq` di host tidak menolak, karena `run_number` baru lebih besar.

PLAUSIBLE: bergantung pada pengaturan fork repo privat.

**Rekomendasi:** tambahkan kondisi berikut di `resolve` (staging):
```yaml
github.event.workflow_run.event == 'push' &&
github.event.workflow_run.head_repository.full_name == github.repository
```

### SEC-83: Low (PLAUSIBLE): Image yang di-scan bukan image yang di-push
**Lokasi:** `build-push.yml:97-106` membangun `local/<svc>:scan` (load). Trivy memindai image itu (`:108-120`). Langkah Push `:148-160` lalu **membangun ulang** dari `cache-from: type=gha` dan mendorong hasilnya. Hasil push inilah yang ditandatangani dan di-attest.

**Skenario:**
- Cache GHA dibatasi 10 GB per repo dengan eviksi otomatis (runbook `:47-48`), sementara ada 8 image termasuk Odoo dengan `mode=max`.
- Bila sebagian layer ter-evict atau ekspor cache gagal di antara dua langkah, langkah `RUN apk/apt/pip install` dijalankan ulang dan dapat menarik paket berbeda.
- Image yang ditandatangani lalu dideploy tidak pernah dipindai, sehingga gate Trivy terlewati tanpa tanda apa pun.

**Rekomendasi:** push satu kali, lalu jalankan Trivy pada `REGISTRY/...@digest` hasil push sebelum `cosign sign`. Image yang gagal gate tidak ditandatangani, dan deploy menolak image tanpa tanda tangan. Alternatif: build sekali dengan `outputs: type=docker` + `push` di build yang sama, lalu verifikasi kesamaan digest/`docker inspect --format '{{.Id}}'`.

### SEC-84: Low (PLAUSIBLE): SBOM private image ke log transparansi publik
**Lokasi:** `build-push.yml:182-192` menjalankan `cosign attest --type spdxjson --predicate sbom.spdx.json` secara keyless, dengan upload tlog default ke Rekor publik. Komentar `62b1bf1` (`:165-166`) sendiri menyebut SBOM 58 MB "ditolak Rekor publik". Artinya seluruh isi attestation ikut dikirim ke Rekor.

Runbook `:31-32` menyatakan "isi image dan SBOM tidak dipublikasikan". **PLAUSIBLE:** apakah Rekor menyimpan dan menyajikan payload in-toto atau hanya hash-nya bergantung pada tipe entri dan versi cosign/Rekor. Hal ini tidak dapat dipastikan dari kode.

**Dampak bila benar:**
- daftar paket + versi kedelapan image privat (peta kerentanan untuk penyerang) terbuka permanen dan tidak bisa dihapus;
- nama repo `bctech-adm/efn-platform` juga terbuka.

Tidak ada PII atau secret di dalamnya.

**Rekomendasi:**
- Verifikasi sekali: `rekor-cli get --log-index <idx> --format json`, lalu cek apakah ada field `Attestation`/payload.
- Bila payload terbuka, pilih salah satu: simpan SBOM sebagai OCI referrer (`oras attach`/`cosign attach sbom`) lalu tandatangani **digest**-nya (`cosign sign <sbom@digest>`), sehingga Rekor hanya berisi digest; atau `--tlog-upload=false` dengan kunci non-keyless (keputusan kustodi, ADR).
- Perbaiki klaim runbook.

### SEC-85: Low: Pengecualian Metabase berbasis fakta keliru; least privilege container
**Lokasi:**
- `.trivyignore.yaml:90-93` (DS-0002, disetujui pemilik proyek, `:5-6`) menyatakan "Di compose: cap_drop ALL, no-new-privileges".
- Faktanya service `metabase` (`docker-compose.yml:1264-1311`) hanya mewarisi `no-new-privileges` dari `x-common` (`:38-42`). **Tidak ada `cap_drop`**, dan komentar `:1263` menyatakan hal itu secara eksplisit. Yang ber-`cap_drop: [ALL]` hanya sidecar `metabase-tls` (via `x-ghostunnel`, `:50`).
- Proses awal container berjalan sebagai root (`run_metabase.sh`) dengan capability default Docker (antara lain `NET_RAW`, `MKNOD`, `SETFCAP`, `DAC_OVERRIDE`).
- `:1287` juga me-mount `clickhouse_password_metabase` ke `metabase`. Sejak G13, koneksi ClickHouse dibuat oleh `metabase-provision` lewat API (`provision.py:395-410`), dan tidak ada env/skrip `metabase` yang membaca file itu. Mount ini melanggar least privilege, karena RCE di Metabase langsung mendapat password dalam bentuk file.

**Rekomendasi:**
- `cap_drop: [ALL]` + `cap_add: [CHOWN, SETUID, SETGID, DAC_OVERRIDE, FOWNER]`. Set minimum ini PLAUSIBLE dan perlu diuji di dev bahwa `su metabase` dan chown `/metabase-data` tetap jalan.
- Hapus `clickhouse_password_metabase` dari `secrets` service `metabase`.
- Perbaiki `statement` DS-0002, lalu minta persetujuan ulang. Persetujuan sebelumnya diberikan atas fakta yang salah.

### SEC-86: Info: Cek entrypoint Metabase dapat dilewati
- `check-encryption.sh:123` menerima `entrypoint` kosong (`length == 0`). Padahal compose spec mengartikan `entrypoint: []` atau `''` sebagai **menghapus** ENTRYPOINT image. Ditambah `command: ["/app/run_metabase.sh"]`, Metabase berjalan tanpa wrapper dan tanpa kunci.
- DB baru akan tersimpan plaintext. DB yang sudah terenkripsi gagal membaca detail koneksi, jadi tetap fail-closed.
- Mengganti `IMAGE_METABASE` dengan image resmi juga melewati wrapper. Di production nilai ini berasal dari manifest bertanda tangan, sehingga hanya relevan untuk dev/override lokal.

**Rekomendasi:**
- Ganti cek menjadi `.services.metabase | has("entrypoint") | not`, dan tolak `command` yang memuat `run_metabase.sh`.
- Di smoke/deploy, cek `docker inspect --format '{{json .Config.Entrypoint}}'` = `["/usr/local/bin/efn-metabase-entrypoint"]`, dan log start memuat "correctly configured" (README `:51`).

### SEC-87: Info: Kunci di argv saat rotasi
- `README.md:48-56` meneruskan kunci baru sebagai argumen `rotate-encryption-key "$(cat …)"`. README menyatakan kunci itu "hanya terlihat di argv proses java di dalam container one-off (PID namespace sendiri)".
- Klaim ini keliru. Dari **host**, `/proc/<pid>/cmdline` proses container terbaca oleh semua user host (`ps -ef`) kecuali `/proc` di-mount `hidepid=2`.
- Rotasi bersifat jarang dan berdurasi pendek, dan host prod hanya untuk admin. Risikonya rendah, tetapi dokumentasi harus jujur.

**Rekomendasi:**
- Jalankan rotasi di host dengan `hidepid=2` atau saat tidak ada user lain.
- Perbaiki teks README.
- Catat di `secret-rotation.md`.

### SEC-88: Info: Kebersihan CI
- `ci.yml` job `changes`, `node`, `python`, `pytest`, `odoo-*` memanggil `actions/checkout` tanpa `persist-credentials: false` (`:33,62,89,136,177,223`). Job `node` lalu menjalankan `npm ci`, sehingga script lifecycle dependency (yang churn-nya naik karena Dependabot) dapat membaca token `contents: read` di `.git/config`, yaitu akses baca kode privat. Mitigasi `cooldown` 7 hari di `dependabot.yml` sudah baik. Workflow lain sudah memakai `false`.
- Tool CI tidak dipin: `pip-audit` (`:96`) dan `pylint-odoo` (`:181`, juga `|| true`, jadi bukan gate).
- `security.yml:176,193` menamai job "checkov: compose". Namun framework `yaml` checkov hanya menjalankan kebijakan kustom, dan checkov maupun Trivy tidak punya kebijakan bawaan untuk Docker Compose. Hardening compose sebenarnya hanya ditegakkan oleh skrip kustom (`check-encryption.sh`, cek port publish, cek token Traefik). Ganti label agar tidak memberi rasa aman palsu.
- Komentar `ci.yml:64-66` ("package-lock.json belum ada") sudah usang, karena lockfile kini ada. Aktifkan `cache: npm` dan pastikan `npm ci` (bukan `npm install`) adalah satu-satunya jalur.

---

## Verifikasi area fokus (kontrol yang sudah benar)

**1. Gate CI tidak melemah**
- Hasil scanner:
  - semgrep `--error` dengan exit dari container (`security.yml:72-76`);
  - Trivy fs `exit-code: "1"` (`:120`) dan image `exit-code: "1"` sebelum push (`build-push.yml:117`);
  - checkov `set -o pipefail` + `docker run … | tee`, tanpa `--soft-fail` (`security.yml:191-195`);
  - gitleaks-action default fail, dengan upload artifact dimatikan hanya untuk artifact;
  - dependency-review `fail-on-severity: high`;
  - `npm audit --audit-level=high` dan `pip-audit --strict` tetap exit 1.
- Langkah SARIF kedua memakai `exit-code: "0"` hanya untuk output dan hanya bila `CODE_SCANNING == 'true'`.
- Tidak ada `pull_request_target` di workflow mana pun.
- Izin default `contents: read` (`ci.yml` `{}`), dan setiap job meminta izinnya sendiri.
- Ringkasan semgrep tidak mencetak potongan kode, sehingga hasil `p/secrets` tidak bocor ke summary.
- Checkov kini memakai image ber-digest, bukan action yang SHA-nya hilang. Ini perbaikan supply chain.
- Catatan sisa ada di SEC-80/81.

**2. `.trivyignore.yaml`**
- Seluruh 17 entri vulnerability dan 1 misconfig punya `statement`.
- `expired_at` ≤ 90 hari dari 9 Okt 2026: 2026-11-09 untuk 16 entri, dan 2027-01-07 untuk dua entri (= +90 hari).
- Entri dibatasi `paths` ke `usr/local/bin/wal-g` / `app/metabase.jar`, bukan global.
- **Tidak ada CVE dependency npm/pip aplikasi.** Yang di-ignore hanya binary upstream WAL-G dan jar Metabase, ditambah satu false positive pseudo-version.
- `secrets: []`.
- Dua masalah: alasan DS-0002 tidak akurat (SEC-85), dan perlu verifikasi apakah input `trivyignores` trivy-action 0.35.0 membaca format YAML apa adanya. Bila action menggabungkannya ke file teks, pengecualian **tidak berlaku** dan gate gagal, jadi kegagalannya fail-closed, bukan melemah. Cek baris "ignored" di log run.

**3. Metabase G16b**
- `efn-entrypoint.sh` fail-closed: `set -eu`, menolak `MB_ENCRYPTION_SECRET_KEY` dari env, file wajib ada dan terbaca, menolak whitespace/CR, dan panjang minimal 32 karakter.
- Nilai kunci tidak pernah di-echo. Wrapper memakai `exec` ke `run_metabase.sh` resmi, dan perilaku `su metabase` (uid 2000) tetap.
- Kunci hanya di-mount ke `metabase`, ber-kustodi Superadmin (`secrets.list:65`, `key-custody.md:16`). `gen-secrets` menghasilkan 44 karakter base64 tanpa newline. Deploy mengirimnya lewat stdin base64, bukan argumen.
- CI `check-encryption.sh:118-125` dijalankan untuk compose dev dan prod.
- DB aplikasi: `sslmode=verify-full` + CA internal (`compose:1283`, dicek `check-encryption.sh:43-44`).
- ClickHouse: `ssl: true`, `sslmode=strict`, `sslrootcert` CA internal, `enable-multiple-db: false` (`provision.py:264-274`). Runtime dev melaporkan bahwa CA salah membuat koneksi gagal.
- Klien provision memakai TLS ≥ 1.2, `CERT_REQUIRED`, mTLS ke sidecar, dan error tanpa payload permintaan.
- `metabase-provision` berjalan read-only, `cap_drop ALL`, uid 65534, `python -I`.
- Dump pra-upgrade terenkripsi `age`, dan dump plaintext dev sudah dihapus. **SEC-64 Fixed.**

**4. Regresi upgrade**
- **Next 16 `proxy.ts`:**
  - header `x-efn-brand` selalu ditimpa dari host;
  - override `?brand=` hanya bila `NODE_ENV !== 'production'`, dan runtime image men-set `NODE_ENV=production` (`Dockerfile:41`);
  - matcher mencakup semua path selain aset statis;
  - tidak ada sisa `middleware.ts` yang bisa bentrok.
- **Header keamanan/CSP** berasal dari `next.config.mjs` `headers()`, bukan proxy, sehingga tidak terpengaruh perubahan runtime Node: CSP tanpa sumber eksternal, `frame-ancestors 'none'`, HSTS di production, `poweredByHeader: false`.
- **Fastify 5:**
  - `trustProxy` tetap array CIDR tervalidasi (proxy-addr), dan `client-ip.spec.ts` mengujinya dengan instance Fastify asli;
  - `rawBody: true` tetap ada, dan verifikasi HMAC fail-closed bila `rawBody` kosong (`webhook-signature.ts:67`, `xendit.gateway.ts:115`);
  - helmet 13 terdaftar global (CSP off untuk API JSON);
  - batas multipart tidak berubah;
  - tidak ada pemakaian `request.hostname` yang berubah semantik di Fastify 5.
- **altcha v3:** widget-only. Verifikasi tetap di BFF (`altcha.service.ts`: HMAC terikat IP, salt berjangkar, expires, sekali pakai Redis), sehingga perubahan widget tidak memengaruhi keamanan server. `humanInteractionSignature: false` (minimisasi data), widget di-bundle sendiri (tanpa CDN), dan `worker-src blob:` sudah ada.
- **Pillow 12.3.0:** `MAX_IMAGE_PIXELS` 40 MP dan penanganan `DecompressionBombError` tetap ada (`imaging.py:11-19`).
- **Base image runtime Node:** npm, corepack, dan yarn dihapus. User non-root `app` dipertahankan, dan stage dev/build kini memakai user `node`.

**5. `deploy.yml`**
- Login GHCR memakai `GITHUB_TOKEN`, dan job `deploy` hanya `contents: read`, `packages: read`, `id-token: write` (yang terakhir untuk `sign-blob` bundel).
- `cosign verify` memakai `--certificate-identity` persis `…/build-push.yml@${SIGNER_REF}` dan issuer persis. `SIGNER_REF` hanya `refs/heads/main` atau `refs/tags/vX.Y.Z` hasil regex, dan production menolak selain tag.
- Digest tunggal divalidasi regex.
- Gate `STAGING_DEPLOY_ENABLED` hanya mempersempit pemicu. Sisa celah ada di SEC-80 dan SEC-82.
- Dependabot memakai `cooldown` 7/14 hari dan dikelompokkan, sehingga mitigasi rilis berbahaya dari supply chain lebih baik.

## Yang belum terverifikasi (butuh runtime)
- Apakah trivy-action 0.35.0 `trivyignores` membaca `.trivyignore.yaml` sebagai YAML (log run).
- Isi entri Rekor attestation SBOM (SEC-84).
- Set capability minimum Metabase (SEC-85).
- Representasi `entrypoint: []` di `docker compose config --format json` (SEC-86).
- Pengaturan fork repo privat (SEC-82).

## Prioritas
1. **Sebelum `STAGING_DEPLOY_ENABLED=true` atau tag `v*` pertama:** SEC-80 dan SEC-82.
2. **Gelombang berikut:** SEC-81, SEC-83, SEC-85, lalu verifikasi SEC-84.
3. **Backlog:** SEC-86, SEC-87, SEC-88.

---

## Status perbaikan G16c (efn-devsecops-engineer, 9 Okt 2026)

Ditambahkan oleh implementer, bukan reviewer. Review ulang oleh `efn-security-reviewer` tetap diperlukan.

| ID | Status | Perubahan | Bukti |
|---|---|---|---|
| SEC-80 | Fixed | `scripts/verify-commit-gates.sh` (Actions API, `actions: read`; run dipilih per FILE workflow + `head_sha` + event push, bukan nama check-run yang tidak unik). `build-push.yml` job `gate` (`--preset release`: `ci.yml` job `CI gate`; `security.yml` job gitleaks, semgrep, Trivy fs, checkov; timeout 60 mnt, run absen 10 mnt) = `needs` job `build`. `deploy.yml` `resolve` mengulang cek + `build-push.yml` success untuk SHA penuh (tag `v*`: menunggu ≤ 90 mnt; dispatch/workflow_run: 5 mnt). Komentar `security.yml:1-6`, runbook `ci-github-free-plan.md` §1 dan §9.1 dikoreksi ("memblokir rilis", bukan merge). Tambahan: `ci.yml` `CI gate` kini `needs: changes` (sebelumnya paths-filter gagal → semua job skipped → gate lolos). | Skrip dijalankan terhadap API nyata (read-only, `gh`): `ef346a0` (semua hijau) exit 0; `7c33351` (CI `failure`) exit 1; SHA tanpa run exit 1; nama job checkov baru vs run lama → `absent` exit 1. actionlint 1.7.7 bersih. Nama check-run diambil dari `gh api …/commits/ef346a0…/check-runs`. Dipilih polling, bukan `workflow_run` (identitas cosign tag rilis hilang di konteks default branch; dua workflow sumber). |
| SEC-81 | Fixed | Job `scan-pr` (`if: pull_request`, `permissions: contents: read`, cache GHA hanya dibaca) terpisah dari job `build` (`if: != pull_request`, izin tulis). | actionlint bersih; checkov `github_actions` 550 passed / 0 failed. |
| SEC-82 | Fixed | `resolve.if`: `workflow_run.event == 'push'`, `head_branch == 'main'`, `head_repository.full_name == github.repository`. SHA penuh: event (workflow_run/push) atau resolusi API untuk dispatch (prefix ambigu → gagal). `cosign verify` + `--certificate-github-workflow-sha "$GIT_SHA"` + `--certificate-github-workflow-repository`. Manifest `git_sha` kini SHA yang dideploy (sebelumnya `github.sha` = HEAD default branch saat dispatch). | Flag ada di cosign v2.6.1 (`verify --help`). Sertifikat Fulcio entri Rekor 3155542984 memuat OID `1.3.6.1.4.1.57264.1.3` = `62b1bf13…` dan `.1.5` = `bctech-adm/efn-platform`. Belum dijalankan end-to-end (staging belum aktif). |
| SEC-83 | Fixed | Job `build`: satu `build-push-action` (push, `cache-to`) → Trivy `image-ref: <repo>@<digest>` (`TRIVY_IMAGE_SRC=remote`, kredensial docker login) → Syft → `cosign sign/attest` atas digest yang sama. Image gagal scan tidak bertanda tangan → ditolak `cosign verify`. | actionlint bersih. Belum ada run GitHub nyata. |
| SEC-84 | Fixed (dok) | Diverifikasi: entri attest = jenis `dsse` (hanya `envelopeHash`, `payloadHash`, signature, sertifikat; `attestation: null`); entri sign = `hashedrekord`. SBOM **tidak** publik. Yang publik: nama repo, workflow + ref, SHA commit, waktu, hash. Runbook §2 + komentar `build-push.yml` dikoreksi. Perilaku tidak diubah. | `GET https://rekor.sigstore.dev/api/v1/log/entries?logIndex=3155542984` dan `…3155540831` (image metabase `sha256:4d64a938…`, run 62b1bf1, job log "tlog entry created with index"). |
| SEC-85 | Fixed | `metabase`: `cap_drop: [ALL]`, `cap_add: [CHOWN, SETUID, SETGID]`, `no-new-privileges` (x-common) tetap; `clickhouse_password_metabase` dilepas (dipakai hanya `metabase-provision`). Statement DS-0002 `.trivyignore.yaml` diperbaiki; perlu persetujuan ulang pemilik proyek. | Stack dev: recreate `metabase metabase-tls` → keduanya healthy; `CapBnd=0xc1`; PID 1 java uid `metabase`; log "MB_ENCRYPTION_SECRET_KEY correctly configured"; `metabase-provision` exit 0 (kartu `completed`, ClickHouse lewat koneksi tersimpan terenkripsi); restart kedua juga diuji. Efek samping: log `adduser: /home/metabase: Operation not permitted` (chmod g+s home tanpa FOWNER; home tidak dipakai). |
| SEC-86 | Fixed | `check-encryption.sh`: `entrypoint == null` (compose config SELALU memuat key `entrypoint`; `[]` dan `''` ter-render `[]`, `null` = default image) + tolak image `metabase/metabase`. `deploy.sh` dan smoke e2e memeriksa `docker inspect .Config.Entrypoint` container berjalan. | Representasi diuji (`docker compose config --format json`): `[]`→`[]`, `''`→`[]`, `null`/absen→`null`. check-encryption di `docker:27-cli`+jq: asli OK; override `entrypoint: []` → FAIL; `image: metabase/metabase` → FAIL. |
| SEC-87 | Partial | README Metabase + `secret-rotation.md`: argumen terlihat `ps` host selama rotasi (±30–60 dtk); mitigasi: tanpa sesi user lain atau `/proc` `hidepid=2`; kunci baru dibaca dari file di dalam container one-off (tidak di shell/`docker inspect`/log). | Perintah resmi hanya menerima kunci baru sebagai argumen. Java `@argfile` diuji di image ini: `/proc/self/cmdline` tetap memuat argumen terekspansi → tidak menolong. Prosedur rotasi belum diuji end-to-end (rotasi kunci dev butuh persetujuan). |
| SEC-88 | Fixed | `persist-credentials: false` di semua checkout `ci.yml` kecuali job `changes` (paths-filter `git fetch` pada push; job tanpa npm/pip, dikomentari). `pip-audit==2.10.1`, `pylint-odoo==10.0.11` (10.0.12 baru 4 hari, di bawah cooldown 7 hari) + label "informatif, bukan gate". Checkov → "IaC (checkov: Dockerfile, GitHub Actions)" + komentar. `cache: npm` + `npm ci` saja (lockfile ada). | actionlint, yamllint (0 error), checkov exit 0. |
| `trivyignores` | Terverifikasi | trivy-action 0.35.0 membaca YAML apa adanya. | Log run Build & Push `62b1bf1` job metabase & postgres: "Using YAML ignorefile '.trivyignore.yaml'" + "Some vulnerabilities have been ignored/suppressed"; tabel `app/metabase.jar` / `usr/local/bin/wal-g` = 0. |

Tambahan di luar temuan: `DOCKER_BUILD_RECORD_UPLOAD=false` di build-push (log run 62b1bf1: "Failed to CreateArtifact: Artifact storage quota has been hit" dari build record buildx).

---

## Re-review G16c (efn-security-reviewer, 9 Okt 2026, commit `4996aa8`)

Read-only. File: `scripts/verify-commit-gates.sh`, `.github/workflows/{build-push,deploy,ci,security}.yml`,
`.trivyignore.yaml` (DS-0002), `infra/compose/docker-compose{,.prod,.dev}.yml` (metabase), `scripts/check-encryption.sh`,
`scripts/deploy.sh:386-414`, `scripts/host/efn-deploy:110-172,275-292`, `docs/runbooks/ci-github-free-plan.md:1-37`.
Tidak ada run GitHub yang dijalankan reviewer; perilaku API GitHub yang tidak bisa dipastikan dari kode ditandai PLAUSIBLE.

### Ringkasan eksekutif
- Mekanisme gerbang rilis yang baru **benar dan gagal-tertutup**: status `neutral`/`skipped`/`cancelled`/`absent`/`duplicate`
  semuanya ditolak, tidak ada injeksi dari input/ref ke shell, dan tanda tangan hanya terjadi atas digest yang lolos Trivy.
- Celah utama tersisa (**SEC-89, Medium**): `CI gate` untuk sebuah commit hanya menguji file yang berubah di *push itu*.
  Commit yang test-nya merah atau dibatalkan bisa ikut dirilis lewat commit berikutnya yang hanya mengubah dokumen.
  Scanner Security (semgrep, Trivy fs, checkov) memindai seluruh repo sehingga tidak terdampak.
- Sisa lain (Low/Info): tag image mutable dipasang sebelum scan, host belum mengikat image ke SHA commit, tag `v*` dari
  commit yang belum masuk `main` dapat membawa workflow yang gate-nya dihapus, dan bentrok `concurrency` Security.

### Status per SEC (terverifikasi)

| ID | Status re-review | Catatan verifikasi |
|---|---|---|
| SEC-80 | **Partial** | Benar: run dipilih per file workflow + `head_sha` + `event=push` (+ `branch=main` untuk CI/Security), `sort_by(created_at) \| last`, `jobs?filter=latest` (attempt terbaru), job wajib harus tepat satu dan `success` (`verify-commit-gates.sh:62-91`). `neutral`/`skipped`/`cancelled` → gagal; run belum dibuat → tunggu ≤ `--absent-min` lalu gagal; API error → coba lagi sampai timeout (gagal-tertutup). Injeksi: `--sha` regex 40 hex, file workflow regex, nama job lewat `jq --arg`, input dispatch lewat env + regex. `CI gate` kini `needs: changes`. `build` `needs: gate`. **Sisa:** cakupan CI per push (SEC-89) dan gate yang hidup di commit yang sama (SEC-92). |
| SEC-81 | **Fixed** | `scan-pr` hanya `contents: read`, tanpa login/OIDC, `cache-from` saja; `build`/`gate` `if: != pull_request`. Catatan kecil: `build` tetap meminta `security-events: write` dan `attestations: write` walau keduanya kondisional (SEC-94). |
| SEC-82 | **Fixed** (sisi CI) | `resolve.if` memfilter `event == push`, `head_branch == main`, `head_repository == github.repository`. `cosign verify` mengikat `--certificate-github-workflow-sha` dan `-repository`. Untuk tag: `github.sha` = commit tag dan resolusi API `commits/vX.Y.Z` mengembalikan commit, jadi konsisten dengan ekstensi Fulcio. Host belum mengikat SHA: SEC-91. |
| SEC-83 | **Fixed** | Satu `build-push-action` → `Digest valid` → Trivy `@digest` (remote) → Syft → `cosign sign/attest` `@digest`. Langkah SBOM, sign, dan attest-provenance tidak memakai `always()` (default `success()` berlaku), sehingga tidak berjalan setelah Trivy gagal. Digest = index (provenance + sbom) dan konsisten dengan yang di-resolve deploy dari tag. Sisa: tag mutable dipasang sebelum scan (SEC-90). |
| SEC-84 | **Fixed** (dok) | Bukti entri Rekor `dsse`/`hashedrekord` dari implementer diterima; tidak diulang reviewer. |
| SEC-85 | **Fixed** (kode) | `cap_drop: [ALL]`, `cap_add: [CHOWN, SETUID, SETGID]` (`docker-compose.yml:1294-1295`), secret ClickHouse dilepas (`:1293`), overlay prod/dev tidak menimpa. `CapBnd=0xc1` konsisten dengan tiga capability. Statement DS-0002 kini akurat (`.trivyignore.yaml:87-97`, `expired_at` 2027-01-07). **Masih menunggu persetujuan ulang pemilik proyek.** Saran: tambah cek `cap_drop` metabase di `check-encryption.sh` atau job `infra`, karena pengecualian DS-0002 kini bergantung padanya. |
| SEC-86 | **Fixed** | `check-encryption.sh:126` `entrypoint == null` + tolak image resmi (`:129`); `deploy.sh:411-413` dan smoke memeriksa `.Config.Entrypoint` container berjalan. Catatan: cek `deploy.sh` terjadi setelah `metabase-provision` (post-hoc); pencegah utamanya tetap cek compose di CI + image bertanda tangan. |
| SEC-87 | **Partial** | Diterima sebagai risiko residual terdokumentasi. |
| SEC-88 | **Fixed** | Semua checkout `ci.yml` `persist-credentials: false` kecuali `changes` (hanya paths-filter, tidak menjalankan kode repo). Label checkov akurat. |

### Temuan baru

| ID | Severity | Judul | Lokasi | Risiko | Status (G16c-2) |
|---|---|---|---|---|---|
| SEC-89 | **Medium** | `CI gate` per commit hanya menguji delta push → commit merah/dibatalkan dirilis lewat commit berikutnya | `ci.yml:14-16,33-47,563-572`, `verify-commit-gates.sh:26`, runbook `:31-32` | R-S07 | **Fixed** (kode; run nyata belum) |
| SEC-90 | Low | Tag mutable (`main`, `latest`, `X.Y`, `X.Y.Z`, `sha-*`) dipasang sebelum Trivy; run tag menimpa `sha-<short>` | `build-push.yml:186-191,197-209` | R-S08 | **Fixed** |
| SEC-91 | Low | Host `efn-deploy` memverifikasi image tanpa `--certificate-github-workflow-sha` = `git_sha` manifest | `scripts/host/efn-deploy:280-289` | R-S08 | **Fixed** |
| SEC-92 | Low (PLAUSIBLE) | Tag `v*` dari commit di luar `main` dapat membawa `build-push.yml`/`deploy.yml` tanpa gate; host menerima `deploy.yml@refs/tags/v*` dari commit mana pun | `efn-deploy:9,117-121`, `deploy.yml:12-13`, `build-push.yml:26-29` | R-S07, R-S08 | **Fixed** (host; pasang ulang efn-deploy + file persetujuan oleh Superadmin) |
| SEC-93 | Info | `concurrency` Security sama untuk `push`/`schedule`/`workflow_dispatch` di `main` → run push bisa dibatalkan scan mingguan (dan sebaliknya) | `security.yml:18-33` | R-S07 (ketersediaan) | **Fixed** |
| SEC-94 | Info | Deploy tag memakai `build-push.yml@*` (bisa memilih run `main` saat run tag belum dibuat); izin `build` tidak kondisional | `deploy.yml:119`, `build-push.yml:144-149` | R-S08 | **Fixed** |

#### SEC-89: Medium: `CI gate` hanya mencerminkan delta push
- `dorny/paths-filter` di job `changes` (`ci.yml:36-47`) tidak memakai `base`. Pada event `push`, pembandingnya adalah
  `github.event.before`, sehingga hanya file yang berubah di push itu yang memicu job. Job yang tidak terpicu menjadi
  `skipped`, dan `CI gate` menganggap `skipped` lolos (`ci.yml:572`).
- `concurrency: ci-${{ github.ref }}` + `cancel-in-progress` (`:14-16`) membatalkan CI commit sebelumnya saat ada push baru.
  Runbook (`:31-32`) bahkan menyarankan "tag commit terbaru saja" untuk kasus ini.
- **Skenario:** push A mengubah `services/odoo/**` dan test record rule multi-company gagal (atau CI A dibatalkan karena
  push B masuk 2 menit kemudian). Push B hanya mengubah `docs/`. CI B: semua job `skipped` → `CI gate` = success.
  Security B hijau, karena scanner memindai seluruh repo. Gate `build-push` B lolos, lalu image berisi kode A yang belum
  pernah lolos test (`npm audit`/`pip-audit`, test OTP/HMAC/record rule) ditandatangani dan dideploy ke staging. Hal ini
  terjadi tanpa niat jahat, cukup dengan alur kerja normal.
- Hal yang sama (PLAUSIBLE) berlaku untuk gitleaks-action pada `push`, yang memindai rentang commit push, bukan seluruh
  riwayat. Secret di commit A yang run Security-nya dibatalkan tidak dipindai gitleaks pada run B. Trivy fs `secret` hanya
  menangkapnya bila secret masih ada di tree.
- **Rekomendasi:**
  1. Pada `push` ke `main`, jalankan semua job CI tanpa filter. Contohnya, `changes` mengeluarkan `true` untuk semua
     filter bila `github.event_name == 'push'`. Biaya menit naik, tetapi menit per push ke `main` relatif kecil
     dibanding PR.
  2. Alternatif yang lebih hemat: set `base` paths-filter ke SHA terakhir yang `Build & Push`-nya success di `main`.
     SHA itu dicari lewat Actions API di job `changes`, dan kalau tidak ditemukan, semua filter diisi `true`.
  3. Gitleaks pada `push`: jalankan `gitleaks git` dengan rentang dari rilis terakhir, atau scan penuh di job terjadwal
     yang juga diwajibkan preset.

#### SEC-90: Low: Tag mutable sebelum scan; tag rilis menimpa `sha-*`
- `build-push.yml:203-204` mendorong semua tag `metadata-action` **sebelum** Trivy. Akibatnya, image yang gagal scan
  tetap dipasang sebagai `main`/`latest`/`X.Y`/`X.Y.Z`/`sha-<short>`, tanpa tanda tangan. Deploy aman karena
  `cosign verify` menolak image tanpa tanda tangan, tetapi konsumen yang menarik image berdasarkan tag tanpa verifikasi
  (dev, uji manual, Metabase/ops ad hoc) mendapat image yang gagal gate.
- `type=sha` (`:187`) juga aktif pada run tag. Run `vX.Y.Z` menimpa `sha-<short>` commit yang sama dengan digest baru
  (build tidak reproducible), yang ditandatangani identitas `refs/tags/vX.Y.Z`. Deploy ulang atau rollback staging dengan
  `sha-<short>` itu (identitas `refs/heads/main`) kemudian gagal `cosign verify`. Kegagalannya tertutup, tetapi rollback
  macet. Hal yang sama terjadi bila `workflow_dispatch` dijalankan dari branch lain yang HEAD-nya commit `main`.
- **Rekomendasi:** push by digest saja (`outputs: type=image,push-by-digest=true,name-canonical=true,push=true`), lalu
  pasang tag dengan `docker buildx imagetools create -t … <repo>@<digest>` setelah `cosign sign` berhasil. Selain itu,
  batasi `type=sha` ke `enable=${{ github.ref == 'refs/heads/main' }}`.

#### SEC-91: Low: Host tidak mengikat image ke SHA commit manifest
- `deploy.yml` (CI) memakai `--certificate-github-workflow-sha "$GIT_SHA"`, tetapi `efn-deploy:286-289` (trust anchor
  terakhir) hanya memeriksa identitas `build-push.yml@${M_REF}` + issuer.
- **Skenario:** job `deploy` yang disusupi (action pihak ketiga, runner) menandatangani manifest yang berisi digest
  image `main` lama yang sah, misalnya build sebelum gate SEC-80 ada atau commit yang CI-nya merah. Host menerimanya
  karena identitasnya cocok, dan `M_GIT` di manifest tidak pernah dicocokkan dengan sertifikat image.
- **Rekomendasi:** tambahkan `--certificate-github-workflow-sha "$M_GIT"` dan
  `--certificate-github-workflow-repository "$EFN_GITHUB_REPO"` di `efn-deploy`. Ini murah dan menyamakan host dengan CI.

#### SEC-92: Low (PLAUSIBLE): Tag `v*` dari commit di luar `main` melewati gate di dalam repo
- Pada `push` tag, GitHub menjalankan `build-push.yml` dan `deploy.yml` **dari commit tag**. Pemilik hak tulis dapat
  membuat commit di branch fitur yang menghapus job `gate`/langkah gerbang, men-tag-nya `v9.9.9`, lalu mendorong tag
  tersebut. Image ditandatangani `build-push.yml@refs/tags/v9.9.9` dengan SHA yang cocok, dan bundel ditandatangani
  `deploy.yml@refs/tags/v9.9.9`. Host menerima ini karena regexp identitas (`efn-deploy:9`) mengizinkan tag apa pun.
- Penghalang yang tersisa hanyalah approval Environment `production`. **PLAUSIBLE:** untuk repo privat, ketersediaan
  required reviewers bergantung pada paket akun, jadi verifikasi di Settings > Environments.
- Ini sisa inheren dari ketiadaan branch/tag protection (SEC-80 rekomendasi 4), tetapi gate SEC-80 tidak menutupnya.
  Tanpa mitigasi ini, gate hanya efektif terhadap kelalaian, bukan terhadap insider.
- **Rekomendasi:** persetujuan out-of-band di host production. Superadmin menulis SHA commit rilis yang disetujui ke
  `/etc/efn/approved-release` (pola sama dengan `allow-downgrade`), lalu `efn-deploy` menolak bila `M_GIT` berbeda.
  Alternatif: `efn-deploy` memanggil `verify-commit-gates.sh` sendiri dengan token baca milik host, plus cek bahwa
  `M_GIT` adalah ancestor `main` (`compare` API). Jangka panjang: ruleset tag (Team/org).

#### SEC-93: Info: Bentrok `concurrency` Security
- `group: security-${{ github.ref }}` dipakai bersama oleh `push`, `schedule` (ref = default branch), dan
  `workflow_dispatch` di `main`. Scan mingguan Senin 02:17 UTC atau dispatch manual dapat membatalkan run push yang
  sedang berjalan. Akibatnya gate rilis commit itu gagal (tertutup, tetapi rilis tertahan sampai run diulang). Sebaliknya,
  push dapat membatalkan scan mingguan tanpa tanda.
- **Rekomendasi:** `group: security-${{ github.event_name }}-${{ github.ref }}`.

#### SEC-94: Info: Pemilihan run tag dan izin `build`
- `deploy.yml:119` `--require 'build-push.yml@*'` memilih run push **terbaru** untuk SHA. Untuk tag, run `main` commit
  yang sama juga cocok. Bila `resolve` memeriksa sebelum run tag dibuat (PLAUSIBLE, jendela detik), gate lolos memakai run
  `main`, lalu `cosign verify` tag `X.Y.Z` gagal karena image belum ada. Kegagalannya tertutup, hanya masalah keandalan.
  Rekomendasi: `build-push.yml@v${tag}` untuk tag dan `@main` untuk `sha-*` (parameter `branch` API mencocokkan
  `head_branch`, yang untuk push tag berisi nama tag).
- `build` selalu meminta `security-events: write` dan `attestations: write`, padahal keduanya hanya dipakai saat
  kondisional. Dampaknya kecil karena job hanya berjalan non-PR setelah gate. Pisahkan ke job terpisah bila suatu saat
  code scanning atau attestations diaktifkan.

### Kontrol yang sudah benar (re-review)
- Tidak ada `pull_request_target`, dan job PR (`scan-pr`, CI, Security) tanpa izin tulis atau OIDC.
- `CI gate` gagal bila `changes` gagal atau dibatalkan, dan tidak lolos karena "semua skipped" akibat paths-filter error.
- Deploy `workflow_run` hanya dari run `push` ke `main` di repo ini dengan `conclusion == success`. Run
  `workflow_dispatch`/PR tidak memicu.
- Production: hanya `refs/tags/vX.Y.Z` (CI `deploy.yml:91-93` dan host `efn-deploy:165-167`), dan digest diikat ke SHA
  penuh di CI.
- Metabase: capability minimum terbukti di dev, secret ClickHouse dilepas, entrypoint diperiksa di compose dan
  container berjalan.

### Prioritas re-review
1. Sebelum `STAGING_DEPLOY_ENABLED=true` / tag `v*` pertama: **SEC-89** (tanpa ini gate CI dapat lolos dengan alur
   kerja normal) dan **SEC-91** (satu baris).
2. Sebelum rilis production pertama: **SEC-92** (keputusan Superadmin: approval out-of-band di host) dan persetujuan
   ulang DS-0002 (SEC-85).
3. Backlog: SEC-90, SEC-93, SEC-94.

### Status perbaikan re-review (G16c-2, efn-devsecops-engineer, 9 Okt 2026, di atas `4996aa8`, belum di-commit)

Ditambahkan implementer; perlu review ulang `efn-security-reviewer`.

| ID | Status | Perubahan | Bukti |
|---|---|---|---|
| SEC-89 | Fixed | `ci.yml` job `changes`: output semua `true` (pyservices = 3 service) bila `event_name == push`; paths-filter hanya untuk pull_request, tanpa checkout (API PR) → tidak ada kredensial git tersimpan di CI. `security.yml` gitleaks: push/schedule/dispatch = `gitleaks git` riwayat penuh (image `zricethezav/gitleaks:v8.30.1@sha256:c00b6bd0…`), PR = action. Gate build-push 75 menit (job 85), deploy tag 110 (job 120). Runbook §1: saran "tag commit terbaru saja" diganti aturan run hijau milik commit itu sendiri + estimasi menit. | actionlint, yamllint, checkov 0 FAILED. gitleaks riwayat penuh lokal (perintah = workflow): 67 commit, "no leaks found", ±37 dtk. Catatan: job Odoo test/tour **belum pernah selesai** di GitHub (riwayat 100 run CI: hanya skipped/cancelled) → run penuh pertama bisa merah (gagal-tertutup). |
| SEC-90 | Fixed | Push `outputs: type=image,name=<repo>,push-by-digest=true,name-canonical=true,push=true` tanpa tag → Trivy → SBOM → sign/attest → `docker buildx imagetools create -t …` atas digest, lalu digest setiap tag diverifikasi sama. `type=sha` hanya `refs/heads/main`. | Uji registry lokal (`registry:2` + builder docker-container): index provenance+sbom push-by-digest `sha256:85bdc772…`; `imagetools create -t :main -t :sha-abc` → kedua tag = digest yang sama (inspect + header `Docker-Content-Digest`). |
| SEC-91 | Fixed | `efn-deploy` `cosign verify` + `--certificate-github-workflow-sha "$M_GIT"` + `--certificate-github-workflow-repository "$EFN_GITHUB_REPO"`. | `test-efn-deploy.sh` (cosign palsu kini mewajibkan kedua flag + mencocokkan nilai): 87/87 PASS, termasuk 80 (image build commit lain) dan 81 (repo lain) ditolak. |
| SEC-92 | Fixed (host) | `efn-deploy` production mewajibkan baris `<X.Y.Z> <git_sha>` di `/etc/efn/approved-release` (root, tidak bisa ditulis group/other), dicek setelah anti-downgrade, sebelum cosign verify image. Staging tidak terpengaruh. Runbook key-custody §4d (cek `compare/<sha>...main`, gate, tag, tulis baris) + §3 dikoreksi. | Kasus 82-87 PASS (tanpa file, SHA lain, versi lain, file 0666 ditolak; disetujui diterima; staging tanpa file diterima). `gh api …/environments` (read-only): `production` hanya `branch_policy` tag `v*` (tanpa required reviewers); `staging` tanpa aturan → rekomendasi admin: staging hanya `main`. |
| SEC-93 | Fixed | `concurrency.group: security-${{ github.event_name }}-${{ github.ref }}`. | actionlint. |
| SEC-94 | Fixed | `resolve` output `build_branch` (`main` / `vX.Y.Z`) → `--require build-push.yml@<ref>`; skrip memfilter `head_branch` juga di jq + validasi nama ref; spesifikasi tanpa `@` = semua ref (bug lama: dulu dianggap nama branch). Job `build` tanpa `security-events`/`attestations: write` (langkah kondisional akan gagal terlihat bila variable diaktifkan tanpa menambah izin; dikomentari). | API nyata: `build-push.yml@main` untuk ef346a0 lolos; `build-push.yml@v1.0.0` gagal (tidak ada run). `head_branch` = nama tag untuk run push tag: PLAUSIBLE (belum ada tag di repo). |
| SEC-85 (saran) | Ditambah | `check-encryption.sh`: metabase wajib `cap_drop` ALL dan `cap_add` ⊆ {CHOWN, SETUID, SETGID}. | Uji negatif `cap_add` + SYS_ADMIN → FAIL; konfigurasi asli OK. |

Sisa: `efn-deploy` di host wajib dipasang ulang manual oleh Superadmin (sha256 dari PR) + file `approved-release` sebelum
rilis production pertama; persetujuan ulang DS-0002; semua perilaku workflow baru menunggu run GitHub nyata.
