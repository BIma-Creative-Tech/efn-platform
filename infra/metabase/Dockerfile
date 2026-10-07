# syntax=docker/dockerfile:1.7
# Metabase + driver ClickHouse resmi (ClickHouse Inc., Apache-2.0) — Gelombang 13, efn-data-ai-engineer.
# Metabase OSS v0.52 TIDAK membawa driver ClickHouse; tanpa ini koneksi `efn_metabase` -> ClickHouse tidak bisa dibuat.
# Driver 1.52.0 = dukungan formal Metabase 0.52.x (JDBC clickhouse-java 0.8.0). Jar dipin sha256 (BuildKit ADD --checksum).
# Wiring compose (`image: efn/metabase:<tag>` + `build: ../metabase`) = tugas efn-devsecops-engineer.
FROM metabase/metabase:v0.52.4@sha256:ea0229a63116ea625ef09085a84ee0d2618311fd34a6e72cc527ae2eb003a5f9

ADD --checksum=sha256:ba694f891c2c76ef949f9c939ae0df7de7f9667089982920ea97c582af460bd0 \
    https://github.com/ClickHouse/metabase-clickhouse-driver/releases/download/1.52.0/clickhouse.metabase-driver.jar \
    /plugins/clickhouse.metabase-driver.jar
RUN chmod 0644 /plugins/clickhouse.metabase-driver.jar
