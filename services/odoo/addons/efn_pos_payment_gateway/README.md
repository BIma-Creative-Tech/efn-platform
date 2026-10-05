# efn_pos_payment_gateway

Metode pembayaran POS **`efn_gateway` (QRIS)** yang tidak terikat provider: Odoo hanya berbicara ke BFF EFN (`docs/04 §8.3`); provider (mock/xendit/midtrans) dipilih di BFF.

- **Fit-gap:** F-30 / E-15 (`docs/08 §4.1`). **ADR:** ADR-016 (provider, *Proposed*), ADR-019 (merchant of record, *Proposed*), ADR-022 (TLS).
- **Risiko dimitigasi:** fraud "QR sudah dibayar" palsu dari kasir/browser (status lunas hanya dari server), pemakaian ulang satu pembayaran untuk banyak order, secret provider di DB Odoo (E-13 gugur: tidak ada API key di Odoo), POS offline (metode nonaktif), PAN/kartu (tidak pernah disentuh).
- **Enkripsi in transit:** semua panggilan BFF lewat `efn.bff.client` (`post`/`get`, HTTPS + CA internal `EFN_CA_FILE`, service key dari file secret).

## Konfigurasi
1. *Point of Sale → Configuration → Payment Methods → New*: Integration **Terminal**, *Use a Payment Terminal* = **EFN Payment Gateway (QRIS)**, jurnal **Bank** PT tsb (atau jurnal clearing sesuai ADR-019), *Identify Customer* mati. Constraint menolak konfigurasi lain dan company tanpa *Kode Company EFN*.
2. Tambahkan metode ke `pos.config` outlet. Jangan konfigurasikan QRIS CE `id_qr` (`efn_setup_pos` sengaja tidak).
3. Parameter: `efn.pos.gateway_max_amount` (default 20 000 000), `efn.pos.gateway_currency` (default `IDR`; hanya diubah untuk DB uji non-IDR), `efn.bff_url`/env `EFN_BFF_URL`, `efn.pos.otp_timeout_seconds` (timeout HTTP bersama).

## Alur
1. Kasir memilih QRIS → JS `PaymentEfnGateway.sendPaymentRequest`: **offline → menolak** ("QRIS tidak tersedia saat POS offline").
2. RPC `pos.payment.method.efn_gateway_create(order_uuid, payment_uuid, amount, pos_config_id)` (cek `group_pos_user` → `AccessDenied`, metode milik config, sesi terbuka, nominal > 0 dan ≤ batas, mata uang) → `POST /v1/pos/payments` `{order_ref, amount, currency, company_code, pos_config_id, method:"qris", idempotency_key:"pos:<order_uuid>:<payment_uuid>"}`. Intent disimpan di `efn.pos.payment.intent`. Panggilan ulang untuk baris yang sama mengembalikan intent yang ada (idempoten, tanpa BFF); setelah intent dibatalkan/kedaluwarsa, percobaan baru memakai key `…:<n>`.
3. QR tampil di kasir (`QRPopup` standar, gambar PNG dirender server via `ir.actions.report.barcode`) dan di layar pelanggan (`order.qrPaymentData`). Tombol "Sembunyikan QR" **tidak** mengonfirmasi pembayaran.
4. Polling `efn_gateway_status(intent_id)` tiap **2 s** → `GET /v1/pos/payments/:id` sampai `paid`/final atau `expires_at` (+5 s). `paid` → `line.transaction_id = payment_id` → baris selesai. Nominal `paid` dari BFF ≠ intent → intent `failed` (`amount_mismatch`).
5. Batal (tombol baris pembayaran atau popup) → `efn_gateway_cancel` → `POST /v1/pos/payments/:id/cancel`. Bila ternyata sudah `paid`, pembatalan ditolak dan polling menyelesaikan baris.
6. **Validasi server saat sync** (`pos.order._process_payment_lines`, order non-draft): setiap `pos.payment` `efn_gateway` wajib punya `transaction_id` intent `paid` (refresh `GET` bila masih pending), order (`uuid`), metode, company, baris (`pos.payment.uuid` bila ada) dan nominal sama, belum dipakai order lain, tidak dipakai dua baris; nominal ≤ 0 ditolak. Gagal → `UserError`, **seluruh sync ditolak** (order tetap di antrean POS). Sukses → intent ditautkan ke `pos.payment`/`pos.order`.
7. Tanpa reversal (`supports_reversals = false`): refund QRIS fase 1 lewat metode lain sesuai SOP (`docs/04 §8.8`; endpoint refund dicadangkan).

## Rekonsiliasi
Cron harian *EFN Gateway: rekonsiliasi intent QRIS POS*: intent `pending` lewat `expires_at` (+10 menit) → refresh lalu cancel (BFF tak terjangkau → dicoba lagi besok; bila status tetap tak jelas tetapi BFF menjawab pending, ditandai `expired` lokal); intent `paid` tanpa `pos.payment` > 60 menit (**yatim**: pelanggan membayar tetapi order tak tersinkron) → log WARNING (alert Loki) + aktivitas To-Do ke user *Accounting / Administrator* pertama di company, sekali saja. Menu *Point of Sale → Orders → QRIS Gateway (EFN)* (POS manager, akuntan), filter "Lunas tanpa order". Perbandingan dengan laporan settlement provider dilakukan di BFF, bukan Odoo. Penutupan sesi & jurnal POS standar.

## Keamanan & data
- ACL: POS user & POS manager & akuntan **read**; tulis hanya lewat RPC/cron (sudo terkontrol) dan admin. Record rule multi-company.
- Tidak ada data kartu; `card_no`/`cardholder_name` tidak diisi; `qr_string` dikosongkan setelah final.
- Log hanya `order uuid`, id intent, kode error — tidak ada payload/QR.

## Uji manual
1. BFF `PAYMENT_PROVIDER=mock`, buat metode seperti di atas, buka sesi POS.
2. Bayar dengan QRIS → QR muncul → tandai paid di BFF mock → baris selesai dalam ~2 s → validasi order → intent `paid` tertaut order.
3. DevTools offline → pilih QRIS → ditolak dengan notifikasi.
4. Batalkan QR → intent `cancelled`; pilih QRIS lagi pada baris yang sama → intent baru (key `:2`).
5. Ubah `transaction_id` baris di payload (klien dimodifikasi) → sync ditolak.

## Test otomatis
`tests/test_payment_gateway.py` (TestPoSCommon, tag `efn`, BFF di-mock): payload kontrak + idempotensi create, nominal/BFF down, non-POS user `AccessDenied`, metode bukan milik config, status `paid` (transaction_id, QR dikosongkan), `paid` dengan nominal beda → `failed`, cancel → retry key baru, balapan cancel vs paid, sync sukses + tautan intent, sync ditolak untuk intent pending / BFF down / nominal beda / intent dipakai ulang / tanpa intent (skenario offline), cron rekonsiliasi (kedaluwarsa + yatim sekali), record rule multi-company, kasir tidak bisa menulis intent, constraint jurnal bank.

## Batasan / belum terverifikasi
- Belum pernah di-install/dijalankan. **Tidak ada test JS** (QUnit/Hoot/tour) — perilaku offline/polling/QR frontend hanya diuji manual.
- Bentuk `order.qrPaymentData` untuk layar pelanggan 19.0 **belum terverifikasi** (template `customer_display.xml` 19.0 tidak merujuknya); kode menulis `{name, amount, qrCode}` dalam `try`. Bila layar pelanggan tidak menampilkannya, QR tetap tampil di popup kasir.
- Props `QRPopup` 19.0 terverifikasi (`line`, `order`, `qrCode`, + props `ConfirmationDialog`); prop `dismiss` dan nilai kembali `dialog.add` (fungsi tutup) diasumsikan seperti 17/18.
- Alur payment screen 19.0 (status `waitingCard`, `pay()` → `retry/done`) diasumsikan seperti `pos_adyen` 19.0.
- `ir.actions.report.barcode("QR", value, width, height)` diasumsikan tersedia (gagal → tanpa gambar, notifikasi).
- Mata uang produksi dipaksa `IDR` (parameter hanya untuk DB uji). Settlement per PT/ADR-019 belum diimplementasi (BFF).
