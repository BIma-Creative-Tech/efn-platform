# Security Review G44 Supermarket (forecast, assortment, replenishment) — 18 Nov 2026

Reviewer: efn-security-reviewer (read-only; hanya membaca kode). Worktree `E:\Projects\efn-platform-g44`, branch
`feat/wave-44-supermarket-forecast` (diff terhadap `feat/wave-44-supermarket-foundation` + file untracked).
Acuan: CLAUDE.md, docs/04 §28.2/§28.10, ADR-045, ADR-046. Nomor temuan lanjut dari G43 (SEC-197..206): **SEC-207..215**.

## Ringkasan eksekutif

- Tidak ada temuan **Critical/High**. Dari sisi keamanan **tidak ada yang memblokir PR**. Jalur forecast-worker ke Odoo
  (mTLS CN, allowlist path per CN, API key per PT, user bot "tepat satu company", sudo baru dipakai setelah cek pemanggil)
  dan isolasi ClickHouse (readonly=2, view DEFINER dengan kolom eksplisit, tanpa PII) sudah dibangun dengan benar.
- Dua temuan **Medium** sebaiknya ditutup sebelum pilot produksi karena berkaitan dengan kepatuhan alkohol/non-halal
  (Q-SM22): (1) kill switch `efn_assortment.enforce` dan parameter `efn_replenishment.*`/`efn_forecast.*` tidak teraudit
  `efn_audit`; (2) capability outlet (`alcohol_license`, `non_halal_allowed`) dan flag DC bisa diubah Inventory
  Administrator tanpa jejak audit, sehingga larangan override bisa dilewati diam-diam.
- Enam temuan Low/Info: bypass assortment lewat transfer dua langkah manual (terdeteksi laporan harian), qty tanpa batas
  atas (kepala toko dan payload worker), guard tulis `efn.demand.forecast`, SoD PO di jalur `button_approve`, dependency
  transitif Python belum di-lock, dan hardening ClickHouse/observability.
- Yang belum diverifikasi: tidak ada test yang dijalankan (review statis). Perilaku runtime Odoo 19 json2 (422/403) dan
  `location_id.warehouse_id` lokasi transit belum diuji di container.

## Tabel temuan

| ID | Severity | Area | File:baris | Ringkas | Risiko |
|---|---|---|---|---|---|
| SEC-207 | Medium | efn_assortment / efn_replenishment / efn_demand_forecast | `efn_setup_audit/data/efn_audit_rule_data.xml:134`; `efn_assortment/models/stock.py:36-38,109-110` | Filter audit `ir.config_parameter` hanya `efn.%`; kunci `efn_assortment.enforce`, `efn_replenishment.enabled/safety_z`, `efn_forecast.*` tidak teraudit | R-O14, R-L (Q-SM22) |
| SEC-208 | Medium | efn_assortment | `efn_assortment/models/stock.py:16-33`; `views/efn_assortment_views.xml:115-129`; `data/efn_audit_rule_data.xml:2-3` | `efn_capability_ids`, `efn_is_dc`, `efn_assortment_policy` di `stock.warehouse` bisa ditulis Inventory Administrator (bukan Assortment Manager), tanpa audit; alkohol/non-halal lolos tanpa override | R-O14, Q-SM22 |
| SEC-209 | Low (PLAUSIBLE) | efn_assortment | `efn_assortment/models/stock.py:49-91` | Transfer DC -> transit lalu transit -> outlet (dua picking manual) tidak dicek; hanya tertangkap laporan harian | ADR-045 §3.2 |
| SEC-210 | Low | efn_replenishment | `efn_replenishment/models/efn_replenishment.py:631-633,337-343` | `qty_approved` kepala toko tanpa batas atas dan tanpa cek finite; cap rak/umur simpan bisa dilewati | R-T11 |
| SEC-211 | Low | efn_demand_forecast | `efn_demand_forecast/models/forecast_api.py:67-73`; `efn_forecast_daily.py:131`; `efn_demand_forecast.py:379` | Angka payload worker hanya `finite >= 0` tanpa plafon (yhat/min/max 1e15 diterima) dan langsung mengisi `qty_to_order_manual` DC | R-S03, ADR-046 |
| SEC-212 | Low | efn_demand_forecast | `efn_demand_forecast/models/efn_demand_forecast.py:56-74`; `security/ir.model.access.csv:2` | Stock user (ACL write) bisa menulis `state`/`source`/`forecast_run_id`/`approved_*` lewat RPC; usulan `shadow` (v2 untuk gudang v1) bisa dihidupkan menjadi `draft` lalu diterapkan | L-148 |
| SEC-213 | Low (PLAUSIBLE) | efn_replenishment | `efn_replenishment/models/stock_purchase.py:156-161` | SoD "kepala toko tidak menyetujui PO" hanya di `button_confirm`; jalur double validation `button_approve` tidak dicek | docs/04 §28.4 |
| SEC-214 | Low | forecast-worker | `services/forecast-worker/requirements.txt:1-21` | Dependency transitif (coreforecast, fugue/utilsforecast, tqdm, cloudpickle, dll.) tidak di-pin/hash; lisensi belum diverifikasi | R-S (supply chain) |
| SEC-215 | Info | ClickHouse / observability | `infra/clickhouse/clickhouse-init.sh:204,220`; `infra/clickhouse/access/forecast-access.sql:87` | User `efn_forecast_reader` `HOST ANY`; `GRANT SELECT ON efn_forecast.*` otomatis mencakup objek baru; tidak ada alert run gagal/stale atau API key bot kedaluwarsa (35 hari) | R-T15 |

## Detail

### SEC-207 (Medium) — Kill switch assortment/replenishment/forecast tidak teraudit

- **Bukti.** Aturan `audit_rule_ir_config_parameter` memakai `('key', '=like', 'efn.%')` (titik literal). Kunci G44
  berawalan `efn_` (garis bawah): `efn_assortment.enforce`, `efn_replenishment.enabled`, `efn_replenishment.safety_z`,
  `efn_forecast.payday_*`, `efn_forecast.daily_retention_days`. Tidak satu pun cocok. README `efn_assortment` baris 53-54
  sudah mengakui celah ini; jejaknya hanya `_logger.warning` di setiap `_action_confirm` (`stock.py:110`), dan log aplikasi
  tidak memuat siapa pelakunya.
- **Skenario.** User Settings (group_system) mengubah `efn_assortment.enforce` menjadi `false`, mengirim bir dari DC ke
  outlet tanpa izin alkohol, lalu mengembalikan nilainya ke `true`. Tidak ada record `efn.audit.log` (nilai lama/baru,
  pelaku). Tanggung jawab tidak bisa dibuktikan saat audit izin alkohol atau sertifikasi halal outlet. Dengan cara yang
  sama, `safety_z=4` menggelembungkan semua usulan order tanpa jejak.
- **Rekomendasi.** Perluas filter aturan audit menjadi `['|', '|', ('key', '=like', 'efn.%'), ('key', '=like', 'efn\_%'), ...]`
  (escape `_`) atau daftarkan kunci G44 secara eksplisit. Karena data `noupdate=1`, ubah lewat migrasi/post-init hook
  `efn_setup_audit` (versi naik) supaya DB yang sudah ada ikut berubah. Tambahkan test: menulis `efn_assortment.enforce`
  menghasilkan log audit. Pertimbangkan alert Loki untuk pesan "penegakan dimatikan".

### SEC-208 (Medium) — Capability outlet dan flag DC dapat diubah tanpa peran Assortment Manager dan tanpa audit

- **Bukti.** Field `efn_is_dc`, `efn_assortment_policy`, dan `efn_capability_ids` (`stock.py:16-33`) tidak punya `groups=`
  dan tidak ada guard di `write`. Hak tulis `stock.warehouse` di CE = Inventory Administrator. File
  `data/efn_audit_rule_data.xml:2-3` sengaja tidak memasang aturan audit untuk `stock.warehouse`, dan `efn_setup_audit` juga
  tidak punya aturan itu. ACL `efn.assortment.capability` yang dibatasi ke Assortment Manager hanya melindungi master
  capability, bukan penautannya ke gudang.
- **Skenario.** Inventory Administrator tanpa grup Assortment Manager menambahkan capability `alcohol_license` ke outlet
  yang tidak punya izin. `_efn_assortment_allowed` lalu mengembalikan `ok` untuk bir: transfer terkonfirmasi tanpa
  override, laporan pelanggaran harian juga bersih karena memakai keputusan yang sama, dan tidak ada jejak audit. Aturan
  "override alkohol/non-halal tidak tersedia" (L-150, Q-SM22) bisa dilewati diam-diam. Sama halnya: hapus centang
  `efn_is_dc` untuk mematikan penegakan bagi seluruh transfer dari DC itu.
- **Rekomendasi.** (a) Pasang `groups="efn_assortment.group_assortment_manager"` pada ketiga field di definisi model,
  plus override `stock.warehouse.write` yang menolak perubahan kunci tersebut bila pemanggil bukan Assortment Manager
  (perlindungan RPC, bukan hanya view). (b) Audit: override `write` yang mencatat nilai lama/baru ke `efn.audit.log`, atau
  aturan `efn.audit.rule` untuk `stock.warehouse` dengan `tracked_field_ids` terbatas (koordinasikan keunikan per model
  dengan efn_setup_audit). (c) Untuk capability terlindungi, pertimbangkan 4-eyes atau bukti izin (nomor dan masa berlaku)
  sebagai syarat penautan (keputusan Legal, Q-SM22). (d) `product.template.efn_required_capability_ids` sudah teraudit
  (aturan produk efn_setup_audit, semua field), tetapi bisa ditulis siapa pun yang punya hak tulis produk. Batasi juga ke
  Assortment Manager.

### SEC-209 (Low, PLAUSIBLE) — Bypass penegakan lewat transfer dua langkah manual

- **Bukti.** `_efn_assortment_violations` hanya memeriksa move yang **sumbernya** gudang DC. Gudang tujuan diambil dari
  `location_dest_id`, `location_final_id`, atau `move_dest_ids` (`stock.py:49-60`). Picking manual DC/Stock -> lokasi
  transit (tanpa `warehouse_id`, tanpa move tujuan berantai) tidak punya target, dan picking kedua transit -> outlet
  bersumber lokasi tanpa gudang, sehingga dilewati.
- **Skenario.** Stock user membuat dua transfer internal manual untuk produk `denied` atau ber-capability alkohol. Tidak
  ada blokir. Stok baru terlihat di laporan pelanggaran keesokan harinya (detektif, bukan preventif).
- **Rekomendasi.** Tegakkan juga di sisi tujuan: move masuk ke gudang non-DC yang sumbernya lokasi `transit` (atau gudang
  lain di PT yang sama) dicek terhadap assortment gudang tujuan. Alternatif: batasi pembuatan move DC -> transit tanpa rute
  resupply. Tambahkan test dua langkah. Status PLAUSIBLE karena `warehouse_id` lokasi transit CE 19 belum diuji runtime.

### SEC-210 (Low) — `qty_approved` kepala toko tanpa batas atas dan tanpa cek finite

- **Bukti.** `efn_replenishment.py:631-633` hanya menolak nilai negatif atau non-angka. Parser JSON Odoo
  (`json.loads`) menerima `Infinity`/`NaN`, dan `inf < 0` bernilai False. Cap rak/umur simpan hanya berlaku untuk
  `qty_proposed`.
- **Skenario.** Kepala toko mengisi `qty_approved = 1e9` dengan alasan `other`, lalu menyetujui. `qty_to_order_manual`
  orderpoint outlet menjadi 1e9 dan `action_replenish` membuat picking DC yang mereservasi seluruh stok DC, sehingga
  outlet lain kekurangan barang. Nilai `inf` memicu galat di procurement. Log bias mencatatnya, tetapi hanya sebagai
  kontrol detektif.
- **Rekomendasi.** Wajibkan `math.isfinite(new_qty)` dan plafon, mis. `<= max(qty_proposed x faktor, qty_rack_cap)` atau
  ambang yang di atasnya butuh persetujuan buyer. Kode alasan baru `over_cap` dicatat di bias log.

### SEC-211 (Low) — Payload worker tanpa plafon numerik

- **Bukti.** `forecast_api.is_number` hanya menuntut finite dan `>= 0`. `yhat/p10/p90/sigma`, `min_qty/max_qty`, dan
  `basis_daily_demand` tidak punya batas atas. `yhat` dipakai otomatis oleh `_efn_compute_dc_orderpoints`
  (`stock_purchase.py:107-117`) untuk menulis `qty_to_order_manual` DC.
- **Skenario.** Bug model (ledakan AutoARIMA) atau API key bot yang bocor dan dipakai lewat sertifikat `forecast-worker`
  mengirim `yhat = 1e12`. Akibatnya qty orderpoint DC sangat besar, usulan outlet menggelembung (dibatasi cap hanya bila
  produk punya ED/kapasitas rak), dan RFQ draft raksasa. Buyer masih menjadi gerbang manusia, sehingga dampaknya
  operasional, bukan finansial langsung.
- **Rekomendasi.** Plafon kontrak §28.2 (mis. `<= 1e7` per hari, `max_qty <= 1e8`), kode tolak per baris `out_of_range`,
  dan sanity check relatif terhadap histori (mis. `yhat <= 50 x p90 histori`) di worker sebelum kirim.

### SEC-212 (Low) — Guard tulis `efn.demand.forecast` belum ada

- **Bukti.** ACL `stock.group_stock_user` punya hak write=1 (`ir.model.access.csv:2`). Field `state`, `source`,
  `forecast_run_id`, dan `approved_*` tidak dijaga `write()` (atribut `readonly` hanya berlaku di UI).
- **Skenario.** Stock user mengubah lewat RPC usulan v2 berstatus `shadow` untuk gudang engine v1 menjadi `draft`.
  Planner yang menekan "Setujui & Terapkan" secara massal lalu menerapkan angka v2 ke gudang yang belum lulus backtest
  (melanggar L-148). Stock user juga bisa mengubah `approved_min/max` milik draft sebelum planner melihatnya.
- **Rekomendasi.** Override `write` dengan pola `efn.replenishment.proposal`: non-su hanya boleh `approved_min/max`
  (dan hanya Inventory Administrator), sedangkan `state`/`source`/`forecast_run_id` hanya berubah lewat tombol atau sudo.

### SEC-213 (Low, PLAUSIBLE) — SoD PO di jalur `button_approve`

- **Bukti.** Cek `GROUP_STORE` hanya ada di `purchase.order.button_confirm`. Bila company memakai double validation
  (`po_double_validation`), PO berstatus `to approve` dilanjutkan oleh `button_approve`, yang tidak memeriksa SoD.
- **Skenario.** Kepala toko yang juga punya hak Purchase Manager menyetujui PO terkelola yang sebelumnya dikonfirmasi
  buyer lain.
- **Rekomendasi.** Pindahkan cek ke helper yang dipanggil dari `button_confirm` dan `button_approve`, lalu tambahkan
  test untuk kedua jalur.

### SEC-214 (Low) — Dependency transitif forecast-worker belum di-lock

- **Bukti.** `requirements.txt` mem-pin paket utama, tetapi dependency transitif statsforecast di-resolve bebas oleh pip
  saat build. Header file menyebut lisensi belum diverifikasi ulang terhadap repo primer.
- **Rekomendasi.** Lock penuh (`pip-compile --generate-hashes`, `pip install --require-hashes`), lalu verifikasi
  lisensi transitif (pastikan tidak ada GPL di image) dan catat hasilnya di README.

### SEC-215 (Info) — Hardening tambahan

- `efn_forecast_reader` dibuat dengan `HOST ANY`, sama dengan user ClickHouse lain. Kontrol utamanya isolasi network
  `forecast-ch`. Sebagai pertahanan berlapis: `HOST IP '172.30.11.0/24'`.
- `GRANT SELECT ON efn_forecast.* TO forecast_reader` otomatis mencakup objek yang kelak ditambahkan ke database itu.
  Grant per view lebih tahan terhadap kesalahan di masa depan.
- Belum ada alert untuk run `failed`/stale (6 jam) maupun untuk masa berlaku API key bot (`api_key_duration` 35 hari,
  rotasi manual). Key yang kedaluwarsa membuat forecast berhenti diam-diam dan replenishment kembali ke reorder rule
  (L-147). Usul: metrik/alert dari log `EFN_FORECAST_RUN` (status failed) dan pengingat rotasi.

## Kontrol yang sudah benar (untuk manajemen)

- **API bot `/json/2`** (`forecast_api.check_caller`): wajib grup `group_forecast_bot`, user aktif dan non-share, tepat
  satu company. Grup tambahan ditolak (fail closed, dicek terhadap `all_group_ids`). Context klien dibuang dan
  `allowed_company_ids` dipaksa. `company_code` atau run milik PT lain ditolak dengan 403 `company_mismatch`. Galat berupa
  kode tetap tanpa detail internal. Batas 500 item, 512 KB, horizon <= 120, chunk <= 10.000, angka finite, regex
  UUID/tanggal/kode. Idempoten `run_id` dan `(run_id, chunk_no)` dengan `SELECT ... FOR UPDATE`. Gudang dan produk
  dibatasi ke company pemanggil (`_efn_scope`). Kalender manual tidak pernah ditimpa. Masa berlaku API key dibatasi
  35 hari.
- **odoo-rpc-gateway**: mTLS wajib. ACL per CN `-m str` dengan allowlist path terpisah, sehingga CN api-gateway tidak bisa
  memanggil path forecast dan sebaliknya (akses api-gateway tidak melebar). Path dicocokkan mentah, query/encoded/header
  asing ditolak, body forecast <= 512 KiB, backend `odoo_forecast` maxconn 1 dengan antrean 4 tidak memakan slot kanal
  online, dan `X-Odoo-Database` dipaksa. Traefik memblok `/json/2` dari jalur staf, jadi API key bot tidak bisa dipakai
  di luar gateway.
- **forecast-worker**: TLS verify-full dengan CA internal ke ClickHouse dan gateway, sertifikat klien mTLS wajib untuk
  https, plaintext hanya untuk dev (`EFN_ALLOW_PLAINTEXT` + APP_ENV non-prod; APP_ENV kosong = production). Secret hanya
  dari `/run/secrets`, placeholder `disabled` ditolak. API key tidak masuk `repr`/log, dan log hanya memuat metode,
  status, dan kode (tanpa body maupun pesan exception mentah). `trust_env=False`, tanpa redirect, respons dibatasi 1 MiB.
  Retry hanya untuk transport/429/5xx, maksimal 6 kali dengan backoff + jitter. Kill switch `FORECAST_ENABLED=false`
  default (mode idle tanpa secret). Dockerfile pinned digest, USER 65534.
- **Compose**: `read_only`, `cap_drop: ALL`, `no-new-privileges`, tmpfs dibatasi, batas memori/CPU. Network `forecast-ch`
  dan `forecast-rpc` internal dan terpisah (worker tanpa akses PostgreSQL/RabbitMQ/internet), semuanya ditegakkan
  `check-encryption.sh` (mount secret, network, CN haproxy, kill switch prod).
- **ClickHouse**: `readonly=2 CONST`, `allow_ddl=0`, introspection off, kuota/batas memori, role tanpa hak di
  `efn_dwh`/`efn_esb`, view DEFINER (definer `HOST NONE`) dengan kolom eksplisit tanpa partner/member/lot/nama produk.
  Plaintext port 8123/9000 dimatikan.
- **ETL/grants**: grant per kolom, tanpa `create_uid/write_uid`, bersyarat. Mart tanpa PII dengan TTL 3 tahun.
- **efn_assortment**: satu fungsi keputusan, enforce fail-closed (hanya `false`/`0` yang mematikan), override append-only
  dengan alasan >= 10 karakter yang teraudit, capability terlindungi tidak bisa di-override (dicek di constraint model dan
  wizard), record rule multi-company di semua model.
- **efn_replenishment**: record rule outlet kepala toko (`efn_outlet_warehouse_ids`, hanya bisa diubah Access Rights),
  cek cakupan dan cut-off di server, guard `create/write/unlink` baris (sudo saja), bias log append-only, perhitungan
  lewat cron (tidak di request RPC), dan `efn_replenishment.enabled=false` default.
- **efn_setup_supermarket / efn_core**: identitas jelas fiktif (NPWP nol, alamat placeholder), tanpa secret,
  `vat_check_vies=False` (tanpa egress). Postgres `19-platform-g44.sql` transaksional, idempoten, dengan pagar merge G43.
- **CI**: overlay forecast ikut `docker compose config`, sinkron dengan `secrets.list`, pip-audit, EKU klien
  diverifikasi, dan image ikut matriks build/sign.

## Keputusan merge (sudut pandang security)

Tidak ada pemblokir. SEC-207 dan SEC-208 **disarankan diperbaiki di PR ini atau menjadi syarat sebelum
`efn_assortment.enforce`/pilot SMD diaktifkan di production**. Sisanya bisa masuk backlog G45.
