{
    "name": "EFN Forecast Permintaan (min/max reorder musiman)",
    "summary": "G28 (K25-12, Q-35): kalender musim (Ramadan, Lebaran, Natal, gajian, akhir pekan) yang diatur bisnis, "
    "forecast v1 dari histori keluar stok (rata-rata harian x faktor musim x lead time + safety stock), usulan "
    "min/max per gudang x produk yang wajib disetujui planner sebelum menimpa reorder rule.",
    "version": "19.0.1.0.0",
    "category": "EFN/Inventory",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Odoo CE tidak punya forecast musiman (lead_days/visibility_days statis). v1 = rumus klasik tanpa ML, di luar jalur
    # transaksi: bila cron gagal, reorder rule terakhir tetap berlaku. v2 (statsforecast/Prophet) di worker data.
    "depends": ["stock", "mail", "efn_core"],
    "data": [
        "security/efn_demand_forecast_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "data/season_data.xml",
        "views/efn_forecast_season_views.xml",
        "views/efn_demand_forecast_views.xml",
        "wizard/efn_forecast_run_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
