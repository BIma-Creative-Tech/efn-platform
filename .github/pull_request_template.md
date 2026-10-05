## Ringkasan
<!-- Apa yang berubah dan kenapa. Tautkan issue / ADR. -->

## Jenis perubahan
- [ ] Fitur
- [ ] Perbaikan bug
- [ ] Infra / CI
- [ ] Dokumen / ADR

## Checklist DevSecOps
- [ ] Tidak ada secret, token, atau data pelanggan di diff (gitleaks hijau)
- [ ] Dependensi baru dicek lisensinya (bukan GPL/AGPL untuk NPM/Py) dan `npm audit`/`pip-audit` bersih
- [ ] Input eksternal divalidasi (schema), output tidak membocorkan stack trace
- [ ] Endpoint baru punya rate limit / auth yang sesuai
- [ ] Data pribadi (nomor HP, lokasi, foto) tidak masuk log (UU PDP)
- [ ] Migrasi DB reversible dan diuji di staging
- [ ] Healthcheck / metrik / log terstruktur untuk komponen baru
- [ ] Dokumen diperbarui (docs/, ADR jika keputusan arsitektur)

## Cara menguji
<!-- Langkah reproduksi di dev/staging -->

## Dampak risiko (lihat docs/01 §4)
<!-- Sebutkan R-xxx yang terkait, atau "tidak ada" -->
