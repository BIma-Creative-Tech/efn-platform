# Security Review — forecast-worker multi-company (G46d, commit `b515026`)

- Tanggal: 22 Nov 2026
- Reviewer: efn-security-reviewer (read-only terhadap kode)
- Cakupan: `feat/forecast-multi-company` @ `b515026` vs `origin/main`, ADR-046 amandemen 22 Nov 2026
- File yang dibaca: `services/forecast-worker/efn_forecast/{__main__,config,runner,source,odoo}.py`,
  `tests/test_config_schedule.py`, `infra/compose/docker-compose.forecast.yml`, `scripts/{deploy.sh,secrets.list,check-encryption.sh}`,
  `scripts/lib/deploy-validate.sh`, `scripts/host/efn-forecast-key-status`, `infra/observability/{alert-rules.yml,loki-rules/efn-forecast.yml}`,
  `services/odoo/addons/efn_demand_forecast/models/{forecast_api,efn_forecast_run}.py`, ADR-046 §Amandemen.
- Keputusan "satu container untuk beberapa PT" **tidak** dipersoalkan ulang; yang dinilai hanya risiko turunannya.

## Ringkasan eksekutif

Tidak ada temuan Critical atau High. Jalur injeksi lewat `FORECAST_COMPANIES` / `FORECAST_DC_CODES` sudah tertutup berlapis
(compose exec-form, regex + allowlist di deploy-validate, `.env` tanpa karakter kontrol, regex + allowlist di skrip root, regex
lagi di worker). Isolasi data antar-PT dijaga Odoo (`company_mismatch`, scope gudang per company) dan query ClickHouse yang
terparametrisasi per PT. Temuan utama (Medium): overlay selalu me-mount API key **semua** PT di allowlist, sedangkan
pemantau kedaluwarsa hanya memeriksa PT di `FORECAST_COMPANIES`, sehingga key PT yang tidak dijalankan bisa tetap ter-mount
dan tidak terpantau. Rekomendasi: perbaiki SEC-G46d-01 sebelum `FORECAST_ENABLED=true` dengan PB di production; PR boleh merge.

## Tabel temuan

| ID | Severity | Ringkas | File:baris | Risiko |
|---|---|---|---|---|
| SEC-G46d-01 | Medium | Key PT di luar `FORECAST_COMPANIES` tetap ter-mount di worker dan tidak dipantau masa berlakunya | `infra/compose/docker-compose.forecast.yml:87-88,104-105`; `scripts/host/efn-forecast-key-status:56-63`; `scripts/deploy.sh:224-231` | R-S01, R-T15 |
| SEC-G46d-02 | Low | Log `EFN_FORECAST_RUN` dan alert tidak memuat kode PT, jadi forensik/triase multi-PT tidak bisa diatribusikan | `services/forecast-worker/efn_forecast/runner.py:368-372`; `infra/observability/loki-rules/efn-forecast.yml:20,35,51` | R-T09 |
| SEC-G46d-03 | Low | Tiga salinan allowlist PT forecast tanpa test sinkronisasi; skrip root membuang PT tak dikenal secara diam-diam (`ok=1`) | `scripts/lib/deploy-validate.sh:102`; `scripts/host/efn-forecast-key-status:34,59-63`; `scripts/check-encryption.sh:506` | R-T15, R-T09 |
| SEC-G46d-04 | Info | `FORECAST_DC_CODES` global untuk semua PT; kode bisa bertabrakan (fungsional, bukan kebocoran) | `efn_forecast/config.py:180`; `runner.py:170` | — |
| SEC-G46d-05 | Info | deploy.sh hanya menolak placeholder `disabled`; worker menolak daftar lebih luas (gagal tertutup, tapi baru saat runtime) | `scripts/deploy.sh:228`; `efn_forecast/config.py:28,263` | R-S01 |
| SEC-G46d-06 | Info (risiko diterima ADR) | Satu container memegang key beberapa PT: bila worker dikompromikan, usulan forecast semua PT di daftar bisa dipalsukan | ADR-046 §Amandemen; `docker-compose.forecast.yml:59,87-88` | R-S02 |

## Detail

### SEC-G46d-01 — Medium — Key PT yang tidak dijalankan tetap ter-mount dan tidak terpantau

**Fakta kode.**
- Overlay me-mount `odoo_api_key_forecast_smd` **dan** `odoo_api_key_forecast_pb` tanpa syarat (`docker-compose.forecast.yml:87-88`
  env `*_FILE`, `:104-105` daftar `secrets`). Isi `FORECAST_COMPANIES` tidak memengaruhi mount.
- `deploy.sh:224-231` hanya memeriksa bahwa key PT **di dalam** `FORECAST_COMPANIES` nyata; PT allowlist di luar daftar tidak
  diperiksa sama sekali (boleh nyata, boleh placeholder).
- `efn-forecast-key-status:56-63` hanya memeriksa PT di irisan `FORECAST_COMPANIES` ∩ allowlist. Komentar baris 15-16 menyatakan
  ini disengaja ("PT allowlist yang tidak dijalankan juga tidak diperiksa").
- Teks ADR-046 amandemen (baris 193) menyebut "satu container memegang key semua PT **di daftar**". Kenyataannya: semua PT di
  **allowlist** overlay.

**Skenario.** Superadmin membuat user `efn_forecast_pb` + API key untuk uji PB, lalu PB dikeluarkan dari daftar
(`FORECAST_COMPANIES=SMD`) karena hasil forecast buruk atau insiden. File `secrets/odoo_api_key_forecast_pb.txt` tetap berisi
key nyata dan tetap di-mount di `/run/secrets` worker. Key-status berhenti menulis metrik PB, sehingga alert
`EfnForecastApiKeyNoExpiry` (SEC-223) dan `ExpiringSoon` tidak lagi berlaku untuk PB. Jika key itu dibuat tanpa kedaluwarsa,
key tersebut hidup tanpa batas di container yang tidak membutuhkannya. Akibatnya, kompromi worker (mis. dependency statsforecast/numba
yang dibajak) memberi akses tulis ke usulan forecast PB lewat gateway tanpa ada alarm.
Dampak dibatasi oleh 5 metode allowlist di gateway dan `check_caller` tepat satu company.

**Rekomendasi (pilih satu, yang pertama paling sederhana).**
1. `deploy.sh`, di blok `FORECAST_ENABLED=true`: untuk setiap PT di `EFN_FORECAST_COMPANY_CODES` yang **tidak** ada di
   `FORECAST_COMPANIES`, tolak (production) atau beri peringatan (staging) bila file key-nya bukan placeholder `disabled`. Pesan:
   "cabut key di Odoo dan kembalikan file ke `disabled`".
2. Atau `efn-forecast-key-status`: bila forecast aktif, periksa **semua** PT allowlist yang user-nya ada. PT di luar daftar yang
   punya `active_keys > 0` diberi metrik/alert "key forecast tak terpakai".
3. Koreksi kalimat ADR-046 amandemen ("di allowlist overlay", bukan "di daftar") supaya penerimaan risiko akurat.

### SEC-G46d-02 — Low — Log run dan alert tanpa atribusi PT

`_log_report` (`runner.py:368-372`) mencetak `run_id, kind, status, …, error` tanpa `company_code`. Dengan dua PT pada jadwal
yang sama, `EfnForecastRunFailed` dan `EfnForecastAuth` (`loki-rules/efn-forecast.yml:20,35`) tidak bisa menyebut PT mana yang
key-nya ditolak. Teks alert juga hanya menulis `efn_forecast_<pt>`. Satu-satunya cara korelasi adalah lewat menu Runs di Odoo. Dead-man
`EfnForecastRunStale` (`:51`) menjumlahkan semua PT, jadi satu PT yang `ok` sudah cukup untuk memenuhinya. Ini aman karena
kegagalan PT lain tetap memicu `RunFailed`, tetapi tidak ada sinyal per PT.
Baris "tidak dapat dimulai" (`__main__.py:89`) sudah memuat kode PT dan hanya nama kelas exception (bagus, tanpa secret).

**Rekomendasi.** Tambahkan `"company":"%s"` di akhir JSON `EFN_FORECAST_RUN`, setelah `error`, agar regex Loki yang ada tidak
patah. Lalu gunakan `| regexp` atau `| json` untuk label `company` di anotasi alert. Kode PT bukan PII.

### SEC-G46d-03 — Low — Drift allowlist dan PT yang dibuang diam-diam oleh skrip root

Allowlist PT forecast ada di tiga tempat: `deploy-validate.sh:102` `(SMD PB)`, `efn-forecast-key-status:34` `(SMD PB)`, dan regex
`check-encryption.sh:506` `smd|pb`. Ketiganya tidak dicek oleh test (`test-efn-deploy.sh` tidak menyentuh `EFN_FORECAST_COMPANIES`).
Selain itu, skrip key-status dipasang **manual** di `/usr/local/sbin` (baris 8-11), sehingga host yang belum memasang ulang
masih memakai versi lama (hanya SMD). Pada versi baru, baris 59-63 membuang kode di luar allowlist tanpa log dan tetap `ok=1`.
PT baru yang lupa ditambahkan di skrip ini lolos tanpa pemantauan kedaluwarsa key.

**Rekomendasi.** (a) Di `test-efn-deploy.sh`, ambil array dari kedua file dan bandingkan dengan regex check-encryption.
(b) Di key-status, kode di `FORECAST_COMPANIES` yang tidak ada di allowlist ditulis ke log dan membuat `ok=0`.
(c) Runbook forecast-worker §4a: tambahkan langkah "pasang ulang `efn-forecast-key-status` setelah rilis G46d".

### SEC-G46d-04 — Info — `FORECAST_DC_CODES` berlaku global

Satu daftar gudang agregat dipakai untuk semua PT (`config.py:180`). Pencocokan sudah dibatasi ke gudang milik PT yang sedang
diproses (`runner.py:170`, gudang dari `SQL_WAREHOUSES WHERE company_code = …`), dan Odoo memvalidasi gudang per company
(`efn_forecast_run.py:292`). Karena itu tidak ada kebocoran antar-PT. Risikonya hanya fungsional: kode yang sama di dua PT
dianggap DC di keduanya. Jika suatu saat perlu, gunakan sintaks per PT (`SMD:SMDDC,PB:P004`). Tidak wajib sekarang.

### SEC-G46d-05 — Info — Cek placeholder di deploy.sh lebih sempit daripada di worker

`deploy.sh:228` hanya menolak isi `disabled`. Worker (`config.py:28,263`) juga menolak `changeme`, `placeholder`, `*:disabled`,
dan nilai serupa. Hasilnya tetap gagal tertutup (batch PT itu gagal dan memicu `RunFailed`), hanya ketahuannya terlambat. Pola ini sudah ada
sebelumnya (promo/booking). Opsional: samakan daftar placeholder.

### SEC-G46d-06 — Info — Risiko diterima: satu container, beberapa key PT

Ini konsekuensi keputusan ADR-046 amandemen. Mitigasi yang sudah ada: user teknis per PT tepat satu company, gateway CN
`forecast-worker` dengan 5 metode allowlist, `company_mismatch` 403 pada `company_code`/run, container non-root/read-only/
`cap_drop ALL`/`no-new-privileges`, dan hanya network internal `forecast-ch` + `forecast-rpc` tanpa egress. Saran: pastikan setiap
key PT berdurasi ≤ 35 hari dan dirotasi secara independen (sudah ada di runbook §3). Penerimaan risiko ini sebaiknya disebut eksplisit
di ADR (lihat SEC-G46d-01 butir 3).

## Kontrol yang sudah benar (terverifikasi)

- **Injeksi lewat compose.** `command` berbentuk exec array (`docker-compose.forecast.yml:59`). `${FORECAST_COMPANIES}` menjadi satu
  argv tanpa shell. Nilai seperti `SMD --once` tetap satu token dan ditolak `company_codes` (`config.py:84-90`: `^[A-Z]{2,8}$`,
  maks. 8, dedup). Nilai berawalan `-` membuat argparse error, bukan menyuntik opsi.
- **deploy-validate.** `FORECAST_COMPANIES` harus subset unik dari allowlist yang dicocokkan persis (`:602-613`), dan
  `FORECAST_DC_CODES` dibatasi regex ketat (`:614-615`). Validasi berjalan sebelum cek key (`deploy.sh:106`).
  `efn_write_dotenv` (`:857-876`) menolak karakter kontrol/newline, kunci ganda, dan baris tidak valid; file dibuat dengan mode 0640.
- **Skrip root `efn-forecast-key-status`.** `.env` tidak di-source; ada cek owner root, mode tanpa w grup/lain, dan bukan symlink
  (`:50-52`). Regex `sed` ketat `[A-Z,]{0,80}`, irisan dengan allowlist, regex ulang `^[A-Z]{2,8}$` (`:90`). Login dikirim
  lewat `docker exec -e`, bukan diinterpolasi ke kode Python. Nilai key tidak pernah dibaca, hanya id dan kedaluwarsa.
- **Secret.** Path key diturunkan dari kode yang sudah tervalidasi, jadi tidak ada path traversal (`config.py:259-262`). Placeholder ditolak.
  `api_key`/`password` memakai `repr=False`. `run_all` hanya mencatat nama kelas exception (`__main__.py:89`). Klien Odoo tidak
  pernah mencatat `Authorization`/body (`odoo.py:11,150-153`). `check-encryption.sh:506-509` memastikan key forecast hanya `smd|pb`
  dan hanya di forecast-worker.
- **Fail-closed dan isolasi kegagalan.** Satu PT gagal tidak menghentikan PT lain, dan run tetap tercatat `failed` serta memicu alert (`__main__.py:81-91`, test
  `test_run_all_continues_after_company_failure`). `FORECAST_ENABLED` termasuk `EFN_GATED_FEATURES` (perlu izin Superadmin di production).
  Overlay hanya disertakan bila forecast aktif.
- **Multi-company.** Ada satu `OdooForecastClient` per PT dengan key PT itu sendiri (`__main__.py:46-54`). Odoo `check_caller` mewajibkan tepat
  satu company, `company_code`/run milik PT lain ditolak `company_mismatch` 403 (`forecast_api.py:159-163`,
  `efn_forecast_run.py:76,135`), dan gudang dibatasi ke company pemanggil (`:292`). Query ClickHouse terparametrisasi dan
  difilter `company_code`/`company_id` (`source.py:36-65`). `run_id` memakai uuid4 per batch. Reader ClickHouse membaca view
  `efn_forecast.*` semua PT; ini memang diperlukan, dan isinya agregat penjualan/stok tanpa PII.
- **Container.** User 65534, `read_only`, tmpfs terbatas, `cap_drop: [ALL]`, `no-new-privileges`, network `internal: true`.

## Belum diverifikasi

- Tidak menjalankan test atau compose (reviewer tanpa shell). Hasil verifikasi lokal yang dilaporkan pemanggil tidak diulang.
- Isi runbook `forecast-worker.md` untuk langkah pembuatan user `efn_forecast_pb` (grup tunggal, satu company, durasi key) tidak
  dibaca baris per baris.
