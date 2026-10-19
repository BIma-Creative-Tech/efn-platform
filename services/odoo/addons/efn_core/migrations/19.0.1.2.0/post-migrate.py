"""Migrasi efn_core 19.0.1.2.0.

1. efn.bff_url default lama `http://api-gateway:3000` -> `https://api-gateway:3000`
   (Gelombang 3, docs/04 §6 v2). Record parameter ber-`noupdate`, jadi nilai lama tidak
   tertimpa saat upgrade. Hanya nilai default lama yang persis sama yang diubah; URL kustom
   dibiarkan (dan akan ditolak klien bila http:// di produksi -> admin wajib memperbaikinya,
   tidak ada fallback plaintext).
2. efn_business_type kini compute stored dari efn_brand_code (Gelombang 3b). Kolom sudah
   ada sehingga Odoo tidak menghitung ulang otomatis; disinkronkan di sini.
"""

OLD_DEFAULT = "http://api-gateway:3000"
NEW_DEFAULT = "https://api-gateway:3000"

# G20 (docs/04 §12.1): kode mengikuti Selection terkini. Migrasi ini hanya berjalan dari versi < 19.0.1.2.0 (DB lama
# sudah dibuat ulang; tidak ada produksi), sehingga kode lama tidak perlu dipetakan di sini.
BRAND_TO_BUSINESS_TYPE = {
    "EFN": "holding",
    "GL": "retail",
    "PB": "fnb",
    "BCR": "retail",
    "BCF": "fnb",
    "FARM": "farm",
}


def migrate(cr, version):
    cr.execute(
        "UPDATE ir_config_parameter SET value = %s WHERE key = %s AND value IN (%s, %s)",
        (NEW_DEFAULT, "efn.bff_url", OLD_DEFAULT, OLD_DEFAULT + "/"),
    )
    cr.execute("UPDATE res_company SET efn_business_type = NULL WHERE efn_brand_code IS NULL")
    for code, business_type in BRAND_TO_BUSINESS_TYPE.items():
        cr.execute(
            "UPDATE res_company SET efn_business_type = %s WHERE efn_brand_code = %s",
            (business_type, code),
        )
