# Security Review — branch `fix/insight-review` (review Insight 21 Nov 2026)

- Tanggal: 21 Nov 2026 · Reviewer: efn-security-reviewer (read-only)
- Cakupan: commit 19c1fac, 0fcbd0d, 50c83d4, f96e501 (terhadap `origin/main`), ditambah satu temuan lama dari lead (row policy `efn_insight_scope`)
- Acuan: CLAUDE.md, docs/04 §26.7/§26.7a/§26.13, ADR-040/041 (Proposed), risk register docs/01 §4.3–4.4
- Metode: baca kode statis di working tree branch. **Tidak ada shell.** Diff git, test (vitest/pytest), dan uji ClickHouse tidak dijalankan reviewer. Verifikasi policy di efn-prod diambil dari laporan lead.

## Ringkasan eksekutif

Perubahan di branch ini aman untuk di-merge. Kedua view baru (`v_esb_movement_daily`, `v_esb_coverage`) tercakup row policy `efn_insight_scope` lewat daftar eksplisit di `clickhouse-init.sh` dan gerbang fail-closed. Semua query baru memakai konstanta, parameter bertipe, dan `has({pt:Array(String)}, company_code)`. Ekspor `mov-category` memakai jalur ekspor yang sudah ada (cakupan sesi, rate limit, escape formula). Tidak ditemukan jalur XSS baru. Temuan terberat bersifat **pre-existing** (SEC-IR-01, High): kunci `insight_scope_key` (dan `text2sql_scope_key`) ditanam literal di ekspresi row policy, sehingga ikut tercetak di pesan galat ClickHouse. Kunci efn-prod lokal sudah bocor ke transkrip dan harus dirotasi. Temuan dari perubahan ini hanya Low/Info.

## Tabel temuan

| ID | Severity | Lokasi | Ringkas | Risiko |
|---|---|---|---|---|
| SEC-IR-01 | High (pre-existing) | `infra/clickhouse/clickhouse-init.sh:410,418-422,441,445`; `infra/clickhouse/access/insight-sm-policy.sh:86` | Kunci scope ditanam literal di `USING` row policy. Galat query (mis. `Unknown setting SQL_efn_codes`) menampilkan ekspresi policy lengkap beserta kunci. Galat itu juga tercatat di `system.query_log.exception` (TTL 90 hari) dan kemungkinan di log server → Loki. Siapa pun yang memegang password `efn_insight`/`efn_text2sql` dan pernah melihat galat itu bisa memalsukan `SQL_efn_sig`/`sig2` untuk PT dan outlet mana pun | R-S (otorisasi lintas PT), R-L01 (UU PDP minimalisasi/otorisasi), SEC-190/191 |
| SEC-IR-02 | Low | `infra/clickhouse/init/19-insight-review.sql:31` | `v_esb_movement_daily`: bila cabang asal tak dikenal atau `company_code` kosong, view memakai PT dari baris fakta. Untuk cabang yang tidak dipetakan, PT itu adalah PT default brand (`config.company_for`), jadi tidak fail-closed (berbeda dengan `v_esb_coverage`). Contoh di brand Bacha: cabang BCR yang belum dipetakan akan terlihat oleh user BCF | R-S (cakupan PT) |
| SEC-IR-03 | Info | `infra/clickhouse/init/19-insight-review.sql:24-26,37-39` | Keputusan `company_code = PT asal`: user PT asal melihat nama dan jenis cabang tujuan milik PT lain dalam brand yang sama (mis. CK BCF → toko BCR). Yang terlihat hanya metadata nama cabang dan volume kiriman PT itu sendiri. User yang hanya punya scope PT tujuan tidak melihat baris kirimannya (benar, fail-closed) | — |
| SEC-IR-04 | Info | `services/esb-extract/efn_esb/extract.py:171`; `transform.py:172-214` | `dim_esb_menu` dari baris penjualan memakai whitelist 8 kolom (brand, PT, kode, nama, kategori, detail, takeaway, versi), tanpa PII. `company_code` = PT default brand, tetapi tidak dipakai join mana pun (`12-g41-esb.sql:330` join brand+menu_code). Tidak ada kebocoran | — |

## Detail

### SEC-IR-01 (High, pre-existing): kunci scope literal di ekspresi row policy bocor lewat pesan galat

**Kode.** `clickhouse-init.sh:441`:
```
INSIGHT_SIG="getSetting('SQL_efn_sig') = lower(hex(SHA256(concat('${INSIGHT_KEY}', '|', getSetting('SQL_efn_cids'), '|', getSetting('SQL_efn_codes')))))"
```
Pola yang sama ada di `:410` (`text2sql_scope_key`, policy `efn_t2s_scope`) dan di `insight-sm-policy.sh:86` (`sig2` SM, memakai `INSIGHT_KEY` yang sama sebagai `$1`). Mitigasi SEC-191 (`--log_queries=0`) hanya melindungi DDL pembuatan policy. Galat runtime dari query user aplikasi tidak ikut terlindungi.

**Skenario eksploit.**
1. Pemegang password `efn_insight` (operator, container insight-web, atau penyerang yang berhasil SSRF/RCE di insight-web) menjalankan `SELECT 1 FROM efn_insight.v_esb_sales_daily` tanpa setting.
2. ClickHouse menolak dengan `Unknown setting SQL_efn_codes` dan menampilkan ekspresi filter lengkap, termasuk `SHA256(concat('<64 hex kunci>', ...`. Lead sudah mereproduksi ini di efn-prod.
3. Penyerang menghitung `sig = sha256(kunci|''|'PB,WTZ,BCF,BCR,IMD,CUP')` lalu mengirim `SQL_efn_codes=...&SQL_efn_sig=...`. Semua PT ESB terbaca. Dengan kunci yang sama, `sig2` SM juga bisa dipalsukan untuk semua outlet/persona.
4. Lapis 2 (row policy) runtuh menjadi "cukup password". Desain ADR-041 justru memakai lapis ini untuk bertahan bila password atau jalur query insight-web bocor.

**Jalur kebocoran lain.**
- `system.query_log.exception` menyimpan teks galat 90 hari (`config.d/system-logs.xml`). Tidak ada grant `system.*` untuk user aplikasi di repo, jadi saat ini hanya admin yang bisa membacanya. Ini **PLAUSIBLE**: bergantung pada `access_control_improvements.select_from_system_db_requires_grant = true` dari config bawaan image, dan belum diverifikasi. Cek: login sebagai `efn_insight`, jalankan `SELECT count() FROM system.query_log`, hasil yang diharapkan ACCESS_DENIED.
- Log server ClickHouse: komentar `system-logs.xml` menyebut log server dikirim ke Loki, dan `promtail.yml` mengambil log semua container stack. Jika galat query tercetak ke stdout, kunci masuk Loki dan bisa dibaca semua pengguna Grafana. Status **PLAUSIBLE**. Cek dengan query Loki `{container=~".*clickhouse.*"} |= "SHA256(concat("`.
- insight-web **tidak** meneruskan teks galat ClickHouse. `lib/clickhouse.ts:108-112` hanya mengambil `Code: N` untuk log, UI menerima `ChError` generik + ref, dan route ekspor hanya mengembalikan status. text2sql juga tidak meneruskan (`efn_text2sql/clickhouse.py:83-86` hanya `query_failed`). Teks galat tidak sampai ke LLM maupun ke klien.
- insight-web selalu mengirim setting (atau gagal lebih dulu dengan `ParamError` di `clickhouse.ts:78-83`), jadi aplikasi tidak memicu galat `Unknown setting`. Galat jenis lain dari query katalog (analyzer/tipe) bisa saja tetap mencetak ekspresi policy ke query_log/log server (**PLAUSIBLE**).

**Rekomendasi.**
1. **Segera, rotasi kunci:** `insight_scope_key` efn-prod lokal (dan efn-stg/efn-dev bila nilainya sama, atau bila galat yang sama pernah dipicu di sana). Langkahnya: ganti secret, jalankan ulang `clickhouse-init`, lalu restart insight-web. `text2sql_scope_key` juga dirotasi bila galat `efn_text2sql` pernah dipicu atau dilihat. Setelah rotasi, bersihkan jejaknya: baris `system.query_log` yang memuat `SHA256(concat(` (operasi destruktif, minta izin user) dan entri Loki terkait (compactor delete atau tunggu retensi). Ini sesuai runbook security-incident.
2. **Jangka pendek, tutup pemicu utama:** beri nilai bawaan untuk semua custom setting di settings profile (`insight_readonly`, profil text2sql): `SQL_efn_cids = ''`, `SQL_efn_codes = ''`, `SQL_efn_sig = ''`, ditambah `SQL_efn_wids/personas/cats/sig2 = ''`, **tanpa** CONST agar klien tetap bisa menimpa. Dengan begitu `getSetting` tidak pernah melempar galat. Setting yang tidak dikirim membuat FMT/regex bernilai salah, sehingga hasilnya 0 baris tanpa galat. Tambahkan negative test: user tanpa setting mendapat 0 baris dan teks galat tidak memuat `SHA256`.
3. **Struktural, kunci keluar dari ekspresi:** ganti HMAC berbasis kunci literal dengan *komitmen*. Contohnya token per PT: `tok_PT = HMAC(insight_scope_key, "insight|PT")` dihitung di insight-web, sedangkan policy hanya menyimpan `SHA256(tok_PT)` per PT dan memeriksa `arrayAll((c,t) -> lower(hex(SHA256(t))) = <peta kode→hash>[c], codes, toks)`. Bila ekspresi bocor, yang terlihat hanya hash (tahan preimage, token 256 bit), jadi tanda tangan tidak bisa dipalsukan. Keamanan replay sama dengan desain sekarang, karena `sig` saat ini juga deterministik tanpa nonce/kedaluwarsa. Untuk SM, cara yang sama berlaku per outlet/persona, atau `sig2` diganti token per (PT, persona) + daftar wids yang ikut dikomit. Perubahan ini mengubah kontrak docs/04 §26.7/§28.13.6 dan perlu revisi kontrak/ADR-041.
   - Catatan atas usulan lead: setting server-side `custom_`/CONST **tidak** menyembunyikan kunci. Setting apa pun yang berlaku untuk user bisa dibaca lewat `getSetting()`/`system.settings`. `dictGet`/SQL UDF juga tidak cocok: row policy dievaluasi dengan hak invoker (butuh grant `dictGet`, sehingga user bisa membaca kunci langsung), dan definisi SQL UDF terlihat/di-inline di pesan galat. Executable UDF (skrip di server yang memegang kunci) memang menyembunyikan kunci, tetapi menambah kompleksitas proses dan beban operasional. Opsi komitmen lebih sederhana.
4. Tambahkan pemindaian di test harness (`g47_row_policy_harness.sh` / `test_insight_access.py`): pastikan tidak ada respons galat user aplikasi yang memuat literal kunci.

### SEC-IR-02 (Low): fallback PT di `v_esb_movement_daily` tidak fail-closed

`CAST(if(ob.company_code = '', t.c_code, ob.company_code), 'String')`. `t.c_code` berasal dari `company_for(origin)` (`transform.py:487,522`), dan untuk cabang yang tidak ada di `branch_company` nilainya PT default brand (`config.py:184-185`). Untuk brand dengan lebih dari satu PT (Bacha: BCF/BCR), kiriman dari cabang yang belum dipetakan akan terlihat oleh PT default. Pola ini sudah ada di fakta lain, jadi bukan regresi khusus, tetapi `v_esb_coverage` di file yang sama sudah fail-closed ("cabang tak dikenal = tidak dihitung").
**Rekomendasi:** untuk brand multi-PT, isi `company_code = ''` bila cabang asal tidak ada di `dim_esb_branch` (policy menyaring `company_code != ''`), lalu munculkan baris tersebut di cek kualitas data `branch_unmapped`. Alternatifnya, catat penerimaan risiko di README esb-extract.

### SEC-IR-03 (Info): metadata cabang tujuan lintas PT
Ini konsekuensi keputusan yang terdokumentasi. Cukup dicatat di docs/04 §26.7a bahwa nama cabang tujuan PT lain (dalam brand yang sama) terlihat oleh PT asal.

### SEC-IR-04 (Info): `dim_esb_menu` dari penjualan
Whitelist kolom sudah benar, tidak ada PII (L-110).

## Kontrol yang sudah benar (diverifikasi)

- **Row policy kedua view baru:** keduanya masuk loop policy `clickhouse-init.sh:443-446`. Gerbang fail-closed `:451-461` mencabut `GRANT efn_insight.*` bila ada view tanpa `efn_insight_scope`. Definer `efn_insight_definer` (HOST NONE) hanya mendapat dua grant view baru, karena tabel dasarnya sudah ada di daftar (`insight-access.sql:60-61`). Role `insight_reader` tetap tanpa hak di `efn_dwh`/`efn_esb`.
- **Kedua view selalu mengisi `company_code`**, dan policy memuat `company_code != ''`. `v_esb_coverage` fail-closed untuk brand atau cabang tanpa PT.
- **Query baru** (`product_categories`, `esb_coverage`, `mov_by_group`, `mov_by_category`, `mov_ck_product`, fragmen `SALES_BRANCH`, `dist_transfer` non-toko, `lib/queries.ts:53-110,505-610`) berupa konstanta dengan placeholder bertipe dan `${PT}` di setiap query. Subquery `SALES_BRANCH_SET` membaca `v_esb_branch_list`, yang juga terkena row policy, sehingga tidak ada pembacaan lintas PT. `LIMIT` literal.
- **Filter:** `pt` dipotong ke kode sesi, `branch`/`product` divalidasi regex dan dibatasi jumlahnya (`lib/filters.ts:115-187`). FilterBar auto-apply hanya `router.push(pathname?qs)` same-origin, dan scope tetap diputuskan server. Memilih cabang tujuan PT lain di `MBR` hanya mempersempit hasil karena `${PT}` tetap berlaku.
- **Ekspor `mov-category`:** memakai route generik `app/api/export/[name]/route.ts` dengan autentikasi cookie, whitelist nama tabel, rate limit 5 per 10 menit per `sub`, PT dari sesi, galat berupa status saja, `API_CSP`, dan audit log tanpa token. `lib/csv.ts` meng-escape `= + - @ TAB CR`.
- **XSS:** tidak ada `dangerouslySetInnerHTML`/`innerHTML`/`eval` di insight-web. `Term`/popover, glosarium, legend chip (`chart-legend.ts`), dan pencarian tabel (`table-filter.ts`, pencocokan substring tanpa `RegExp` dari input) dirender lewat React. Tooltip ECharts memakai formatter bawaan, tanpa formatter HTML kustom. CSP: `script-src` nonce + `strict-dynamic` tanpa `unsafe-inline`; pelonggaran hanya `style-src-attr`.
- **localStorage `insight-nav`:** hanya `{collapsed, closed[]}` dengan id tervalidasi regex (maks 10), dibungkus try/catch, tanpa data sensitif (`lib/nav-state.ts`, `components/Chrome.tsx:27-50`).
- **`/glosarium`:** berada di grup `(app)`, terkena gerbang sesi `proxy.ts` (bukan `PUBLIC_PATHS`) dan `requireSession()` di layout. Isinya statis.
- **"Kembali ke Odoo":** `odooHomeUrl` = origin dari env `EFN_INSIGHT_ODOO_URL` (divalidasi `parseConfig`) + `/odoo`. Tidak ada input pengguna, jadi tidak ada open redirect.
- **Galat ClickHouse tidak bocor ke browser:** lihat SEC-IR-01, butir "insight-web tidak meneruskan".

## Belum diverifikasi oleh reviewer
- Diff git aktual (review dilakukan atas isi file di working tree) serta hasil vitest/pytest/harness CH.
- Akses `system.query_log` oleh user aplikasi dan keberadaan teks galat di Loki (SEC-IR-01, PLAUSIBLE).
