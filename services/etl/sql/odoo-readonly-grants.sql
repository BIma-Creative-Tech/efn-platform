-- Grant minimum untuk job ETL `sales_fact_pos` (services/etl/efn_etl/jobs.py SQL_SALES_FACT) pada DB Odoo.
-- Menutup QA Gelombang 4 U1: tabel Odoo dibuat oleh Odoo (pemilik `efn_odoo`) SETELAH init Postgres, jadi grant
-- tidak bisa ditaruh di infra/postgres/init. Usulan penerapan (devsecops): langkah idempoten di scripts/deploy.sh
-- setelah `odoo -i/-u` selesai, dijalankan sebagai pemilik tabel:
--   psql "host=postgres dbname=efn_odoo user=efn_odoo sslmode=verify-full sslrootcert=/run/secrets/efn_ca_crt" \
--        -v ON_ERROR_STOP=1 -f services/etl/sql/odoo-readonly-grants.sql
-- Pemilik objek boleh memberi GRANT tanpa superuser. GRANT idempoten (diulang tiap deploy, termasuk setelah upgrade
-- modul yang membuat ulang tabel). Nama kolom = Odoo CE 19.0 (dicek di sumber 19.0, 5 Okt 2026):
--   addons/point_of_sale/models/pos_order.py, addons/product/models/product_category.py.
-- Script gagal (ON_ERROR_STOP) bila ada kolom yang tidak ada -> sekaligus verifikasi skema 19.0 saat deploy.
--
-- Prinsip: hanya kolom yang dibaca query; TIDAK ada akses ke res_partner (nama/HP/email/alamat = PII),
-- pos_payment, res_users. Sejak Gelombang 12: kolom akuntansi non-PII untuk mart GL + log audit (lihat bagian bawah).
-- `pos_order.partner_id` hanya id (pseudonim) — tidak bisa di-join ke res_partner.
-- Koneksi ETL sudah read-only (`default_transaction_read_only=on`), tetapi hak di bawah ini juga hanya SELECT.

\set ON_ERROR_STOP on

GRANT CONNECT ON DATABASE efn_odoo TO efn_etl;
GRANT USAGE ON SCHEMA public TO efn_etl;

-- pos.order.line
GRANT SELECT (id, order_id, product_id, full_product_name, qty, price_unit, discount,
              price_subtotal, price_subtotal_incl, total_cost, write_date)
    ON public.pos_order_line TO efn_etl;

-- pos.order (config_id stored di 19.0; state: draft|cancel|paid|done)
GRANT SELECT (id, name, pos_reference, company_id, config_id, date_order, partner_id, state)
    ON public.pos_order TO efn_etl;

-- res.company (efn_brand_code dari modul efn_core)
GRANT SELECT (id, name, efn_brand_code) ON public.res_company TO efn_etl;

-- product.product -> product.template -> product.category
GRANT SELECT (id, product_tmpl_id) ON public.product_product TO efn_etl;
GRANT SELECT (id, categ_id) ON public.product_template TO efn_etl;
GRANT SELECT (id, complete_name) ON public.product_category TO efn_etl;

-- Index cursor ETL (R-O03): Odoo tidak meng-index pos_order_line.write_date; tanpa ini setiap siklus = seq scan.
-- CONCURRENTLY tidak boleh di dalam transaksi: psql -f menjalankan tiap statement autocommit (jangan pakai -1).
CREATE INDEX CONCURRENTLY IF NOT EXISTS efn_etl_pos_order_line_write_date_id_idx
    ON public.pos_order_line (write_date, id);

-- ===================================================================== Gelombang 12 (efn-data-ai-engineer)
-- Kolom diverifikasi di DB dev Odoo CE 19.0 (information_schema, 7 Okt 2026). Tetap hanya SELECT level kolom.

-- (a) Mart konsolidasi GL (services/etl/efn_etl/gl.py, F-19). partner_id dibaca HANYA untuk dicocokkan dengan
--     res_company.partner_id (inter-company); tidak disalin. Label baris (`name`), `ref`, narration TIDAK di-grant.
GRANT SELECT (id, move_id, journal_id, company_id, account_id, partner_id, date, parent_state, debit, credit, balance,
              amount_currency, currency_id, write_date)
    ON public.account_move_line TO efn_etl;
GRANT SELECT (id, name, move_type, write_date) ON public.account_move TO efn_etl;
GRANT SELECT (id, code_store, name, account_type, active, write_date) ON public.account_account TO efn_etl;
GRANT SELECT (account_account_id, res_company_id) ON public.account_account_res_company_rel TO efn_etl;
GRANT SELECT (id, name, efn_brand_code, partner_id, parent_id, parent_path, currency_id, fiscalyear_last_month,
              fiscalyear_last_day, write_date)
    ON public.res_company TO efn_etl;
GRANT SELECT (id, name) ON public.res_currency TO efn_etl;

-- (b) Salinan audit tamper-evident (services/etl/efn_etl/audit*.py). Nilai log sudah di-mask di sumber (SEC-45);
--     salinan meredaksi isi log orang/ber-mask/ref orang. origin_user_id: efn_audit >= 19.0.1.2.0 (SEC-46) — script
--     ini gagal (ON_ERROR_STOP) bila modul belum di-upgrade, sengaja (urutan deploy: -u efn_audit dulu).
GRANT SELECT (id, create_date, create_uid, user_id, origin_user_id, is_sudo, operation, model_name, res_id, res_name,
              company_id, prev_company_id, is_personal, note)
    ON public.efn_audit_log TO efn_etl;
GRANT SELECT (id, log_id, create_date, field_name, field_label, old_value, new_value, old_ref, new_ref, is_masked)
    ON public.efn_audit_log_line TO efn_etl;

-- Index cursor (R-O03). account_move_line/account_move tidak meng-index write_date secara bawaan. efn_audit_log:
-- PK id cukup (keyset id), log_id baris sudah ber-index (field index=True).
CREATE INDEX CONCURRENTLY IF NOT EXISTS efn_etl_account_move_line_write_date_id_idx
    ON public.account_move_line (write_date, id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS efn_etl_account_move_write_date_id_idx
    ON public.account_move (write_date, id);

-- ===================================================================== Gelombang 13 (efn-data-ai-engineer, Fase 1)
-- Kolom diverifikasi di DB dev Odoo CE 19.0 (information_schema, 7 Okt 2026). Tetap hanya SELECT level kolom.
-- Butuh modul product_expiry (stock_lot.expiration_date) dan pos_loyalty (pos_order_line.is_reward_line): keduanya
-- terpasang di DB dev; script gagal (ON_ERROR_STOP) bila belum, sengaja.

-- (a) Penjualan POS: retur/refund, jenis baris (reward/diskon/poin), outlet, metode bayar.
--     pos_payment: TANPA card_no, cardholder_name, card_brand, card_type, transaction_id, payment_ref_no,
--     payment_method_authcode, payment_method_issuer_bank, ticket (PCI/UU PDP). pos_order: TANPA email, mobile,
--     efn_otp_member_masked, general_customer_note, internal_note.
GRANT SELECT (refunded_orderline_id, is_reward_line) ON public.pos_order_line TO efn_etl;
GRANT SELECT (is_refund) ON public.pos_order TO efn_etl;
GRANT SELECT (id, pos_order_id, payment_method_id, amount, is_change, payment_date, write_date)
    ON public.pos_payment TO efn_etl;
GRANT SELECT (id, name, payment_method_type, is_cash_count) ON public.pos_payment_method TO efn_etl;
GRANT SELECT (id, name, company_id, warehouse_id, discount_product_id, tip_product_id, active, write_date)
    ON public.pos_config TO efn_etl;

-- (b) Produk (dim_product, indeks Price Hunter) dan biaya per company (nilai stok).
GRANT SELECT (id, product_tmpl_id, default_code, active, standard_price, write_date) ON public.product_product TO efn_etl;
GRANT SELECT (id, categ_id, name, company_id, list_price, type, is_storable, tracking, use_expiration_date, sale_ok,
              available_in_pos, active, write_date)
    ON public.product_template TO efn_etl;

-- (c) Snapshot stok & kedaluwarsa (efn_etl/stock.py). stock_quant: TANPA owner_id (partner konsinyasi), user_id.
GRANT SELECT (id, company_id, location_id, product_id, lot_id, quantity, reserved_quantity, in_date)
    ON public.stock_quant TO efn_etl;
GRANT SELECT (id, name, expiration_date) ON public.stock_lot TO efn_etl;
GRANT SELECT (id, complete_name, usage, warehouse_id) ON public.stock_location TO efn_etl;
GRANT SELECT (id, name) ON public.stock_warehouse TO efn_etl;
GRANT SELECT (id, company_id, warehouse_id, location_id, product_id, product_min_qty, product_max_qty, trigger, active)
    ON public.stock_warehouse_orderpoint TO efn_etl;

-- Index cursor pembayaran (R-O03): pos_payment.write_date tidak ber-index bawaan.
CREATE INDEX CONCURRENTLY IF NOT EXISTS efn_etl_pos_payment_write_date_id_idx
    ON public.pos_payment (write_date, id);

-- ===================================================================== Gelombang 14 (efn-data-ai-engineer, sisa SEC-61)
-- Rekonsiliasi ledger poin BFF <-> pos.order (services/etl/efn_etl/loyalty_recon.py). Kolom diverifikasi di DB dev
-- Odoo CE 19.0 + efn_pos_loyalty_ledger 19.0.1.1.0 (information_schema, 8 Okt 2026). Butuh modul efn_pos_loyalty_ledger
-- terpasang; script gagal (ON_ERROR_STOP) bila belum, sengaja.
-- pos_order.uuid = order_ref BFF (opak, bukan PII). TIDAK di-grant: efn_pos_loyalty_reservation.partner_id,
-- member_ref_masked, bff_reservation_id, otp_challenge_id, note; outbox.last_error/idempotency_key tidak dibaca.
GRANT SELECT (uuid, efn_loyalty_state) ON public.pos_order TO efn_etl;
GRANT SELECT (efn_loyalty_earn_enabled) ON public.pos_config TO efn_etl;
GRANT SELECT (id, company_id, order_id, original_order_id, kind, state, create_date)
    ON public.efn_pos_loyalty_outbox TO efn_etl;
GRANT SELECT (id, company_id, order_id, order_uuid, points, value_idr, state, create_date)
    ON public.efn_pos_loyalty_reservation TO efn_etl;
