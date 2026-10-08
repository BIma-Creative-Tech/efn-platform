# Redaksi PII & rahasia di log (Promtail → Loki)

Gelombang 15 (9 Okt 2026). Menutup SEC-72, SEC-73 dan sisi infra SEC-78 (`docs/reviews/SECURITY_REVIEW_2026-10-09-r8.md`).
Risiko: R-L01 (PII di log), R-S03/R-S10 (token di URL), UU PDP 27/2022. Audiens: on-call ops, DevSecOps, DPO.

> Prinsip (CLAUDE.md): data pribadi **di-mask di sumber**. Promtail hanyalah **jaring kedua**. Bila redaksi
> Promtail menangkap sesuatu, berarti ada komponen yang mencatat nilai mentah. Perbaiki komponen itu.

## 1. Apa yang diredaksi

Berlaku untuk semua stream container, kecuali `efn_audit_sink` dan `efn_audit_anchor`. Kedua stream itu
bertanda tangan HMAC/hash, sehingga redaksi akan merusak verifikasi. PII di sana wajib dicegah di sumber (SEC-78,
Odoo). Pola yang sama dipakai di `promtail-journal.yml` (journal host, prod).

| Tahap | Pola (ringkas) | Hasil |
|---|---|---|
| (a) query/logfmt | `token`, `access_token`, `refresh_token`, `id_token`, `auth_token`, `password`, `passwd`, `pwd`, `csrf_token`, `secret`, `client_secret`, `api_key`, `apikey`, `signature`, `x-amz-signature`, `x-amz-credential`, `x-amz-security-token`, `otp`, `otp_code` `=nilai`; prefiks `?`, `&`, spasi, `\u0026` (JSON Traefik), `%3F`/`%26`; `=` boleh `%3D` | `token=[REDACTED]` |
| (a2) khusus query | `code`, `key`, `sig`, `auth`, `session_id`, `sid`, hanya setelah `?`/`&`/`;`/`\u0026` | `?code=[REDACTED]` |
| (b) JSON/dict | `"password": "…"`, `'token': '…'`, `otp`, `secret`, `api_key` | `"token": "[REDACTED]"` |
| (c) Authorization | `Bearer <token>`, `Basic <base64>` (wajib memuat digit/`+`/`/`/`=`) | `Bearer [REDACTED]` |
| (d) Nomor HP (2×) | `08…`, `62…`, `+62…`, `(+62)…`, `%2B62…`, dengan pemisah opsional spasi/titik/strip; batas kiri & kanan non-alfanumerik | `[PHONE_REDACTED]` |

Yang **sengaja tidak** diredaksi:
- bentuk ter-mask `62812****789`;
- potongan UUID/hash/ID container yang diapit huruf hex;
- ID thread, durasi `0.81…`;
- kata biasa "Basic authentication" / "token expired".

### Batas yang diketahui
- Nomor yang menempel huruf di kedua sisinya (`nomor081234…x`) atau berpemisah tidak lazim (`08 12…`, `%20`) lolos.
  Segmen UUID yang kebetulan seluruhnya digit dan berpola `08…` ikut terganti (kosmetik).
- Nama parameter di luar daftar (mis. `discount_token`, `reset_key`) tidak diredaksi. Tambahkan ke tahap (a) dan fixture.
- Teks bebas (nama orang, alamat, email) **tidak** diredaksi. Itu wajib dicegah di sumber.
- Log mentah container di host (driver `json-file`, 20 MB × 5 per container, root-only) tidak ikut teredaksi.
  Promtail hanya menyaring yang dikirim ke Loki.
- Traefik tidak dapat meredaksi query per parameter atau per router. `RequestPath` tetap dicatat (perlu untuk
  investigasi) dan redaksi dilakukan di Promtail. Header tidak dicatat (`fields.headers.defaultMode: drop`).
- Odoo: `log_handler = :INFO,werkzeug:WARNING` (`services/odoo/odoo.conf.tpl`). Baris request werkzeug (URL + query)
  tidak dicatat di semua lingkungan. Bila level diturunkan untuk debugging, tahap (a) tetap berlaku.

## 2. Mengubah pola (wajib lewat uji)

1. Ubah tahap (a)–(d) di `infra/observability/promtail.yml`. Salin **persis** ke `promtail-journal.yml`.
2. Tambahkan baris fixture fiktif di `infra/observability/tests/promtail/fixtures/*.txt`. Nilai rahasia diberi awalan
   `SENTINEL`, nomor HP rekaan. Jangan memakai data nyata.
3. Jalankan uji:
   ```bash
   bash scripts/test-promtail-redaction.sh            # cek
   bash scripts/test-promtail-redaction.sh --update   # bila perubahan disengaja -> tinjau diff expected.txt baris per baris
   ```
4. CI menjalankan job `promtail-redaction` (`.github/workflows/ci.yml`): `-check-syntax` dan uji fixture. Uji gagal bila:
   - keluaran berbeda dari `expected.txt`;
   - ada `SENTINEL*` yang lolos;
   - pipeline journal ≠ pipeline docker;
   - ada entri yang hilang.
5. Deploy: `docker compose … up -d --force-recreate promtail` (file di-bind-mount, `restart` saja tidak cukup).
   Posisi Promtail ada di tmpfs, sehingga log lama dibaca ulang:
   - entri > ±3 jam ditolak Loki (`entry too far behind`, wajar);
   - entri dalam jendela itu dikirim ulang dengan redaksi baru, sehingga muncul duplikat yang berbeda isi.

## 3. Deteksi kebocoran di Loki

Jalankan di Grafana Explore (datasource Loki) atau dengan `curl` dari namespace container `loki` (dev).

```logql
# Nomor utuh berformat apa pun yang lolos (cek berkala; harus 0 kecuali false positive UUID)
sum by (service) (count_over_time({container=~".+", stream=""} |~ `(^|[^0-9A-Za-z+])(\+?62\)?[\s.-]{0,2}|0)8[1-9][0-9]{1,2}[\s.-]?[0-9]{3,4}[\s.-]?[0-9]{3,5}([^0-9A-Za-z]|$)` [1d]))
# Sisa bug G14 (sebelum 8 Okt 16:16 UTC): awalan teredaksi, ekor nomor bocor
sum by (service) (count_over_time({container=~".+"} |~ `\[PHONE_REDACTED\]\d{6}` [30d]))
# Rahasia di query string yang lolos
sum by (service) (count_over_time({service=~"traefik|odoo|api-gateway|s3-gateway"} |~ `(?i)[?&](token|access_token|password|signature|code)=[^\[&]` [1d]))
```

Bila hasilnya tidak nol di **staging/production**:
1. Perlakukan sebagai dugaan kegagalan pelindungan data pribadi → [breach-response-72h.md](breach-response-72h.md).
   Catat T0.
2. Telusuri sumbernya dengan `sum by (service, container)`, lalu perbaiki di komponen tersebut (mask di sumber).
3. Bila pola Promtail yang kurang, ikuti §2.
4. Hapus baris yang bocor (§4).

## 4. Menghapus baris bocor dari Loki (delete API)

Prasyarat (sudah di `loki.yml`): `compactor.retention_enabled: true`, `delete_request_store: filesystem`.
`deletion_mode` default Loki 3.x adalah `filter-and-delete`. API berada di Loki loopback `127.0.0.1:3101`. Sidecar
`loki-tls` hanya menerima CN promtail/grafana/prometheus, sehingga API dijalankan dari namespace jaringan container
`loki` (akses host = Superadmin/ops).

```bash
# dev; di host prod ganti nama container sesuai `$DC ps`
START=$(date -d '2026-09-08T00:00:00Z' +%s); END=$(date -d '2026-10-08T17:46:00Z' +%s)
docker run --rm --network container:efn-loki-1 curlimages/curl:8.10.1 -s -w 'HTTP %{http_code}\n' -X POST -G \
  http://127.0.0.1:3101/loki/api/v1/delete \
  --data-urlencode 'query={container=~".+", stream=""} |~ `\[PHONE_REDACTED\]\d{6}`' \
  --data-urlencode "start=$START" --data-urlencode "end=$END"          # 204 = diterima
docker run --rm --network container:efn-loki-1 curlimages/curl:8.10.1 -s http://127.0.0.1:3101/loki/api/v1/delete   # status
```

- `stream=""` mengecualikan stream audit (`efn_audit_sink`/`efn_audit_anchor`) dan `efn_loyalty`. Menghapus dari stream
  audit memicu alarm integritas, jadi lakukan hanya dengan persetujuan DPO + Superadmin dan catat di register.
- Request diproses compactor setelah `delete_request_cancel_period` (24 jam). Dalam jangka itu request dapat dibatalkan
  (`DELETE /loki/api/v1/delete?request_id=…`). Verifikasi dengan query §3 setelah ±25 jam.
- Tindakan ini merusak bukti forensik. Ekspor dulu (terenkripsi `age`) bila insiden masih diinvestigasi.
- Alternatif tanpa API: biarkan retensi 30 hari habis.

## 5. Status dev (G15, 9 Okt 2026), dicatat sebagai near-miss

- Sumber baris `[PHONE_REDACTED]\d{6}` di Loki dev, per container (query §3, 30 hari):
  - **postgres** (±300 kemunculan, sumber nyata nomor HP): `log_statement=all` dev mencetak INSERT
    `res_partner.phone`/`pos_order.mobile` dari fixture smoke "SMOKE Member A–N" (fiktif). **Diperbaiki di sumber**:
    dev kini `log_statement=${PG_LOG_STATEMENT:-ddl}` (`docker-compose.dev.yml`). Berlaku setelah postgres di-recreate.
  - **efn-g14-odoo / efn-g14-promtail**: baris uji sintetis G14 (fiktif).
  - Sisanya **false positive** pola lama (UUID report, hash, ID container, ID thread, durasi):
    - ocr-worker / api-gateway / s3-gateway: UUID report;
    - cadvisor, docker-socket-proxy, promtail, loki;
    - etl: `row_hash` lama sebelum G14.
- Access log Traefik: **tidak ada** nomor HP maupun `token=`/`password=`/`code=`/`signature=` dalam 30 hari terakhir.
  Probe R2 `member_ref=` (E2E_SMOKE_REPORT) sudah di luar retensi atau tidak pernah tercatat di Traefik.
- Penghapusan via delete API (§4) **belum dijalankan**. Butuh persetujuan operator (eksekusi ditolak di sesi agent).
  Tanpa itu, baris habis oleh retensi ±7 Nov 2026.
- Sebelum staging pertama menerima log: jalankan ketiga query §3 di staging. Hasil harus 0.
