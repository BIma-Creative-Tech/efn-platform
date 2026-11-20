#!/bin/sh
# G47 (docs/04 §28.13.7): uji PERILAKU row policy SM di ClickHouse 24.8 (8 test negatif + kontrol positif).
# BELUM PERNAH DIJALANKAN oleh penulis (tanpa shell) -- dijalankan lead/devsecops, hasil dicatat di laporan G47.
#
# Memakai predikat yang SAMA dengan produksi: memuat infra/clickhouse/access/insight-sm-policy.sh dengan
# SM_POLICY_DEFINE_ONLY=1 (hanya fungsi sm_policy_predicate), lalu memasang policy di database uji sementara
# `efn_g47_rp_test` dengan kunci SINTETIS ("ab" x 32, sama dengan vektor §28.13.6) dan user uji sementara.
# Butuh: klien admin TLS ($CH, mis. di container clickhouse-init), server mengizinkan setting kustom berprefiks SQL_
# (custom_settings_prefixes, sudah dipakai insight/text2sql). Semua objek uji dihapus di akhir (juga saat gagal).
#
# Contoh (dev):
#   docker compose run --rm --entrypoint sh -v "$(pwd -W)/services/etl/tests/clickhouse:/harness:ro" clickhouse-init \
#     -c 'CH="clickhouse-client --config-file /tmp/efn-clickhouse-client.xml" POLICY_SH=/init/access/insight-sm-policy.sh \
#         sh /harness/g47_row_policy_harness.sh'
# (file konfigurasi klien admin dibuat seperti clickhouse-init.sh; sesuaikan bila dijalankan di tempat lain.)
set -eu

: "${CH:?CH = perintah klien admin ClickHouse (TLS)}"
POLICY_SH="${POLICY_SH:-$(dirname "$0")/../../../../infra/clickhouse/access/insight-sm-policy.sh}"
# shellcheck disable=SC2034 # dibaca file yang di-source di bawah
SM_POLICY_DEFINE_ONLY=1
# shellcheck source=/dev/null
. "$POLICY_SH"

KEY="abababababababababababababababababababababababababababababababab"
DB=efn_g47_rp_test
TUSER=g47_rp_user
TPW="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
FAILS=0

cleanup() {
  $CH --multiquery <<EOF >/dev/null 2>&1 || true
DROP USER IF EXISTS ${TUSER};
DROP ROLE IF EXISTS g47_rp_reader;
DROP DATABASE IF EXISTS ${DB} SYNC;
EOF
}
trap cleanup EXIT INT TERM
cleanup

$CH --multiquery <<EOF
CREATE DATABASE ${DB};
CREATE TABLE ${DB}.sales (company_id UInt32, warehouse_id UInt32) ENGINE = Memory;
CREATE TABLE ${DB}.margin (company_id UInt32, warehouse_id UInt32) ENGINE = Memory;
CREATE TABLE ${DB}.kpi (company_id UInt32, warehouse_id UInt32) ENGINE = Memory;
CREATE TABLE ${DB}.fresh (mart String) ENGINE = Memory;
INSERT INTO ${DB}.sales VALUES (11, 21), (11, 22), (11, 0), (0, 0), (12, 22);
INSERT INTO ${DB}.margin VALUES (11, 22), (11, 21);
INSERT INTO ${DB}.kpi VALUES (11, 999), (0, 0);
INSERT INTO ${DB}.fresh VALUES ('stock');
CREATE ROLE g47_rp_reader;
GRANT SELECT ON ${DB}.* TO g47_rp_reader;
CREATE USER ${TUSER} IDENTIFIED WITH sha256_password BY '${TPW}' HOST ANY;
GRANT g47_rp_reader TO ${TUSER};
SET DEFAULT ROLE g47_rp_reader TO ${TUSER};
EOF

P_SALES="$(sm_policy_predicate "$KEY" W category,dc,exec,store)"
P_MARGIN="$(sm_policy_predicate "$KEY" W category,exec)"
P_KPI="$(sm_policy_predicate "$KEY" C category,dc,exec,marketing,store)"
P_FRESH="$(sm_policy_predicate "$KEY" F category,dc,exec,marketing,store)"
$CH --log_queries=0 --multiquery <<EOF
CREATE ROW POLICY efn_insight_scope ON ${DB}.sales USING ${P_SALES} TO g47_rp_reader;
CREATE ROW POLICY efn_insight_scope ON ${DB}.margin USING ${P_MARGIN} TO g47_rp_reader;
CREATE ROW POLICY efn_insight_scope ON ${DB}.kpi USING ${P_KPI} TO g47_rp_reader;
CREATE ROW POLICY efn_insight_scope ON ${DB}.fresh USING ${P_FRESH} TO g47_rp_reader;
EOF

sig2() {
  # $1 cids, $2 codes, $3 wids (literal ClickHouse, boleh escape), $4 personas, $5 cats
  $CH --log_queries=0 --query "SELECT lower(hex(SHA256(concat('${KEY}', '|', '$1', '|', '$2', '|', '$3', '|', '$4', '|', '$5'))))"
}

q() {
  # $1 tabel, $2 klausa SETTINGS (tanpa kata SETTINGS); mencetak count() atau ERR
  $CH --user "$TUSER" --password "$TPW" --query "SELECT count() FROM ${DB}.$1 SETTINGS $2" 2>/dev/null || echo ERR
}

expect() {
  # $1 nama uji, $2 hasil, $3 harapan
  if [ "$2" = "$3" ]; then echo "OK   $1 ($2)"; else echo "FAIL $1 (dapat $2, harap $3)"; FAILS=$((FAILS + 1)); fi
}

scope() {
  # $1 cids $2 codes $3 wids $4 personas $5 cats $6 sig
  printf "SQL_efn_cids='%s', SQL_efn_codes='%s', SQL_efn_wids='%s', SQL_efn_personas='%s', SQL_efn_cats='%s', SQL_efn_sig2='%s'" \
    "$1" "$2" "$3" "$4" "$5" "$6"
}

S_STORE="$(sig2 11 SMD 22 store '')"
STORE="$(scope 11 SMD 22 store '' "$S_STORE")"
S_EXEC="$(sig2 11 SMD 0,20,21,22 exec '')"
EXEC="$(scope 11 SMD 0,20,21,22 exec '' "$S_EXEC")"

# Kontrol positif
expect "positif store melihat tokonya (wh 22)" "$(q sales "$STORE")" 1
expect "positif exec melihat wh 0/21/22 PT 11" "$(q sales "$EXEC")" 3
expect "positif kelas C (kpi) tanpa cek warehouse" "$(q kpi "$STORE")" 1
expect "positif kelas F (freshness)" "$(q fresh "$STORE")" 1
# (1) wids=22 tidak melihat 21 maupun 0
expect "(1) store wids=22: hanya wh 22 dari 3 baris PT 11 (tanpa wh 21 & 0)" "$(q sales "$STORE")" 1
# (2) persona store -> 0 baris di view biaya
expect "(2) store di v_sm_margin_daily" "$(q margin "$STORE")" 0
# (3) sig2 dari wids lain
expect "(3) sig2 wids lain" "$(q sales "$(scope 11 SMD 21,22 store '' "$S_STORE")")" 0
# (4) sig v1 sebagai sig2
S_V1="$($CH --log_queries=0 --query "SELECT lower(hex(SHA256(concat('${KEY}', '|', '11', '|', 'SMD'))))")"
expect "(4) sig v1 sebagai sig2" "$(q sales "$(scope 11 SMD 22 store '' "$S_V1")")" 0
# (5) wids memuat byte non-digit (sig dihitung atas string yang sama -> tetap ditolak format)
S_BAD="$(sig2 11 SMD '22\n' store '')"
expect "(5) wids non-digit" "$(q sales "$(scope 11 SMD '22\n' store '' "$S_BAD")")" 0
# (6) tanpa SQL_efn_personas -> galat
expect "(6) tanpa SQL_efn_personas" \
  "$(q sales "SQL_efn_cids='11', SQL_efn_codes='SMD', SQL_efn_wids='22', SQL_efn_cats='', SQL_efn_sig2='${S_STORE}'")" ERR
# (7) user uji (pola insight_reader) tidak bisa SELECT tabel efn_dwh
expect "(7) tidak bisa SELECT efn_dwh" \
  "$($CH --user "$TUSER" --password "$TPW" --query "SELECT count() FROM efn_dwh.agg_sm_stock_daily" 2>/dev/null || echo ERR)" ERR
# (8) baris company_id = 0 tidak terlihat walau cids memuat 0
S_ZERO="$(sig2 0,11 SMD 0,22 exec '')"
expect "(8) company_id 0 (kelas W)" "$(q sales "$(scope 0,11 SMD 0,22 exec '' "$S_ZERO") ")" 2
expect "(8) company_id 0 (kelas C)" "$(q kpi "$(scope 0,11 SMD 0,22 exec '' "$S_ZERO")")" 1

if [ "$FAILS" -ne 0 ]; then
  echo "g47_row_policy_harness: ${FAILS} uji GAGAL" >&2
  exit 1
fi
echo "g47_row_policy_harness: semua uji lulus"
