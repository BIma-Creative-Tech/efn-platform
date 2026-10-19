# efn_setup_org

Modul konfigurasi **data-only** (G21, W-31): templat **departemen** (`hr.department`) dan **jabatan** (`hr.job`) modul `hr` CE 19 untuk keenam PT EFN. Tidak ada model, field, atau logika Python (selain test). Nama orang **tidak** ada di data (keputusan 18-24, 18-40).

- **Kontrak:** `docs/04_SERVICE_CONTRACTS.md` §13.3 no. 1, L-64. **Spesifikasi & matriks peran:** `docs/08_ODOO_FIT_GAP.md` §12.
- **ADR:** ADR-001 (satu DB multi-company, *Proposed*), ADR-031 (tanpa dependency vendor, *Proposed*).
- **Risiko dimitigasi (sebagian):** R-S14 (hak akses menumpuk / tanpa review), R-S10 (insider). Mitigasi penuh butuh `efn_role` (peran dari jabatan + SoD, E-47) — modul ini hanya menyiapkan kerangka organisasi.
- **depends:** `hr`, `efn_setup_company` (terverifikasi `odoo/odoo@19.0`, 2026-10-19).

## Yang dikonfigurasi (noupdate)

Per PT (sufiks xmlid `efn`, `gl`, `pb`, `bcr`, `bcf`, `farm`): **9 departemen**, **15 jabatan** (total 54 + 90 record).

| Departemen (`dept_<kode>_<pt>`) | Jabatan (`job_<kode>_<pt>`) |
|---|---|
| `board` Direksi | `director` Direktur |
| `finance` Keuangan & Akuntansi | `finance_manager` Finance Manager, `finance_staff` Staf Finance |
| `store` Operasional Toko & Outlet | `store_manager` Store Manager, `cashier` Kasir |
| `merch` Merchandising & Pengadaan | `merchandiser` Merchandiser, `procurement` Staf Pengadaan |
| `warehouse` Gudang & Logistik | `warehouse_staff` Staf Gudang, `courier` Kurir |
| `cs` Layanan Pelanggan (CS) | `cs_agent` Staf CS, `cs_refund_approver` Approver Refund CS |
| `online` Penjualan Online & Loyalty | `sales_manager_online` Sales Manager Online, `loyalty_manager` Loyalty Manager |
| `it` IT | `superadmin` Superadmin IT |
| `audit` Audit Internal | `auditor` Auditor Internal |

Templat §13.3 no. 1 (Direksi, Finance, Store Manager, Kasir, Merchandiser, Gudang, CS, IT/Superadmin, Auditor) ditambah jabatan yang dibutuhkan aturan pemisahan tugas §13.3 no. 3 (Sales Manager Online, Approver Refund CS, Loyalty Manager, Finance Manager = *Accounting Manager*) serta Staf Pengadaan dan Kurir (W-05, docs/08 §11.9).

Catatan teknis (terverifikasi source 19.0):
- `hr.department.company_id` = compute stored `readonly=False` dari induk, default `env.company` → ditulis eksplisit (departemen tanpa induk).
- `hr.job`: constraint `unique(name, company_id, department_id)` → nama sama di 6 PT sah; `no_of_recruitment = 0` (templat, bukan lowongan); `user_id` dikosongkan.
- `hr.job` **tidak** punya tautan ke `res.groups` → jabatan tidak memberi hak akses apa pun sampai `efn_role` terpasang.
- Isolasi antar-PT memakai rule standar `hr.hr_dept_comp_rule` / `hr.hr_job_comp_rule` (`company_id in company_ids + [False]`).
- Departemen bawaan `hr.dep_administration` ("Administration", company utama saat install) tidak disentuh.

## Cara mengubah (pengguna HR / Superadmin)
- Ganti nama, susun induk (`parent_id`), isi manajer & karyawan lewat **Karyawan → Konfigurasi → Departemen / Jabatan**. Upgrade modul tidak menimpa (noupdate).
- Departemen/jabatan yang tidak berlaku di suatu PT (mis. *Kasir* di Holding EFN, *Merchandising* di FARM): **arsipkan**, jangan hapus — `efn_role` akan menaut jabatan lewat xmlid.

## Keputusan klien yang dibutuhkan
- **Q-ORG-1:** departemen/jabatan mana yang berlaku per PT (Holding & FARM kemungkinan tanpa toko). Default: semua ada; yang tidak dipakai diarsipkan klien.
- **Q-ORG-2:** apakah CS, IT/Superadmin, dan Auditor terpusat di Holding (layanan bersama) atau ada di tiap PT. Berpengaruh ke `company_ids` user dan peran lintas PT (keputusan 18-38 *grup auditor lintas PT* masih terbuka).
- **Q-ORG-3:** nama jabatan resmi per PT (mis. *Store Manager* vs *Kepala Toko*) — ubah lewat UI.
- Nama orang per jabatan (A-5, 18-24): diisi klien, tidak lewat data modul.

## Test
`tests/test_setup_org.py` (tag `efn`): 9 departemen & 15 jabatan per PT dengan company benar; jabatan di departemen company sama; jumlah record; tanpa data orang & semua noupdate; rule multi-company standar memisahkan templat antar-PT; nama jabatan sama lintas PT sah.

## Belum
- Test **belum dijalankan** (sesi tanpa shell); modul belum ditambahkan ke `EFN_TEST_MODULES` CI (`.github/workflows/ci.yml`, wilayah devsecops).
- Peran (jabatan → grup), SoD, dan laporan review akses = `efn_role` (E-47, efn-odoo-engineer) → spesifikasi `docs/08` §12.3–§12.5. Setelah `efn_role` ada, functional menambah data peran & aturan SoD (rencana versi 19.0.1.1.0 modul ini atau `efn_setup_role`, §12.6).
