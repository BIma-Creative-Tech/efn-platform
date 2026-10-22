# efn_online_slot (W-36 / G23) — pre-order dengan jadwal ambil

> **Ringkasan manajemen.** Pelanggan bisa memesan kue/hampers sekarang dan memilih **tanggal + jam ambil** di
> gerai. Setiap gerai menentukan sendiri jam ambil, panjang slot (30/60 menit), jumlah pesanan per slot, dan berapa
> jam sebelumnya pesanan harus masuk. Jadwal disimpan di field standar Odoo `commitment_date` pada pesanan
> penjualan, jadi tampil di laporan dan picking tanpa modul tambahan. Gerai yang tidak mengaktifkan fitur ini
> **berperilaku persis seperti sebelumnya**. Fitur **mati di produksi** sampai diaktifkan (`ONLINE_SLOTS_ENABLED=false`).

- Versi `19.0.1.0.0`, LGPL-3. `depends`: `efn_online_order` (≥ 19.0.1.8.0, kode galat `slot_unavailable`).
  Tanpa modul pihak ketiga (ADR-031).
- Kontrak: `docs/04_SERVICE_CONTRACTS.md` §15.4, §15.9 (L-87). Rencana: `docs/10` §A.8 (W-36).

## Field baru di `efn.online.outlet`
| Field | Default | Arti |
| --- | --- | --- |
| `slot_enabled` | `False` | Pre-order berjadwal aktif di gerai ini. |
| `slot_minutes` | `60` | Panjang slot (30/60 menit). |
| `slot_capacity` | `10` | Pesanan maksimal per slot (≥ 1). |
| `slot_lead_minutes` | `240` | Minimal menit sebelum jam ambil. |
| `slot_horizon_days` | `7` | Maks hari ke depan (1–30). |
| `slot_calendar_id` | kosong | Jam ambil; kosong = `resource_calendar_id` outlet (salah satunya wajib bila aktif). |

`efn.online.order.pickup_slot` (Datetime UTC, readonly) menyimpan jadwal; `sale.order.commitment_date` diisi
nilai yang sama. Model tidak baru, sehingga `company_id`/record rule mengikuti model induk.

## RPC & perubahan kontrak
- **#20** `efn.online.channel/efn_online_slots(channel_code, outlet_code, date)` — slot satu hari lokal:
  `{outlet_code, date, timezone, slot_minutes, slots: [{start_at, end_at, remaining}]}`. Slot sejajar kelipatan
  `slot_minutes`, seluruhnya di dalam jam ambil, ≥ `slot_lead_minutes` dari sekarang, maks 48 per hari. Pemanggil
  dicek `_efn_online_check_caller` (user teknis kanal online, sama seperti RPC online lain).
- `efn_online_quote(..., pickup_slot=None)` dan `efn_online_place(order{..., pickup_slot})`:
  - **Tanpa `pickup_slot`** → perilaku dan `quote_hash` identik dengan sebelumnya.
  - **Dengan slot** (hanya `fulfilment="pickup"`): quote menambah issue `slot_unavailable` bila slot tidak sah/penuh;
    `quote_hash` = sha256 `{q: hash dasar, pickup_slot}` (penanda lewat `efn_core.scope`, bukan context klien).
    Place mengunci baris outlet (`FOR UPDATE` + `touch_row`, SEC-127), menghitung kapasitas (pesanan selain
    `cancelled`), lalu menyimpan slot; penuh → `UserError("efn_online:slot_unavailable")`. Idempoten: kunci sama +
    slot berbeda → `idempotency_conflict`.
- Blok kanal (`efn_online_channels`): bila ada outlet ber-slot, tiap outlet mendapat `slot_enabled`/`slot_minutes`
  dan kanal `fulfilment.scheduled`. Kanal tanpa outlet ber-slot → blok **identik** dengan sebelumnya.

## Tampilan
Form `efn.online.outlet`: field di atas ditambahkan lewat `views/efn_online_outlet_views.xml`.

## Uji
`tests/test_online_slot.py` (tag `efn`): pembentukan slot & zona waktu, jendela waktu, kapasitas, quote_hash dengan/
tanpa slot, place + `commitment_date`, idempotensi, blok kanal tidak berubah tanpa slot. Jalankan
`odoo --test-tags /efn_online_slot` (G23: 249/249 lulus bersama `efn_core`, `efn_online_order`, `efn_table_booking`).
