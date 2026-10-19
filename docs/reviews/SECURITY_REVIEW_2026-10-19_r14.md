# Security Review r14 — Gelombang 21 (branch `chore/wave-21`, basis `main` 490d06e)

Tanggal: 19 Okt 2026 · Reviewer: efn-security-reviewer (read-only, tanpa shell) · Nomor temuan lanjut dari SEC-153.
Konteks: docs/04 §13 + L-61..L-66, ADR-036 §0, docs/08 §11–§12, laporan agent G21. Hasil test (lead, container):
Odoo 746/0 (22 modul) + gabungan efn_pii* 841/0; BFF 753 + 7 vektor bersama AC-2; FE 19 file; ETL ±330; deploy 195/0.

## Ringkasan eksekutif

- **Tidak ada temuan Critical/High.** Kontrol inti G21 benar: HMAC publikasi kebijakan (byte persis, `t` ikut
  ditandatangani, tanpa fallback unsigned, 503 bila kunci hilang), tabel kebijakan append-only, berlaku tidak surut,
  XML Coretax terenkripsi + unduhan terkontrol, gerbang `efnpii1` (SEC-148), dan profil faktur pembeli tidak disimpan/di-log.
- **2 Medium (pemblokir aktivasi, bukan merge):** eskalasi hak lewat sinkron jabatan→peran oleh user HR (SEC-154) dan
  rencana memberi `base.group_allow_export` ke Auditor = ekspor massal data pribadi terdekripsi (SEC-155).
- **5 Low + 3 Info:** idempotensi jurnal akrual hanya lewat `ref`, empat mata hanya penyunting terakhir, perubahan aturan
  SoD tanpa notifikasi auditor (L-65), demosi admin oleh Access Rights, forfeit/breakage tak dijurnal (alert fatigue).
- **Verdict: GO merge** (online & efn_pii tetap OFF di prod). Aktivasi `efn_setup_role`/peran per jabatan dan kebijakan
  poin produksi menunggu SEC-154/155/157/159.

## Tabel temuan

| ID | Sev | Area | File:baris | Pemblokir |
|---|---|---|---|---|
| SEC-154 | Medium | efn_role sinkron jabatan | `efn_role/models/efn_role.py:216-262` | Aktivasi `efn_setup_role` |
| SEC-155 | Medium | Hak ekspor Auditor | `efn_role/README.md:91-92`, `efn_role/tests/common.py:37` | Aktivasi `efn_setup_role` |
| SEC-156 | Low | Akrual: idempotensi via `ref` | `efn_loyalty_accounting/models/efn_loyalty_accrual.py:307-316` | Aktivasi akrual prod |
| SEC-157 | Low | Empat mata kebijakan poin | `efn_loyalty_accounting/models/efn_loyalty_policy.py:256-271, 282-294`; `efn_role/models/efn_sod_rule.py:328-335` | Aktivasi kebijakan prod |
| SEC-158 | Low | Perubahan aturan SoD tanpa notifikasi (L-65) | `efn_role/models/efn_sod_rule.py:156-163` | Aktivasi `efn_setup_role` |
| SEC-159 | Low | Access Rights dapat menandai admin sebagai "managed" | `efn_role/models/efn_role_line.py:77-94`, `efn_role/models/efn_role.py:174-197` | — (PLAUSIBLE) |
| SEC-160 | Low | Forfeit DSAR & breakage tidak dijurnal → selisih RECON permanen | `efn_loyalty_accrual.py:337-398` vs `api-gateway/src/pos/loyalty-accrual.service.ts:54-55,193` | Aktivasi alert RECON |
| SEC-161 | Info | `EFN_ODOO_PII_ACCEPT_V1` tidak ditolak eksplisit di production | `scripts/lib/deploy-validate.sh:19-33`, `infra/compose/docker-compose.yml:836-859` | — |
| SEC-162 | Info | View ETL `partner_region` mencakup semua partner | `services/etl/sql/odoo-readonly-grants.sql:196-203` | — |
| SEC-163 | Info | `efn_online_invoice_buyer` hanya readonly di UI | `efn_online_order/models/res_partner.py:15-21` | — |

## Detail

### SEC-154 (Medium) — Eskalasi hak lewat jabatan karyawan (HR → grup peran)
`_efn_sync_job_lines` (efn_role.py:216-262, cron harian + tombol) membuat baris `source='job'` untuk SETIAP
`hr.employee` ber-`user_id` yang `job_id`-nya = `role.job_id`, lalu `efn.role.line.create` menandai user
`efn_role_managed` dan menerapkan grup (efn_role_line.py:85-93). Peran admin dikecualikan (bagus), peran lain tidak.
**Skenario:** user HR Officer (hak tulis `hr.employee`/`hr.version`, tanpa Access Rights) mengubah jabatan karyawan
miliknya sendiri (atau rekan) menjadi "Finance Manager" → cron 00:30 memberi `account.group_account_manager` (penyetuju
kebijakan poin, posting jurnal, lock date) tanpa keterlibatan Access Rights/Superadmin. SoD hanya menolak pasangan
terlarang; review akses baru melihatnya sampai sebulan kemudian.
**Rekomendasi:** (a) flag per peran `auto_sync_from_job` default False untuk peran sensitif (Accounting Manager, Loyalty
Manager, POS Manager, Refund Approver) — baris `job` untuk peran itu dibuat `is_effective=False` menunggu konfirmasi
`base.group_erp_manager`; (b) tolak baris `job` bila `write_uid` perubahan `job_id` = user target (penetapan diri);
(c) log `EFN_ROLE_JOB_GRANT` + aktivitas Auditor untuk setiap baris `job` baru peran sensitif. Risiko: R-S14, A-5.

### SEC-155 (Medium) — `base.group_allow_export` untuk Auditor membuka ekspor massal PII terdekripsi
README efn_role:91-92 menetapkan peran Auditor (`efn_setup_role`) "harus memuat `base.group_allow_export`"; fixture test
melakukannya (tests/common.py:37). Di CE 19 grup itu membuka `/web/export/*` untuk SEMUA model yang bisa dibaca user.
Auditor = user internal (`base.group_user`) → baca `res.partner`; efn_pii mendekripsi di ORM.
**Skenario:** Auditor membuka Kontak → pilih semua → Ekspor (nama, HP, email, alamat, NPWP) → seluruh data member
plaintext dalam XLSX di laptop. Melanggar minimisasi UU PDP Pasal 16 dan tujuan ADR-036 (kolom terenkripsi tak berarti
bila ekspor massal terbuka).
**Rekomendasi:** jangan berikan `group_allow_export` ke Auditor. Sediakan aksi server privat
`efn.access.review._efn_export_csv()` (tombol ber-allowlist RPC, cek `group_auditor` + record rule) yang membangun CSV
hanya dari `efn.access.review.line`, atau beri `group_allow_export` lewat pengecualian berjangka (≤ 7 hari) yang tercatat.
Bila tetap diberikan: catat di ADR-036 sebagai residu yang diterima + alert `efn_audit` untuk ekspor `res.partner`.
Risiko: R-L01, R-S14.

### SEC-156 (Low) — Idempotensi jurnal akrual hanya berdasarkan `ref`
efn_loyalty_accrual.py:307-316: `account.move` mana pun di company dengan `ref = EFN-LOY-<PT>-<tgl>` (draft, batal,
dibuat manual, tipe apa pun) membuat akrual `done` dan ditautkan.
**Skenario:** akuntan (insider) membuat jurnal draft kosong ber-ref `EFN-LOY-GL-2026-10-19` sebelum 02:00 → beban &
liabilitas poin hari itu tidak pernah diposting; hanya baris RECON `mismatch` yang menandai (dan lihat SEC-160: mismatch
bisa sudah "normal"). Juga: jurnal akrual yang di-reset ke draft tetap dianggap selesai.
**Rekomendasi:** tautkan lewat field khusus (`account.move.efn_loyalty_accrual_id`, readonly, diisi sudo) dan cari
`state='posted'` + `journal_id` akrual; bila ada jurnal ber-ref sama tapi tidak memenuhi → `error:ref_conflict` + aktivitas.

### SEC-157 (Low) — Empat mata hanya membandingkan pembuat & penyunting TERAKHIR
efn_loyalty_policy.py:256-271 menimpa `last_editor_id` setiap edit; `_efn_check_approver` (282-294) hanya menolak
`maker_id | last_editor_id`. **Skenario:** A (LM) membuat draft; C (LM + Accounting Manager) menaikkan `point_value_idr`;
A menyunting catatan (`note`) → `last_editor = A`; C menyetujui perubahan buatannya sendiri. Syarat: user memegang dua grup
— SOD-03 mencegahnya, TETAPI `_efn_ensure_default_rules` (efn_sod_rule.py:328-335) membuat SOD-03 sebagai `report_only`
bila saat install sudah ada pelanggaran, hanya dengan log WARNING.
**Rekomendasi:** simpan himpunan penyunting (`editor_ids` Many2many, ditulis server di `write`) dan tolak bila approver ∈
himpunan; tambah pemeriksaan pasca-deploy/test integrasi bahwa SOD-03 aktif `block` sebelum kebijakan prod dipublikasikan.

### SEC-158 (Low) — Penurunan/arsip aturan SoD tidak memberi tahu Auditor (L-65)
L-65 menerima Superadmin tunggal dengan kompensasi notifikasi auditor — diterapkan untuk pengecualian
(efn_sod_exception.py:216-246) tetapi tidak untuk `efn.sod.rule.write` (156-163). **Skenario:** Superadmin mengubah
SOD-03 ke `report_only` (atau `active=False`), memberi dirinya LM + Accounting Manager, menyetujui kebijakan, lalu
mengembalikan aturan — tanpa aktivitas ke Auditor; hanya log `efn_audit` yang baru dibaca saat review bulanan.
**Rekomendasi:** pada perubahan `mode`/`active`/grup aturan: `message_post` + aktivitas ke `auditor_users()` + log
`EFN_SOD_RULE_CHANGED rule=<code> uid=<id> from=<mode> to=<mode>`; alert Loki pada log itu.

### SEC-159 (Low, PLAUSIBLE) — Access Rights dapat membuat admin menjadi "managed" dan mencabut grup admin-nya
`efn.role.line.create` (efn_role_line.py:77-94) hanya memeriksa apakah PERAN-nya admin, bukan apakah USER target admin.
Bila ada satu peran admin (berisi `group_system`), `_efn_managed_group_ids` memuat `group_system`; penerapan (sudo,
efn_role.py:174-197) menghitung `target = (current - managed) | desired`. **Skenario:** user `base.group_erp_manager`
membuat baris peran non-admin untuk akun Superadmin kedua/teknis → akun itu kehilangan `group_system` (penulisan sudo
melewati batasan CE). Superadmin terakhir terlindungi constraint CE `_check_at_least_one_administrator` (belum
diverifikasi berjalan di jalur ini). **Rekomendasi:** di `_check_user`, tolak baris untuk user yang efektif memegang
`ADMIN_GROUP_XMLIDS` kecuali pemanggil `base.group_system`; atau keluarkan grup admin dari `managed` untuk pemanggil non-system.

### SEC-160 (Low) — Forfeit DSAR dan breakage tidak dijurnal → selisih RECON permanen (alert fatigue)
BFF mengeluarkan `forfeited_value_idr` (erasure, loyalty-accrual.service.ts:54-55) dan `expired_value_idr`; Odoo tidak
menjurnal forfeit sama sekali dan breakage hanya bila `efn.loyalty.breakage_journal_enabled` (default False,
efn_loyalty_accrual.py:389-397). `closing_liability_idr` BFF sudah dikurangi keduanya → `diff_idr` tumbuh setiap kali
ada erasure/kedaluwarsa dan alert `liability_recon` menyala terus. Selisih nyata (mis. SEC-156) tenggelam.
**Rekomendasi:** jurnal forfeit (Dr Liabilitas / Cr akun yang ditentukan Finance) atau sertakan forfeit/expired yang
belum dijurnal sebagai komponen terpisah di payload RECON (tetap 5 kunci kontrak + baris kedua) sehingga `diff_idr`
hanya mencerminkan selisih tak terjelaskan. Keputusan akuntansi: Finance (ADR-020).

### SEC-161 (Info) — `EFN_ODOO_PII_ACCEPT_V1` tidak ditolak eksplisit di production
Laporan engineer meminta "deploy-validate menolak ACCEPT_V1 di production"; tidak ada referensi di `scripts/`/`infra/`.
Saat ini **tidak bisa dicapai**: bukan `EFN_RELEASE_CONFIG_VARS` (deploy-validate.sh:19-33, stdin ditolak), env service
`odoo` di compose eksplisit tanpa `env_file`/variabel itu (docker-compose.yml:836-859), overlay pii.yml juga tidak. Selain
itu kait DB `efn_pii.v1_migration_done` mematikannya setelah migrasi. **Rekomendasi (pertahanan berlapis):** tambah
assertion `check-encryption.sh` bahwa render prod/prod-pii service `odoo` tidak memuat `EFN_ODOO_PII_ACCEPT_V1`, agar
overlay migrasi masa depan tidak lolos diam-diam.

### SEC-162 (Info) — View `efn_etl.partner_region` mencakup semua partner
odoo-readonly-grants.sql:196-203 mengekspos `(partner_id, company_id, region_code)` untuk semua partner ber-wilayah
(termasuk karyawan/vendor perorangan bila diisi). Diterima ADR-036 §0.7 dan job hanya LEFT JOIN ke baris penjualan.
**Saran:** batasi ke partner pelanggan (mis. `p.customer_rank > 0` atau bukan partner user internal) untuk minimisasi.

### SEC-163 (Info) — Penanda `efn_online_invoice_buyer` dapat ditulis lewat ORM
res_partner.py:15-21 `readonly=True` hanya UI. Penyunting partner dapat menandai partner lain sebagai kandidat pakai
ulang; dampak dapat diabaikan karena pakai ulang mensyaratkan NPWP (blind index) + nama + alamat + email identik
(efn_online_order.py:1057-1079, 1102-1104). **Saran:** tolak penulisan field itu di `write` kecuali sudo internal.

## Kontrol yang sudah benar (diverifikasi)

**E-44 kebijakan & akrual (Odoo)**
- Empat mata per record: grup Accounting Manager + `check_access` + company ∈ `user.company_ids` + penyetuju ∉
  {pembuat, penyunting terakhir}; field status/jejak hanya via sudo setelah cek (`AccessError` untuk RPC); isi read-only
  setelah keluar draft **termasuk sudo** (`write`), `_efn_system_write` hanya mengizinkan pergeseran `effective_from`.
- Versi server-side dengan advisory lock + UNIQUE; tidak bisa dihapus setelah diputuskan; `copy` diblok.
- `effective_from` lampau digeser ke waktu persetujuan; BFF memakai `GREATEST(effective_from, received_at)` — tidak surut.
- Outbox: kunci dibaca dari file per kirim (≥ 32 byte, tidak di DB/log), tanpa kunci → tertahan `pending` + ERROR tanpa
  rahasia, **tidak pernah dikirim unsigned**; `t` baru per percobaan; hanya SHA-256 body disimpan; kode error disanitasi
  (`_safe_code`). `_efn_loyalty_request`: hanya PUT/GET, header tambahan hanya `X-EFN-Signature` (service key tidak bisa
  ditimpa), transport efn_core (https + CA, tanpa redirect), query allowlist `company_code ^[A-Z]{2,8}$` + `date` ISO —
  bypass penjaga mirip-HP aman karena regex allowlist lebih ketat daripada penjaga itu.
- Akrual: respons BFF divalidasi ketat (`company_code`/`date` harus sama, integer ≥ 0, kode PT kontrak, L-66 tanpa PT
  sendiri); akun kosong → `missing_config` tanpa jurnal; lock date → `post_failed:<kelas>` tanpa pesan Odoo; baris
  RECON tepat 5 kunci numerik/kode, logger terpisah, tanpa PII.

**BFF**
- Guard tanda tangan setelah `ServiceKeyGuard`, `rawBody` aktif, HMAC atas `"<t>." + rawBody`, `timingSafeEqual`,
  jendela 300 s, 503 bila kunci tidak ada, alasan hanya di log; kunci ≥ 32 byte dan wajib ≠ `ODOO_BFF_API_KEY`, wajib di
  luar dev. Parser skema ketat (`additionalProperties: false`, ≤ 8 KiB, path = body, `point_value_idr` hanya EFN).
- Plafon env dicek saat simpan DAN saat pakai; duplikat-sebelum-plafon idempoten; konflik → 409 + anomali.
- `platform.loyalty_policy` append-only (trigger UPDATE/DELETE/TRUNCATE), efn_bff hanya SELECT/INSERT, efn_readonly
  tanpa `approved_by_uid`.
- Akrual: snapshot REPEATABLE READ READ ONLY, tanggal belum ditutup → 422, invarian L-66 dicek, hanya agregat.
- Erasure: forfeit per lot (penerbit, expires_at) lalu tombstone; reservasi/earn dihapus.
- `invoice_request`: tepat 5 field, regex NPWP/NIK/paspor, karakter kontrol/bidi ditolak, error generik; tidak di DB
  (sidik hanya boolean), tidak di log (anomali hanya order/part/kode); hanya PT PPN (GL, BCR) → 422 sebelum order dibuat;
  `invoice_not_available` → kompensasi tanpa payment intent. Vektor bersama Odoo↔BFF identik (AC-2).

**PII 1a/1b**
- `invoice_partner_display_name` E + penanda satu arah + pencarian lewat subquery token partner; `mail.mail`
  `email_to`/`email_cc` E untuk subjek terlindungi, fail-closed saat terkunci; login portal ber-`@` → `vnd-XXXXXX`
  (`secrets.choice`); rekening orang E + `sanitized_acc_number` = blind index (unik & duplikat tetap jalan).
- `efn_pii_l10n_id`: NIK & dokumen pembeli E + B; XML Coretax ditulis `efnpii2:` berikat id (AAD) — salinan ciphertext
  ke lampiran lain gagal dekripsi; penanda format dari luar ditolak; chatter tanpa salinan lampiran; controller unduhan:
  `check_access('read')` + grup Billing + semua lampiran wajib dokumen Coretax (bukan `assert`) + jejak chatter + log
  tanpa isi; `/web/content` hanya menyajikan ciphertext.
- SEC-148: `efnpii1` hanya untuk id yang di-INSERT di cursor ini (penanda `cr.cache`, bukan context) atau mode migrasi
  (env DAN kait DB kosong; kait hanya mengetatkan); selain itu pengganti + CRITICAL. Nilai berawalan penanda ditolak di
  create/write. SEC-149: padding deterministik HMAC per (tabel, id, token).
- `efn_region`: kode `PP.KK` saja (kecamatan ditolak), ETL hanya lewat view pemilik `efn_odoo` dengan daftar kolom;
  erasure `efn_privacy` mengosongkan `efn_region_id`, `l10n_id_nik`, `l10n_id_buyer_document_number`.

**efn_role**
- Penanda internal di `cr.cache` (tidak bisa diisi klien RPC); grup efektif dihitung via SQL setelah flush; jalur
  `res.users` (constraint) dan `res.groups` (create/write implied/user_ids, baseline) tertutup; Superadmin tidak kebal.
- Peran admin hanya `base.group_system` dan tidak pernah disinkron dari jabatan; pengecualian SoD: hanya Superadmin,
  pemberi ≠ penerima, ≥ 20 karakter (di-mask), ≤ 90 hari, tidak surut, immutable, notifikasi auditor.
- Review akses: `done` read-only termasuk sudo, tanpa unlink, 3 tombol + 1 tombol peran ber-allowlist dengan cek grup +
  `check_access`.

**E-40 / E-39**
- Coretax rounding: angka dari builder CE (tidak ditulis ulang), pengaman |d| ≤ baris × Rp1, lock date
  fiscal/tax/sale/hard dicek eksplisit, ekspor ketat / posting best-effort, reset draft → reversal; piutang tidak disentuh.
- `efn_online_set_invoice_party`: pemanggil = user kanal, order dibatasi kanal, ketersediaan fail-closed (PT retail +
  semua field target benar-benar `EfnPiiChar`), partner pembeli wajib terlindungi (rollback bila tidak), advisory lock
  tanpa PII, hasil `{"state":"set"}`, log hanya id/tipe; pesan `ValidationError` base_vat tidak diteruskan.

**DevSecOps**
- Job `odoo-test-pii`: 3 kunci acak per job, berbeda, tidak di-echo, mount ro, log diperiksa ERROR/CRITICAL + test efn_pii
  wajib berjalan + grep nilai kunci di log; modul `efn_pii_*` baru ikut otomatis.
- Kunci PII prod `host:disabled [SA]`; overlay `docker-compose.pii.yml` hanya bila `EFN_ODOO_PII_ENABLED=true` dan ketiga
  kunci valid (32 byte base64 kanonik, bukan placeholder/symlink, berbeda); production ENABLED ⇒ REQUIRED=true; modul
  efn_pii terpasang tanpa kunci → deploy gagal; `check-encryption.sh` memastikan `odoo_pii_*` hanya ke odoo dan
  `loyalty_policy_signing_key` hanya ke odoo + api-gateway.
- Pengecualian redaksi Promtail tahap 2c: selector berjangkar penuh, hanya `service="odoo"`, logger
  `efn_loyalty_accounting`, JSON 5 kunci numerik/kode/tanggal — tidak ada ruang teks bebas; baris palsu dari logger lain
  tetap diredaksi (fixture `odoo.txt:14-15` → `expected.txt:58-59`).

## Belum diverifikasi oleh reviewer
- Perilaku CE 19 `_check_at_least_one_administrator` pada penulisan sudo (SEC-159).
- Isi `efn_audit_rule_data.xml` efn_role/efn_loyalty_accounting tidak dibaca baris per baris.
- `efn_pii_hr` (8+8 field, tracking) dan log merge `base_partner_merge` hanya dibaca lewat laporan engineer + hasil test.
