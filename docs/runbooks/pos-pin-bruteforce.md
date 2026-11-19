# Runbook: tebakan PIN supervisor POS (`EfnPosPin*`)

Gelombang 45 (19 Nov 2026), SEC-216 (`docs/reviews/SECURITY_REVIEW_2026-11-19_g45.md`). Modul `efn_pos_refund_origin`
(E-41 retur tanpa struk, L-51) dan `efn_expiry_monitor` (override jual lot kedaluwarsa, K18-01). Audiens: on-call
IT/security, Area Manager, kepala toko.

## Apa yang terjadi

PIN Store Manager/supervisor (`res.users.efn_return_pin_hash`, 6–8 digit, hash pbkdf2 hanya di server) menyetujui dua
hal di POS: **retur tanpa struk** (uang keluar) dan **penjualan lot kedaluwarsa** (keamanan pangan, R-L07). PIN yang
tertebak membuka keduanya atas nama supervisor itu.

Server mengunci (lintas kedua jalur, SEC-216):

- approver: 5 PIN salah dalam 15 menit → `locked`;
- kasir: 10 PIN salah dalam 60 menit lintas approver → `cashier_locked`.

Setiap kegagalan (termasuk percobaan saat terkunci) menulis satu WARNING tanpa PIN/hash/data pelanggan:

```text
2026-11-19 10:00:00,123 77 WARNING efn odoo.addons.efn_pos_refund_origin.models.pos_config: EFN_POS_PIN_FAIL path=return_approval config=3 cashier=41 approver=12 reason=wrong_pin
```

Jalur: log Odoo → Promtail (label Docker `service=odoo`, redaksi standar; tanpa stream khusus, retensi 30 hari) →
ruler Loki `infra/observability/loki-rules/efn-pos-pin.yml` (regex berjangkar: timestamp, level WARNING, logger
`odoo.addons.efn_*`, skema key=value tetap) → Alertmanager. Bukti permanen ada di Odoo (append-only, teraudit
`efn_audit`): `efn.pos.return.approval` dan `efn.expiry.pos.approval` dengan `state = failed`.

Query dasar (Grafana → Explore → Loki):

```logql
{service="odoo"} |= "EFN_POS_PIN_FAIL"
  | regexp `EFN_POS_PIN_FAIL path=(?P<path>\S+) config=(?P<config>\d+) cashier=(?P<cashier>\d+) approver=(?P<approver>\d+) reason=(?P<reason>\S+)`
sum by (config, cashier, approver, reason) (count_over_time({service="odoo"} |= "EFN_POS_PIN_FAIL"
  | regexp `config=(?P<config>\d+) cashier=(?P<cashier>\d+) approver=(?P<approver>\d+) reason=(?P<reason>\S+)` [24h]))
```

`config` = `pos.config.id` (Point of Sale → Configuration → Point of Sales, kolom ID / URL), `cashier`/`approver` =
`res.users.id`. Nama user dicari di Odoo, **jangan** disalin ke tiket publik/chat (data pribadi staf, UU PDP).

## EfnPosPinFailures {#efnpospinfailures}

**Warning**: ≥ 5 baris `EFN_POS_PIN_FAIL` dalam 15 menit untuk satu POS config (setara satu lockout approver).

1. **Konfirmasi ke outlet (≤ 15 menit, jam kerja).** Hubungi kepala toko outlet config itu: apakah supervisor memang
   hadir dan salah ketik (mis. PIN baru diganti, layar sentuh bermasalah)? Bila ya dan polanya berhenti, catat di tiket
   dan tutup.
2. **Siapa kasirnya?** Odoo → *Point of Sale → Persetujuan Retur Tanpa Struk* (filter PIN salah/terkunci, group by
   kasir/Store Manager/POS). Untuk override kedaluwarsa: menu persetujuan expiry (`efn.expiry.pos.approval`, filter
   `state = failed`). Satu kasir yang mencoba beberapa approver = pola tebakan.
3. **Bila tidak terjelaskan**: minta kepala toko menutup sesi POS kasir itu dan supervisor yang ditargetkan mengganti
   PIN (*Preferensi → Atur PIN*). Lanjut ke [EfnPosPinBruteForce](#efnpospinbruteforce) langkah 2.

## EfnPosPinBruteForce {#efnpospinbruteforce}

**Critical**: ≥ 10 dalam 15 menit untuk satu config (menekan warning config yang sama, `alertmanager.yml`). Lockout
server sudah aktif, tetapi pola ini berarti percobaan berlanjut lintas approver/jalur atau otomatis.

1. **Hentikan sumber (≤ 15 menit).** Kepala toko/Area Manager menutup sesi POS config itu (*Close Session*) dan
   menahan kasir terkait dari register sampai ditinjau. Jangan mematikan seluruh outlet bila register lain normal.
2. **Rotasi PIN** semua supervisor yang muncul sebagai `approver` di log (*Settings → Users → Atur PIN* oleh admin,
   atau supervisor sendiri). PIN baru tidak boleh lemah (validasi server menolak deret/berulang).
3. **Periksa apakah ada yang lolos.** Di menu persetujuan, cari `approved`/`consumed` untuk approver yang sama dalam
   jendela kejadian dan sesudahnya: retur tanpa struk (nominal, metode tunai) dan `efn.expiry.event` jenis
   `pos_expired_override`. Persetujuan sah yang tidak dikenali supervisor = insiden fraud.
4. **Eskalasi** ke [security-incident.md](security-incident.md) (severity sesuai temuan langkah 3), libatkan HR/Loss
   Prevention untuk kasir terkait. Simpan bukti: ekspor record approval (`state`, waktu, kasir, approver, config) dan
   query Loki di atas; record Odoo append-only dan teraudit.
5. Setelah ditinjau, buka kembali register dengan kasir yang diizinkan.

## EfnPosPinLogFormatUnrecognized {#efnpospinlogformatunrecognized}

**Warning**: ada baris dari logger `odoo.addons.efn_*` dengan tag `EFN_POS_PIN_FAIL` yang tidak cocok skema berjangkar
(mis. `path`/`reason` baru, urutan key berubah). Dua alert di atas buta untuk baris itu.

1. Bandingkan format `log_pin_failure` di `services/odoo/addons/efn_pos_refund_origin/models/return_pin.py` dengan regex
   di `infra/observability/loki-rules/efn-pos-pin.yml` (tiga tempat, harus identik). Perbarui keduanya dalam satu PR.
2. Sementara itu periksa kegagalan PIN langsung di Odoo (langkah 2 [EfnPosPinFailures](#efnpospinfailures)).

## Batas yang diketahui

- Penghitungan per `config` (register). Penyerang yang berpindah register dalam satu outlet tetap terkunci per
  approver/kasir di server, tetapi alert per config bisa di bawah ambang; query per `cashier` di atas menutup analisis.
- Odoo offline/log tidak sampai ke Loki (promtail mati) = alert buta; lockout server tetap berlaku. `EfnServiceDown`
  dan alert promtail/Loki menutup sebagian.
- Ambang 5/10 adalah titik awal; sesuaikan setelah pilot SMD bila salah ketik wajar sering memicu warning.
