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

# Retensi mart (docs/04 §8.4, SEC-11; usulan menunggu DPO): TTL default 5 tahun, configurable lewat env
# CH_MART_RETENTION_YEARS (bilangan bulat 1..30). Diterapkan ulang setiap deploy (idempoten; mengubah env = mengubah
# TTL). materialize_ttl_after_modify=0: tidak menulis ulang part saat deploy; baris kedaluwarsa dibuang saat merge
# (ttl_only_drop_parts=1 -> partisi bulanan yang seluruhnya kedaluwarsa dibuang utuh). Pemilik: efn-data-ai-engineer.
RETENTION_YEARS="${CH_MART_RETENTION_YEARS:-5}"
case "$RETENTION_YEARS" in
  ''|*[!0-9]*) echo "clickhouse-init: CH_MART_RETENTION_YEARS harus bilangan bulat" >&2; exit 1 ;;
esac
if [ "$RETENTION_YEARS" -lt 1 ] || [ "$RETENTION_YEARS" -gt 30 ]; then
  echo "clickhouse-init: CH_MART_RETENTION_YEARS di luar rentang 1..30" >&2; exit 1
fi
echo "clickhouse-init: TTL mart ${RETENTION_YEARS} tahun"
$CH --multiquery <<EOF
SET materialize_ttl_after_modify = 0;
ALTER TABLE efn_dwh.competitor_prices MODIFY TTL reported_at + INTERVAL ${RETENTION_YEARS} YEAR;
ALTER TABLE efn_dwh.competitor_prices MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE efn_dwh.loyalty_events MODIFY TTL toDateTime(created_at) + INTERVAL ${RETENTION_YEARS} YEAR;
ALTER TABLE efn_dwh.loyalty_events MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE efn_dwh.fact_sales_line MODIFY TTL order_date + INTERVAL ${RETENTION_YEARS} YEAR;
ALTER TABLE efn_dwh.fact_sales_line MODIFY SETTING ttl_only_drop_parts = 1;
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
