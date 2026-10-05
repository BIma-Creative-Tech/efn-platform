# notification — consumer `notify.push` v2

Kontrak: `docs/04` §4 (v2) & §7.2. Mengirim template WhatsApp (Cloud API resmi, D-11; HTTPS publik dengan verifikasi sertifikat default Node) via `WHATSAPP_SENDER`; mode console hanya log ter-mask.

| `template` | Template WA | Param body (urutan dari `params`) |
|---|---|---|
| `price_hunter_valid` | `efn_price_hunter_valid` | `product_name`, `points` |
| `price_hunter_rejected` | `efn_price_hunter_rejected` | `product_name`, `reason` |

- Payload v2: `{ schema_version: 2, idempotency_key, member_hash, source: "price_hunter", source_ref: <report_id>, template, params }` — tanpa nomor HP.
- **Resolve penerima**: `price_hunter_report(id = source_ref)` → `user_hash` harus = `member_hash` → dekripsi `user_enc` → verifikasi `member_hash(nomor)` (`common/pii/price-hunter-subject.ts`). Gagal → DLQ.
- Publisher (properti AMQP `user_id`) harus `efn-ocr` atau `efn-bff` untuk `price_hunter`.
- Template dicek dengan `hasOwnProperty` (bukan `in`, catatan security review).
- Param hilang → `-`; baris baru dibuang; dipotong 100 karakter.
- Idempotensi: klaim `processed_message('bff.notify-push', key)` + kirim WA dalam satu transaksi; gagal kirim → rollback → requeue sekali → DLQ.

## Belum
- Push app (FCM), preferensi/opt-out (consent UU PDP), pemetaan `reason` teknis → kalimat pelanggan.
