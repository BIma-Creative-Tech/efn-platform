# Security Review — PR #155 gabung/pisah meja oleh kasir POS (2026-11-23)

Reviewer: efn-security-reviewer (read-only). Branch `fix/pos-table-management`, worktree `efn-platform-postable`.
Objek: `services/odoo/addons/efn_setup_pos/models/restaurant_table.py` (baru, 43 baris) dan
`services/odoo/addons/efn_setup_pos/tests/test_table_merge_access.py`.

## Ringkasan eksekutif

- Elevasi `sudo` dibatasi dengan benar ke field `parent_id`, ke POS User non-manager, dan ke meja di lantai POS milik company user yang sesinya terbuka. Tidak ditemukan jalan eskalasi Critical/High: kasir tidak bisa mengubah field lain, tidak bisa menyentuh meja di lantai yang tidak bertaut ke POS company-nya, dan `write_uid` tetap kasir (jejak audit tetap ada).
- **Blocker merge (SEC-01, Medium):** `set_parent_id` adalah method publik di kelas `efn_*` dan **belum terdaftar di `RPC_ALLOWLIST`**. Akibatnya gerbang statis `efn_core/tests/test_rpc_surface.py::test_public_methods_are_allowlisted` akan merah.
- Tiga temuan Low: argumen `config_id` tidak divalidasi sebelum CE dijalankan di bawah `sudo`; syarat sesi `state != 'closed'` lebih longgar daripada pola proyek (`opened`); test belum menutup skenario lintas company.
- Verdict: **belum layak merge** sampai SEC-01 diperbaiki. SEC-02 sebaiknya dikerjakan sekalian karena perubahannya kecil. SEC-03 dan SEC-04 boleh menyusul.

## Tabel temuan

| ID | Severity | File:baris | Judul | Risiko |
|---|---|---|---|---|
| SEC-01 | Medium (blocker CI/kebijakan) | `efn_setup_pos/models/restaurant_table.py:34`; `efn_core/tests/test_rpc_surface.py:37,715-726` | Override publik `set_parent_id` tidak ada di `RPC_ALLOWLIST` | SEC-58, R-S (least privilege) |
| SEC-02 | Low | `restaurant_table.py:34-37` | `config_id` dari pemanggil tidak dicek, padahal diteruskan ke CE di bawah `sudo` (data balikan `_load_pos_data_read`) | PLAUSIBLE, kebocoran data minor |
| SEC-03 | Low | `restaurant_table.py:29` | Sesi dianggap terbuka bila `state != 'closed'` (termasuk `opening_control`/`closing_control`), tidak konsisten dengan `_efn_check_otp_caller` (`opened`) | R-S least privilege |
| SEC-04 | Low | `tests/test_table_merge_access.py:42-43,65-76` | Test negatif belum mencakup multi-company, sesi yang sudah ditutup, `config_id` milik company lain, dan jalur manajer | regresi |
| INFO-01 | Info | `restaurant_table.py:25-32` | Pemeriksaan tidak terikat ke POS yang sedang dioperasikan pemanggil: kasir bisa gabung/pisah meja POS lain di company yang sama selama POS itu bersesi terbuka | diterima (setara perilaku CE) |
| INFO-02 | Info | `restaurant_table.py:25-26` | Lantai (`restaurant.floor`) tanpa `company_id`; satu lantai bisa ditaut ke POS lintas PT | konfigurasi |

## Detail

### SEC-01 — `set_parent_id` belum di allowlist RPC (Medium, blocker)

Bukti: `scan_public_methods()` (test_rpc_surface.py:669-690) memindai `efn_*/**/*.py`. Untuk kelas dengan `_inherit = "restaurant.table"`, scanner mengembalikan model `restaurant.table`, dan method `set_parent_id` tidak diawali `_`. Pada baris 719 hanya `ORM_OVERRIDES` (`create`, `write`, ...) yang dikecualikan. Grep `set_parent_id|restaurant.table` di `efn_core` tidak menemukan entri, sehingga `test_public_methods_are_allowlisted` akan gagal dengan pesan `efn_setup_pos/models/restaurant_table.py: restaurant.table.set_parent_id`. Override `write` aman karena `write` termasuk `ORM_OVERRIDES`. Gerbang registry (baris 771) tidak terpicu karena method juga didefinisikan di kelas CE.

Ini bukan sekadar urusan administrasi CI. Method itu memang RPC-callable dan kini punya cabang `sudo`, jadi SEC-58 mewajibkan reviewer mencatat cek aksesnya di allowlist.

Perbaikan: tambahkan entri berikut ke `RPC_ALLOWLIST`.
```python
# efn_setup_pos (PR #155): override CE; sudo hanya parent_id bila _efn_cashier_may_link
# (grup POS user non-manager + meja & induk di lantai POS company user bersesi terbuka); selain itu ACL CE.
("restaurant.table", "set_parent_id"): "_efn_cashier_may_link (override CE, sudo hanya parent_id)",
```
Setelah itu jalankan `test_rpc_surface` di container.

### SEC-02 — `config_id` tidak divalidasi sebelum CE berjalan di bawah `sudo` (Low, PLAUSIBLE)

`set_parent_id(parent_id, config_id)` meneruskan `config_id` apa adanya ke `super(..., self.sudo())`. Rujukan dari pemanggil menyebut bahwa CE 19 mengembalikan `_load_pos_data_read(...)`. Isi persis method itu tidak bisa saya verifikasi, karena source CE tidak ada di host. Dampak yang mungkin terjadi:
- `restaurant.table` dan `restaurant.floor` tidak punya `company_id` maupun record rule, dan POS User memang punya ACL baca ke keduanya. Karena itu field meja yang dikembalikan sebenarnya sudah bisa dibaca kasir tanpa `sudo`. Kebocoran data meja tidak terjadi.
- Bila CE mem-`browse(config_id)` untuk memilih field atau membaca data turunan config, di bawah `sudo` config milik PT lain ikut terbaca tanpa record rule company. Pola serupa di proyek ini selalu memvalidasi config (`_efn_check_otp_caller`: `check_access('read')` + `company_id in env.user.company_ids` + sesi `opened`).

Skenario: kasir PT IMD memanggil `call_kw restaurant.table.set_parent_id([t2], t1, <id config PT lain>)`. Meja t1/t2 lolos cek karena ada di lantai PT IMD, lalu CE menjalankan `_load_pos_data_*` dengan config PT lain di bawah `sudo`. Dampak maksimum yang masuk akal adalah metadata config dan daftar field, bukan PII.

Perbaikan: di cabang `sudo`, wajibkan `config = env['pos.config'].browse(config_id)` termasuk `open_configs` yang dihitung `_efn_cashier_may_link`, dan `config` itu bertaut ke `floor_id` meja (`tables.floor_id.pos_config_ids`). Bila tidak memenuhi, jatuhkan ke `super()` tanpa `sudo` (AccessError standar). Cara termudah: `_efn_cashier_may_link(parent, config_id=None)` membatasi `configs` ke `config_id` bila diberikan.

### SEC-03 — definisi "sesi terbuka" terlalu longgar (Low)

`("state", "!=", "closed")` juga meloloskan `opening_control` (sesi dibuat tetapi kas pembuka belum dikonfirmasi) dan `closing_control` (sedang ditutup). Pola proyek (`efn_pos_otp_gate/models/pos_config.py:83-85`) memakai `current_session_id.state == 'opened'`. Dampaknya kecil: kasir bisa gabung/pisah meja saat sesi di tahap kontrol. Meski begitu, sebaiknya disamakan agar mudah diaudit.

Perbaikan: ganti dengan `("state", "=", "opened")` atau `"in", ("opened",)`. Catatan: `_open_session()` di test membuat sesi ber-state default `opening_control`, jadi test perlu memanggil `session.action_pos_session_open()` (atau set state) bila filter diubah.

### SEC-04 — cakupan test negatif (Low)

Lantai "asing" di test (baris 42) tidak bertaut ke POS mana pun, sehingga belum membuktikan isolasi multi-company. Tambahkan:
1. POS PT kedua (mis. `company_smd` atau company test) dengan sesi **terbuka** dan meja di lantainya. Kasir yang hanya punya akses PT IMD harus kena AccessError saat `write({'parent_id': False})` dan saat `set_parent_id`.
2. Sesi ditutup lalu kasir mencoba pisah meja: harus AccessError.
3. `set_parent_id` dengan `config_id` PT lain: harus ditolak (setelah SEC-02).
4. Manajer POS tetap bisa melakukannya lewat jalur standar tanpa `sudo` (regresi `has_group` manager).
5. Satu `write` campuran: `{'parent_id': False, 'seats': 2}` harus AccessError (bukti cek `set(vals) == {'parent_id'}`).

### INFO-01 — tidak terikat ke sesi milik user / POS yang dioperasikan

Jawaban untuk pertanyaan 2: mensyaratkan "sesi milik user ini" **tidak disarankan**. Dengan `pos_hr`, satu user terminal dipakai banyak karyawan, dan `pos.session.user_id` hanya mencatat pembuka sesi, sehingga syarat itu akan memutus operasi normal tanpa menambah keamanan yang berarti. Di CE, POS User juga bisa membuka POS mana pun yang bersesi di company-nya lewat UI, jadi kemampuan menggabung meja POS lain di company sendiri setara dengan perilaku standar. Penguncian yang proporsional adalah mengikat ke `config_id` yang dikirim frontend (SEC-02). Itu cukup untuk konsistensi, tanpa mengikat ke identitas user.

### INFO-02 — lantai tanpa `company_id`

`restaurant.floor` di CE 19 tidak punya `company_id` (README modul baris 209). Bila admin menautkan satu lantai ke POS PT A dan PT B, kasir PT A yang bersesi terbuka bisa memisah meja yang juga dipakai PT B. Ini masalah konfigurasi, bukan celah kode. Pertimbangkan constraint di `pos.config`/`restaurant.floor` yang menolak satu lantai ditaut ke POS dengan company berbeda (tiket terpisah, di luar PR ini).

## Jawaban pertanyaan reviewer

1. **Eskalasi.**
   - `write({'parent_id': False})` lewat RPC/JSON-2 ke meja POS company lain ditolak, karena `configs` difilter `company_id in env.user.company_ids` dan setiap meja wajib bertaut ke config bersesi terbuka (baris 25-32), lalu fallback ke ACL CE.
   - `env.user.company_ids` dipakai alih-alih `env.companies`, sama dengan pola `_efn_check_otp_caller`. Artinya user multi-company boleh bertindak di company yang diizinkan baginya walaupun company itu tidak aktif di switcher. Ini dapat diterima.
   - Meja `active=False` tetap dicek lewat `floor_id`, dan dampaknya nihil.
   - Lantai yang ditaut ke banyak POS: lihat INFO-02.
   - `config_id` lintas company: lihat SEC-02.
   - Siklus: jalur `write` hanya mengizinkan nilai falsy. Jalur `set_parent_id` tetap melalui `_has_cycle` milik CE, dan error membatalkan transaksi.
   - ID meja yang tidak ada: `floor_id` kosong, cek gagal, lalu jatuh ke jalur non-`sudo` (error standar).
   - Recordset kosong: `bool(tables)` False, jadi tidak ada elevasi.
2. **Sesi terbuka.** Syarat ini cukup asalkan diperketat ke `opened` (SEC-03) dan diikat ke `config_id` (SEC-02). Jangan mengikat ke user pemilik sesi (INFO-01).
3. **Nilai balikan di bawah `sudo`.** Data meja tidak bocor (ACL baca CE, tanpa record rule). Satu-satunya jalur yang masih mungkin adalah config asing di SEC-02 (PLAUSIBLE, belum bisa dicek terhadap source CE).
4. **Kebijakan.**
   - Least privilege: terpenuhi. Hanya satu field, kondisi diperiksa sebelum `sudo`, `write_uid` tetap pemanggil.
   - Allowlist RPC: wajib ditambah (SEC-01).
   - Polanya selaras dengan `_efn_check_otp_caller`, `_efn_gateway_check_caller`, dan `_efn_kds_check_device` (cek grup + company + sesi, lalu `sudo` sempit), kecuali dua hal: belum ada `check_access` pada config dan filter state berbeda.
   - Konvensi `company_id` + record rule: tidak berlaku, karena tidak ada model baru.

## Kontrol yang sudah benar

- Elevasi hanya untuk `set(vals) == {"parent_id"}` dengan nilai falsy pada `write`. Write campuran atau field lain jatuh ke ACL CE (diuji: `seats`).
- Cek grup memakai `has_group`. Manajer dan `env.su` tidak dielevasi (tidak ada `sudo` yang tidak perlu).
- `_efn_cashier_may_link` berawalan `_`, jadi tidak bisa dipanggil RPC.
- `all(...)` per meja, termasuk meja induk, sehingga satu meja asing di recordset menggagalkan elevasi seluruh batch.
- `sudo()` tidak mengganti `uid`, jadi `write_uid` dan chatter tetap mencatat kasir.
- README modul mendokumentasikan gap, tambalan, dan hal yang belum terverifikasi (alur `pos_hr` di browser).

## Belum terverifikasi

- Isi persis `set_parent_id` / `_load_pos_data_read` CE 19 (source tidak ada di host; perlu dicek di image `odoo:19`).
- Test belum saya jalankan (read-only review). Kesimpulan SEC-01 berasal dari pembacaan logika scanner, bukan hasil run.
