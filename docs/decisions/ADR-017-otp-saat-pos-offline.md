# ADR-017: Perilaku OTP-Gated Promo saat POS Offline — Fitur Server Dinonaktifkan (Opsi a)

- **Status:** Proposed
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** PO + Operasional Outlet (SOP kasir) + Finance/Risk (toleransi fraud)
- **Referensi review:** §3.2, R-T02 (skor 20), R-S04, R-B06, BRD §7.2 vs FSD-2.1
- **Terkait:** `docs/02 §3.1`, `docs/04 §3.1`, ADR-011, ADR-020

> **Ringkasan manajemen.** Saat internet outlet putus, kasir tetap bisa berjualan seperti biasa, tetapi **promo khusus member, penukaran poin, dan reward MGM tidak bisa dipakai** sampai koneksi kembali. Alasannya: verifikasi OTP butuh server dan WhatsApp; membuka promo tanpa verifikasi justru membuka celah fraud yang ingin ditutup. Kasir menampilkan pesan standar ke pelanggan. Biaya: tidak ada tambahan.

## Konteks
- BRD §7.2: promo/MGM/redeem poin wajib OTP (anti-fraud kasir).
- FSD-2.1 / Blueprint: POS **offline-first**.
- `docs/01 §3.2`: *"semua yang bergantung pada server (OTP ke Redis, kirim WhatsApp, cek saldo poin) tidak tersedia"* saat offline.
- Kontrak BFF sudah ada (`/v1/pos/otp/request|verify`, `/v1/pos/discount-token/consume`) dan parameter Odoo `efn.pos.promo_disabled_when_offline`, `efn.pos.otp_timeout_seconds` (default 5).

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu |
|---|---|---|---|
| **a. Nonaktifkan promo/MGM/redeem poin saat offline**; transaksi reguler jalan | Paling aman, sederhana, mudah diaudit | Pelanggan kecewa saat internet putus; potensi kehilangan penjualan promo | Termasuk scope `efn_pos_otp`; 0 tambahan |
| b. Soft-lock: izinkan promo dengan foto KTP/approval manager, rekonsiliasi saat online | Penjualan promo tidak hilang | Membuka celah fraud yang persis ingin ditutup (kolusi kasir-manager); beban rekonsiliasi | +2–3 minggu (flow approval, foto, rekonsiliasi) + biaya audit berulang |
| c. TOTP dari app pelanggan, diverifikasi offline oleh POS dengan shared secret per member | Bekerja offline untuk pemilik app | Secret per member harus ada di POS (risiko kebocoran massal); cek saldo poin tetap tidak mungkin offline (double-spend); hanya member ber-app | +4–6 minggu + review kriptografi; risiko keamanan tinggi |

## Keputusan
Opsi **a**. Detail perilaku:
1. POS mendeteksi offline (status koneksi Odoo POS) **atau** panggilan BFF gagal/timeout > `efn.pos.otp_timeout_seconds` → tombol "Gunakan Poin/Promo Member/MGM" dinonaktifkan dengan pesan standar.
2. Order yang sudah memegang `discount_token` dan kemudian offline: token divalidasi ulang via `/v1/pos/discount-token/consume` saat sinkronisasi; jika gagal (`expired`/`already_used`), order ditandai untuk review supervisor (tidak dibatalkan otomatis).
3. **Pencatatan poin earn** dari transaksi offline (bukan redeem) dapat diproses setelah order tersinkron bila nomor member tercatat — *perlu konfirmasi PO (lihat Q di bawah)*; default: tidak di-earn.
4. Metrik: % waktu offline per outlet, jumlah promo yang tertolak karena offline → bahan evaluasi.

## Konsekuensi
- **Positif:** tidak ada celah fraud offline; implementasi sederhana.
- **Negatif:** pengalaman pelanggan saat outlet offline; ketergantungan pada kualitas internet outlet (dorong backup 4G, Q-55).
- **Dampak dokumen:** BRD §7.2 ditambah klausul offline; FSD-2.1 ditambah pengecualian.

## Risiko terkait
R-T02 (skor 20), R-S04, R-B06, R-O06 (SOP kasir).

## Pertanyaan terbuka untuk klien
- **Q-16** apakah reward MGM berlaku untuk transaksi POS tanpa app/hanya nomor HP?
- **Q-55** internet outlet & backup 4G; **Q-27** approval supervisor.
- Bolehkah earn poin pasif dicatat setelah sinkronisasi untuk transaksi offline?

## Kapan ditinjau ulang
Setelah 3 bulan pilot F&B: bila waktu offline > 2% jam operasional di banyak outlet, evaluasi ulang opsi c untuk member ber-app.
