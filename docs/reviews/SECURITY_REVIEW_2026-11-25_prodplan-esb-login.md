# Security Review G66b — Login per pengguna ESB untuk modul Produksi — 25 Nov 2026

Reviewer: efn-security-reviewer (read-only terhadap kode). Worktree `E:\Projects\efn-platform-pplogin`, branch
`feat/prodplan-esb-login` (belum di-commit, basis `origin/main`). Acuan: CLAUDE.md, ADR-051 amandemen A1 (Proposed),
docs/04 §30.17, review sebelumnya `SECURITY_REVIEW_2026-11-24_prodplan.md`.

Cakupan yang dibaca: `services/prodplan/efn_prodplan/{session,access,actor,esb,api,provision,logs,metrics}.py` (penuh),
`service.py` (util, akses, job, post, cancel, resend, resume), `jobs.py` (runner + pr_import), `outbox.py` (worker),
`workflow.py` (aturan SoD/cabang/resume), `config.py` (secret, gerbang tulis, provisioning), `store.py` (`PgActorStore`,
purge aktor), `__main__.py` (wiring klien); `infra/postgres/init/24-prodplan-esb-login.sql`; insight-web
`lib/prodplan-{session,login,token,client,guard,access}.ts`, `lib/pp-page.ts`, `lib/origin-guard.ts`, `lib/rate-limit.ts`
(`clientIp`), `proxy.ts`, `app/produksi/masuk/page.tsx`, `app/(pp)/layout.tsx`, `app/api/prodplan/{login,logout,expired,[action],boms}`;
Odoo `efn_esb_identity` (model, security XML, ACL, views), entri `efn_core/tests/test_rpc_surface.py`, `efn_role`
`TECHNICAL_GROUP_XMLIDS`; `infra/odoo-rpc-gateway/haproxy.cfg` (bagian prodplan-odoo); `docker-compose.prodplan-odoo.yml`,
potongan `docker-compose.prodplan.yml`, `scripts/secrets.list`, `scripts/gen-pki.sh` (profil), `PDP_DATA_INVENTORY.md`.

Nomor temuan: **SEC-234..241**. Metode: review statis, tanpa `git diff` dan tanpa menjalankan test. Hasil uji lead
(ruff/pytest, vitest/build, 285 test Odoo, check-encryption, E2E staging satu akun) diterima dan tidak diulang.

## Ringkasan eksekutif

- **Tidak ada Critical maupun High.** Jalur password bersih: password hanya ada di body request. Tidak masuk log, PG,
  metrik, cookie, URL, atau pesan galat. `refreshToken` dibuang saat parsing. Hash body di asersi diberi nonce 128 bit,
  jadi tidak bisa ditebak offline. Sid 256 bit dan yang disimpan hanya SHA-256-nya. `ppa2` tanpa sid hanya berlaku untuk
  login dan meta. Akibatnya insight-web yang dikompromikan pun tidak bisa bertindak sebagai pengguna lain tanpa cookie
  pengguna itu.
- **Satu Medium (SEC-234):** lockout per username tidak memandang sumber. Orang dalam yang tahu username planner atau
  kepala gudang bisa mengunci akun itu dari modul Produksi terus-menerus. Biayanya 5 percobaan per 15 menit per
  korban. Dampaknya approve, posting, dan resume-posting terhenti.
- **Lima Low:** import PR tanpa batas cabang sesi, sesi yatim yang tetap memegang token ESB, identitas aktor berbasis
  username lowercase, celah kecil validasi provisioning, dan masking username pendek. **Dua Info**, termasuk risiko
  residual desain *credential relay* yang perlu diketahui manajemen dan DPO.
- **Putusan:** boleh merge ke staging selama `PROD_WRITE_UNLOCKED=False` dan `PRODPLAN_ODOO_PROVISION_ENABLED=false`
  di production. SEC-234 sebaiknya ditutup sebelum dipakai pengguna production.

## Kontrol yang sudah benar (diverifikasi di kode)

| Area | Bukti |
|---|---|
| Password tidak bocor | `api.py:261-270` body login hanya ke `LoginService.create`; `logs.py:13-44` allowlist field (tanpa username/password/token); `JsonFormatter` mengabaikan `msg`/`exc_info` sehingga traceback library tidak tercetak; `esb.py:533-551` pesan exception hanya status HTTP; `prodplan-login.ts:54-95` log tanpa username; error → redirect `?e=<kode tetap>` |
| refreshToken / token | `esb.py:396-417` `LoginResult` hanya 4 field (token `repr=False`); `Session.__repr__` (`session.py:150`) dan `EsbClient.__repr__` menyamarkan token; token tidak pernah keluar proses (`SessionView` tanpa token/username/sid) |
| Sid | `secrets.token_urlsafe(32)` (`session.py:575`); store dikunci `sha256(sid)` (`actor.py:75-77`); cookie `__Host-epp` `HttpOnly; Secure; SameSite=Strict; Path=/` tanpa Domain (`prodplan-session.ts:18-25`), jadi tidak bisa diset subdomain (fiksasi sesi tertutup); sid tidak dikirim ke komponen klien |
| Pemisahan sesi | `proxy.ts:65-94`: `/produksi*` hanya membaca `__Host-epp`, halaman lain hanya `__Host-eis`; `prodplan-guard.ts` tidak membaca cookie Odoo; lapis kedua `requirePpSession` di layout dan setiap halaman `(pp)` |
| `ppa2` | `actor.py:132-182`: HMAC constant-time, 8 kunci berurutan, `exp-iat==60`, pengikatan m/p(+query)/bh, jti sekali pakai (TTL 120 > jendela 90 dtk), cache penuh = fail closed; `api.py:209-212` sid kosong hanya `session_create`/`meta`, login dengan sid ditolak |
| CSRF | login/logout/aksi: `isSameOriginMutation` (Sec-Fetch-Site/Origin) + Content-Type ketat; GET baca terlindung SameSite=Strict |
| Enumerasi | Semua galat sebelum kredensial valid = `invalid_credentials`/`login_locked`; `no_pp_access`/`esb_company_not_allowed` hanya setelah password benar |
| Otorisasi | `_visible` (`service.py:313-322`) env+brand+cabang → 404; `create_plan` ∩ CK; approve/transfer/post/resume `require_branches` + `require_doc_access`; SoD `check_sod` + kontributor audit; cache BOM/nama per `actor_id` |
| Token pemicu | Worker hanya memakai `token_for(run_as(row))`. `run_as_sub` hanya diubah ke pemanggil sendiri (post/cancel/resume). Baris yang mungkin sudah tercipta (`unknown`/`sent`/`needs_login` dengan `attempts>0`) hanya bisa dilanjutkan aktor aslinya, dan akan menjalani rekonsiliasi dulu, bukan POST ulang (`workflow.py:168-203`). Tidak ditemukan jalur bagi pengguna B untuk memakai token A |
| Gerbang production | `PROD_WRITE_UNLOCKED=False` konstanta (`config.py:23,106-111`); base URL dua nilai persis; allowlist method+path sebelum socket; `/auth/login`, `/auth/logout` POST tanpa `allow_write`; `/user/access`, `/branch/list/user` GET saja |
| Provisioning Odoo | `check_caller` menolak user dengan grup lain dan membuang context klien. Payload tertutup 7 kunci. Login shadow yang bentrok ditolak, jadi tidak pernah menaut ke user yang sudah ada. User baru `active=False`, tanpa password atau email, dengan context `no_reset_password`. Grup dipaksa ke `group_user` (ditambah multi-company) setiap login. Peran PP tidak dijadikan `res.groups`. Login API key, password, maupun passkey memerlukan user aktif. Haproxy: CN `prodplan-odoo` hanya untuk satu path exact, body ≤ 16 KiB. Traefik memblok `/json/2`. Flag default `false` dan dipaku literal di overlay dasar |
| Infra | prodplan `user 65534`, `read_only`, `cap_drop ALL`, `no-new-privileges`, core dump mati; sertifikat `prodplan-odoo` profil client saja; API key placeholder `disabled` ditolak `config.py:220`; tidak ada username ESB nyata di repo (grep username lama SEC-180 dan pola username: hanya data sintetis) |
| PDP | `PDP_DATA_INVENTORY.md` v21 mencatat `prodplan.actor`, `efn.esb.identity`, token di memori ≤ 1 jam, retensi; log memakai `user_mask` |

## Status tindak lanjut (efn-lead, 25 Nov 2026, setelah putaran perbaikan)

| ID | Status | Bukti |
|---|---|---|
| SEC-234 | Ditutup | Lockout per (username, kunci klien) + rem global; uji staging: sumber A terkunci pada percobaan ke-6, sumber B tidak |
| SEC-235 | Ditutup | `import-pr` dibatasi cabang sesi; test `test_needs_login` |
| SEC-236 | Sebagian | Route `expired` mencabut sesi; timeout diselaraskan; login ulang tetap tidak mencabut sid lama (batas 3 sesi per aktor) |
| SEC-237 | Tidak berlaku di staging | Login username huruf kecil = akun yang sama; konfirmasi production masih terbuka |
| SEC-238 | Terbuka (diterima) | Verifikasi PT/cabang saat aktivasi shadow user (cutover) |
| SEC-239 | Ditutup | `user_mask` username pendek |
| SEC-240 | Terbuka: butuh penerimaan risiko tertulis manajemen/DPO sebelum login ke ESB production | - |
| SEC-241 | Ditutup | `repr` tanpa body, `cancel` menegakkan cabang, limiter dibatasi |

## Tabel temuan

| ID | Severity | Area | File:baris | Ringkas | Risiko |
|---|---|---|---|---|---|
| SEC-234 | Medium | Lockout login | `session.py:271-307,508-517`; `prodplan-login.ts:68-72` | Lockout 5 gagal/15 menit per username tanpa memandang sumber → orang dalam bisa mengunci planner/kepala gudang tertentu secara permanen | R-S (auth DoS), R-O10 |
| SEC-235 | Low (PLAUSIBLE) | Cabang / import PR | `service.py:734-746`; `jobs.py:614-616` | `branch_ids` import PR tidak dibatasi ke cabang sesi; kosong = semua cabang yang dilihat token ESB; hasil (PR, produk, qty) terlihat semua peserta rencana | R-T24, §30.17.6 |
| SEC-236 | Low | Sesi yatim | `session.py:47,329,513-514`; `prodplan-client.ts:19,67`; `app/api/prodplan/expired/route.ts:10-15`; `prodplan-login.ts:54-95` | Login yang selesai setelah insight-web timeout, cookie yang dihapus lewat `/expired`, dan login ulang membiarkan sesi + token ESB hidup ≤ 1 jam tanpa pemegang | R-S25 |
| SEC-237 | Low (PLAUSIBLE) | Identitas aktor | `session.py:88-89,505-506,574`; `store.py:275-284`; `24-prodplan-esb-login.sql:39-42` | Aktor = `lower(username)`; bila username ESB case-sensitive, dua akun berbagi `actor_id` (token, SoD, audit tercampur) | ADR-051 §3.1 |
| SEC-238 | Low | Provisioning Odoo | `efn_esb_identity.py:238-245,265-270` | `branch_ids` kosong lolos validasi, dan cabang dari brand yang dikirim pemanggil menentukan PT shadow user tanpa pengecekan lain. Dampak kecil karena user diarsipkan, tetapi kepercayaan penuh ditaruh pada API key | R-S22 |
| SEC-239 | Low | PDP masking | `session.py:92-96` | Username 4–5 karakter hampir utuh di log (`budi` → `bu***i`) | UU PDP, R-L |
| SEC-240 | Info | Desain credential relay | (arsitektur A1) | Kompromi insight-web = panen password ESB. Kompromi prodplan = ≤ 200 token ESB dengan seluruh izin pengguna, termasuk tulis production di ESB (kunci `PROD_WRITE_UNLOCKED` hanya mengikat kode kita) | R-S25, SEC-233 |
| SEC-241 | Info | Hardening | `esb.py:237-241`; `service.py:1151-1182`; `session.py:291-302` | `HttpResponse` repr memuat body (login: token), `cancel` tanpa `require_branches`, memori `LoginLimiter` tumbuh per username unik | – |

## Detail

### SEC-234 — Medium — Lockout per username bisa dipakai mengunci korban

**Lokasi:** `services/prodplan/efn_prodplan/session.py:271-307` (`LoginLimiter`), `:508-517` (`locked()` dicek sebelum
ESB; `failure()` pada `EsbInvalidCredentials`); `services/insight-web/lib/prodplan-login.ts:68-72` (batas 10/15 menit per IP).

**Skenario:** karyawan di jaringan ops (Insight `internal-ops`) tahu username ESB kepala gudang, misalnya `gudang.ck`.
Setiap 15 menit ia mengirim 5 POST `/api/prodplan/login` dengan password acak. Satu IP cukup untuk dua korban
sekaligus (batas IP 10). Hasilnya `login_locked` permanen untuk korban, karena `locked()` dicek sebelum ESB dan login
sukses pun ditolak. Dampak bisnis: rencana tidak bisa di-approve. Baris outbox `needs_login` dengan `attempts>0` hanya
bisa dilanjutkan aktor aslinya (`workflow.py:195-196`), jadi posting CK tertahan. Efek samping (PLAUSIBLE): semua
percobaan gagal sampai ke ESB dari satu IP egress prodplan. Bila ESB punya lockout akun atau blokir IP sendiri, akun ESB
korban atau IP egress kita (yang mungkin dipakai bersama `esb-extract`) ikut terkunci.

**Rekomendasi minimal:** kunci lockout pada pasangan `(username_norm, client_ip)` untuk penguncian 15 menit. IP diteruskan
insight-web sebagai field tambahan yang ikut di-hash `bh`, atau lewat header bertanda tangan. Pertahankan batas global
per username yang lebih longgar (mis. 30/jam) sebagai rem terhadap brute force. Tambahkan alert Prometheus pada
lonjakan `prodplan_login_total{result="invalid_credentials"}` dan `login_locked`. Tanyakan ke vendor ESB kebijakan
lockout akun dan blokir IP (masukkan ke daftar Q).

### SEC-235 — Low (PLAUSIBLE) — Import PR tidak dibatasi cabang sesi

**Lokasi:** `service.py:734-746` (`branch_ids` dari body, tanpa `require_branches`), `jobs.py:614-616` (kosong = tanpa
filter `branchIDs`). insight-web selalu mengirim tanpa `branch_ids` (`prodplan-actions.ts:150`).

**Skenario:** planner yang cabang ESB-nya hanya CK + 3 outlet memicu import PR. Query ke `/purchase/purchase-request`
dikirim tanpa `branchIDs`, atau dengan cabang lain bila ia memanggil prodplan langsung lewat body. Bila ESB tidak membatasi
daftar PR berdasarkan cabang pengguna (belum terverifikasi; hanya satu akun admin di staging), nomor PR, produk, dan qty
dari outlet di luar cakupannya masuk ke `plan_demand`/`plan_unmapped`. Data itu lalu terlihat semua peserta rencana.
Tabel §30.17.6 juga tidak mencantumkan import PR.

**Rekomendasi minimal:** di `import_pr`, `require_branches(actor, branch_ids)`; bila kosong, isi `params["branch_ids"]`
dengan `sorted(actor.branch_ids)` (atau saring baris PR di job berdasarkan `branchID ∈ cabang pemicu`). Tambahkan baris
`import-pr` ke tabel §30.17.6.

### SEC-236 — Low — Sesi yatim tetap memegang token ESB

**Lokasi:**
1. Timeout tidak selaras: insight-web abort setelah 15 dtk (`prodplan-client.ts:19,67`). prodplan menunggu antrean login
   hingga 30 dtk (`session.py:47,329`), ditambah timeout HTTP ESB 30 dtk. Handler tidak mendeteksi klien putus, sehingga
   sesi tetap disimpan (`session.py:532`), sementara sid tidak pernah sampai ke browser.
2. `GET /api/prodplan/expired` (`expired/route.ts:10-15`) menghapus cookie tanpa `DELETE /v1/session`. GET lintas situs
   (navigasi top-level) bisa memicunya.
3. `handleLogin` (`prodplan-login.ts:54-95`) tidak mencabut sid lama bila browser sudah punya `__Host-epp` sah.

**Skenario:** pada jam sibuk (antrean 1/dtk) pengguna melihat "ESB tidak dapat dihubungi", padahal prodplan memegang token
ESB-nya selama ≤ 1 jam. Token itu bisa dipakai worker untuk baris outbox milik aktor tersebut (`token_for`). Pengguna yakin
belum masuk atau sudah keluar, padahal aplikasi masih bertindak atas namanya. Dampak terbatas pada aktor yang sama dan
dibatasi 3 sesi per aktor.

**Rekomendasi minimal:** `LOGIN_QUEUE_WAIT_SECONDS` ≤ 5 dan timeout `POST /auth/login` ≤ 8 dtk, sehingga totalnya di
bawah 15 dtk. Alternatifnya, timeout khusus login di insight-web 45 dtk. `/api/prodplan/expired` cukup diubah menjadi
`POST` same-origin, atau tetap GET tetapi memanggil `DELETE /v1/session` dulu bila cookie ada. Di `handleLogin`, bila
cookie lama sah, kirim `DELETE /v1/session` untuk sid lama (best-effort) sebelum `Set-Cookie` baru.

### SEC-237 — Low (PLAUSIBLE) — Identitas aktor = username lowercase

**Lokasi:** `session.py:88-89` (normalisasi), `:505-506` (ESB menerima username asli, aktor memakai lowercase),
`:574` (`upsert`), `store.py:275-284`, `24-prodplan-esb-login.sql:39-42` (UNIQUE `(esb_env, esb_brand, username_norm)`).

**Skenario:** bila ESB membedakan `Budi.CK` dan `budi.ck` sebagai dua akun (belum terverifikasi), keduanya mendapat
`actor_id` yang sama. Akibatnya `token_for` bisa memakai token akun lain untuk outbox (dokumen ESB tercatat atas nama
orang lain), SoD menyamakan dua orang, dan jejak audit tercampur. Sebaliknya, rename username di ESB membuat aktor baru,
sehingga riwayat SoD orang yang sama terputus.

**Rekomendasi minimal:** konfirmasi ke vendor ESB bahwa username case-insensitive. Lebih kuat lagi, kunci aktor pada ID
pengguna ESB yang stabil (klaim JWT / field login, mis. `userID`) dan simpan username hanya untuk tampilan. Minimal, tolak
login bila klaim `username` di JWT berbeda dari input setelah normalisasi.

### SEC-238 — Low — Validasi provisioning menaruh kepercayaan penuh pada API key

**Lokasi:** `efn_esb_identity.py:238-245` (`branch_ids` kosong → `branches` = recordset kosong → `no_company`, aman);
`:265-270` (identitas lama di-update tanpa membandingkan perubahan PT atau cabang).

**Skenario:** pemegang API key `efn_esb_provisioner` beserta sertifikat `prodplan-odoo` (artinya prodplan sudah
dikompromikan) bisa membuat shadow user arsip untuk username apa pun dan memindahkan PT shadow user yang belum diaktifkan.
User tetap arsip dengan grup minimal, jadi tidak ada eskalasi login. Yang terdampak hanya integritas data peta migrasi.
Kontrol yang ada sudah memadai untuk tahap ini. Temuan ini dicatat agar langkah aktivasi (migrasi) tidak memercayai
`company_id`/`esb_branch_ids` mentah.

**Rekomendasi minimal:** runbook aktivasi mewajibkan Superadmin memverifikasi PT dan cabang terhadap ESB sebelum
`action_efn_activate`. Opsional: log perubahan `company_id` identitas (tanpa PII) sebagai event audit.

### SEC-239 — Low — Masking username pendek

**Lokasi:** `session.py:92-96`. Username 4–5 karakter (`budi`, `rina1`) menjadi `bu***i` / `ri***1`, sehingga hampir
seluruhnya terbaca di log. Ini kurang selaras dengan semangat masking HP (`62812****789`).

**Rekomendasi minimal:** panjang ≤ 6 → `***`; selebihnya 2 + `***` + 1.

### SEC-240 — Info — Risiko residual desain *credential relay* (untuk manajemen/DPO)

Ini bukan bug, melainkan konsekuensi A1 yang perlu diterima secara tertulis:
- insight-web memegang password ESB setiap pengguna selama satu request. Kompromi proses insight-web berarti seluruh
  password yang diketik selama masa kompromi bisa dipanen. Password ESB juga mungkin dipakai ulang di sistem lain.
- prodplan memegang ≤ 200 token ESB hidup dengan **seluruh izin ESB pengguna**, bukan hanya endpoint allowlist kita.
  Kompromi prodplan memungkinkan penulisan atau penghapusan di ESB production atas nama pengguna yang sedang login.
  `PROD_WRITE_UNLOCKED` hanya mengikat kode kita, tidak mengikat penyerang yang menguasai proses. Dengan akun service
  dulu, dampaknya terbatas pada izin satu akun.
- Mitigasi yang disarankan: tutup SEC-233 (egress nftables hanya ke IP ESB + image bertanda tangan); minta ESB
  menyediakan peran API terbatas atau MFA; catat di DPIA (Q-ESBU4); pertahankan TTL ≤ 1 jam dan tanpa refresh token
  (sudah).

### SEC-241 — Info — Hardening kecil

- `esb.py:237-241` `HttpResponse` (frozen dataclass) memakai `repr` bawaan yang memuat `body`. Untuk login, body berisi
  `accessToken` + `refreshToken`. Saat ini tidak ada jalur log atau repr, tetapi tambahkan `field(repr=False)` pada `body`
  dan `headers`.
- `service.py:1151-1182` `cancel` hanya mensyaratkan `doc_delete`, tidak `require_branches` untuk asal/tujuan transfer
  yang akan dihapus. Ini sesuai §30.17.6, dan ESB yang memutuskan dengan token pembatal. Jika ESB menolak, baris menjadi
  `delete_failed`. Pertimbangkan menyamakan aturannya dengan `post`/`resume`.
- `session.py:291-302` `_failures` hanya dibersihkan untuk deque kosong, sehingga username unik menumpuk sampai restart.
  Laju dibatasi antrean 1/dtk (sekitar 86 ribu entri/hari). Bersihkan juga entri yang semua timestamp-nya di luar jendela.

## Belum diperiksa / belum terverifikasi

- Tidak dibaca: `components/pp/*` secara penuh (hanya grep XSS/`sid`/storage), `lib/prodplan-actions.ts` selain builder
  path, `prodplan-errors.ts`, `jobs/[id]/route.ts`, sisa `store.py` (claim/candidates), `check-encryption.sh`,
  `gen-secrets.sh`, test prodplan/insight/Odoo, README/runbook, seluruh `docker-compose.prodplan.yml`.
- Perilaku ESB yang menentukan severity: scoping cabang pada `/purchase/purchase-request` (SEC-235), case-sensitivity
  username (SEC-237), kebijakan lockout/IP ESB (SEC-234), dan apakah `/auth/logout` mencabut satu token atau semua token
  pengguna.
- Alur dua orang (approve, posting, `needs_login`, resume oleh aktor lain) hanya diverifikasi secara statis. E2E belum
  ada karena hanya ada satu akun staging.
- Bentuk `/json/2` untuk `efn_esb_provision` belum terverifikasi end-to-end (sesuai catatan kontrak).
