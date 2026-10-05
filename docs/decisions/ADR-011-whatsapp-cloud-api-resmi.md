# ADR-011: WhatsApp Cloud API Resmi (Meta) via Akun Langsung atau BSP — Baileys Dilarang

- **Status:** Proposed
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** IT Lead + Finance (biaya per pesan) + Legal (ToS & PDP)
- **Referensi review:** D-11, R-S04, R-B02, R-L01

> **Ringkasan manajemen.** OTP dan notifikasi dikirim lewat WhatsApp resmi Meta. Library gratis tidak resmi (Baileys) melanggar ketentuan WhatsApp: nomor bisa diblokir sewaktu-waktu dan seluruh OTP kasir berhenti. Biaya resmi kira-kira Rp350–400 per OTP (harga daftar Meta Indonesia 2026 dari sumber sekunder, belum termasuk PPN dan markup BSP; validasi dengan vendor).

## Konteks
- Arsitektur v2: *"Provider murah / Baileys open source"*.
- TSD: *"WhatsApp API"* (tanpa detail).
- OTP adalah kontrol anti-fraud (BRD §7.2); kanal yang bisa diblokir = kontrol yang bisa hilang.

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya (asumsi, validasi dengan vendor) |
|---|---|---|---|
| A. **Cloud API langsung (Meta) + template authentication** | Resmi, tanpa markup BSP, API stabil | Verifikasi bisnis Meta; penanganan billing sendiri | Tarif Meta per pesan (authentication Indonesia ±Rp357 + PPN 11%, sumber sekunder Okt 2026) |
| B. **Cloud API via BSP lokal** (mis. Qontak, Gupshup, dsb.) | Bantuan onboarding, invoice Rupiah, dashboard | Markup per pesan / biaya bulanan | Tarif Meta + markup BSP (umumnya biaya platform bulanan; validasi) |
| C. Baileys / library unofficial | Gratis | Melanggar ToS, nomor diblokir, tanpa SLA, risiko keamanan sesi | "Gratis" sampai nomor diblokir — kehilangan kanal OTP |
| D. SMS OTP | Jangkauan universal | Lebih mahal, rawan SIM-swap/SMS pumping | Rp300–700+/SMS (asumsi) |

## Keputusan
Opsi A sebagai target, opsi B dapat dipakai bila tim butuh bantuan onboarding — **keduanya memakai Cloud API resmi**. Baileys/library unofficial **dilarang** di semua environment (prinsip CLAUDE.md). SMS hanya sebagai fallback opsional dengan rate limit ketat. Di dev, `WHATSAPP_ENABLED=false` → sender console.

## Konsekuensi
- **Positif:** kanal OTP stabil dan legal; template terverifikasi.
- **Negatif:** biaya per pesan masuk OpEx (lihat `docs/07_TCO_TEMPLATE.md`); template perlu approval Meta (beberapa jam–hari).
- **Dampak dokumen:** Arsitektur v2 (Baileys) tidak berlaku.

## Risiko terkait
R-S04 (enumeration — respons tidak membocorkan nomor), R-B02 (OpEx), R-L01 (consent marketing ≠ OTP).

## Pertanyaan terbuka untuk klien
- **Q-17** kanal & fallback & budget per pesan; **Q-41** siapa pemegang akun Meta Business / BSP.
- Estimasi volume OTP/bulan (POS + login app) untuk negosiasi tier volume.

## Kapan ditinjau ulang
Setiap perubahan tarif Meta, atau bila biaya WA > anggaran 2 bulan berturut-turut.
