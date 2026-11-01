# ADR-037: Gateway LLM 9router dengan Fallback Ollama (K25-01)

- **Status:** Accepted — pemilik proyek (K25-01 di `CLAUDE.md`), diterapkan G37, 1 Nov 2026. Pemakaian di **production
  ditahan** sampai DPO memutuskan transfer lintas negara (Q-T7).
- **Tanggal:** 2026-11-01 (Gelombang 37)
- **Pemutus:** pemilik proyek (pilihan penyedia), DPO/Legal (transfer data ke luar negeri)
- **Merevisi:** ADR-014 (AI di jaringan privat tanpa egress), hanya untuk ocr-worker dan hanya bila 9router dipilih.
- **Terkait:** ADR-008 (OCR dua tahap), ADR-022 (TLS), `docs/04` §22, `docs/compliance/PDP_TRANSFER_LINTAS_NEGARA.md` P6

> **Ringkasan manajemen.** Membaca label harga yang sulit (OCR tahap 2) sekarang bisa memakai model Claude lewat
> 9router (layanan EFN di VPS Indonesia). Jika 9router gagal atau kuota habis, sistem otomatis memakai model lokal
> (Ollama). Karena 9router meneruskan foto ke penyedia model di luar negeri, fitur ini **mati secara default** dan
> **tidak bisa dinyalakan di production** sebelum DPO memutuskan dasar transfernya. Yang dikirim hanya foto label
> harga yang sudah diproses ulang (tanpa lokasi/GPS) dan instruksi tetap; nama pelapor, nomor HP, dan isi laporan tidak dikirim.

## Konteks
- K25-01: gateway LLM = 9router (`https://9router.bimacreative.tech`, wajib API key); bila kuota Claude habis/gagal →
  fallback Ollama lokal. Satu antarmuka untuk OCR tahap 2, Text-to-SQL, dan AI agent.
- ADR-014 melarang egress dari container AI. Ollama CPU di dev butuh ±70 s per foto (G10/G12); Claude via 9router ±2 s.
- 9router meneruskan permintaan ke Anthropic/Google (luar negeri) → transfer data pribadi lintas negara bila foto
  memuat data pribadi.

## Keputusan
1. Antarmuka `VlmClient` + `FallbackVlm` di ocr-worker (`efn_ocr/llm.py`); penyedia `NineRouterVlm` (Anthropic
   Messages API `/v1/messages`) dan `OllamaVlm`.
2. `OCR_LLM_PROVIDER` default `ollama` (perilaku dan isolasi lama tidak berubah).
3. Egress hanya lewat overlay `docker-compose.llm.yml` (network `egress` + secret key). Allowlist host di aplikasi
   (https, `9router.bimacreative.tech:443`, tanpa redirect/proxy env). `check-encryption` memastikan egress ↔ key.
4. Gerbang PDP: worker butuh `OCR_LLM_CROSS_BORDER_OK=true`; deploy menolak 9router di production sampai Q-T7.
5. Minimisasi: hanya gambar ter-encode ulang + prompt tetap (`docs/04` §22.3).

## Konsekuensi
- **Positif:** akurasi & latensi OCR tahap 2 jauh lebih baik; Ollama tetap jadi cadangan; tanpa egress bila tidak dipakai.
- **Negatif:** ketergantungan ke 9router (VPS bersama) dan penyedia model; biaya/kuota Claude; transfer lintas negara.
- **Belum:** firewall egress di level jaringan (saat ini network `egress` Docker tidak membatasi tujuan; pembatasan di
  aplikasi). Rekomendasi: proxy egress allowlist (mis. squid/smokescreen) sebelum production.

## Pertanyaan terbuka
- **Q-T7 (DPO/Legal):** dasar transfer foto Price Hunter ke penyedia model via 9router (kesetaraan / kontrak /
  persetujuan pengguna di syarat Price Hunter)? Sampai dijawab: production `ollama` saja.
- **Q-37-1 (IT):** model default 9router untuk vision (`cc/claude-haiku-4-5-20251001`) cukup, atau Sonnet?
