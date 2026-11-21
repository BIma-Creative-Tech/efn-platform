#!/bin/sh
# One-shot bootstrap ClickHouse (service `clickhouse-init` di compose). Idempoten, dijalankan setiap `up`/deploy.
# Pemilik: efn-devsecops-engineer. Menggantikan /docker-entrypoint-initdb.d bawaan image karena entrypoint resmi
# menjalankan skrip init lewat port native PLAINTEXT 9000 (127.0.0.1) - port itu dihapus (config.d/tls.xml).
#
# Urutan (semua lewat native TLS clickhouse:9440, sertifikat diverifikasi terhadap CA internal):
#  1. init/*.sql   (skema mart, milik efn-data-ai-engineer; wajib idempoten: IF NOT EXISTS)
#  2. CREATE/ALTER USER per service dengan password dari Docker secret (docs/04 §7.3)
#  3. access/*.sql (role, settings profile, grant; milik efn-data-ai-engineer) -> SEC-04 lapisan kedua
set -eu

ADMIN_USER="${CLICKHOUSE_USER:-efn}"
umask 077
CFG=/tmp/efn-clickhouse-client.xml

pw_ok() {
  # Password disisipkan sebagai literal SQL -> charset dibatasi (generator `random` = base64, `hex32` = hex).
  case "$1" in
    ''|*[!A-Za-z0-9+/=]*) return 1 ;;
  esac
  [ "${#1}" -ge 16 ]
}

ADMIN_PW="$(cat /run/secrets/clickhouse_password)"
pw_ok "$ADMIN_PW" || { echo "clickhouse-init: clickhouse_password tidak valid (charset/panjang)" >&2; exit 1; }

# Konfigurasi klien privat di tmpfs: TLS + verifikasi CA + kredensial admin (tidak muncul di argv/ps).
cat > "$CFG" <<EOF
<config>
    <host>clickhouse</host>
    <port>9440</port>
    <secure>true</secure>
    <user>${ADMIN_USER}</user>
    <password>${ADMIN_PW}</password>
    <openSSL>
        <client>
            <caConfig>/run/secrets/efn_ca_crt</caConfig>
            <loadDefaultCAFile>false</loadDefaultCAFile>
            <verificationMode>strict</verificationMode>
            <disableProtocols>sslv2,sslv3,tlsv1,tlsv1_1</disableProtocols>
            <invalidCertificateHandler><name>RejectCertificateHandler</name></invalidCertificateHandler>
        </client>
    </openSSL>
</config>
EOF
unset ADMIN_PW
CH="clickhouse-client --config-file ${CFG} --host clickhouse --secure --port 9440"

i=0
until $CH --query "SELECT 1" > /dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo "clickhouse-init: clickhouse:9440 tidak siap / TLS gagal diverifikasi setelah 120 detik" >&2
    $CH --query "SELECT 1" || true
    exit 1
  fi
  sleep 2
done

# G43 (docs/04 §27.10, L-130): map_esb_odoo kunci baru (brand, company_code, entity, esb_key) + odoo_model, run_id.
# DDL baru = 14-g43-migration.sql (CREATE IF NOT EXISTS). Tabel versi G41 (kunci lama): KOSONG -> dihapus di sini lalu
# dibuat ulang oleh 14; BERISI -> init gagal (tindakan manual: salin ke tabel baru/arsip, lalu hapus; bukan otomatis).
MAP_NEW_KEY="brand, company_code, entity, esb_key"
MAP_KEY="$($CH --query "SELECT sorting_key FROM system.tables WHERE database = 'efn_dwh' AND name = 'map_esb_odoo'")"
if [ -n "$MAP_KEY" ] && [ "$MAP_KEY" != "$MAP_NEW_KEY" ]; then
  MAP_ROWS="$($CH --query "SELECT count() FROM efn_dwh.map_esb_odoo")"
  if [ "$MAP_ROWS" != "0" ]; then
    echo "clickhouse-init: efn_dwh.map_esb_odoo kunci lama (${MAP_KEY}) berisi ${MAP_ROWS} baris; migrasi ke kunci (${MAP_NEW_KEY}) wajib manual (docs/04 §27.10)" >&2
    exit 1
  fi
  echo "clickhouse-init: map_esb_odoo kunci lama (${MAP_KEY}) kosong -> dihapus, dibuat ulang oleh 14-g43-migration.sql"
  $CH --query "DROP TABLE efn_dwh.map_esb_odoo SYNC" < /dev/null
fi

for f in /init/schema/*.sql; do
  [ -e "$f" ] || continue
  echo "clickhouse-init: skema $(basename "$f")"
  $CH --multiquery < "$f"
done

# ===================================================================== Retensi mart (pemilik: efn-data-ai-engineer)
# docs/04 §8.4, SEC-11; G18/W-19 (PDP_DATA_INVENTORY v7 §0a/§3, keputusan Q-6/Q-49 + Q-P7 + Q-P8):
#  - baris mart: CH_MART_RETENTION_YEARS (default 10, 1..30)                      -> TTL baris
#  - kolom pseudonim (user_hash, member_hash, customer_id): CH_PSEUDONYM_RETENTION_YEARS (default 5,
#    1..CH_MART_RETENTION_YEARS) -> TTL kolom: nilai diganti default kolom ('' / NULL) saat merge
#  - koordinat Price Hunter (geo_lat/geo_lng): CH_GEO_RETENTION_DAYS (default 90, 1..3650) -> TTL kolom (NULL)
# Penafsiran Q-P7 vs Q-6 (rencana G18 §3): baris fakta 10 th, kolom pseudonim dikosongkan setelah 5 th.
# Idempoten: diterapkan ulang setiap deploy. materialize_ttl_after_modify=0 -> ALTER tidak menulis ulang part; bila
# definisi TTL suatu tabel BERUBAH (create_table_query beda sebelum/sesudah) dijalankan `MATERIALIZE TTL` sekali
# (mutation async) agar part lama -- yang tidak punya info TTL dan bisa tidak pernah di-merge lagi -- ikut diproses.
# Deploy berikutnya dengan env sama = tanpa mutation. ttl_only_drop_parts=1 hanya mengatur TTL baris (partisi
# bulanan yang seluruhnya kedaluwarsa dibuang utuh); TTL kolom tetap berlaku per baris (diuji 24.8.14, 10 Okt 2026).
# TTL kolom tidak boleh pada kolom kunci ORDER BY -> loyalty_events dimigrasi ke ORDER BY (event_id) di bawah.
int_in_range() {
  # $1 = nama env, $2 = nilai, $3 = min, $4 = max
  case "$2" in
    ''|*[!0-9]*) echo "clickhouse-init: $1 harus bilangan bulat" >&2; exit 1 ;;
  esac
  if [ "$2" -lt "$3" ] || [ "$2" -gt "$4" ]; then
    echo "clickhouse-init: $1 di luar rentang $3..$4" >&2; exit 1
  fi
}
RETENTION_YEARS="${CH_MART_RETENTION_YEARS:-10}"
int_in_range CH_MART_RETENTION_YEARS "$RETENTION_YEARS" 1 30
PSEUDO_YEARS="${CH_PSEUDONYM_RETENTION_YEARS:-5}"
int_in_range CH_PSEUDONYM_RETENTION_YEARS "$PSEUDO_YEARS" 1 "$RETENTION_YEARS"
GEO_DAYS="${CH_GEO_RETENTION_DAYS:-90}"
int_in_range CH_GEO_RETENTION_DAYS "$GEO_DAYS" 1 3650

# --- Migrasi sekali loyalty_events: ORDER BY (member_hash, event_id) -> (event_id) (W-19). DDL = 02-loyalty-etl.sql.
# Langkah 1 (hanya bila kunci lama): salin ke tabel baru, lalu EXCHANGE (atomik; view/grant memakai nama).
# Langkah 2 (bila tabel lama masih ada setelah exchange): salin baris yang ditulis ETL ke tabel lama selama jendela
# migrasi (_loaded_at 1 jam terakhir, belum ada di tabel baru), lalu DROP tabel lama (hash member di dalamnya tidak
# boleh tertinggal). Baris lebih tua yang tidak ada di tabel baru = sudah dihapus erasure DSAR -> TIDAK disalin ulang.
# Rekomendasi operasional: hentikan `etl` selama deploy yang menjalankan migrasi ini (lihat README ETL).
LE_COLS="event_id, member_hash, company_code, delta_points, reason, source, source_ref, created_at, _loaded_at"
LE_KEY="$($CH --query "SELECT sorting_key FROM system.tables WHERE database = 'efn_dwh' AND name = 'loyalty_events'")"
if [ "$LE_KEY" != "event_id" ]; then
  echo "clickhouse-init: migrasi loyalty_events ORDER BY (${LE_KEY}) -> (event_id)"
  $CH --multiquery <<EOF
DROP TABLE IF EXISTS efn_dwh.loyalty_events_w19 SYNC;
CREATE TABLE efn_dwh.loyalty_events_w19
(
    event_id      String,
    member_hash   String,
    company_code  LowCardinality(String),
    delta_points  Int64,
    reason        LowCardinality(String),
    source        LowCardinality(String),
    source_ref    String,
    created_at    DateTime64(6, 'UTC'),
    _loaded_at    DateTime DEFAULT now(),
    INDEX idx_member_hash member_hash TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = ReplacingMergeTree(created_at)
PARTITION BY toYYYYMM(created_at)
ORDER BY (event_id)
SETTINGS ttl_only_drop_parts = 1;
INSERT INTO efn_dwh.loyalty_events_w19 (${LE_COLS}) SELECT ${LE_COLS} FROM efn_dwh.loyalty_events;
EXCHANGE TABLES efn_dwh.loyalty_events AND efn_dwh.loyalty_events_w19;
EOF
fi
LE_OLD="$($CH --query "SELECT count() FROM system.tables WHERE database = 'efn_dwh' AND name = 'loyalty_events_w19' AND sorting_key != 'event_id'")"
if [ "$LE_OLD" = "1" ]; then
  $CH --multiquery <<EOF
INSERT INTO efn_dwh.loyalty_events (${LE_COLS}) SELECT ${LE_COLS} FROM efn_dwh.loyalty_events_w19
WHERE _loaded_at >= now() - INTERVAL 1 HOUR AND event_id NOT IN (SELECT event_id FROM efn_dwh.loyalty_events);
EOF
  LE_SKIPPED="$($CH --query "SELECT count() FROM efn_dwh.loyalty_events_w19 WHERE event_id NOT IN (SELECT event_id FROM efn_dwh.loyalty_events)")"
  $CH --query "DROP TABLE efn_dwh.loyalty_events_w19 SYNC"
  echo "clickhouse-init: migrasi loyalty_events selesai; tabel lama dihapus (baris lama tak disalin: ${LE_SKIPPED})"
fi

ttl_state() {
  $CH --query "SELECT name, cityHash64(create_table_query) FROM system.tables WHERE database = 'efn_dwh'
    AND name IN ('competitor_prices', 'loyalty_events', 'fact_sales_line', 'fact_pos_payment', 'fact_stock_snapshot',
                 'fact_orderpoint_snapshot', 'stock_snapshot_run') ORDER BY name FORMAT TSV"
}
TTL_BEFORE="$(ttl_state)"
echo "clickhouse-init: TTL mart ${RETENTION_YEARS} th, pseudonim ${PSEUDO_YEARS} th, lokasi Price Hunter ${GEO_DAYS} hari"
# MODIFY COLUMN wajib menyebut tipe yang SAMA dengan skema (tanpa tipe, parser membaca `TTL` sebagai nama tipe);
# tipe sama = perubahan metadata saja (tanpa konversi data).
$CH --multiquery <<EOF
SET materialize_ttl_after_modify = 0;
ALTER TABLE efn_dwh.competitor_prices MODIFY TTL reported_at + INTERVAL ${RETENTION_YEARS} YEAR;
ALTER TABLE efn_dwh.competitor_prices MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE efn_dwh.competitor_prices MODIFY COLUMN user_hash String TTL reported_at + INTERVAL ${PSEUDO_YEARS} YEAR;
ALTER TABLE efn_dwh.competitor_prices MODIFY COLUMN geo_lat Nullable(Float64) TTL reported_at + INTERVAL ${GEO_DAYS} DAY;
ALTER TABLE efn_dwh.competitor_prices MODIFY COLUMN geo_lng Nullable(Float64) TTL reported_at + INTERVAL ${GEO_DAYS} DAY;
ALTER TABLE efn_dwh.loyalty_events MODIFY TTL toDateTime(created_at) + INTERVAL ${RETENTION_YEARS} YEAR;
ALTER TABLE efn_dwh.loyalty_events MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE efn_dwh.loyalty_events MODIFY COLUMN member_hash String TTL toDateTime(created_at) + INTERVAL ${PSEUDO_YEARS} YEAR;
ALTER TABLE efn_dwh.fact_sales_line MODIFY TTL order_date + INTERVAL ${RETENTION_YEARS} YEAR;
ALTER TABLE efn_dwh.fact_sales_line MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE efn_dwh.fact_sales_line MODIFY COLUMN customer_id Nullable(UInt32) TTL order_date + INTERVAL ${PSEUDO_YEARS} YEAR;
ALTER TABLE efn_dwh.fact_pos_payment MODIFY TTL payment_date + INTERVAL ${RETENTION_YEARS} YEAR;
ALTER TABLE efn_dwh.fact_pos_payment MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE efn_dwh.fact_stock_snapshot MODIFY TTL snapshot_date + INTERVAL ${RETENTION_YEARS} YEAR;
ALTER TABLE efn_dwh.fact_stock_snapshot MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE efn_dwh.fact_orderpoint_snapshot MODIFY TTL snapshot_date + INTERVAL ${RETENTION_YEARS} YEAR;
ALTER TABLE efn_dwh.fact_orderpoint_snapshot MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE efn_dwh.stock_snapshot_run MODIFY TTL snapshot_date + INTERVAL ${RETENTION_YEARS} YEAR;
ALTER TABLE efn_dwh.stock_snapshot_run MODIFY SETTING ttl_only_drop_parts = 1;
EOF
TTL_AFTER="$(ttl_state)"
if [ "$TTL_BEFORE" != "$TTL_AFTER" ]; then
  # Hanya tabel yang definisinya berubah. Mutation async (mutations_sync=0): deploy tidak menunggu penulisan ulang.
  printf '%s\n' "$TTL_AFTER" | while IFS="$(printf '\t')" read -r tbl hash; do
    if ! printf '%s\n' "$TTL_BEFORE" | grep -qx "${tbl}$(printf '\t')${hash}"; then
      echo "clickhouse-init: MATERIALIZE TTL efn_dwh.${tbl} (definisi TTL berubah)"
      $CH --query "ALTER TABLE efn_dwh.${tbl} MATERIALIZE TTL" < /dev/null
    fi
  done
fi

# Salinan audit (G12): retensi terpisah, default 10 tahun (>= retensi log Odoo; selaras kewajiban simpan dokumen
# 10 tahun UU KUP) — usulan menunggu DPO (Q-49). TTL dijalankan server saat merge (bukan hak ETL). Tanpa partisi ->
# baris kedaluwarsa dibuang per baris; verifikasi rantai memakai baris tertua yang tersisa sebagai jangkar.
# GL (fact_gl_line) sengaja TANPA TTL: catatan akuntansi; penghapusan mengikuti kebijakan arsip keuangan.
AUDIT_YEARS="${CH_AUDIT_COPY_RETENTION_YEARS:-10}"
case "$AUDIT_YEARS" in
  ''|*[!0-9]*) echo "clickhouse-init: CH_AUDIT_COPY_RETENTION_YEARS harus bilangan bulat" >&2; exit 1 ;;
esac
if [ "$AUDIT_YEARS" -lt 2 ] || [ "$AUDIT_YEARS" -gt 30 ]; then
  echo "clickhouse-init: CH_AUDIT_COPY_RETENTION_YEARS di luar rentang 2..30" >&2; exit 1
fi
echo "clickhouse-init: TTL salinan audit ${AUDIT_YEARS} tahun"
$CH --multiquery <<EOF
SET materialize_ttl_after_modify = 0;
ALTER TABLE efn_dwh.audit_log_copy MODIFY TTL toDateTime(create_date) + INTERVAL ${AUDIT_YEARS} YEAR;
ALTER TABLE efn_dwh.audit_log_line_copy MODIFY TTL toDateTime(create_date) + INTERVAL ${AUDIT_YEARS} YEAR;
EOF

create_user() {
  # $1 = nama user, $2 = file secret, $3 (opsional) = CIDR IPv4 tervalidasi -> HOST IP '<cidr>' (CREATE + ALTER, jadi
  # user lama ikut dipersempit). Tanpa $3 = HOST ANY (pola lama; kontrol utama = isolasi network Docker).
  # ClickHouse menyamarkan password CREATE/ALTER USER di query_log/log server.
  pw="$(cat "$2")"
  pw_ok "$pw" || { echo "clickhouse-init: $2 tidak valid (charset/panjang)" >&2; exit 1; }
  if [ -n "${3:-}" ]; then
    $CH --multiquery <<EOF
CREATE USER IF NOT EXISTS $1 IDENTIFIED WITH sha256_password BY '${pw}' HOST IP '$3';
ALTER USER $1 IDENTIFIED WITH sha256_password BY '${pw}' HOST IP '$3';
EOF
  else
    $CH --multiquery <<EOF
CREATE USER IF NOT EXISTS $1 IDENTIFIED WITH sha256_password BY '${pw}' HOST ANY;
ALTER USER $1 IDENTIFIED WITH sha256_password BY '${pw}';
EOF
  fi
  unset pw
}

# SEC-215 (G44) + SEC-223 (G45): CIDR IPv4 privat dengan prefix /24../32. /16../23 ditolak: 172.30.0.0/16 mencakup
# SEMUA network EFN (data/insight-data/esb/...) dan meniadakan pembatasan HOST. Nilai disisipkan sebagai literal SQL ->
# charset dibatasi [0-9./] sebelum apa pun. Kanonik (bit host nol) ditegakkan di deploy-validate.sh.
private_cidr_ok() {
  case "$1" in
    */*) ;;
    *) return 1 ;;
  esac
  case "$1" in *[!0-9./]*) return 1 ;; esac
  c_ip="${1%/*}"; c_p="${1#*/}"
  case "$c_p" in ''|*[!0-9]*) return 1 ;; esac
  if [ "${#c_p}" -gt 2 ] || [ "$c_p" -lt 24 ] || [ "$c_p" -gt 32 ]; then return 1; fi
  IFS=. read -r o1 o2 o3 o4 c_rest <<EOF
$c_ip
EOF
  [ -z "$c_rest" ] || return 1
  for o in "$o1" "$o2" "$o3" "$o4"; do
    case "$o" in ''|*[!0-9]*) return 1 ;; esac
    if [ "${#o}" -gt 3 ] || [ "$o" -gt 255 ]; then return 1; fi
  done
  if [ "$o1" -eq 10 ]; then return 0; fi
  if [ "$o1" -eq 172 ] && [ "$o2" -ge 16 ] && [ "$o2" -le 31 ]; then return 0; fi
  if [ "$o1" -eq 192 ] && [ "$o2" -eq 168 ]; then return 0; fi
  return 1
}
create_user efn_etl /run/secrets/clickhouse_password_etl
create_user efn_text2sql /run/secrets/clickhouse_password_text2sql
create_user efn_metabase /run/secrets/clickhouse_password_metabase
# G41 (docs/04 §25.7): user service esb-extract. Secret di-mount ke clickhouse-init di base compose (devsecops).
create_user efn_esb /run/secrets/clickhouse_password_esb
# G42 (kontrak G42 §B, ADR-041): user service insight-web. WAJIB seperti efn_text2sql (secret di-mount ke
# clickhouse-init di base compose, devsecops) -> secret hilang = init gagal (fail closed, bukan user tanpa password).
create_user efn_insight /run/secrets/clickhouse_password_insight
# G44 (ADR-046, docs/04 §28.2): user forecast-worker (read-only efn_forecast). Devsecops (G44) me-mount secret
# clickhouse_password_forecast ke clickhouse-init di base compose (semua env; scripts/secrets.list `random`) -> WAJIB
# seperti efn_insight: secret hilang = init gagal (fail closed, bukan user tanpa password).
# SEC-215 (pertahanan berlapis di atas isolasi network): login HANYA dari subnet network `forecast-ch`
# (overlay docker-compose.forecast.yml, ipam <EFN_SUBNET_BASE>.11.0/24) -> password yang bocor tidak berguna dari network
# lain (data/insight-data/esb). CH_FORECAST_READER_HOST_CIDR (compose clickhouse-init, default turunan EFN_SUBNET_BASE,
# K25-06) WAJIB = subnet itu (scripts/check-encryption.sh); antar-mesin (K25-02) = subnet WireGuard mesin data (runbook
# forecast-worker §7). Fallback di bawah hanya bila skrip dijalankan tanpa compose.
FORECAST_CIDR="${CH_FORECAST_READER_HOST_CIDR:-${EFN_SUBNET_BASE:-172.30}.11.0/24}"
private_cidr_ok "$FORECAST_CIDR" \
  || { echo "clickhouse-init: CH_FORECAST_READER_HOST_CIDR harus CIDR IPv4 privat /24../32 (dapat: ${FORECAST_CIDR}; SEC-223)" >&2; exit 1; }
create_user efn_forecast_reader /run/secrets/clickhouse_password_forecast "$FORECAST_CIDR"
# G50 (ADR-047 Proposed, docs/04 §28.7): user segment-worker (read-only efn_segment.v_*, access/segment-access.sql).
# Pola efn_forecast_reader: secret clickhouse_password_segment di-mount ke clickhouse-init di base compose (semua env;
# scripts/secrets.list `random`) -> secret hilang = init gagal (fail closed). segment-worker hanya di overlay
# docker-compose.segment.yml.
# K25-06 + pola SEC-215/SEC-223: login HANYA dari subnet network `segment-ch` (ipam <EFN_SUBNET_BASE>.17.0/24).
# CH_SEGMENT_READER_HOST_CIDR (compose clickhouse-init) WAJIB = subnet itu (scripts/check-encryption.sh).
SEGMENT_CIDR="${CH_SEGMENT_READER_HOST_CIDR:-${EFN_SUBNET_BASE:-172.30}.17.0/24}"
private_cidr_ok "$SEGMENT_CIDR" \
  || { echo "clickhouse-init: CH_SEGMENT_READER_HOST_CIDR harus CIDR IPv4 privat /24../32 (dapat: ${SEGMENT_CIDR}; SEC-223)" >&2; exit 1; }
create_user efn_segment_reader /run/secrets/clickhouse_password_segment "$SEGMENT_CIDR"
# G43 (docs/04 §27.10): user mesin migrasi `efn_mig` HANYA selama program migrasi. Secret tidak di-mount / kosong /
# placeholder (`disabled`, scripts/secrets.list) -> user dihapus (bukan gagal: stack normal tidak butuh migrasi).
# Secret ada tetapi tidak valid (charset/panjang) -> init gagal (create_user, fail closed).
MIG_SECRET=/run/secrets/clickhouse_password_mig
MIG_PW=""
[ -r "$MIG_SECRET" ] && MIG_PW="$(cat "$MIG_SECRET")"
case "$(printf '%s' "$MIG_PW" | tr '[:upper:]' '[:lower:]')" in
  ''|disabled|changeme|change-me|placeholder|xxx|todo|password) MIG_ENABLED=0 ;;
  *) MIG_ENABLED=1 ;;
esac
unset MIG_PW
if [ "$MIG_ENABLED" = "1" ]; then
  create_user efn_mig "$MIG_SECRET"
else
  echo "clickhouse-init: secret clickhouse_password_mig tidak ada/placeholder -> user efn_mig dihapus (migrasi nonaktif)"
  $CH --query "DROP USER IF EXISTS efn_mig" < /dev/null
fi

for f in /init/access/*.sql; do
  [ -e "$f" ] || continue
  echo "clickhouse-init: akses $(basename "$f")"
  $CH --multiquery < "$f"
done

# text2sql-access.sql membuat role & profile; pengikatan ke user dilakukan di sini (file itu hanya memberi contoh).
$CH --multiquery <<'EOF'
GRANT text2sql_reader TO efn_text2sql;
SET DEFAULT ROLE text2sql_reader TO efn_text2sql;
ALTER USER efn_text2sql SETTINGS PROFILE 'text2sql_readonly';
EOF

# G41: esb-access.sql membuat role esb_writer & profil; pengikatan ke user di sini (pola text2sql).
# Row policy efn_t2s untuk v_esb_* sengaja BELUM dibuat (ditunda ke G42, docs/04 §25.7).
$CH --multiquery <<'EOF'
GRANT esb_writer TO efn_esb;
SET DEFAULT ROLE esb_writer TO efn_esb;
ALTER USER efn_esb SETTINGS PROFILE 'esb_writer_profile';
EOF

# G43: mig-access.sql membuat role mig_rw & profil mig_profile; pengikatan ke user di sini (hanya bila user ada).
if [ "$MIG_ENABLED" = "1" ]; then
  $CH --multiquery <<'EOF'
GRANT mig_rw TO efn_mig;
SET DEFAULT ROLE mig_rw TO efn_mig;
ALTER USER efn_mig SETTINGS PROFILE 'mig_profile';
EOF
fi

# G42: insight-access.sql membuat role insight_reader & profil insight_readonly; pengikatan ke user di sini.
$CH --multiquery <<'EOF'
GRANT insight_reader TO efn_insight;
SET DEFAULT ROLE insight_reader TO efn_insight;
ALTER USER efn_insight SETTINGS PROFILE 'insight_readonly';
EOF

# G44: forecast-access.sql membuat role forecast_reader & profil forecast_readonly; pengikatan ke user di sini.
$CH --multiquery <<'EOF'
GRANT forecast_reader TO efn_forecast_reader;
SET DEFAULT ROLE forecast_reader TO efn_forecast_reader;
ALTER USER efn_forecast_reader SETTINGS PROFILE 'forecast_readonly';
EOF
# SEC-215: forecast-access.sql memberi `SELECT ON efn_forecast.*` (wildcard; test_access_sql.py milik data-AI mengunci
# bentuk itu) -> objek APA PUN yang kelak dibuat di efn_forecast otomatis terbaca forecast_reader. Pagar fail-closed:
# database efn_forecast hanya boleh berisi VIEW berikut (= VIEW_COLUMNS services/forecast-worker/efn_forecast/source.py);
# objek lain (tabel, view baru yang belum ditinjau) -> grant role dicabut + init gagal. View baru = tambah di sini
# DAN di forecast-access.sql + test data-AI dalam PR yang sama (runbook forecast-worker §5).
FC_ALLOWED="'v_sales_daily','v_stock_daily','v_scrap_daily','v_calendar','v_product','v_warehouse'"
FC_UNEXPECTED="$($CH --query "SELECT name || ' (' || engine || ')' FROM system.tables WHERE database = 'efn_forecast'
  AND (engine != 'View' OR name NOT IN (${FC_ALLOWED})) ORDER BY name FORMAT TSV")"
if [ -n "$FC_UNEXPECTED" ]; then
  $CH --query "REVOKE SELECT ON efn_forecast.* FROM forecast_reader" < /dev/null
  echo "clickhouse-init: objek di luar allowlist di efn_forecast (grant forecast_reader dicabut, SEC-215):" >&2
  printf '%s\n' "$FC_UNEXPECTED" >&2
  exit 1
fi
# SEC-223 (G45): nama + engine saja tidak cukup -- view allowlist yang di-CREATE OR REPLACE menjadi SELECT atas tabel
# lain (atau SQL SECURITY INVOKER/NONE, definer lain) tetap lolos pagar di atas. Bila ClickHouse mengekspos
# system.tables.create_table_query (24.8: ya; kolom dicek lewat system.columns agar versi lain tidak salah gagal),
# definisi tiap view WAJIB berbentuk persis (setelah backtick dibuang dan spasi dinormalkan):
#   ... DEFINER = efn_forecast_definer SQL SECURITY DEFINER AS SELECT <daftar kolom polos> FROM efn_dwh.v_fc_<nama tanpa v_>
# Daftar kolom hanya [A-Za-z0-9_, ] -> tanpa fungsi/table function (remote/url/file), subquery, JOIN, atau db.tabel lain.
# Regex tanpa backslash ([.] / [[:space:]]) agar tidak bergantung pada escape string literal ClickHouse.
FC_HAS_CTQ="$($CH --query "SELECT count() FROM system.columns WHERE database = 'system' AND table = 'tables'
  AND name = 'create_table_query' FORMAT TSV")"
if [ "$FC_HAS_CTQ" = "1" ]; then
  # Satu SELECT dan satu FROM saja (menutup UNION ALL ... SELECT ... FROM <tabel tanpa prefix db>).
  FC_BADDEF="$($CH --query "SELECT name FROM (SELECT name,
      replaceRegexpAll(replaceAll(create_table_query, '\`', ''), '[[:space:]]+', ' ') AS q
    FROM system.tables WHERE database = 'efn_forecast' AND engine = 'View')
    WHERE NOT match(q, concat('DEFINER = efn_forecast_definer SQL SECURITY DEFINER AS SELECT [A-Za-z0-9_, ]+ FROM efn_dwh[.]v_fc_',
                              substring(name, 3), ' ?\$'))
      OR countSubstrings(upper(q), 'SELECT ') != 1 OR countSubstrings(upper(q), ' FROM ') != 1
    ORDER BY name FORMAT TSV")"
  if [ -n "$FC_BADDEF" ]; then
    $CH --query "REVOKE SELECT ON efn_forecast.* FROM forecast_reader" < /dev/null
    echo "clickhouse-init: definisi view efn_forecast menyimpang (wajib DEFINER = efn_forecast_definer SQL SECURITY DEFINER," \
      "SELECT kolom polos FROM efn_dwh.v_fc_*; grant forecast_reader dicabut, SEC-223):" >&2
    printf '%s\n' "$FC_BADDEF" >&2
    exit 1
  fi
else
  echo "clickhouse-init: PERINGATAN system.tables.create_table_query tidak tersedia; pagar definisi view efn_forecast (SEC-223) dilewati" >&2
fi

# G50: segment-access.sql membuat role segment_reader, profil segment_readonly, kuota segment_quota, definer
# efn_segment_definer (HOST NONE) dan view efn_segment.v_*; pengikatan ke user di sini (pola forecast G44).
$CH --multiquery <<'EOF'
GRANT segment_reader TO efn_segment_reader;
SET DEFAULT ROLE segment_reader TO efn_segment_reader;
ALTER USER efn_segment_reader SETTINGS PROFILE 'segment_readonly';
EOF

# G38 (docs/04 §23.3): row policy cakupan PT pada view efn_t2s. Service text2sql mengirim setting SQL_efn_cids
# (company_id, dipisah koma), SQL_efn_codes (kode PT §12.1) dan SQL_efn_sig = hex(SHA256(kunci|cids|codes)); kunci =
# secret text2sql_scope_key (hex). Tanpa setting / tanda tangan salah -> 0 baris atau error (fail closed). Kunci hanya
# ada di policy (role text2sql_reader tidak bisa membaca system.row_policies) dan di service text2sql.
SCOPE_KEY="$(cat /run/secrets/text2sql_scope_key)"
case "$SCOPE_KEY" in
  ''|*[!0-9a-f]*) echo "clickhouse-init: text2sql_scope_key harus hex huruf kecil" >&2; exit 1 ;;
esac
[ "${#SCOPE_KEY}" -ge 32 ] || { echo "clickhouse-init: text2sql_scope_key terlalu pendek" >&2; exit 1; }
SIG="getSetting('SQL_efn_sig') = lower(hex(SHA256(concat('${SCOPE_KEY}', '|', getSetting('SQL_efn_cids'), '|', getSetting('SQL_efn_codes')))))"
CIDS="has(arrayMap(x -> toUInt32OrZero(x), splitByChar(',', getSetting('SQL_efn_cids'))), company_id)"
CODES="has(splitByChar(',', getSetting('SQL_efn_codes')), company_code)"
# SEC-190 (G42): format setting cakupan WAJIB kanonik sebelum tanda tangan dihitung. SHA256(kunci|cids|codes) rentan
# length-extension (codes = "PB<padding>,WTZ" dengan sig turunan); regex ini menolak byte padding/biner apa pun.
# Kode PT 2..8 huruf besar (text2sql: EFN/FARM; insight: PB/WTZ/BCF/BCR/IMD/CUP), cids angka dipisah koma (boleh kosong).
FMT="match(getSetting('SQL_efn_codes'), '^[A-Z]{2,8}(,[A-Z]{2,8})*\$') AND match(getSetting('SQL_efn_cids'), '^([0-9]{1,10}(,[0-9]{1,10})*)?\$')"
for v in v_sales_daily v_outlet_daily v_waste_daily v_mo_yield_daily; do
  echo "CREATE ROW POLICY OR REPLACE efn_t2s_scope ON efn_t2s.${v} USING ${FMT} AND ${CIDS} AND ${SIG} TO text2sql_reader;"
done | $CH --log_queries=0 --multiquery
# SEC-191: --log_queries=0 -> DDL yang memuat kunci tidak tercatat di system.query_log.
echo "CREATE ROW POLICY OR REPLACE efn_t2s_scope ON efn_t2s.v_loyalty_daily USING ${FMT} AND ${CODES} AND ${SIG} TO text2sql_reader;" \
  | $CH --log_queries=0 --multiquery
unset SIG

# G42 (kontrak G42 §B): row policy cakupan PT pada SETIAP view efn_insight (semua ber-company_code). insight-web
# mengirim SQL_efn_cids / SQL_efn_codes / SQL_efn_sig dengan format SAMA dengan text2sql, tetapi kunci = secret
# insight_scope_key (hex, BEDA dari text2sql_scope_key -> tanda tangan satu service tidak berlaku di service lain).
# Predikat hanya company_code (kode ESB: PB/WTZ/BCF/BCR/IMD/CUP; IMD/CUP = company Odoo sejak G43, tetapi scope tetap per kode ESB) + tanda tangan;
# cids tetap ikut ditandatangani. `company_code != ''` = defense in depth (C4 §26.13: splitByChar(',', '') = [''] akan
# mencocokkan baris tanpa PT bila codes kosong ber-sig sah). WAJIB seperti text2sql_scope_key: secret hilang/tidak
# valid = init gagal.
# Kunci = string hex apa adanya (byte ASCII, bukan hex-decode; `$(cat)` membuang newline akhir), [0-9a-fA-F] >= 64
# karakter (docs/04 §26.7/§26.10) -- divalidasi sebelum disisipkan ke DDL (charset hex = aman sebagai literal SQL).
INSIGHT_KEY="$(cat /run/secrets/insight_scope_key)"
case "$INSIGHT_KEY" in
  ''|*[!0-9a-fA-F]*) echo "clickhouse-init: insight_scope_key harus hex" >&2; exit 1 ;;
esac
[ "${#INSIGHT_KEY}" -ge 64 ] || { echo "clickhouse-init: insight_scope_key terlalu pendek (min 64 hex)" >&2; exit 1; }
[ "$INSIGHT_KEY" != "$SCOPE_KEY" ] || { echo "clickhouse-init: insight_scope_key tidak boleh sama dengan text2sql_scope_key" >&2; exit 1; }
unset SCOPE_KEY
INSIGHT_SIG="getSetting('SQL_efn_sig') = lower(hex(SHA256(concat('${INSIGHT_KEY}', '|', getSetting('SQL_efn_cids'), '|', getSetting('SQL_efn_codes')))))"
# Daftar = semua view di insight-access.sql (diuji test_insight_access.py) + diverifikasi ke system.tables di bawah.
for v in v_esb_sales_daily v_esb_sales_branch_daily v_esb_payment_daily v_esb_flow_daily v_esb_menu_mix \
         v_esb_data_quality v_esb_branch_list v_esb_product_list v_esb_movement_daily v_esb_coverage; do
  echo "CREATE ROW POLICY OR REPLACE efn_insight_scope ON efn_insight.${v} USING company_code != '' AND ${FMT} AND ${CODES} AND ${INSIGHT_SIG} TO insight_reader;"
done | $CH --log_queries=0 --multiquery
# G47 (docs/04 §28.13.7, L-188): row policy efn_insight_scope untuk v_sm_* (butuh $INSIGHT_KEY tervalidasi di atas).
# shellcheck source=/dev/null  # file di-mount saat runtime (/init/access)
. /init/access/insight-sm-policy.sh
unset INSIGHT_KEY INSIGHT_SIG
# Fail closed: view efn_insight tanpa row policy = semua PT terbaca (grant efn_insight.* wildcard). Bila ada, cabut
# grant role lalu gagalkan init.
INSIGHT_MISSING="$($CH --query "SELECT name FROM system.tables WHERE database = 'efn_insight' AND name NOT IN
  (SELECT table FROM system.row_policies WHERE database = 'efn_insight' AND short_name = 'efn_insight_scope')
  ORDER BY name FORMAT TSV")"
if [ -n "$INSIGHT_MISSING" ]; then
  $CH --query "REVOKE SELECT ON efn_insight.* FROM insight_reader" < /dev/null
  echo "clickhouse-init: view efn_insight tanpa row policy efn_insight_scope (grant insight_reader dicabut):" >&2
  printf '%s\n' "$INSIGHT_MISSING" >&2
  exit 1
fi

rm -f "$CFG"
echo "clickhouse-init: skema, user (efn_etl, efn_text2sql, efn_metabase, efn_esb, efn_insight, efn_forecast_reader, efn_segment_reader, efn_mig bila aktif) dan grant diterapkan"
