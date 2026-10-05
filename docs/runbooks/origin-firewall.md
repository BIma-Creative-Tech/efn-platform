# Prosedur: Firewall origin (SEC-03, SEC-01)

Tujuan: hanya Cloudflare (lalu lintas publik `app.`, `api.`, `mart.`, `kitchen.`) dan mesh WireGuard (`erp.`, `bi.`, `ops.`, `status.`) yang bisa mencapai Traefik :443. Tanpa ini, penyerang dapat melewati WAF Cloudflare dan memalsukan IP klien.

Sumber rentang: `infra/traefik/cloudflare-ips.txt` (diperbarui `scripts/update-cloudflare-ips.sh`; CI memastikan `traefik.yml` sinkron). Rentang yang sama dipakai Traefik `forwardedHeaders.trustedIPs` dan env `TRUSTED_PROXIES` BFF (ditulis `deploy.sh`).

## nftables (host Debian/Ubuntu)
Docker memasang aturan sendiri di chain `DOCKER-USER`; filter port yang dipublish container (Traefik 80/443) **harus** di `DOCKER-USER`, bukan `INPUT`.

```bash
# /etc/efn/fw.sh - dijalankan systemd setelah docker.service; idempoten
set -euo pipefail
CF=/opt/efn/traefik/cloudflare-ips.txt
iptables -N EFN-EDGE 2>/dev/null || iptables -F EFN-EDGE
iptables -A EFN-EDGE -i wg0 -j RETURN                                   # WireGuard
for c in $(grep -Ev '^\s*(#|$)' "$CF" | grep -v ':'); do iptables -A EFN-EDGE -s "$c" -j RETURN; done
iptables -A EFN-EDGE -p tcp --dport 80 -j RETURN                        # ACME HTTP-01 (redirect ke 443)
iptables -A EFN-EDGE -j DROP
iptables -C DOCKER-USER -p tcp -m multiport --dports 443 -j EFN-EDGE 2>/dev/null \
  || iptables -I DOCKER-USER -p tcp -m multiport --dports 443 -j EFN-EDGE
# IPv6: ulangi dengan ip6tables untuk rentang yang mengandung ':'
```
Port lain: SSH hanya dari `wg0` (bastion), UDP 51820 terbuka, sisanya DROP di `INPUT`.

Sejak gelombang 5 (ADR-023 `wg-https`): Traefik publik (80/443 → container 80/443) di-bind ke `EFN_PUBLIC_BIND` (IP publik) dan host internal di-bind ke `10.88.0.1:443` → container **8443**. Setelah DNAT, aturan `--dports 443` di `DOCKER-USER` hanya mengenai lalu lintas publik; lalu lintas WireGuard (port container 8443) dibatasi oleh `infra/wireguard/efn-wg.nft` (hanya dari `wg0`). Baris `-i wg0 -j RETURN` di atas tetap aman.

## Setelah memperbarui rentang Cloudflare
1. `scripts/update-cloudflare-ips.sh` → review diff → commit (`cloudflare-ips.txt` + `traefik.yml`).
2. Deploy (Traefik + `TRUSTED_PROXIES` BFF ikut diperbarui).
3. Jalankan ulang `/etc/efn/fw.sh` di host.
4. Uji: `curl -I https://api.<domain>/health` (via Cloudflare) = 200; `curl -I --resolve api.<domain>:443:<ip-origin> https://api.<domain>/health` dari luar Cloudflare = timeout.
5. Uji IP klien (SEC-03): login OTP dari dua jaringan berbeda; `platform.otp_audit` (dekripsi `ip_enc` oleh admin) harus berisi IP klien, bukan IP Cloudflare.
