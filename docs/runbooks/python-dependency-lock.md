# Runbook: lock dependency Python ber-hash (SEC-214)

Dasar: temuan SEC-214 (`docs/reviews/SECURITY_REVIEW_2026-11-18_g44.md`, risiko supply chain R-S). Berlaku untuk
service Python yang terdaftar di `LOCKED_SERVICES` pada `scripts/check-py-lock.sh` (saat ini: `forecast-worker`).
Audiens: developer, lead, dan on-call IT EFN.

> **Ringkasan manajemen.** Image forecast-worker sekarang hanya mau dibangun dari daftar paket yang sudah dikunci,
> termasuk paket turunan, dan setiap paket diverifikasi dengan sidik jari (hash). Kalau seseorang mengganti paket di
> PyPI, build akan gagal dan tidak diam-diam memakai paket itu. Konsekuensinya, setiap perubahan versi paket wajib
> disertai pembaruan file kunci lewat satu perintah.

## Fakta teknis

| Hal | Nilai |
|---|---|
| Input (pin langsung, direview) | `services/<svc>/requirements.txt` (hanya `nama==versi`) |
| Lock (pin penuh + hash) | `services/<svc>/requirements.lock` (keluaran `pip-compile --generate-hashes`, Python 3.12 linux) |
| Pembuat lock | `bash scripts/lock-python-deps.sh <svc> [--upgrade]` (container = image `FROM` Dockerfile service, pip-tools 7.5.1) |
| Pemeriksa (CI job `python`) | `bash scripts/check-py-lock.sh` (bash + awk, tanpa jaringan) |
| Install di image | `pip install --require-hashes --no-deps -r requirements.lock` lalu `pip check` (Dockerfile) |
| Install di CI pytest | sama dengan image, lalu `requirements-dev.txt` (hanya alat uji) |
| Audit CVE | `pip-audit -r requirements.lock --require-hashes --disable-pip --strict` (CI job `python`) |
| Placeholder | lock berisi `EFN_LOCK_PLACEHOLDER` → build image, pytest, dan check-py-lock **sengaja gagal** |

## 1. Membuat lock pertama kali / setelah mengubah `requirements.txt` {#buat}

Dijalankan lead/developer dari **root repo** di Git Bash (Windows) atau shell Linux. Docker Desktop wajib berjalan dan
butuh akses ke PyPI. Skrip menangani `MSYS_NO_PATHCONV=1` dan `pwd -W` sendiri.

```bash
bash scripts/lock-python-deps.sh forecast-worker      # menulis services/forecast-worker/requirements.lock
bash scripts/check-py-lock.sh forecast-worker         # harus: "check-py-lock: forecast-worker OK (...)"
```

Tanpa `--upgrade`, pip-compile mempertahankan pin transitif yang sudah ada di lock selama masih memenuhi, jadi hasilnya
stabil (idempoten). Pakai `--upgrade` hanya bila memang ingin menaikkan dependency transitif, misalnya untuk menutup CVE.

Bila skrip tidak bisa dipakai, perintah mentah yang setara (hasil harus identik):

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd)/services/forecast-worker:/src:ro" \
  python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f \
  sh -c 'pip install -q --no-cache-dir pip-tools==7.5.1 >&2 && mkdir -p /work && cd /work \
    && cp /src/requirements.txt . \
    && CUSTOM_COMPILE_COMMAND="bash scripts/lock-python-deps.sh forecast-worker" \
       pip-compile --quiet --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url \
         --resolver=backtracking --output-file=requirements.lock requirements.txt >&2 \
    && cat requirements.lock' > services/forecast-worker/requirements.lock.new \
  && mv services/forecast-worker/requirements.lock.new services/forecast-worker/requirements.lock
```

Digest image di atas harus sama dengan baris `FROM` di `services/forecast-worker/Dockerfile`. Kalau digest base
dinaikkan (Dependabot `docker`), buat ulang lock.

## 2. Verifikasi sebelum commit {#verifikasi}

```bash
bash scripts/check-py-lock.sh forecast-worker
# Build image (gagal bila hash tidak cocok / dependency hilang):
MSYS_NO_PATHCONV=1 docker build -t efn/forecast-worker:lock-test services/forecast-worker
# Uji unit dengan dependency yang sama dengan image:
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd):/src:ro" -w /src/services/forecast-worker \
  -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f \
  sh -c 'pip install -q --require-hashes --no-deps -r requirements.lock && pip check \
         && pip install -q pytest==9.0.3 && python -m pytest -q -p no:cacheprovider tests'
```

Commit `requirements.txt` dan `requirements.lock` dalam commit yang sama.

## 3. Cek lisensi dependency transitif {#lisensi}

SEC-214 juga meminta lisensi transitif diverifikasi (tidak boleh ada GPL/AGPL di image yang didistribusikan tanpa
keputusan Legal). Jalankan setelah lock dibuat, lalu catat hasilnya di `services/forecast-worker/README.md` (tugas
pemilik service, efn-data-ai-engineer):

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd)/services/forecast-worker:/src:ro" \
  python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f \
  sh -c 'pip install -q --require-hashes --no-deps -r /src/requirements.lock \
         && pip install -q pip-licenses==5.0.0 \
         && pip-licenses --from=mixed --order=license --format=markdown \
              --ignore-packages pip-licenses prettytable wcwidth'
```

Kolom lisensi berisi `GPL`/`AGPL` (bukan `LGPL`) atau `UNKNOWN` → periksa manual repo primer paket itu dan eskalasi ke
lead. `MPL-2.0` (certifi, tqdm) boleh dipakai tanpa modifikasi.

## 4. CI gagal di `check-py-lock` {#ci-gagal}

| Pesan | Arti / tindakan |
|---|---|
| `masih placeholder` | Lock belum pernah dibuat → §1 |
| `requirements.txt==X != lock==Y` / `tidak di lock` | `requirements.txt` diubah (termasuk PR Dependabot `pip`) tanpa lock ulang → §1 di branch itu, push lock |
| `tidak beranotasi 'via -r requirements.txt'` / `lock basi` | Lock dibuat dari input lain atau diedit manual → buat ulang §1 |
| `tanpa --hash=sha256` / `opsi pip/URL` | Lock diedit manual atau dibuat tanpa `--generate-hashes` → buat ulang §1 |
| `bukan keluaran pip-compile Python 3.12` | Lock dibuat dengan Python lain → pakai skrip (image base service) |

Dependabot `pip` hanya memperbarui `requirements.txt`, jadi PR Dependabot untuk forecast-worker selalu gagal di langkah
ini sampai lock dibuat ulang. Itu disengaja: pembaruan dependency tetap melewati manusia.

## 5. Build image gagal: `requirements.lock belum dibuat` / `THESE PACKAGES DO NOT MATCH THE HASHES` {#build-gagal}

- `belum dibuat`: lock masih placeholder → §1.
- `DO NOT MATCH THE HASHES`: wheel di PyPI berbeda dengan hash yang dikunci. Jangan langsung membuat ulang lock.
  Perlakukan sebagai **dugaan supply-chain**: catat paket dan versinya, cek riwayat rilis paket itu (apakah file
  di-yank/diunggah ulang), lalu eskalasi ke security reviewer. Lock baru dibuat setelah penyebabnya jelas.
- `pip check` gagal: lock tidak lengkap (dibuat dengan `--no-deps` atau diedit) → buat ulang §1.

## 6. Versi pip-tools {#versi-pip-tools}

`PIP_TOOLS_VERSION` di `scripts/lock-python-deps.sh` dipin (SEC-88). Menaikkannya lewat PR terpisah, lalu buat ulang lock
dan pastikan diff hanya di header/format.

## 7. Menambah service lain ke pola lock {#service-baru}

1. Dockerfile service: pola `COPY requirements.txt requirements.lock` + `pip install --require-hashes --no-deps` +
   `pip check` (salin dari `services/forecast-worker/Dockerfile`).
2. Tambahkan nama service ke `LOCKED_SERVICES` di `scripts/check-py-lock.sh`.
3. Buat lock (§1). Job pytest & pip-audit CI otomatis memakai lock bila file ada.
