# efn_data_ask — Tanya Data (Text-to-SQL, G38)

Kontrak: `docs/04_SERVICE_CONTRACTS.md` §23, ADR-038, K25-01, Q-48 (dijawab pemilik 1 Nov 2026).

- Menu **Tanya Data** (grup `Tanya Data: Pengguna`, ditetapkan admin untuk Manager/Finance/Owner). Staf menulis
  pertanyaan dalam bahasa Indonesia; service `text2sql` membuat SQL (9router/Ollama), guard sqlglot, lalu ClickHouse
  `efn_t2s` menjalankan dengan row policy per PT. Hasil = tabel + SQL berlabel AI-generated.
- Cakupan PT = PT dipilih ∩ `user.company_ids` (dicek di `action_ask`), ditegakkan ulang di ClickHouse.
- Tanpa GL/keuangan. Hasil tidak dikirim ke LLM. Pertanyaan/SQL/hasil tidak di-log server.
- Log per user (`efn.data.ask`): record rule pemilik; auditor membaca semua di PT-nya. Hasil hanya ditulis sistem.
- Env: `TEXT2SQL_ENABLED` (default false), `EFN_TEXT2SQL_URL` (`https://text2sql:8443`),
  `EFN_TEXT2SQL_API_KEY_FILE` (`/run/secrets/text2sql_api_key`), `EFN_CA_FILE`.
