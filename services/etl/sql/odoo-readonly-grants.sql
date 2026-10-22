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
-- G22: bagian KDS memakai \if/\gset (psql >= 10) agar CREATE INDEX CONCURRENTLY tetap di luar transaksi.

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

-- ===================================================================== Gelombang 18 (efn-data-ai-engineer, K-18-6)
-- Redeem poin = pembayaran metode "Poin EFN" (docs/04 §9.9, Odoo E-30 `efn_pos_loyalty_ledger` 19.0.2.0.0): ETL
-- membaca penanda `pos_payment_method.efn_is_loyalty_points` (ETL_LOYALTY_POINTS_MODE=payment). Kolom baru ada
-- SETELAH E-30 terpasang -> grant bersyarat agar skrip tetap lolos sebelum E-30 (satu-satunya pengecualian pola
-- "gagal bila kolom tidak ada"; jalankan ulang skrip setelah upgrade modul E-30). pos_payment & pos_order.uuid sudah
-- di-grant di atas.
DO $grant_g18$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'pos_payment_method' AND column_name = 'efn_is_loyalty_points'
    ) THEN
        EXECUTE 'GRANT SELECT (efn_is_loyalty_points) ON public.pos_payment_method TO efn_etl';
    ELSE
        RAISE NOTICE 'efn_is_loyalty_points belum ada (E-30 belum terpasang): grant G18 dilewati';
    END IF;
END
$grant_g18$;

-- ===================================================================== Gelombang 18 (efn-data-ai-engineer, chain epoch)
-- Penanda restore untuk salinan audit (efn_etl/audit.py `decide_epoch`): runbook restore mengubah
-- ir.config_parameter `efn_audit.chain_epoch` SETELAH DB Odoo dipulihkan. efn_etl TIDAK diberi akses ke
-- ir_config_parameter (memuat rahasia, mis. database.secret): view baris-tunggal milik efn_odoo (dijalankan dengan hak
-- pemilik) + security_barrier (predikat/fungsi pemanggil tidak didorong ke dalam view). Dibuat ulang tiap deploy
-- (idempoten) — juga setelah restore, karena view ikut DB yang dipulihkan atau hilang bila DB dibuat ulang.
CREATE OR REPLACE VIEW public.efn_etl_audit_epoch WITH (security_barrier) AS
    SELECT value::text AS marker FROM public.ir_config_parameter WHERE key = 'efn_audit.chain_epoch';
REVOKE ALL ON public.efn_etl_audit_epoch FROM PUBLIC;
GRANT SELECT ON public.efn_etl_audit_epoch TO efn_etl;

-- ===================================================================== Gelombang 21 (efn-data-ai-engineer, ADR-036 §0.7)
-- Laporan penjualan per wilayah domisili (docs/04 §13.2, E-38b, K-36-5 diralat). efn_etl TETAP tanpa grant apa pun
-- pada res_partner (nama/alamat/kota/kode pos/kontak terenkripsi ADR-036) dan efn_region: dua view milik efn_odoo di
-- skema `efn_etl` (dijalankan dengan hak pemilik; JANGAN diberi security_invoker). Kolom sumber yang dibaca view:
--   res_partner : id, company_id, efn_region_id (plaintext menurut ADR-036 §0.2; diisi dropdown kasir/back office/BFF,
--                 dikosongkan erasure K-36-4)
--   efn_region  : id, code (^\d{2}\.\d{2}$ kab/kota Kemendagri), name, state_id, active (bila ada), write_date
--   res_country_state: id, code, name
-- Tanpa security_barrier: view tidak menyaring baris demi keamanan (pembatas = daftar kolom), sehingga predikat join
-- ETL (`pr.partner_id = o.partner_id`) boleh didorong ke dalam view (index PK res_partner).
-- Bersyarat: dilewati (NOTICE) sampai modul efn_region terpasang -> jalankan ulang skrip setelah `-i efn_region`.
-- ETL membaca view ini hanya bila ETL_PARTNER_REGION_ENABLED=true (efn_etl/region.py). Kolom nama efn_region/state
-- bisa jsonb (translate=True) atau varchar: ekspresi dipilih dari information_schema (kolom & tipe dicek saat deploy).
-- Catatan: nama skema = nama role efn_etl -> ETL memaksa search_path=public (efn_etl/adapters.py) dan selalu
-- merujuk view berkualifikasi. Skema hanya milik efn_odoo; efn_etl hanya USAGE (tidak bisa membuat objek bayangan).
DO $grant_g21$
DECLARE
    region_name text;
    state_join  text := '';
    state_code  text := $q$''$q$;
    state_name  text := $q$''$q$;
    active_expr text := 'TRUE';
    dtype       text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'res_partner' AND column_name = 'efn_region_id'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'efn_region' AND column_name = 'code'
    ) THEN
        RAISE NOTICE 'efn_region belum terpasang: view efn_etl.partner_region / efn_etl.region dilewati (G21)';
        RETURN;
    END IF;

    EXECUTE 'CREATE SCHEMA IF NOT EXISTS efn_etl';
    EXECUTE 'REVOKE ALL ON SCHEMA efn_etl FROM PUBLIC';
    EXECUTE 'GRANT USAGE ON SCHEMA efn_etl TO efn_etl';

    -- (a) partner -> kode wilayah. Kontrak kolom (ADR-036 §0.7): partner_id, company_id, region_code. Hanya partner
    --     yang punya wilayah (INNER JOIN): partner tanpa wilayah / sudah dihapus (erasure) tidak muncul.
    EXECUTE $v$
        CREATE OR REPLACE VIEW efn_etl.partner_region AS
        SELECT p.id AS partner_id, p.company_id, r.code::text AS region_code
        FROM public.res_partner p
        JOIN public.efn_region r ON r.id = p.efn_region_id
    $v$;
    EXECUTE 'REVOKE ALL ON efn_etl.partner_region FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON efn_etl.partner_region TO efn_etl';

    -- (b) master wilayah -> dim_region (data Kemendagri, bukan data pribadi).
    SELECT data_type INTO dtype FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'efn_region' AND column_name = 'name';
    region_name := CASE
        WHEN dtype = 'jsonb' THEN $q$COALESCE(r.name ->> 'id_ID', r.name ->> 'en_US', '')$q$
        WHEN dtype IS NULL THEN $q$''$q$
        ELSE $q$COALESCE(r.name::text, '')$q$
    END;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'efn_region' AND column_name = 'active'
    ) THEN
        active_expr := 'COALESCE(r.active, TRUE)';
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'efn_region' AND column_name = 'state_id'
    ) THEN
        state_join := 'LEFT JOIN public.res_country_state s ON s.id = r.state_id';
        state_code := $q$COALESCE(s.code::text, '')$q$;
        SELECT data_type INTO dtype FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'res_country_state' AND column_name = 'name';
        state_name := CASE
            WHEN dtype = 'jsonb' THEN $q$COALESCE(s.name ->> 'id_ID', s.name ->> 'en_US', '')$q$
            ELSE $q$COALESCE(s.name::text, '')$q$
        END;
    END IF;
    EXECUTE format(
        $v$
        CREATE OR REPLACE VIEW efn_etl.region AS
        SELECT r.code::text AS region_code, %s AS name, %s AS state_code, %s AS state_name, %s AS active,
               COALESCE(r.write_date, r.create_date) AS write_date
        FROM public.efn_region r
        %s
        $v$,
        region_name, state_code, state_name, active_expr, state_join
    );
    EXECUTE 'REVOKE ALL ON efn_etl.region FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON efn_etl.region TO efn_etl';
END
$grant_g21$;

-- ===================================================================== Gelombang 22 (efn-data-ai-engineer)
-- (a) Mart KPI dapur (services/etl/efn_etl/kds.py, docs/04 §14.B, docs/08 §7.3 F-54). Kolom DIPILIH: TIDAK di-grant
--     table_label, tracking_number, internal_note, session_id (tiket) maupun product_name, attribute_text, note,
--     combo_parent_name, order_line_uuid (baris); stasiun TANPA access_token, device_user_ids, last_seen,
--     token_rotated_at, fallback_printer_id. Bersyarat (psql \if): dilewati bila efn_pos_kds belum terpasang ->
--     jalankan ulang skrip setelah `-i efn_pos_kds`. ETL membaca hanya bila ETL_ENABLE_KDS=true.
SELECT to_regclass('public.efn_kds_ticket') IS NOT NULL
   AND to_regclass('public.efn_kds_ticket_line') IS NOT NULL
   AND to_regclass('public.efn_kds_station') IS NOT NULL AS efn_g22_kds \gset
\if :efn_g22_kds
GRANT SELECT (id, company_id, station_id, order_id, config_id, preset_name, course_index, sequence_no, state, is_rush,
              printed_offline, sent_at, started_at, ready_at, served_at, cancelled_at, write_date)
    ON public.efn_kds_ticket TO efn_etl;
GRANT SELECT (id, ticket_id, qty_delta) ON public.efn_kds_ticket_line TO efn_etl;
GRANT SELECT (id, name, company_id, mode, warn_minutes, late_minutes, active, write_date)
    ON public.efn_kds_station TO efn_etl;
-- Index cursor (R-O03): efn_kds_ticket.write_date tidak ber-index bawaan (bump/recall menulis ulang tiket).
CREATE INDEX CONCURRENTLY IF NOT EXISTS efn_etl_efn_kds_ticket_write_date_id_idx
    ON public.efn_kds_ticket (write_date, id);
\else
\echo 'efn_pos_kds belum terpasang: grant G22 KDS dilewati'
\endif

-- (a2) Kanal online tiket KDS (E-29a efn_online_kds_bridge: efn_kds_ticket.efn_online_order_id). Bersyarat: kolom
--      baru ada setelah modul jembatan terpasang (ETL_KDS_ONLINE_ENABLED=true hanya setelah itu).
DO $grant_g22_kds_online$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'efn_kds_ticket' AND column_name = 'efn_online_order_id'
    ) THEN
        EXECUTE 'GRANT SELECT (efn_online_order_id) ON public.efn_kds_ticket TO efn_etl';
    ELSE
        RAISE NOTICE 'efn_kds_ticket.efn_online_order_id belum ada (E-29a belum terpasang): grant G22 dilewati';
    END IF;
END
$grant_g22_kds_online$;

-- (b) Rekonsiliasi poin online (services/etl/efn_etl/loyalty_recon_online.py, docs/04 §14.C.5). Dua view milik efn_odoo
--     di skema efn_etl (pola G21; dijalankan dengan hak pemilik, JANGAN security_invoker) sehingga efn_etl TIDAK diberi
--     akses ke account_payment (memo/partner/rekening), efn_online_order (hash kode ambil/bayar, payment_ref) maupun
--     efn_online_refund (provider_refund_ref). Kolom sumber yang dibaca view:
--       account_payment              : id, company_id, journal_id, payment_type, amount, date, state
--       account_move__account_payment: payment_id, invoice_id (M2M `invoice_ids` account.payment Odoo 18+)
--       efn_online_order             : id, platform_order_ref (part_id BFF, UUID opak), company_id, state, invoice_id
--       efn_online_refund            : order_id, credit_note_id, state
--       jurnal Poin EFN              : account_journal.efn_is_loyalty_points bila E-28 menambahkannya; jika tidak,
--                                      journal_id metode POS `pos_payment_method.efn_is_loyalty_points` (jurnal PEFN per PT)
--     Status pembayaran dihitung: in_process | paid (Odoo 18+: draft/canceled/rejected tidak). Tanggal = account_payment.date.
--     Bersyarat: dilewati (NOTICE) sampai efn_online_order + penanda jurnal Poin EFN ada.
DO $grant_g22_online$
DECLARE
    journals text;
BEGIN
    IF to_regclass('public.efn_online_order') IS NULL OR to_regclass('public.efn_online_refund') IS NULL
       OR to_regclass('public.account_move__account_payment') IS NULL THEN
        RAISE NOTICE 'efn_online_order / account_move__account_payment belum ada: view G22 poin online dilewati';
        RETURN;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'account_journal' AND column_name = 'efn_is_loyalty_points'
    ) THEN
        journals := 'SELECT j.id FROM public.account_journal j WHERE j.efn_is_loyalty_points';
    ELSIF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'pos_payment_method' AND column_name = 'efn_is_loyalty_points'
    ) THEN
        journals := 'SELECT m.journal_id FROM public.pos_payment_method m '
                    'WHERE m.efn_is_loyalty_points AND m.journal_id IS NOT NULL';
    ELSE
        RAISE NOTICE 'penanda jurnal Poin EFN belum ada (E-30/E-28): view G22 poin online dilewati';
        RETURN;
    END IF;

    EXECUTE 'CREATE SCHEMA IF NOT EXISTS efn_etl';
    EXECUTE 'REVOKE ALL ON SCHEMA efn_etl FROM PUBLIC';
    EXECUTE 'GRANT USAGE ON SCHEMA efn_etl TO efn_etl';

    -- Faktur: pembayaran masuk (+, redeem). Nota kredit refund: pembayaran keluar (−, restore).
    EXECUTE format(
        $v$
        CREATE OR REPLACE VIEW efn_etl.online_points_payment AS
        SELECT p.id AS payment_id, o.id AS online_order_id, o.platform_order_ref::text AS part_ref, p.company_id,
               p.date AS paid_on,
               CASE WHEN p.payment_type = 'outbound' THEN -p.amount ELSE p.amount END AS amount
        FROM public.account_payment p
        JOIN public.account_move__account_payment rel ON rel.payment_id = p.id
        JOIN public.efn_online_order o ON o.invoice_id = rel.invoice_id
        WHERE p.state IN ('in_process', 'paid') AND p.journal_id IN (%1$s)
        UNION ALL
        SELECT p.id, o.id, o.platform_order_ref::text, p.company_id, p.date,
               CASE WHEN p.payment_type = 'outbound' THEN -p.amount ELSE p.amount END
        FROM public.account_payment p
        JOIN public.account_move__account_payment rel ON rel.payment_id = p.id
        JOIN public.efn_online_refund r ON r.credit_note_id = rel.invoice_id
        JOIN public.efn_online_order o ON o.id = r.order_id
        WHERE p.state IN ('in_process', 'paid') AND p.journal_id IN (%1$s)
        $v$,
        journals
    );
    EXECUTE 'REVOKE ALL ON efn_etl.online_points_payment FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON efn_etl.online_points_payment TO efn_etl';

    -- Status bagian + jumlah refund yang tidak gagal (cek earn saat picked_up / clawback).
    EXECUTE $v$
        CREATE OR REPLACE VIEW efn_etl.online_order_ref AS
        SELECT o.id AS online_order_id, o.platform_order_ref::text AS part_ref, o.company_id, o.state::text AS state,
               (SELECT count(*) FROM public.efn_online_refund r WHERE r.order_id = o.id AND r.state <> 'failed')
                   AS refunds
        FROM public.efn_online_order o
    $v$;
    EXECUTE 'REVOKE ALL ON efn_etl.online_order_ref FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON efn_etl.online_order_ref TO efn_etl';
END
$grant_g22_online$;
