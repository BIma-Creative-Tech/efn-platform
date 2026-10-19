# efn_loyalty_accounting

Kebijakan Poin EFN per PT (empat mata, publikasi bertanda tangan ke BFF) + akrual liabilitas poin harian per PT +
rekonsiliasi saldo. Odoo CE 19, **LGPL-3**, `depends: account, mail, efn_core, efn_audit, efn_pos_loyalty_ledger`.

- **Kontrak mengikat:** `docs/04_SERVICE_CONTRACTS.md` §13.1 + §13.7 L-61, L-62 (menang atas `docs/08` §11.4).
- **ADR:** ADR-020 (ledger poin tunggal di BFF; Odoo hanya kebijakan + jurnal, saldo member tidak di Odoo, D-05),
  ADR-001 revisi (PT penerbit per lot earn, kliring antar-PT), ADR-027 (tanpa akun/angka bisnis di kode →
  PLACEHOLDER), ADR-031 (tanpa modul pihak ketiga), ADR-022 (TLS + CA internal untuk setiap panggilan BFF).
- **Risiko dimitigasi:** R-B06 / R-S10 (insider mengubah parameter poin sendirian → empat mata + audit + read-only),
  SEC-61 (batas earn per PT/kasir/member dikelola Finance, BFF tetap memegang plafon env sebagai batas keras),
  liabilitas poin tidak tercatat di buku PT (W-29) → akrual harian + rekonsiliasi dead-man.

## Model
| Model | Isi |
|---|---|
| `efn.loyalty.policy` | Per (company, `version`). Field per PT: `earn_idr_per_point`, `redeem_min_points`, `redeem_max_pct`, `max_points_per_order`, `daily_max_points_member`, `pos_config_hourly_max`, `company_hourly_max`, `earn_on_money_only` (default True, 18-10), `effective_from`. `point_value_idr` **hanya** di record global (company berkode `EFN`, L-62). `state` `draft → approved → published` \| `rejected`. Jejak: `maker_id`, `last_editor_id`, `approved_by_id/at`, `rejected_by_id/at`, `published_at`. |
| `efn.loyalty.policy.outbox` | Satu per kebijakan disetujui (`loyalty_policy:<PT>:<versi>`, UNIQUE): state, percobaan, backoff, HTTP terakhir, kode error, SHA-256 body. |
| `efn.loyalty.accrual` | Satu per (company, tanggal bisnis) (UNIQUE): komponen BFF, `move_id`, saldo liabilitas Odoo, selisih, status rekonsiliasi. |
| `res.company` (+ Settings › Invoicing › *Poin EFN*) | Akun liabilitas, beban program, Piutang Poin Antar-PT (kliring, PT penebus), Utang Poin Antar-PT (L-66, PT penerbit), pendapatan kedaluwarsa (breakage), jurnal Misc. Semua **PLACEHOLDER** per PT; tidak ada default otomatis. Saran liabilitas = akun `21990091` dari `efn_setup_pos` (akun outstanding metode Poin EFN) agar penukaran POS dan akrual memakai akun yang sama. |

Semua model: `company_id` wajib, `_check_company_auto`, record rule `company_ids`, ACL baca-saja kecuali draft
kebijakan oleh Loyalty Manager. Penulisan status/akrual/outbox hanya oleh kode server (sudo) setelah cek.

## Aturan kebijakan
- **Versi** dialokasikan server (`pg_advisory_xact_lock` per company + UNIQUE), naik monoton; pengguna tidak bisa
  mengisi/mengubahnya. Draft yang ditolak tetap memakai nomornya.
- **Empat mata:** pembuat = grup baru **EFN / Loyalty Manager** (`efn_loyalty_accounting.group_loyalty_manager`,
  implies `base.group_user`);
  penyetuju/penolak = **Accounting / Manager** (`account.group_account_manager`) dengan company record di
  `company_ids`-nya, dan **bukan** pembuat maupun penyunting terakhir draft (juga bila penyuntingan lewat kode
  ber-sudo — uid asli). SoD per user (Loyalty Manager ⟂ Accounting Manager) ditegakkan `efn_role` (§13.3).
- **Read-only setelah approved/rejected** untuk semua termasuk `sudo()`; perubahan = versi baru (draft baru otomatis
  diisi dari versi terakhir yang disetujui). Non-draft tidak bisa dihapus. Field status/jejak ditulis lewat RPC →
  `AccessError`. `effective_from` yang sudah lewat saat disetujui digeser ke waktu persetujuan (tidak berlaku surut).
- **Audit:** aturan `efn_audit` untuk `efn.loyalty.policy` (semua field, nilai lama/baru, uid asli walau sudo) +
  tracking chatter + log aplikasi (uid, tanpa PII).

## Publikasi Odoo → BFF (L-61)
`PUT /v1/pos/loyalty/policies/{company_code}/{version}` lewat klien Odoo→BFF yang ada (`efn.bff.client`: https + CA
internal `EFN_CA_FILE`, `X-EFN-Service-Key` dari file secret, tanpa redirect). Perluasan privat
`_efn_loyalty_request` di modul ini mengirim **byte body persis** dan header:

```
X-EFN-Signature: t=<unix>,v1=<hex HMAC-SHA256(key, "<t>." + body)>
```
- Body = `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`:
  `{schema_version: 1, company_code, version, effective_from: "YYYY-MM-DDTHH:MM:SSZ", approved_by_uid, params: {8 field per PT}, point_value_idr?}` (`point_value_idr` hanya `company_code=EFN`).
- Kunci = isi file `EFN_LOYALTY_POLICY_SIGNING_KEY_FILE` (default `/run/secrets/loyalty_policy_signing_key`),
  whitespace ujung dibuang, dipakai sebagai byte UTF-8, minimal 32 byte; dibaca setiap kirim; tidak pernah di DB/log.
  Implementasi acuan penerima: `models/signing.py::verify_signature` (replay window 300 s, `hmac.compare_digest`).
- Tanpa kunci / kunci < 32 byte → **publikasi tertahan** (`pending`, `signing_key_missing|signing_key_too_short`),
  log ERROR tanpa rahasia, aktivitas Loyalty Manager; tidak pernah dikirim tanpa tanda tangan, tidak gagal karena umur.
- 2xx → `done` + kebijakan `published`. 409 `policy_conflict`, 422 `policy_out_of_bounds`, 400 → gagal final +
  aktivitas (kebijakan tetap `approved`; perbaikan = versi baru). 401/404/408/429/5xx/jaringan → backoff 1 menit…1 jam,
  tanda tangan dibuat ulang dengan `t` baru setiap percobaan, gagal setelah `efn.loyalty.policy_outbox_max_hours` (72).
  Tombol *Kirim ulang* (Loyalty/Accounting Manager company itu) untuk outbox gagal.

## Akrual harian (§13.1 no. 6, L-61 — Odoo menarik)
Cron `ir_cron_efn_loyalty_accrual` 19:00 UTC (= 02:00 Asia/Jakarta): untuk setiap company berkode dan setiap tanggal
bisnis kemarin + `efn.loyalty.accrual_lookback_days` (3) hari ke belakang yang belum final →
`GET /v1/pos/loyalty/accruals?company_code=<PT>&date=YYYY-MM-DD` (query hanya allowlist `company_code` + `date`;
penjaga R2 `_efn_get` menolak tanggal ISO sebagai "mirip HP"). Respons divalidasi ketat (integer non-negatif,
kode PT kontrak, `company_code`/`date` sama dengan permintaan). Satu `account.move` per (company, tanggal),
`ref = EFN-LOY-<PT>-<YYYY-MM-DD>`; sudah ada → dilewati & ditautkan (idempoten).

| Komponen | Debit | Kredit |
|---|---|---|
| `earned_value_idr` | Beban Program Poin | Liabilitas Poin |
| `earn_reversed_value_idr` (clawback) | Liabilitas Poin | Beban Program Poin |
| `redeemed_value_idr_by_issuer[I]`, I ≠ PT ini | Kliring Poin Antar-PT (label menyebut PT penerbit) | Liabilitas Poin |
| `redeemed_value_idr_by_issuer[PT ini]` | — (sudah dijurnal POS, §9.2) | — |
| L-66 `redeemed_value_idr_as_issuer_by_redeemer[R]` (PT ini = penerbit, R = PT penebus) | Liabilitas Poin | Utang Poin Antar-PT |
| L-66 `restored_value_idr_as_issuer_by_redeemer[R]` | Utang Poin Antar-PT | Liabilitas Poin |
| `restored_value_idr` | — (refund POS pembayaran Poin EFN negatif sudah menjurnal) | — |
| `expired_value_idr` (W-17, G22) | Liabilitas Poin | Pendapatan kedaluwarsa — hanya bila `efn.loyalty.breakage_journal_enabled=True` (default False) |

Akun/jurnal yang dibutuhkan tidak ada → `missing_config`, log ERROR (kode saja, tanpa angka/PII), aktivitas
Accounting Manager, **tanpa jurnal**; semua komponen 0 → `no_entry` (tanpa akun pun tidak galat). BFF mati/respons
tidak valid/posting gagal (mis. lock date) → `error` + aktivitas; dicoba lagi run berikutnya selama dalam lookback,
atau tombol *Proses ulang* (Accounting Manager company itu).

## Rekonsiliasi (§13.1 no. 7) — format log mengikat (lead G21)
Setiap run, per PT per tanggal (termasuk akrual yang sudah final dan selisih 0 → dead-man), logger
`odoo.addons.efn_loyalty_accounting.recon`:
```
EFN_LOYALTY_RECON {"bff_closing_liability_idr": 9500, "company_code": "GL", "date": "2026-10-18", "diff_idr": 0, "odoo_liability_idr": 9500}
```
`json.dumps(obj, sort_keys=True)`, tepat 5 kunci, integer IDR, `diff_idr = odoo − bff`; INFO bila 0, WARNING bila
≠ 0 (+ aktivitas Finance). Saldo Odoo = saldo kredit akun liabilitas PT dari jurnal posted s.d. tanggal itu. Akun
liabilitas belum diatur → saldo Odoo 0 + WARNING terpisah (bukan baris RECON). Tidak ada koreksi otomatis.
Catatan: BFF juga memakai token `EFN_LOYALTY_RECON` (heartbeat `service=api-gateway`); pipeline Promtail harus
memisahkan per `service`.

## Integrasi POS (`efn_pos_loyalty_ledger` 19.0.2.3.0)
`GET /v1/pos/loyalty/params?company_code=<PT config>` (cache per db+PT, 5 menit). BFF menerapkan kebijakan aktif PT
untuk earn/redeem; Odoo tidak menyalin nilai poin.

## Cara uji manual
1. Install `efn_loyalty_accounting`; beri user A grup *EFN Loyalty / Loyalty Manager* dan user B *Invoicing /
   Administrator* (company yang sama).
2. A: menu **Poin EFN › Kebijakan Poin › Baru** → simpan (versi 1, draft). A tidak melihat tombol Setujui; bila
   dipaksa lewat RPC → ditolak.
3. B: buka draft → **Setujui** → status Disetujui, outbox *Publikasi ke BFF* `pending` → `done` bila BFF + kunci
   tersedia, kebijakan menjadi Dipublikasikan. Coba ubah field → ditolak; buat versi baru (terisi dari versi 1).
4. Tanpa file `/run/secrets/loyalty_policy_signing_key`: outbox tetap `pending`, log ERROR `kunci tanda tangan tidak
   tersedia`, aktivitas ke Loyalty Manager.
5. Settings › Invoicing › *Poin EFN*: isi akun PT. Jalankan cron *EFN Loyalty: akrual …* (Settings › Technical ›
   Scheduled Actions › Run) → menu **Akrual Harian**: status, jurnal `EFN-LOY-…`, rekonsiliasi; log `EFN_LOYALTY_RECON`.

## Test (`tests/`, tag `efn`; BFF di-mock)
`test_signing.py` (format, HMAC atas byte persis, tamper, replay window ±300 s, kunci lain, kunci hilang/pendek),
`test_policy.py` (versi monoton & tidak bisa diisi, pembuat = penyetuju ditolak, penyunting terakhir ditolak, hanya
Accounting Manager company sama, read-only termasuk sudo, tidak bisa dihapus/disalin, effective_from tidak surut,
prefill versi baru, L-62, batas nilai, company tanpa kode, record rule, audit `efn_audit`),
`test_policy_outbox.py` (body kontrak persis + tanda tangan terverifikasi, global `point_value_idr`, retry dengan `t`
baru dan header lama tidak bisa diputar ulang, 401/404 → max age, 409/422/400 final + aktivitas, tanpa kunci tertahan
lalu terkirim setelah kunci dipasang, kunci pendek, tombol retry & akses, idempotensi enqueue, transport klien:
https + CA + service key, header tidak bisa menimpa service key, allowlist query),
`test_accrual.py` (baris jurnal per komponen, lintas PT kliring, PT sendiri tidak dijurnal, idempoten run ulang &
record hilang, akun per PT, tanpa akun → tanpa jurnal + ERROR + aktivitas + retry, nilai 0 tanpa akun, format RECON
persis (ok/mismatch/dead-man), galat BFF & validasi respons, breakage nonaktif default, record rule/ACL, cron tanggal
& semua company).

## Referensi (ADR-031)
Tidak ada modul OCA/Cybrosys 19.0 terverifikasi untuk ledger poin eksternal + jurnal akrual; konsep parameter per
program dari CE `loyalty` saja. Pola outbox/backoff/aktivitas = `efn_pos_loyalty_ledger` (kode sendiri). Tidak ada
kode yang disalin.

## Batasan / belum terverifikasi
- **Belum dijalankan** di Odoo 19 nyata oleh penulis (tanpa shell) — lead menjalankan test di container.
- Metode publik baru wajib masuk `RPC_ALLOWLIST` `efn_core/tests/test_rpc_surface.py` (di luar cakupan file modul
  ini): `efn.loyalty.policy.action_efn_approve`, `efn.loyalty.policy.action_efn_reject`,
  `efn.loyalty.policy.outbox.action_efn_retry`, `efn.loyalty.accrual.action_efn_retry`.
- L-66: kedua sisi lintas PT dijurnal (penebus: Dr Piutang/Kliring / Cr Liabilitas; penerbit: Dr Liabilitas / Cr
  Utang Poin Antar-PT, restore kebalikannya), sehingga saldo liabilitas kedua PT cocok dengan `closing_liability_idr`
  (diuji: earn GL, tukar BCR). Field L-66 opsional (BFF lama → map kosong); kunci PT sendiri/tidak dikenal → respons
  ditolak. Settlement piutang/utang antar-PT = W-15 (belum).
- `restored_value_idr` tidak dijurnal (asumsi: restore selalu lewat refund POS di PT tempat refund). Restore lintas PT
  belum ditangani.
- Mata uang company diasumsikan IDR (nilai BFF integer IDR); tidak ada konversi.
- Posting di periode terkunci (lock date) → `error post_failed:*` (perilaku `action_post` CE 19 pada lock date belum
  diuji).
- Breakage disiapkan, nonaktif (G22). Settings hanya dapat disimpan admin sistem (perilaku CE `res.config.settings`).
- Aturan `efn_audit` untuk perubahan akun di `res.company` tidak ditambahkan (satu aturan per model; diserahkan ke
  `efn_setup_audit`).
