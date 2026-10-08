# Runbook: kegagalan tiket Kitchen Display (`EfnKds*`)

Gelombang 15b (9 Okt 2026), SEC-76 (review keamanan r8), modul `efn_pos_kds` (E-26). Audiens: on-call IT / ops outlet,
kepala dapur/supervisor outlet.

## Apa yang terjadi

Saat POS menyinkronkan order, Odoo membuat tiket KDS per stasiun dapur. Bila pembuatan tiket gagal, Odoo **tidak**
menggagalkan order (KDS di luar jalur kas, savepoint). Sebagai gantinya:

1. Odoo menulis satu baris log ERROR tanpa PII di logger khusus `odoo.addons.efn_pos_kds.alert`:
   `EFN_KDS_TICKET_FAILED {"company_id": 1, "config_id": 3, "error": "ValueError", "event": "EFN_KDS_TICKET_FAILED", "kds_only": true, "order_id": 4501, "session_id": 12, "stations": [{"id": 1, "mode": "kds"}]}`.
   `error` hanya nama tipe exception. Pesan dan traceback hanya ditulis di level DEBUG, karena bisa memuat catatan dapur.
2. Order ditandai `pos.order.efn_kds_ticket_failed = True`. POS kasir membaca tanda itu, mencetak perubahan ke
   **printer cadangan** stasiun mode `kds`, dan menampilkan notifikasi *sticky* ke kasir.

Jalur: log Odoo → Promtail (`infra/observability/promtail.yml` tahap (2b): regex berjangkar penuh, skema ketat, hanya
`service=odoo`) → Loki stream `{stream="efn_kds", kds_event="ticket_failed"}` (label `kds_only`, retensi default 30 hari)
→ ruler (`infra/observability/loki-rules/efn-kds.yml`) → Alertmanager (mTLS).

`kds_only=true` berarti minimal satu stasiun order ini bermode `kds` (layar; printer hanya cadangan). Risikonya paling
tinggi: tanpa printer cadangan yang berfungsi, dapur tidak melihat pesanan.

Query dasar (Grafana → Explore → Loki):

```logql
{stream="efn_kds"} | regexp `EFN_KDS_TICKET_FAILED (?P<payload>[{].*[}])` | line_format "{{.payload}}" | json
sum by (company_id, config_id, error) (count_over_time({stream="efn_kds"} | regexp `EFN_KDS_TICKET_FAILED (?P<payload>[{].*[}])` | line_format "{{.payload}}" | json [24h]))
```

## EfnKdsTicketFailed

**Warning**: minimal 1 kegagalan dalam 5 menit, dikelompokkan per `company_id`, `config_id`, dan `kds_only`.

1. **Outlet dulu (≤ 5 menit).** Hubungi outlet (company/config POS di label alert, cocokkan dengan register POS). Pastikan
   dapur menerima pesanan:
   - printer cadangan mencetak, atau
   - kasir membacakan/menulis ulang pesanan.

   Order id ada di payload (`order_id`). Kasir bisa mencarinya di POS → Orders.
2. Bila hanya satu atau dua kejadian dengan `error` yang sama, kemungkinan data order tertentu, mis. produk tanpa stasiun
   atau kategori yang baru diubah. Catat `order_id` + `error` dan teruskan ke tim Odoo. Sinkron berikutnya untuk order
   yang sama mencoba lagi, dan tanda kegagalan dikosongkan bila berhasil.
3. Daftar order yang masih bertanda gagal: Odoo → Point of Sale → Orders, filter `efn_kds_ticket_failed` = set (perlu
   hak POS manager). Order yang tetap bertanda setelah shift ditutup sudah dilayani lewat printer/manual. Catat di
   laporan shift.
4. Detail teknis: hanya di staging/dev, aktifkan sementara level DEBUG untuk logger
   `odoo.addons.efn_pos_kds.models.pos_order` agar traceback terlihat. Di production **jangan** aktifkan DEBUG tanpa
   persetujuan, karena traceback bisa memuat teks catatan dapur.

## EfnKdsTicketFailedSpike

**Critical**: ≥ 10 kegagalan dalam 15 menit di satu company. Ambang ini PLACEHOLDER dan akan dikalibrasi saat pilot.
Kegagalan bersifat sistemik.

1. Umumkan ke outlet company tersebut: pakai printer dapur. Stasiun mode `kds` sementara bisa diubah ke `kds_printer`
   oleh POS manager. Bila printer juga bermasalah, jalankan prosedur pesanan manual.
2. Periksa perubahan terakhir:
   - deploy atau upgrade `efn_pos_kds` / `efn_setup_pos`;
   - perubahan konfigurasi stasiun, kategori, atau `pos.config`;
   - migrasi.

   Gunakan `git log` rilis dan jejak `efn-deploy` di Loki (`{service="host-efn-deploy"}`).
3. Periksa kesehatan Odoo dan PostgreSQL (lock, deadlock, constraint) di [odoo-http-5xx.md](odoo-http-5xx.md) dan
   [service-down.md](service-down.md). `error` = `IntegrityError`/`UniqueViolation` berulang → eskalasi ke tim Odoo.
   Bentrok nomor tiket yang biasa sudah di-retry otomatis (`ConcurrencyError`) dan tidak muncul di sini.
4. Rollback rilis bila kegagalan dimulai tepat setelah deploy ([dev-stack-boot.md](dev-stack-boot.md) untuk upgrade
   modul di dev; prod mengikuti prosedur rilis `efn-deploy`).

## EfnKdsAlertFormatUnrecognized

**Warning**: logger `odoo.addons.efn_pos_kds.alert` menulis baris `EFN_KDS_TICKET_FAILED` yang **tidak** cocok dengan regex
Promtail (2b). Akibatnya baris itu tidak masuk stream `efn_kds`, dan dua alert di atas tidak melihatnya. Penyebab:

- format payload Odoo berubah (kunci baru, urutan, separator), atau
- field `error` memuat teks selain identifier Python. Ini juga **bug PII**: pesan exception bisa memuat nama/nomor
  pelanggan.

Langkah:

1. Anggap ada kegagalan tiket yang belum ditangani dan ikuti langkah 1 [EfnKdsTicketFailed](#efnkdsticketfailed). Cari
   barisnya:
   `{service="odoo", stream=""} |= "odoo.addons.efn_pos_kds.alert: EFN_KDS_TICKET_FAILED"`.
2. Samakan format. Jika perubahan Odoo disengaja, perbarui regex di `promtail.yml` (2b) dan fixture
   `infra/observability/tests/promtail/fixtures/odoo.txt`, lalu jalankan `scripts/test-promtail-redaction.sh --update`
   dan tinjau diff-nya. Bila tidak disengaja, perbaiki di `efn_pos_kds`.
3. Baris di luar stream `efn_kds` tetap lewat redaksi Promtail (HP/token). Bila ternyata memuat data pribadi lain,
   ikuti [log-redaction.md](log-redaction.md) §3–4.

## Batas (jujur)

- Alert hanya melihat kegagalan yang **ditangkap** Odoo, yaitu exception di pembuatan tiket. Kondisi berikut tidak
  terdeteksi di sini:
  - layar KDS mati atau websocket putus (POS sudah mencetak cadangan bila layar stasiun tidak hidup);
  - POS offline: tiket dibuat saat sinkron berikutnya, dan printer standar mencetak saat offline.
- `SerializationFailure`/lock/deadlock **naik** dan di-retry Odoo (request), jadi tidak tercatat sebagai kegagalan tiket.
  Bila gagal permanen, alert yang muncul adalah 5xx Odoo.
- Newline di argumen log Python tidak di-escape. Satu baris palsu utuh yang ditulis kode lain di container Odoo masih
  mungkin masuk stream (batas yang sama dengan sink audit). Payload dibatasi angka/enum, jadi dampaknya hanya alert
  palsu.
- Ambang spike 10/15 menit dan routing `team: ops-outlet` = PLACEHOLDER. Penerima Alertmanager saat ini satu webhook
  generik.

## Uji (dev)

G15b sudah diuji dengan container sintetis `efn-g15b-synth` berlabel `service=odoo`, berisi 11 baris valid
(company/config 999) dan 1 baris malformed. Ketiga alert aktif di Alertmanager, dan container sudah dihapus. Untuk
mengulang, ikuti pola di laporan devsecops G15b. Gunakan ID fiktif (999), jangan ID order nyata.
