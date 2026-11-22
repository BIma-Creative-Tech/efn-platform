#!/bin/sh
# G52 (docs/04 §28.15.7): uji PERILAKU row policy ESB kelas PT/BR di ClickHouse 24.8 (negatif E1-E9 + kontrol positif).
# BELUM PERNAH DIJALANKAN oleh penulis (tanpa shell) -- dijalankan lead/devsecops, hasil dicatat di laporan G52.
#
# Memakai predikat yang SAMA dengan produksi: memuat infra/clickhouse/access/insight-esb-policy.sh dengan
# ESB_POLICY_DEFINE_ONLY=1 (hanya fungsi esb_policy_predicate) dan badan view efn_dwh.v_esb_store_receipt_daily dari
# infra/clickhouse/init/21-g52-insight-fnb.sql (nama objek efn_dwh diganti ke database uji). Objek uji di database
# sementara `efn_g52_rp_test`, kunci SINTETIS ("ab" x 32, vektor §28.15.5/§28.15.6), user uji sementara (pola
# insight_reader: SELECT database uji saja). Butuh klien admin TLS ($CH, mis. di container clickhouse-init) dan server
# yang mengizinkan setting kustom berprefiks SQL_ (sudah dipakai insight/text2sql). Semua objek uji dihapus di akhir.
#
# Contoh (dev):
#   docker compose run --rm --entrypoint sh \
#     -v "$(pwd -W)/services/esb-extract/tests/clickhouse:/harness:ro" clickhouse-init \
#     -c 'CH="clickhouse-client --config-file /tmp/efn-clickhouse-client.xml" \
#         POLICY_SH=/init/access/insight-esb-policy.sh DDL21=/init/schema/21-g52-insight-fnb.sql \
#         sh /harness/g52_row_policy_harness.sh'
# (file konfigurasi klien admin dibuat seperti clickhouse-init.sh; sesuaikan bila dijalankan di tempat lain.)
set -eu

: "${CH:?CH = perintah klien admin ClickHouse (TLS)}"
HERE="$(dirname "$0")"
POLICY_SH="${POLICY_SH:-${HERE}/../../../../infra/clickhouse/access/insight-esb-policy.sh}"
DDL21="${DDL21:-${HERE}/../../../../infra/clickhouse/init/21-g52-insight-fnb.sql}"
# shellcheck disable=SC2034 # dibaca file yang di-source di bawah
ESB_POLICY_DEFINE_ONLY=1
# shellcheck source=/dev/null
. "$POLICY_SH"

KEY="abababababababababababababababababababababababababababababababab"
DB=efn_g52_rp_test
TUSER=g52_rp_user
TPW="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
FAILS=0

cleanup() {
  $CH --multiquery <<EOF >/dev/null 2>&1 || true
DROP USER IF EXISTS ${TUSER};
DROP ROLE IF EXISTS g52_rp_reader;
DROP DATABASE IF EXISTS ${DB} SYNC;
EOF
}
trap cleanup EXIT INT TERM
cleanup

# Kelas diambil dari manifest produksi (ESB_POLICY_VIEWS), bukan disalin.
cls_of() {
  printf '%s\n' "$ESB_POLICY_VIEWS" | awk -v v="$1" '$1 == v { print $2 }'
}
[ "$(cls_of v_esb_sales_branch_daily)" = PT ] && [ "$(cls_of v_esb_payment_daily)" = PT ] \
  && [ "$(cls_of v_esb_store_sales_daily)" = BR ] && [ "$(cls_of v_esb_store_receipt_daily)" = BR ] \
  && [ "$(cls_of v_esb_branch_list)" = BR ] \
  || { echo "g52_row_policy_harness: kelas manifest tidak sesuai §28.15.7" >&2; exit 1; }

# Badan view penerimaan dari DDL produksi; objek efn_dwh -> database uji.
RECEIPT_SQL="$(sed -n '/^CREATE OR REPLACE VIEW efn_dwh\.v_esb_store_receipt_daily AS$/,/;$/p' "$DDL21" \
  | sed -e "s/^CREATE OR REPLACE VIEW efn_dwh\.v_esb_store_receipt_daily AS\$/CREATE VIEW ${DB}.receipt AS/" \
        -e "s/efn_dwh\.v_esb_movement_daily/${DB}.movement/g" \
        -e "s/efn_dwh\.dim_esb_branch/${DB}.dim_esb_branch/g")"
case "$RECEIPT_SQL" in
  "CREATE VIEW ${DB}.receipt AS"*) ;;
  *) echo "g52_row_policy_harness: badan v_esb_store_receipt_daily tidak ditemukan di ${DDL21}" >&2; exit 1 ;;
esac
case "$RECEIPT_SQL" in
  *efn_dwh*) echo "g52_row_policy_harness: masih ada rujukan efn_dwh di badan view uji" >&2; exit 1 ;;
esac

$CH --multiquery <<EOF
CREATE DATABASE ${DB};
-- Tabel pengganti view efn_insight (kolom target policy saja).
CREATE TABLE ${DB}.sales_pt (brand String, company_code String, branch_id UInt32) ENGINE = Memory;
CREATE TABLE ${DB}.payment_pt (brand String, company_code String, branch_id UInt32) ENGINE = Memory;
CREATE TABLE ${DB}.store_sales (brand String, company_code String, branch_id UInt32) ENGINE = Memory;
CREATE TABLE ${DB}.branch_list (brand String, company_code String, branch_id UInt32) ENGINE = Memory;
INSERT INTO ${DB}.sales_pt VALUES ('pb', 'PB', 7), ('pb', 'PB', 12);
INSERT INTO ${DB}.payment_pt VALUES ('pb', 'PB', 7), ('pb', 'PB', 0);
INSERT INTO ${DB}.store_sales VALUES ('pb', 'PB', 7), ('pb', 'PB', 12), ('pb', 'PB', 0), ('pb', '', 7),
    ('bacha', 'BCR', 15);
INSERT INTO ${DB}.branch_list VALUES ('pb', 'PB', 1), ('pb', 'PB', 7), ('pb', 'PB', 12);
-- Sumber view penerimaan (kolom = efn_dwh.dim_esb_branch / v_esb_movement_daily).
CREATE TABLE ${DB}.dim_esb_branch (brand String, company_code String, branch_id UInt32, branch_code String,
    branch_name String, is_ck UInt8, is_store UInt8, active UInt8, _version UInt64)
    ENGINE = ReplacingMergeTree(_version) ORDER BY (brand, branch_id);
INSERT INTO ${DB}.dim_esb_branch VALUES
    ('pb', 'PB', 1, 'CK1', 'CK PB', 1, 0, 1, 1), ('pb', 'PB', 7, 'S7', 'Toko 7', 0, 1, 1, 1),
    ('pb', 'PB', 12, 'S12', 'Toko 12', 0, 1, 1, 1), ('bacha', 'BCR', 15, 'B15', 'Butik 15', 0, 1, 1, 1),
    ('bacha', 'BCF', 16, 'B16', 'CK Bacha', 1, 0, 1, 1);
CREATE TABLE ${DB}.movement (day Date, brand String, company_code String, kind String, origin_branch_id UInt32,
    origin_branch_name String, origin_kind String, dest_branch_id UInt32, dest_branch_name String, dest_kind String,
    item_group String, category String, product_code String, product_name String, uom String, qty Float64,
    lines UInt64) ENGINE = MergeTree ORDER BY tuple();
INSERT INTO ${DB}.movement VALUES
    ('2026-11-20', 'pb', 'PB', 'delivery', 1, 'CK PB', 'ck', 7, 'Toko 7', 'store', 'fin', 'C', 'P1', 'Roti', 'EA', 10, 1),
    ('2026-11-20', 'pb', 'PB', 'delivery', 1, 'CK PB', 'ck', 12, 'Toko 12', 'store', 'fin', 'C', 'P1', 'Roti', 'EA', 5, 1),
    ('2026-11-20', 'pb', 'PB', 'delivery', 1, 'CK PB', 'ck', 99, 'Cabang #99', 'non_sales', 'fin', 'C', 'P1', 'Roti', 'EA', 3, 1),
    ('2026-11-20', 'pb', 'PB', 'delivery', 7, 'Toko 7', 'store', 1, 'CK PB', 'ck', 'fin', 'C', 'P1', 'Roti', 'EA', 1, 1),
    ('2026-11-20', 'pb', 'PB', 'production', 1, 'CK PB', 'ck', 0, '', '', 'fin', 'C', 'P1', 'Roti', 'EA', 20, 1),
    ('2026-11-20', 'bacha', 'BCF', 'delivery', 16, 'CK Bacha', 'ck', 15, 'Butik 15', 'store', 'fin', 'C', 'P2', 'Kue', 'EA', 4, 1);
${RECEIPT_SQL}
CREATE ROLE g52_rp_reader;
GRANT SELECT ON ${DB}.* TO g52_rp_reader;
CREATE USER ${TUSER} IDENTIFIED WITH sha256_password BY '${TPW}' HOST ANY;
GRANT g52_rp_reader TO ${TUSER};
SET DEFAULT ROLE g52_rp_reader TO ${TUSER};
EOF

P_PT="$(esb_policy_predicate "$KEY" PT)"
P_BR="$(esb_policy_predicate "$KEY" BR)"
$CH --log_queries=0 --multiquery <<EOF
CREATE ROW POLICY efn_insight_scope ON ${DB}.sales_pt USING ${P_PT} TO g52_rp_reader;
CREATE ROW POLICY efn_insight_scope ON ${DB}.payment_pt USING ${P_PT} TO g52_rp_reader;
CREATE ROW POLICY efn_insight_scope ON ${DB}.store_sales USING ${P_BR} TO g52_rp_reader;
CREATE ROW POLICY efn_insight_scope ON ${DB}.branch_list USING ${P_BR} TO g52_rp_reader;
CREATE ROW POLICY efn_insight_scope ON ${DB}.receipt USING ${P_BR} TO g52_rp_reader;
EOF

sig3() {
  # $1 cids, $2 codes, $3 brs
  $CH --log_queries=0 --query "SELECT lower(hex(SHA256(concat('${KEY}', '|', '$1', '|', '$2', '|', '$3'))))"
}

scope() {
  # $1 cids $2 codes $3 brs $4 sig3
  printf "SQL_efn_cids='%s', SQL_efn_codes='%s', SQL_efn_brs='%s', SQL_efn_sig3='%s'" "$1" "$2" "$3" "$4"
}

q() {
  # $1 tabel, $2 klausa SETTINGS (tanpa kata SETTINGS); mencetak count() atau ERR
  $CH --user "$TUSER" --password "$TPW" --query "SELECT count() FROM ${DB}.$1 SETTINGS $2" 2>/dev/null || echo ERR
}

expect() {
  # $1 nama uji, $2 hasil, $3 harapan
  if [ "$2" = "$3" ]; then echo "OK   $1 ($2)"; else echo "FAIL $1 (dapat $2, harap $3)"; FAILS=$((FAILS + 1)); fi
}

# Vektor scope §28.15.6: E = KEY|3,5,11|BCF,PB|  (exec, brs kosong); D = KEY|5|PB|pb:12,pb:7 (store PB)
EXEC="$(scope 3,5,11 BCF,PB '' "$(sig3 3,5,11 BCF,PB '')")"
S_D="$(sig3 5 PB 'pb:12,pb:7')"
STORE_D="$(scope 5 PB 'pb:12,pb:7' "$S_D")"
S_PB7="$(sig3 5 PB pb:7)"
PB7="$(scope 5 PB pb:7 "$S_PB7")"
BCR15="$(scope 4 BCR bacha:15 "$(sig3 4 BCR bacha:15)")"

# Kontrol positif
expect "positif exec: kelas PT melihat semua PT PB" "$(q sales_pt "$EXEC")" 2
expect "positif exec: kelas BR tanpa brs = semua cabang PB (tanpa baris company_code '')" "$(q store_sales "$EXEC")" 3
expect "positif store D (pb:12,pb:7): dua cabang" "$(q store_sales "$STORE_D")" 2
expect "positif store BCR bacha:15: kiriman CK Bacha (BCF) ke butik 15 (PT tujuan BCR)" "$(q receipt "$BCR15")" 1
# (E1) brs=pb:7 tidak melihat pb:12 (view toko + v_esb_branch_list)
expect "(E1) store_sales pb:7" "$(q store_sales "$PB7")" 1
expect "(E1) branch_list pb:7" "$(q branch_list "$PB7")" 1
expect "(E1) receipt pb:7 (tanpa kiriman ke 12)" "$(q receipt "$PB7")" 1
# (E2) brs tidak kosong -> 0 baris di kelas PT
expect "(E2) sales_pt dengan brs" "$(q sales_pt "$PB7")" 0
expect "(E2) payment_pt dengan brs" "$(q payment_pt "$PB7")" 0
# (E3) sig3 dihitung dari brs lain
expect "(E3) brs=pb:7,pb:12 dengan sig3 milik pb:7" "$(q store_sales "$(scope 5 PB 'pb:12,pb:7' "$S_PB7")")" 0
# (E4) sig v1 (KEY|cids|codes) sebagai sig3 dengan brs=''
S_V1="$($CH --log_queries=0 --query "SELECT lower(hex(SHA256(concat('${KEY}', '|', '5', '|', 'PB'))))")"
expect "(E4) sig v1 sebagai sig3 (kelas PT)" "$(q sales_pt "$(scope 5 PB '' "$S_V1")")" 0
expect "(E4) sig v1 sebagai sig3 (kelas BR)" "$(q store_sales "$(scope 5 PB '' "$S_V1")")" 0
# (E5) format brs salah, sig3 sah atas string yang sama
expect "(E5) brs=PB:7" "$(q store_sales "$(scope 5 PB PB:7 "$(sig3 5 PB PB:7)")")" 0
expect "(E5) brs=pb:07" "$(q store_sales "$(scope 5 PB pb:07 "$(sig3 5 PB pb:07)")")" 0
# (E6) tanpa SQL_efn_brs -> galat
expect "(E6) tanpa SQL_efn_brs" "$(q store_sales "SQL_efn_cids='5', SQL_efn_codes='PB', SQL_efn_sig3='${S_PB7}'")" ERR
# (E7) baris branch_id = 0 tidak terlihat sesi ber-cabang (BR: tidak cocok brs; PT: brs wajib kosong; pb:0 = format salah)
expect "(E7) store_sales pb:7 tanpa baris cabang 0" "$(q store_sales "$PB7")" 1
expect "(E7) payment_pt (baris cabang 0) sesi ber-cabang" "$(q payment_pt "$PB7")" 0
expect "(E7) brs=pb:0 ditolak format" "$(q store_sales "$(scope 5 PB pb:0 "$(sig3 5 PB pb:0)")")" 0
# (E8) kiriman ke cabang tujuan tak dikenal (99) / bukan toko (CK 1) tidak muncul; produksi tidak muncul
expect "(E8) receipt exec PB = kiriman ke toko 7 dan 12 saja" "$(q receipt "$EXEC")" 2
# (E9) brs=bacha:15 dengan codes=BCF (PT salah; butik 15 milik BCR)
BCF15="$(scope 4 BCF bacha:15 "$(sig3 4 BCF bacha:15)")"
expect "(E9) receipt bacha:15 codes BCF" "$(q receipt "$BCF15")" 0
expect "(E9) store_sales bacha:15 codes BCF" "$(q store_sales "$BCF15")" 0

if [ "$FAILS" -ne 0 ]; then
  echo "g52_row_policy_harness: ${FAILS} uji GAGAL" >&2
  exit 1
fi
echo "g52_row_policy_harness: semua uji lulus"
