# efn_online_pos_sale — guard POS untuk SO kanal online (SEC-105)

Modul jembatan **auto-install** (`efn_online_order` + `pos_sale`). Tujuan: pesanan online (SO ber-
`efn_online_order_id`) tidak bisa dimuat, di-settle, atau diberi uang muka di POS, sehingga barang online hanya
keluar lewat wizard serah terima berkode ambil. ADR-034, ADR-031; re-review G17 SEC-105, docs/04 L-19 (b).
Risiko: R-S10, R-O05.

- `sale.order._load_pos_data_domain` + `("efn_online_order_id", "=", False)`.
- `sale.order.load_sale_order_from_pos` (override metode publik CE `pos_sale`, terdaftar di `RPC_ALLOWLIST`) menolak
  SO online.
- Jaring terakhir tetap di `efn_online_order` (tanpa ketergantungan `pos_sale`): `pos.order.line.create` menolak
  `sale_order_origin_id` SO online; `stock.move._action_done` menolak move SO online ke pelanggan di luar wizard.

Kenapa modul terpisah: override `pos_sale` hanya efektif bila berada di atas `pos_sale` dalam MRO (butuh
`depends`), sedangkan `efn_online_order` tidak boleh menarik `pos_sale` + `sale_management` ke semua instalasi.

Belum: UI daftar SO di POS tetap bisa menampilkan SO lain; tidak ada test tour POS khusus untuk SO online.
