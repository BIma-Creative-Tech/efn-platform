# Kebijakan Keamanan EFN Platform

## Melaporkan kerentanan
Kirim laporan ke security@efn.example (ganti dengan alamat resmi) atau melalui GitHub Security Advisories (private). Jangan buka issue publik untuk kerentanan. Kami menargetkan respons awal dalam 2 hari kerja dan perbaikan untuk severity tinggi dalam 14 hari.

## Cakupan
- Repo ini (kode, Dockerfile, workflow, konfigurasi).
- Lingkungan staging. Pengujian terhadap produksi hanya dengan izin tertulis.

## Kontrol yang diterapkan (DevSecOps)
| Tahap | Kontrol | Lokasi |
|---|---|---|
| Pre-commit | gitleaks, ruff, hadolint, yamllint | `.pre-commit-config.yaml` |
| PR | gitleaks, semgrep (SAST), trivy fs, dependency review, checkov (IaC), CodeQL | `.github/workflows/security.yml`, `codeql.yml` |
| Build | trivy image gate HIGH/CRITICAL, SBOM SPDX, cosign keyless signature, SLSA provenance | `build-push.yml` |
| Deploy | verifikasi cosign sebelum `compose pull`, secrets dari GitHub Environments ke file, approval manual untuk produksi | `deploy.yml`, `scripts/deploy.sh` |
| Runtime | Traefik security headers, rate limit, IP allowlist dashboard internal, container non-root, `read_only`, `no-new-privileges`, network internal | `infra/` |
| Post-deploy | OWASP ZAP baseline, Dependabot mingguan | `dast.yml`, `dependabot.yml` |

## Penanganan data pribadi
Nomor HP, lokasi, dan foto pelanggan adalah data pribadi menurut UU No. 27/2022 (PDP). Jangan menyalin data produksi ke laptop atau staging tanpa anonimisasi. Log dire-daksi oleh Promtail (`infra/observability/promtail.yml`).

## Dukungan versi
Hanya image dengan tag `vX.Y.Z` yang ditandatangani dan diverifikasi yang didukung di produksi.
