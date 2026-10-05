# ADR-023: Akses Odoo Hanya via WireGuard + 2FA Wajib untuk User Internal

- **Status:** Proposed
- **Tanggal:** 2026-10-05 (revisi 2026-10-06: opsi konektivitas outlet open source & biaya realistis, atas pertanyaan klien "tidak ada solusi open source?")
- **Pemutus (klien):** IT + Operasional Outlet + Finance (biaya perangkat outlet)
- **Referensi review:** SEC-01 (High), SEC-03, SEC-21, SEC-28, R-S03 (skor 20), R-T02, Q-55 (internet outlet)
- **Terkait:** ADR-002 (Traefik edge), ADR-004 (4 node, WireGuard mesh), ADR-017 (OTP saat POS offline), ADR-022 (enkripsi), `docs/04 §7.4`, `docs/02 §5`, `infra/wireguard/*`, `docs/07_TCO_TEMPLATE.md` §J

> **Ringkasan manajemen.** Sistem ERP (Odoo) hanya bisa diakses lewat jaringan privat terenkripsi **WireGuard**, ditambah verifikasi 2 langkah untuk semua user internal. **WireGuard sendiri open source dan gratis**; angka Rp1,5–3 juta per outlet pada versi sebelumnya adalah **harga perangkat keras router**, bukan lisensi. Revisi ini menyediakan jalur yang semuanya open source: (a) **aplikasi WireGuard resmi langsung di perangkat kasir** — **Rp0 perangkat keras**, cocok untuk outlet dengan ≤ 3 perangkat yang perlu membuka Odoo; (b) **firmware OpenWrt** di router yang sudah ada (bila didukung) atau router murah berbasis OpenWrt **±Rp400–900 rb** (indikatif, validasi vendor), untuk outlet dengan banyak perangkat. Biaya pemasangan ±30 menit per perangkat (a) atau ±1 jam per outlet (b). Bila internet/tunnel putus, kasir tetap berjualan offline (ADR-017).

## Konteks
- `CLAUDE.md`: *"Odoo tidak pernah diakses langsung dari internet."* Router publik Odoo sudah ditutup di kode (SEC-01 **Fixed** di review r2); sisa: isolasi subnet outlet vs staf (SEC-21) dan firewall hub (SEC-28).
- Pengguna Odoo: kasir/POS & KDS di outlet, staf warehouse/CK, back-office, manajemen. Super-App pelanggan **tidak** memakai Odoo langsung (lewat BFF).
- Odoo POS 19 bisa beroperasi offline setelah sesi dibuka — dasar ADR-017.
- **Pertanyaan klien 6 Okt 2026:** "Router WireGuard Rp1,5–3 jt/outlet — tidak ada solusi open source?" Jawaban: WireGuard (protokol, modul kernel Linux, aplikasi resmi Windows/Android/macOS/iOS/Linux) sudah open source (kernel GPL-2.0; aplikasi Android Apache-2.0 — terverifikasi dari berkas `COPYING` repositori `WireGuard/wireguard-android`). Biaya yang disebut adalah perangkat keras. Revisi ini menimbang opsi tanpa/dengan perangkat keras murah.

## Opsi yang dipertimbangkan

### A. Model akses
| Opsi | Kelebihan | Kekurangan | Biaya |
|---|---|---|---|
| **A1. Hanya via WireGuard** (Traefik entrypoint khusus di IP WireGuard) | Permukaan serangan dari internet = 0 untuk Odoo; credential stuffing mustahil tanpa kunci WG | Setiap perangkat/outlet harus terhubung WG | Lihat B |
| A2. Internet + path allowlist + TOTP + rate limit | Tanpa perangkat/aplikasi tambahan | `/web/login`, `/pos/`, `/websocket` tetap terekspos; bug pra-autentikasi Odoo langsung terekspos | 3–5 hari konfigurasi |
| A3. Zero-trust SaaS (Cloudflare Access/Tailscale SaaS) | UX baik, SSO | Pihak ketiga di jalur ERP (transfer lintas negara, PDP), biaya per user/bulan, ketergantungan vendor | USD 5–7/user/bulan (indikatif) |

### B. Cara outlet terhubung (bila A1) — **semua opsi open source**
Harga = **indikatif Oktober 2026, belum divalidasi vendor**; kurs USD 1 = Rp16.000.

| Opsi | Apa | Perangkat keras / outlet | Tenaga pasang | Cocok untuk | Kekurangan |
|---|---|---|---|---|---|
| **B-a. Klien WireGuard resmi di perangkat** (Windows, Android, Linux; juga macOS/iOS) | Aplikasi resmi gratis dipasang di tiap perangkat yang perlu membuka Odoo (PC/tablet kasir, KDS, customer display terpisah) | **Rp0** | ±30 menit/perangkat (impor konfigurasi via QR/berkas) | Outlet dengan **≤ 3 perangkat** yang perlu Odoo | Kunci per perangkat harus dikelola; kasir bisa mematikan aplikasi bila tidak dikunci (lihat kontrol); setiap perangkat baru = peer baru |
| **B-b. OpenWrt di router** — flash router yang sudah ada **bila didukung** daftar perangkat OpenWrt, atau beli router murah yang **sudah berbasis OpenWrt** (mis. kelas GL.iNet entry-level) | Satu tunnel untuk seluruh LAN/VLAN POS outlet | **Rp0** (router lama didukung) atau **±Rp400–900 rb** (router OpenWrt baru) | ±1 jam/outlet | Outlet dengan **banyak perangkat**, KDS, kiosk, printer jaringan | Flash firmware pihak ketiga dapat membatalkan garansi & berisiko gagal (brick) → lebih aman beli yang sudah OpenWrt; router outlet jadi aset yang dikelola IT; semua perangkat di VLAN POS ikut terpercaya |
| B-c. Mini-PC / Raspberry Pi + Debian atau OpenWrt sebagai **gateway outlet** | Perangkat kecil menjalankan WireGuard + routing/NAT untuk VLAN POS di belakang router ISP | Raspberry Pi 4/5 + PSU + casing + kartu/SSD **±Rp1,2–2 jt**; mini-PC kelas N100 **±Rp2–3,5 jt** | ±2 jam/outlet | Outlet yang router ISP-nya terkunci dan tidak bisa diganti; outlet besar yang butuh layanan lokal tambahan (cache, monitoring) | Paling mahal di antara opsi OSS; kartu SD rentan rusak (pakai SSD); butuh skill Linux |
| B-d. Router komersial dengan WireGuard (MikroTik RouterOS v7) — *bukan open source*, dicatat sebagai pembanding | | ±Rp700 rb–1,5 jt (kelas entry hAP) hingga Rp1,5–3 jt (dual-WAN/LTE) | ±1 jam | Bila vendor jaringan klien sudah standar MikroTik | RouterOS proprietary |

**Komponen manajemen peer (opsional, terpisah dari B):**
| Opsi | Lisensi (diverifikasi 6 Okt 2026) | Kelebihan | Kekurangan | Kapan |
|---|---|---|---|---|
| **D1. `wg` polos + skrip `infra/wireguard/gen-peer.sh`** (sudah ada) | WireGuard GPL-2.0 / skrip internal | Tidak ada komponen server baru; konfigurasi transparan & teraudit di repo infra | Onboarding/revokasi manual; tidak ada dashboard; skala terbatas | **Default** hingga ±50 outlet / ±150 peer |
| D2. **Headscale** (implementasi server koordinasi Tailscale, self-hosted) | **BSD-3-Clause** (berkas `LICENSE` repo `juanfont/headscale`) | ACL terpusat, NAT traversal otomatis, onboarding cepat dengan klien Tailscale | README menyatakan ruang lingkup sempit: *"a single Tailscale network (tailnet), suitable for a personal use, or a small open-source organisation"*; memakai klien Tailscale (sebagian GUI klien di beberapa OS **belum terverifikasi** open source); komponen baru yang harus dijaga & di-backup | Bila > 50 outlet dan tim IT kecil |
| D3. **NetBird** (self-hosted) | **Campuran**: repo BSD-3-Clause **kecuali** direktori `management/`, `signal/`, `relay/` = **AGPLv3** (dikutip dari README repo `netbirdio/netbird`) | Dashboard, ACL, SSO/IdP, klien sendiri (BSD-3) untuk Windows/Android/Linux | Server AGPLv3: aman untuk pemakaian internal tanpa modifikasi yang didistribusikan, tetapi modifikasi server yang diakses pihak lain wajib dibuka (cek Legal); 3 komponen server baru | Bila butuh dashboard & SSO dan Legal menyetujui AGPL |

### Trade-off per jenis perangkat outlet
Kunci desain: tunnel **split** — `AllowedIPs = <IP WG edge>/32` (lihat `infra/wireguard/peer-outlet.conf.example`), sehingga hanya trafik ke Odoo yang lewat tunnel; trafik LAN (printer) dan internet (QRIS/EDC bank) tidak.

| Perangkat | Butuh akses Odoo? | B-a (klien di perangkat) | B-b/B-c (router/gateway) |
|---|---|---|---|
| PC kasir Windows (POS di browser) | Ya | Aplikasi WireGuard Windows sebagai *service*; user kasir non-admin tidak dapat menonaktifkannya (konfigurasi via kebijakan — **verifikasi PoC**) | Otomatis |
| Tablet/terminal **Android POS** | Ya | Aplikasi WireGuard Android + setelan sistem **Always-on VPN** & **Block connections without VPN**; perangkat POS Android bermerek kadang memblokir aplikasi VPN / Play Store — **cek per model** | Otomatis (paling aman untuk perangkat terkunci vendor) |
| **KDS** (layar dapur, browser) | Ya | +1 peer per layar | Otomatis |
| **Kiosk self-order** (`pos_self_order` mode kiosk) | Ya | +1 peer per kiosk; mode kiosk Android harus mengizinkan VPN | Otomatis |
| Customer display (layar kedua di PC yang sama) | Ya, lewat PC kasir | Tidak perlu peer tambahan | Otomatis |
| Customer display di perangkat terpisah | Ya | +1 peer | Otomatis |
| **Printer ePOS** (Epson, IP LAN) | **Tidak langsung**: browser POS mengirim cetak langsung ke IP printer di LAN (`pos.printer.epson_printer_ip`, F-07 `docs/08`), bukan dari server Odoo | Tetap jalan karena split tunnel tidak menyentuh LAN — **asal** `AllowedIPs` tidak mencakup subnet LAN outlet. Catatan: halaman POS HTTPS → printer ePOS perlu mode HTTPS printer (sertifikat printer) — **[B] PoC**, sama untuk B-a dan B-b | Sama |
| Timbangan/EDC bank | Tidak (EDC lewat jaringan bank) | Tidak terdampak | Tidak terdampak (pastikan VLAN POS tetap punya internet langsung) |
| IoT Box Odoo | Tidak dipakai (IoT = Enterprise, `docs/08` F-07) | — | — |

Aturan praktis: hitung **perangkat yang membuka Odoo** (kasir + KDS + kiosk + display terpisah). **≤ 3 → B-a; > 3, atau ada perangkat Android terkunci vendor, atau outlet butuh kontrol terpusat → B-b.**

### Perbandingan biaya per outlet (indikatif, validasi vendor)
| Skenario outlet | B-a | B-b (router lama didukung OpenWrt) | B-b (router OpenWrt baru) | B-c | Router komersial (versi lama ADR) |
|---|---|---|---|---|---|
| Kiosk/toko kecil: 1 kasir | **Rp0** + 30 mnt | Rp0 + 1 jam | Rp0,4–0,9 jt + 1 jam | Rp1,2–3,5 jt | Rp1,5–3 jt |
| Café: 2 kasir + 1 KDS | **Rp0** + 1,5 jam | Rp0 + 1 jam | Rp0,4–0,9 jt | Rp1,2–3,5 jt | Rp1,5–3 jt |
| Resto: 3 kasir + 2 KDS + kiosk + 2 printer | Rp0 + 3 jam, 6 peer | Rp0 + 1 jam | **Rp0,4–0,9 jt** + 1 jam | Rp1,2–3,5 jt | Rp1,5–3 jt |
| Supermarket: 6–10 kasir | Rp0, 6–10 peer (beban kelola tinggi) | Rp0 | **Rp0,4–0,9 jt** (pilih model dual-WAN bila ada 4G backup: ±Rp1–2 jt) | Rp1,2–3,5 jt | Rp1,5–3 jt |

Backup 4G (Q-55) adalah biaya terpisah (modem/router LTE + paket data) untuk semua opsi; di B-a dapat memakai tethering/hotspot ponsel outlet sebagai darurat.

## Keputusan (rekomendasi, Proposed)
**A1 + B hibrid open source + D1:**
1. **Traefik**: entrypoint `wg-https` yang hanya listen di alamat WireGuard node edge; router `odoo` & `odoo-ws` hanya pada entrypoint itu **dan** middleware allowlist sumber langsung (RemoteAddr). Allowlist dipisah sesuai SEC-21: `internal-erp` (subnet outlet + staf + node) untuk Odoo, `internal-ops` (staf IT/back-office saja) untuk `bi.`/`ops.`/`status.`. Rate limit ketat login; blok `/xmlrpc`, `/jsonrpc`, `/json/2`, `/web/database/*` (defense in depth).
2. **DNS & sertifikat**: `erp.<domain>` hanya di-resolve ke IP WireGuard (split DNS atau entri `hosts`). Sertifikat ACME **DNS-01** sehingga browser POS memakai CA publik — tidak perlu memasang CA internal di perangkat outlet. Catatan: `DNS = 10.88.0.1` di konfigurasi klien membuat resolusi DNS perangkat bergantung pada tunnel; pada B-a lebih aman memakai entri `hosts`/DNS lokal agar internet (QRIS, EDC) tidak ikut mati saat tunnel putus — diputuskan devsecops di PoC.
3. **Pengalamatan (FINAL — keputusan lead gelombang 5, docs/04 §8.10)**: outlet `10.88.0.0/16` (satu `/29` per outlet untuk B-b/B-c, `/32` per perangkat untuk B-a), staf `10.89.0.0/16`, hub `10.88.0.1`. Diimplementasi di `infra/wireguard/*`, Traefik `internal-only` (outlet+staf) / `internal-ops` (staf), default `scripts/deploy.sh` & `deploy.yml` (`EFN_WG_OUTLET_CIDR`, `EFN_WG_STAFF_CIDR`, `EFN_WG_BIND`). Alokasi lain (mis. `10.66/16` di draf lama) tidak dipakai. Firewall hub (nftables): peer outlet **hanya** ke `<IP WG edge>:443`; antar-peer drop; ke subnet Docker drop; SSH hanya dari subnet staf IT (SEC-28).
4. **Hub WireGuard** di node edge (tidak menambah SPOF baru selain Traefik); hub kedua saat edge HA.
5. **Outlet**: **default B-a** (klien WireGuard resmi di perangkat) untuk outlet dengan **≤ 3 perangkat** yang membuka Odoo; **B-b** (OpenWrt — router lama bila didukung, atau router OpenWrt baru ±Rp400–900 rb) untuk outlet dengan **> 3 perangkat**, perangkat Android POS yang tidak bisa memasang VPN, kiosk, atau kebutuhan VLAN POS terpisah dari Wi-Fi tamu. B-c hanya bila router ISP tidak bisa diganti. Laptop staf back-office/remote = B-a. Kunci privat **dibuat di perangkat**; hanya public key yang dikirim ke hub (`gen-peer.sh --pubkey`, SEC-28); inventaris peer (outlet, perangkat, pemilik, tanggal) di repo infra **tanpa** kunci privat.
6. **Manajemen peer = D1** (`wg` + `gen-peer.sh`) sampai ±50 outlet; evaluasi D2 Headscale (BSD-3) atau D3 NetBird (server AGPLv3 — butuh persetujuan Legal) setelahnya.
7. **Kontrol perangkat untuk B-a**: Android = Always-on VPN + block without VPN; Windows = tunnel sebagai service, user kasir tanpa hak admin; checklist pemasangan di runbook `wireguard-peer-onboarding.md` (devsecops).
8. **2FA wajib**: `auth_totp` + `auth_totp_mail` (**terverifikasi** 19.0: `auth_totp.policy` = `employee_required`). User ber-2FA tidak bisa RPC dengan password → integrasi server-to-server memakai **API key** per service user (ADR-024).
9. **POS**: sesi POS dibuka oleh user ber-2FA saat online; pergantian kasir via PIN `pos_hr` (CE).

## Perilaku saat internet atau tunnel outlet putus
- Tunnel putus = POS offline; POS 19 mencatat order lokal dan sinkron saat tunnel kembali; **ADR-017 berlaku**: OTP/promo member/poin/MGM/pembayaran gateway (`efn_gateway`, ADR-026) nonaktif.
- Tidak bisa saat offline: membuka sesi POS baru, login user baru, sinkron stok/harga.
- EDC bank & QRIS statis bank memakai internet langsung, **tidak** lewat WireGuard (split tunnel). QRIS dinamis via BFF (ADR-026) butuh Odoo online → ikut nonaktif saat offline.
- WireGuard pulih otomatis setelah ganti uplink (keepalive 25 s).

## Dampak ke ADR-017
Tidak mengubah keputusan (opsi a). Metrik "% waktu offline per outlet" diukur juga dari hub (`wireguard_latest_handshake_seconds`; alert `EfnOutletTunnelDown` > 5 menit pada jam buka). Pada B-a, metrik per perangkat (bukan per outlet).

## Konsekuensi
- **Positif:** menutup SEC-01/R-S03 secara struktural; biaya perangkat keras turun dari Rp1,5–3 jt/outlet menjadi **Rp0** (B-a) atau **±Rp0,4–0,9 jt** (B-b); seluruh stack open source.
- **Negatif:** B-a menambah jumlah peer & beban support per perangkat; dua prosedur (B-a & B-b) harus didokumentasikan; flash OpenWrt pada router lama berisiko garansi/brick; hub tetap komponen kritis.
- **Dampak dokumen:** `docs/07_TCO_TEMPLATE.md` §J (baris konektivitas outlet) diperbarui; `docs/02 §5` (label "router outlet atau klien perangkat"); runbook devsecops: `wireguard-peer-onboarding.md` (B-a Android/Windows, B-b OpenWrt), `outlet-tunnel-down.md`.

## Risiko terkait
R-S03, R-T02, R-O06 (SOP kasir), R-T07 (hub kritis), SEC-21 (LAN outlet terpercaya → VLAN POS), SEC-28 (firewall hub).

## Pertanyaan terbuka untuk klien
- **Q-55** Internet outlet: provider, backup 4G, **merek/model router yang sudah ada** (untuk cek dukungan OpenWrt).
- **Q-W1** Jumlah outlet & **jumlah perangkat yang membuka Odoo per outlet** (kasir, KDS, kiosk, display) saat go-live dan 12 bulan → menentukan B-a vs B-b per outlet.
- **Q-W2** Siapa memasang (IT internal / vendor)? SLA penggantian perangkat?
- **Q-W3** User Odoo eksternal (akuntan, konsultan pajak, supplier)? Portal tidak dibuka ke internet di desain ini.
- **Q-W4 (baru)** Merek/model perangkat Android POS & kiosk: apakah mengizinkan aplikasi VPN dan Always-on VPN?
- **Q-W5 (baru)** Bila > 50 outlet: setuju evaluasi Headscale/NetBird? (NetBird server AGPLv3 → perlu Legal.)

## Yang belum terverifikasi
Harga perangkat (semua indikatif — **validasi vendor**); daftar dukungan OpenWrt untuk router yang ada di outlet; perilaku Always-on VPN di perangkat POS Android bermerek; kunci aplikasi WireGuard Windows untuk user non-admin; printer ePOS dari halaman HTTPS; dukungan klien Headscale per OS (dokumentasi tidak dapat diambil saat penulisan). Situs `wireguard.com` tidak dapat diakses dari lingkungan penulis; daftar platform klien resmi berdasarkan pengetahuan umum.

## Kapan ditinjau ulang
Bila > 50 outlet atau > 150 peer (D2/D3), bila Odoo portal publik dibutuhkan, atau setelah 3 bulan pilot bersama ADR-017.
