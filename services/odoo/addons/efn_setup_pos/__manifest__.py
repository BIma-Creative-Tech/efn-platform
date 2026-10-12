{
    "name": "EFN Setup - Point of Sale (Retail & Restoran)",
    "summary": "Bundel modul POS CE 19 untuk retail & F&B; demo stasiun dapur, printer dapur, denah meja.",
    "version": "19.0.1.8.0",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # ADR-013 (scope POS F&B CE 19: printer dapur, kiosk pay-after-meal; KDS custom), ADR-017, ADR-020.
    # Semua depends terverifikasi ada di odoo/odoo 19.0 (docs/06 + verifikasi 2026-10-05).
    # pos_self_order depends pos_restaurant; pos_mrp auto-install (point_of_sale + mrp);
    # l10n_id_pos auto-install (l10n_id + point_of_sale).
    "depends": [
        "point_of_sale",
        "pos_restaurant",
        "pos_self_order",
        "pos_loyalty",
        "pos_mrp",
        "pos_hr",
        "pos_discount",
        "efn_setup_company",
        # G18: W-22 default stasiun KDS (ir.default ke field efn_pos_kds) + demo stasiun pilot.
        "efn_pos_kds",
        # G18: W-25 grup kasir boleh membuat pelanggan -> kunci HP member (SEC-07/SEC-44) WAJIB ikut terpasang.
        "efn_pos_otp_gate",
        # G18 W-01/E-30: field penanda metode Poin EFN (pos.payment.method.efn_is_loyalty_points).
        "efn_pos_loyalty_ledger",
    ],
    # Gelombang 13 (Fase 1 pilot retail): nomenklatur barcode IDR (Q-25), pecahan Rupiah,
    # default SOP kas/void (R-O05, R-O06, Q-27), batas muat produk offline (Q-7).
    "data": [
        "data/pos_config_defaults.xml",
        "data/pos_retail_defaults.xml",
        "data/pos_audit_filters.xml",
        "data/pos_payment_method_data.xml",
        "data/barcode_nomenclature_data.xml",
        # SEC-63: noupdate=0 -> aturan barcode diskon dinetralkan di SEMUA nomenklatur tiap upgrade.
        "data/barcode_hardening_data.xml",
        "data/pos_bill_idr_data.xml",
        # Gelombang 14 (Fase 2 F&B): preset delivery tidak di self-order (BFF/ADR-019, UU PDP),
        # slot takeout bawaan Odoo dimatikan (jam buka demo main company). docs/08 §7.
        "data/pos_fnb_defaults.xml",
        # Gelombang 15 (SEC-75): self-order dikunci ke nothing/kiosk (ir.default + constraint +
        # normalisasi tiap upgrade). Membuka QR-order ponsel = ADR Q-23 + parameter Superadmin.
        "data/pos_self_order_lock.xml",
        # Gelombang 18 (docs/08 §9): W-22 default KDS warn 5 / late 10; W-25 grup kasir pembuat pelanggan.
        "data/pos_kds_defaults.xml",
        "data/pos_cashier_groups.xml",
        # G18 L-23 (Q-F18-7): harga member = promo pos_loyalty ber-OTP (template nonaktif), bukan pricelist POS.
        "data/pos_member_price_data.xml",
        # G18 W-01/E-30: metode bayar "Poin EFN" per PT (jurnal bank + akun poin PLACEHOLDER Q-F18-3) + tautan ke POS.
        "data/pos_loyalty_points_payment.xml",
        "data/pos_loyalty_points_link.xml",
    ],
    "demo": [
        "demo/pos_demo.xml",
        "demo/pos_retail_demo.xml",
        "demo/pos_fnb_demo.xml",
        # G18: W-22 stasiun Bakery/Pastry + Bar Kopi (tanpa expo); W-10 promo standar pos_loyalty.
        "demo/pos_kds_demo.xml",
        "demo/pos_promo_demo.xml",
    ],
    "installable": True,
    "application": False,
}
