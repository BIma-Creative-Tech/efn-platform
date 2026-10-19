# efn_role

Peran per PT (jabatan → himpunan grup), **pemisahan tugas (SoD) yang ditegakkan sistem**, pengecualian SoD oleh
Superadmin, dan **review akses bulanan** (snapshot) per PT. Odoo CE 19, **LGPL-3**,
`depends: hr, mail, efn_audit`. Gap **E-47** + **E-47c** dalam satu modul.

- **Kontrak mengikat:** `docs/04_SERVICE_CONTRACTS.md` §13.3 no. 2–4, §13.7 **L-64**, **L-65**.
  **Spesifikasi:** `docs/08_ODOO_FIT_GAP.md` §12.2b (matriks), §12.3 (E-47), §12.4 (SoD), §12.5b (E-47c), §12.8 (test).
- **ADR:** ADR-001 (satu DB multi-company), ADR-031 (tanpa modul pihak ketiga) — keduanya *Proposed*.
- **Risiko dimitigasi:** **R-S14** (hak akses menumpuk tanpa review), R-S10 (insider/fraud: satu orang memegang
  peran yang harus terpisah), R-B06 (fraud promo/refund), R-B11 (satu Superadmin → pengecualian teraudit + auditor).

### Mengapa E-47c di modul yang sama (bukan `efn_role_review`)
Snapshot memakai mesin SoD (grup efektif, pengecualian) dan record rule/ACL yang sama; review tanpa peran tidak
bermakna (kolom peran, "user tanpa peran"). Satu modul = satu daftar `EFN_TEST_MODULES`, tidak ada dependency
melingkar, dan `efn_setup_role` (functional, §12.6) cukup bergantung pada satu modul.

## Model

| Model | Isi |
|---|---|
| `efn.role` | Per company (`_check_company_auto`): `name`, `job_id` (opsional; unik per company lewat `models.UniqueIndex ... WHERE job_id IS NOT NULL`), `group_ids` (≥1, non-portal, bukan grup akun teknis), `active`, `sequence`, `note`, `line_ids`, `user_count`, `is_admin_role`. Peran yang (efektif) memuat `base.group_system`/`base.group_erp_manager` hanya dibuat/diubah/ditetapkan oleh `base.group_system`. Company peran terkunci setelah ada penetapan. |
| `efn.role.line` | `role_id` (restrict), `user_id`, `company_id` (related stored), `date_from` (default hari ini WIB), `date_to`, `source` `manual`/`job`, `is_effective` (Boolean tersimpan, **hanya ditulis server** — RPC ditolak), `reason` (wajib untuk peran admin tanpa `date_to`). Ditolak untuk user portal/publik dan akun teknis (`efn_online_order.group_online_channel`, `efn_pos_kds.group_kds_device`). Peran/user baris tidak bisa diganti (buat baris baru). |
| `res.users` (+) | `efn_role_line_ids`, `efn_role_managed` (readonly; diisi otomatis saat baris pertama; hanya sistem/Superadmin yang boleh mengubah; diabaikan saat create). |
| `efn.sod.rule` | `code` (unik), `name`, `company_id` (kosong = global, default), `group_a_id`/`group_b_id` (≠, pasangan tak berurut unik per company), `mode` `block`/`report_only`, `active`, `rationale`, `violation_count`. Tulis hanya `base.group_system`; tidak bisa dihapus (arsipkan). |
| `efn.sod.exception` | `rule_id`, `user_id`, `company_id` (server: company aturan atau company utama user), `reason` (≥20 karakter), `granted_by_id` (= user login, wajib Superadmin, ≠ `user_id`), `date_from` (≥ hari ini), `date_to` (≤ 90 hari, default +30), `active`, `state` (compute), `expiry_notified`, `revoked_by_id/on`. Immutable kecuali dicabut (`active=False`); tanpa pengaktifan ulang; tanpa unlink. |
| `efn.access.review` | `company_id`, `period` `YYYY-MM` (unik per company), `state` `draft → in_review → done`, `reviewer_id`, `reviewed_on`, `snapshot_date`, `notes`, hitungan (user, pelanggaran SoD, pengecualian aktif, pengecualian kedaluwarsa, pemberian grup langsung, tanpa login > 90 hari, tanpa 2FA, user tanpa peran di PT). `done` read-only untuk semua (termasuk sudo) kecuali chatter; tanpa unlink (kecuali GC retensi). |
| `efn.access.review.line` | Snapshot tersimpan per (user internal company review, grup efektif): login, aktif, login terakhir, 2FA, nama peran PT, grup + nama lengkap + privilege, `source` `role`/`direct`/`implied`, peran asal, aturan SoD dilanggar (+ kode teks), pengecualian (+ status `effective`/`expired`). Hanya dibuat/dihapus snapshot (penanda `cr.cache`); tidak bisa diubah. List/pivot/graph + ekspor standar CSV/XLSX. |

Grup penanda baru (tanpa ACL, tanpa `implied_ids`, privilege *EFN Peran*): `efn_role.group_role_cashier`,
`efn_role.group_refund_approver` (cermin grup host BFF `efn-cs-approver`).

## Penerapan peran
Ide "nothing more, nothing less" dari OCA `base_user_role` (referensi desain, lihat Referensi), ditulis ulang:
`managed` = grup semua peran (aktif & arsip, semua company) + grup penanda − `base.group_user`; grup eksplisit
baru = (grup user − managed) ∪ grup peran efektif ∪ {`base.group_user`}; `company_ids` += company baris efektif
(tidak pernah dikurangi). Hanya user ber-`efn_role_managed`. Perubahan grup peran → grup lama dicabut dari
pemegang peran. Menulis grup terkelola langsung ke user ber-flag (dari sisi user maupun `res.groups.user_ids`) →
`ValidationError efn_role:managed_group`.

Penanda penerapan memakai **`cr.cache`**, bukan `with_context(efn_role_apply=True)` seperti di spesifikasi:
context dapat diisi klien lewat RPC, `cr.cache` tidak (pola efn_audit).

**Jabatan:** tombol *Terapkan peran dari jabatan* (form peran) + cron harian: karyawan ber-`user_id` dengan
`hr.employee.job_id` (19.0: field turunan `hr.version.job_id` versi aktif lewat `_inherits` [V source
`hr/models/hr_version.py`]) = `role.job_id` di company sama → baris `job`; jabatan berubah → baris `job` ditutup
(`date_to` = kemarin; baris yang dibuat hari ini di-unlink, tercatat audit). Baris `manual` tidak disentuh.
**Peran admin tidak pernah disinkron dari jabatan** (cegah eskalasi: HR menetapkan jabatan *Superadmin IT* →
`base.group_system`); peran admin = baris manual oleh Superadmin. Tombol karyawan (spesifikasi "di
peran/karyawan") **tidak** dibuat agar tidak mewarisi view `hr.employee` 19.0 yang belum diverifikasi; cron + tombol
peran mencakup kebutuhan.

## Penegakan SoD
- Pelanggaran = user aktif internal, kedua grup ∈ grup **efektif** (penutup `implied_ids`), aturan aktif `block`,
  `company_id` kosong atau ∈ `company_ids` user, tanpa pengecualian efektif.
- Grup efektif dihitung dari tabel relasi (`res_groups_users_rel`, `res_groups_implied_rel`) lewat SQL setelah
  flush — tidak bergantung pada cache `groups` CE yang baru dibersihkan setelah `res.groups.write`.
- Titik: `res.users` constraint (`group_ids`, `company_ids`, `active`); `res.groups` create/write (`user_ids`,
  `implied_ids`, `implied_by_ids`) untuk anggota grup terdampak + grup leluhurnya (menutup *Settings* yang
  menambah `implied_ids` ke `base.group_user`) — hanya pelanggaran **baru** (baseline sebelum write); penetapan
  peran; aturan yang (menjadi) aktif `block` ditolak bila masih ada pelanggaran (pesan berisi **jumlah**).
- Pesan berisi kode aturan + nama grup, tanpa login/nama user. Log server: `EFN_SOD_BLOCK rule=<code> uid=<id>`.
- Superadmin/sudo **tidak** kebal. Saat registry belum siap (install/upgrade modul) penegakan dilewati (pelanggaran
  tetap tampil di review akses).

### Aturan bawaan (opsional per xmlid)
`post_init_hook` + cron harian membuat aturan yang belum ada (kunci `code`, termasuk arsip) **hanya bila kedua
grup ada** (`env.ref(..., raise_if_not_found=False)`; tanpa dependency keras):
SOD-01 `sales_team.group_sale_manager` ⟂ `efn_role.group_refund_approver` (block), SOD-02
`efn_role.group_role_cashier` ⟂ `point_of_sale.group_pos_manager` (block), SOD-03
`efn_loyalty_accounting.group_loyalty_manager` ⟂ `account.group_account_manager` (block), SOD-05
`purchase.group_purchase_user` ⟂ `account.group_account_manager` (report_only), SOD-06 `efn_audit.group_auditor`
⟂ `base.group_erp_manager` (report_only; Superadmin selalu memegang keduanya lewat implied CE). SOD-04 tidak dibuat.
Aturan `block` yang saat dibuat sudah punya pelanggaran dibuat `report_only` + log `EFN_SOD_DEFAULT_REPORT_ONLY`.
Aturan dibuat tanpa xmlid; **`efn_setup_role` (functional) harus merujuk lewat `code`**, bukan membuat ulang.

## Pengecualian SoD (L-65)
Hanya `base.group_system`, alasan wajib ≥ 20 karakter (HP/email/NIK di-mask otomatis), maks. 90 hari, mulai
≥ hari ini, pemberi ≠ penerima. Saat dibuat: catatan chatter + aktivitas To-Do untuk setiap **Auditor**
(`efn_audit.group_auditor` efektif, bukan pemegang Access Rights) yang punya company pengecualian; tanpa auditor →
log `EFN_SOD_EXCEPTION_NO_AUDITOR`. Kedaluwarsa (cron): grup **tidak** dicabut, aktivitas untuk Superadmin
(sekali), dan penulisan grup berikutnya untuk user itu ditolak sampai pelanggaran diselesaikan. Tampil di review
(`exception_state`, hitungan aktif/kedaluwarsa).

## Review akses bulanan (E-47c)
Cron harian 02:30 WIB membuat review **periode bulan lalu** untuk setiap company pada tanggal 1 (toleransi s/d
tanggal 3 bila cron terlewat; idempoten) + aktivitas ke auditor company. Tombol: *Ambil ulang snapshot* (draft;
Auditor/Access Rights), *Mulai review*, *Selesai & tandatangani* (Auditor). Menu: *Settings → Users & Companies →
Peran EFN / Penetapan Peran / Aturan SoD / Pengecualian SoD / Review Akses* (Access Rights) dan *Audit → Review
Akses / Snapshot Review Akses / Aturan SoD / Pengecualian SoD / Peran EFN* (Auditor tanpa Access Rights).
Ekspor = standar (`/web/export/xlsx`); **ORM `export_data` 19.0 mensyaratkan `base.group_allow_export`** [V source
`odoo/orm/models.py`] → peran Auditor (`efn_setup_role`) harus memuat `base.group_allow_export`.
Retensi: `@api.autovacuum` menghapus review `done` lebih tua dari `efn.audit.retention_days` (730, PLACEHOLDER;
minimum efn_audit berlaku; 0 = selamanya). Data pribadi: hanya nama & login user internal (18-40).
Rekonsiliasi cermin `efn-cs-approver` (BFF) manual: filter *Cermin efn-cs-approver*, catat di *Catatan / Temuan*.

## ACL & record rule
`efn.role`, `efn.role.line`: baca Access Rights + Auditor; tulis Access Rights. `efn.sod.rule`: baca keduanya, tulis
`base.group_system`. `efn.sod.exception`: baca keduanya, buat/cabut `base.group_system`. `efn.access.review`: Auditor
baca/tulis(status lewat tombol)/buat; Access Rights baca/buat. Baris review: baca keduanya, tanpa tulis. Record
rule global: `company_id in company_ids` (aturan SoD + `False`; pengecualian juga bila `user_id.company_ids` ∩
company pembaca). Audit: aturan `efn.audit.rule` bawaan (xmlid, tidak bisa dihapus) untuk `efn.role`,
`efn.role.line`, `efn.sod.rule`, `efn.sod.exception`, `efn.access.review`; `res.users.group_ids`/`res.groups`
sudah dicatat bawaan efn_audit.

RPC publik baru (terdaftar `efn_core/tests/test_rpc_surface.py` `RPC_ALLOWLIST`): `efn.role.action_efn_apply_job_roles`,
`efn.access.review.action_efn_snapshot` / `action_efn_start_review` / `action_efn_done`.

## Cara uji manual
1. Pasang `efn_role` (+ `point_of_sale`, `sales_team` untuk SOD-01/02). *Settings → Users & Companies → Aturan SoD*:
   SOD-01/02 (block), SOD-06 (report_only) ada.
2. Buat peran *Kasir* (company GL, grup `Point of Sale / User` + `EFN Peran / Kasir`), tetapkan ke user U →
   U mendapat grup + company GL; grup lain U tetap.
3. Di form user U tambahkan `Point of Sale / Administrator` → ditolak `efn_role:sod_block — SOD-02`.
4. Login Superadmin B: *Pengecualian SoD* untuk U (alasan ≥ 20 karakter, ≤ 90 hari) → auditor menerima aktivitas;
   langkah 3 kini lolos.
5. *Review Akses* → buat (company GL, periode) → snapshot; filter *Melanggar SoD*; auditor *Mulai review* →
   *Selesai* → form read-only; ekspor XLSX dari list snapshot.

## Test
`tests/` (tag `efn`, `post_install`): penerapan (1–5, 12, jabatan, unik), SoD (6–8, 10, 11, bawaan, penanda),
pengecualian (9), review (16, cron), keamanan (13–15, flag). Grup modul lain diuji bila terpasang (`skipTest`
bila tidak). Uji 17 = regresi seluruh `--test-tags efn` dengan `efn_role` terpasang.

## Asumsi 19.0 / belum terverifikasi
- [V] `res.groups`: `implied_ids`/`implied_by_ids` (`res_groups_implied_rel` gid/hid), `user_ids`
  (`res_groups_users_rel` gid/uid), `all_implied_ids`, `all_user_ids`, cache `groups` dibersihkan setelah write;
  `res.users.group_ids`/`all_group_ids`/`share`; `models.UniqueIndex`; `export_data` + `base.group_allow_export`;
  `base.menu_users`; `hr.version.job_id` (WebFetch `odoo/odoo@19.0`, 19 Okt 2026).
- [B] `check_access(operation)`, `action_archive` → `write({'active': False})`, `activity_schedule` untuk user
  dengan hak baca record, render `web_ribbon`/pivot di 19.0, efek `hr.employee.write({'job_id'})` ke versi aktif.
- Test **belum dijalankan** oleh penulis (sesi tanpa shell). Modul belum ada di `EFN_TEST_MODULES` CI (wilayah devsecops).

## Batasan
- Grup Odoo global: peran per company hanya menambah company; SoD dievaluasi global per user (default).
- `managed` mencakup grup semua peran: grup yang dipakai peran mana pun tidak bisa diberikan langsung ke user
  terkelola (by design). User tanpa flag tetap bebas (kasir ber-PIN tanpa user Odoo tidak terdampak).
- Pelanggaran yang tercipta saat install/upgrade modul (registry belum siap) tidak diblokir — tampil di review.
- Cermin `efn-cs-approver` tidak tersinkron otomatis dengan BFF (di luar cakupan G21).
- SoD karyawan POS (`advanced_employee_ids`) = E-47b `efn_role_pos` (belum).
- Superadmin tunggal (L-65) tidak bisa mengecualikan dirinya sendiri; Superadmin memang tidak memegang peran transaksi.

## Referensi (bukan dependency, ADR-031)
OCA `server-backend@19.0` `base_user_role` (LGPL-3): konsep peran = himpunan grup + baris bertanggal + sinkron grup
user. Tidak ada kode yang disalin. `base_user_role_company` (AGPL-3) **tidak** dipakai sebagai acuan (ganti grup
saat ganti company). Tidak ada modul SoD OCA/Cybrosys 19.0 terverifikasi yang dirujuk.
