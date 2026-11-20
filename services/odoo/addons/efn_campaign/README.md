# efn_campaign — kampanye tertarget supermarket (G50)

Versi `19.0.1.0.0`, LGPL-3, Odoo 19 CE. Depends: `mail`, `loyalty`, `stock`, `efn_core`, `efn_audit` (hanya CE +
efn_*; tanpa OCA/Cybrosys; tidak bergantung pada `efn_legacy_history`, `efn_mig_*`, ESB, atau `efn_expiry_monitor`).

## Tujuan

Sisi Odoo dari kampanye promo personal supermarket SMD (K18-01, ADR-047 §3.4, `docs/04` §28.7/§28.10, L-155/L-156/
L-157/L-164, fit-gap G45 S-17):

1. **Usulan segmen** dari segment-worker (`efn_campaign_proposal_upsert`, `/json/2`): ringkasan saja (`run_id`,
   `segment_code`, jumlah anggota, metrik agregat, ringkasan teks) — **tanpa data member**. Idempoten per
   `(company, run_id, segment_code)`; menghasilkan `efn.campaign` berstatus `proposed`.
2. **Approval Marketing** dengan pemisahan tugas: *Kampanye: Marketing* melengkapi kanal, pesan, program voucher CE, dan
   mode voucher; *Kampanye: Marketing approver* yang **bukan** pembuat/penyunting menyetujui. State
   `draft → proposed → approved → sent` (atau `cancelled`). `approval_rev` bertambah setiap approval.
3. **Voucher per mode** (`voucher_mode`, dikunci setelah approval pertama), dibuat lewat wizard CE
   `loyalty.generate.wizard` dengan context `loyalty_no_mail` (Odoo tidak mengirim email kartu ke member; notifikasi
   dikirim BFF):
   | Mode | Program CE | Yang dibuat Odoo | Key kode di payload |
   |---|---|---|---|
   | `anonymous_code` (default) | `coupons` | `code_qty` kartu tanpa partner | `voucher_codes: [...]` |
   | `public_code` | `promo_code`, tepat satu aturan aktif berkode | `limit_usage=True`, `max_usage=code_qty` | `public_code: "..."` |
   | `member_bound` | `coupons` | kartu per partner di `partner_ids` (diisi Marketing) | tidak ada (CE menegakkan partner; BFF hanya notifikasi) |
   Tanpa program = kampanye notifikasi saja (`voucher_mode: null`, tanpa key kode).
4. **Outbox** `efn.campaign.outbox` (`docs/04` §28.7): satu baris per `(campaign, approval_rev)`,
   `idempotency_key = campaign:<company_code>:<campaign_id>:<approval_rev>`. BFF: `efn_campaign_claim(limit ≤ 20)`
   (`FOR UPDATE SKIP LOCKED`, `queued → claimed`, `attempts += 1`) → publish `promo.campaign.requested` →
   `efn_campaign_ack(ids)` (`done`, kampanye `sent`). Cron 5 menit: klaim > 10 menit tanpa ack → `queued`; klaim ke-5
   yang tetap tidak di-ack → `failed` + aktivitas ke penyetuju.
5. **Markdown (G45)** tanpa depends: `efn.campaign._efn_propose_markdown(company, ref_model, ref_id, product_ids,
   warehouse_ids, date_to)` membuat kampanye `markdown` `proposed` (idempoten per sumber); outbox membawa
   `ref_model`/`ref_id` sumber (mis. `efn.lot.markdown`).

## Kontrak RPC (bot `efn_campaign_<kode>`, satu company)

Semua metode di `efn.campaign`, pemanggil wajib Internal User + `group_campaign_bot` **saja**, tepat satu company
(selain itu `403 efn_campaign:access_denied`). Context klien dibuang. Sakelar mati → `422 efn_campaign:disabled`.

| Metode | Argumen | Respons |
|---|---|---|
| `efn_campaign_proposal_upsert` | `schema_version=1`, `company_code`, `run_id` (UUIDv4), `segment_code` (`^[a-z][a-z0-9_]{1,39}$`, sama dengan BFF), `member_count` (0..10 jt), opsional `metrics` (objek ≤ 20 kunci `^[a-z][a-z0-9_]{0,39}$` → angka; kunci baku §28.7b-2: `product_count`, `lookback_days`, `reason_market`, `reason_habit`), `summary` (≤ 256, tanpa PII), `generated_at` (ISO-8601 UTC, akhiran `Z` atau `+00:00`) | `{ok, campaign_id, state, created, updated[, locked]}` |
| `efn_campaign_claim` | `limit` 1..20 (default 20) | `{items: [{id, idempotency_key, kind, ref_model, ref_id, approval_rev, attempts, payload}]}` |
| `efn_campaign_ack` | `ids` (1..20 integer) | `{ok, acked[], already_done[], rejected[{id, code: not_found|not_claimed}]}` |

Kode galat 422: `invalid_input`, `unsupported_schema`, `payload_too_large` (> 64 KB), `too_many_items`,
`pii_suspected` (ringkasan/metrik memuat ≥ 6 digit berurutan atau pola email), `disabled`. 403: `access_denied`,
`company_mismatch` (`company_code` ≠ PT user). Klien: 422/403 tidak di-retry.

Galat UI (bukan RPC bot): `efn_campaign:field_not_writable` (Marketing menulis key di luar field konten, SEC-G50-01),
`efn_campaign:segment_required` (approval kampanye `segment` tanpa usulan segmen), `efn_campaign:no_channel`.

### Payload outbox v1 (= bentuk parser BFF `api-gateway/src/campaign/odoo-campaign.ts`, SEC-G50-02)

Fixture bersama (dibaca test Odoo dan Jest BFF): `tests/fixtures/campaign_payload_v1.json`, satu contoh per mode
(`anonymous_code`, `public_code`, `member_bound`, `no_voucher`/markdown). Kunci:
`schema_version, idempotency_key, campaign_id, company_code, kind, approval_rev, segment_run_id, segment_code,
markdown {channel_code: null, product_ids, outlet_codes, valid_to}|null, channels[], message {title, body},
voucher_mode|null, voucher_program_ref ("loyalty.program:<id>")|null, voucher_value_idr|null, budget_idr|null,
cap_per_member_week|null, valid_from, valid_to` + `voucher_codes` (hanya `anonymous_code`) atau `public_code` (hanya
`public_code`).
- Tanggal ISO-8601 UTC `...Z`: `valid_from` = `date_from` 00:00 WIB; `valid_to` = (`date_to` + 1 hari) 00:00 WIB
  (eksklusif). WIB = UTC+7 tanpa DST.
- `voucher_value_idr` = nilai reward program: diskon `per_order` → nilai diskon; diskon persen dengan
  `discount_max_amount` → batas itu; selain itu `null` (parser BFF hanya menerima integer > 0). Mata uang = mata uang
  program (asumsi IDR).
- Anggaran/cap hanya terisi bila `cap_enabled` (default mati, L-157); penegakan di BFF.
- Kampanye `segment` wajib punya `segment_run_id` + `segment_code` (termasuk kode publik); markdown ≤ 200 produk,
  ≤ 50 outlet berkode `^[A-Za-z0-9_-]{1,32}$`; kode voucher `^[A-Za-z0-9_-]{4,40}$`.

## Konfigurasi

- `efn_campaign.enabled` (default **`false`**; override per PT `efn_campaign.enabled.<KODE>`). Mati → RPC bot 422 dan
  tombol Setujui ditolak. Production OFF sampai DPO + Marketing + Finance (`docs/04` §28.10).
- User teknis per PT: login `efn_campaign_<kode>`, grup Internal User + *Layanan kampanye*, satu company, API key
  `/run/secrets/odoo_api_key_campaign_<kode>` (masa berlaku maks 35 hari, `api_key_duration`).

## Keamanan & privasi

- Record rule multi-company pada `efn.campaign` dan `efn.campaign.outbox`; `_check_company_auto` + pemeriksaan
  eksplisit company program/member/outlet.
- SEC-G50-01: pemanggil non-sudo hanya boleh `create`/`write` field konten (nama, jenis, kanal, periode, pesan, mode,
  program, jumlah kode, member, cap, markdown). Key lain (state/approval, ringkasan segmen, `outbox_ids`, `card_ids`,
  `message_ids`, `activity_ids`, ...) ditolak utuh `efn_campaign:field_not_writable`; `default_<field>` context untuk
  field non-konten dibuang. Isi ditulis sebagai user (ACL + record rule + check_company); hanya `state` awal,
  `maker_id`, `editor_ids` lewat jalur sistem. Isi terkunci setelah approval; mode terkunci permanen setelah
  `approval_rev > 0`.
- SEC-G50-03: kode voucher bersifat bearer. Modul **tidak** memberi ACL `loyalty.card` ke grup Marketing maupun
  approver; `efn.campaign.card_ids` hanya `base.group_system`; form memakai `card_count` (dihitung sudo).
  `efn.campaign.outbox.payload` hanya `base.group_system`. **Tetap terlihat lewat ACL bawaan CE**: `loyalty` sendiri
  memberi `base.group_user` 0/0/0/0 (dicek di source 19.0), tetapi `sale_loyalty`/`pos_loyalty` kemungkinan memberi
  baca `loyalty.card` (termasuk `code`) ke grup Sales/POS (belum diverifikasi di source) — user Marketing yang juga Salesman/POS user/manager tetap bisa membaca kode lewat hak itu (tidak
  diblokir modul ini agar penebusan kupon di POS/online tidak rusak). Kode publik terbaca lewat `loyalty.rule` (wajar).
- Audit `efn_audit` pada `efn.campaign` (create/write/unlink) kecuali `partner_ids` (nama member tidak disalin ke log).
  Outbox tidak diaudit.
- `member_bound` menyimpan identitas member di Odoo (kartu ber-partner, L-164), tetapi payload outbox tidak membawa
  partner maupun kode.

## Risiko yang dimitigasi

R-B17 (promo berlebihan/anggaran bocor: approval + SoD, cap opsional), R-L13/R-L01 (Odoo tanpa data member di jalur
segmen, deteksi PII di ringkasan worker, minimisasi audit), replay/dobel kirim (klaim atomik, idempotency_key dengan
`approval_rev`, ack idempoten), R-S04 (permukaan RPC bot fail-closed).

## Cara uji manual

1. `ir.config_parameter` `efn_campaign.enabled = true`; buat program Kupon di PT SMD.
2. Sebagai bot (`/json/2` lewat gateway) panggil `efn.campaign/efn_campaign_proposal_upsert` → kampanye muncul di
   menu *Kampanye* (filter "Menunggu approval").
3. Marketing: isi judul/isi pesan, pilih program, `code_qty`. Approver lain: *Setujui & buat voucher* → tab Voucher
   menampilkan jumlah kartu; tab Approval menampilkan baris outbox `queued`.
4. Bot: `efn_campaign_claim` → `efn_campaign_ack([id])` → kampanye `Terkirim ke BFF`.

Test otomatis: `tests/test_campaign.py` (tag `efn`).

## Batasan / belum

- Sumber `partner_ids` untuk `member_bound` belum ditetapkan kontrak (sementara diisi manual oleh Marketing); BFF
  hanya memberi notifikasi ke anggota segmen, jadi penerima notifikasi dan pemegang kartu bisa berbeda.
- `voucher_value_idr` tidak dikonversi kurs (asumsi mata uang program = IDR).
- Markdown lewat `_efn_propose_markdown` tetap butuh approval Marketing (§28.5a menyiratkan langsung ke outbox) —
  pertanyaan terbuka; integrasi tombol "kirim ke member" G45 belum.
- Ambang 4-eyes per anggaran (Q-SM24) belum: semua kampanye butuh penyetuju ≠ pembuat/penyunting.
- Erasure member (`member_bound`): kartu ber-partner belum dianonimkan otomatis saat erasure (ikut alur erasure
  partner CE/efn_privacy; perlu hook).
- Batas `MAX_CODES = 5000` kode per kampanye dan ukuran respons klaim (≤ 20 baris) belum diukur terhadap batas body
  gateway.

## Referensi

ADR-047 §3.4, ADR-044 (kanal WA unofficial), ADR-031 (tanpa dependency pihak ketiga; tidak ada kode OCA/Cybrosys yang
dirujuk untuk modul ini), pola `efn_promo` (klaim push), `efn_demand_forecast` (`forecast_api.check_caller`),
`efn_mgm_policy` (SoD penyunting), `efn_audit`.
