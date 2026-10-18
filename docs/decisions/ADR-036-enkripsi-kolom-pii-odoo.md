# ADR-036: Enkripsi Kolom PII di Odoo (W-08 / E-38, tahap 1)

- **Status:** **Accepted — pemilik proyek, 18 Okt 2026** (butir 18-34 "disetujui sesuai rekomendasi", dengan ralat cakupan 18-31, 18-32, 18-33; jawaban 18-30, 18-35, 18-7, 18-8; dicatat efn-lead di `docs/decisions/README.md` bagian "Keputusan pemilik proyek 18 Okt 2026"). Nama & jabatan penandatangan dilengkapi klien di register keputusan. Sebelumnya: Proposed (18 Okt pagi).
  - **Yang diterima:** opsi C (AES-256-GCM di ORM Odoo), key ring & kustodi §4.3, blind index exact §4.4, mode terkunci §4.6, rotasi §4.12, migrasi §4.13, erasure §4.14, log §4.15.
  - **Yang diralat oleh pemilik proyek:** cakupan tahap 1 diperluas (pelanggan + vendor individu + karyawan + akun portal vendor; nama, alamat, kota, kode pos ikut dienkripsi), salinan email/HP di `pos.order` **dienkripsi** (bukan dikosongkan), pencarian nama lengkap + HP lengkap + **awalan kata**. Rinciannya di **§0**. **§0 menang atas §1–§14 bila bertentangan.**
  - **Interpretasi architect yang masih perlu konfirmasi** (tidak menghalangi status Accepted; default dipakai tim): Q-PII7..Q-PII12 di §0.13.
- **Tanggal:** 2026-10-18 (Gelombang 19); revisi Accepted 2026-10-18 (perencanaan Gelombang 20)
- **Pemutus (klien):** IT + Security (opsi & kunci), Legal/DPO (cakupan, residu), Finance + Tax (dampak e-Faktur/Coretax), PO + Ops Outlet (perilaku pencarian kasir, jadwal pilot)
- **Referensi:** Q-E3, Q-P21, Q-ON7, PP-3, pengecualian **E14** (ADR-022), paket **W-08** & **W-06** (`docs/10_RENCANA_GELOMBANG_18_PLUS.md` §1, §3 butir 6), R-L01, R-L10, R-T08, R-B11, R-S10, R-T13
- **Terkait:** ADR-022 (Keputusan 5, kustodi, E14), ADR-024 (kredensial per service), ADR-029 (identitas member opak), ADR-031 (tanpa modul pihak ketiga), ADR-034 (kanal online), ADR-035 (portal), `docs/04_SERVICE_CONTRACTS.md` §1, §7.2, §7.3, §8.4, §8.6, §10; `docs/compliance/PDP_DATA_INVENTORY.md` v7 §2.2, §2.2d; acuan BFF `services/api-gateway/src/common/pii/{crypto,reencrypt,pii-columns}.ts`; `efn_privacy/models/{member_hash,res_partner}.py`; `efn_pos_otp_gate/models/res_partner.py`

> **Ringkasan manajemen (Accepted, 18 Okt 2026).** Data pribadi di Odoo milik **pelanggan, vendor perorangan, karyawan, dan akun portal vendor** (nama, alamat, kota, kode pos, HP, email, NPWP/NIK) dienkripsi oleh Odoo sebelum masuk database, termasuk salinan email/HP di order kasir. Staf yang berhak tetap melihat dan mencetak data seperti biasa. Pencarian tetap bisa dengan **nama lengkap**, **HP lengkap**, dan **awalan kata minimal 3 huruf** (mis. "budi", "sant"), tetapi **tidak** dengan potongan di tengah kata ("anto" tidak menemukan "Santoso"), dan daftar pelanggan tidak lagi bisa diurutkan menurut abjad. Laporan per kota diambil dari data warehouse dengan kode wilayah. Biaya hasil verifikasi architect: **±9–11,5 minggu-engineer (±Rp45–104 jt)**, lebih tinggi dari perkiraan awal lead (±7–9), karena data karyawan, file XML Coretax, dan propagasi ke kontak anak ikut tahap 1. Dengan 2 engineer Odoo, selesai **±5–6 minggu kalender** (G20 untuk pelanggan, G21 untuk vendor/karyawan). Syarat: database produksi dibuat **setelah** tahap pelanggan selesai, dan data vendor/karyawan diimpor **setelah** tahap kedua selesai.

> *Ringkasan awal (Proposed, 18 Okt pagi; diralat oleh §0):* Nomor HP, email, NPWP/NIK, dan alamat pelanggan yang tersimpan di Odoo akan **dienkripsi oleh aplikasi Odoo sebelum masuk database**. Salinan database, backup, atau akun baca database yang bocor tidak lagi membuka data itu. Staf yang memang berhak tetap melihat data seperti biasa, dan faktur pajak tetap tercetak lengkap. Konsekuensinya: **kasir hanya bisa mencari pelanggan dengan nomor HP/email/NPWP lengkap**, tidak bisa mencari sebagian angka. Biaya **±4–6 minggu-engineer (±Rp20–54 jt)**, lisensi Rp0, muat di G19 bila uji coba teknis 3 hari pertama lolos. **Rekomendasi jadwal:** bila database produksi baru dibuat setelah W-08 selesai (kemungkinan besar, karena server staging/produksi belum ada), enkripsi langsung aktif sejak hari pertama dan pengecualian E14 tidak pernah dipakai. Bila pilot ritel terpaksa mulai lebih dulu, migrasi wajib selesai **sebelum outlet ke-2 atau sebelum data faktur pembeli online diterima, mana yang lebih dulu**, dengan batas akhir 31 Jan 2027.

---

## 0. Revisi 18 Okt 2026 — Accepted (berlaku; menang atas §1–§14 bila bertentangan)

### 0.1 Dasar keputusan
| Butir | Isi keputusan pemilik proyek | Bagian ADR yang diralat |
|---|---|---|
| 18-30 (Q-PII3) | Kasir mencari member dengan **HP lengkap**; data pelanggan tidak dimuat ke PC kasir sebelum dicari | §4.5 (L-36-3 diterima) |
| 18-31 (Q-PII2) | Email/HP di order POS **boleh disimpan asalkan dienkripsi**; struk email tetap bisa dari Odoo | §4.1 baris `pos.order`, §4.7 no. 4, L-36-4 |
| 18-32 (Q-PII1) | **Nama, alamat, kota, kode pos ikut dienkripsi di tahap 1**; pencarian dengan nama lengkap, HP lengkap, dan nama ber-wildcard | §4.1 baris `name/city/zip`, §4.4, §4.5, §4.7, §4.9 |
| 18-33 (Q-PII5) | Kontak vendor individu, karyawan, akun portal vendor **ikut tahap 1** | §4.1 (cakupan S1 → S1+), §12 |
| 18-34 | ADR-036 **disetujui sesuai rekomendasi** dengan ralat di atas | Status |
| 18-35 (Q-PII4) | Retensi backup off-site **ikuti rekomendasi**: 35 hari + base backup bulanan 12 bulan; kunci pensiun disimpan ≥ retensi terpanjang + 30 hari | §4.11 |
| 18-7 (Q-F19-6) | NPWP/NIK hanya untuk pembeli yang **meminta faktur** atau B2B | §4.1 (NIK hanya di jalur W-06) |
| 18-8 (Q-PII6) | **File XML Coretax disimpan** di Odoo (retensi 10 th, masuk cakupan enkripsi) | §4.8 |

### 0.2 Cakupan tahap 1 yang baru (S1+)

**Kelas subjek terlindungi.** Penanda `efn_pii_protected` tetap satu arah (§4.1). Aturannya berubah dari "ditandai saat menjadi member/pembeli" menjadi **terlindungi sejak dibuat** (*protect-by-default*), sehingga tidak ada jendela waktu ketika data tersimpan plaintext.

| Kelas | Siapa | Aturan penanda (dievaluasi di `create`, dalam panggilan yang sama) |
|---|---|---|
| **P** pelanggan | member, pelanggan buatan kasir, pembeli online (W-06), partner B2C lain | semua partner `is_company=False` yang tidak masuk pengecualian K-a di bawah |
| **V** vendor individu | vendor perorangan + kontak perorangan di bawah vendor perusahaan | sama dengan P (berlaku untuk semua individu, tidak bergantung `supplier_rank`). `supplier_rank` baru naik saat PO pertama, terlalu terlambat untuk dijadikan pemicu |
| **A** akun portal vendor | `res.users` dengan `share=True` (portal) + partner-nya | partner portal = individu → terlindungi. **Login portal = ID opak** (mis. `vnd-7Q3K9M`), bukan email (Q-PII8) |
| **K** karyawan | data **pribadi** karyawan di `hr.employee` / `hr.version` | **selalu** dienkripsi (bukan berbasis penanda), lihat tabel field |
| Turunan | kontak anak (`child_ids`), alamat kirim/tagih, partner hasil merge | anak dari partner terlindungi → terlindungi; kontak `type='contact'` di bawah perusahaan → terlindungi; alamat `type≠'contact'` milik perusahaan → tidak; merge: tujuan terlindungi bila salah satu sumber terlindungi (SEC-136) |
| Tidak terlindungi | partner perusahaan (`is_company=True`: 6 entitas EFN, vendor/pelanggan B2B berbadan usaha), alamat perusahaan; **pengecualian K-a**: partner milik user internal (`share=False`) dan *work contact* karyawan | user internal & work contact dibuat dengan konteks `efn_pii_skip_default` oleh alur `res.users`/`hr` (bukan oleh klien RPC). Alasan K-a: nama karyawan/user tampil di seluruh Odoo (chatter, penanggung jawab, kasir `pos_hr`, persetujuan, jejak audit) sebagai **identitas kerja**. Mengenkripsinya +2–3 minggu-engineer dan memutus pemilihan karyawan di banyak modul. **Interpretasi architect → Q-PII7** |

Catatan: partner yang `is_company` berubah dari False ke True tetap terlindungi (satu arah). Hal ini diterima dan didokumentasikan.

**Field per model** (E = dienkripsi `EfnPiiChar`; B = + blind index exact; T = + token nama; K = dikosongkan):

| Model.field | Perlakuan | Kelas | Catatan / sumber 19.0 |
|---|---|---|---|
| `res.partner.name` | **E + T** (+ token `f` nama lengkap) | P, V, A | `name = fields.Char(index=True, …)`; constraint `CHECK (type='contact' AND name IS NOT NULL …)` tetap terpenuhi oleh ciphertext |
| `res.partner.complete_name` | **E + T** (compute stored) | P, V, A | `compute='_compute_complete_name', store=True, index=True`; `_order = "complete_name ASC, id DESC"` (§0.6) |
| `res.partner.street`, `street2`, `city`, `zip` | **E** (tanpa indeks pencarian) | P, V, A | laporan per kota lewat `efn_region_id` (§0.7) |
| `res.partner.phone` | **E + B** | P, V, A | — |
| `res.partner.email` | **E + B** | P, V, A | — |
| `res.partner.email_normalized` | **E + B** (diralat: **bukan** dikosongkan) | P, V, A | stored compute (`mail.thread.blacklist`). Dienkripsi + blind index (normalisasi sama dengan `email`) agar `_find_or_create_from_emails` / pencarian `in` tetap menemukan pemilik email. Ini menutup jalur "partner baru dari email masuk" (SEC-136). SQL join blacklist tetap tidak cocok untuk partner terlindungi |
| `res.partner.phone_sanitized` | **K** | P, V, A | tetap seperti PoC (pencarian `REGEXP_REPLACE` mentah tidak bisa didukung) |
| `res.partner.vat` | **E + B** | P, V, A | `index=True` (btree berisi ciphertext). `efn_pii_base_vat` tetap wajib (L-39) |
| `res.partner.l10n_id_nik`, `l10n_id_buyer_document_number` | **E** (+ **B** untuk NIK) | P (hanya jalur permintaan faktur/B2B, 18-7), V | `efn_pii_l10n_id` |
| `res.partner.commercial_company_name` | tidak | — | untuk individu biasanya kosong/`company_name` (nama usaha); SOP: `company_name` tidak dipakai untuk pelanggan individu |
| `res.partner.efn_region_id` (**baru**) | **tidak** dienkripsi | P, V | Many2one `efn.region` (kab/kota, kode Kemendagri). Kebocoran yang diterima: kab/kota domisili (§0.7) |
| `pos.order.email`, `pos.order.mobile` | **E** untuk **semua** order (bukan hanya partner terlindungi) | P | diralat dari "tidak disalin" (L-36-4 → 18-31). Email yang diketik kasir untuk struk juga data pribadi |
| `account.move.invoice_partner_display_name` | **E** bila partner terlindungi | P, V | `compute='_compute_invoice_partner_display_info', store=True` = salinan `display_name` partner. Penanda turunan `account.move.efn_pii_protected` (stored, satu arah, dari `commercial_partner_id`) |
| `ir.attachment` XML Coretax (`res_model='l10n_id_efaktur_coretax.document'`) | **E** isi file (lihat §0.8) | P, V | dibuat `ir.attachment.create({'raw': …})` lalu `message_post` |
| `hr.employee.private_phone`, `private_email`, `emergency_contact`, `emergency_phone` | **E** | K | `groups="hr.group_hr_user"`; `emergency_*` `tracking=True` |
| `hr.version.private_street`, `private_street2`, `private_city`, `private_zip`, `identification_id`, `ssnid`, `passport_id` | **E** (+ **B** untuk `identification_id`) | K | `hr.employee` `_inherits = {'hr.version': 'version_id'}`; setiap versi menyimpan salinan sendiri. Semua `tracking=True` → redaksi tracking (§0.5) |
| `mail.tracking.value` (nilai lama/baru field di atas) | **tidak ditulis** untuk field cakupan; riwayat lama diredaksi saat penanda dipasang | semua | `mail` memberi `tracking` pada `phone`/`email` partner (docs/08 §10.5); `hr.version` men-tracking semua field privat |
| `res.users.login` | **tidak** dienkripsi | A | login portal wajib opak (Q-PII8) sehingga bukan data pribadi; email tetap di partner (E + B) |
| `res.partner.bank.acc_number`, `acc_holder_name` | **tidak** di tahap 1 | V, K | `sanitized_acc_number` + constraint unik tidak kompatibel dengan IV acak. Usulan tahap 1b (+0,5–1 minggu) → **Q-PII9** |
| `mail.message.email_from`, `mail.mail.email_to`, `comment`, filestore lain (PDF faktur) | **tidak** (tahap 2) | — | residu, dicatat di inventaris PDP & DPIA. `mail.mail` struk email dihapus otomatis setelah terkirim (`auto_delete`, **belum terverifikasi** untuk struk POS 19) |

### 0.3 Format, AAD, dan pengikatan ke record (keputusan atas SEC-138)
- **Diputuskan: ciphertext diikat ke id record.** Format `efnpii2:` = blob §4.2 dengan AAD `efn:odoo:<tabel>.<kolom>:<id>:v2`. Format `efnpii1:` (tanpa id) hanya boleh ada **di dalam transaksi `create`**.
- Alurnya: `INSERT` menulis `efnpii1:` (id belum diketahui). Setelah `super().create()` kembali, metode milik mixin `efn_pii` menulis ulang kolom cakupan record baru ke `efnpii2:` dengan **SQL langsung di transaksi yang sama** (pola `_efn_pii_encrypt_existing` PoC). Plaintext tidak pernah mencapai SQL, dan `efnpii1:` tidak pernah ter-commit bila jalurnya benar.
- **Baca:** `efnpii2:` didekripsi dengan id record. `efnpii1:` yang ditemukan di luar transaksi pembuatnya didekripsi (agar tidak ada kehilangan data), dicatat `CRITICAL` dengan kode `unbound_ciphertext`, dan dihitung oleh query verifikasi harian (alert `EfnOdooPiiUnbound`).
- Ciphertext yang dipindah antar-record lewat SQL → gagal autentikasi → nilai pengganti + `auth_failed` (diuji, kriteria AC-7).
- Biaya: 1 `UPDATE` tambahan per `create` yang mengisi field cakupan. Untuk `pos.order`, ini hanya terjadi bila email/HP terisi. Diukur di AC-1.
- Ditolak: (b) id dari `nextval` sebelum insert (`create` ORM tidak menerima id dari luar tanpa monkeypatch); (c) id acak per record di kolom yang sama (penyerang dengan hak tulis DB ikut memindahkan kolom id acak, sehingga tidak memberi integritas); (d) menerima risiko (r12 merekomendasikan id).
- **Test vector bersama BFF ↔ Odoo** (L-39 b) memakai string AAD EFN untuk kedua format (`…:v1` dan `…:<id>:v2`) dan kunci uji acak yang dibuat saat test. Vektor tidak disimpan sebagai kunci di repo (gitleaks).

### 0.4 Blind index exact (tidak berubah) + kunci ketiga untuk token nama
| Secret | Isi | Dipakai untuk |
|---|---|---|
| `odoo_pii_key_v<n>` | AES-256 key ring | enkripsi semua field E |
| `odoo_pii_bidx_key` | HMAC | blind index exact (B): `phone`, `email`, `email_normalized`, `vat`, `l10n_id_nik`, `identification_id` |
| **`odoo_pii_name_key`** (baru) | HMAC | token nama (T), §0.5. Dipisah agar rotasi/kebocoran satu kunci tidak memengaruhi yang lain, dan agar kebocoran kunci token (ruang nama besar, tebakan mahal) terpisah dari kunci HP (ruang kecil, mudah di-brute force, SEC-140 c) |

Ketiga secret hanya di-mount ke service `odoo` (SEC-140 b: berada di container yang sama dengan kunci enkripsi). Risiko ini **diterima**, karena Odoo memang harus membaca plaintext. `deploy-validate` dengan `EFN_ODOO_PII_REQUIRED=true` menolak deploy tanpa ketiganya.

### 0.5 Pencarian nama: blind index token per kata + awalan kata ≥ 3 huruf

**Normalisasi** (sama saat tulis dan cari; modul `efn_pii/tools/name_tokens.py`):
1. Unicode NFKC → `casefold()` → NFKD lalu buang tanda diakritik (é → e).
2. Karakter selain `[a-z0-9]` → spasi (titik, koma, tanda hubung, apostrof: "Hj.", "Al-Fatih" → `hj`, `al fatih`).
3. Rapatkan spasi. Hasilnya `N(s)` dan daftar kata `w₁…wₖ`.
4. Tidak ada kamus gelar/sinonim, fonetik, atau ejaan alternatif di tahap 1 ("Muhammad" ≠ "Muhamad").

**Jenis token** (HMAC-SHA256 dipotong **128 bit**, base64url tanpa padding = 22 karakter; subkunci HKDF dari `odoo_pii_name_key` per jenis):

| Jenis | Subkunci HKDF `info` | Isi | Untuk |
|---|---|---|---|
| `f` | `efn:odoo:nametok:f:v1` | `N(name)` dan `N(complete_name)` (bila berbeda) | nama lengkap persis (`=`, `in`, `=ilike`) |
| `w` | `efn:odoo:nametok:w:v1` | setiap kata `wᵢ` dengan panjang ≥ 2 | kata utuh (dipakai `=`-per-kata dan dedup) |
| `p` | `efn:odoo:nametok:p:v1` | setiap awalan `wᵢ[:L]` untuk 3 ≤ L ≤ min(len(wᵢ), 15) (termasuk kata utuh) | awalan kata (`ilike`) |

- **Padding:** daftar token per record diurutkan (bukan urutan kata) lalu dipadatkan ke kelipatan **16** dengan token acak unik yang tidak pernah cocok dengan kueri. Tujuannya menyembunyikan jumlah kata dan panjang kata (tanpa padding, jumlah token `p` membuka panjang kata).
- **Sumber token** = kata dari `complete_name`. Kumpulan ini memuat kata `name` dan, untuk kontak di bawah perusahaan, juga kata nama perusahaan, sehingga mencari "sumber" menemukan "PT Sumber Rejeki, Budi".

**Penyimpanan (rekomendasi T2):** kolom `res_partner.efn_name_tokens` (Char stored compute, `groups="base.group_system"`, `copy=False`, tidak dimuat POS, tidak di-grant ETL) berisi token dipisah spasi, dengan **indeks GIN ekspresi** `USING gin (string_to_array(efn_name_tokens, ' '))` yang dibuat di `init()`. Kueri memakai `string_to_array(efn_name_tokens, ' ') @> ARRAY[t₁,…,tₙ]`.
- Kelebihan: ikut baris (erasure = kosongkan kolom; `VACUUM FULL` menulis ulang bersama tabel), tanpa tabel/ACL tambahan, AND multi-kata dalam satu operator.
- **Fallback T1** bila uji GIN gagal kriteria AC-1: tabel `efn_pii_name_token(partner_id int, token bytea(16))` PK `(token, partner_id)` + indeks `(partner_id)`, kueri `id IN (SELECT partner_id … WHERE token = ANY(…) GROUP BY partner_id HAVING count(DISTINCT token) = n)`. Fallback ini memerlukan sinkronisasi eksplisit di flush dan erasure.

**Perkiraan ukuran (belum diukur):** asumsi nama rata-rata 2,5 kata × 6 huruf, sehingga token nyata ±13–15 per record, dipadatkan ke 16 atau 32. Heap ±0,4–0,75 KB + GIN ±0,5–1 KB, jadi **±1–1,8 KB per partner terlindungi**: 200 rb partner ≈ 0,2–0,35 GB; 1 jt partner ≈ 1–1,8 GB. Token acak padding menambah kunci GIN unik (beban indeks), dan itulah sebab dipilih kelipatan 16, bukan ukuran tetap besar.

**Penulisan ulang domain** (di `condition_to_sql` `EfnPiiChar` untuk `name`/`complete_name`; partner tidak terlindungi tetap memakai kolom asli seperti PoC):

| Operator / nilai | Perilaku untuk partner terlindungi |
|---|---|
| `=`, `in`, `=ilike` (tanpa `%`/`_`) | token `f` atas `N(nilai)` |
| `ilike`, `like` (termasuk `%x%` dari UI) | buang wildcard `%`, `*`, `_`; pecah menjadi kata; kata ≥ 3 huruf → token `p`; kata < 3 huruf diabaikan bila ada kata lain; **semua kata wajib cocok (AND, urutan bebas)**. Tidak ada kata ≥ 3 huruf → tidak cocok |
| `not …` | negasi semantik (pola PoC) |
| `=like`, regex, `<`/`>` | tidak cocok |
| Kueri bermaksud infiks (`*anto*`) | diperlakukan sebagai **awalan** "anto" (menemukan "Antonius", **tidak** "Santoso"). Didokumentasikan ke pengguna |

```mermaid
flowchart LR
    Q["name_search / domain ilike 'budi sant'"] --> N[normalisasi + pecah kata]
    N --> K{"kata >= 3 huruf?"}
    K -- tidak ada --> X[partner terlindungi: tidak cocok]
    K -- ada --> H["HMAC p-token per kata (odoo_pii_name_key)"]
    H --> S["SQL: (plaintext ilike AND NOT protected) OR (protected AND tokens @> ARRAY[...])"]
    S --> R[ORM mendekripsi hasil untuk user berhak]
```

**Kebocoran yang diterima** (dump DB + tanpa kunci): (1) **kesetaraan token**, yaitu dua record berbagi kata/awalan yang sama; (2) **frekuensi token**, sehingga analisis frekuensi dengan statistik nama publik bisa menebak token populer (mis. awalan nama paling umum); (3) **ember jumlah token** (16/32/48). **Tidak** bocor: urutan abjad, panjang kata persis, posisi kata, pola infiks.
**Ditolak:** trigram/infiks (kebocoran pola jauh lebih besar dan ukuran ±5–10×; hanya bila diminta, lewat ADR baru), *order-preserving/order-revealing encryption* (membuka urutan abjad), kunci sort huruf pertama (membuka huruf pertama).
**Rotasi `odoo_pii_name_key`:** hanya bila bocor. Job batch menghitung ulang token dari nama hasil dekripsi (pola rotasi `odoo_pii_bidx_key`, §4.4).

### 0.6 Dampak per area (wajib di fit-gap lanjutan & test)
| Area | Dampak | Penanganan |
|---|---|---|
| `complete_name` / `display_name` | `complete_name` stored menjadi ciphertext; `display_name` (compute non-stored, depends `complete_name, email, vat, …`) didekripsi ORM → tampilan normal | `complete_name` = `EfnPiiChar` compute stored (flush compute = jalur yang sama dengan PoC K1; **diuji ulang khusus**) |
| `_rec_names_search = ['complete_name','email','ref','vat','company_registry']` | `name_search` = OR `ilike` pada kelima field | `complete_name` → token `p`; `email`/`vat` `ilike` → blind index **bila istilah berbentuk email/NPWP lengkap**, selain itu tidak cocok; `ref`/`company_registry` tidak berubah |
| POS (`get_new_partner(config_id, domain, offset)` → `search(domain, limit=100)`; preload `get_limited_partners_loading` SQL `ORDER BY order_count DESC, name`) | pencarian nama/HP di server memakai penulisan ulang di atas; preload memuat partner terlindungi plaintext ke browser | `efn_pii_pos`: preload **mengecualikan** partner terlindungi kecuali partner order terbuka (18-30); pencarian HP lengkap → blind index (sudah PoC); pencarian nama → token `p`. **Belum terverifikasi:** apakah UI POS 19 otomatis memanggil server saat hasil lokal kosong atau perlu tombol "cari di server" (fit-gap; mungkin perlu patch OWL kecil) |
| Purchase / Sales / Accounting (pilih partner Many2one, filter daftar) | memakai `name_search` res.partner + traversal `partner_id.name` (mis. `account.move._rec_names_search = ['name','partner_id.name','ref']`) | otomatis lewat `condition_to_sql` (traversal domain); `account.move.invoice_partner_display_name` ber-`ilike` → ditulis ulang ke `partner_id IN (subquery token)` untuk move terlindungi. **Belum terverifikasi:** `filter_domain` view pencarian faktur/PO 19.0 |
| HR | nama karyawan tidak dienkripsi (K-a) → pencarian HR tidak berubah; field privat tidak dicari kecuali `identification_id` (= blind index) | `efn_pii_hr` |
| Sorting | `_order = "complete_name ASC, id DESC"`: partner terlindungi terurut menurut ciphertext (acak tetapi stabil); `read_group` per partner terurut sama, labelnya terdekripsi | **O1 (default, Rp0):** diterima + didokumentasikan; laporan QWeb yang butuh abjad mengurutkan di Python setelah dekripsi (`sorted(records, key=…)`). **O2 (+1–2 hari):** kolom `efn_sort_name` (= `complete_name` untuk non-terlindungi, NULL untuk terlindungi) dan `_order = "efn_sort_name, id desc"` → terlindungi tampil "terbaru dulu". Tidak dipilih dulu karena mengubah urutan global partner (→ **Q-PII11**) |
| Laporan per kota | `city`/`zip` ciphertext → pivot/group by kota di Odoo tidak bermakna | §0.7 (DWH + `efn_region_id`). Laporan per **kota outlet** tidak terdampak (alamat outlet = partner perusahaan) |
| Dokumen QWeb (faktur, PO, struk, slip) & Coretax | dibaca lewat ORM → plaintext (Coretax: `_l10n_id_coretax_build_invoice_vals` lewat ORM, docs/08 §10.5; XML dirender `ir.qweb._render(...)` lalu disimpan sebagai `ir.attachment`) | test AC-3; XML disimpan terenkripsi (§0.8) |
| Mail gateway & portal | `email_normalized` terenkripsi + blind index → pencarian pemilik email (ORM `in`) tetap jalan | **Belum terverifikasi:** `auth_signup` reset password (cari `login` lalu `email`) dan jalur `mail` yang memakai SQL mentah selain blacklist |
| `same_vat_partner_id`, deteksi duplikat | `vat in` → blind index (PoC K3 lulus) | — |
| Erasure (`efn_privacy`) | anonimisasi menulis `False` → ciphertext, blind index, **token nama**, `efn_region_id` ikut kosong | test wajib (pola §4.14) |
| ETL | tidak berubah: tanpa grant `res_partner` | pengecualian sempit untuk region (§0.7) |
| Performa | dekripsi per field per record (PoC: `search_read` 1.000 partner +280–430 %, ≈ +0,08 ms/partner) | cache dekripsi per request/transaksi (L-39 a) + preload POS tanpa partner terlindungi; diukur ulang AC-1 |

### 0.7 Laporan per kota lewat data warehouse (kode wilayah)
- Model master **`efn.region`** (modul `efn_region`, data saja): kab/kota dengan `code` Kemendagri (`^\d{2}\.\d{2}$`, mis. `31.71`), nama, `state_id`. Sumber data: kode wilayah resmi Kemendagri (**sumber & lisensi data → Q-PII10**).
- `res.partner.efn_region_id` diisi lewat dropdown (kasir/back office) atau dari BFF (W-06/W-05). Tidak ada penurunan otomatis dari kode pos di tahap 1, karena tabel kode pos → kab/kota butuh sumber data tambahan.
- ETL membaca **view** `efn_etl.partner_region(partner_id, company_id, region_code)` (owner `efn_odoo`, grant SELECT ke `efn_etl` hanya pada view ini). Grant ke `res_partner` tetap **dilarang** (K-36-5 diralat, §0.9). Mart: `dim_region` + kolom `customer_region_code` di `fact_sales_line` (data-ai).
- Kebocoran yang diterima: kab/kota domisili (granular kasar, ±500 nilai) tersimpan plaintext di Odoo dan DWH. Kode pos & alamat tetap terenkripsi.

### 0.8 XML Coretax disimpan terenkripsi (18-8)
- `efn_pii_l10n_id` membungkus pembuatan/regenerasi lampiran: isi `raw` disimpan `efnpii2:` (AAD `efn:odoo:ir_attachment.raw:<id>:v2`) dan didekripsi hanya oleh controller unduhan Coretax (`/l10n_id_efaktur_coretax/download_attachments/…`, grup Accounting, tercatat `efn_audit`).
- `message_post` lampiran ke chatter diganti pesan tanpa lampiran, agar `/web/content` tidak menyajikan ciphertext atau plaintext.
- **Belum terverifikasi:** titik override persis di 19.0 (`_generate_efaktur_invoice`, controller unduhan) dan apakah Coretax membaca ulang lampiran untuk regenerasi. Fit-gap wajib.
- Estimasi +0,5–0,75 minggu. Alternatif lebih murah (simpan plaintext di filestore di atas LUKS) bertentangan dengan 18-8 "masuk cakupan enkripsi" (→ **Q-PII12**).
- PDF faktur (lampiran laporan) tetap plaintext di filestore (tahap 2, residu dicatat).

### 0.9 Ralat atas ADR-022 dan K-36 (kontrak usulan)
**ADR-022** (diterapkan di ADR-022 pada 18 Okt 2026, bagian "Revisi 18 Okt 2026"):
- Keputusan 5 "Batas cakupan" **digantikan**: kolom PII Odoo untuk kelas P/V/A/K dienkripsi di ORM (ADR-036 §0.2).
- Tabel kunci + kustodi: tambah `odoo_pii_key_v<n>`, `odoo_pii_bidx_key`, `odoo_pii_name_key` (hanya Superadmin + salinan tersegel brankas Direksi).
- E14: cakupannya kini termasuk nama/alamat/kota/kode pos, kontak vendor, dan data karyawan. Kedaluwarsa tidak berubah (§5.4), ditambah syarat "sebelum impor master vendor/karyawan".
- Q-E3 ditutup.

**K-36** (usulan kontrak `docs/04`, milik lead):

| ID | Ralat / tambahan |
|---|---|
| K-36-1 | Tambah format `efnpii2:` (AAD `…:<id>:v2`), aturan `efnpii1:` hanya di transaksi create, kode `unbound_ciphertext` |
| K-36-2 | Tambah blind index `email_normalized`, `identification_id`; **tambah token nama** (§0.5: normalisasi, jenis f/w/p, HMAC-128 base64url, padding 16, kolom `efn_name_tokens`) |
| K-36-3 | Secret ketiga `odoo_pii_name_key` (hanya `odoo`) |
| K-36-4 | Erasure juga mengosongkan `efn_name_tokens`, `efn_region_id`, field `hr.*` bila karyawan dihapus menurut retensi HR |
| K-36-5 | **Diralat:** ETL tetap tanpa grant `res_partner`; satu-satunya pengecualian view `efn_etl.partner_region` (tanpa nama/alamat/kontak) |
| K-36-6 | Daftar kunci Superadmin + `odoo_pii_name_key` |
| K-36-7 | W-06: NIK hanya bila pembeli meminta faktur tanpa NPWP / B2B (18-7); partner pembeli dibuat **terlindungi sejak create** (otomatis oleh protect-by-default) |
| K-36-8 | Log `EFN_PII_KEYRING`: `error_code` + `unbound_ciphertext`, `plaintext_in_protected`; `counts` per model (bukan hanya `res_partner`); alert `EfnOdooPiiUnbound` |
| K-36-9 (baru) | `pos.order.email/mobile` selalu terenkripsi; BFF/ETL tidak membaca kolom ini |
| K-36-10 (baru) | Login portal vendor = ID opak; BFF/portal-gateway tidak pernah memakai email sebagai login |
| K-36-11 (baru) | Kode wilayah `efn.region.code` = format Kemendagri; payload BFF W-05/W-06 boleh membawa `region_code` (bukan nama kota) |

### 0.10 Status keputusan lead L-36
| # | Status 18 Okt |
|---|---|
| L-36-1 (jadwal/E14) | Tetap. **Ditambah:** DB produksi dibuat setelah **E-38a** (kelas P). Impor master vendor/karyawan setelah **E-38b** |
| L-36-2 (cakupan) | **Diganti** S1 → **S1+** (§0.2) |
| L-36-3 (tanpa preload) | **Diterima** pemilik proyek (18-30) |
| L-36-4 (tidak disalin) | **Diralat** → dienkripsi (18-31) |
| L-36-5 (estimasi) | **Diganti** §0.11 |
| L-36-6 (gerbang PoC) | **Selesai** (L-39: lulus bersyarat) |

### 0.11 Kriteria penerimaan E-38 penuh (gerbang merge + aktivasi)
E-38 dipecah menjadi dua tonggak: **E-38a** (kelas P + inti + POS + Coretax + akuntansi) dan **E-38b** (kelas V/A/K + region + mail gateway/portal). Modul tetap **tidak** di daftar install produksi/CI wajib sampai semua kriteria tonggaknya lulus.

| # | Kriteria | Bukti | Tonggak | Pemilik |
|---|---|---|---|---|
| AC-1 | **L-39 (a):** ukur `_load_pos_data` nyata (partner terlindungi tidak di-preload) + cache dekripsi per request. Overhead pemuatan POS < 5 % terhadap baseline. Pencarian POS nama/HP p95 ≤ 300 ms pada 200 rb partner terlindungi (hardware dev; **ulang di staging**). Angka ambang = usulan architect | laporan `docs/testing/` + test ukur | a | odoo-engineer + QA |
| AC-2 | **L-39 (b):** test vector bersama AAD EFN (`efnpii1`, `efnpii2`) lulus di Jest BFF dan test Odoo | CI dua sisi | a | odoo + bff |
| AC-3 | **L-39 (c):** PDF faktur & XML Coretax partner terlindungi isinya identik dengan baseline partner biasa; `raw` lampiran XML di DB/filestore = `efnpii2:` | test `efn_pii_l10n_id` | a | odoo |
| AC-4 | **L-39 (d):** gerbang CI run gabungan `efn_pii*` + **semua** modul daftar install produksi, dengan cek SQL mentah per model/field cakupan (deteksi R-T18) | job CI wajib | a | devsecops + QA |
| AC-5 | **SEC-136:** anak di bawah partner terlindungi, `_commercial_sync_to_children`, wizard merge, partner dari email masuk, redaksi `mail.tracking.value` lama → SQL mentah kolom = ciphertext / tracking tanpa nilai | test | a (merge, email), b (vendor) | odoo |
| AC-6 | **SEC-137:** `create`/`write` menolak tulis field cakupan bila `self._fields[f]` bukan `EfnPiiChar` (modul uji yang mendefinisikan ulang field → `UserError`); baca plaintext pada record terlindungi → log `CRITICAL` `plaintext_in_protected` (kode saja) + hitungan harian | test | a | odoo |
| AC-7 | **SEC-138:** ciphertext ditukar antar-record lewat SQL → nilai pengganti + `auth_failed`; tidak ada `efnpii1:` ter-commit (query verifikasi) | test | a | odoo |
| AC-8 | **SEC-140:** residu (kesetaraan, kunci sekontainer, brute force HP bila `odoo_pii_bidx_key` bocor) tercatat di ADR ini + runbook rotasi bidx/name key | dokumen | a | architect + devsecops |
| AC-9 | Token nama: normalisasi, awalan ≥ 3, AND multi-kata, tanpa infiks, padding kelipatan 16, erasure mengosongkan, rotasi menghitung ulang | test | a | odoo |
| AC-10 | Cakupan kelas: SQL mentah = ciphertext untuk member, pelanggan kasir, pembeli, vendor individu, kontak vendor, partner portal, `hr.employee`/`hr.version` privat, `pos.order.email/mobile`, `account.move.invoice_partner_display_name` | test + smoke | a (P), b (V/A/K) | odoo + QA |
| AC-11 | Query verifikasi residu harian semua model → metrik/alert `EfnOdooPiiPlaintextResidual` dan `EfnOdooPiiUnbound` | rule + uji sintetis | a | devsecops |
| AC-12 | Tour/regresi pencarian POS, Purchase, Accounting, HR (nama lengkap, awalan, HP lengkap, email/NPWP lengkap) lulus | tour | a, b | QA |
| AC-13 | Secret ketiga di `gen-secrets.sh`/`secrets.list`, `deploy-validate`, runbook `pii-key-rotation.md` + `key-custody.md` + `backup-restore.md` (key ring lengkap, kunci pensiun ≥ 12 bulan + 30 h sesuai 18-35) | review devsecops | a | devsecops |
| AC-14 | Re-review security-reviewer GO untuk tiap tonggak | review | a, b | security-reviewer |

### 0.12 Estimasi baru (verifikasi atas angka lead ±7–9 minggu-engineer)
Tarif rujukan ±Rp5–9 jt per minggu-engineer (asumsi ADR-031). PoC G19 sudah dibayar (tidak dihitung ulang).

| Pekerjaan | Agent | Tonggak | Minggu-engineer |
|---|---|---|---|
| Fit-gap lanjutan: `hr.version`, `account.move`, filter_domain view, wizard merge, mail gateway/`auth_signup`, UX cari POS, override Coretax | functional | a + b | 0,75 |
| Refactor PoC → mixin multi-model (`efn.pii.mixin`), SEC-137 fail-closed, SEC-138 `efnpii2`, cache dekripsi | odoo | a | 1–1,5 |
| SEC-136 (propagasi anak, commercial sync, merge, tracking redaksi) + aturan protect-by-default + pengecualian K-a | odoo | a | 0,75–1 |
| Token nama (normalisasi, kolom + GIN, penulisan ulang operator, padding, rotasi) | odoo | a | 1,25–1,5 |
| `complete_name`, `account.move` display name, sorting O1, `read_group` | odoo | a | 0,5 |
| `efn_pii_pos` (preload, `get_new_partner`, `pos.order.email/mobile`, struk email, ukur AC-1) | odoo | a | 0,75–1 |
| `efn_pii_l10n_id` (NIK, dokumen pembeli, XML terenkripsi + unduh, AC-3) | odoo | a | 0,5–0,75 |
| Migrasi/rotasi/`--decrypt` multi-model + token | odoo | a | 0,5 |
| devsecops: secret ketiga, deploy-validate, alert, runbook, gerbang CI AC-4 | devsecops | a | 0,5–0,75 |
| BFF: vektor bersama AAD EFN (AC-2) | bff | a | 0,25 |
| QA tonggak a | qa | a | 0,5–0,75 |
| **Subtotal E-38a** | | | **±7,25–9,25** |
| Kelas V/A (aturan vendor/portal, login opak, mail gateway, `auth_signup`) | odoo | b | 0,5 |
| `efn_pii_hr` (`hr.employee` + `hr.version`, tracking redaksi, blind index `identification_id`) | odoo | b | 0,5–0,75 |
| `efn_region` + view ETL + `dim_region` | odoo + data-ai | b | 0,5–0,75 |
| QA tonggak b | qa | b | 0,25 |
| **Subtotal E-38b** | | | **±1,75–2,25** |
| **Total E-38 penuh** | | | **±9–11,5 (±Rp45–104 jt)** |
| *Opsional:* O2 sorting (+0,25), rekening bank tahap 1b (+0,5–1), nama karyawan dienkripsi K-b (+2–3) | | | |

- **Hasil verifikasi:** angka lead ±7–9 hanya tercapai bila **XML Coretax tetap plaintext di filestore** (−0,5–0,75, bertentangan dengan 18-8), **data karyawan ditunda ke tahap 2** (−0,5–0,75, bertentangan dengan 18-33), dan **laporan per kota hanya per kota outlet** (−0,5–0,75). Ketiganya butuh keputusan pemilik proyek. Rekomendasi: pakai angka **±9–11,5**.
- **Kalender:** 2 engineer Odoo paralel (A: inti + token + POS; B: Coretax + akuntansi + migrasi, lalu E-38b) → E-38a ±4–5 minggu, E-38b ±1,5 minggu sesudahnya, **total ±5–6 minggu**. Dengan 1 engineer: ±9–11 minggu, dan W-06/W-13 mundur sebanding (R-T13).
- **Tidak butuh staging untuk dibangun.** Staging dibutuhkan untuk AC-1 di hardware nyata, uji restore dengan key ring (R-T08), dan rehearsal migrasi (hanya bila DB produksi terlanjur dibuat sebelum E-38a).

### 0.13 Pertanyaan konfirmasi (interpretasi architect; default dipakai tim)
| ID | Pertanyaan | Pemutus | Default | Dampak bila berbeda |
|---|---|---|---|---|
| Q-PII7 | Nama karyawan/user internal (identitas kerja) tetap plaintext; yang dienkripsi data **pribadi** karyawan (alamat rumah, HP/email pribadi, NIK, paspor, kontak darurat)? | DPO (Legal) + HR | Ya (K-a) | K-b: +2–3 minggu-engineer, memutus pemilihan karyawan/kasir di banyak modul |
| Q-PII8 | Login portal vendor memakai ID opak (bukan email)? | IT + Procurement | Ya | Email sebagai login = data pribadi tak terenkripsi di `res_users.login` (+1 minggu bila harus dienkripsi + constraint unik via blind index) |
| Q-PII9 | Rekening bank vendor individu/karyawan (`acc_number`, `acc_holder_name`) dienkripsi di tahap 1b? | DPO + Finance | Tahap 1b setelah E-38b | +0,5–1 minggu |
| Q-PII10 | Sumber kode wilayah (Kemendagri) & granularitas laporan (kab/kota) | Finance + DPO | Kab/kota Kemendagri | Granularitas lebih halus (kecamatan) menambah kebocoran |
| Q-PII11 | Daftar partner terlindungi tidak urut abjad (O1). Perlu "terbaru dulu" (O2)? | PO + Ops | O1 | O2 +1–2 hari |
| Q-PII12 | XML Coretax dienkripsi aplikasi (rekomendasi) — konfirmasi bahwa Finance tidak butuh membuka XML di luar unduhan Coretax | Finance + Tax | Ya | Plaintext di filestore: −0,5–0,75 minggu, residu PII 10 th |

### 0.14 Risiko (pembaruan)
| Risiko | Perubahan |
|---|---|
| **R-T18** (usulan → **masukkan ke register**) | Terbukti nyata (L-39, `base_vat`). Mitigasi kini **fail-closed** (AC-6) + gerbang CI (AC-4). Skor tetap 3×4 = 12 sampai AC-4 aktif, lalu 2×4 = 8 |
| R-L01 | Cakupan langkah teknis diperluas ke vendor & karyawan |
| R-T13 | Beban porting naik (mixin multi-model, token, override Coretax/POS) |
| R-B11 | Bertambah satu kunci fatal (`odoo_pii_name_key` tidak fatal: token bisa dihitung ulang; `odoo_pii_key_v<n>` tetap fatal) |
| Usulan **R-T19** (baru) | *Kebocoran frekuensi token nama* dari dump DB memungkinkan penebakan sebagian nama populer. Kemungkinan 2 × dampak 2 = 4. Mitigasi: kunci terpisah, padding, akses dump dibatasi, WAL-G terenkripsi |
| Usulan **R-O07** (baru) | *Keluhan pengguna atas pencarian/urutan* (tanpa infiks, tanpa abjad) mendorong *workaround* ekspor CSV. Kemungkinan 3 × dampak 2 = 6. Mitigasi: pelatihan, O2 bila perlu, audit ekspor (tahap 2) |

### 0.15 Belum terverifikasi (tambahan atas §13)
- Perilaku flush compute stored untuk `complete_name` dengan `EfnPiiChar` (PoC baru membuktikan field non-compute + blind index compute).
- Biaya & rencana kueri GIN `string_to_array(...) @>` pada 200 rb–1 jt baris (belum diukur); fallback T1.
- UX pencarian POS 19 (server dipanggil otomatis atau lewat tombol).
- `filter_domain` view pencarian faktur/PO 19.0 yang memakai `invoice_partner_display_name`.
- Titik override Coretax (`_generate_efaktur_invoice`, controller unduhan) dan apakah lampiran dibaca ulang.
- `auth_signup` reset password (pencarian `email`) dan jalur SQL mentah lain di `mail`.
- `mail.mail` struk POS `auto_delete`.
- Sumber dan lisensi data kode wilayah Kemendagri.

---

## 1. Konteks

### 1.1 Dokumen yang bertentangan
| Sumber | Kutipan | Status |
|---|---|---|
| ADR-022 Keputusan 5 | "**Batas cakupan (bukan pengecualian):** PII di **database Odoo** (`res.partner.phone/email/street`, `pos.order.partner_id`) tidak dienkripsi per kolom karena Odoo harus mencari/menampilkannya; dilindungi oleh LUKS (S1), TLS (T6), backup terenkripsi (S4) …" | **Digantikan** oleh keputusan 10 Okt (butir #50) → ADR ini |
| `docs/decisions/README.md` butir #50 | "**LUKS + AES-NI wajib** di pengadaan; **kolom PII Odoo dienkripsi** (pola AES-GCM key ring)" — "Pekerjaan baru … revisi ADR-022 Keputusan 5" | Prinsip berlaku |
| PP-3 | "Pilot retail jalan; enkripsi kolom PII Odoo (W-08) menyusul di G19 … migrasi enkripsi in-place sebelum online/F&B memakai profil pembeli" | Berlaku; dipertajam di §7 |
| ADR-022 E14 | "Kolom PII Odoo (HP member `res.partner`) belum dienkripsi kolom selama pilot retail … Kedaluwarsa: W-08 selesai (G19)" | Direvisi di §5.2 |
| `PDP_DATA_INVENTORY.md` §2.2 | `res.partner` name, phone, email, street, vat: "L, W, T (bukan per kolom — ADR-022 Keputusan 5, Q-E3)" | Berubah menjadi **K-Odoo** setelah W-08 |
| `PDP_DATA_INVENTORY.md` §2.2d (v7) | profil pembeli online: "**K (Odoo, Q-E3 — W-08)** … pencarian lewat *blind index* HMAC, bukan teks" | Diwujudkan ADR ini |
| `docs/10` W-08 | "pola AES-GCM key ring (seperti BFF) … *blind index* HMAC … secret `odoo_pii_key*` dikelola Superadmin; rotasi; dampak ekspor e-Faktur/Coretax & laporan" | Diwujudkan; estimasi direvisi (§9) |
| `docs/04 §7.2` | "Kolom PII dienkripsi di aplikasi … Implementasi acuan di BFF `src/common/pii/`" | Diperluas ke Odoo (usulan §6) |

### 1.2 Kenapa harus diputuskan sekarang
1. **W-06 (faktur atas nama pembeli) terblokir**: profil pembeli tidak boleh masuk Odoo sebelum kolomnya terenkripsi (`docs/10` §3 butir 6, E14 kolom kontrol kompensasi).
2. **Murah sebelum ada data produksi.** Migrasi in-place setelah go-live harus menulis ulang tabel, membangun ulang indeks, dan menunggu backup lama kedaluwarsa (§4.13). Tanpa data produksi, cukup pasang modul.
3. Sampai per dokumen terakhir yang saya baca (syarat G-1, K-4, A-3 di `docs/decisions/README.md`), **server staging/produksi belum ada**. Jadi peluang "enkripsi sejak hari pertama" masih terbuka.

### 1.3 Fakta Odoo CE 19.0 yang menentukan desain (terverifikasi dari sumber primer, branch `19.0`, 18 Okt 2026)
| Fakta | Sumber | Dampak ke desain |
|---|---|---|
| `res.partner`: `phone = fields.Char()`, `email = fields.Char()`, `street`/`street2`/`city`/`zip` = `fields.Char()`; **`vat = fields.Char(index=True)`**; **field `mobile` tidak ada** di base | `odoo/addons/base/models/res_partner.py` | Cakupan HP = `phone` saja. Indeks btree `vat` memuat plaintext, sehingga wajib REINDEX setelah migrasi |
| `_rec_names_search = ['complete_name', 'email', 'ref', 'vat', 'company_registry']` | idem | `name_search` dengan `ilike` atas email/vat tidak lagi cocok untuk partner terlindungi (nama tetap cocok) |
| `_compute_same_vat_partner_id` mencari `('vat', 'in', vats)` | idem | Bisa dipertahankan lewat penulisan ulang domain ke blind index |
| `mail` menambahkan `mail.thread.blacklist` ke `res.partner`; **`email_normalized` stored** (compute, tanpa index); `find_or_create` / `_find_or_create_from_emails` mencari `email_normalized`; blacklist memakai SQL join pada `email_normalized` | `addons/mail/models/{res_partner,mail_thread_blacklist}.py` | `email_normalized` = **salinan plaintext**. Untuk partner terlindungi nilainya harus dikosongkan |
| `phone_validation`: `res.partner` mewarisi `mail.thread.phone`; **`phone_sanitized` stored** + indeks ekspresi (dibuat di `init()`); pencarian `phone_mobile_search` memakai **SQL mentah `REGEXP_REPLACE(phone …) LIKE`** | `addons/phone_validation/models/{res_partner,mail_thread_phone}.py` | `phone_sanitized` = salinan plaintext → dikosongkan. Pencarian HP sebagian tidak bisa didukung |
| POS: `_load_pos_data_fields` memuat `street, street2, city, vat, phone, zip, email, …` ke browser; preload = `get_limited_partners_loading()` + partner order terbuka; pencarian server `get_new_partner(config, domain, offset)` dengan `limit=100` | `addons/point_of_sale/models/res_partner.py` | Plaintext dikirim ke browser kasir untuk partner yang dipreload. Pencarian server lewat `get_new_partner` dapat ditulis ulang ke blind index |
| `pos.order`: `email`, `mobile` = **compute stored** (`_compute_contact_details`, salinan dari partner) | `addons/point_of_sale/models/pos_order.py` | Salinan plaintext di setiap order → untuk partner terlindungi tidak disalin |
| `l10n_id_efaktur_coretax` (LGPL-3, depends `l10n_id`) ada di 19.0; menambah ke `res.partner`: **`l10n_id_nik`**, **`l10n_id_buyer_document_number`** (Char), `l10n_id_buyer_document_type`, `l10n_id_tku`, `l10n_id_pkp` (compute stored dari `vat`), `l10n_id_kode_transaksi` | `addons/l10n_id_efaktur_coretax/{__manifest__,models/res_partner}.py` | NIK dan nomor dokumen pembeli adalah PII kelas tinggi dan **masuk cakupan tahap 1** |
| `requirements.txt` Odoo 19.0: `cryptography==42.0.8 ; python_version >= '3.12'` | `requirements.txt` | AES-GCM (`cryptography.hazmat.primitives.ciphers.aead.AESGCM`) tersedia tanpa dependency baru. **Keberadaannya di image `odoo:19.0` belum terverifikasi** (§11) |

---

## 2. Model ancaman: apa yang dilindungi dan apa yang tidak

| Skenario | Tanpa W-08 (LUKS + TLS + WAL-G) | Dengan W-08 (opsi C) |
|---|---|---|
| Disk/VPS dicuri dalam keadaan mati | Terlindungi (LUKS) | Terlindungi |
| Dump `pg_dump` / salinan DB bocor (staf ops, laptop, salah kirim) | **Bocor** | Terlindungi (ciphertext; kunci tidak ada di DB) |
| Backup WAL-G + `walg_libsodium_key` bocor bersamaan | **Bocor** | Terlindungi bila `odoo_pii_key*` tidak ikut bocor |
| Role SQL salah grant (ETL, Metabase, exporter, akun baca ad-hoc) | **Bocor** | Terlindungi |
| Log query PostgreSQL (`log_min_duration_statement`, `auto_explain`) | **Bocor** (parameter plaintext) | Terlindungi (parameter sudah ciphertext) |
| DBA/superuser PostgreSQL | **Bocor** | Terlindungi selama ia tidak punya akses ke container Odoo / `/run/secrets` |
| Container Odoo dikompromikan (RCE) | Bocor | **Tetap bocor**: kunci ada di memori/secret Odoo |
| User Odoo yang berhak (Sales, Accounting, admin) membaca/ekspor CSV | Bocor sesuai hak | **Tetap sesuai hak**: ORM mendekripsi untuk user berhak. Kontrol = grup, record rule, audit (bukan enkripsi) |
| Browser POS (IndexedDB partner preload) | Plaintext di perangkat | **Tetap** untuk partner yang dimuat. Dikurangi dengan mengecualikan partner terlindungi dari preload (§4.5) |
| Filestore (PDF faktur, file ekspor Coretax) | Plaintext di atas LUKS | **Tetap** (di luar cakupan tahap 1, §4.8) |

Kesimpulan: W-08 menutup kelas kebocoran **"data keluar dari database"**. Kelas kebocoran **"aplikasi/user berhak disalahgunakan"** tidak ditutup oleh enkripsi apa pun yang tetap memungkinkan Odoo bekerja. Kelas itu tetap ditangani ADR-023 (akses), `efn_audit` (R-S10), dan minimisasi.

---

## 3. Opsi yang dipertimbangkan

| Kriteria | **A. Disk saja** (LUKS, status quo E14) | **B. `pgcrypto` di DB** | **C. AES-256-GCM di layer ORM Odoo** (direkomendasikan) | **D. TDE** (Percona `pg_tde`) | **E. Vault/tokenisasi di BFF** |
|---|---|---|---|---|---|
| Cara kerja | Enkripsi blok di disk host | `pgp_sym_encrypt/decrypt` lewat trigger + view, kunci dikirim di SQL/GUC sesi | Odoo mengenkripsi nilai sebelum `INSERT/UPDATE` dan mendekripsi saat dibaca ORM. Kunci hanya di `/run/secrets` container Odoo | Enkripsi file data + WAL di level server PostgreSQL | Odoo hanya menyimpan token. PII disimpan di DB platform (BFF) dan diambil saat cetak faktur |
| Dump/backup/role SQL bocor | Bocor | Terlindungi sebagian (kunci terlihat di `pg_stat_activity`, log, GUC; superuser bisa membaca) | **Terlindungi** | Dump logis (`pg_dump`) **plaintext**; hanya file fisik terlindungi | Terlindungi (PII tidak ada di Odoo) |
| Kompatibilitas Odoo standar | Penuh | Rendah: ORM membaca tabel langsung → tetap butuh perubahan ORM untuk dekripsi; trigger bentrok dengan flush ORM | Sedang: pencarian sebagian/sorting/group by atas field terenkripsi hilang; field turunan perlu dikosongkan (§4.2) | Penuh | Rendah: faktur, e-Faktur, POS butuh panggilan ke BFF; Odoo tidak bisa mencetak faktur saat BFF mati |
| Memenuhi keputusan #50 (Q-E3) | **Tidak** | Secara nama ya, secara substansi lemah | **Ya** (pola sama dengan BFF) | Tidak (bukan per kolom) | Ya (lebih dari yang diminta) |
| Konsistensi platform | — | Pola baru | **Format & key ring sama dengan BFF** (`docs/04 §7.2`) → test vector bersama | Mengganti distribusi PostgreSQL 16 → fork PG17 | Pola baru lintas service |
| Ketergantungan baru | — | Ekstensi `pgcrypto` (contrib resmi) | Tidak ada (`cryptography` sudah dibawa Odoo, perlu verifikasi image) | Distribusi Percona + upgrade major PG 16→17 | BFF menjadi jalur kritis untuk dokumen pajak |
| Biaya tenaga (kasar) | Rp0 | 2,5–4 minggu-engineer (±Rp12–36 jt) | **4–6 minggu-engineer (±Rp20–54 jt)** | 2–4 minggu devsecops + uji upgrade PG (±Rp10–36 jt) + risiko migrasi DB | 6–9 minggu-engineer (±Rp30–81 jt) |
| Waktu kalender | 0 | ±3 minggu | **±3 minggu** (G19, 2 engineer paralel di sebagian pekerjaan) | ±3–4 minggu + jendela upgrade DB | ±5–6 minggu, W-06 mundur 1 gelombang |
| Mudah dibalik | — | Sedang | **Ya**: job dekripsi balik memakai kunci yang sama (§4.13) | Sulit (balik distribusi DB) | Sulit |

**Alasan menolak B:** kunci harus sampai ke server PostgreSQL. Di sana kunci terlihat oleh superuser, `pg_stat_statements`/log (pola SEC-18), dan setiap sesi yang menyetel GUC. Odoo tetap perlu diubah agar membaca hasil dekripsi. Biayanya hampir sama dengan C, tetapi perlindungannya lebih lemah.
**Alasan menolak D:** setara dengan LUKS ditambah enkripsi WAL. PostgreSQL 16 komunitas tidak punya TDE (ADR-022 opsi C3), dan dump logis tetap plaintext. Pilihan ini mengganti distribusi database tanpa memenuhi "per kolom".
**Alasan menunda E:** minimisasi terbaik, tetapi menjadikan BFF syarat untuk mencetak faktur pajak dan ekspor Coretax. Itu bertentangan dengan pola "Odoo sumber dokumen pajak" (ADR-019/034). Opsi ini ditinjau ulang untuk tahap 2 bila DPO meminta Odoo tidak menyimpan HP sama sekali.

---

## 4. Keputusan (rekomendasi): opsi C, enkripsi di layer ORM Odoo

### 4.1 Cakupan tahap 1
> **⚠ Diralat 18 Okt 2026 — lihat §0.2.** Cakupan berlaku = S1+ (pelanggan, vendor individu, portal, karyawan; nama/alamat/kota/kode pos ikut; `pos.order.email/mobile` dienkripsi; protect-by-default). Tabel di bawah dipertahankan sebagai jejak keputusan awal.

**Kelas partner terlindungi** (`res.partner.efn_pii_protected`, Boolean, stored, index, `groups="base.group_system"`, `copy=False`). Nilainya **satu arah**: setelah `True` tidak pernah kembali `False` secara otomatis. Penanda menjadi `True` bila salah satu kondisi berikut terpenuhi:
1. partner adalah **member** (`efn_member_locked_at` terisi, riwayat OTP, atau order ber-`efn_otp_gate_state`; logika yang sama dengan `_efn_otp_member_partners`);
2. partner **dibuat kasir** lewat grup `efn_setup_pos.group_efn_cashier_contact_creation` (W-25: pelanggan individu dari POS);
3. partner **pembeli online** yang dibuat W-06. Penanda wajib diisi **di panggilan `create` yang sama**, sehingga tidak ada jendela waktu di mana data tersimpan plaintext;
4. ditandai manual oleh Superadmin (teraudit `efn_audit`, log kontrol).

| Model.field | Perlakuan untuk partner terlindungi | Alasan |
|---|---|---|
| `res.partner.phone` | **Dienkripsi** + blind index `efn_phone_bidx` | HP = identitas member (R-L01) |
| `res.partner.email` | **Dienkripsi** + `efn_email_bidx` | Kontak pribadi |
| `res.partner.vat` (NPWP) | **Dienkripsi** + `efn_vat_bidx` | Identitas pajak (W-06) |
| `res.partner.street`, `street2` | **Dienkripsi** (tanpa blind index) | Alamat |
| `res.partner.l10n_id_nik`, `l10n_id_buyer_document_number` (bila `l10n_id_efaktur_coretax` terpasang) | **Dienkripsi** + `efn_nik_bidx` (NIK saja) | NIK = data pribadi kelas tinggi |
| `res.partner.email_normalized` (mail) | **Dikosongkan** (compute dioverride → `False`) | Salinan plaintext. Akibatnya mail gateway/blacklist Odoo tidak berlaku untuk partner ini. Notifikasi pelanggan memang lewat BFF (WA/push), bukan email Odoo |
| `res.partner.phone_sanitized` (phone_validation) | **Dikosongkan** | Salinan plaintext + indeks ekspresi |
| `pos.order.email`, `pos.order.mobile` | **Tidak disalin** (compute → `False` bila partner terlindungi) | Minimisasi. Salinan kontak tidak dibutuhkan akuntansi |
| `efn_member_hash` | **Tetap** (dihitung dari HP hasil dekripsi) | Kunci join erasure BFF (`docs/04 §8.4`) tidak berubah |
| `name`, `city`, `zip`, `state_id`, `country_id` | **Tidak** dienkripsi di tahap 1 | Nama dibutuhkan untuk pencarian kasir dan dokumen. Kota/kode pos dibutuhkan laporan penjualan per wilayah. Dilindungi L/W/T. **Perlu persetujuan DPO (Q-PII1)** |
| `comment`, `mail.message`, `mail.tracking.value`, `ir.attachment`/filestore | **Tidak** (tahap 2) | Teks bebas & dokumen. Ditangani SOP dan `efn_audit` (sudah ter-mask) |
| Partner **non-terlindungi** (perusahaan, vendor, PT sendiri, karyawan) | **Tidak berubah** | Pencarian bebas Purchase/Accounting tetap jalan. Kontak vendor individu dan portal vendor (ADR-035) = tahap 2 (Q-PII5) |

Alternatif cakupan yang ditolak: **S2** semua partner individu (`is_company=False`) memutus pencarian kontak vendor individu dan email karyawan; **S3** semua partner memutus mail gateway dan pencarian vendor. **S1 (kelas terlindungi)** paling sempit tetapi menutup seluruh data pelanggan yang dimaksud keputusan #50.

### 4.2 Format ciphertext (identik dengan BFF, dibungkus teks)
> **⚠ Ditambah 18 Okt 2026 — §0.3:** format `efnpii2:` dengan AAD terikat id record (SEC-138). `efnpii1:` hanya transisional di dalam transaksi `create`.
- Kolom Odoo bertipe `varchar` (Char tanpa `size`), jadi ciphertext disimpan sebagai **teks**: `efnpii1:` ‖ base64 standar dari blob `docs/04 §7.2`: `versi_kunci (1 byte) ‖ IV 12 byte acak ‖ ciphertext ‖ tag 16 byte`.
  - `efnpii1:` = penanda **format** (bukan versi kunci). Versi kunci ada di byte pertama blob, sama dengan BFF.
  - Plaintext yang diawali `efnpii1:` **ditolak** saat tulis (`ValidationError`) agar penanda tidak bisa dipalsukan.
- **AAD** = `efn:odoo:<tabel>.<kolom>:v1` (mis. `efn:odoo:res_partner.phone:v1`). Prefix `odoo:` mencegah ciphertext BFF dipindahkan ke kolom Odoo atau sebaliknya.
  - AAD **tidak** mengikat id baris di tahap 1, karena id belum diketahui saat `INSERT`.
  - Residu: penyerang dengan hak tulis DB bisa menukar ciphertext antar-partner. Dampaknya integritas, bukan kerahasiaan, dan hak tulis DB sudah berarti kompromi penuh.
  - Pengikatan per baris (pola SEC-102 BFF) dievaluasi di tahap 2.
- Panjang: plaintext *n* byte → ±`8 + ⌈(29 + n)/3⌉×4` karakter (HP 13 digit ≈ 64 karakter).
- **Test vector bersama**: modul Odoo wajib mendekripsi blob yang dibuat `PiiCipher` BFF (kunci dan AAD uji yang sama), dan sebaliknya. Ini membuktikan format identik. Kuncinya beda di produksi.

### 4.3 Key ring & secret (dikelola Superadmin)
| Secret (`/run/secrets/…`) | Env | Isi | Pemegang | Catatan |
|---|---|---|---|---|
| `odoo_pii_key_v1`, `odoo_pii_key_v2`, … | `EFN_ODOO_PII_KEY_V<n>_FILE` (default `/run/secrets/odoo_pii_key_v<n>`) | 32 byte acak, base64 | **Hanya container `odoo`** (termasuk cron/worker Odoo) | Terpisah dari `pii_encryption_key*` BFF (ADR-024): kompromi Odoo tidak membuka PII BFF dan sebaliknya |
| — | `EFN_ODOO_PII_ACTIVE_KEY_VERSION` (non-rahasia, default 1) | versi untuk enkripsi baru | Odoo | Harus ada kuncinya |
| — | `EFN_ODOO_PII_RETIRED_VERSIONS` (non-rahasia) | daftar versi pensiun | Odoo | Start check: ada ciphertext berversi pensiun → alert `CRITICAL` (bukan dekripsi diam-diam) |
| `odoo_pii_bidx_key` | `EFN_ODOO_PII_BIDX_KEY_FILE` | 32 byte acak, base64 | Hanya `odoo` | **Bukan** `member_hash_pepper`. Pepper itu juga dipegang BFF dan ETL, sehingga ETL bisa membuat kamus HP |
| — | `EFN_ODOO_PII_REQUIRED` (non-rahasia; `true` di staging/prod) | wajib-kunci | `deploy-validate` | Produksi menolak deploy tanpa kunci aktif + kunci blind index |

- Kunci **tidak pernah** masuk DB, `ir.config_parameter`, log, image, atau repo. Dev/CI membuat kunci saat runtime (`scripts/gen-secrets.sh`, fixture acak). Tidak ada kunci contoh di repo (gitleaks).
- Kustodi: masuk daftar ADR-022 "Kustodi kunci" / `docs/04 §8.6` (hanya Superadmin) + **salinan offline tersegel di brankas Direksi** (keputusan 10 Okt, R-B11). **Kehilangan `odoo_pii_key*` berarti kehilangan HP/email/NPWP/alamat partner terlindungi secara permanen**, termasuk di semua backup.
- Pembacaan kunci mengikuti pola `member_hash.py`: cache per proses dikunci `(path, mtime, size)`, error berupa **kode** (`key_missing`, `key_invalid`, `version_unknown`), isi tidak pernah dicatat.

### 4.4 Blind index untuk pencarian exact
> **⚠ Ditambah 18 Okt 2026 — §0.4, §0.5:** blind index juga untuk `email_normalized` dan `identification_id`; pencarian nama memakai token terpisah dengan kunci `odoo_pii_name_key`.
- Subkunci per field: `k_f = HKDF-SHA256(ikm = odoo_pii_bidx_key, salt = "", info = "efn:odoo:bidx:<field>:v1", 32 byte)`.
- Nilai: `efn_<field>_bidx = hex(HMAC-SHA256(k_f, normalize_f(plaintext)))`, 64 hex, `index=True`, `groups="base.group_system"`, tidak dimuat ke POS, tidak diekspor ETL.
- Normalisasi (harus sama saat tulis dan saat cari):

| Field | `normalize_f` |
|---|---|
| `phone` | E.164 tanpa `+` lewat `efn_core.normalize_member_ref` (Indonesia `62…`). Nomor luar negeri: digit saja dengan kode negara. Gagal normalisasi → blind index kosong (hanya bisa ditemukan lewat nama) |
| `email` | `tools.email_normalize(value, strict=False)` (sama dengan `email_normalized` standar) |
| `vat` | digit saja (NPWP 15/16 digit) |
| `l10n_id_nik` | digit saja (16 digit) |

- Kebocoran yang diterima: kesetaraan nilai (dua partner dengan HP sama punya blind index sama). Ini dibutuhkan untuk deteksi duplikat.
- Rotasi `odoo_pii_bidx_key`: **hanya bila bocor**. Hitung ulang semua blind index dari plaintext hasil dekripsi (job batch). Selama job berjalan, pencarian exact bisa meleset beberapa menit, jadi dijalankan di luar jam operasional.

### 4.5 Perilaku pencarian, tampilan, dan POS
> **⚠ Diralat 18 Okt 2026 — §0.5, §0.6:** nama kini terenkripsi; `ilike` atas nama didukung sebagai **awalan kata ≥ 3 huruf** (AND multi-kata), nama lengkap exact lewat token `f`. Baris "Pencarian nama tetap `ilike`" dan "hanya nama yang cocok" di bawah tidak berlaku lagi.
| Operasi atas field terenkripsi (partner terlindungi) | Perilaku |
|---|---|
| Baca lewat ORM (form, laporan QWeb, faktur, ekspor CSV oleh user berhak, `_load_pos_data`) | **Plaintext** (didekripsi). Hak akses tetap menurut grup/record rule standar |
| Domain `=`, `in` (termasuk `_compute_same_vat_partner_id`, deteksi duplikat) | **Ditulis ulang** ke `efn_<field>_bidx` atas nilai yang dinormalisasi. Partner non-terlindungi tetap dicocokkan pada kolom asli: `(bidx = h) OR (NOT efn_pii_protected AND kolom = nilai)` |
| `ilike`, `like`, `=like`, `not ilike`, regex (`phone_mobile_search`), pencarian sebagian | **Tidak didukung** untuk partner terlindungi: tidak cocok, **tanpa error** (UI standar tetap jalan). Partner non-terlindungi tetap cocok seperti biasa |
| `!=`, `not in` | Diterjemahkan lewat blind index (negasi) — bila PoC menunjukkan rumit, **tidak didukung** dan didokumentasikan |
| `ORDER BY`, `group by`, pivot atas field terenkripsi | **Tidak bermakna** (urut/kelompok atas ciphertext). Tidak diblokir, didokumentasikan |
| POS `get_new_partner(config, domain, offset)` | Bila istilah pencarian **berbentuk HP lengkap / email lengkap / NPWP lengkap**, tambahkan leaf `OR` blind index exact. Pencarian nama tetap `ilike` |
| POS preload (`_load_pos_data_domain` / `get_limited_partners_loading`) | **Usulan:** partner terlindungi **tidak dipreload** ke browser kasir (kecuali partner order terbuka). Kasir menemukannya lewat pencarian HP lengkap. Fitur yang butuh member sudah nonaktif saat offline (ADR-017 Accepted), jadi offline tidak kehilangan fungsi member. **Keputusan PO + Ops (Q-PII3)** |
| `name_search` (`_rec_names_search` memuat email & vat) | Untuk partner terlindungi hanya nama yang cocok. Email/NPWP lengkap ditangani rewrite `=` di atas bila PoC memungkinkan |

### 4.6 Perilaku tulis & gagal-tertutup
- **Tulis**: semua jalur ORM yang menyimpan ke kolom (create, write, flush compute) mengenkripsi untuk partner terlindungi. Plaintext tidak pernah mencapai SQL, juga lewat `sudo()`.
- **Kunci aktif tidak terbaca** → modul masuk **mode terkunci**:
  1. tulis ke field terenkripsi → `UserError` (tidak ada fallback plaintext);
  2. baca → nilai pengganti tetap `"•••• (kunci PII tidak tersedia)"`, **bukan** `False`, agar form/compute tidak menimpa ciphertext dengan kosong;
  3. `efn_member_hash` dan blind index **tidak** dihitung ulang (compute menahan nilai lama, bukan mengosongkan);
  4. log `EFN_PII_KEYRING` level `CRITICAL` + alert.

  POS dan penjualan **tetap jalan** tanpa data kontak (prinsip CLAUDE.md: POS tidak berhenti karena komponen pendukung). Odoo **tidak** menolak start, karena menolak start berarti POS mati.
- **Ciphertext gagal autentikasi** (tag salah) → nilai pengganti yang sama + log hitungan (tanpa id partner di pesan publik, id hanya di log teknis tanpa nilai).

### 4.7 Yang TIDAK didukung (dicatat agar tidak ditagih sebagai bug)
> **⚠ Diralat 18 Okt 2026:** butir 1 kini juga mencakup **nama (infiks), kota, kode pos** dan berlaku untuk vendor individu & karyawan (field privat); butir 2 mencakup **urutan abjad nama** (§0.6 O1); butir 3 diperbaiki sebagian (pencarian pemilik email masuk tetap jalan lewat `email_normalized` terenkripsi + blind index; blacklist/mass mailing tetap tidak); butir 4 **tidak berlaku** (struk email tetap bisa, `pos.order.email` terenkripsi, 18-31).
1. Pencarian bebas / sebagian (`ilike`) atas HP, email, NPWP, NIK, alamat partner terlindungi, termasuk di daftar Kontak, POS, dan Sales.
2. Sorting, group by, pivot atas field tersebut.
3. Mail gateway Odoo (cocokkan email masuk ke partner), blacklist email/SMS Odoo, dan mass mailing Odoo ke partner terlindungi.
4. Kirim struk POS otomatis ke email yang disalin dari partner (`pos.order.email` kosong). Kasir masih bisa mengetik email manual bila fitur itu dipakai. **Rekomendasi:** struk digital lewat BFF (WA) di fase berikut.
5. SQL langsung (Metabase, query ad-hoc, laporan SQL) yang membaca kolom tersebut. Hasilnya ciphertext, dan memang itu tujuannya.
6. Dekripsi oleh service lain selain Odoo (BFF, ETL, OCR, Metabase tidak memegang kunci Odoo).

### 4.8 Dampak e-Faktur / Coretax
> **⚠ Diralat 18 Okt 2026 — §0.8:** XML Coretax **disimpan** (18-8) dan isinya dienkripsi aplikasi; usulan "dibuat sesuai permintaan lalu dihapus" tidak dipakai. NIK hanya di jalur permintaan faktur/B2B (18-7).
- `l10n_id_efaktur_coretax` (CE 19.0) membaca `vat`, `l10n_id_nik`, `l10n_id_buyer_document_*`, nama, dan alamat partner. **Asumsi:** pembacaan lewat ORM/QWeb (`data/efaktur_templates.xml`), sehingga nilai sudah didekripsi dan ekspor tetap lengkap. **Belum terverifikasi** (kode generator belum dibaca). PoC wajib mengekspor satu faktur partner terlindungi dan membandingkan XML-nya dengan partner biasa.
- `l10n_id_pkp` (compute stored dari `vat`) tetap benar karena compute membaca nilai hasil dekripsi. Field ini Boolean, bukan PII.
- **File ekspor XML dan PDF faktur** disimpan di filestore sebagai **plaintext** (di atas LUKS). Ini dokumen pajak 10 tahun (Q-P2). Tahap 1: diterima dan dicatat di inventaris PDP. Usulan: file ekspor Coretax dibuat **sesuai permintaan** dan dihapus setelah diunggah ke Coretax (bila Finance tidak wajib menyimpan salinan XML; **Q-PII6**). PDF faktur tetap disimpan.
- Validasi format NPWP/NIK berjalan atas plaintext sebelum enkripsi (constraint ORM), jadi tidak terdampak.

### 4.9 Dampak laporan
- Laporan Odoo (faktur, SO, laporan akuntansi `efn_account_reports` W-16) mencetak plaintext lewat ORM. Tidak ada perubahan.
- Pivot/list yang dikelompokkan per email/HP/NPWP **tidak bermakna** untuk partner terlindungi. Belum ada laporan EFN yang memakai pengelompokan itu (berdasarkan `docs/08` §5.3, §9). Functional wajib mengonfirmasi.
- **Ekspor CSV oleh user berhak** tetap menghasilkan plaintext. Ini kanal kebocoran yang sah-tetapi-berisiko. Tahap 2: audit ekspor `res.partner` di `efn_audit` (CE tidak mencatat ekspor; ±1–2 hari).

### 4.10 ETL ke ClickHouse
> **⚠ Ditambah 18 Okt 2026 — §0.7:** satu pengecualian sempit: view `efn_etl.partner_region` (partner_id, company_id, region_code) untuk laporan per kota domisili.
- **Tidak berubah dan dikunci.** `services/etl/sql/odoo-readonly-grants.sql` sudah tidak memberi akses ke `res_partner`, dan tidak memberi akses ke `pos_order.email/mobile` (komentar G13). `customer_id` = id partner (pseudonim).
- Aturan baru (usulan kontrak K-36-5):
  1. ETL **tidak pernah** di-grant kolom terenkripsi Odoo, `efn_*_bidx`, atau `efn_pii_protected`;
  2. ETL tidak pernah memegang `odoo_pii_key*` / `odoo_pii_bidx_key`;
  3. kolom pseudonim yang boleh dibaca hanya yang sudah ada (`customer_id`, `member_hash` dari BFF).
- Guard CI (usulan untuk data-AI/devsecops, ±0,5 hari): test yang menolak grant ke `res_partner` dan ke nama kolom terenkripsi/blind index di file grant (melengkapi `test_gl.py:55`).

### 4.11 Backup & pemulihan
> **⚠ Diputuskan 18 Okt 2026 (18-35):** off-site 35 hari + base backup bulanan 12 bulan → kunci pensiun disimpan **≥ 12 bulan + 30 hari** setelah ciphertext versi itu hilang dari semua backup.
- WAL-G (S4) setelah W-08 berisi ciphertext. Pemulihan membutuhkan **`walg_libsodium_key` + seluruh versi `odoo_pii_key*` yang masih dipakai data di backup itu**.
- **Kunci pensiun tidak boleh dimusnahkan** sebelum backup **tertua** yang memuat ciphertext versi itu kedaluwarsa. Rentang ini mencakup retensi base backup WAL-G (14 base backup harian, `docs/04 §8.16`), object lock `pg-backup`, dan **backup off-site** (wajib sebelum go-live, retensinya belum ditetapkan, Q-PII4). Kunci pensiun disimpan di brankas Direksi bersama salinan offline lain.
- Uji pemulihan 2×/tahun (ADR-022, disaksikan) harus mencakup: restore DB → Odoo membaca partner terlindungi → ekspor e-Faktur satu faktur.
- **Residu migrasi (bila E14 terpakai):** base backup dan arsip WAL **sebelum** migrasi tetap memuat plaintext sampai kedaluwarsa (±15 hari untuk WAL-G lokal; off-site menurut Q-PII4). Mempercepat penghapusan tidak dianjurkan, karena object lock dan s3-gateway sengaja menolak bypass retensi. Residu ini **diterima dan dicatat**.

### 4.12 Rotasi kunci (re-encrypt bertahap, tanpa downtime)
```mermaid
sequenceDiagram
    participant SA as Superadmin
    participant H as Host /opt/efn/secrets
    participant O as Odoo (cron efn_pii_reencrypt)
    participant DB as PostgreSQL efn_odoo
    SA->>H: buat odoo_pii_key_v2 (32 byte acak), salinan tersegel ke brankas
    SA->>O: deploy dengan V2_FILE terpasang + ACTIVE_KEY_VERSION=2 (rolling restart)
    Note over O: tulis baru = v2; baca = v1 atau v2
    loop batch keyset 500 baris, commit per batch
        O->>DB: SELECT id, kolom WHERE versi(kolom) <> 2 AND id > last
        O->>O: dekripsi (v1) lalu enkripsi (v2), AAD sama
        O->>DB: UPDATE … WHERE id = $1 AND kolom = <ciphertext lama> (optimistik)
    end
    O-->>SA: laporan angka per kolom & versi (tanpa nilai), log EFN_PII_KEYRING
    SA->>O: RETIRED_VERSIONS=1; cabut V1_FILE dari container
    Note over SA: kunci v1 TETAP di brankas sampai backup tertua yang memuat v1 kedaluwarsa (§4.11)
```
- Versi diperiksa di SQL tanpa dekripsi: `get_byte(decode(substring(kolom from 9), 'base64'), 0)` (`efnpii1:` = 8 karakter).
- Job menulis lewat **SQL langsung** dengan nilai yang dihitung di Python. Tujuannya agar tidak menaikkan `write_date`, tidak membuat ribuan baris `efn_audit`/chatter, dan aman berjalan bersamaan dengan POS (pola `reencrypt.ts` BFF). Satu entri ringkasan dicatat di `efn_audit` (log kontrol: siapa, kapan, jumlah baris, versi).
- Idempoten: dijalankan ulang = no-op. Jadwal: tahunan, saat Superadmin berganti (ADR-022 mitigasi 4), atau saat dicurigai bocor.

### 4.13 Migrasi in-place untuk data yang sudah ada
Hanya diperlukan bila database produksi dibuat **sebelum** W-08 (pengecualian E14 terpakai). Data dev/staging cukup diinstal ulang.

```mermaid
flowchart TD
    A[0. Rehearsal: restore backup prod ke staging, jalankan langkah 1-6, ukur durasi] --> B
    B[1. Deploy modul W-08, mode dual-read: baca plaintext lama + ciphertext] --> C
    C[2. Tandai kelas terlindungi: member, buatan kasir, pembeli] --> D
    D[3. Job enkripsi batch: plaintext -> efnpii1, hitung blind index, kosongkan email_normalized & phone_sanitized, NULL-kan pos_order.email/mobile partner terlindungi] --> E
    E{4. Query verifikasi = 0 baris plaintext?} -- tidak --> D
    E -- ya --> F[5. Mode strict: plaintext di partner terlindungi = alert]
    F --> G[6. Jendela malam sebelum 01:30: VACUUM FULL + REINDEX res_partner, pos_order]
    G --> H[7. Base backup WAL-G baru + catat tanggal residu backup lama]
    H --> I[8. Lead + security mencabut E14; inventaris PDP diperbarui]
```
- **Query verifikasi** (dijalankan role `efn_odoo` di psql; hasil angka saja):
  - `SELECT count(*) FROM res_partner WHERE efn_pii_protected AND ((phone IS NOT NULL AND phone NOT LIKE 'efnpii1:%') OR (email IS NOT NULL AND email NOT LIKE 'efnpii1:%') OR (vat IS NOT NULL AND vat NOT LIKE 'efnpii1:%') OR (street IS NOT NULL AND street NOT LIKE 'efnpii1:%') OR (street2 IS NOT NULL AND street2 NOT LIKE 'efnpii1:%') OR email_normalized IS NOT NULL OR phone_sanitized IS NOT NULL)` → **0**
  - `… OR l10n_id_nik / l10n_id_buyer_document_number …` bila Coretax terpasang → **0**
  - `SELECT count(*) FROM pos_order o JOIN res_partner p ON p.id = o.partner_id WHERE p.efn_pii_protected AND (o.email IS NOT NULL OR o.mobile IS NOT NULL)` → **0**
- **Kenapa `VACUUM FULL`/`REINDEX`:** UPDATE di PostgreSQL meninggalkan versi baris lama (plaintext) di halaman data sampai ditimpa. Indeks btree `vat` dan indeks ekspresi `phone_sanitized` juga menyimpan plaintext. `VACUUM FULL` menulis ulang tabel dan membangun ulang indeksnya, tetapi mengunci tabel (`ACCESS EXCLUSIVE`). Untuk data pilot (perkiraan ribuan partner, puluhan ribu order) diperkirakan menit. **Belum diukur**, sehingga rehearsal langkah 0 wajib. Selama kunci, POS tetap menerima order secara offline dan sinkron setelahnya. `pg_repack` tidak dipakai karena menambah ekstensi pihak ketiga.
- **Residu yang tidak bisa dihapus:** WAL/backup sebelum langkah 7 (§4.11), dan blok disk lama di bawah LUKS. Keduanya dilindungi enkripsi lapisan lain.
- **Jalan balik** (bila PoC/produksi gagal): job dekripsi balik (mode `--decrypt`) dengan kunci yang sama, lalu uninstall. ±0,5 hari. Pengecualian E14 dipakai lagi dengan tanggal baru.

### 4.14 Erasure UU PDP (`efn_privacy`)
- Tetap memakai `efn_member_hash` (tidak berubah). Anonimisasi menulis `False` ke field terenkripsi, sehingga ciphertext dan blind index ikut NULL. `efn_erased` tetap satu-satunya penanda (SEC-31).
- Blind index **wajib** ikut dikosongkan (test). Kalau tidak, partner teranonim masih bisa dicocokkan dengan HP.
- Partner pembeli W-06: nama/NPWP/alamat yang melekat di faktur 10 tahun tetap ada di dokumen (PDF/Coretax), sesuai `PDP_DATA_INVENTORY` §2.2d (menunggu Legal).

### 4.15 Kontrak observability (log, bukan event RabbitMQ)
W-08 tidak menambah queue. Status key ring dilaporkan lewat satu baris log terstruktur yang dibaca Promtail/Loki, sejajar dengan pola `EFN_AUDIT_SINK`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "EFN_PII_KEYRING",
  "type": "object",
  "required": ["schema_version", "event", "db", "active_version", "loaded_versions", "status"],
  "additionalProperties": false,
  "properties": {
    "schema_version": { "const": 1 },
    "event": { "enum": ["startup_check", "reencrypt_run", "migration_run", "locked", "decrypt_failure"] },
    "db": { "type": "string", "maxLength": 64 },
    "active_version": { "type": ["integer", "null"], "minimum": 1, "maximum": 255 },
    "loaded_versions": { "type": "array", "items": { "type": "integer", "minimum": 1, "maximum": 255 } },
    "status": { "enum": ["ok", "locked", "retired_in_use", "plaintext_residual", "error"] },
    "counts": {
      "type": "object",
      "description": "jumlah baris per kolom per versi, mis. {\"res_partner.phone\": {\"1\": 812, \"plain\": 0}}",
      "additionalProperties": { "type": "object", "additionalProperties": { "type": "integer", "minimum": 0 } }
    },
    "error_code": { "enum": ["key_missing", "key_invalid", "version_unknown", "auth_failed", null] }
  }
}
```
Tidak pernah memuat nilai, ciphertext, kunci, id partner, atau nama. Alert usulan (devsecops): `EfnOdooPiiLocked` (status `locked`, kritis), `EfnOdooPiiPlaintextResidual` (`counts.*.plain > 0` di mode strict), `EfnOdooPiiRetiredInUse`, `EfnOdooPiiDecryptFailure` (> 0 per 15 menit).

---

## 5. Perubahan yang diusulkan ke ADR-022 (ditulis lead/architect setelah ADR ini disetujui; ADR-022 belum diubah)
> **⚠ 18 Okt 2026:** ADR ini Accepted; perubahan **sudah dicatat** di ADR-022 bagian "Revisi 18 Okt 2026 (ADR-036 Accepted)" dengan cakupan §0.2 (bukan teks §5.1 di bawah) dan kunci ketiga `odoo_pii_name_key` (§0.9).

### 5.1 Keputusan 5: ganti paragraf "Batas cakupan"
> **Kolom PII Odoo (revisi 10 Okt 2026, Q-E3; rincian ADR-036):** untuk partner **terlindungi** (member, pelanggan buatan kasir, pembeli online), `res.partner.phone/email/vat/street/street2` (+ `l10n_id_nik`, `l10n_id_buyer_document_number`) dienkripsi **di ORM Odoo** dengan AES-256-GCM (format `docs/04 §7.2`, AAD `efn:odoo:<tabel>.<kolom>:v1`, key ring `odoo_pii_key_v<n>` milik Odoo saja), dengan blind index HMAC (`odoo_pii_bidx_key`) untuk pencarian exact. Salinan turunan (`email_normalized`, `phone_sanitized`, `pos.order.email/mobile`) dikosongkan. Nama, kota, dan kode pos partner, partner non-pelanggan, teks bebas, dan filestore tetap dilindungi L/W/T (tahap 2: ADR-036 §4.1).

### 5.2 Tabel "Manajemen kunci & rotasi": tambah dua baris
| Kunci | Lokasi | Pemegang | Rotasi | Cara | Bila hilang |
|---|---|---|---|---|---|
| `odoo_pii_key_v<n>` | `/run/secrets` | Odoo saja | Tahunan / bila bocor / ganti Superadmin | Key ring, cron re-encrypt (ADR-036 §4.12); kunci pensiun disimpan sampai backup tertua kedaluwarsa | **Kehilangan PII partner terlindungi permanen** → salinan tersegel di brankas wajib |
| `odoo_pii_bidx_key` | `/run/secrets` | Odoo saja | Hanya bila bocor | Hitung ulang blind index dari plaintext | Pencarian exact gagal sampai dihitung ulang (data tidak hilang) |

### 5.3 "Kustodi kunci": tambah `odoo_pii_key*` dan `odoo_pii_bidx_key` ke daftar kunci yang hanya dipegang Superadmin.

### 5.4 Pengecualian E14: revisi baris
| Kolom | Isi baru (usulan) |
|---|---|
| Hop / penyimpanan | PII partner Odoo (HP member dan pelanggan buatan kasir) belum dienkripsi kolom **selama pilot ritel satu outlet**, hanya bila DB produksi dibuat sebelum W-08 |
| Kontrol kompensasi (tambahan) | (a) **dilarang** menyimpan profil pembeli online (W-06) dan PII baru dari kanal online; (b) partner member tidak dipreload ke POS bila W-08 sudah ada di kode (§4.5); (c) rehearsal migrasi di staging sebelum tanggal kedaluwarsa; (d) tanggal mulai pilot dan tanggal migrasi dicatat untuk menghitung residu backup |
| Kedaluwarsa | **Mana yang lebih dulu:** migrasi W-08 terverifikasi di produksi (§4.13 langkah 8) · sebelum outlet ritel ke-2 go-live · sebelum W-06 aktif · **31 Jan 2027** |
| Residu setelah dicabut | Backup/WAL sebelum migrasi sampai kedaluwarsa (§4.11), diterima |

### 5.5 Q-E3: tandai "dijawab 10 Okt (dienkripsi), rincian ADR-036".

---

## 6. Usulan revisi kontrak `docs/04` (untuk lead; `docs/04` belum diubah)
> **⚠ Diralat 18 Okt 2026 — §0.9** (K-36-1..K-36-8 diubah, K-36-9..K-36-11 baru).

| ID | Bagian | Usulan |
|---|---|---|
| K-36-1 | §7.2 | Tambah subbagian **"Kolom PII Odoo"**: teks `efnpii1:` + base64(blob §7.2), AAD `efn:odoo:<tabel>.<kolom>:v1`, key ring `odoo_pii_key_v<n>` / `EFN_ODOO_PII_KEY_V<n>_FILE`, `EFN_ODOO_PII_ACTIVE_KEY_VERSION`, `EFN_ODOO_PII_RETIRED_VERSIONS`, `EFN_ODOO_PII_REQUIRED`; plaintext berawalan `efnpii1:` ditolak; test vector bersama BFF ↔ Odoo |
| K-36-2 | §7.2 | Rumus blind index Odoo: `hex(HMAC-SHA256(HKDF(odoo_pii_bidx_key, info "efn:odoo:bidx:<field>:v1"), normalize(v)))` + tabel normalisasi §4.4; tegaskan **berbeda** dari `member_hash` (§1) yang tidak berubah |
| K-36-3 | §7.3 | Baris baru: secret `odoo_pii_key_v<n>`, `odoo_pii_bidx_key` hanya di-mount ke service `odoo`; **tidak** ke BFF, ETL, OCR, Metabase, `odoo-rpc-gateway` |
| K-36-4 | §8.4 | Erasure Odoo: anonimisasi wajib mengosongkan ciphertext **dan** semua `efn_*_bidx` partner; `efn_member_hash` tetap kunci join |
| K-36-5 | §7.3 / ETL | ETL dilarang di-grant kolom terenkripsi Odoo, `efn_*_bidx`, `efn_pii_protected`; guard CI |
| K-36-6 | §8.6 | Daftar kunci Superadmin + `odoo_pii_key*`, `odoo_pii_bidx_key` |
| K-36-7 | §10 (W-06) | Metode Odoo pembuat partner pembeli (kontrak W-06, milik lead) wajib menulis `efn_pii_protected=True` dalam **create yang sama**; BFF tidak menyimpan profil (kecuali `partner_ref` opak) dan tidak mencatatnya di log; respons tidak mengembalikan PII |
| K-36-8 | §8.15 (observability) | Baris log `EFN_PII_KEYRING` (skema §4.15) + 4 alert |

---

## 7. Keputusan yang dibutuhkan dari lead / PO
> **⚠ Status 18 Okt 2026 — §0.10.**

| # | Keputusan | Opsi | Rekomendasi architect | Dampak biaya/waktu |
|---|---|---|---|---|
| **L-36-1** | **Pilot ritel menunggu W-08, atau jalan dengan E14 lalu migrasi?** (melanjutkan PP-3) | (a) pilot menunggu W-08; (b) pilot jalan dengan E14, migrasi sebelum outlet ke-2; (c) pilot jalan, migrasi "kapan sempat" | **Gabungan a/b:** PP-3 tetap (pilot **tidak** ditahan). **Bila DB produksi dibuat setelah W-08 merge, pasang W-08 sejak awal** (E14 tidak terpakai). Ini kemungkinan besar karena staging/produksi belum ada dan W-08 dijadwalkan G19 (±3 minggu). Bila pilot mulai lebih dulu: (b) dengan E14 versi §5.4 (batas keras 31 Jan 2027). (c) ditolak | (a/b-awal): Rp0 tambahan. (b-migrasi): +±1–1,5 minggu-engineer (rehearsal, jendela malam, verifikasi) + residu backup ±15 hari s.d. retensi off-site |
| L-36-2 | Cakupan kelas partner | S1 terlindungi / S2 individu / S3 semua | **S1** (§4.1) | S2/S3 menambah ±1–2 minggu (vendor, mail gateway) dan merusak alur Purchase |
| L-36-3 | Partner terlindungi tidak dipreload ke POS | ya / tidak | **Ya** (minimisasi; offline tidak kehilangan fungsi member karena ADR-017) | ±1 hari; kasir wajib mengetik HP lengkap (Q-PII3) |
| L-36-4 | `pos.order.email/mobile` tidak disalin untuk partner terlindungi | tidak disalin / dienkripsi | **Tidak disalin** | Lebih murah dari enkripsi (±0,5 hari lebih hemat) |
| L-36-5 | Estimasi W-08 direvisi 3–5 → **4–6** minggu-engineer | — | Terima. Tambahan berasal dari field turunan yang terverifikasi di §1.3 (`email_normalized`, `phone_sanitized`, salinan `pos.order`, NIK Coretax) | +Rp5–9 jt |
| L-36-6 | Gerbang PoC 3 hari di awal G19 | lanjut / tidak | **Wajib**: bila kriteria PoC §8.2 gagal, lead memutuskan ulang (opsi E tahap 1 untuk pembeli saja, atau perluasan E14) **sebelum** W-06 dijadwalkan | PoC 0,5 minggu-engineer |

---

## 8. Spesifikasi singkat untuk efn-odoo-engineer (setelah fit-gap efn-odoo-functional)

### 8.1 Prasyarat: fit-gap efn-odoo-functional W-08 (belum ada di `docs/08`)
Functional mendaftar dan memverifikasi di CE 19.0:
1. Semua field stored di `res.partner` dan model lain yang **menyalin** phone/email/vat/street/NIK (selain yang di §1.3), termasuk `mail.tracking.value` (apakah field ini di-`tracking`), `account.move`, `sale.order`, `l10n_id_efaktur_coretax.document`.
2. Bentuk domain yang dikirim klien POS 19 ke `get_new_partner` (field apa, operator apa) dan batas `get_limited_partners_loading`.
3. Cara generator Coretax membaca partner (ORM/QWeb vs SQL).
4. Laporan/filter standar yang dipakai klien atas field tersebut.

### 8.2 PoC (gerbang, ±3 hari)
Kriteria lulus semuanya:
1. Hook enkripsi menangkap **semua** jalur tulis ke kolom (create, write, flush compute stored, `sudo()`, import), dibuktikan dengan test yang memeriksa SQL mentah (`SELECT phone FROM res_partner`) = `efnpii1:…`.
2. Baca ORM, form, `web_read`, `_load_pos_data`, laporan QWeb faktur, dan ekspor Coretax menghasilkan plaintext.
3. Domain `=`/`in` ditulis ulang ke blind index; `ilike` tidak error.
4. Test vector BFF ↔ Odoo lulus.
5. Overhead < 5 % pada `_load_pos_data` 1.000 partner (diukur, bukan diasumsikan).

Kandidat mekanisme yang diuji (keputusan akhir milik engineer, dicatat di README):
- **M1**: subclass `fields.Char` (enkripsi di konversi ke kolom, dekripsi di konversi ke record). Field didefinisikan ulang di `_inherit`, dengan cek "terlindungi" per record.
- **M2**: override `create`/`write`/`_write`/flush di model. Hanya dipakai bila M1 tidak menangkap jalur compute.

Tidak boleh memakai monkeypatch registry atau menyalin kode OCA/Cybrosys (ADR-031). Referensi desain boleh dicatat di README.

### 8.3 Modul (usulan nama, `19.0.1.0.0`, LGPL-3)
> **⚠ Ditambah 18 Okt 2026:** `efn_pii` berisi mixin abstrak `efn.pii.mixin` (daftar field cakupan per model, mode proteksi `flag` / `always` / `related`, guard create/write SEC-137, rebind `efnpii2` SEC-138, redaksi tracking, hitungan startup per model) + `tools/name_tokens.py`. Modul baru: **`efn_pii_account`** (auto_install `account`: `invoice_partner_display_name`), **`efn_pii_hr`** (auto_install `hr`), **`efn_region`** (master kab/kota + `efn_region_id` + view ETL). `efn_pii_base_vat` (sudah ada) tetap. Setiap README memuat ADR-036, R-L01/R-T18/R-T19, dan "yang belum".
| Modul | Isi | `depends` |
|---|---|---|
| `efn_pii` | Key ring (`EFN_ODOO_PII_*`), `PiiCipher` Python (format §4.2), blind index (§4.4), field/hook, `efn_pii_protected` + aturan penandaan, override `res.partner` (enkripsi, blind index, rewrite domain, `email_normalized`/`phone_sanitized` kosong), mode terkunci, cron/aksi `efn_pii_reencrypt` (rotasi + migrasi + `--decrypt`), log `EFN_PII_KEYRING`, start check | `base`, `mail`, `phone_validation`, `efn_core`, `efn_privacy` |
| `efn_pii_pos` (auto_install) | `pos.order.email/mobile` tidak disalin; rewrite `get_new_partner`; pengecualian preload; penandaan dari OTP/kasir (koordinasi dengan `efn_pos_otp_gate`, `efn_setup_pos`) | `efn_pii`, `point_of_sale`, `efn_pos_otp_gate` |
| `efn_pii_l10n_id` (auto_install) | `l10n_id_nik`, `l10n_id_buyer_document_number` terenkripsi + `efn_nik_bidx`; test ekspor Coretax | `efn_pii`, `l10n_id_efaktur_coretax` |

Setiap model/field baru: `company_id` tidak diperlukan, karena field ada di `res.partner` yang sudah ber-rule. `ir.model.access.csv` untuk model teknis (bila ada). README memuat ADR-036, risiko R-L01/R-L10/R-T08, dan "yang belum".

### 8.4 Test wajib (tag `efn`)
1. SQL mentah = ciphertext untuk setiap field cakupan, juga lewat `sudo()` dan import CSV.
2. Partner non-terlindungi tetap plaintext dan bisa dicari `ilike`.
3. Penandaan satu arah. Klien tidak bisa menulis `efn_pii_protected`; hanya admin teraudit.
4. Blind index cocok untuk HP format lokal `0812…`/`+62…`/`62…` (normalisasi), email beda huruf besar/kecil, dan NPWP bertanda baca.
5. Mode terkunci: baca mengembalikan nilai pengganti, tulis `UserError`, `efn_member_hash` dan blind index tidak hilang.
6. Rotasi v1 → v2 idempoten dan bersamaan dengan write. Kunci pensiun yang masih dipakai memicu status `retired_in_use`.
7. Migrasi: query verifikasi §4.13 = 0, `--decrypt` mengembalikan data.
8. Erasure: ciphertext dan blind index NULL, `efn_erased` saja.
9. Tour POS: cari member dengan HP lengkap ditemukan; HP sebagian tidak menemukan partner terlindungi; partner terlindungi tidak ada di preload.
10. Log tidak memuat plaintext/ciphertext (pola uji redaksi G15).
11. Ekspor Coretax partner terlindungi = isi sama dengan partner biasa.

### 8.5 Untuk agent lain
- **devsecops:** secret `odoo_pii_key_v1` + `odoo_pii_bidx_key` di `gen-secrets.sh`/`secrets.list`, mount hanya ke `odoo`, `deploy-validate` (`EFN_ODOO_PII_REQUIRED`), runbook `pii-key-rotation.md` (bagian Odoo) + `key-custody.md`, alert §4.15, langkah uji restore. ±1,5–2,5 hari.
- **data-ai:** guard CI grant ETL (K-36-5). ±0,5 hari.
- **bff:** tidak ada perubahan untuk W-08. W-06 mengikuti K-36-7.
- **QA:** test negatif SQL mentah di stack dev + rehearsal migrasi di staging.
- **security-reviewer:** review hook tulis (jalur bypass), mode terkunci, dan log.

---

## 9. Biaya & waktu (opsi C)
> **⚠ Digantikan 18 Okt 2026 — §0.12** (±9–11,5 minggu-engineer untuk cakupan S1+). Tabel di bawah = cakupan awal S1.
| Pekerjaan | Agent | Minggu-engineer (kasar) |
|---|---|---|
| Fit-gap W-08 (§8.1) | efn-odoo-functional | 0,5 |
| PoC gerbang (§8.2) | efn-odoo-engineer | 0,5 |
| `efn_pii` inti (key ring, cipher, hook, penandaan, blind index, mode terkunci) | efn-odoo-engineer | 1–1,5 |
| Rewrite pencarian + `efn_pii_pos` | efn-odoo-engineer | 0,5–1 |
| Rotasi/migrasi/dekripsi-balik + log | efn-odoo-engineer | 0,5–1 |
| `efn_pii_l10n_id` + uji Coretax | efn-odoo-engineer | 0,25–0,5 |
| Secret, deploy-validate, alert, runbook | efn-devsecops-engineer | 0,25–0,5 |
| QA + guard ETL | efn-qa-engineer, efn-data-ai-engineer | 0,5 |
| **Total** | | **±4–6 (±Rp20–54 jt)** |
| *Tambahan bila migrasi in-place produksi (E14 terpakai)* | odoo + devsecops + QA | *+1–1,5* |

Kalender: ±3 minggu di G19 dengan 1 engineer Odoo penuh (+ functional di minggu 1). Tanpa engineer Odoo kedua, W-06 (G20) mundur sebanding (R-T13).

## 10. Konsekuensi
- **Positif:** memenuhi keputusan #50 dan prasyarat W-06. Dump, backup, role SQL, dan log query Odoo tidak lagi membuka kontak pelanggan. Pola dan format sama dengan BFF (satu cara rotasi, satu runbook). Minimisasi naik: salinan `pos.order` dan preload POS berkurang. Bisa dibalik.
- **Negatif / utang teknis:** pencarian kasir hanya exact. Mail gateway Odoo tidak berlaku untuk pelanggan. Bertambah dua secret yang kehilangannya fatal (disiplin brankas, R-B11). Hook ORM harus diuji ulang setiap upgrade Odoo mayor (R-T13). Filestore, teks bebas, nama, dan kontak vendor belum terlindungi kolom (tahap 2).
- **Dampak dokumen:** ADR-022 (§5), `docs/04` (§6), `PDP_DATA_INVENTORY.md` §2.2/§2.2a/§2.2d (legenda **K-Odoo**, residu backup, `pos.order` salinan kontak), `docs/08` (fit-gap W-08), runbook `pii-key-rotation.md`, `key-custody.md`, `backup-restore.md`, README `efn_privacy`/`efn_pos_otp_gate`.

## 11. Risiko terkait
| Risiko | Kaitan |
|---|---|
| R-L01 (UU PDP) | Dimitigasi: "langkah teknis pelindungan" untuk data pelanggan di Odoo |
| R-L10 (PII pembeli di Odoo) | Prasyarat mitigasi. Akses staf tetap luas, jadi masih perlu record rule + audit |
| R-T08 (backup/DR) | Bertambah: restore butuh key ring lengkap. Kunci pensiun disimpan sesuai retensi backup |
| R-B11 (bus factor Superadmin) | Bertambah: dua kunci fatal baru di tangan satu orang. Salinan tersegel wajib sebelum DB produksi dibuat |
| R-S10 (insider) | Tidak ditutup oleh enkripsi (user berhak tetap membaca). Tahap 2: audit ekspor |
| R-T13 (kapasitas Odoo engineer) | Hook ORM = beban porting per rilis mayor |
| Usulan **R-T18** (baru) | *Hook enkripsi ORM terlewati jalur tulis baru* (modul standar/kustom menulis lewat SQL atau jalur flush yang tidak tertangkap) → plaintext diam-diam. Mitigasi: query verifikasi §4.13 dijadwalkan harian (`EfnOdooPiiPlaintextResidual`), test SQL mentah di CI. Skor usulan 3×4 = 12 |

## 12. Pertanyaan terbuka untuk klien
> **⚠ 18 Okt 2026:** Q-PII1 → **ditolak** (nama/kota/kode pos dienkripsi, 18-32); Q-PII2 → **disimpan terenkripsi** (18-31); Q-PII3 → **setuju** (18-30); Q-PII4 → **35 h + bulanan 12 bln** (18-35); Q-PII5 → **tahap 1** (18-33); Q-PII6 → **XML disimpan** (18-8). Pertanyaan konfirmasi baru: Q-PII7..Q-PII12 (§0.13).
| ID | Pertanyaan | Pemutus | Default bila tidak dijawab | Memblokir |
|---|---|---|---|---|
| Q-PII1 | Nama pelanggan (dan kota/kode pos) tetap plaintext di Odoo (terlindungi disk/backup/TLS), karena dibutuhkan pencarian kasir dan dokumen. Setuju? | DPO (Legal) | Ya (tahap 1) | Tanda tangan ADR |
| Q-PII2 | Salinan email/HP di order POS tidak disimpan lagi untuk pelanggan (struk email otomatis dari Odoo tidak tersedia). Setuju? | Finance + Ops | Ya | — |
| Q-PII3 | Kasir mencari member/pelanggan dengan **HP lengkap** (bukan sebagian), dan pelanggan terdaftar tidak dimuat ke kasir sebelum dicari. Setuju? | PO + Ops Outlet | Ya | SOP & pelatihan kasir |
| Q-PII4 | Berapa lama backup **off-site** disimpan? (menentukan lamanya kunci pensiun disimpan dan residu plaintext bila migrasi in-place) | IT + Finance | Sama dengan lokal (±15 h) | Runbook rotasi |
| Q-PII5 | Kapan kontak vendor individu, karyawan, dan akun portal vendor (ADR-035) ikut dienkripsi (tahap 2)? | DPO + IT | Bersama W-13 (portal vendor V1) | — |
| Q-PII6 | File ekspor XML Coretax perlu disimpan di Odoo setelah diunggah, atau cukup PDF faktur + bukti unggah? | Finance + Tax | Disimpan (10 th, plaintext di filestore) | — |

## 13. Belum terverifikasi
- Mekanisme hook ORM Odoo 19.0 yang menangkap **semua** jalur tulis termasuk flush compute stored (§8.2). Belum dibaca di `odoo/orm/fields*.py` 19.0; PoC yang memutuskan.
- Bentuk domain pencarian klien POS 19 ke `get_new_partner` (kode JS belum dibaca) dan batas `get_limited_partners_loading`.
- Apakah generator `l10n_id_efaktur_coretax` membaca partner lewat ORM/QWeb (asumsi §4.8) dan di mana file ekspornya disimpan.
- Apakah `phone`/`email`/`vat` partner di-`tracking` (salinan di `mail.tracking.value`) di 19.0.
- Ketersediaan `cryptography` (versi dengan `AESGCM`) di image `odoo:19.0@sha256:dd9013e…`. Requirements Odoo menyebut 42.0.8, tetapi image resmi memasang paket Ubuntu. Cek saat build (`python3 -c "from cryptography.hazmat.primitives.ciphers.aead import AESGCM"`), sama seperti cek `passlib` di Dockerfile.
- Durasi `VACUUM FULL` dan overhead dekripsi (§4.13, §8.2): belum diukur.
- Retensi backup off-site (Q-PII4) dan object lock `pg-backup` aktual di staging.

## 14. Kapan ditinjau ulang
- *(18 Okt 2026)* AC-1 gagal dengan GIN (pindah ke T1), pengguna meminta pencarian infiks (ADR baru: trigram, kebocoran lebih besar), Q-PII7 dijawab K-b, atau PP pelaksana UU PDP mengatur teknik pseudonimisasi/enkripsi tertentu.
- PoC §8.2 gagal (pilih ulang M1/M2/opsi E).
- Upgrade Odoo mayor (20.0): hook ORM wajib diuji ulang.
- Portal vendor (W-13) atau pesan-antar (W-05, alamat penerima) mulai dibangun: tahap 2.
- Platform memakai KMS (OpenBao/Vault, ADR-022 "Kapan ditinjau ulang").
- Insiden kunci, pergantian Superadmin, atau tahunan bersama audit PDP/DPIA (W-28).
