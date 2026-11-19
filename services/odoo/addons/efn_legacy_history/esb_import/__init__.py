"""Importer migrasi ESB -> Odoo (G43 W2b, ADR-042, docs/04 §27.2/§27.4/§27.5/§27.8a v19.1).

Paket Python server-side biasa — BUKAN model dan tidak diimpor `efn_legacy_history/__init__.py`, sehingga tidak
menambah permukaan RPC (SEC-58) dan tidak memuat `cryptography` saat modul dimuat. Pemanggil sah:
`scripts/migration/odoo_import_esb.py` (di dalam `odoo shell`) dan test modul ini.

- `mapping`  : MURNI (tanpa `odoo`): baris bundel -> nilai Odoo; diuji pytest di `scripts/migration/tests`.
- `importer` : butuh Odoo (ORM + `efn_legacy_history.bulk`): urutan impor §27.2, upsert xmlid §27.4, histori bulk.
- `efn_mig`  : salinan terkendali `scripts/migration/efn_mig/{bundle,xmlid}.py` (lihat docstring paketnya).
"""
