# efn_online_transfer (G26 / K25-07) — transfer bank + bukti, diverifikasi staf

> **Ringkasan manajemen.** Pesanan online bisa dibayar dengan **transfer ke rekening bank PT penjual**. Pelanggan
> mengunggah foto bukti di Super-App, lalu staf ber-peran **Verifikator Pembayaran** mencocokkannya dengan mutasi bank di
> Odoo dan menekan *Setujui* atau *Tolak*. Gerai baru menyiapkan pesanan setelah disetujui. Tidak butuh vendor payment
> gateway. Verifikator tidak boleh sekaligus kasir atau petugas gudang (SoD), dan setiap keputusan tercatat di audit.
> Fitur **mati** sampai kanal diberi metode `bank_transfer` + jurnal bank, dan BFF mengaktifkannya lewat env.

- Versi `19.0.1.0.0`, LGPL-3. `depends`: `efn_online_order` (≥ 19.0.1.9.0), `efn_role`, `efn_audit`, `account`.
  Tanpa modul pihak ketiga (ADR-031).
- Kontrak: `docs/04_SERVICE_CONTRACTS.md` §17 (L-95..L-104), antarmuka §17.15.

## Konfigurasi (per kanal online)
1. Buat **Rekening Bank** milik partner perusahaan PT (bank, nomor rekening, nama pemilik) dan **jurnal Bank** yang
   memakainya (Accounting → Configuration → Journals).
2. Di **Kanal online** isi *Jurnal transfer bank* lalu tambahkan `bank_transfer` ke *Metode bayar*
   (`qris,bank_transfer`). Constraint menolak bila rekening tidak lengkap, milik partner lain, atau beda company.
3. Beri grup **Verifikator Pembayaran (transfer bank)** ke staf Finance/CS yang ditunjuk (bukan kasir/gudang).
   Aturan SoD `SOD-PAY-01` (⟂ User POS) dan `SOD-PAY-02` (⟂ User Inventory) dibuat otomatis saat install; bila saat
   itu sudah ada pelanggaran, aturan dibuat `report_only` dan Superadmin menaikkannya ke `block` setelah dibereskan.

## Alur
| Langkah | Siapa | Hasil |
|---|---|---|
| Place `payment_mode: "bank_transfer"` | BFF | SO draft + reservasi; hasil place membawa `transfer_account` (rekening PT). |
| RPC #12 `efn_online_transfer_submit` | BFF | `efn.online.transfer.proof` + foto sebagai `ir.attachment` privat. |
| Menu **Verifikasi Transfer → Bukti transfer** | Verifikator | Antrean: nomor pesanan, gerai, nominal, kode berita, rekening, batas waktu, tanda *diduga duplikat*. |
| *Setujui* (tanggal + nominal + referensi mutasi; nominal harus sama persis) | Verifikator | Alur `mark_paid` biasa → SO dikonfirmasi, faktur, pembayaran di jurnal transfer kanal, event `paid` (`method: bank_transfer`). |
| *Tolak* (alasan dari daftar tetap) | Verifikator | Event `transfer_rejected {reason}`; pesanan tetap menunggu bayar, pelanggan unggah ulang. |
| Pesanan batal/kedaluwarsa | sistem | Bukti menunggu → `expired`. Bila dana ternyata masuk: *Setujui* → `review` (`transfer_paid_after_cancel` / `_after_expiry`), Finance mengembalikan dana. |

BFF tidak bisa melunasi pesanan transfer (`efn_online_mark_paid` → `invalid_state`).

## Keamanan & data
- Model bukti: `company_id` + record rule multi-company. ACL: Verifikator baca/tulis (tanpa create/unlink), Auditor &
  Superadmin baca. User biasa, kasir, dan user teknis BFF tidak bisa membaca foto.
- Data antrean disalin ke record bukti sehingga Verifikator tidak perlu hak baca pesanan/SO. Tidak ada nama, nomor HP,
  atau data pribadi pelanggan di record ini; catatan internal Verifikator tidak pernah dikirim ke BFF.
- `efn_audit` mencatat create/write/unlink bukti (status, alasan, mutasi, pemutus, waktu). Foto (binary) dan catatan
  bebas dikecualikan.
- Validasi RPC #12: pemanggil `_efn_online_check_caller`, kunci baris order, kunci idempoten + `proof_id` unik + hash
  payload, JPEG (magic `FFD8FF`) ≤ 1,5 MiB, `sha256` = isi, satu bukti aktif per pesanan, pesanan belum kedaluwarsa.
- Gateway `odoo-rpc-gateway`: path ini menerima body s.d. 2,5 MiB; path lain tetap ≤ 256 KB.

## Retensi
Masa simpan foto di Odoo menunggu keputusan Legal/DPO + Finance (**Q-PAY4**; usulan ikut retensi dokumen akuntansi).

## Uji
`tests/test_online_transfer.py` (tag `efn`): konfigurasi kanal, `counter` beku, place + rekening, idempotensi, validasi
foto, akses Verifikator/non-Verifikator, setuju → faktur lunas di jurnal transfer, tolak → event + unggah ulang,
batal → `expired` → setuju terlambat → `review`, SoD + aturan audit.
