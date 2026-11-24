# Security Review G66 Production Planning di atas ESB (`services/prodplan`) — 24 Nov 2026

Reviewer: efn-security-reviewer (read-only terhadap kode). Worktree `E:\Projects\efn-platform-prodplan`, branch
`feat/production-planning` (belum di-commit, basis `origin/main`). Cakupan: `services/prodplan/efn_prodplan/*`,
insight-web (`app/api/prodplan/**`, `lib/prodplan-*.ts`, `lib/origin-guard.ts`, `lib/config.ts` bagian PP), Odoo
`efn_insight` (bagian PP) + `efn_role/models/role_policy.py`, `infra/compose/docker-compose.prodplan.yml`,
`infra/postgres/init/23-prodplan-g66.sql`, `00-roles.sh`, `pg_hba.conf`, `scripts/secrets.list`, potongan
`scripts/check-encryption.sh`. Acuan: CLAUDE.md, docs/04 §30, ADR-051 (Proposed).

Nomor temuan: **SEC-225..233** (lanjutan SEC-224, nomor tertinggi di `docs/reviews/` worktree ini). Catatan: ada review
`SECURITY_REVIEW_2026-11-22_esb-aggregates.md` yang belum di-commit di worktree lain dan tidak dibaca. Bila review itu
memakai SEC-225+, nomor di sini perlu disesuaikan saat merge.

Catatan metode: review statis. Tidak ada test yang dijalankan dan tidak ada `git diff`. File dibaca langsung dari worktree.
Hasil verifikasi nyata dari lead (pytest, vitest, E2E staging, replay 401, `write_disabled`, `digest_mismatch`) diterima
dan tidak diulang.

## Ringkasan eksekutif

- **Kunci tulis production tidak bisa dilewati.** `PROD_WRITE_UNLOCKED=False` adalah konstanta kode. Base URL hanya dua
  nilai persis. Transport memakai `http.client` tanpa proxy dan tanpa redirect. Allowlist method+path ditegakkan sebelum
  socket dibuka. Tidak ditemukan Critical.
- **Satu temuan High (SEC-225) bisa menggandakan dokumen di ERP klien.** Rekonsiliasi dokumen produksi hanya membaca
  halaman pertama daftar ESB. Dokumen yang sudah ada bisa dinyatakan "tidak ditemukan", lalu tombol "kirim ulang" membuat
  dokumen kedua. Pada mode `actual`, stok bergerak dua kali. SEC-226 (Medium) memperparah: kirim ulang juga diizinkan saat
  nomor dokumen sudah tercatat, sehingga dokumen lama menjadi yatim dan tidak terhapus saat rencana dibatalkan.
- **SoD hanya memeriksa pembuat dan pengaju (SEC-227, Medium).** User yang memegang kedua peran bisa menulis isi rencana
  (demand, hitung, terima usulan) lalu menyetujuinya sendiri.
- **Putusan:** boleh merge selama tulis production tetap terkunci. SEC-225 dan SEC-226 wajib ditutup sebelum PR
  pembuka kunci (L-250). SEC-227 juga wajib sebelum production. Sisanya backlog.

## Status tindak lanjut (efn-lead, 24 Nov 2026, setelah putaran perbaikan)

| ID | Status | Bukti |
|---|---|---|
| SEC-225 | Ditutup | Rekonsiliasi berpaginasi; test dokumen ber-marker di halaman 2; pemulihan baris tersangkut terbukti di staging |
| SEC-226 | Ditutup | Nomor dokumen tidak bisa ditimpa (SQL); resend 409 bila nomor ada; uji staging `resend` dokumen confirmed → 409 |
| SEC-227 | Ditutup | Uji staging: penyunting dua-peran ditolak 403 `sod_violation` saat menyetujui |
| SEC-228 | Ditutup | `efn_role` 19.0.1.2.4; test Odoo 139 lulus |
| SEC-229 | Terbuka (dampak nol selama hanya `pb`) | Wajib ditutup sebelum brand kedua |
| SEC-230 | Sebagian | Race cancel vs worker ditutup; pembatalan rencana `posted` oleh satu planner tetap sesuai kontrak (Q-PP7) |
| SEC-231 | Ditutup | Qty ≤ 0 dilewati + ditolak di payload (422 `invalid_quantity`) |
| SEC-232 | Ditutup | UI menampilkan nomor dokumen |
| SEC-233 | Terbuka | Rantai image bertanda tangan + allowlist egress nftables = tindak lanjut (runbook §9) |

Temuan tambahan dari uji staging (bukan dari review statis): ESB memakai ulang nomor dokumen yang dihapus → sebelum
perbaikan, pembatalan bisa menghapus dokumen milik orang lain yang kebetulan mendapat nomor sama. Ditutup dengan cek marker
sebelum DELETE + indeks unik hanya untuk dokumen hidup (docs/04 §30.16), dengan test.

## Tabel temuan

| ID | Severity | Area | File:baris | Ringkas | Risiko |
|---|---|---|---|---|---|
| SEC-225 | P1 / High | outbox rekonsiliasi | `efn_prodplan/outbox.py:100-122` (`client.get` baris 109); `service.py:1032-1056` | Rekonsiliasi hanya membaca halaman pertama daftar ESB. Produksi: tanpa filter marker. Transfer: filter `additionalInfo` belum terbukti dihormati ESB. Hasilnya `not_found_after_timeout` palsu, lalu resend menggandakan dokumen | R-O10, docs/04 §30.9 |
| SEC-226 | P2 / Medium | outbox + resend | `outbox.py:242-247,275-277`; `esb.py:562-565`; `service.py:1042-1054`; `store.py:201-210` | `get_doc` gagal sementara/envelope `fail` membuat `sent` turun ke `unknown`, lalu `failed`. Resend tetap diizinkan walau `esb_doc_number` sudah ada. POST baru menimpa nomor sehingga dokumen pertama yatim dan tidak dihapus saat cancel | R-O10 |
| SEC-227 | P2 / Medium | SoD | `workflow.py:109-112`; `service.py:540-606,668-732,772,800,816-835` | `check_sod` hanya membandingkan dengan `created_by_sub`/`submitted_by_sub`. Penulis demand/hitung/usulan dan pengaju lama (dihapus `return_to_draft`) tidak dicek | ADR-051 §3.1, Q-PP7 |
| SEC-228 | P3 / Low | Odoo efn_role | `efn_role/models/role_policy.py:26-46`; `efn_insight/security/efn_insight_security.xml:126-136` | Grup PP belum masuk `SENSITIVE_GROUP_XMLIDS`, sehingga bisa ikut sinkron jabatan. Ini bertentangan dengan §30.7 ("tidak ikut sinkron jabatan"). Pola sama dengan SEC-193 | R-S16 |
| SEC-229 | P3 / Low | Odoo efn_insight | `efn_insight/controllers/main.py:211-228`; `service.py:239-240` | Peran PP global per user, tidak per PT. Planner PB yang juga punya company WTZ otomatis menjadi planner WTZ begitu brand itu diaktifkan | R-S22 |
| SEC-230 | P3 / Low | cancel / DELETE ESB | `workflow.py:25`; `service.py:999-1030`; `store.py:174-199` | Satu planner bisa membatalkan rencana `posted` dan memicu DELETE dokumen produksi ESB yang sudah Authorized, tanpa persetujuan kedua. Ada juga race cancel vs claim worker yang bisa meninggalkan dokumen terkirim untuk rencana batal (PLAUSIBLE) | R-O10 |
| SEC-231 | P3 / Low (PLAUSIBLE) | validasi qty | `jobs.py:244-247`; `calc.py:250-257`; `payloads.py:94-97` | Qty baris BOM dari ESB yang ≤ 0 tidak ditolak. Hasilnya `totalQty` ≤ 0 di payload produksi. `materials` jsonb tanpa CHECK | R-O10 |
| SEC-232 | Info | pemetaan galat | `service.py:881,895`; `insight-web/lib/prodplan-errors.ts:77` | Server mengirim `details.doc_numbers`, klien hanya menerima `docs`/`doc_nums`. Nomor dokumen penghambat tidak pernah tampil (fungsional, fail closed) | – |
| SEC-233 | Info | hardening | `esb.py:381-391`; `docker-compose.prodplan.yml:7-8,255-257`; `insight-web/lib/prodplan-guard.ts:68-77` | Sisa risiko: kunci L-250 tidak dicek ulang di `EsbClient`; overlay di luar rantai image bertanda tangan; secret staging bisa menunjuk akun esb-extract; body dibaca penuh sebelum cek ukuran bila tanpa Content-Length; teks bebas bisa memuat data pribadi | R-S17, PDP |

Pemetaan prioritas: P0 = blokir rilis apa pun, P1 = blokir production (di sini: blokir pembukaan kunci tulis production),
P2 = perbaiki sebelum production/aktivasi luas, P3 = backlog hardening.

---

## Detail

### SEC-225 (P1 / High) — Rekonsiliasi hanya membaca halaman pertama, jadi resend bisa menggandakan dokumen ESB

**Bukti (dari kode).**
- `find_by_marker` (`outbox.py:100-122`) memanggil `client.get(DOC_BASE[kind], params)` (baris 109). Pemanggilan itu
  **tanpa** `page`/`limit` dan **tanpa** `get_all`. Yang dibaca hanya halaman default pertama dari respons ESB.
- Untuk `simple_manufacturing`, parameternya hanya `dateFrom/dateTo/branchID` (baris 105-106), tanpa filter marker.
  Satu rencana bisa menghasilkan hingga `PRODPLAN_POST_MAX_DOCS=60` dokumen produksi (L-257), ditambah dokumen manual CK.
  Dalam jendela 3 hari (`production_date ± 1`), jumlah dokumen di cabang CK hampir pasti melebihi satu halaman default.
- Untuk transfer, filter `additionalInfo=<marker>` dikirim, tetapi tidak ada bukti di repo bahwa ESB benar-benar memfilter
  dengan parameter itu (docs/04 §30.9 hanya menuliskannya). Bila ESB mengabaikan parameter yang tidak dikenal, masalahnya
  sama (PLAUSIBLE untuk transfer, TERBUKTI untuk produksi).
- Bila dokumen tidak ditemukan dan `now - sent_at ≥ grace`, baris menjadi `failed/not_found_after_timeout`
  (`outbox.py:275-277`). Itu satu-satunya status yang boleh di-resend (`service.py:1042-1054`). Resend mengembalikan baris
  ke `pending`, lalu worker mem-POST ulang (`outbox.py:190-196`).

**Skenario.** POST `assembly-actual` sampai ke ESB dan dokumen tercipta (stok langsung bergerak), tetapi respons timeout,
sehingga baris menjadi `unknown`. CK sudah punya lebih dari satu halaman dokumen produksi dalam 3 hari, dan dokumen baru
kita ada di halaman 2. Rekonsiliasi tidak menemukannya, dan setelah 300 detik baris menjadi `failed`. Planner melihat "tidak
ditemukan" lalu menekan "Kirim ulang". Akibatnya ada dokumen produksi kedua: bahan berkurang dua kali, produk jadi bertambah
dua kali. Dokumen pertama juga tidak pernah tercatat di outbox, jadi tidak terhapus saat cancel.

**Rekomendasi minimal.**
1. Ganti baris 109 dengan `client.get_all(DOC_BASE[kind], params, limit=200, max_pages=N)`. Bila halaman habis tanpa
   kepastian (`EsbProtocolError` melebihi halaman), **jangan** turunkan baris ke `failed`; tetap `unknown` dan biarkan alert
   `EfnProdplanOutboxStuck` menyala.
2. Transfer: uji di staging bahwa `additionalInfo` benar-benar memfilter (buat dua dokumen, filter satu marker). Bila tidak,
   perlakukan sama dengan produksi (paginasi penuh + cocokkan marker).
3. Test regresi: ESB palsu dengan dokumen ber-marker di halaman 2 harus menghasilkan `sent`, bukan `failed`.
4. UI resend: tampilkan peringatan bahwa resend membuat dokumen baru dan minta pengguna memeriksa ESB secara manual dulu
   (nomor marker ditampilkan agar bisa dicari di ESB).

### SEC-226 (P2 / Medium) — Resend tidak memeriksa nomor dokumen yang sudah tercatat; dokumen lama menjadi yatim

**Bukti.**
- `_confirm` (`outbox.py:242-247`): bila `get_doc` mengembalikan `None` sampai lewat grace, status turun ke `unknown`
  dengan `esb_doc_number` **tetap terisi**. `get_doc` mengembalikan `None` bukan hanya saat 404, tetapi juga saat
  envelope `status != ok` ("toleran", `esb.py:562-565`).
- `_reconcile`: `sent_at` sudah lama, jadi bila marker tidak ditemukan (lihat SEC-225, atau masalah detail endpoint yang
  sama), baris **langsung** menjadi `failed/not_found_after_timeout` (`outbox.py:275-277`).
- `resend` (`service.py:1042-1054`) tidak memeriksa `esb_doc_number IS NULL`. Setelah POST baru, `_finish` menulis
  `esb_doc_number` baru (`outbox.py:223-232`) lewat `store.update` yang mengizinkan kolom itu ditimpa (`store.py:201-210`).

**Skenario.** Dokumen `SM… - 1` berhasil dibuat. GET detail sempat membalas envelope `fail` (gangguan ESB, atau format
nomor ber-spasi) selama lebih dari 5 menit. Baris menjadi `unknown`, lalu `failed`, lalu planner melakukan resend. Hasilnya
dokumen kedua dibuat, nomor pertama terhapus dari outbox, dan cancel hanya menghapus dokumen kedua.

**Rekomendasi minimal.** (a) `resend` menolak (409) bila `esb_doc_number` tidak NULL. (b) `_confirm` yang memegang nomor
dokumen tidak turun ke `unknown/failed`; tetap `sent` dengan backoff, plus alert. (c) `store.update` (atau trigger DB)
menolak menimpa `esb_doc_number` yang sudah non-NULL dengan nilai lain.

### SEC-227 (P2 / Medium) — SoD tidak mencakup penulis isi rencana

**Bukti.** `check_sod(actor, created_by_sub, submitted_by_sub)` (`workflow.py:109-112`) dipanggil di `decide_transfer`
(`service.py:772`) dan `approve` (`:800`). Aksi yang menentukan isi dokumen ESB (`put_demand` `:540-606`,
`decide_suggestion` `:668-732`, `calculate`/`import_pr` `:630-649`) hanya mensyaratkan peran `pp_planner`, bukan pembuat.
`return_to_draft` mengosongkan `submitted_by_sub` (`:828-833`), sehingga riwayat pengaju hilang dari pemeriksaan. CHECK DB
`plan` juga hanya mencakup dua kolom itu. Kontrak mengizinkan satu user memegang dua peran (Q-PP7).

**Skenario.** A (planner) membuat rencana. X (planner + kepala gudang) mengganti demand (mis. menaikkan qty) dan menghitung.
A mengajukan tanpa memeriksa ulang, lalu X menyetujui semua transfer dan rencana. Pemeriksaan SoD lolos karena X bukan
pembuat maupun pengaju, padahal X adalah penulis isi sekaligus penyetuju.

**Rekomendasi minimal.** Catat penyunting isi per rencana (mis. kolom `editor_subs integer[]` yang ditambah oleh
`put_demand`, `decide_suggestion`, `import_pr`, `calculate`, dan pengaju sebelum `return_to_draft`). Tolak `approve` dan
`decide_transfer` bila aktor ada di himpunan itu. Alternatifnya, query `audit_event` aksi isi milik aktor untuk plan
tersebut. Tambah test negatif.

### SEC-228 (P3 / Low) — Grup PP belum sensitif di efn_role

`role_policy.py:26-46` belum memuat `efn_insight.group_prodplan_planner` maupun `efn_insight.group_prodplan_warehouse`.
Akibatnya peran ber-`sync_from_job` boleh memuat grup PP, dan perubahan jabatan di HR bisa memberi hak tulis ke ERP klien.
docs/04 §30.7 dan README `efn_insight` menyatakan grup PP "tidak ikut sinkron jabatan", tetapi pernyataan itu tidak
ditegakkan kode. **Perbaikan:** tambahkan kedua xmlid ke tuple (pola SEC-193) dan masukkan pemegangnya ke review akses
bulanan.

### SEC-229 (P3 / Low) — Peran PP berlaku untuk semua PT ESB yang dimiliki user

`apply_pp_roles` (`controllers/main.py:211-228`) menambah peran ke seluruh `codes`. prodplan mengizinkan brand mana pun
yang kodenya ada di `codes` (`service.py:239-240`). Saat ini dampaknya nol karena `PRODPLAN_BRANDS=pb`. **Sebelum brand
kedua diaktifkan:** buat klaim `pp_codes` terpisah (PT tempat peran PP berlaku) atau grup per PT, atau catat sebagai
keterbatasan L-xxx yang diterima pemilik proyek.

### SEC-230 (P3 / Low) — Cancel = tulis destruktif ke ERP oleh satu orang; race cancel vs worker

- `cancel` diizinkan dari `posted` (`workflow.py:25`) hanya dengan peran P (`service.py:1000`). Cancel memicu DELETE dokumen
  produksi ESB yang sudah Authorized dan sudah menggerakkan stok, tanpa SoD (sesuai kontrak, tetapi tetap risiko bisnis).
  Saran: cancel rencana yang memiliki outbox `confirmed` mensyaratkan peran W dan bukan pembuat (SoD yang sama dengan
  approve).
- Race (PLAUSIBLE): `cancel` menghitung baris "sibuk" tanpa mengunci baris outbox (`service.py:1006-1012`). Worker bisa
  meng-claim `pending → sending` (`store.py:174-188`) di antara hitung dan `UPDATE … WHERE status='pending'`. Rencana
  menjadi `cancelled`, tetapi dokumen tetap terkirim lalu `confirmed` dan tidak pernah dijadwalkan DELETE. Saran:
  `SELECT … FROM outbox WHERE plan_id=%s FOR UPDATE` sebelum hitung; atau di `_confirm`, bila plan `cancelled`, langsung
  `delete_pending`.

### SEC-231 (P3 / Low, PLAUSIBLE) — Qty BOM ≤ 0 lolos ke payload ESB

`parse_bom_detail` menerima qty apa pun yang finite (`jobs.py:244-247`). Ledakan BOM mengalikan qty tanpa cek tanda
(`calc.py:250-257`). `materials[].total_qty` masuk payload `simpleManufacturingMaterials.totalQty` (`payloads.py:94-97`)
tanpa CHECK, karena `materials` berupa jsonb. Baris produksi WIP ber-qty ≤ 0 gagal di CHECK DB (job gagal, aman), tetapi
bahan ber-qty negatif tidak tertahan. Bila ESB menerima `totalQty` negatif, stok bahan justru bertambah. **Perbaikan:**
tolak `qty <= 0` di `parse_bom_detail` (`parse_error`) dan assert semua qty payload > 0 di `manufacturing_body`/body
transfer.

### SEC-232 (Info) — Nama kunci `details` tidak cocok

`_transfers_authorized` mengirim `details={"doc_numbers": [...]}` (`service.py:881,895`), sedangkan `sanitizeDetails`
hanya membaca `docs`/`doc_nums` (`prodplan-errors.ts:77`). Pengguna tidak melihat nomor dokumen yang belum di-authorize.
Tidak ada kebocoran (fail closed). Samakan kontrak, atau tambahkan `doc_numbers` ke sanitizer.

### SEC-233 (Info) — Sisa risiko & hardening

1. `EsbClient.__init__` (`esb.py:381-391`) tidak memeriksa ulang `check_write_lock` saat `allow_write=True` dan base
   production. Kunci saat ini sudah tertutup lewat `Settings.from_env`. Pemeriksaan kedua di klien murah untuk
   pertahanan berlapis.
2. Overlay prodplan belum masuk rantai image bertanda tangan `deploy.sh` (`docker-compose.prodplan.yml:7-8`). Image hasil
   build lokal tidak diverifikasi tanda tangannya. Tutup sebelum VPS/production.
3. `PRODPLAN_ESB_PASSWORD_PB_SECRET_FILE` dicontohkan menunjuk `esb_core_password_stg.txt` (`:255-257`). Itu berarti akun
   staging esb-extract (GET-only menurut ADR-040) ternyata punya hak tulis, atau dua service berbagi kredensial. Hanya
   staging, tetapi pemisahan R-S17 sebaiknya juga berlaku di staging.
4. `readJsonBody` membaca seluruh body (`req.text()`) sebelum cek ukuran bila request tanpa `Content-Length`
   (`prodplan-guard.ts:68-77`). Pengguna harus terautentikasi dan sudah lewat rate limit; dampaknya hanya memori Node.
5. Field teks bebas (`notes`, `cancel_reason`, `reject_reason`, `reason` di audit) bisa memuat data pribadi dan ikut retensi
   1825 hari. Tambahkan catatan di PDP inventory §2.8.15 dan petunjuk UI "jangan isi data pribadi".
6. Egress `egress-prodplan` terbuka ke internet; allowlist host hanya di aplikasi (sisa risiko yang sama dengan SEC-183).

---

## Kontrol yang sudah benar (terverifikasi dari kode)

- **Kunci L-250:** konstanta `PROD_WRITE_UNLOCKED=False` (`config.py:18`), `preflight` selalu dijalankan (juga saat
  nonaktif), dan proses exit 2. Tidak ada jalur env, CLI (`check-config`/`serve` saja), atau redirect yang melewatinya.
  Outbox difilter `esb_env` proses, jadi baris staging tidak terkirim ke production.
- **Allowlist ESB** (`esb.py:167-205`): path dengan charset terbatas, tanpa `..`/`//`, maks 200 karakter, regex per
  template. Nomor dokumen divalidasi lalu di-encode kanonik (`%2F` untuk `/`, `%20` untuk spasi; bentuk non-kanonik
  ditolak). POST produksi hanya satu endpoint per mode, dan POST/DELETE butuh `allow_write`. Klien baca dan klien job dibuat
  dengan `allow_write=False`. URL akhir wajib berawalan base yang diizinkan. `http.client` langsung: tanpa proxy env,
  tanpa redirect, TLS ≥ 1.2 dengan verifikasi hostname.
- **Tanpa retry POST otomatis:** `sending` di-commit sebelum POST. Gagal sebelum kirim (`EsbNotSent`) dibedakan dari
  gagal setelah kirim (`EsbSentUnknown`). Crash saat `sending` menjadi `unknown`, bukan kirim ulang. Satu-satunya re-POST
  setelah terkirim adalah resend manusia (lihat SEC-225/226).
- **DELETE hanya untuk dokumen milik prodplan:** nomor hanya berasal dari respons POST atau rekonsiliasi marker. Marker
  48 bit acak `secrets`, UNIQUE, dan tidak dikirim ke browser. `known_numbers` mencegah satu nomor diklaim dua baris. Tidak
  ada input pengguna yang menjadi nomor dokumen ESB.
- **`ppa1`:** HMAC constant-time sebelum parse; 11 kunci berurutan dengan kunci ganda ditolak; `aud`/TTL 60 detik/skew
  30 detik; terikat method + path+query mentah + hash body; cache `jti` fail closed saat penuh. Peran dan PT berasal dari
  sesi terverifikasi, bukan input browser. Kunci wajib berbeda dari kunci SSO/sesi/scope (`config.ts:148-171`).
- **Otorisasi objek:** setiap akses plan lewat `_visible` (env + brand ∈ `codes`). `get_job` dan `resend` memeriksa plan
  induk. Anak (suggestion/transfer) di-query dengan `AND plan_id = %s`. Tidak ditemukan IDOR.
- **Workflow:** `FOR UPDATE` + `row_version` di setiap mutasi. Job menulis hasil hanya bila `draft` dan `row_version`
  sama. `put_demand` hanya di `draft`. Digest pratinjau dihitung ulang di server dari DB.
- **SQL:** semua f-string `# noqa: S608` hanya menyisipkan konstanta (`PLAN_COLUMNS`, potongan `where` tetap, nama kolom
  dari allowlist `_OUTBOX_FIELDS`). Semua nilai lewat parameter. Tidak ada SQL injection.
- **PostgreSQL:** owner NOLOGIN, role aplikasi hanya DML, `audit_event` hanya SELECT/INSERT. Fungsi `SECURITY DEFINER`
  memakai `search_path = pg_catalog, prodplan`, tabel schema-qualified, batas 1825 hari tetap, EXECUTE hanya untuk
  `efn_prodplan`, dan `REVOKE ALL ON DATABASE … FROM PUBLIC` (tanpa TEMP). `hostssl` + SCRAM, klien `verify-full`.
- **Container/jaringan:** non-root 65534, `read_only`, `cap_drop: ALL`, `no-new-privileges`, batas memori, image dipin
  digest, tanpa port host. API plaintext hanya di loopback. ghostunnel mTLS dengan `--allow-cn` persis. `prodplan-db` dan
  `insight-plan` internal. Secret ESB prodplan terpisah dari esb-extract, dan username tidak ada di repo (SEC-180 tetap
  tertutup).
- **Log:** `log_event` memakai allowlist kunci, sehingga password/token/header aktor/query/body tidak bisa masuk log. Akses
  log stdlib dimatikan. Galat API generik tanpa stack/SQL/URL ESB. insight-web memetakan pesan dari tabel sendiri dan
  menyaring `details`.
- **insight-web:** mutasi lewat daftar aksi tertutup (tanpa penerusan path bebas), `Sec-Fetch-Site: same-origin` atau
  Origin = Host, Content-Type JSON, rate limit per user, `PRODPLAN_URL` hanya `http://127.0.0.1:7443`, `redirect: 'error'`,
  dan kunci hanya di server (`server-only`).
- **Odoo:** PP hanya di eit3. T9 ditegakkan dua sisi (peran dibuang bila `brs` ada atau tanpa kode ESB). Log hanya uid.

## Belum diperiksa / belum terverifikasi

- `jobs.save_result` dan `calc.py` tidak dibaca seluruhnya (kebenaran aritmetika §30.5 di luar cakupan security).
- Halaman/komponen `app/(pp)/**`, `components/pp/**`, `proxy.ts`, `claims.ts`, `lib/insight_token.py` hanya dibaca
  sebagian (React meng-escape output; tidak ada `dangerouslySetInnerHTML` yang dicari secara khusus).
- `scripts/gen-pki.sh` (EKU/SAN sertifikat `insight-web-pp`/`prodplan`) dan seluruh `check-encryption.sh` tidak dibaca rinci.
- Perilaku ESB nyata: default page size list, filter `additionalInfo`, apakah `totalQty` negatif diterima, dan apakah DELETE
  dokumen Authorized ditolak.
- Tidak ada test yang dijalankan.
