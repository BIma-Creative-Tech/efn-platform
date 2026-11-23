# shellcheck shell=sh
# V-01 (keputusan pemilik proyek 23 Nov 2026): row policy `efn_insight_scope` untuk view efn_insight.v_vis_* (halaman
# Insight "Pengunjung" lintas PT). Pemilik isi: efn-data-ai-engineer. BUKAN dieksekusi loop access/*.sql (ekstensi .sh);
# DISOURCING clickhouse-init.sh:
#
#     . /init/access/insight-visitor-policy.sh
#
# SETELAH policy SM (insight-sm-policy.sh) dan SEBELUM `unset INSIGHT_KEY` (butuh $INSIGHT_KEY tervalidasi hex >= 64 dan
# $CH = klien TLS admin). Policy dibuat dengan --log_queries=0 (SEC-191: kunci tidak masuk system.query_log).
#
# Setting yang dikirim insight-web (dihitung dari SESI terverifikasi, lib/visitor.ts visitorCodes + lib/scope.ts
# scopeSettingsVisitor; tidak pernah dari input browser):
#   SQL_efn_vcodes = kode PT yang boleh dilihat di halaman Pengunjung, urut naik dipisah koma:
#                    kode ESB sesi (hanya sesi tanpa pembatasan cabang `brs`) + kode SM bila persona category/exec/marketing
#   SQL_efn_vsig   = lower(hex(SHA256(KEY + "|visitor|" + vcodes)))
# Domain tanda tangan berbeda dari sig/sig2/sig3 (setting lain + label "visitor"; regex format melarang `|`), jadi tanda
# tangan view ESB/SM tidak berlaku di sini dan sebaliknya. Setting tidak dikirim -> getSetting galat (fail closed).
# Kelas tunggal: baris boleh dilihat bila company_code ∈ vcodes (view v_vis_* tanpa dimensi cabang/outlet).

VIS_POLICY_VIEWS="
v_vis_page_daily
v_vis_funnel_daily
v_vis_product_daily
v_vis_search_daily
"
VIS_POLICY_EXPECTED=4

vis_policy_predicate() {
  # $1 = kunci hex. Mencetak predikat USING (tanpa newline).
  case "$1" in
    ''|*[!0-9a-fA-F]*) echo "insight-visitor-policy: kunci bukan hex" >&2; return 1 ;;
  esac
  [ "${#1}" -ge 64 ] || { echo "insight-visitor-policy: kunci terlalu pendek" >&2; return 1; }
  _vis="company_code != ''"
  _vis="${_vis} AND match(getSetting('SQL_efn_vcodes'), '^[A-Z]{2,8}(,[A-Z]{2,8})*\$')"
  _vis="${_vis} AND has(splitByChar(',', getSetting('SQL_efn_vcodes')), company_code)"
  _vis="${_vis} AND getSetting('SQL_efn_vsig') = lower(hex(SHA256(concat('$1', '|visitor|', getSetting('SQL_efn_vcodes')))))"
  printf '%s' "$_vis"
}

if [ "${VIS_POLICY_DEFINE_ONLY:-0}" != "1" ]; then
  [ -n "${INSIGHT_KEY:-}" ] || { echo "clickhouse-init: insight-visitor-policy butuh INSIGHT_KEY" >&2; exit 1; }
  VIS_POLICY_SQL="$(printf '%s\n' "$VIS_POLICY_VIEWS" | while read -r vis_v; do
      [ -n "$vis_v" ] || continue
      case "$vis_v" in
        v_vis_*) ;;
        *) echo "insight-visitor-policy: nama view tidak sah" >&2; exit 1 ;;
      esac
      case "$vis_v" in *[!a-z0-9_]*) echo "insight-visitor-policy: nama view tidak sah" >&2; exit 1 ;; esac
      vis_pred="$(vis_policy_predicate "$INSIGHT_KEY")" || exit 1
      printf 'CREATE ROW POLICY OR REPLACE efn_insight_scope ON efn_insight.%s USING %s TO insight_reader;\n' \
        "$vis_v" "$vis_pred"
    done)" || { echo "clickhouse-init: gagal menyusun row policy view Pengunjung (V-01)" >&2; exit 1; }
  VIS_POLICY_N="$(printf '%s\n' "$VIS_POLICY_SQL" | grep -c '^CREATE ROW POLICY')"
  [ "$VIS_POLICY_N" = "$VIS_POLICY_EXPECTED" ] \
    || { echo "clickhouse-init: jumlah policy Pengunjung ${VIS_POLICY_N} != ${VIS_POLICY_EXPECTED}" >&2; exit 1; }
  printf '%s\n' "$VIS_POLICY_SQL" | $CH --log_queries=0 --multiquery
  unset VIS_POLICY_SQL VIS_POLICY_N
  echo "clickhouse-init: row policy efn_insight_scope view Pengunjung (V-01) diterapkan"
fi
