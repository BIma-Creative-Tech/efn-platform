# shellcheck shell=sh
# G47 (docs/04 §28.13.6-§28.13.7 v20.3, L-188): row policy `efn_insight_scope` untuk view efn_insight.v_sm_*.
# Pemilik isi: efn-data-ai-engineer. BUKAN dieksekusi loop access/*.sql (ekstensi .sh); DISOURCING clickhouse-init.sh:
#
#     . /init/access/insight-sm-policy.sh
#
# tepat SETELAH loop policy v_esb_* dan SEBELUM `unset INSIGHT_KEY INSIGHT_SIG` (butuh $INSIGHT_KEY tervalidasi hex >= 64
# dan $CH = klien TLS admin). Policy dibuat dengan --log_queries=0 (SEC-191: kunci tidak masuk system.query_log).
# Fail closed: kunci/kelas/persona tidak sah -> init gagal; daftar view di bawah = tabel §28.13.9 (satu sumber, diuji
# services/etl/tests/test_insight_sm_access.py terhadap access/insight-sm-access.sql).
#
# Setting yang dikirim insight-web (dari sesi eis2 terverifikasi, §28.13.6): SQL_efn_cids, SQL_efn_codes (codes ∩ SM),
# SQL_efn_wids, SQL_efn_personas (urut naik), SQL_efn_cats (boleh kosong), SQL_efn_sig2 =
# lower(hex(SHA256(KEY|cids|codes|wids|personas|cats))). Setting yang tidak dikirim -> getSetting galat (fail closed).
# Regex format melarang `|` dan byte non-ASCII sehingga sig v1 (KEY|cids|codes) tidak bisa dipakai sebagai sig2.
#
# Harness uji perilaku (ClickHouse 24.8, 8 test negatif §28.13.7): services/etl/tests/clickhouse/g47_row_policy_harness.sh
# (memakai SM_POLICY_DEFINE_ONLY=1 untuk hanya memuat fungsi predikat).

# Daftar view SM: <view> <kelas W|C|F> <persona dipisah koma, urut naik>. G51 (spesifikasi lead §B2/§B4): +4 view terakhir.
SM_POLICY_VIEWS="
v_sm_warehouse_list W category,dc,exec,marketing,store
v_sm_product_list C category,dc,exec,marketing,store
v_sm_sales_daily W category,dc,exec,store
v_sm_outlet_daily W category,dc,exec,store
v_sm_margin_daily W category,exec
v_sm_gmroi_monthly W category,exec
v_sm_waste_daily W category,dc,exec,store
v_sm_forecast_accuracy_weekly W category,dc,exec,store
v_sm_stock_daily W category,dc,exec,store
v_sm_oos_product_daily W category,dc,exec,store
v_sm_stock_value_daily W category,dc,exec
v_sm_stock_critical W category,dc,exec,store
v_sm_replenishment W category,dc,exec,store
v_sm_replenishment_line W category,dc,exec,store
v_sm_replenishment_bias_weekly W category,dc,exec
v_sm_dc_fill_rate W category,dc,exec,store
v_sm_dc_purchase W category,dc,exec
v_sm_lot_expiry W category,dc,exec,store
v_sm_expiry_daily W category,dc,exec,store
v_sm_markdown W category,exec,store
v_sm_price_index W category,exec,marketing
v_sm_price_coverage W category,exec
v_sm_price_signal W category,exec,marketing
v_sm_visitor_funnel_daily W category,exec,marketing
v_sm_search_terms W category,marketing
v_sm_segment_daily W exec,marketing
v_sm_campaign_result W exec,marketing
v_sm_freshness F category,dc,exec,marketing,store
v_sm_kpi_config C category,dc,exec,marketing,store
v_sm_payment_daily W exec,store
v_sm_product_sales_daily W category,exec,store
v_sm_forecast_daily W category,dc,exec,store
v_sm_lot_stock W category,exec,store
"

sm_persona_array() {
  # "category,dc" -> ['category','dc'] (hanya persona §28.13.3; selain itu gagal)
  _out=""
  _rest="$1,"
  while [ -n "$_rest" ]; do
    _p="${_rest%%,*}"
    _rest="${_rest#*,}"
    case "$_p" in
      category|dc|exec|marketing|store) ;;
      *) echo "insight-sm-policy: persona tidak dikenal" >&2; return 1 ;;
    esac
    if [ -z "$_out" ]; then _out="'${_p}'"; else _out="${_out},'${_p}'"; fi
  done
  [ -n "$_out" ] || return 1
  printf '[%s]' "$_out"
}

sm_policy_predicate() {
  # $1 = kunci hex, $2 = kelas W|C|F, $3 = persona dipisah koma. Mencetak predikat USING (tanpa newline).
  case "$1" in
    ''|*[!0-9a-fA-F]*) echo "insight-sm-policy: kunci bukan hex" >&2; return 1 ;;
  esac
  [ "${#1}" -ge 64 ] || { echo "insight-sm-policy: kunci terlalu pendek" >&2; return 1; }
  _pers="$(sm_persona_array "$3")" || return 1
  _shared="match(getSetting('SQL_efn_codes'), '^[A-Z]{2,8}(,[A-Z]{2,8})*\$')"
  _shared="${_shared} AND match(getSetting('SQL_efn_cids'), '^[0-9]{1,10}(,[0-9]{1,10})*\$')"
  _shared="${_shared} AND match(getSetting('SQL_efn_wids'), '^[0-9]{1,10}(,[0-9]{1,10})*\$')"
  _shared="${_shared} AND match(getSetting('SQL_efn_personas'), '^(category|dc|exec|marketing|store)(,(category|dc|exec|marketing|store))*\$')"
  _shared="${_shared} AND match(getSetting('SQL_efn_cats'), '^([0-9]{1,10}(,[0-9]{1,10})*)?\$')"
  _shared="${_shared} AND getSetting('SQL_efn_sig2') = lower(hex(SHA256(concat('$1', '|', getSetting('SQL_efn_cids'), '|', getSetting('SQL_efn_codes'), '|', getSetting('SQL_efn_wids'), '|', getSetting('SQL_efn_personas'), '|', getSetting('SQL_efn_cats')))))"
  _shared="${_shared} AND hasAny(splitByChar(',', getSetting('SQL_efn_personas')), ${_pers})"
  _cid="company_id != 0 AND has(arrayMap(x -> toUInt32OrZero(x), splitByChar(',', getSetting('SQL_efn_cids'))), company_id)"
  _wid="has(arrayMap(x -> toUInt32OrZero(x), splitByChar(',', getSetting('SQL_efn_wids'))), warehouse_id)"
  case "$2" in
    W) printf '%s AND %s AND %s' "$_shared" "$_cid" "$_wid" ;;
    C) printf '%s AND %s' "$_shared" "$_cid" ;;
    F) printf '%s' "$_shared" ;;
    *) echo "insight-sm-policy: kelas tidak dikenal" >&2; return 1 ;;
  esac
}

if [ "${SM_POLICY_DEFINE_ONLY:-0}" != "1" ]; then
  [ -n "${INSIGHT_KEY:-}" ] || { echo "clickhouse-init: insight-sm-policy butuh INSIGHT_KEY" >&2; exit 1; }
  SM_POLICY_SQL="$(printf '%s\n' "$SM_POLICY_VIEWS" | while read -r sm_v sm_cls sm_pers; do
      [ -n "$sm_v" ] || continue
      case "$sm_v" in
        v_sm_*) ;;
        *) echo "insight-sm-policy: nama view tidak sah" >&2; exit 1 ;;
      esac
      case "$sm_v" in *[!a-z0-9_]*) echo "insight-sm-policy: nama view tidak sah" >&2; exit 1 ;; esac
      sm_pred="$(sm_policy_predicate "$INSIGHT_KEY" "$sm_cls" "$sm_pers")" || exit 1
      printf 'CREATE ROW POLICY OR REPLACE efn_insight_scope ON efn_insight.%s USING %s TO insight_reader;\n' \
        "$sm_v" "$sm_pred"
    done)" || { echo "clickhouse-init: gagal menyusun row policy view SM (G47)" >&2; exit 1; }
  SM_POLICY_N="$(printf '%s\n' "$SM_POLICY_SQL" | grep -c '^CREATE ROW POLICY')"
  [ "$SM_POLICY_N" = "33" ] || { echo "clickhouse-init: jumlah policy SM ${SM_POLICY_N} != 33" >&2; exit 1; }
  printf '%s\n' "$SM_POLICY_SQL" | $CH --log_queries=0 --multiquery
  unset SM_POLICY_SQL SM_POLICY_N
  echo "clickhouse-init: row policy efn_insight_scope view SM (G47) diterapkan"
fi
