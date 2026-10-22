"""G22 W-17 (D-22-13, D-22-15): kolom baru ``inactivity_expiry_months``, ``expiry_notice_days``, ``activity_epoch``.

Saat kolom ditambahkan, ORM mengisi baris lama dengan default yang dihitung dari company aktif superuser (bisa 12/30
untuk SEMUA baris bila company itu EFN). Normalisasi:
- baris PT selain EFN -> 0 / NULL (parameter global-only; tanpa ini draft lama gagal validasi saat disunting);
- versi global yang sudah diputuskan (non-draft) -> 0 / NULL agar body publikasi versi lama tetap byte-identik
  (kunci W-17 dihilangkan; BFF memakai fallback env);
- draft global -> default kontrak 12 bulan / 30 hari bila kosong.
SQL langsung (tanpa ORM) karena record non-draft read-only untuk semua termasuk sudo.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE efn_loyalty_policy
           SET inactivity_expiry_months = 0, expiry_notice_days = 0, activity_epoch = NULL
         WHERE company_code IS DISTINCT FROM 'EFN' OR state <> 'draft'
        """
    )
    cr.execute(
        """
        UPDATE efn_loyalty_policy
           SET inactivity_expiry_months = 12
         WHERE company_code = 'EFN' AND state = 'draft' AND COALESCE(inactivity_expiry_months, 0) = 0
        """
    )
    cr.execute(
        """
        UPDATE efn_loyalty_policy
           SET expiry_notice_days = 30
         WHERE company_code = 'EFN' AND state = 'draft' AND COALESCE(expiry_notice_days, 0) = 0
        """
    )
