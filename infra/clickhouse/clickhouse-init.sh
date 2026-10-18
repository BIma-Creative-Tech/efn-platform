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
  # $1 = nama user, $2 = file secret. ClickHouse menyamarkan password CREATE/ALTER USER di query_log/log server.
  pw="$(cat "$2")"
  pw_ok "$pw" || { echo "clickhouse-init: $2 tidak valid (charset/panjang)" >&2; exit 1; }
  $CH --multiquery <<EOF
CREATE USER IF NOT EXISTS $1 IDENTIFIED WITH sha256_password BY '${pw}' HOST ANY;
ALTER USER $1 IDENTIFIED WITH sha256_password BY '${pw}';
EOF
  unset pw
}
create_user efn_etl /run/secrets/clickhouse_password_etl
create_user efn_text2sql /run/secrets/clickhouse_password_text2sql
create_user efn_metabase /run/secrets/clickhouse_password_metabase

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

rm -f "$CFG"
echo "clickhouse-init: skema, user (efn_etl, efn_text2sql, efn_metabase) dan grant diterapkan"
