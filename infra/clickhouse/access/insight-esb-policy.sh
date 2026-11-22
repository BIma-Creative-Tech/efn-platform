# shellcheck shell=sh
# G52 (docs/04 §28.15.6-§28.15.7 v21.1, L-243/L-244): row policy `efn_insight_scope` untuk view efn_insight.v_esb_*.
# Pemilik isi: efn-data-ai-engineer. BUKAN dieksekusi loop access/*.sql (ekstensi .sh); DISOURCING clickhouse-init.sh:
#
#     ESB_POLICY_EXPECTED=14
#     . /init/access/insight-esb-policy.sh
#
# SETELAH validasi $INSIGHT_KEY (hex >= 64, beda dari text2sql_scope_key) dan SEBELUM `unset INSIGHT_KEY`; $CH = klien
# TLS admin. Policy dibuat dengan --log_queries=0 (SEC-191: kunci tidak masuk system.query_log). Fail closed: kunci/
# kelas/nama view tidak sah atau jumlah policy != $ESB_POLICY_EXPECTED -> init gagal.
#
# Setting yang dikirim insight-web (dari sesi eis3, atau eis1/eis2 dengan brs = [], §28.15.6): SQL_efn_cids,
# SQL_efn_codes (codes ∩ ESB), SQL_efn_brs (boleh kosong), SQL_efn_sig3 = lower(hex(SHA256(KEY|cids|codes|brs))).
# Menggantikan SQL_efn_sig (v1) untuk view ESB: TIDAK ada fallback v1 (sig v1 = 2 pemisah, sig3 = 3; regex format
# melarang `|` dan byte non-ASCII sehingga sig v1/sig2 tidak bisa dipakai sebagai sig3). Setting tidak dikirim ->
# getSetting galat (fail closed). Kompatibilitas rollout eit2 ada di insight-web (sesi lama = brs '' -> kelas PT tetap
# jalan); insight-web lama yang masih mengirim SQL_efn_sig gagal tertutup -> deploy bersama (§28.15.10).
#
# Kelas (§28.15.7):
#   PT = cakupan PT penuh; sesi ber-cabang (brs != '') ditolak (view memuat cogs/mdr atau tanpa dimensi cabang toko).
#   BR = brs kosong -> PT penuh; brs terisi -> hanya baris dengan "<brand>:<branch_id>" ∈ brs.
# Harness uji perilaku (ClickHouse 24.8, negatif E1-E9): services/esb-extract/tests/clickhouse/g52_row_policy_harness.sh
# (memakai ESB_POLICY_DEFINE_ONLY=1 untuk hanya memuat fungsi predikat).

# Daftar view ESB: <view> <kelas PT|BR>. Satu sumber; diuji services/esb-extract/tests/test_insight_fnb_access.py
# terhadap view di access/insight-access.sql.
ESB_POLICY_VIEWS="
v_esb_sales_daily PT
v_esb_sales_branch_daily PT
v_esb_payment_daily PT
v_esb_flow_daily PT
v_esb_menu_mix PT
v_esb_data_quality PT
v_esb_product_list PT
v_esb_movement_daily PT
v_esb_coverage PT
v_esb_branch_list BR
v_esb_store_sales_daily BR
v_esb_store_menu_daily BR
v_esb_store_payment_daily BR
v_esb_store_receipt_daily BR
"

esb_policy_predicate() {
  # $1 = kunci hex, $2 = kelas PT|BR. Mencetak predikat USING (tanpa newline).
  case "$1" in
    ''|*[!0-9a-fA-F]*) echo "insight-esb-policy: kunci bukan hex" >&2; return 1 ;;
  esac
  [ "${#1}" -ge 64 ] || { echo "insight-esb-policy: kunci terlalu pendek" >&2; return 1; }
  _esb="company_code != ''"
  _esb="${_esb} AND match(getSetting('SQL_efn_codes'), '^[A-Z]{2,8}(,[A-Z]{2,8})*\$')"
  _esb="${_esb} AND match(getSetting('SQL_efn_cids'), '^([0-9]{1,10}(,[0-9]{1,10})*)?\$')"
  _esb="${_esb} AND match(getSetting('SQL_efn_brs'), '^((bacha|cup|imd|pb|wtz):[1-9][0-9]{0,9}(,(bacha|cup|imd|pb|wtz):[1-9][0-9]{0,9})*)?\$')"
  _esb="${_esb} AND has(splitByChar(',', getSetting('SQL_efn_codes')), company_code)"
  _esb="${_esb} AND getSetting('SQL_efn_sig3') = lower(hex(SHA256(concat('$1', '|', getSetting('SQL_efn_cids'), '|', getSetting('SQL_efn_codes'), '|', getSetting('SQL_efn_brs')))))"
  _pt="getSetting('SQL_efn_brs') = ''"
  _br="(getSetting('SQL_efn_brs') = '' OR has(splitByChar(',', getSetting('SQL_efn_brs')), concat(brand, ':', toString(branch_id))))"
  case "$2" in
    PT) printf '%s AND %s' "$_esb" "$_pt" ;;
    BR) printf '%s AND %s' "$_esb" "$_br" ;;
    *) echo "insight-esb-policy: kelas tidak dikenal" >&2; return 1 ;;
  esac
}

if [ "${ESB_POLICY_DEFINE_ONLY:-0}" != "1" ]; then
  [ -n "${INSIGHT_KEY:-}" ] || { echo "clickhouse-init: insight-esb-policy butuh INSIGHT_KEY" >&2; exit 1; }
  [ -n "${ESB_POLICY_EXPECTED:-}" ] || { echo "clickhouse-init: insight-esb-policy butuh ESB_POLICY_EXPECTED" >&2; exit 1; }
  ESB_POLICY_SQL="$(printf '%s\n' "$ESB_POLICY_VIEWS" | while read -r esb_v esb_cls; do
      [ -n "$esb_v" ] || continue
      case "$esb_v" in
        v_esb_*) ;;
        *) echo "insight-esb-policy: nama view tidak sah" >&2; exit 1 ;;
      esac
      case "$esb_v" in *[!a-z0-9_]*) echo "insight-esb-policy: nama view tidak sah" >&2; exit 1 ;; esac
      esb_pred="$(esb_policy_predicate "$INSIGHT_KEY" "$esb_cls")" || exit 1
      printf 'CREATE ROW POLICY OR REPLACE efn_insight_scope ON efn_insight.%s USING %s TO insight_reader;\n' \
        "$esb_v" "$esb_pred"
    done)" || { echo "clickhouse-init: gagal menyusun row policy view ESB (G52)" >&2; exit 1; }
  ESB_POLICY_N="$(printf '%s\n' "$ESB_POLICY_SQL" | grep -c '^CREATE ROW POLICY')"
  [ "$ESB_POLICY_N" = "$ESB_POLICY_EXPECTED" ] \
    || { echo "clickhouse-init: jumlah policy ESB ${ESB_POLICY_N} != ${ESB_POLICY_EXPECTED}" >&2; exit 1; }
  printf '%s\n' "$ESB_POLICY_SQL" | $CH --log_queries=0 --multiquery
  unset ESB_POLICY_SQL ESB_POLICY_N
  echo "clickhouse-init: row policy efn_insight_scope view ESB (G52, sig3, kelas PT/BR) diterapkan"
fi
