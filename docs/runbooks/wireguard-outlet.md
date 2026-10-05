# Prosedur: WireGuard hub, onboarding outlet & staf (akses Odoo/POS dan dashboard internal)

Kontrak: `docs/04_SERVICE_CONTRACTS.md` §7.4, ADR-023 (A1 + B3), SEC-01/SEC-21/SEC-28. Semua komponen open source: WireGuard (kernel + `wireguard-tools`, klien resmi Windows/Android/Linux/macOS), OpenWrt (router outlet), nftables.

## Desain

| Item | Nilai |
|---|---|
| Hub | `wg0` di node edge (yang menjalankan Traefik), UDP 51820, alamat `10.88.0.1` (+ `10.89.0.1`). Template `infra/wireguard/wg0-server.conf.example` |
| Outlet | `10.88.0.0/16`, satu `/32` per router/perangkat mulai `10.88.1.1` → **hanya `erp.`** (middleware Traefik `internal-only`) |
| Staf | `10.89.0.0/16` (staf IT `10.89.0.0/24`, boleh SSH ke hub) → `erp.` + `bi.` + `ops.` + `status.` (`internal-ops`) |
| Traefik | Entrypoint **`wg-https`** di-publish **hanya** ke `10.88.0.1:443` (`EFN_WG_BIND`); publik (`websecure`) hanya di IP publik (`EFN_PUBLIC_BIND`). Router `erp.`/`bi.`/`ops.`/`status.` hanya di `wg-https` |
| Sertifikat | Let's Encrypt **DNS-01** (resolver `letsencrypt-dns`, secret `acme_dns_api_token`; Cloudflare: token *Zone → DNS → Edit* untuk zona domain saja). Browser POS tidak perlu CA internal; tidak perlu record A publik |
| DNS | Split DNS: `erp.<domain>`, `bi.`, `ops.`, `status.` → `10.88.0.1` (resolver di hub, dnsmasq OpenWrt, atau hosts file). **Jangan** buat record publik/di-proxy Cloudflare |
| Firewall hub | `infra/wireguard/efn-wg.nft` (SEC-28): peer → hanya `10.88.0.1:443` (+ DNS :53, SSH dari staf IT); peer↔peer, peer→container lain/DB/internet = drop. `ip_forward` tetap 1 (Docker) |
| Lapisan kedua | 2FA Odoo user internal, rate limit login 10/menit per peer, blok `/xmlrpc`, `/jsonrpc`, `/json/2`, sidecar Odoo hanya mTLS CN `traefik` |

## Pemasangan hub (sekali, Superadmin/IT)

```bash
apt install wireguard-tools nftables
umask 077; wg genkey | tee /etc/wireguard/server.key | wg pubkey > /etc/wireguard/server.pub
install -d /etc/efn && install -m 0644 /opt/efn/wireguard/efn-wg.nft /etc/efn/efn-wg.nft
cp /opt/efn/wireguard/wg0-server.conf.example /etc/wireguard/wg0.conf   # isi PrivateKey, chmod 600
mkdir -p /etc/systemd/system/docker.service.d
printf '[Unit]\nWants=wg-quick@wg0.service\nAfter=wg-quick@wg0.service\n' > /etc/systemd/system/docker.service.d/10-efn-wg.conf
systemctl daemon-reload && systemctl enable --now wg-quick@wg0 && systemctl restart docker
nft list table inet efn_wg            # aturan aktif
```
GitHub Environment variables: `EFN_PUBLIC_BIND=<IP publik host>`, `EFN_WG_BIND=10.88.0.1` (default), secret `ACME_DNS_API_TOKEN`. `deploy.sh` gagal bila `10.88.0.1` belum ada di host.

## Onboarding outlet — pola B1: router OpenWrt (direkomendasikan)

1. **Data**: kode outlet (`FNB-JKT-001`), PIC, model router (OpenWrt ≥ 22.03 / GL.iNet; MikroTik RouterOS 7 juga bisa dengan konsep yang sama).
2. **VLAN (wajib, SEC-21)**: pisahkan jaringan **POS/KDS/printer** (`lan_pos`) dari **Wi-Fi tamu** (`guest`). Wi-Fi tamu tidak pernah boleh masuk zona `pos` atau tunnel. Contoh lengkap: `infra/wireguard/openwrt/network.example` + `firewall.example`.
3. **Kunci di router**: `umask 077; wg genkey > /etc/wireguard/efn.key; wg pubkey < /etc/wireguard/efn.key` → kirim public key ke ops (boleh lewat chat; bukan rahasia).
4. **Alokasi & daftar** (ops, di hub): ambil IP berikutnya dari register (jangan pakai ulang IP yang dicabut < 30 hari):
   ```bash
   bash /opt/efn/wireguard/gen-peer.sh --pubkey '<PUBKEY_ROUTER>' outlet FNB-JKT-001 10.88.1.17/32 vpn.<domain>:51820
   ```
   Jalankan perintah `wg set ...` yang dicetak; kirim PSK dari `FNB-JKT-001.conf` lewat kanal terenkripsi; `shred -u` file setelah terpasang.
5. **Konfigurasi router**: isi `network.example` (alamat `10.88.1.17/32`, public key hub, PSK, endpoint) + `firewall.example`; split DNS `erp.<domain>` → `10.88.0.1` di dnsmasq.
6. **Uji** dari perangkat POS: `ping 10.88.0.1`; `https://erp.<domain>/web/login` (sertifikat publik valid); login 2FA; buka sesi POS, websocket online. Dari Wi-Fi tamu: `erp.<domain>` **tidak** terjangkau. Dari perangkat POS: `https://ops.<domain>` → 403 (bukan subnet staf).
7. **Catat** di register peer: outlet, peran, IP, public key, tanggal, PIC (tanpa kunci privat).

## Onboarding — pola B2: klien di perangkat POS / laptop staf

Untuk outlet tanpa router WireGuard, outlet darurat, dan semua laptop staf. Contoh: `infra/wireguard/peer-outlet.conf.example` (POS Windows/Android/Linux) dan `peer-staff.conf.example`.
- **Windows**: WireGuard for Windows → *Add empty tunnel* (kunci dibuat aplikasi) → kirim public key → isi Address/DNS/[Peer]. Pasang sebagai service (`wireguard.exe /installtunnelservice`) dengan akun kasir non-admin agar tidak bisa dimatikan.
- **Android**: aplikasi WireGuard (F-Droid/Play) → *Create from scratch* → *Generate* → kirim public key; aktifkan **Always-on VPN**.
- **Linux**: `wireguard-tools`, `wg-quick@efn`.
- Daftarkan: `gen-peer.sh --pubkey '<PUBKEY>' outlet FNB-JKT-001-POS1 10.88.1.31/32 ...` atau `staff ANDI-FIN 10.89.1.5/32 ...` (staf IT: `10.89.0.x`).

Mode darurat tanpa `--pubkey` (kunci dibuat di hub) hanya bila perangkat tidak bisa membuat kunci sendiri; file berisi kunci privat wajib dikirim terenkripsi lalu dihancurkan.

## Pencabutan (perangkat hilang, outlet tutup, staf keluar)
```bash
wg set wg0 peer <PUBKEY_PEER> remove
# hapus blok [Peer] terkait dari /etc/wireguard/wg0.conf, tandai di register
```
Lalu nonaktifkan user Odoo terkait, rotasi PIN POS, cek log Traefik akses dari IP peer tersebut.

## Firewall host
UDP 51820 terbuka dari internet. TCP 443 publik hanya dari Cloudflare ([origin-firewall.md](origin-firewall.md)); `10.88.0.1:443` hanya dari `wg0` (efn-wg.nft). SSH hanya dari `10.89.0.0/24` via `wg0`.

## Troubleshooting
| Gejala | Cek |
|---|---|
| `erp.` timeout dari outlet | DNS tidak ke `10.88.0.1` (`nslookup erp.<domain>`); handshake (`wg show wg0 latest-handshakes`); `nft list ruleset \| grep efn_wg` counter drop naik |
| `erp.` 403 | Sumber bukan `10.88/16`/`10.89/16` (NAT tambahan di router, atau router outlet memakai IP di luar alokasi) |
| `bi.`/`ops.` 403 dari outlet | Disengaja (SEC-21): dashboard hanya subnet staf |
| Sertifikat `erp.` tidak valid | Log Traefik `letsencrypt-dns`; token DNS (`acme_dns_api_token`) atau `ACME_DNS_PROVIDER` salah |
| Compose gagal bind `10.88.0.1:443` | `wg0` belum naik sebelum docker (drop-in systemd), atau `EFN_PUBLIC_BIND` kosong (0.0.0.0:443 bentrok) |
| Login 429 | `ratelimit-odoo-login` (10/menit per outlet) |
