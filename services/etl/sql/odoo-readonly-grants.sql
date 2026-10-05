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
-- pos_payment, account_*, res_users. `pos_order.partner_id` hanya id (pseudonim) — tidak bisa di-join ke res_partner.
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
