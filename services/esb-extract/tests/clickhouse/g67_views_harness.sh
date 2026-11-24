#!/bin/sh
# G67 (docs/04 §31.6.1 + §31.7): uji PERILAKU view G67 di ClickHouse 24.8 dengan fixture SINTETIS.
# BELUM PERNAH DIJALANKAN oleh penulis (tanpa shell) -- dijalankan lead, hasil dicatat di laporan efn-data-ai-engineer.
#
# Badan tabel + view = infra/clickhouse/init/24-g67-esb-pl-waste-fillrate.sql APA ADANYA (efn_dwh./efn_esb. diganti ke
# database uji `efn_g67_v_test`). Kasus wajib §31.6.1: kirim parsial, kirim lebih (rasio 1), dua GD untuk satu GTR,
# GD beda satuan, GTR tanpa GD (zero), GTR belum matang, GD dihapus lalu ditarik ulang (slice versi baru -> kirim
# hilang) + GD trans_type lain diabaikan, status batal & bypass tidak dihitung. Ditambah: P&L hanya versi slice
# ber-checkpoint + selisih total brand, kelas purpose/status jurnal item, resolusi product_id, dan negatif row policy
# §31.7 (scope PB tidak melihat WTZ, brs terisi -> 0 baris, company_code '' tidak terlihat) memakai predikat PRODUKSI
# (access/insight-esb-policy.sh, ESB_POLICY_DEFINE_ONLY=1). Semua objek uji dihapus di akhir.
#
# Contoh (dev; pola g52_row_policy_harness.sh):
#   docker compose run --rm --entrypoint sh \
#     -v "$(pwd -W)/services/esb-extract/tests/clickhouse:/harness:ro" clickhouse-init \
#     -c 'CH="clickhouse-client --config-file /tmp/efn-clickhouse-client.xml" \
#         POLICY_SH=/init/access/insight-esb-policy.sh DDL24=/init/schema/24-g67-esb-pl-waste-fillrate.sql \
#         sh /harness/g67_views_harness.sh'
set -eu

: "${CH:?CH = perintah klien admin ClickHouse (TLS)}"
HERE="$(dirname "$0")"
POLICY_SH="${POLICY_SH:-${HERE}/../../../../infra/clickhouse/access/insight-esb-policy.sh}"
DDL24="${DDL24:-${HERE}/../../../../infra/clickhouse/init/24-g67-esb-pl-waste-fillrate.sql}"
# shellcheck disable=SC2034 # dibaca file yang di-source di bawah
ESB_POLICY_DEFINE_ONLY=1
# shellcheck source=/dev/null
. "$POLICY_SH"

KEY="abababababababababababababababababababababababababababababababab"
DB=efn_g67_v_test
TUSER=g67_v_user
TPW="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
FAILS=0

cleanup() {
  $CH --multiquery <<EOF >/dev/null 2>&1 || true
DROP USER IF EXISTS ${TUSER};
DROP ROLE IF EXISTS g67_v_reader;
DROP DATABASE IF EXISTS ${DB} SYNC;
EOF
}
trap cleanup EXIT INT TERM
cleanup

SCHEMA_SQL="$(grep -v '^--' "$DDL24" | sed -e "s/efn_dwh\./${DB}./g" -e "s/efn_esb\./${DB}./g")"
case "$SCHEMA_SQL" in
  *efn_dwh.*|*efn_esb.*) echo "g67_views_harness: masih ada rujukan efn_dwh/efn_esb" >&2; exit 1 ;;
esac
case "$SCHEMA_SQL" in
  *"CREATE OR REPLACE VIEW ${DB}.v_esb_fill_rate_line AS"*) ;;
  *) echo "g67_views_harness: view fill rate tidak ditemukan di ${DDL24}" >&2; exit 1 ;;
esac

D30="toDate(now(), 'Asia/Jakarta') - 30"
D1="toDate(now(), 'Asia/Jakarta') - 1"
V1="toDateTime64('2026-11-01 00:00:00.000', 3, 'UTC')"
V2="toDateTime64('2026-11-02 00:00:00.000', 3, 'UTC')"

$CH --multiquery <<EOF
CREATE DATABASE ${DB};
-- Tabel G41 yang dirujuk view (kolom = 12-g41-esb.sql).
CREATE TABLE ${DB}.extract_checkpoint (brand LowCardinality(String), entity LowCardinality(String), scope_key String,
    day Date, status LowCardinality(String), rows UInt64, calls UInt32, finished_at DateTime64(3, 'UTC'),
    written_at DateTime64(3, 'UTC') DEFAULT now64(3))
    ENGINE = ReplacingMergeTree(finished_at) ORDER BY (brand, entity, scope_key, day);
CREATE TABLE ${DB}.dim_esb_branch (brand LowCardinality(String), company_code LowCardinality(String),
    branch_id UInt32, branch_code String, branch_name String, is_ck UInt8, is_store UInt8, active UInt8,
    _version DateTime64(3, 'UTC'), _loaded_at DateTime64(3, 'UTC') DEFAULT now64(3))
    ENGINE = ReplacingMergeTree(_version) ORDER BY (brand, branch_id);
EOF
printf '%s\n' "$SCHEMA_SQL" | $CH --multiquery

$CH --multiquery <<EOF
INSERT INTO ${DB}.dim_esb_branch (brand, company_code, branch_id, branch_code, branch_name, is_ck, is_store, active,
    _version) VALUES
    ('pb', 'PB', 4, 'CK1', 'CK Sintetis', 1, 0, 1, ${V1}), ('pb', 'PB', 7, 'T07', 'Toko Tujuh', 0, 1, 1, ${V1}),
    ('wtz', 'WTZ', 9, 'W09', 'Toko WTZ', 0, 1, 1, ${V1});

-- ---------------------------------------------------------------- fill rate
INSERT INTO ${DB}.fact_esb_transfer_request_line (brand, company_code, transfer_date, transfer_num,
    requester_branch_id, supplier_branch_id, status_id, status_name, category_type, bypass_gd, finished,
    purchase_request_num, product_id, product_detail_id, product_code, product_name, uom, request_qty, qty,
    pending_qty, available_qty, stock_qty, _version) VALUES
    ('pb', 'PB', ${D30}, 'TF1', 7, 4, 3, 'Authorized', 'C', 0, 0, '', 1, 1, 'P1', 'Roti', 'EA', 10, 10, 0, 0, 0, ${V1}),
    ('pb', 'PB', ${D30}, 'TF2', 7, 4, 3, 'Authorized', 'C', 0, 0, '', 1, 1, 'P1', 'Roti', 'EA', 10, 10, 0, 0, 0, ${V1}),
    ('pb', 'PB', ${D30}, 'TF3', 7, 4, 6, 'Finished', 'C', 0, 1, '', 1, 1, 'P1', 'Roti', 'EA', 10, 10, 0, 0, 0, ${V1}),
    ('pb', 'PB', ${D30}, 'TF4', 7, 4, 3, 'Authorized', 'C', 0, 0, '', 1, 1, 'P1', 'Roti', 'EA', 5, 5, 0, 0, 0, ${V1}),
    ('pb', 'PB', ${D30}, 'TF5', 7, 4, 3, 'Authorized', 'C', 0, 0, '', 2, 2, 'P2', 'Kue', 'EA', 8, 8, 0, 0, 0, ${V1}),
    ('pb', 'PB', ${D30}, 'TF7', 7, 4, 3, 'Authorized', 'C', 0, 0, '', 1, 1, 'P1', 'Roti', 'EA', 9, 9, 0, 0, 0, ${V1}),
    ('pb', 'PB', ${D30}, 'TF8', 7, 4, 9, 'Cancelled', 'C', 0, 0, '', 1, 1, 'P1', 'Roti', 'EA', 4, 4, 0, 0, 0, ${V1}),
    ('pb', 'PB', ${D30}, 'TF9', 7, 4, 3, 'Authorized', 'C', 1, 0, '', 1, 1, 'P1', 'Roti', 'EA', 4, 4, 0, 0, 0, ${V1}),
    ('pb', '', ${D30}, 'TFX', 7, 4, 3, 'Authorized', 'C', 0, 0, '', 1, 1, 'P1', 'Roti', 'EA', 4, 4, 0, 0, 0, ${V1}),
    ('pb', 'PB', ${D1}, 'TF6', 7, 4, 3, 'Authorized', 'C', 0, 0, '', 1, 1, 'P1', 'Roti', 'EA', 7, 7, 0, 0, 0, ${V1}),
    ('wtz', 'WTZ', ${D30}, 'TW1', 9, 5, 3, 'Authorized', 'C', 0, 0, '', 1, 1, 'W1', 'Roti', 'EA', 3, 3, 0, 0, 0, ${V1});
INSERT INTO ${DB}.fact_esb_delivery_ref_line (brand, company_code, delivery_date, doc_num, reference_num,
    trans_type, ref_date, origin_branch_id, dest_branch_id, product_code, uom, qty_sent, qty_received, _version) VALUES
    ('pb', 'PB', ${D30} + 1, 'GD1', 'TF1', 'Transfer Request', NULL, 4, 7, 'P1', 'EA', 6, 6, ${V1}),
    ('pb', 'PB', ${D30} + 2, 'GD2', 'TF2', 'Transfer Request', NULL, 4, 7, 'P1', 'EA', 15, 15, ${V1}),
    ('pb', 'PB', ${D30} + 1, 'GD3A', 'TF3', 'Transfer Request', NULL, 4, 7, 'P1', 'EA', 4, 4, ${V1}),
    ('pb', 'PB', ${D30} + 3, 'GD3B', 'TF3', 'Transfer Request', NULL, 4, 7, 'P1', 'EA', 6, 5, ${V1}),
    ('pb', 'PB', ${D30} + 1, 'GD4', 'TF4', 'Transfer Request', NULL, 4, 7, 'P1', 'BOX', 1, 1, ${V1}),
    ('pb', 'PB', ${D30} + 1, 'GD5', 'TF5', 'Lain', NULL, 4, 7, 'P2', 'EA', 8, 8, ${V1}),
    ('pb', 'PB', ${D30} + 5, 'GD7', 'TF7', 'Transfer Request', NULL, 4, 7, 'P1', 'EA', 9, 9, ${V1}),
    ('pb', 'PB', ${D30} + 5, 'GDZ', 'TF999', 'Transfer Request', NULL, 4, 7, 'P9', 'EA', 1, 1, ${V2});
-- ---------------------------------------------------------------- jurnal item
INSERT INTO ${DB}.fact_esb_item_journal_line (brand, company_code, journal_date, journal_num, detail_id, line_no,
    branch_id, location_id, location_name, status_id, status_name, template_name, purpose_id, purpose_name, product_id,
    product_detail_id, product_name, uom, qty, hpp, value, _version) VALUES
    ('pb', 'PB', ${D30}, 'IJ1', 1, 0, 7, 1, 'L', 3, 'Authorized', '', 1, 'WASTE', 501, 1, 'Roti', 'EA', -2, 50, -100, ${V1}),
    ('pb', 'PB', ${D30}, 'IJ1', 2, 0, 7, 1, 'L', 3, 'Authorized', '', 1, ' Waste ', 502, 1, 'Kue', 'EA', -1, 10, -10, ${V1}),
    ('pb', 'PB', ${D30}, 'IJ2', 3, 0, 7, 1, 'L', 6, 'Finished', '', 2, 'PRODUCTION SUPPLIES', 501, 1, 'Roti', 'EA', -1, 30, -30, ${V1}),
    ('pb', 'PB', ${D30}, 'IJ3', 4, 0, 7, 1, 'L', 1, 'Draft', '', 1, 'WASTE', 501, 1, 'Roti', 'EA', -5, 100, -500, ${V1}),
    ('wtz', 'WTZ', ${D30}, 'IJW', 5, 0, 9, 1, 'L', 3, 'Authorized', '', 1, 'WASTE', 9, 1, 'X', 'EA', -1, 1, -1, ${V1});
INSERT INTO ${DB}.dim_esb_product_id (brand, company_code, product_id, product_code, source, _version) VALUES
    ('pb', 'PB', 501, 'RM501', 'gtr', ${V1});
-- ---------------------------------------------------------------- P&L (tahun 2025 tertutup)
INSERT INTO ${DB}.fact_esb_profit_loss (brand, company_code, period_month, scope, branch_id, line_type, section,
    account_no, account_key, account_name, amount, slice_year, source_lines, _version) VALUES
    ('pb', 'PB', '2025-03-01', 'branch', 7, 'summary', 'netSales', '', '', 'Net', 100, 2025, 1, ${V1}),
    ('pb', 'PB', '2025-03-01', 'branch', 7, 'account', 'cogs', '5 1', '5.1', 'Lama', 40, 2025, 1, ${V1}),
    ('pb', 'PB', '2025-03-01', 'branch', 7, 'summary', 'netSales', '', '', 'Net', 200, 2025, 1, ${V2}),
    ('pb', 'PB', '2025-03-01', 'brand', 0, 'summary', 'netSales', '', '', 'Net', 250, 2025, 1, ${V2}),
    ('wtz', 'WTZ', '2025-03-01', 'branch', 9, 'summary', 'netSales', '', '', 'Net', 77, 2025, 1, ${V2});
-- ---------------------------------------------------------------- checkpoint (versi slice)
INSERT INTO ${DB}.extract_checkpoint (brand, entity, scope_key, day, status, rows, calls, finished_at) VALUES
    ('pb', 'transfer_request', '*', ${D30}, 'done', 0, 0, ${V1}),
    ('pb', 'transfer_request', '*', ${D1}, 'done', 0, 0, ${V1}),
    ('wtz', 'transfer_request', '*', ${D30}, 'done', 0, 0, ${V1}),
    ('pb', 'delivery_ref', '*', ${D30} + 1, 'done', 0, 0, ${V1}),
    ('pb', 'delivery_ref', '*', ${D30} + 2, 'done', 0, 0, ${V1}),
    ('pb', 'delivery_ref', '*', ${D30} + 3, 'done', 0, 0, ${V1}),
    ('pb', 'delivery_ref', '*', ${D30} + 5, 'done', 0, 0, ${V2}),
    ('pb', 'item_journal', '*', ${D30}, 'done', 0, 0, ${V1}),
    ('wtz', 'item_journal', '*', ${D30}, 'done', 0, 0, ${V1}),
    ('pb', 'profit_loss', '7', '2025-01-01', 'done', 0, 0, ${V2}),
    ('pb', 'profit_loss', '*', '2025-01-01', 'done', 0, 0, ${V2}),
    ('wtz', 'profit_loss', '9', '2025-01-01', 'done', 0, 0, ${V2});
EOF

a() {
  # $1 query admin -> satu nilai
  $CH --query "$1" 2>/dev/null || echo ERR
}

expect() {
  # $1 nama uji, $2 hasil, $3 harapan
  if [ "$2" = "$3" ]; then echo "OK   $1 ($2)"; else echo "FAIL $1 (dapat $2, harap $3)"; FAILS=$((FAILS + 1)); fi
}

L="${DB}.v_esb_fill_rate_line"
line() {
  # $1 transfer_num -> qty_sent|uom_mismatch|is_mature|is_counted
  a "SELECT concat(toString(toFloat64(qty_sent)), '|', toString(uom_mismatch), '|', toString(is_mature), '|',
     toString(is_counted)) FROM ${L} WHERE brand = 'pb' AND transfer_num = '$1'"
}
expect "fill: kirim parsial TF1" "$(line TF1)" "6|0|1|1"
expect "fill: kirim lebih TF2 (qty_sent apa adanya, rasio dibatasi di agregat)" "$(line TF2)" "15|0|1|1"
expect "fill: dua GD untuk satu GTR TF3" "$(line TF3)" "10|0|1|1"
expect "fill: GD beda satuan TF4 -> uom_mismatch" "$(line TF4)" "0|1|1|1"
expect "fill: GTR tanpa GD + GD trans_type lain diabaikan TF5" "$(line TF5)" "0|0|1|1"
expect "fill: GTR belum matang TF6" "$(line TF6)" "0|0|0|0"
expect "fill: GD dihapus lalu slice ditarik ulang TF7 -> kirim hilang" "$(line TF7)" "0|0|1|1"
expect "fill: status batal TF8 tidak dihitung" "$(line TF8)" "0|0|1|0"
expect "fill: bypass_gd TF9 tidak dihitung" "$(line TF9)" "0|0|1|0"
expect "fill: kiriman pertama TF3 (+1 hari)" \
  "$(a "SELECT dateDiff('day', transfer_date, first_delivery_date) FROM ${L} WHERE transfer_num = 'TF3'")" 1
expect "fill: first_delivery_date NULL tanpa kiriman TF5" \
  "$(a "SELECT isNull(first_delivery_date) FROM ${L} WHERE transfer_num = 'TF5'")" 1

DAILY_COLS="concat(toString(requests), '|', toString(requests_full), '|', toString(lines), '|', toString(lines_full),
  '|', toString(lines_zero), '|', toString(round(line_ratio_sum, 4)), '|', toString(lines_uom_mismatch), '|',
  toString(lines_open), '|', toString(lead_days_sum), '|', toString(lead_days_n))"
# D30: baris terhitung TF1 (0.6), TF2 (1), TF3 (1), TF5 (0), TF7 (0) -> rasio 2.6; penuh TF2+TF3; nol TF5+TF7;
# mismatch TF4; waktu kirim TF1 1 + TF2 2 + TF3 1 = 4 / 3.
expect "fill daily D30 (aditif)" \
  "$(a "SELECT ${DAILY_COLS} FROM ${DB}.v_esb_fill_rate_daily WHERE brand = 'pb' AND company_code = 'PB'
        AND transfer_date = ${D30}")" "5|2|5|2|2|2.6|1|0|4|3"
expect "fill daily D1 (masih berjalan)" \
  "$(a "SELECT ${DAILY_COLS} FROM ${DB}.v_esb_fill_rate_daily WHERE brand = 'pb' AND transfer_date = ${D1}")" \
  "0|0|0|0|0|0|0|1|0|0"
expect "fill daily: nama outlet & pemasok" \
  "$(a "SELECT concat(branch_name, '|', supplier_name) FROM ${DB}.v_esb_fill_rate_daily
        WHERE brand = 'pb' AND company_code = 'PB' AND transfer_date = ${D30}")" "Toko Tujuh|CK Sintetis"
# P1 EA: TF1 (10/6), TF2 (10/15 -> capped 10), TF3 (10/10), TF7 (9/0) -> lines 4, req 39, capped 26, sent 31.
expect "fill product monthly P1 EA" \
  "$(a "SELECT concat(toString(lines), '|', toString(toFloat64(request_qty)), '|', toString(toFloat64(sent_capped)),
        '|', toString(toFloat64(qty_sent))) FROM ${DB}.v_esb_fill_rate_product_monthly
        WHERE brand = 'pb' AND company_code = 'PB' AND product_code = 'P1' AND uom = 'EA'")" "4|39|26|31"
expect "fill product monthly: P1 BOX (mismatch) tidak ada" \
  "$(a "SELECT count() FROM ${DB}.v_esb_fill_rate_product_monthly WHERE uom = 'BOX'")" 0

# ---------------------------------------------------------------- jurnal item
U="${DB}.v_esb_item_usage_daily"
expect "waste: status Draft tidak dihitung, ' Waste ' dinormalkan (100 + 10)" \
  "$(a "SELECT concat(toString(toFloat64(value_net)), '|', toString(toFloat64(value_out)), '|',
        toString(toFloat64(value_in)), '|', toString(docs), '|', toString(lines)) FROM ${U}
        WHERE brand = 'pb' AND usage_class = 'waste'")" "110|110|0|1|2"
expect "waste: PRODUCTION SUPPLIES -> internal_use" \
  "$(a "SELECT toFloat64(value_net) FROM ${U} WHERE brand = 'pb' AND usage_class = 'internal_use'")" 30
expect "waste: kode produk via dim_esb_product_id / ID:<product_id>" \
  "$(a "SELECT arrayStringConcat(arraySort(groupArray(product_code)), ',')
        FROM ${DB}.v_esb_item_usage_product_monthly WHERE brand = 'pb' AND usage_class = 'waste'")" "ID:502,RM501"

# ---------------------------------------------------------------- P&L
P="${DB}.v_esb_pl_monthly"
expect "pl: hanya versi slice ber-checkpoint (200, bukan 100; akun versi lama hilang)" \
  "$(a "SELECT concat(toString(toFloat64(sum(amount))), '|', toString(count())) FROM ${P}
        WHERE brand = 'pb' AND scope = 'branch'")" "200|1"
expect "pl: baris total brand" \
  "$(a "SELECT concat(branch_name, '|', toString(is_partial)) FROM ${P} WHERE brand = 'pb' AND scope = 'brand'")" \
  "(Total brand)|0"
expect "pl: quality pl_branch_vs_brand (250 - 200)" \
  "$(a "SELECT value FROM ${DB}.v_esb_g67_quality WHERE check_name = 'pl_branch_vs_brand' AND brand = 'pb'")" 50

# ---------------------------------------------------------------- row policy §31.7 (predikat produksi, kelas PT)
P_PT="$(esb_policy_predicate "$KEY" PT)"
$CH --log_queries=0 --multiquery <<EOF
CREATE VIEW ${DB}.w_pl AS SELECT * FROM ${DB}.v_esb_pl_monthly;
CREATE VIEW ${DB}.w_usage AS SELECT * FROM ${DB}.v_esb_item_usage_daily;
CREATE VIEW ${DB}.w_usage_product AS SELECT * FROM ${DB}.v_esb_item_usage_product_monthly;
CREATE VIEW ${DB}.w_fill AS SELECT * FROM ${DB}.v_esb_fill_rate_daily;
CREATE VIEW ${DB}.w_fill_product AS SELECT * FROM ${DB}.v_esb_fill_rate_product_monthly;
CREATE ROLE g67_v_reader;
GRANT SELECT ON ${DB}.* TO g67_v_reader;
CREATE USER ${TUSER} IDENTIFIED WITH sha256_password BY '${TPW}' HOST ANY;
GRANT g67_v_reader TO ${TUSER};
SET DEFAULT ROLE g67_v_reader TO ${TUSER};
CREATE ROW POLICY efn_insight_scope ON ${DB}.w_pl USING ${P_PT} TO g67_v_reader;
CREATE ROW POLICY efn_insight_scope ON ${DB}.w_usage USING ${P_PT} TO g67_v_reader;
CREATE ROW POLICY efn_insight_scope ON ${DB}.w_usage_product USING ${P_PT} TO g67_v_reader;
CREATE ROW POLICY efn_insight_scope ON ${DB}.w_fill USING ${P_PT} TO g67_v_reader;
CREATE ROW POLICY efn_insight_scope ON ${DB}.w_fill_product USING ${P_PT} TO g67_v_reader;
EOF

sig3() {
  $CH --log_queries=0 --query "SELECT lower(hex(SHA256(concat('${KEY}', '|', '$1', '|', '$2', '|', '$3'))))"
}
scope() {
  printf "SQL_efn_cids='%s', SQL_efn_codes='%s', SQL_efn_brs='%s', SQL_efn_sig3='%s'" "$1" "$2" "$3" "$4"
}
u() {
  # $1 objek, $2 klausa WHERE, $3 SETTINGS -> count() sebagai user uji
  $CH --user "$TUSER" --password "$TPW" --query "SELECT count() FROM ${DB}.$1 WHERE $2 SETTINGS $3" 2>/dev/null \
    || echo ERR
}
PB_EXEC="$(scope 5 PB '' "$(sig3 5 PB '')")"
PB_STORE="$(scope 5 PB pb:7 "$(sig3 5 PB pb:7)")"
for w in w_pl w_usage w_usage_product w_fill w_fill_product; do
  expect "policy ${w}: scope PB tidak melihat WTZ" "$(u "$w" "company_code = 'WTZ'" "$PB_EXEC")" 0
  expect "policy ${w}: company_code '' tidak terlihat" "$(u "$w" "company_code = ''" "$PB_EXEC")" 0
  expect "policy ${w}: brs terisi -> 0 baris" "$(u "$w" "1" "$PB_STORE")" 0
  expect "policy ${w}: scope PB melihat baris PB (kontrol positif)" \
    "$(u "$w" "company_code = 'PB'" "$PB_EXEC")" "$(a "SELECT count() FROM ${DB}.$w WHERE company_code = 'PB'")"
done
expect "policy w_fill: baris PB ada (kontrol positif tidak kosong)" \
  "$(a "SELECT count() > 0 FROM ${DB}.w_fill WHERE company_code = 'PB'")" 1

if [ "$FAILS" -ne 0 ]; then
  echo "g67_views_harness: ${FAILS} uji GAGAL" >&2
  exit 1
fi
echo "g67_views_harness: semua uji lulus"
