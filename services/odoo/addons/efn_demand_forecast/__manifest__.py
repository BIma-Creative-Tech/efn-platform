{
    "name": "EFN Forecast Permintaan (min/max reorder musiman)",
    "summary": "G28 (K25-12, Q-35): kalender musim (Ramadan, Lebaran, Natal, gajian, akhir pekan) yang diatur bisnis, "
    "forecast v1 dari histori keluar stok (rata-rata harian x faktor musim x lead time + safety stock), usulan "
    "min/max per gudang x produk yang wajib disetujui planner sebelum menimpa reorder rule. G44: kalender forecast, "
    "run/proyeksi harian/min-max v2 dari forecast-worker lewat /json/2, sakelar engine v1|v2 per gudang.",
    # 19.0.2.0.0 (G44, docs/04 §28.2-§28.3, ADR-046, L-145..L-148): efn.forecast.calendar, efn.forecast.run (run v2;
    # wizard lama -> efn.forecast.run.wizard), efn.forecast.chunk, efn.forecast.daily, efn.demand.forecast.line,
    # group_forecast_bot, musim payday (7+7 parameter) & holiday, stock.warehouse.efn_forecast_engine.
    # Catatan merge: G43 menaikkan ke 19.0.1.1.0 (hook _efn_legacy_outflow) — signature publik v1 tidak diubah di sini.
    # 19.0.2.1.0 (G44 perbaikan): SEC-211 plafon nilai worker (efn_forecast.max_qty_per_line), SEC-212 guard tulis
    # state/source/approved_* efn.demand.forecast, run v2 ber-chatter/aktivitas.
    "version": "19.0.2.1.0",
    "category": "EFN/Inventory",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Odoo CE tidak punya forecast musiman (lead_days/visibility_days statis). v1 = rumus klasik tanpa ML, di luar jalur
    # transaksi: bila cron gagal, reorder rule terakhir tetap berlaku. v2 (statsforecast) di forecast-worker (efn-data).
    "depends": ["stock", "mail", "efn_core", "efn_audit"],
    "data": [
        "security/efn_demand_forecast_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "data/ir_config_parameter.xml",
        "data/season_data.xml",
        "data/efn_audit_rule_data.xml",
        "views/efn_forecast_season_views.xml",
        "views/efn_demand_forecast_views.xml",
        "views/efn_forecast_v2_views.xml",
        "wizard/efn_forecast_run_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
