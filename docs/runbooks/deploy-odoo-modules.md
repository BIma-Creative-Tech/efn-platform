# Runbook: Deploy gagal — modul Odoo wajib belum terpasang (SEC-129)

Dasar: review keamanan r11 (SEC-122/SEC-129, L-25j), kontrol E-31 "refund POS hanya ke metode bayar asal". Audiens:
on-call IT + Superadmin.

> **Ringkasan manajemen.** Beberapa aturan keamanan kasir hidup di modul Odoo buatan EFN. Bila modul itu tidak
> terpasang, aturannya diam-diam tidak berlaku. Deploy sekarang menolak berjalan sampai modul wajib terpasang.

## Gejala

`deploy.sh` berhenti (langkah setelah `up`, sebelum grant ETL) dengan pesan:

```
deploy: modul Odoo wajib belum terpasang di efn_odoo: efn_pos_refund_origin=<state|absent> - install/upgrade ...
```

`<state>` dari `ir_module_module.state`: `uninstalled`, `to install`, `to upgrade`, `to remove`, atau `absent` (modul
tidak dikenal DB — daftar modul belum diperbarui).

## Cek

Daftar modul wajib: `EFN_REQUIRED_ODOO_MODULES` di `scripts/lib/deploy-validate.sh` (G18: `efn_pos_refund_origin`).
Query yang sama dengan deploy (user `efn_odoo`, socket lokal container postgres):

```bash
cd /opt/efn/compose && DC="docker compose $(sed 's/^/-f /' .efn-compose-files | tr '\n' ' ')"
$DC exec -T postgres psql -X -t -A -U efn_odoo -d efn_odoo \
  -c "SELECT name || '|' || state FROM ir_module_module WHERE name IN ('efn_pos_refund_origin')"
```

## Perbaikan

1. Pastikan image Odoo rilis memuat addon (`efn_pos_refund_origin` ada di `services/odoo/addons`).
2. Install/upgrade di luar jam operasional POS (Superadmin; pola [dev-stack-boot.md](dev-stack-boot.md) §3):
   ```bash
   $DC run --rm --no-deps -T odoo odoo -d efn_odoo --no-http --stop-after-init -u base -i efn_pos_refund_origin
   $DC restart odoo
   ```
   (`-u base` memperbarui daftar modul bila state `absent`.) Perubahan modul tercatat `efn_audit`
   ([audit-sink-alerts.md](audit-sink-alerts.md)).
3. Ulangi workflow Deploy. Pesan `==> Modul Odoo wajib terpasang: ...` = lulus.

Bila modul sengaja di-uninstall tanpa change request → perlakukan sebagai insiden
([security-incident.md](security-incident.md)): refund POS sejak saat itu tidak dicek metode asalnya.

## Menambah modul wajib

Tambah nama ke `EFN_REQUIRED_ODOO_MODULES` (deploy-validate.sh) + kasus di `scripts/host/test-efn-deploy.sh`
(`efn_odoo_modules_missing`). Cek berjalan hanya bila DB Odoo sudah ter-install (`pos_order` ada); DB kosong (deploy
pertama sebelum install Odoo) dilewati bersama grant ETL.
