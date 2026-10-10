# Runbook — CI GitHub di repo privat paket gratis (G16)

Berlaku untuk `bctech-adm/efn-platform` (repo **privat**, akun GitHub **gratis**). Ditulis gelombang 16 (9 Okt 2026)
setelah run pertama CI/Security/Build gagal karena batas paket, bukan karena kode. Audiens: lead, devsecops, admin repo.

## 1. Batas paket gratis yang memengaruhi CI

| Batas | Nilai | Dampak bila terlampaui | Mitigasi di repo |
|---|---|---|---|
| Storage artifact + GitHub Packages | 500 MB | `upload-artifact` gagal "Artifact storage quota has been hit" (dihitung ulang tiap 6-12 jam) | Artefak hanya saat job gagal (`if: failure()`), `retention-days` 2-3, `continue-on-error: true`; SBOM tidak lagi di-upload sebagai artifact (di-attest ke registry); gitleaks tanpa artifact |
| Menit Actions (runner Linux) | 2.000 menit/bulan | Workflow berhenti sampai bulan berikutnya | `concurrency` cancel-in-progress (CI, Security, CodeQL, Build); Dependabot dikelompokkan + limit PR; E2E nightly hanya bila `E2E_NIGHTLY_ENABLED=true`; DAST hanya bila `STAGING_DOMAIN` di-set |
| Code scanning (CodeQL, upload SARIF) | Tidak tersedia tanpa GitHub Advanced Security (GHAS) di repo privat | `Resource not accessible by integration` | CodeQL & upload SARIF hanya bila repo publik atau `CODE_SCANNING_ENABLED=true`; hasil scanner ditulis ke log + job summary |

Gate keamanan **tidak dilemahkan**: semgrep (`--error`), Trivy fs/image (`exit-code: 1`, HIGH/CRITICAL fixed), checkov,
gitleaks, dependency-review, `npm audit`/`pip-audit` tetap gagal (merah) pada temuan. Yang hilang tanpa code scanning
hanya **tampilan** temuan di tab Security; temuan tetap terlihat di log job dan di ringkasan run (Summary).

**Apa yang benar-benar diblokir (SEC-80, r9).** Tanpa branch protection (paket gratis), check merah **tidak** mencegah
merge atau push langsung ke `main`; pada push ke `main` hasil CI/Security keluar *setelah* commit masuk. Yang diblokir
secara teknis adalah **rilis**:
- `build-push.yml` job `Gate (CI & Security hijau untuk commit ini)` menunggu (maks. 75 menit, polling 30 dtk) lalu
  memverifikasi lewat Actions API bahwa untuk **commit yang sama** (event push, branch `main`) workflow `ci.yml`
  (job `CI gate`: test, lint, `npm audit`, `pip-audit`, infra) dan `security.yml` (job `Secret scan (gitleaks)`,
  `SAST (semgrep)`, `Trivy filesystem (vuln, secret, misconfig)`, `IaC (checkov: Dockerfile, GitHub Actions)`)
  selesai `success`. Gagal/absen/batas waktu habis -> tidak ada push, scan, maupun tanda tangan image.
- `deploy.yml` job `Resolve target` mengulang verifikasi itu (+ `Build & Push Images` success) untuk SHA penuh commit
  yang dideploy, termasuk tag `v*` dan `workflow_dispatch`, lalu `cosign verify` mengikat digest ke SHA tersebut
  (`--certificate-github-workflow-sha`). Run Build & Push dipilih per ref: `main` untuk `sha-*`, `vX.Y.Z` untuk rilis
  (SEC-94). Host `efn-deploy` mengulang ikatan SHA (SEC-91) dan, untuk production, mewajibkan persetujuan
  out-of-band Superadmin `/etc/efn/approved-release` (SEC-92, [key-custody.md §4d](key-custody.md)).
- **CI penuh pada push ke `main` (SEC-89).** Filter path (`dorny/paths-filter`) hanya berlaku untuk pull_request. Pada
  push ke `main` semua job CI berjalan, sehingga `CI gate` commit X = hasil uji seluruh kode di X, bukan hanya file
  yang berubah di push itu. Tanpa ini, commit A yang test-nya merah/dibatalkan bisa ikut dirilis lewat commit B yang
  hanya mengubah dokumen. Gitleaks pada push/jadwal memindai **seluruh riwayat** (bukan rentang push); semgrep, Trivy
  fs, dan checkov memang selalu memindai seluruh tree.
- Daftar job wajib ada di satu tempat: `scripts/verify-commit-gates.sh` (`PRESET_RELEASE`). Mengganti `name:` job di
  `ci.yml`/`security.yml` tanpa memperbarui preset = gate gagal-tertutup ("absent").
- Tag `v*` hanya untuk commit di `main` yang **run CI penuh + Security-nya sendiri** `success` (cek:
  `bash scripts/verify-commit-gates.sh --sha <SHA> --preset release --timeout-min 0 --absent-min 0`). Bila run CI
  commit itu dibatalkan (`cancel-in-progress` karena push berikutnya), jangan men-tag commit itu: tunggu run CI penuh
  commit berikutnya selesai hijau lalu tag commit tersebut (gate menolak commit tanpa run hijau miliknya sendiri).
  Production juga butuh baris persetujuan di host (key-custody §4d).
- **Biaya menit (diukur, merge PR #35 = ab0e4aa, 9 Okt 2026):** CI penuh 26 menit runner (Odoo test ±6 dan tour ±6
  menit), Build & Push termasuk job gate 30, Security 5, CodeQL 1 → **±62 menit per merge ke `main`** (pembulatan
  ke atas per job). Dengan kuota 2.000 menit/bulan: ±30 merge/bulan, di luar run PR (PR yang mengubah `ci.yml`
  menjalankan semua job, ±60 menit; PR biasa lebih sedikit karena path-filter). Alur satu PR per gelombang (bagian 9)
  tetap dipertahankan; bila kuota habis: runner self-hosted untuk job Odoo atau menit tambahan (bagian 8). Perkiraan
  awal (135-195 menit) terlalu tinggi.
- Pilihan yang ditolak: memicu `build-push` lewat `workflow_run` setelah CI + Security. Workflow_run berjalan di
  konteks default branch (identitas cosign untuk tag rilis `build-push.yml@refs/tags/vX.Y.Z` hilang) dan dua workflow
  sumber memicu dua run yang harus saling menunggu.

## 2. Repository variables (Settings > Secrets and variables > Actions > Variables)

| Variable | Nilai | Efek |
|---|---|---|
| `CODE_SCANNING_ENABLED` | `true` hanya bila GHAS aktif (atau repo dibuat publik) | CodeQL berjalan; SARIF semgrep/Trivy/checkov diunggah ke tab Security |
| `E2E_NIGHTLY_ENABLED` | `true` hanya bila memakai runner self-hosted (`E2E_RUNNER`) atau kuota menit cukup | Jadwal E2E nightly (hingga 150 menit/run) aktif |
| `E2E_RUNNER` | label runner self-hosted | E2E tidak memakai menit GitHub-hosted |
| `STAGING_DOMAIN` | domain staging | DAST ZAP berjalan (tanpa ini job dilewati) |
| `STAGING_DEPLOY_ENABLED` | `true` setelah host staging disiapkan Superadmin (efn-deploy, deploy.conf, secrets environment `staging`) | Deploy otomatis ke staging setelah Build & Push di main (tanpa ini dilewati; tag `v*` dan dispatch manual tetap jalan) |
| `ATTESTATIONS_ENABLED` | `true` hanya bila repo di organisasi berbayar / publik | Build provenance GitHub (`attest-build-provenance`); deploy tidak bergantung padanya (host memakai `cosign verify`) |

Catatan: artifact attestation GitHub tidak tersedia untuk repo privat milik akun pribadi ("Feature not available for
user-owned private repositories"). Signature cosign keyless + attestation SBOM (SPDX tingkat paket, ±2 MB) tetap
dibuat dan disimpan di GHCR. Urutan per image (SEC-83/90): build & push **by digest tanpa tag** -> Trivy atas
`<repo>@<digest>` -> SBOM -> `cosign sign` + `cosign attest` -> baru pasang tag (`main`, `sha-<short>` hanya dari main,
`X.Y.Z`, `X.Y`, `latest`) atas digest itu (`docker buildx imagetools create`, digest diverifikasi sama). Image yang gagal
Trivy hanya ada di GHCR sebagai digest tanpa tag dan **tanpa tanda tangan**, sehingga ditolak `cosign verify` di deploy
dan tidak terambil oleh `docker pull <tag>`. Digest yatim itu boleh dihapus berkala (bagian 3, catatan GHCR).

**Apa yang publik di Rekor (SEC-84, diverifikasi 9 Okt 2026).** Entri run `62b1bf1` image metabase
(`sha256:4d64a938…`): sign = log index 3155540831 (`hashedrekord`), attest SBOM = 3155542984 (`dsse`). API publik
`https://rekor.sigstore.dev/api/v1/log/entries?logIndex=<idx>` mengembalikan untuk entri `dsse` hanya `envelopeHash`,
`payloadHash`, signature, dan sertifikat Fulcio; field `attestation` kosong (isi SBOM **tidak** disimpan/disajikan).
Yang **publik permanen** lewat sertifikat: nama repo `bctech-adm/efn-platform` (+ owner), path workflow + ref
(`build-push.yml@refs/heads/main` / tag), SHA commit, event pemicu, waktu, dan hash. Daftar paket/versi image tetap
privat (GHCR). Catatan: saat `cosign attest`, envelope lengkap **dikirim** ke server Rekor (untuk verifikasi
signature) lalu tidak disimpan; batas ukuran itulah yang menolak SBOM 58 MB. Cek ulang:
`curl -s "https://rekor.sigstore.dev/api/v1/log/entries?logIndex=<idx>" | jq '.[] | {attestation, kind: (.body|@base64d|fromjson|.kind), spec: (.body|@base64d|fromjson|.spec|keys)}'`.

## 3. Membersihkan kuota storage artifact (sekali, setelah merge G16)

Artefak lama (SBOM 8 image per build, log Odoo, SARIF gitleaks) tetap memakan kuota sampai kedaluwarsa. Hapus manual
(butuh `gh` terautentikasi dengan hak admin repo). Perintah ini menghapus **semua** artifact repo; tidak ada data
produksi di dalamnya (log CI dan SBOM yang juga tersedia di registry).

```bash
R=bctech-adm/efn-platform
gh api --paginate "repos/$R/actions/artifacts?per_page=100" --jq '.artifacts[] | "\(.id) \(.name) \(.size_in_bytes)"'
gh api --paginate "repos/$R/actions/artifacts?per_page=100" --jq '.artifacts[].id' \
  | while read -r id; do gh api -X DELETE "repos/$R/actions/artifacts/$id"; done
```

Kuota dihitung ulang 6-12 jam setelah penghapusan. Cache Actions (`type=gha` BuildKit, cache Trivy DB) **tidak**
termasuk kuota 500 MB (batas cache terpisah 10 GB per repo, entri lama dibuang otomatis).

Catatan GHCR: image di GitHub Container Registry untuk repo privat termasuk "GitHub Packages". Pantau
Settings > Billing; bila storage Packages ikut dihitung, hapus versi image `sha-*` lama (retensi mis. 30 hari, kecuali
digest yang tercatat di manifest rilis bertanda tangan).

## 4. Membaca hasil scanner tanpa code scanning

- Buka run > job (mis. "SAST (semgrep)") > **Summary**: tabel `file:baris | rule | severity`. Semgrep sengaja tidak
  menampilkan potongan kode (rule `p/secrets` bisa memuat nilai rahasia).
- Trivy (fs & image): tabel HIGH/CRITICAL yang punya versi perbaikan; secret di-mask oleh Trivy.
- Checkov: daftar check FAILED (`--compact`, tanpa potongan kode).
- Gitleaks: temuan di-redact (`--redact`) di log + summary.

## 5. Pengecualian (suppression) — hanya dengan alasan dan tanggal tinjau

| Scanner | Mekanisme | Aturan |
|---|---|---|
| Trivy | `.trivyignore.yaml` (root repo) | Wajib `statement` (alasan) + `expired_at` (maks. 90 hari); CVE dependency aplikasi diperbaiki di dependency, bukan di-ignore |
| Semgrep | komentar `# nosemgrep: <rule-id>` di baris sebelum temuan | Hanya false positive yang jelas + komentar alasan di atasnya |
| Checkov | komentar `# checkov:skip=<ID>: <alasan>` di file terkait | Alasan di komentar yang sama |
| Gitleaks | `.gitleaksignore` (fingerprint) | Hanya data uji fiktif |

## 6. Dependabot

- Satu PR berkelompok per ekosistem/aplikasi per minggu (minor+patch); mayor terpisah; security update dikelompokkan.
- `open-pull-requests-limit` 1-2 per entri; `cooldown` 7 hari (mayor 14 hari).
- Setelah merge G16, PR Dependabot lama (33 PR run pertama) sebaiknya **ditutup** agar Dependabot membuat ulang dalam
  bentuk berkelompok: `gh pr list -R bctech-adm/efn-platform --author app/dependabot --json number --jq '.[].number' | xargs -n1 -I{} gh pr close {} -R bctech-adm/efn-platform`.

## 7. Validasi lokal sebelum push (container, tanpa Node/Python di host)

```bash
export MSYS_NO_PATHCONV=1; W=$(pwd -W)
docker run --rm -v "$W:/repo:ro" -w /repo rhysd/actionlint:1.7.7 -color=false
docker run --rm -v "$W:/mnt:ro" -w /mnt --entrypoint sh koalaman/shellcheck-alpine:v0.9.0 -c 'shellcheck -x tests/load/*.sh scripts/smoke/*.sh'
docker run --rm -v "$W:/src" -w /src semgrep/semgrep:1.179.0 semgrep scan --config p/owasp-top-ten --config p/secrets \
  --config p/dockerfile --config p/typescript --config p/python --config p/nodejs --severity ERROR --severity WARNING --error
docker run --rm -v "$W:/src:ro" -w /src bridgecrew/checkov:3.3.21@sha256:9aefe56582004ebdac112fe85c3268dc4d3658231a8f023fb714ba9d29539d53 -d . --framework dockerfile,github_actions,yaml \
  --skip-check CKV_DOCKER_2 --skip-path secrets --compact --quiet
docker run --rm -v trivy-cache:/root/.cache -v "$W:/src:ro" -w /src aquasec/trivy:0.69.3 fs --timeout 30m \
  --scanners vuln,secret,misconfig --severity HIGH,CRITICAL --ignore-unfixed --ignorefile .trivyignore.yaml \
  --skip-dirs secrets --exit-code 1 .
```

Trivy DB (±120 MB) di jaringan lambat: unduh dulu `trivy image --download-db-only --timeout 40m` ke volume
`trivy-cache`, lalu scan dengan `--skip-db-update`. Gunakan digest yang sama dengan workflow untuk image di atas.

## 8. Pilihan berbayar / keputusan (bukan default)

- **GHAS / GitHub Team + Code Security**: tab Security + CodeQL di repo privat -> set `CODE_SCANNING_ENABLED=true`.
- **Menit tambahan** (spending limit) atau **runner self-hosted** (menit gratis; wajib hardening: runner ephemeral,
  tidak untuk PR dari fork).
- **Repo publik**: code scanning gratis, tetapi seluruh kode & riwayat terbuka — tidak disarankan sebelum review
  kepatuhan (UU PDP, rahasia bisnis).

## 9. Alur PR manual (pengganti required reviewers di paket gratis)

Keputusan pemilik proyek (9 Okt 2026): tidak ada push langsung ke `main`. Setiap gelombang kerja dikumpulkan di **satu
branch dan satu PR**, lalu di-review dan di-merge manual oleh pemilik proyek. Di repo privat paket gratis, branch
protection / ruleset tidak bisa memaksakan ini, jadi disiplin ini adalah gerbangnya.

### 9.1 Review & merge (web)

1. Buka `https://github.com/bctech-adm/efn-platform/pulls` > PR terbaru.
2. Tab **Conversation**: baca ringkasan (apa berubah, apa yang sudah/belum diverifikasi, keputusan yang diminta).
3. Tab **Checks**: pastikan semua hijau — `CI gate`, `Security`, `Build & Push Images` (di PR hanya job
   `scan <service>`: build + scan dengan token baca saja, tanpa push), `CodeQL` (notice bila code scanning tidak
   aktif). Klik job merah > **Summary** untuk temuan. Merge dengan check merah tetap mungkin secara teknis, tetapi
   image commit itu tidak akan di-push/ditandatangani/dideploy (gerbang rilis bagian 1).
4. Tab **Files changed**: perhatikan khusus `.github/workflows/**`, `.trivyignore.yaml`, `.gitleaksignore`,
   `infra/compose/**`, `scripts/host/**`, dan file `security/` Odoo — perubahan di sini menyentuh gerbang keamanan.
5. **Merge pull request** > pilih **Squash and merge** (satu commit per gelombang di `main`) > Confirm.
6. Setelah merge: tab **Actions** — run di `main` (CI, Security, Build & Push) harus hijau. Deploy staging hanya
   jalan bila `STAGING_DEPLOY_ENABLED=true`.

### 9.2 Review & merge (PowerShell, gh CLI)

```powershell
$gh = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\bin\gh.exe"
$R  = "bctech-adm/efn-platform"
& $gh pr list -R $R                       # daftar PR terbuka
& $gh pr view <nomor> -R $R               # ringkasan
& $gh pr checks <nomor> -R $R --watch     # tunggu semua check
& $gh pr diff <nomor> -R $R               # lihat perubahan
& $gh pr merge <nomor> -R $R --squash --delete-branch
```

### 9.3 Pembersihan sekali jalan setelah G16 (PowerShell)

```powershell
# Tutup PR Dependabot lama (akan dibuat ulang berkelompok oleh konfigurasi baru)
& $gh pr list -R $R --author "app/dependabot" --state open --limit 100 --json number --jq ".[].number" |
  ForEach-Object { & $gh pr close $_ -R $R --delete-branch -c "Ditutup: Dependabot kini dikelompokkan (G16)." }

# Hapus semua artifact lama (log CI/SBOM; tidak ada data produksi). Kuota dihitung ulang 6-12 jam kemudian.
& $gh api --paginate "repos/$R/actions/artifacts?per_page=100" --jq ".artifacts[].id" |
  ForEach-Object { & $gh api -X DELETE "repos/$R/actions/artifacts/$_" }
```

### 9.4 Disarankan (gratis): aktifkan di Settings

- **Settings > General > Pull Requests**: centang hanya *Allow squash merging*; centang *Automatically delete head
  branches*.
- **Settings > Code security**: *Dependency graph* ON, *Dependabot alerts* ON, *Dependabot security updates* ON.
