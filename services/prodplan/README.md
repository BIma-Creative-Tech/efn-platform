# prodplan — Production planning CK di atas ESB (G66)

**Kontrak:** `docs/04_SERVICE_CONTRACTS.md` §30 · **ADR:** ADR-051 (**Proposed** — belum disetujui klien) ·
**Skema DB:** `infra/postgres/init/23-prodplan-g66.sql` (satu-satunya sumber DDL; aplikasi tidak bermigrasi).

## Tujuan
Planner CK PB menyusun rencana produksi: permintaan (impor PR + manual) → ledakan BOM multi-level → kebutuhan bahan
(kemasan utuh + usulan batch tambahan) → usulan transfer → persetujuan kepala gudang (SoD) → posting dokumen transfer
dan produksi ke ESB lewat outbox. Rencana disimpan di PostgreSQL `efn_prodplan` (sumber kebenaran), bukan ClickHouse.

## Menjalankan
- `python -m efn_prodplan serve` — satu proses: API `127.0.0.1:8080` (di belakang ghostunnel `prodplan-tls` :8443) +
  thread worker job + worker outbox + purge harian 02:00 WIB.
- `python -m efn_prodplan check-config` — validasi env/secret tanpa ESB/PG (exit 2 bila tidak valid).
- `PRODPLAN_ENABLED` bukan `true` → `/healthz` 200, `/readyz` 200 `disabled`, `/v1/*` 503, tanpa ESB/PG; proses tidak exit.
- Test: `pip install -r requirements-dev.txt && ruff check . && pytest` (integrasi PG: set `PRODPLAN_TEST_DSN`).

## Modul
| Modul | Isi |
|---|---|
| `config.py` | env §30.3 fail-closed; `PROD_WRITE_UNLOCKED = False` (L-250) |
| `esb.py` | klien ESB: allowlist §30.4 sebelum socket, dua base URL, TLS certifi, tanpa redirect/proxy, token bucket, tulis tanpa retry (`EsbNotSent` vs `EsbSentUnknown`), nomor dokumen di-URL-encode |
| `calc.py` | perhitungan murni §30.5 (Decimal) |
| `actor.py` | verifikasi `X-EFN-Actor` `ppa1` §30.7 |
| `workflow.py` | mesin status rencana, SoD, gerbang posting, galat API |
| `payloads.py` | body ESB kedua mode + marker `pp:<12hex>` |
| `outbox.py` | worker outbox §30.9 (rekonsiliasi GET, tanpa POST ulang otomatis) |
| `jobs.py` | job `pr_import`/`calculate`, katalog BOM (header `/product/bom/{bomID}`, cache 15 menit) |
| `service.py` / `api.py` | endpoint §30.8 (stdlib `ThreadingHTTPServer`, tanpa framework) + `GET /v1/boms` |
| `store.py` | psycopg 3, TLS verify-full CA internal, purge §30.10 (audit lewat fungsi SECURITY DEFINER) |

## Risiko yang dimitigasi
R-S23 (tulis ke environment salah: allowlist host+path, kunci kode production, `allow_write` per klien), R-O15 (dokumen
produksi langsung Authorized: SoD dua orang, digest pratinjau, `PRODPLAN_POST_MAX_DOCS`, DELETE terurut), R-T23 (stok
basi: umur snapshot saat submit/posting), R-O10 (beban ESB: token bucket 2 req/dtk, `PRODPLAN_JOB_MAX_CALLS`), R-S17
(akun admin: password terpisah dari esb-extract), R-S18 (kunci `prodplan_assert_key` terpisah dari kunci sesi Insight).

## Belum / keterbatasan
- Belum diuji end-to-end ke staging ESB maupun PostgreSQL nyata (test unit memakai ESB/store palsu).
- Bentuk respons ESB yang belum terbukti: field nomor PR di list (`purchaseRequestNum` diasumsikan), arti `subtitution`
  di BOM detail (baris bernilai 1 dianggap bahan pengganti dan dilewati), `uomName` di `GET /product`, mode `standard`
  (Q-PP3). `GET /product/branch-stock` diizinkan tetapi belum dipakai (stok sumber dibaca lewat BOM detail per lokasi).
- Indeks BOM = satu GET header per BOM Assembly aktif (di-cache 15 menit) — panggilan awal bisa ratusan.
- Replika tunggal (cache `jti` + rate limit di memori).
