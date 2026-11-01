# ADR-038: Text-to-SQL "Tanya Data" — Odoo → text2sql → LLM → ClickHouse per PT

- **Status:** Accepted — pemilik proyek (K25-01; menjawab Q-48, 1 Nov 2026), diterapkan G38. Pemakaian 9router di
  **production ditahan** sampai DPO memutuskan transfer lintas negara (Q-T7, aliran P7).
- **Tanggal:** 2026-11-01 (Gelombang 38)
- **Terkait:** ADR-010 (AI di luar jalur transaksi), ADR-014 (AI tanpa egress), ADR-037 (gateway LLM 9router),
  `docs/04` §23, R-S05 (injection Text-to-SQL), R-T06 (jawaban AI salah dipakai manajemen), SEC-04.

> **Ringkasan manajemen.** Manager, Finance, dan Owner bisa bertanya data operasional (penjualan, transaksi outlet,
> poin, waste & yield dapur pusat, harga kompetitor) dalam bahasa Indonesia langsung di Odoo, menu *Tanya Data*.
> Jawaban berupa tabel plus query yang dibuat AI. Setiap orang hanya melihat data PT yang memang boleh ia akses, dan
> pembatasan itu ditegakkan dua kali (di Odoo dan di gudang data). Laporan keuangan (GL) tidak tersedia di fitur ini.
> Ke AI hanya dikirim struktur tabel dan pertanyaan; angka hasil tidak pernah dikirim. Fitur ini mati secara default.

## Konteks
- Q-48 (siapa pengguna Text-to-SQL) terbuka sejak ADR-010. Pemilik memutuskan: staf internal di Odoo, per PT,
  data operasional tanpa GL, hasil tidak dikirim ke LLM.
- Pra-G38: `services/text2sql` hanya paket guard; user ClickHouse `efn_text2sql` membaca view `efn_dwh` dengan grant
  kolom tabel dasar (view INVOKER) dan **tanpa pembatasan per PT**.

## Keputusan
1. **Service `text2sql`** (Python stdlib HTTPS 8443, image sendiri): `POST /v1/ask` dengan service key Odoo. Prompt =
   semantic layer + contoh + pertanyaan → LLM (Ollama lokal default; 9router via overlay `docker-compose.llm-text2sql.yml`,
   fallback Ollama) → `guard_sql` (sqlglot) → ClickHouse.
2. **Database `efn_t2s`**: view `SQL SECURITY DEFINER` (definer `efn_t2s_definer` yang tidak bisa login) di atas view
   `efn_dwh`; role `text2sql_reader` hanya `SELECT ON efn_t2s.*` (grant `efn_dwh` dicabut). Diuji di ClickHouse 24.8.
3. **Cakupan PT lapis 2**: row policy `efn_t2s_scope` = `company_id ∈ SQL_efn_cids` (atau kode PT) **dan** tanda
   tangan `SHA256(kunci|cids|codes)` cocok. Kunci (`text2sql_scope_key`) hanya di clickhouse-init + text2sql. Guard
   menolak `SETTINGS` dan `getSetting`, dan tanda tangan mencegah subquery mengganti cakupan.
4. **Odoo `efn_data_ask`**: menu + log per user (record rule pemilik/auditor), cakupan = PT dipilih ∩
   `user.company_ids` (dibaca dari tabel relasi), hasil hanya ditulis sistem, tidak ada kredensial ClickHouse di Odoo.
5. Kill switch `TEXT2SQL_ENABLED` default false; 9router butuh `TEXT2SQL_CROSS_BORDER_OK=true` dan ditolak di production.

## Konsekuensi
- **Positif:** Q-48 tertutup; isolasi per PT ditegakkan di database, bukan hanya di prompt/guard; jalur AI tetap di luar
  transaksi (ADR-010).
- **Negatif:** satu service + image + sertifikat baru (CA dev dibuat ulang karena nameConstraints; production =
  Superadmin menerbitkan ulang intermediate, runbook internal-pki#service-baru); jawaban AI bisa salah (R-T06) →
  SQL selalu ditampilkan dan diberi label AI-generated.
- **Belum:** GL/keuangan (butuh keputusan terpisah), ringkasan kalimat (sengaja tidak: hasil tidak dikirim ke LLM),
  proxy egress ber-allowlist di level jaringan (sama dengan ADR-037).
