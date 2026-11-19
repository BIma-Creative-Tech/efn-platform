"""Mesin migrasi ESB -> Odoo sisi data (G43, docs/04 §27, ADR-042).

Modul murni (bisa diuji tanpa service): `bundle` (format & kripto bundel), `xmlid` (external ID §27.4), `mapping`
(baris ClickHouse -> JSONL), `segments` (rentang & segmentasi histori §27.3a), `validate` (aturan §27.7), `reconcile`
(laporan §27.5). I/O: `source` (ClickHouse TLS), `settings` (env/secret).
`bundle` dan `xmlid` hanya memakai stdlib + `cryptography` agar bisa dipakai importer sisi Odoo (odoo shell).
"""
