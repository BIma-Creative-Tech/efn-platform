{
    "name": "EFN Privacy (UU PDP erasure)",
    "summary": "member_hash pelanggan + cron tarik/anonimkan/selesaikan permintaan hapus data dari BFF (docs/04 §8.4).",
    "version": "19.0.1.1.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Fit-gap F-29 / E-16 (docs/08 §4.2). privacy_lookup = jejak standar `privacy.log`
    # (+ wizard manual nama/email untuk DSAR non-member). auditlog OPSIONAL (dideteksi runtime).
    "depends": [
        "base",
        "mail",
        "privacy_lookup",
        "point_of_sale",
        "efn_core",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "data/ir_actions_server.xml",
    ],
    "installable": True,
    "application": False,
}
