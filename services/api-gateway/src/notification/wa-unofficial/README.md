# `src/notification/wa-unofficial` — sisi BFF WhatsApp tidak resmi (G50-B)

**ADR terkait:** ADR-044 (**Proposed**, membatalkan sebagian ADR-011), K18-01 di `CLAUDE.md`, ADR-047 (consent &
kampanye). Kontrak: `docs/04` §28.7, §28.9, §28.10, L-158/L-159. **Status:** dibangun G50, belum diuji end-to-end.
`WA_UNOFFICIAL_ENABLED=false` default; **OFF di production** sampai `WA_UNOFFICIAL_RISK_ACCEPTANCE_REF` (Q-SM14) +
DPO (Q-SM18).

## Batas tegas
- Hanya kategori `promo`, `notify_restock`, `price_alert`. OTP, pesanan, pembayaran, poin, MGM, reservasi tetap
  **WhatsApp Cloud API resmi** (`src/otp/whatsapp`, `notify.push`). Modul ini tidak memuat Baileys; pengiriman ada di
  container terisolasi `services/wa-unofficial`.
- Flag mati → **tidak publish apa pun** (dilewati, bukan diantre). Kanal `banned`/`disconnected` → dilewati sementara,
  **tidak** dialihkan ke Cloud API.

## Komponen
| File | Peran |
|---|---|
| `wa-unofficial.module.ts` | `WaUnofficialModule` (didaftarkan BFF-A), ekspor `WaUnofficialNotifier` |
| `wa-unofficial.notifier.ts` | `enqueue(...)` → `'queued' \| 'skipped_disabled' \| 'skipped_no_consent' \| 'skipped_no_phone'` |
| `wa-unofficial.publisher.ts` | publish `notify.wa_unofficial` dengan properti AMQP `expiration` (koneksi sendiri, lihat catatan) |
| `wa-unofficial-result.consumer.ts` | consumer `notify.wa_unofficial.result` (status, STOP → cabut consent) |
| `wa-unofficial.contract.ts` | kategori, template, `to_hash`, kunci idempotensi (salinan di service) |
| `wa-unofficial.config.ts` | flag + gerbang production + kunci hash |

## Alur
1. Gerbang `enqueue`: flag persis `true` (+ ref di production + kunci hash) → kanal tidak ditandai down → member aktif →
   consent `marketing` (semua kategori, default Q-SM25) → nomor = `member_ref` (terverifikasi OTP saat login; nomor
   Indonesia) dengan cek integritas `member_hash`.
2. Satu transaksi: upsert `wa_unofficial_recipient(to_hash → member)` + `wa_unofficial_outbound` (`ON CONFLICT DO
   NOTHING`) + publish confirm. Publish gagal → rollback. Payload §28.7: `to_e164` (digit tanpa `+`), `to_hash =
   HMAC-SHA256(wa_unofficial_hash_key, to_e164)`, `expires_at`; `expiration` AMQP = sisa waktu (≤ 24 jam).
   **Tidak lewat outbox PostgreSQL** (payload memuat nomor plaintext).
3. Hasil (`efn-wa-unofficial`): `result` → status outbound (hanya dari `queued`/`disconnected`); `banned` → penanda Redis
   `waun:down` 1 jam, `disconnected` 10 menit; `opt_out` → cabut consent `marketing` (source `wa_stop`).

## Kontrak antarmuka untuk BFF-A
```ts
enqueue(input: { memberId: string; category: 'promo'|'notify_restock'|'price_alert'; source: string;
  sourceRef: string; template: string; params: Record<string,string|number>; expiresAt: Date })
  : Promise<'queued'|'skipped_disabled'|'skipped_no_consent'|'skipped_no_phone'>
```
Template: `promo_generic` (promo; `title`,`body`, opsional `valid_to`,`voucher_code`), `markdown_near_expiry` (promo;
`product_name`,`outlet_name`, opsional `valid_to`), `restock_available` (notify_restock; `product_name`,`outlet_name`),
`price_drop` (price_alert; `product_name`,`new_price_idr`). `source` `^[a-z_]{1,32}$`, `sourceRef`
`^[A-Za-z0-9:_.-]{1,128}$`. Kesalahan pemanggil (kategori/template/param/`expiresAt` lewat) → `WaContractError`.

## Risiko
R-B16 (ban: kanal dilewati, transaksi tidak terdampak), R-S21 (Baileys tidak di BFF), R-L01 (nomor hanya di
`notify.wa_unofficial`, log mask `62812****789`, tabel hanya `to_hash`), R-B17 (consent + STOP + batas teknis di service).

## Belum / keterbatasan
- `AmqpService.publish` tidak menerima opsi `expiration` → publisher memakai koneksi AMQP kedua (user `efn-bff`, TLS
  sama). Usulan: tambah parameter opsi di `AmqpService` lalu ganti `AmqpWaPublisher` dengan adaptor.
- Queue baru tidak ada di passive check `AmqpService` → consumer hanya dipasang saat kanal aktif; queue wajib sudah
  dideklarasikan broker sebelum flag dinyalakan (bila tidak, `consume` gagal dan koneksi BFF reconnect berulang).
- Consent per purpose: STOP di WA mencabut `marketing` untuk semua kanal promo (email & push juga).
- Production tanpa ref: BFF tidak gagal start, kanal mati + log error; penolakan deploy = `deploy-validate`.
- Tabel baru belum ikut DSAR export (`src/privacy`, file milik bersama).
