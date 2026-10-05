# text2sql — guard SQL read-only (paket Python, belum service HTTP)

**Tujuan:** memastikan SQL hasil LLM hanya membaca view semantic layer ClickHouse.
**Risiko:** R-S05 (injection via Text-to-SQL), R-T06 (jawaban salah dipakai manajemen). ADR-010/ADR-014 (AI di luar jalur transaksi, jaringan privat).

## Isi

- `efn_text2sql/guard.py` — `guard_sql(sql, config, limit=None)`: parser sqlglot dialect ClickHouse; satu statement; akar `SELECT`/`WITH`/`UNION`; tolak DDL/DML/perintah di node mana pun; whitelist tabel dari semantic layer (CTE lokal diizinkan); tolak database lain (`system`, `information_schema`), table function (`url`, `file`, `remote*`, `s3*`, `numbers`, `mysql`, ...), fungsi efek samping/introspeksi (`sleep`, `dictGet*`, `version`, ...); tolak `SETTINGS`/`FORMAT`/`INTO` dari pengguna; regenerasi SQL tanpa komentar lalu bungkus `SELECT * FROM (...) AS _guarded LIMIT <=1000 SETTINGS max_execution_time = N`.
- **SEC-04 (diperbaiki):** nama CTE diresolusi **per scope** — hanya terlihat di query yang mendefinisikannya dan subquery turunannya (bukan query luar, bukan CTE sebelumnya/badannya sendiri, pada UNION hanya cabang kiri); alias skalar ClickHouse (`WITH 1 AS x`) bukan nama tabel; nama CTE yang bentrok dengan view whitelist atau `protected_tables` ditolak (`cte_shadowing`); `WITH RECURSIVE` ditolak; `IN` hanya subquery atau daftar konstanta (`x IN tabel`, `IN (kolom)` ditolak, `in_not_allowed`); query wajib membaca minimal satu view whitelist. Test regresi: payload laporan security + variasi di `tests/test_guard.py` (`SEC04_EXPLOITS`).
- `efn_text2sql/semantic.py` + `semantic_layer.yaml` — daftar view, kolom, contoh Q/SQL, `protected_tables` (semua tabel dasar `efn_dwh`; wajib diperbarui saat tabel baru dibuat).
- `efn_text2sql/prompt.py` — prompt untuk model SQL dan ekstraksi blok ```sql.

## Pemakaian dengan Ollama Qwen2.5-Coder

```python
import requests
from efn_text2sql.guard import SqlGuardError, guard_sql
from efn_text2sql.prompt import build_prompt, extract_sql
from efn_text2sql.semantic import load_semantic_layer

layer = load_semantic_layer("semantic_layer.yaml")
resp = requests.post(
    "https://ollama:11434/api/generate",  # sidecar TLS; verify = CA internal (docs/04 §7.1)
    verify="/run/secrets/efn_ca_crt",
    json={
        "model": "qwen2.5-coder:7b",
        "prompt": build_prompt(layer, "Rata-rata harga beras per kompetitor"),
        "stream": False,
        "options": {"temperature": 0},
    },
    timeout=60,
)
try:
    safe = guard_sql(extract_sql(resp.json()["response"]), layer.guard_config())
except SqlGuardError as err:
    ...  # tampilkan "pertanyaan tidak bisa dijawab", catat err.code (jangan tampilkan SQL mentah ke log publik)
# Eksekusi safe.sql dengan user ClickHouse efn_text2sql (profil text2sql_readonly) lewat HTTPS 8443:
#   clickhouse_connect.get_client(host="clickhouse", port=8443, secure=True, verify=True,
#                                 ca_cert="/run/secrets/efn_ca_crt", username="efn_text2sql", ...)
# Tampilkan SQL-nya ke pengguna beserta label "AI-generated" (R-T06).
```

Lapisan kedua wajib: `infra/clickhouse/access/text2sql-access.sql` (dijalankan saat deploy setelah devsecops membuat user `efn_text2sql`): profil `readonly=2 CONST`, `allow_ddl=0`, `allow_introspection_functions=0`, `max_execution_time` MAX 30, `max_result_rows` 1000, kuota per jam, role hanya SELECT view + kolom non-identitas tabel dasar (tanpa `user_hash`/`member_hash`/`customer_id`/`order_ref`). Guard bukan pengganti hak akses database.

## Test

```
pip install -r requirements-dev.txt
pytest
```

## Belum

- Service HTTP (auth, rate limit, audit log pertanyaan/SQL) belum dibuat.
- Whitelist level kolom belum (view sudah membuang kolom identitas).
- Perilaku sqlglot 26.0.0 untuk beberapa sintaks ClickHouse (INTO OUTFILE, ARRAY JOIN, `x IN tabel`, alias CTE skalar) belum diverifikasi dengan menjalankan test; guard memilih menolak bila ragu (bentuk WITH tak dikenal, IN tak dikenal -> ditolak).
- Semantik visibilitas CTE ClickHouse 24.8 (analyzer baru) diasumsikan: CTE luar terlihat di subquery. Bila ternyata tidak, `protected_tables` + grant kolom tetap menahan pembacaan tabel dasar/identitas.
