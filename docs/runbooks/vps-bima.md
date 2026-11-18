# Runbook: VPS Bima Creative — A. lingkungan pengembangan, B. stack ekstraksi ESB

- **Keputusan:** ADR-043 (**Proposed**, ditulis efn-architect paralel dengan runbook ini). Tahap A boleh segera;
  tahap B **DIBLOKIR** sampai ADR-043 disetujui **dan** ada persetujuan tertulis klien + DPO (§B0).
- **Terkait:** K25-02 (stack data terpisah), K25-06 (profil dev ≤ 8 GB), ADR-040 + [esb-extract.md](esb-extract.md)
  (G41), ADR-041 + [insight-web.md](insight-web.md) (G42), ADR-023 (pola WireGuard `infra/wireguard/`),
  [luks-disk.md](luks-disk.md), [key-custody.md](key-custody.md), [dev-stack-boot.md](dev-stack-boot.md).
- **Asumsi OS:** Ubuntu Server 24.04 LTS (noble), x86_64, systemd 255, cgroup v2, `ssh.socket` aktif. Perbedaan untuk
  OS lain ditandai **[beda OS]**.
- **Status:** runbook ini **belum pernah dijalankan di VPS nyata** (ditulis tanpa akses VPS/docker). Setiap langkah
  diberi perintah verifikasi; catat hasil nyata dan perbaiki runbook bila berbeda.

> **Ringkasan manajemen.** VPS milik Bima Creative dipakai dua cara yang **dipisah tegas**. **A** = mesin kerja
> pengembang (Claude Code + Docker) yang hanya berisi kode repo dan secret *dev* buatan sendiri, tanpa data atau
> kredensial klien; boleh disiapkan sekarang. **B** = mesin yang menarik data penjualan brand dari ESB ke ClickHouse
> memakai akun ESB klien. B memegang data bisnis klien, jadi baru boleh dibangun setelah ADR-043 disetujui, klien dan
> DPO memberi persetujuan tertulis, dan checklist §B0 lengkap. Rekomendasi: B di **VM/VPS terpisah** dari A, disk
> terenkripsi, keluar internet hanya ke ESB, akses per orang yang tercatat, dan data dihapus secara kriptografis saat
> pekerjaan selesai.

## 0. Keputusan, topologi, dan perkiraan resource

### 0.1 Topologi yang direkomendasikan

| | Host | Siapa | Isi | Jaringan |
|---|---|---|---|---|
| **A** | VPS-A (`efn-dev-bima`) | user `efnadmin` (admin manusia), `efndev` (Claude Code + Docker) | repo, secret DEV, data demo | WireGuard `10.90.0.0/24` (hub `10.90.0.1`), SSH hanya via WG |
| **B** | **VM/VPS terpisah** (`efn-esb-bima`) | akun per orang Bima yang disetujui EFN, grup `efn-esb-ops` | repo (commit disetujui), secret host-B, password ESB, ClickHouse | WireGuard `10.91.0.0/24` (hub `10.91.0.1`), egress kontainer hanya `services.esb.co.id:443` |

Subnet `10.90/10.91` sengaja berbeda dari hub EFN (`10.88/10.89`, ADR-023) agar tidak pernah tertukar dengan jaringan
outlet/staf EFN. Alasan B terpisah: di A, `efndev` (dan karenanya sesi Claude Code) berada di grup `docker` = setara
root host; bila B satu host dengan A, siapa pun yang mengendalikan A bisa membaca data klien di B. Varian satu host
hanya boleh dengan syarat §B1.2.

### 0.2 Perkiraan resource (estimasi, ukur ulang setelah dipakai)

Dasar hitung: batas memori di compose (`deploy.resources.limits`), bukan pemakaian nyata. Profil `core` dev saat ini
berjumlah **±11,2 GiB batas** (postgres 2G, odoo 3G, api-gateway 1,5G dev, superapp-web 1G dev, rabbitmq 1G, minio
1G, redis 768M, traefik 256M, s3-gateway 128M, ±10 sidecar/proxy × 64M), profil `data` menambah ±7,7 GiB (clickhouse
4G, metabase 2G, etl 1G, insight-web + sidecar 576M). Target K25-06 dev ≤ 8 GB **belum tercapai lewat batas compose**;
§A6 menegakkan plafon total 8 GiB di tingkat host (cgroup slice) dan menyarankan menyalakan subset service.

| Skenario | vCPU | RAM | Disk | Catatan |
|---|---|---|---|---|
| **A** saja | 4 minimum, **8 nyaman** (build image Odoo/MinIO-Go, test Odoo) | **16 GB** (8 GiB plafon kontainer + OS + Claude Code ±0,5–1 GB + cache build) + swap 8 GB | **100 GB** SSD (image + build cache ±40–60 GB, volume dev, repo) | tanpa disk terenkripsi khusus (tidak ada data klien) |
| **B** saja | 2 minimum, 4 nyaman | **8 GB** (ClickHouse batas 4G + esb-extract 512M + OS), **tanpa swap** | OS 30 GB + **disk/blok tambahan 100 GB** terenkripsi LUKS | data ESB 5 brand × 24 bulan diperkirakan 15–25 GB terkompresi (raw_doc + mart) + ruang merge 2× + staging backup; hitung ulang dari `system.parts` setelah brand pertama |
| **A + B satu VPS** (tidak direkomendasikan) | 8 | **24–32 GB** | OS 100 GB + **blok terpisah 150 GB** untuk B (LUKS) | wajib syarat §B1.2 (A rootless, B daemon root terpisah) |

Bandwidth tidak menjadi pembatas: backfill dibatasi 2 panggilan/detik/brand (esb-extract.md §5).

### 0.3 Keputusan yang harus diambil user/klien sebelum mulai

1. Spesifikasi VPS A (vCPU/RAM/disk) dan apakah B mendapat VM/VPS sendiri (rekomendasi: ya).
2. Lokasi fisik/negara pusat data VPS (bila di luar Indonesia: transfer lintas negara UU PDP → catatan DPO, wajib
   untuk B).
3. Akses root/console penyedia (dibutuhkan untuk break-glass bila SSH/WG terkunci, dan untuk unlock LUKS bila jaringan
   gagal).
4. Ketersediaan **block volume tambahan** untuk B (tanpa itu LUKS memakai file container di disk root, §B3.2).
5. Cara Claude Code mendorong kode dari A ke GitHub: fine-grained PAT (push branch) atau deploy key read-only (§A7);
   proteksi branch `main` di GitHub (wajib bila PAT bisa push).
6. Tahap B: kanal alert heartbeat/backup (journal saja, Uptime Kuma push EFN lewat WG, atau email), lokasi storage
   backup milik EFN untuk `rclone`, siapa pemegang passphrase LUKS dan kunci privat `age`.

### 0.4 Konvensi

- `<...>` = isi oleh operator. Blok berawalan `sudo -i` dijalankan sebagai root (keluar dengan `exit` setelahnya).
- Semua file yang dibuat di bawah idempoten: menjalankan ulang langkah tidak merusak state.
- Jangan pernah menempelkan secret ke argumen perintah, chat, tiket, atau file repo.

---

# Bagian A — Lingkungan pengembangan (tanpa data/secret klien)

Aturan tahap A (wajib, juga untuk sesi Claude Code):
- Tidak ada kredensial klien: password ESB (`secrets/esb_core_password_*.txt` tetap `disabled`), `ESB_USER_*` kosong,
  tidak ada dump DB/ClickHouse produksi, tidak ada data member asli. Data dev = data demo kecil.
- Jangan menyalin memori Claude Code laptop yang memuat detail akun ESB/klien ke VPS.
- `ninerouter_api_key` (gateway LLM milik Bima, K25-01) boleh dipakai untuk dev, dengan payload data demo saja.

## A1. Akun OS dan SSH key-only

Dua akun: `efnadmin` (manusia, sudo dengan password) dan `efndev` (Claude Code + Docker, **tanpa sudo** — itulah
"sudo terbatas": administrasi selalu lewat `efnadmin`).

```bash
# Login pertama dari console/SSH bawaan penyedia (root atau user cloud-init), lalu:
sudo -i
id efnadmin >/dev/null 2>&1 || adduser --gecos '' efnadmin        # minta password: dipakai untuk sudo saja
usermod -aG sudo efnadmin
id efndev >/dev/null 2>&1 || adduser --disabled-password --gecos '' efndev
for u in efnadmin efndev; do
  install -d -m 0700 -o "$u" -g "$u" "/home/$u/.ssh"
  touch "/home/$u/.ssh/authorized_keys"; chown "$u:$u" "/home/$u/.ssh/authorized_keys"; chmod 0600 "/home/$u/.ssh/authorized_keys"
done
# Tempel PUBLIC key (ed25519; dibuat di laptop: ssh-keygen -t ed25519 -C "<nama>@laptop") ke masing-masing:
nano /home/efnadmin/.ssh/authorized_keys
nano /home/efndev/.ssh/authorized_keys
exit
```

Hardening sshd. Nama file `00-` wajib: sshd memakai nilai **pertama** yang ditemukan, dan cloud-init sering menulis
`50-cloud-init.conf` dengan `PasswordAuthentication yes`.

```bash
sudo tee /etc/ssh/sshd_config.d/00-efn-hardening.conf > /dev/null <<'EOF'
# EFN VPS A (runbook vps-bima.md §A1)
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PubkeyAuthentication yes
AuthenticationMethods publickey
AllowUsers efnadmin efndev
# Tunnel lokal untuk membuka stack dev dari laptop (§A8); tanpa agent/X11 forwarding
AllowTcpForwarding local
AllowAgentForwarding no
X11Forwarding no
MaxAuthTries 3
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
# Mencatat sidik jari key per login (audit per orang)
LogLevel VERBOSE
EOF
sudo sshd -t && sudo systemctl reload ssh || sudo systemctl restart ssh
sudo sshd -T | grep -Ei '^(passwordauthentication|permitrootlogin|kbdinteractiveauthentication|allowusers) '
```

**Jangan menutup sesi lama** sebelum login baru `ssh efnadmin@<IP_PUBLIK>` dan `ssh efndev@<IP_PUBLIK>` berhasil.
Ubuntu 24.04 memakai `ssh.socket`: perubahan `Port`/`ListenAddress` butuh `systemctl daemon-reload && systemctl
restart ssh.socket` (runbook ini tidak mengubahnya). **[beda OS]** Debian 12/Ubuntu 22.04: service `ssh`/`sshd`
tanpa socket activation.

## A2. Firewall (UFW) dan WireGuard

Urutan aman: izinkan SSH dari IP allowlist → aktifkan UFW → pasang WireGuard → uji SSH via WG → cabut allowlist.

```bash
sudo apt-get update && sudo apt-get install -y ufw wireguard-tools nftables
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 51820/udp comment 'WireGuard'
sudo ufw allow from <IP_ALLOWLIST_OPERATOR>/32 to any port 22 proto tcp comment 'SSH bootstrap (hapus setelah WG)'
sudo ufw allow in on wg0 from 10.90.0.0/24 to 10.90.0.1 port 22 proto tcp comment 'SSH via WireGuard'
sudo ufw logging low
sudo ufw --force enable
sudo ufw status verbose
```

Catatan penting: **port yang dipublish Docker melewati UFW** (Docker memasang aturan di chain FORWARD/NAT sendiri).
Karena itu A3 menambah penjaga nftables dan A8 mengikat Traefik ke `127.0.0.1`.

WireGuard (pola `infra/wireguard/wg0-server.conf.example`, subnet berbeda; `gen-peer.sh` tidak dipakai karena
memvalidasi subnet hub EFN 10.88/10.89):

```bash
sudo -i
umask 077
[ -s /etc/wireguard/server.key ] || wg genkey > /etc/wireguard/server.key
wg pubkey < /etc/wireguard/server.key > /etc/wireguard/server.pub
cat /etc/wireguard/server.pub            # kirim ke operator laptop (bukan rahasia)
if [ ! -s /etc/wireguard/wg0.conf ]; then
  cat > /etc/wireguard/wg0.conf <<EOF
# VPS A Bima (runbook vps-bima.md §A2). Hub 10.90.0.1; satu /32 per laptop, mulai 10.90.0.10.
[Interface]
Address = 10.90.0.1/24
ListenPort = 51820
PrivateKey = $(cat /etc/wireguard/server.key)
EOF
fi
chmod 600 /etc/wireguard/wg0.conf
systemctl enable --now wg-quick@wg0
exit
```

Tambah peer (kunci privat **dibuat di laptop**, ADR-023 butir 5; hanya public key yang dikirim):

```bash
# Di laptop (aplikasi WireGuard → "Add empty tunnel" membuat kunci) atau: wg genkey | tee laptop.key | wg pubkey
sudo -i
PEER_PUB='<PUBKEY_LAPTOP>'; PEER_IP=10.90.0.10; NAME='<nama-orang>-laptop'
psk=$(wg genpsk)
wg set wg0 peer "$PEER_PUB" preshared-key <(printf '%s' "$psk") allowed-ips "${PEER_IP}/32"
grep -q "$PEER_PUB" /etc/wireguard/wg0.conf || printf '\n[Peer]\n# %s, dibuat %s, kunci dibuat di perangkat\nPublicKey = %s\nPresharedKey = %s\nAllowedIPs = %s/32\n' \
  "$NAME" "$(date +%F)" "$PEER_PUB" "$psk" "$PEER_IP" >> /etc/wireguard/wg0.conf
printf '%s\n' "$psk"     # sampaikan PSK lewat kanal terenkripsi, lalu bersihkan layar (clear)
unset psk
exit
```

Konfigurasi di laptop (pola `peer-staff.conf.example`):

```ini
[Interface]
PrivateKey = <DIBUAT_DI_LAPTOP>
Address = 10.90.0.10/32

[Peer]
PublicKey = <isi /etc/wireguard/server.pub>
PresharedKey = <PSK>
Endpoint = <IP_PUBLIK_VPS_A>:51820
AllowedIPs = 10.90.0.1/32
PersistentKeepalive = 25
```

Uji: `ssh efnadmin@10.90.0.1` berhasil → cabut allowlist publik:
`sudo ufw delete allow from <IP_ALLOWLIST_OPERATOR>/32 to any port 22 proto tcp`. Break-glass bila WG rusak: console
penyedia. Cabut peer: `wg set wg0 peer <PUB> remove` + hapus blok di `wg0.conf`.

## A3. Penjaga nftables (peer WG dan port Docker)

Tabel sendiri (bukan `flush ruleset`; jangan aktifkan `nftables.service` bawaan karena `/etc/nftables.conf` Ubuntu
menghapus aturan UFW dan Docker).

```bash
ip -br link          # catat nama antarmuka publik (mis. eth0, ens3)
sudo install -d -m 0755 /etc/efn
sudo tee /etc/efn/efn-vps.nft > /dev/null <<'EOF'
#!/usr/sbin/nft -f
# Penjaga VPS A (runbook vps-bima.md §A3). Idempoten & atomik (pola infra/wireguard/efn-wg.nft).
define PUB_IF = "eth0"
define WG_IF = "wg0"

table inet efn_vps
delete table inet efn_vps

table inet efn_vps {
  chain forward {
    type filter hook forward priority filter - 10; policy accept;
    # Peer WireGuard hanya ke host (SSH lewat INPUT/UFW): tidak ke kontainer, peer lain, atau internet
    iifname $WG_IF counter drop
    oifname $WG_IF ct state established,related accept
    oifname $WG_IF counter drop
    # Port yang (tidak sengaja) dipublish Docker ke 0.0.0.0 tidak terjangkau dari internet
    iifname $PUB_IF ct state new ct status dnat counter drop
  }
}
EOF
sudo sed -i 's/^define PUB_IF = .*/define PUB_IF = "<ANTARMUKA_PUBLIK>"/' /etc/efn/efn-vps.nft
sudo tee /etc/systemd/system/efn-vps-nft.service > /dev/null <<'EOF'
[Unit]
Description=EFN VPS nftables guard (tabel inet efn_vps)
Before=docker.service wg-quick@wg0.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/efn/efn-vps.nft
ExecStop=/usr/sbin/nft delete table inet efn_vps

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now efn-vps-nft.service
sudo nft list table inet efn_vps
```

## A4. Pembaruan otomatis, NTP, fail2ban, swap

```bash
sudo apt-get install -y unattended-upgrades needrestart chrony fail2ban tmux jq git curl ca-certificates
# Pembaruan keamanan otomatis, TANPA reboot otomatis (reboot memutus sesi Claude Code di tmux)
sudo tee /etc/apt/apt.conf.d/52efn-unattended > /dev/null <<'EOF'
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
EOF
sudo dpkg-reconfigure -f noninteractive -plow unattended-upgrades
systemctl status unattended-upgrades --no-pager | head -3
# Reboot terjadwal manual: cek mingguan  [ -f /var/run/reboot-required ] && cat /var/run/reboot-required.pkgs
```

NTP (chrony menggantikan systemd-timesyncd). Penting untuk token SSO Insight 60 detik (insight-web.md §5, C6) dan
validitas TLS/JWT:

```bash
echo 'pool id.pool.ntp.org iburst maxsources 4' | sudo tee /etc/chrony/sources.d/efn.sources > /dev/null
sudo chronyc reload sources
chronyc tracking | grep -E 'System time|Leap status'      # Leap status: Normal; selisih < 0,1 dtk
timedatectl | grep -E 'synchronized|NTP service'          # yes / active
```

fail2ban (SSH hanya lewat WG, jadi ini lapisan tambahan selama masa bootstrap allowlist):

```bash
sudo tee /etc/fail2ban/jail.d/efn.local > /dev/null <<'EOF'
[sshd]
enabled = true
backend = systemd
maxretry = 4
findtime = 10m
bantime = 1h
ignoreip = 127.0.0.1/8 ::1 10.90.0.0/24
EOF
sudo systemctl enable --now fail2ban && sudo systemctl restart fail2ban
sudo fail2ban-client status sshd
```

Bila `fail2ban` gagal start (ada laporan crash modul Python di rilis awal noble; cek `journalctl -u fail2ban`):
`sudo apt-get install -y sshguard` lalu `sudo systemctl disable --now fail2ban` (sshguard membaca journal sshd sendiri).

Swap (A tidak memegang data klien, swap file biasa cukup):

```bash
sudo -i
if ! swapon --show | grep -q /swapfile; then
  [ -f /swapfile ] || { fallocate -l 8G /swapfile; chmod 600 /swapfile; mkswap /swapfile; }
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10' > /etc/sysctl.d/90-efn.conf && sysctl --system > /dev/null
swapon --show
exit
```

## A5. Docker Engine resmi

### A5.1 Pasang dari repo Docker (dua mode berbagi langkah ini)

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
gpg --show-keys /etc/apt/keyrings/docker.asc | grep -q '9DC858229FC7DD38854AE2D88D81803C0EBFCD88' && echo "kunci Docker OK"
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
docker --version && docker compose version
```

Paket Docker tidak termasuk origin unattended-upgrades: perbarui manual bulanan (`sudo apt-get upgrade`), container
dimulai ulang.

### A5.2 Pilih mode

| Mode | Kapan | Risiko |
|---|---|---|
| **A5a rootful + grup `docker`** (default) | VPS A khusus pengembangan, B di host lain | Anggota grup `docker` = **setara root host**. Karena Claude Code berjalan sebagai `efndev`, sesi Claude Code secara efektif bisa menjadi root di VPS A. Diterima karena A tidak menyimpan data/kredensial klien dan tidak ada layanan lain di host. |
| **A5b rootless** | **Wajib** bila B satu host dengan A (§B1.2); opsional sebagai pengerasan | Lebih rumit; jebakan diketahui (bawah). Belum diverifikasi dengan stack EFN penuh. |

**A5a rootful:**

```bash
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" },
  "live-restore": true,
  "cgroup-parent": "efndev.slice"
}
EOF
# Slice dibuat di A6 SEBELUM restart docker
sudo usermod -aG docker efndev        # efndev logout/login agar grup berlaku
```

**A5b rootless** (login SSH langsung sebagai `efndev` untuk langkah user; jangan `su`/`sudo -iu`, butuh sesi systemd user):

```bash
# sebagai efnadmin
sudo apt-get install -y docker-ce-rootless-extras uidmap dbus-user-session slirp4netns
# Ubuntu 24.04 membatasi user namespace tanpa hak lewat AppArmor -> profil untuk rootlesskit  [beda OS: tidak perlu di Debian]
sudo tee /etc/apparmor.d/usr.bin.rootlesskit > /dev/null <<'EOF'
abi <abi/4.0>,
include <tunables/global>

/usr/bin/rootlesskit flags=(unconfined) {
  userns,

  include if exists <local/usr.bin.rootlesskit>
}
EOF
sudo systemctl restart apparmor.service
# Delegasi controller cgroup (cpu dibutuhkan untuk deploy.resources.limits.cpus)
sudo install -d /etc/systemd/system/user@.service.d
printf '[Service]\nDelegate=cpu cpuset io memory pids\n' | sudo tee /etc/systemd/system/user@.service.d/delegate.conf > /dev/null
sudo systemctl daemon-reload
# Traefik dev mem-publish 80/443 (port < 1024). Ulangi setelah paket rootless-extras diperbarui.
sudo setcap cap_net_bind_service=ep /usr/bin/rootlesskit
sudo loginctl enable-linger efndev
# Bila daemon rootful tidak dipakai di host ini (A saja): sudo systemctl disable --now docker.service docker.socket

# sebagai efndev (SSH langsung)
dockerd-rootless-setuptool.sh install      # bila menolak karena daemon rootful aktif (varian §B1.2): tambahkan --force
systemctl --user enable --now docker
grep -q DOCKER_HOST ~/.bashrc || echo "export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock" >> ~/.bashrc
. ~/.bashrc && docker info --format '{{.SecurityOptions}}'      # memuat name=rootless
```

Jebakan rootless yang diketahui (belum diuji di stack EFN): IP sumber koneksi ke port publish tidak dipertahankan
oleh port driver bawaan → middleware Traefik `internal-only`/`internal-ops` bisa menolak (403) akses `:9443`;
`node-exporter`/`cadvisor` (profil `ops`) tidak berfungsi; `ulimits nofile 262144` ClickHouse butuh batas
`LimitNOFILE` user manager ≥ nilai itu (default noble 524288). Laporkan temuan ke efn-devsecops-engineer.

## A6. Batas memori total (K25-06, profil dev ≤ 8 GiB)

Batas per service di compose berjumlah lebih dari 8 GiB (§0.2), jadi plafon ditegakkan di cgroup host. Bila total
pemakaian nyata melewati plafon, kernel mematikan proses di dalam slice (OOM) — container tertentu restart; itu sinyal
untuk menyalakan lebih sedikit service, bukan menaikkan plafon.

```bash
# A5a rootful: semua container di bawah efndev.slice (daemon.json "cgroup-parent")
sudo tee /etc/systemd/system/efndev.slice > /dev/null <<'EOF'
[Unit]
Description=Plafon memori total container dev EFN (K25-06, runbook vps-bima.md §A6)

[Slice]
MemoryHigh=7G
MemoryMax=8G
MemorySwapMax=2G
EOF
sudo systemctl daemon-reload && sudo systemctl restart docker
systemctl show efndev.slice -p MemoryMax -p MemoryHigh

# A5b rootless: plafon seluruh user efndev (container + Claude Code), 1 GiB lebih longgar
sudo systemctl set-property "user-$(id -u efndev).slice" MemoryHigh=8G MemoryMax=9G MemorySwapMax=2G
```

Praktik hemat: nyalakan hanya service yang dibutuhkan (`$DC up -d <service>` membawa dependensinya), `PG_ARCHIVE_MODE=off`
di `.env` dev, jangan menyalakan profil `ai` (Ollama dilarang di dev, K25-06) dan `observability`/`ops` kecuali
sedang menguji itu. Ukur: `docker stats --no-stream` dan `systemctl status efndev.slice` (baris `Memory:`). Usulan
overlay dev ramping ada di laporan efn-devsecops-engineer (belum dibuat).

## A7. Clone repo dan autentikasi GitHub

Repo publik → clone tanpa kredensial. Kredensial hanya dibutuhkan untuk **push**. Pilih satu (keputusan §0.3 no. 5):

| Opsi | Cara | Konsekuensi |
|---|---|---|
| **A7a fine-grained PAT** (disarankan bila Claude Code bekerja mandiri) | GitHub → Settings → Developer settings → Fine-grained token: *Only select repositories* = repo ini; Contents RW, Pull requests RW, Metadata R; kedaluwarsa ≤ 90 hari | Token di `~/.config/gh/hosts.yml` (0600, plaintext tanpa keyring) dan terbaca oleh sesi Claude Code. **Wajib** proteksi branch `main` (PR + review, tanpa force push) agar pola "satu PR per gelombang, user merge manual" tidak bisa dilangkahi. |
| **A7b deploy key read-only** | `ssh-keygen -t ed25519 -f ~/.ssh/efn_deploy -N ''` → tambahkan `.pub` di repo Settings → Deploy keys (tanpa *Allow write access*) | Tidak bisa push. Perubahan keluar lewat `git bundle`/`format-patch` yang diambil laptop (`scp`). |

A7a — pasang GitHub CLI dari repo resmi (sebagai `efnadmin`):

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null
sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
sudo apt-get update && sudo apt-get install -y gh
```

A7a — login dan clone (sebagai `efndev`):

```bash
gh auth login --hostname github.com --git-protocol https --with-token   # tempel PAT, Enter, Ctrl-D (tidak masuk riwayat shell)
gh auth setup-git
git clone https://github.com/<org>/<repo>.git ~/efn-platform
git -C ~/efn-platform config user.name "<Nama Pengembang>"
git -C ~/efn-platform config user.email "<email-kerja>"
```

A7b — deploy key read-only (sebagai `efndev`):

```bash
[ -f ~/.ssh/efn_deploy ] || ssh-keygen -t ed25519 -f ~/.ssh/efn_deploy -N '' -C "efn-vps-a deploy key"
cat ~/.ssh/efn_deploy.pub        # tempel di repo Settings -> Deploy keys (tanpa write access)
grep -q efn_deploy ~/.ssh/config 2>/dev/null || printf 'Host github.com\n  IdentityFile ~/.ssh/efn_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
git clone git@github.com:<org>/<repo>.git ~/efn-platform
```

Alternatif tanpa PAT: `gh auth login --web` (device code dibuka di browser laptop) memberi token OAuth berscope `repo`
untuk **semua** repo akun itu — lebih luas daripada PAT fine-grained, tidak disarankan.

## A8. Secret DEV, `.env`, dan menyalakan stack dev

Node/Python **tidak** dipasang di host; semua build/test di container (sama dengan laptop, dev-stack-boot.md). Di
Linux tidak perlu `MSYS_NO_PATHCONV`.

```bash
cd ~/efn-platform
bash scripts/gen-secrets.sh                                  # idempoten; CA DEV + leaf (secrets/ 0700, gitignored)
cp -n infra/compose/.env.example infra/compose/.env
chmod 600 infra/compose/.env
```

Ubah di `infra/compose/.env` (editor, bukan `sed` dengan nilai di argumen):

| Kunci | Nilai VPS A | Alasan |
|---|---|---|
| `EFN_PUBLIC_BIND` | `127.0.0.1` | Traefik base mem-publish `${EFN_PUBLIC_BIND:-0.0.0.0}:80/443`; di VPS jangan terbuka ke internet (Docker melewati UFW) |
| `EFN_WG_BIND` | `127.0.0.1` (default) | wg-https `:9443` hanya lewat tunnel SSH |
| `PG_ARCHIVE_MODE` | `off` (opsional) | hemat disk; uji backup/WAL tetap bisa dinyalakan sementara |
| `ESB_ENABLED` / `ESB_USER_*` | `false` / **kosong** | tahap A tanpa kredensial klien |

Cek tidak ada kredensial klien (jalankan setelah setiap `gen-secrets.sh`/perubahan secret):

```bash
for f in secrets/esb_core_password_*.txt secrets/clickhouse_password_mig.txt secrets/migration_bundle_key.txt; do grep -qx disabled "$f" || echo "PERINGATAN: $f bukan placeholder 'disabled' (dilarang di tahap A)"; done
grep -E '^ESB_USER_[A-Z]+=.+' infra/compose/.env && echo "PERINGATAN: ESB_USER_* terisi (dilarang di tahap A)"
```

Menyalakan (pola dev-stack-boot.md §2/§3; subset dulu):

```bash
DC="docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.dev.yml --env-file infra/compose/.env"
$DC --profile core up -d --build
$DC --profile core ps
# Data/Insight hanya bila dibutuhkan (memakan ±7,7 GiB batas): $DC --profile core --profile data up -d clickhouse clickhouse-init insight-web
```

Membuka dari laptop (tunnel lewat WireGuard; port lokal 9443 dipertahankan karena Origin SSO Insight dev wajib
`:9443`, insight-web.md §6):

```bash
# laptop
ssh -N -L 9443:127.0.0.1:9443 -L 8443:127.0.0.1:443 efndev@10.90.0.1
# hosts laptop: 127.0.0.1 erp.efn.local app.efn.local api.efn.local insight.efn.local
# https://erp.efn.local:9443/web/login   https://app.efn.local:8443/
```

## A9. Claude Code CLI di tmux (tetap jalan saat laptop mati)

Login dilakukan **sendiri oleh user** di VPS; tidak ada API key di repo, `.env` compose, atau `secrets/`.

```bash
# sebagai efndev
curl -fsSL https://claude.ai/install.sh -o /tmp/claude-install.sh && less /tmp/claude-install.sh   # baca dulu
bash /tmp/claude-install.sh && rm -f /tmp/claude-install.sh       # biner native di ~/.local/bin (tanpa Node)
claude --version
tmux new -s claude -c ~/efn-platform
claude            # login pertama: ikuti URL/kode di layar, buka di browser laptop
# Lepas tanpa mematikan: Ctrl-b lalu d.  Sambung lagi dari laptop:
#   ssh -t efndev@10.90.0.1 tmux attach -t claude
```

- Sesi tmux bertahan setelah SSH putus (logind noble default `KillUserProcesses=no`); **tidak** bertahan reboot
  (karena itu A4 mematikan reboot otomatis).
- Bila terpaksa memakai API key: simpan di `~/.config/efn/anthropic.env` (0600, di luar repo), `set -a; . ~/.config/efn/anthropic.env; set +a`
  di jendela tmux itu saja. Jangan pernah di repo, `.env` compose, atau riwayat shell.
- Hindari mode tanpa konfirmasi izin; bila dipakai, hanya di A (tidak ada kredensial klien) dan **tidak pernah** di B.
  Claude Code **tidak dipasang** di host B.
- Kredensial login Claude Code ada di `~/.claude/` (0600). Pencabutan: logout di CLI dan cabut sesi di akun.

## A10. Validasi CI lokal (perintah yang sama dengan lead/CI)

Dari `~/efn-platform`. Image dipin sama dengan `.github/workflows/ci.yml`; `koalaman/shellcheck` belum dipin digest
(CI memakai shellcheck runner).

```bash
PY=python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea
NODE=node:22.23-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402

# 1. ruff (lint + format) - job `python`
docker run --rm -v "$PWD:/src:ro" -w /src -e PYTHONDONTWRITEBYTECODE=1 "$PY" sh -c \
  'pip install -q --disable-pip-version-check ruff==0.16.10 &&
   D="services/ai-ocr-worker services/etl services/text2sql services/esb-extract services/odoo/addons" &&
   ruff check --no-cache $D && ruff format --no-cache --check $D'

# 2. pytest per service - job `pytest`
for s in etl text2sql esb-extract ai-ocr-worker; do
  docker run --rm -v "$PWD/services/$s:/src:ro" -w /src -e PYTHONDONTWRITEBYTECODE=1 -e SVC="$s" "$PY" sh -c \
    'if [ "$SVC" = ai-ocr-worker ]; then apt-get update -q >/dev/null && apt-get install -y -q --no-install-recommends tesseract-ocr tesseract-ocr-ind >/dev/null; fi &&
     pip install -q --disable-pip-version-check -r requirements.txt &&
     if [ -f requirements-dev.txt ]; then pip install -q -r requirements-dev.txt; else pip install -q pytest; fi &&
     python -m pytest -q -p no:cacheprovider tests' || echo "GAGAL pytest: $s"
done

# 3. Node (lint + Jest/Vitest) - job `node`. node_modules di volume bernama, tidak mengotori repo.
for s in insight-web api-gateway superapp-web; do
  docker run --rm -v "efn-nm-$s:/nm" "$NODE" chown "$(id -u):$(id -g)" /nm
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e CI=true \
    -v "$PWD/services/$s:/app" -v "efn-nm-$s:/app/node_modules" -w /app "$NODE" \
    sh -c 'npm ci --no-audit --no-fund && npm run lint --if-present && npm test --if-present' || echo "GAGAL node: $s"
done

# 4. Enkripsi + compose config - job `infra` (butuh jq di host; .env sudah ada dari A8)
bash scripts/check-encryption.sh
( cd infra/compose && for env in dev prod; do
    docker compose -f docker-compose.yml -f "docker-compose.${env}.yml" -f docker-compose.esb.yml \
      --profile core --profile data --profile ai --profile observability --profile ops config -q && echo "config $env OK"
  done )

# 5. shellcheck (daftar lengkap: langkah `shellcheck` job infra di ci.yml)
docker run --rm -v "$PWD:/mnt:ro" -w /mnt koalaman/shellcheck:v0.10.0 -x \
  scripts/*.sh scripts/lib/*.sh scripts/host/efn-deploy scripts/host/efn-online-cs scripts/host/efn-rotate-online-key
```

Test addon Odoo (job `odoo-test`, container Odoo + Postgres, ±3–4 GiB): jalankan perintah job itu dari `ci.yml` saat
stack dev **tidak** menyala penuh, agar tidak menabrak plafon A6. CI GitHub tetap gerbang akhir.

## A11. Backup sederhana repo kerja

Sumber kebenaran tetap GitHub (push branch sering). Skrip ini menyelamatkan pekerjaan yang belum di-push; `secrets/`
dan `.env` tidak ikut (gitignored, dibuat ulang dengan `gen-secrets.sh`).

```bash
# sebagai efndev
install -d -m 0700 ~/bin ~/backup
cat > ~/bin/efn-repo-backup.sh <<'EOF'
#!/usr/bin/env bash
# Cadangan repo kerja VPS A (runbook vps-bima.md §A11): bundle semua ref + patch belum di-commit + file untracked.
# Tidak memuat file gitignored (secrets/, infra/compose/.env).
set -euo pipefail
REPO="${REPO:-$HOME/efn-platform}"
OUT="${OUT:-$HOME/backup}"
KEEP_DAYS="${KEEP_DAYS:-14}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
umask 077
mkdir -p "$OUT"
git -C "$REPO" bundle create "$OUT/efn-$stamp.bundle" --all
git -C "$REPO" diff HEAD --binary > "$OUT/efn-$stamp.uncommitted.patch"
git -C "$REPO" ls-files --others --exclude-standard -z \
  | tar -C "$REPO" --null -T - -czf "$OUT/efn-$stamp.untracked.tgz"
find "$OUT" -maxdepth 1 -name 'efn-*' -mtime +"$KEEP_DAYS" -delete
echo "backup repo: $OUT/efn-$stamp.*"
EOF
chmod 0700 ~/bin/efn-repo-backup.sh
~/bin/efn-repo-backup.sh
( crontab -l 2>/dev/null | grep -v efn-repo-backup; echo '17 */6 * * * $HOME/bin/efn-repo-backup.sh >> $HOME/backup/backup.log 2>&1' ) | crontab -
```

Salinan di luar VPS (dari laptop, lewat WG): `rsync -a efndev@10.90.0.1:backup/ <folder-laptop>/efn-vps-backup/`.
Pulihkan: `git clone efn-<stamp>.bundle efn-platform && cd efn-platform && git apply ../efn-<stamp>.uncommitted.patch`.

## A12. Checklist selesai A

- [ ] `sshd -T`: password & root login mati; login hanya key; `AllowUsers efnadmin efndev`.
- [ ] `ufw status`: hanya 51820/udp publik + SSH di wg0; allowlist bootstrap sudah dicabut.
- [ ] `nft list table inet efn_vps` ada; dari luar, `nc -vz <IP_PUBLIK> 443` gagal walau Traefik menyala.
- [ ] `chronyc tracking` sinkron; `unattended-upgrades` aktif; fail2ban/sshguard aktif; swap 8 GB.
- [ ] `docker info` OK (mode A5a/A5b dicatat); plafon A6 terpasang.
- [ ] Cek "tidak ada kredensial klien" (A8) bersih; Claude Code login di tmux; validasi A10 hijau.
- [ ] Cron backup repo jalan; salinan laptop diuji sekali.

---

# Bagian B — Stack ekstraksi ESB (data dan kredensial klien)

> **JANGAN mulai bagian ini** sebelum §B0 seluruhnya tercentang dan ditandatangani. Sampai itu, B paling jauh boleh
> disiapkan sebagai host kosong (B1–B4) **tanpa** password ESB, username ESB, maupun data.

## B0. Checklist go/no-go (ditandatangani pemilik data EFN, DPO, dan lead ops Bima)

| # | Syarat | Bukti |
|---|---|---|
| 1 | ADR-043 berstatus **Accepted** untuk tahap B | ADR + tanggal |
| 2 | Persetujuan tertulis klien: Bima Creative sebagai pemroses, brand yang dicakup, kategori data, tujuan (datamart G41, migrasi G43, forecast G44), lokasi VPS (negara/penyedia/pusat data), periode, **tanggal penghapusan**, daftar orang | surat/email resmi |
| 3 | Perjanjian pemrosesan data + kerahasiaan EFN–Bima ditandatangani | dokumen |
| 4 | DPO menandatangani entri inventaris PDP "Datamart ESB" (SEC-184) **dengan lokasi host B**; bila pusat data di luar Indonesia: catatan transfer lintas negara (`docs/compliance/PDP_TRANSFER_LINTAS_NEGARA.md`) | entri + tanda tangan |
| 5 | esb-extract.md §1a butir 2, 3, 4, 6, 7 terpenuhi: **Q-ESB1** user API read-only per brand (akun admin tidak dipakai), **Q-ESB7** izin jadwal backfill, **Q-ESB9** dasar pemrosesan, redaksi `raw_doc` dicocokkan, `ESB_ALLOW_IGNORE_WINDOW=false`. Butir 5 (Metabase) N/A karena Metabase tidak dijalankan di B | catatan per butir |
| 6 | Host B dibangun sesuai B1–B7: LUKS aktif, data-root Docker di LUKS, snapshotter containerd mati, tanpa swap, uji egress lolos | keluaran verifikasi B3/B4/B7 |
| 7 | Image `esb-extract` terverifikasi cosign atau di-build dari commit yang disetujui (B5); commit SHA dicatat | SHA + digest |
| 8 | Storage backup **milik EFN**, kunci privat `age` dipegang EFN (bukan Bima); uji restore B11 lolos dengan data dummy | log uji |
| 9 | Register akses disetujui EFN (B12); hanya orang terdaftar punya akun; user host A tidak punya akses ke B; Claude Code tidak terpasang di B | register |
| 10 | Kanal alert heartbeat/backup ditetapkan dan diuji (B10) | uji alert |
| 11 | Kontak insiden: peran Bima dicantumkan di [security-incident.md](security-incident.md) / [breach-response-72h.md](breach-response-72h.md) (laporan 3×24 jam) | daftar kontak |
| 12 | Rencana & tanggal penghapusan (B14) disepakati; pemegang passphrase LUKS ditetapkan (key-custody.md) | berita acara |
| 13 | SEC-199: `secrets/clickhouse_password_mig.txt` dan `secrets/migration_bundle_key.txt` di B berisi `disabled`; `SHOW USERS` ClickHouse B **tanpa** `efn_mig` (B8.1). Migrasi G43 (`esb_to_odoo.py export`/`writeback`/`reconcile`, `run-import.sh`) **tidak** dijalankan di B (ADR-043 §3.4) | keluaran B6 + B8.1 |

## B1. Topologi host B

### B1.1 Rekomendasi: VM/VPS terpisah

Host B dibangun dari nol (bukan klon A). Terapkan A1–A4 dengan perbedaan berikut:

| Langkah A | Perbedaan di B |
|---|---|
| A1 akun | **Tidak ada** `efndev`. Akun per orang (B12), grup `efn-esb-ops`. `AllowUsers` = daftar orang. `AllowTcpForwarding no`. Disarankan kunci FIDO2 (`ssh-keygen -t ed25519-sk`). |
| A2 WireGuard | Subnet `10.91.0.0/24`, hub `10.91.0.1`, SSH hanya `in on wg0 from 10.91.0.0/24`. Peer hanya laptop operator terdaftar. |
| A3 nftables | Diganti tabel `efn_esb` (B7), yang juga memuat penjaga peer WG + DNAT. |
| A4 swap | **Tanpa swap** (secret/data bisa ter-page ke disk tak terenkripsi): `swapoff -a` dan hapus baris swap di `/etc/fstab`. Core dump dimatikan (bawah). |
| A5 Docker | Rootful, **tanpa grup `docker`**: operator memakai `sudo docker ...` agar setiap perintah tercatat per orang (sudo log + auditd). data-root di LUKS (B4). |
| A6–A11 | Tidak berlaku (tanpa Claude Code, tanpa build dev, tanpa backup repo). |

```bash
sudo tee /etc/sysctl.d/90-efn-esb.conf > /dev/null <<'EOF'
# Host B: tanpa core dump (memori proses bisa memuat secret/data)
kernel.core_pattern=|/bin/false
fs.suid_dumpable=0
vm.swappiness=0
EOF
sudo sysctl --system > /dev/null
sudo swapoff -a && sudo sed -i.bak '/\sswap\s/d' /etc/fstab && swapon --show     # kosong
```

### B1.2 Varian satu host dengan A (hanya bila VM terpisah benar-benar tidak tersedia)

Syarat **semua** wajib, dicatat sebagai risiko diterima di ADR-043:
1. A memakai **A5b rootless**; `efndev` tidak di grup `docker`, tidak punya sudo, dan tidak di `efn-esb-ops`.
2. B memakai daemon Docker **rootful** sistem dengan data-root di LUKS (B4); hanya akun B12 yang bisa `sudo`.
3. `efnadmin` (sudo) di host itu otomatis punya akses ke data B → orangnya wajib ada di register B12.
4. Hub WireGuard dipakai bersama: peer tahap A dan B dicatat terpisah; firewall B7 tetap berlaku.
5. Sisa risiko: kerentanan kernel/escape container di lingkungan dev yang menjalankan kode tidak tepercaya (dependency
   baru, kode buatan agen) bisa menjangkau data B. Itulah alasan rekomendasi B1.1.

## B2. Unlock, pemasangan, dan perilaku boot

Tanpa server Tang/TPM di VPS, LUKS dibuka **manual** setelah setiap reboot (fail closed: tanpa operator, data tidak
terbuka dan stack tidak jalan). Prosedur setelah reboot:

```bash
sudo cryptsetup open <DEVICE_ATAU_FILE> efn-esb        # minta passphrase (pemegang: key-custody.md)
sudo mount /dev/mapper/efn-esb /srv/efn-esb
sudo auditctl -R /etc/efn/audit-esb-paths.rules         # watch path di LUKS (B12)
sudo systemctl start docker
cd /srv/efn-esb/repo/infra/compose && sudo docker compose -p efn-esb ps
```

## B3. Enkripsi at rest (LUKS2)

Yang harus berada di volume terenkripsi: data-root Docker (volume ClickHouse, log container esb-extract, lapisan
tulis container ClickHouse yang memuat konfigurasi user hasil entrypoint), salinan repo + `secrets/` (password ESB,
CA host B, kunci disk ClickHouse), `.env` (username ESB), staging backup, konfigurasi rclone. ClickHouse sendiri juga
mengenkripsi part data (`encrypted-storage.xml`, kunci `clickhouse_disk_key`), tetapi metadata/DDL tidak, dan kuncinya
berada di `secrets/` → LUKS tetap wajib (luks-disk.md).

Keterbatasan VPS: LUKS melindungi dari snapshot/disk bekas/backup penyedia, **bukan** dari penyedia hypervisor yang
membaca memori VM yang sedang berjalan. Catat di ADR-043.

### B3.1 Block volume tambahan (disarankan)

```bash
lsblk -f                                   # identifikasi disk kosong, mis. /dev/vdb (TANPA FSTYPE)
sudo -i
DEV=/dev/vdb
wipefs -n "$DEV"                            # harus kosong; bila ada tanda tangan -> berhenti, salah disk
cryptsetup luksFormat --type luks2 --cipher aes-xts-plain64 --key-size 512 --pbkdf argon2id "$DEV"
cryptsetup open "$DEV" efn-esb
mkfs.ext4 -L efn-esb /dev/mapper/efn-esb
install -d -m 0755 /srv/efn-esb
mount /dev/mapper/efn-esb /srv/efn-esb
install -d -m 0711 /srv/efn-esb/docker
install -d -m 0700 /srv/efn-esb/backup /srv/efn-esb/backup/staging
cryptsetup luksHeaderBackup "$DEV" --header-backup-file /root/efn-esb.luksheader
exit
```

Header backup: enkripsi dengan `age` ke kunci publik EFN, pindahkan keluar host, lalu `shred -u /root/efn-esb.luksheader`.
Jangan menambahkan entri `/etc/crypttab`/`/etc/fstab` otomatis (unlock manual B2).

### B3.2 Tanpa disk tambahan: LUKS di atas file

```bash
sudo -i
F=/var/lib/efn-esb.luks
[ -f "$F" ] || { fallocate -l 100G "$F"; chmod 600 "$F"; }
cryptsetup luksFormat --type luks2 --cipher aes-xts-plain64 --key-size 512 --pbkdf argon2id "$F"
cryptsetup open "$F" efn-esb               # cryptsetup memasang loop device otomatis
# lanjut mkfs/mount/install -d persis seperti B3.1 (header backup: luksHeaderBackup "$F")
exit
```

Keterbatasan: berbagi I/O dan kapasitas disk root; snapshot disk root oleh penyedia memuat file terenkripsi (aman)
**dan** semua yang di luar LUKS (journal, `/root`, `/tmp`) → jangan pernah menaruh data/secret ESB di luar
`/srv/efn-esb`. `fscrypt`/`gocryptfs` **tidak** cocok untuk data-root Docker (overlay2 di atas FUSE/fscrypt tidak
didukung); bila dipakai, hanya untuk folder kecil (staging backup), bukan pengganti LUKS.

## B4. Docker dengan data-root di LUKS

```bash
# Pasang paket sesuai A5.1, lalu (host BARU: belum ada container/volume):
sudo -i
EMPTY=0
if [ "$(docker ps -aq | wc -l)" -eq 0 ] && [ "$(docker volume ls -q | wc -l)" -eq 0 ]; then EMPTY=1
else echo "TIDAK kosong: jangan hapus /var/lib/docker; migrasi dengan rsync (luks-disk.md §Migrasi)"; fi
systemctl disable --now docker.service docker.socket
cat > /etc/docker/daemon.json <<'EOF'
{
  "data-root": "/srv/efn-esb/docker",
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" },
  "features": { "containerd-snapshotter": false }
}
EOF
# Docker tidak boleh start bila LUKS belum terpasang (data-root kosong di disk root)
install -d /etc/systemd/system/docker.service.d
printf '[Unit]\nConditionPathIsMountPoint=/srv/efn-esb\nRequiresMountsFor=/srv/efn-esb\n' > /etc/systemd/system/docker.service.d/10-efn-esb.conf
systemctl daemon-reload
[ "$EMPTY" = 1 ] && rm -rf /var/lib/docker     # hanya bila kosong; isinya dibuat ulang di data-root LUKS
systemctl start docker
docker info --format 'root={{.DockerRootDir}} driver={{.Driver}}'    # root=/srv/efn-esb/docker driver=overlay2
exit
```

`containerd-snapshotter: false`: pada instalasi Docker baru, image store containerd bisa menjadi default dan
menyimpan lapisan container di `/var/lib/containerd` (di luar LUKS). Verifikasi `driver=overlay2`; bila tetap
`overlayfs`, pindahkan juga `root` containerd ke `/srv/efn-esb/containerd` (`/etc/containerd/config.toml`).
Docker tidak di-enable saat boot (B2 menyalakannya setelah unlock).

## B5. Repo dan image

```bash
sudo -i
groupadd -f efn-esb-ops
git clone https://github.com/<org>/<repo>.git /srv/efn-esb/repo       # repo publik: tanpa kredensial GitHub di B
git -C /srv/efn-esb/repo checkout --detach <COMMIT_SHA_DISETUJUI>
chown -R root:efn-esb-ops /srv/efn-esb/repo && chmod -R g+rX,o-rwx /srv/efn-esb/repo
exit
```

Image `esb-extract` — pilih satu:

1. **Disarankan: image bertanda tangan dari CI** (`build-push.yml`, cosign keyless). Pasang cosign dari rilis resmi
   (versi sama dengan host produksi, key-custody.md §4) dengan verifikasi checksum, lalu:
   ```bash
   sudo docker login ghcr.io -u <user>          # hanya bila paket privat; token read:packages, tempel saat diminta
   sudo docker pull ghcr.io/<org>/esb-extract@sha256:<DIGEST>
   sudo docker logout ghcr.io                   # hapus kredensial dari /root/.docker/config.json
   sudo cosign verify \
     --certificate-identity "https://github.com/<org>/<repo>/.github/workflows/build-push.yml@refs/heads/main" \
     --certificate-oidc-issuer https://token.actions.githubusercontent.com \
     --certificate-github-workflow-sha <COMMIT_SHA_DISETUJUI> \
     --certificate-github-workflow-repository <org>/<repo> \
     ghcr.io/<org>/esb-extract@sha256:<DIGEST> > /dev/null && echo "cosign OK"
   ```
2. **Build di B** dari commit yang disetujui: `sudo $DC build esb-extract` (butuh egress host ke Docker Hub + PyPI
   saat build; buka jendela egress B7.3 lalu tutup).

ClickHouse: `sudo docker pull clickhouse/clickhouse-server:24.8-alpine@sha256:b002e56ed5c16e224c312527f6fcba7e77216fec5d7a88a7828f59efc614feb5`
(digest = integritas, sama dengan compose).

## B6. Secret dan `.env` host B

Secret B dibuat **di B** (tidak pernah disalin dari A atau dari laptop). `gen-secrets.sh` tanpa `APP_ENV` di shell
membuat CA lokal khusus host B (kunci CA di LUKS) + semua leaf; secret service yang tidak dijalankan ikut terbuat
(tidak dipakai). Pengecualian **SEC-199**: secret migrasi G43 (`clickhouse_password_mig`, `migration_bundle_key`)
hanya bernilai nyata bila `APP_ENV` = dev/ci/test; di B harus `disabled` (tanpa itu `clickhouse-init` membuat user
`efn_mig` permanen dengan hak baca customer/AR/AP, dan kunci bundel berada di host pihak ketiga).

**Migrasi ESB → Odoo (G43) TIDAK dijalankan di host B**: tidak ada `esb-migrate`, `esb_to_odoo.py export`/`writeback`/
`reconcile`, maupun `run-import.sh` di B. Data ESB dipindah dulu ke infrastruktur EFN (ADR-043 §3.4 "Pemindahan"),
lalu migrasi dijalankan di sana sesuai [migrasi-esb-odoo.md](migrasi-esb-odoo.md).

```bash
sudo -i
cd /srv/efn-esb/repo
env -u APP_ENV bash scripts/gen-secrets.sh            # secrets/ 0700 root, file 0644 (pola gen-secrets.sh)
# SEC-199: paksa placeholder (juga bila host pernah menjalankan versi gen-secrets lama yang membuat nilai nyata)
printf disabled > secrets/clickhouse_password_mig.txt
printf disabled > secrets/migration_bundle_key.txt
grep -qx disabled secrets/clickhouse_password_mig.txt && grep -qx disabled secrets/migration_bundle_key.txt \
  && echo "secret migrasi: disabled (OK)" || echo "GAGAL: secret migrasi bukan disabled - jangan lanjut ke B8"
cp -n infra/compose/.env.example infra/compose/.env && chmod 600 infra/compose/.env
exit
```

Password ESB per brand: ikuti **esb-extract.md §2** ("Brand lain") di shell root `sudo -i` dari
`/srv/efn-esb/repo`, satu brand per kali, hanya brand yang disetujui (B0 no. 2). Jangan memakai file
`esb_core_password.txt` lama dari laptop.

Isi `infra/compose/.env` dengan editor (`sudo nano`):

| Kunci | Nilai | Catatan |
|---|---|---|
| `APP_ENV` | `production` | aturan prod: `--ignore-window` ditolak (SEC-185) |
| `ESB_ENABLED` | `false` dulu | dinyalakan di B8 |
| `ESB_BRANDS` | `pb` (mulai satu brand) | hanya brand dengan secret terisi |
| `ESB_USER_<BRAND>` | user API **read-only** (Q-ESB1) | tidak pernah di repo/tiket (SEC-180) |
| `ESB_ALLOW_IGNORE_WINDOW` | `false` | |
| `IMAGE_ESB_EXTRACT` | `ghcr.io/<org>/esb-extract@sha256:<DIGEST>` (opsi B5.1) | |
| `EFN_PUBLIC_BIND` | `127.0.0.1` | jaga-jaga bila Traefik tak sengaja dinyalakan |
| `INSIGHT_ENABLED` | `false` | insight-web tidak dijalankan di B (B8.3) |

Variabel kerja (semua perintah B berikut memakai ini, sebagai root atau dengan `sudo`):

```bash
cd /srv/efn-esb/repo/infra/compose
# --profile core hanya agar validasi compose lolos (metabase/etl bergantung pada postgres); service core TIDAK dinyalakan.
DC="docker compose -p efn-esb -f docker-compose.yml -f docker-compose.esb.yml --env-file .env --profile core --profile data"
chq() { $DC exec -T clickhouse sh -c 'CLICKHOUSE_USER="${CLICKHOUSE_USER:-efn}" CLICKHOUSE_PASSWORD="$(cat /run/secrets/clickhouse_password)" clickhouse-client --host clickhouse --multiquery'; }
$DC config -q && echo "compose OK"
$DC config --format json | jq '.services["esb-extract"].environment | {APP_ENV, ESB_ENABLED, ESB_ALLOW_IGNORE_WINDOW, ESB_BRANDS}'
```

**Larangan:** jangan pernah `$DC up -d` tanpa nama service (akan menyalakan seluruh profil core + data: Odoo, BFF,
Metabase, Traefik). Selalu sebut `clickhouse clickhouse-init esb-extract`.

## B7. Firewall egress

### B7.1 Egress container (wajib): hanya `services.esb.co.id:443`

Overlay ESB memberi `esb-extract` network `egress-esb` bersubnet tetap `172.30.8.0/24` (SEC-183), sehingga aturan per
service bisa dipasang di host (sisa risiko esb-extract.md §9 ditutup di sini).

```bash
sudo install -d -m 0755 /etc/efn
sudo tee /etc/efn/efn-esb.nft > /dev/null <<'EOF'
#!/usr/sbin/nft -f
# Host B (runbook vps-bima.md §B7): egress esb-extract hanya ke IP services.esb.co.id:443 + DNS; penjaga peer WG/DNAT.
# Set IP diisi /usr/local/sbin/efn-esb-egress-refresh (timer). Idempoten & atomik.
define PUB_IF = "eth0"
define WG_IF = "wg0"
define ESB_EGRESS_NET = 172.30.8.0/24

table inet efn_esb
delete table inet efn_esb

table inet efn_esb {
  set esb_v4 { type ipv4_addr; }
  set dns_v4 { type ipv4_addr; }

  chain forward {
    type filter hook forward priority filter - 10; policy accept;
    iifname $WG_IF counter drop
    oifname $WG_IF ct state established,related accept
    oifname $WG_IF counter drop
    iifname $PUB_IF ct state new ct status dnat counter drop
    ip saddr $ESB_EGRESS_NET ct state established,related accept
    ip saddr $ESB_EGRESS_NET ip daddr @esb_v4 tcp dport 443 accept
    ip saddr $ESB_EGRESS_NET ip daddr @dns_v4 udp dport 53 accept
    ip saddr $ESB_EGRESS_NET ip daddr @dns_v4 tcp dport 53 accept
    ip saddr $ESB_EGRESS_NET limit rate 6/minute log prefix "efn-esb-egress-drop "
    ip saddr $ESB_EGRESS_NET counter drop
  }
}
EOF
sudo sed -i 's/^define PUB_IF = .*/define PUB_IF = "<ANTARMUKA_PUBLIK>"/' /etc/efn/efn-esb.nft

sudo tee /usr/local/sbin/efn-esb-egress-refresh > /dev/null <<'EOF'
#!/usr/bin/env bash
# Mengisi set IP tujuan egress esb-extract (runbook vps-bima.md §B7). Gabungan 3 kali resolusi (DNS round-robin/CDN).
set -euo pipefail
HOST="services.esb.co.id"
RESOLV="/run/systemd/resolve/resolv.conf"
join() { local IFS=,; printf '%s' "$*"; }
mapfile -t esb < <(for _ in 1 2 3; do getent ahostsv4 "$HOST" || true; sleep 1; done | awk '{print $1}' | sort -u)
if (( ${#esb[@]} == 0 )); then
  logger -p user.err -t efn-esb-egress "resolusi ${HOST} gagal; set lama dipertahankan"
  exit 1
fi
mapfile -t dns < <(awk '$1 == "nameserver" && $2 ~ /^[0-9.]+$/ {print $2}' "$RESOLV" | sort -u)
{
  echo "flush set inet efn_esb esb_v4"
  echo "add element inet efn_esb esb_v4 { $(join "${esb[@]}") }"
  echo "flush set inet efn_esb dns_v4"
  if (( ${#dns[@]} > 0 )); then echo "add element inet efn_esb dns_v4 { $(join "${dns[@]}") }"; fi
} | nft -f -
logger -t efn-esb-egress "esb_v4=$(join "${esb[@]}") dns_v4=$(join "${dns[@]}")"
EOF
sudo chmod 0755 /usr/local/sbin/efn-esb-egress-refresh

sudo tee /etc/systemd/system/efn-esb-nft.service > /dev/null <<'EOF'
[Unit]
Description=EFN host B: tabel nftables efn_esb (egress esb-extract)
Before=docker.service wg-quick@wg0.service
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/efn/efn-esb.nft
ExecStartPost=-/usr/local/sbin/efn-esb-egress-refresh
ExecStop=/usr/sbin/nft delete table inet efn_esb

[Install]
WantedBy=multi-user.target
EOF
sudo tee /etc/systemd/system/efn-esb-egress-refresh.service > /dev/null <<'EOF'
[Unit]
Description=EFN host B: perbarui IP services.esb.co.id
Requires=efn-esb-nft.service
After=efn-esb-nft.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/efn-esb-egress-refresh
EOF
sudo tee /etc/systemd/system/efn-esb-egress-refresh.timer > /dev/null <<'EOF'
[Unit]
Description=EFN host B: perbarui IP egress ESB tiap 5 menit

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now efn-esb-nft.service efn-esb-egress-refresh.timer
sudo nft list table inet efn_esb | grep -A2 'set esb_v4'
```

Uji (setelah B6, sebelum password dipakai):

```bash
# Harus GAGAL (timeout): host lain
$DC run --rm --no-deps --pull never --entrypoint python esb-extract -c \
  "import socket; socket.create_connection(('github.com', 443), 5)" && echo "SALAH: egress terbuka" || echo "OK: diblok"
# Harus BERHASIL: ESB
$DC run --rm --no-deps --pull never --entrypoint python esb-extract -c \
  "import socket; socket.create_connection(('services.esb.co.id', 443), 5); print('OK: ESB terjangkau')"
sudo nft list chain inet efn_esb forward | grep -E 'counter|drop'
journalctl -k --since -10min | grep efn-esb-egress-drop | tail -3
```

Keterbatasan: allowlist berbasis IP hasil DNS. Bila ESB berada di belakang CDN, IP bisa berganti (koneksi gagal
sampai refresh berikut; esb-extract mengulang otomatis) dan IP CDN dipakai bersama situs lain (allowlist efektif =
seluruh situs di IP itu, dilengkapi allowlist host di aplikasi). DNS ke resolver host tetap terbuka (kanal
eksfiltrasi DNS = sisa risiko; mitigasi lanjutan: resolver lokal yang hanya menjawab `services.esb.co.id`).

### B7.2 Egress host

UFW `outgoing` hanya mengatur proses host (OUTPUT), **tidak** container (FORWARD; itu B7.1).

```bash
sudo ufw default deny outgoing
sudo ufw allow out 53 comment 'DNS'
sudo ufw allow out 123/udp comment 'NTP'
sudo ufw allow out 80/tcp comment 'apt mirror Ubuntu'
sudo ufw allow out 443/tcp comment 'apt, GitHub, registry, rclone ke storage EFN'
sudo ufw status verbose
```

Port 443 host masih ke tujuan mana pun (mirror Ubuntu, GitHub, ghcr/Docker Hub, storage EFN memakai banyak IP CDN
yang berganti; UFW tidak bisa memfilter per domain). Opsi lebih ketat (keputusan ops): hapus `allow out 443/tcp` dan
izinkan hanya IP storage EFN (`sudo ufw allow out to <IP_STORAGE_EFN> port 443 proto tcp`), lalu buka 443 sementara
saat jendela pemeliharaan (B7.3); atau egress proxy HTTP dengan allowlist domain (`/etc/systemd/system/docker.service.d/http-proxy.conf`
+ `https_proxy` untuk apt/git) — belum disediakan repo.

### B7.3 Jendela pemeliharaan (build/pull/upgrade)

```bash
sudo ufw allow out 443/tcp comment 'JENDELA SEMENTARA'   # bila opsi ketat dipakai
# ... apt-get upgrade / docker pull / build ...
sudo ufw delete allow out 443/tcp
```

## B8. Menyalakan ekstraksi

### B8.1 ClickHouse + skema

```bash
$DC up -d --no-build --pull never clickhouse clickhouse-init
$DC ps -a clickhouse clickhouse-init          # clickhouse healthy; clickhouse-init Exited (0)
$DC logs --tail=20 clickhouse-init            # berakhir "... diterapkan"; memuat "user efn_mig dihapus (migrasi nonaktif)"
chq <<'SQL'
SELECT database, name, storage_policy FROM system.tables WHERE database = 'efn_esb';   -- storage_policy = encrypted
SHOW USERS;                                                                            -- SEC-199: TANPA efn_mig
SQL
```

`efn_mig` muncul di `SHOW USERS` = berhenti: kembalikan kedua secret migrasi ke `disabled` (B6), jalankan ulang
`$DC up -d --force-recreate --no-build --pull never clickhouse-init`, cek lagi (B0 no. 13).

### B8.2 Probe lalu loop incremental (esb-extract.md §3–§4)

```bash
$DC run --rm --no-deps --pull never esb-extract python -m efn_esb probe --brand pb     # login OK + JUMLAH cabang
# .env: ESB_ENABLED=true  (editor), lalu:
$DC up -d --no-build --pull never esb-extract
$DC logs -f --tail=100 esb-extract           # tidak boleh ada password, token Bearer/JWT
```

Brand berikutnya: secret (esb-extract.md §2) → probe → tambahkan ke `ESB_BRANDS` → `$DC up -d --no-build --pull never esb-extract`.

### B8.3 insight-web (opsional) — tidak dijalankan di tahap B awal

insight-web butuh Traefik di mesin data, jalur WireGuard staf, dan SSO dari Odoo EFN dengan kunci `insight_sso_key`
yang sama (insight-web.md §8: desain lintas mesin belum ada). Menjalankannya di VPS Bima juga membuka data klien ke
browser lewat host pihak ketiga. Bila dibutuhkan: ADR/kontrak terpisah + review security dulu. Verifikasi data di B
cukup lewat `chq` oleh operator terdaftar.

## B9. Backfill per brand (jendela malam)

Ikuti **esb-extract.md §5** persis; ringkasnya, satu brand per proses, dimulai kapan saja (proses menunggu sendiri
sampai jendela `22:00-05:00` WIB), `--ignore-window` ditolak karena `APP_ENV=production`.

```bash
# 1. Keluarkan brand X dari loop: .env ESB_BRANDS tanpa X -> $DC up -d --no-build --pull never esb-extract
#    (atau $DC stop esb-extract bila hanya X yang aktif)
# 2. Backfill (contoh pb, entitas sales; urutan entitas: production, delivery, transfer, lalu sales)
$DC run -d --name efn-esb-backfill-pb esb-extract \
  python -m efn_esb backfill --brand pb --from 2024-11-17 --to 2026-11-16 --entity sales
docker logs -f efn-esb-backfill-pb
# 3. Selesai/hentikan: docker stop efn-esb-backfill-pb; docker rm efn-esb-backfill-pb; masukkan X lagi ke ESB_BRANDS
```

Rentang tanggal mengikuti persetujuan klien (B0 no. 2), bukan otomatis 24 bulan. Jangan menaikkan `ESB_RATE_PER_SEC`
di atas 2; percepat dengan brand berbeda di malam berbeda.

## B10. Pemantauan dan heartbeat

Manual (esb-extract.md §6): `chq` dengan query `extract_run`, `extract_checkpoint FINAL`, `v_esb_data_quality`.

Dead-man otomatis (tidak ada Prometheus di B). Skrip menulis ke journal dengan prioritas `err`; kanal keluar
(keputusan §0.3 no. 6) disambungkan pada baris `ALERT_CMD`.

```bash
sudo tee /usr/local/sbin/efn-esb-heartbeat > /dev/null <<'EOF'
#!/usr/bin/env bash
# Dead-man host B (runbook vps-bima.md §B10): loop incremental ESB, brand ditangguhkan, kesegaran backup.
set -euo pipefail
COMPOSE_DIR="/srv/efn-esb/repo/infra/compose"
STAGING="/srv/efn-esb/backup/staging"
ALERT_CMD="${ALERT_CMD:-}"      # mis. curl ke push monitor Uptime Kuma EFN lewat WG; kosong = journal saja
cd "$COMPOSE_DIR"
dc() { docker compose -p efn-esb -f docker-compose.yml -f docker-compose.esb.yml --env-file .env --profile core --profile data "$@"; }
# shellcheck disable=SC2016  # $Q dan secret diekspansi DI DALAM container, bukan di host
q() { dc exec -T -e Q="$1" clickhouse sh -c 'CLICKHOUSE_USER="${CLICKHOUSE_USER:-efn}" CLICKHOUSE_PASSWORD="$(cat /run/secrets/clickhouse_password)" clickhouse-client --host clickhouse --query "$Q"' < /dev/null; }
problems=()
if grep -qx 'ESB_ENABLED=true' .env; then
  interval=$(sed -n 's/^ESB_INTERVAL_SECONDS=\([0-9]*\).*/\1/p' .env); interval="${interval:-3600}"
  age_s=$(q "SELECT dateDiff('second', max(started_at), now()) FROM efn_esb.extract_run WHERE mode = 'run'" || echo 999999999)
  (( age_s <= 2 * interval )) || problems+=("run incremental terakhir ${age_s}s lalu (> 2x ${interval}s)")
  susp=$(q "SELECT count() FROM efn_esb.extract_run WHERE status = 'auth_suspended' AND started_at > now() - INTERVAL 1 DAY" || echo -1)
  [[ "$susp" == "0" ]] || problems+=("brand auth_suspended/kueri gagal (${susp}) - esb-extract.md §7")
fi
latest=$(find "$STAGING" -mindepth 1 -maxdepth 1 -type d -name '20*' -newermt '-26 hours' -print -quit)
[[ -n "$latest" ]] || problems+=("tidak ada backup sukses < 26 jam di ${STAGING}")
if (( ${#problems[@]} > 0 )); then
  for p in "${problems[@]}"; do logger -p user.err -t efn-esb-heartbeat "$p"; done
  [[ -z "$ALERT_CMD" ]] || bash -c "$ALERT_CMD" || true
  exit 1
fi
logger -t efn-esb-heartbeat "ok"
EOF
sudo chmod 0755 /usr/local/sbin/efn-esb-heartbeat
printf '[Unit]\nDescription=EFN host B heartbeat\nConditionPathIsMountPoint=/srv/efn-esb\n\n[Service]\nType=oneshot\nExecStart=/usr/local/sbin/efn-esb-heartbeat\n' \
  | sudo tee /etc/systemd/system/efn-esb-heartbeat.service > /dev/null
printf '[Unit]\nDescription=EFN host B heartbeat per jam\n\n[Timer]\nOnCalendar=hourly\nRandomizedDelaySec=300\n\n[Install]\nWantedBy=timers.target\n' \
  | sudo tee /etc/systemd/system/efn-esb-heartbeat.timer > /dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now efn-esb-heartbeat.timer
journalctl -t efn-esb-heartbeat -p err --since -1d
```

Catatan: backup belum berjalan pada hari pertama → heartbeat melaporkan "tidak ada backup" sampai B11 aktif (sengaja).

## B11. Backup terenkripsi ClickHouse dan uji restore

Repo belum punya backup ClickHouse (WAL-G hanya PostgreSQL). Konfigurasi `BACKUP ... TO Disk/File` ClickHouse butuh
konfigurasi server tambahan (perubahan compose), jadi dipakai **ekspor logis** per tabel (`FORMAT Native`) +
`SHOW CREATE TABLE`, dienkripsi `age` ke dua penerima: **kunci EFN** (privat dipegang EFN, offline) dan **kunci uji
restore host B** (privat di LUKS; penyerang yang sudah di B memang sudah bisa membaca data hidup). Lalu `rclone` ke
storage milik EFN. Data ESB juga bisa ditarik ulang, tetapi backfill ulang = ±11 malam per brand beban ke API
produksi klien, jadi backup tetap wajib.

```bash
sudo apt-get install -y age zstd rclone
sudo -i
cd /srv/efn-esb/backup
[ -s restore-test.agekey ] || age-keygen -o restore-test.agekey 2> /dev/null
chmod 600 restore-test.agekey
{ echo '<KUNCI_PUBLIK_AGE_EFN age1...>'; age-keygen -y restore-test.agekey; } > recipients.txt
cat > backup.conf <<'EOF'
# runbook vps-bima.md §B11
AGE_RECIPIENTS_FILE=/srv/efn-esb/backup/recipients.txt
RCLONE_CONFIG=/srv/efn-esb/backup/rclone.conf
RCLONE_REMOTE=<remote>:<bucket-efn>/efn-esb-bima
KEEP_LOCAL=3
EOF
RCLONE_CONFIG=/srv/efn-esb/backup/rclone.conf rclone config   # kredensial storage EFN (sebaiknya write-only prefix ini)
chmod 600 backup.conf rclone.conf
exit
```

```bash
sudo tee /usr/local/sbin/efn-esb-backup > /dev/null <<'EOF'
#!/usr/bin/env bash
# Backup logis terenkripsi tabel ESB di ClickHouse host B (runbook vps-bima.md §B11). Root.
set -euo pipefail
umask 077
COMPOSE_DIR="/srv/efn-esb/repo/infra/compose"
CONF="/srv/efn-esb/backup/backup.conf"
# shellcheck source=/dev/null
. "$CONF"
: "${AGE_RECIPIENTS_FILE:?}" "${RCLONE_CONFIG:?}" "${RCLONE_REMOTE:?}" "${KEEP_LOCAL:=3}"
export RCLONE_CONFIG
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STAGING="/srv/efn-esb/backup/staging"
WORK="${STAGING}/.${STAMP}.partial"
cd "$COMPOSE_DIR"
dc() { docker compose -p efn-esb -f docker-compose.yml -f docker-compose.esb.yml --env-file .env --profile core --profile data "$@"; }
# shellcheck disable=SC2016  # $Q dan secret diekspansi DI DALAM container, bukan di host
q() { dc exec -T -e Q="$1" clickhouse sh -c 'CLICKHOUSE_USER="${CLICKHOUSE_USER:-efn}" CLICKHOUSE_PASSWORD="$(cat /run/secrets/clickhouse_password)" clickhouse-client --host clickhouse --query "$Q"' < /dev/null; }

mapfile -t tables < <(q "SELECT database || '.' || name FROM system.tables
  WHERE engine LIKE '%MergeTree' AND (database = 'efn_esb' OR (database = 'efn_dwh' AND name LIKE '%esb%'))
  ORDER BY name != 'extract_checkpoint', name != 'extract_run', database, name FORMAT TSV")
(( ${#tables[@]} > 0 )) || { logger -p user.err -t efn-esb-backup "tidak ada tabel ESB"; exit 1; }

# Konsistensi: loop incremental berhenti, merge dibekukan (count == isi ekspor). Backfill di luar jendela malam idle.
was_running=0
if [[ -n "$(dc ps -q --status running esb-extract)" ]]; then was_running=1; dc stop esb-extract > /dev/null; fi
cleanup() {
  for t in "${tables[@]}"; do q "SYSTEM START MERGES ${t}" > /dev/null 2>&1 || true; done
  (( was_running == 0 )) || dc start esb-extract > /dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$WORK"
for t in "${tables[@]}"; do
  q "SYSTEM STOP MERGES ${t}"
  q "SHOW CREATE TABLE ${t} FORMAT TSVRaw" > "${WORK}/${t}.sql"
  rows=$(q "SELECT count() FROM ${t}")
  q "SELECT * FROM ${t} FORMAT Native" | zstd -q -T0 | age -R "$AGE_RECIPIENTS_FILE" > "${WORK}/${t}.native.zst.age"
  printf '%s\t%s\n' "$t" "$rows" >> "${WORK}/MANIFEST.tsv"
  q "SYSTEM START MERGES ${t}"
done
( cd "$WORK" && sha256sum -- *.sql *.age MANIFEST.tsv > SHA256SUMS )
mv "$WORK" "${STAGING}/${STAMP}"
rclone copy --immutable "${STAGING}/${STAMP}" "${RCLONE_REMOTE}/${STAMP}"
find "$STAGING" -mindepth 1 -maxdepth 1 -type d -name '20*' | sort | head -n "-${KEEP_LOCAL}" | xargs -r rm -rf --
logger -t efn-esb-backup "ok ${STAMP} tabel=${#tables[@]}"
EOF
sudo chmod 0755 /usr/local/sbin/efn-esb-backup
```

Restore test **nyata**: memulihkan ke container ClickHouse sementara (`--network none`, kunci disk acak sekali pakai,
volume anonim di data-root LUKS), lalu mencocokkan jumlah baris per tabel dengan manifest.

```bash
sudo tee /usr/local/sbin/efn-esb-restore-test > /dev/null <<'EOF'
#!/usr/bin/env bash
# Uji pulih backup ESB ke container sementara (runbook vps-bima.md §B11). Root. Arg: direktori backup lokal.
set -euo pipefail
umask 077
SRC="${1:?direktori backup (berisi MANIFEST.tsv)}"
KEY="/srv/efn-esb/backup/restore-test.agekey"
REPO="/srv/efn-esb/repo"
IMG="clickhouse/clickhouse-server:24.8-alpine@sha256:b002e56ed5c16e224c312527f6fcba7e77216fec5d7a88a7828f59efc614feb5"
NAME="efn-esb-restore-test"
docker rm -fv "$NAME" > /dev/null 2>&1 || true
trap 'docker rm -fv "$NAME" > /dev/null 2>&1 || true' EXIT
( cd "$SRC" && sha256sum -c --quiet SHA256SUMS )
docker run -d --name "$NAME" --network none \
  -e CLICKHOUSE_USER=rt -e CLICKHOUSE_PASSWORD="$(openssl rand -hex 16)" \
  -e CLICKHOUSE_DISK_KEY_HEX="$(openssl rand -hex 32)" \
  -v "${REPO}/infra/clickhouse/config.d/encrypted-storage.xml:/etc/clickhouse-server/config.d/encrypted-storage.xml:ro" \
  -v /var/lib/clickhouse "$IMG" > /dev/null
ch() { docker exec "$NAME" clickhouse-client "$@"; }           # tanpa stdin
chi() { docker exec -i "$NAME" clickhouse-client "$@"; }         # stdin = data/DDL
for _ in $(seq 1 60); do ch --query 'SELECT 1' > /dev/null 2>&1 && break; sleep 2; done
ch --query 'CREATE DATABASE IF NOT EXISTS efn_esb'
ch --query 'CREATE DATABASE IF NOT EXISTS efn_dwh'
fail=0
# Manifest dibaca dari fd 3 agar perintah di dalam loop tidak menelan baris manifest dari stdin
while IFS=$'\t' read -r -u 3 t rows; do
  chi --multiquery < "${SRC}/${t}.sql"
  age -d -i "$KEY" "${SRC}/${t}.native.zst.age" | zstd -dq | chi --query "INSERT INTO ${t} FORMAT Native"
  got=$(ch --query "SELECT count() FROM ${t}")
  if [[ "$got" == "$rows" ]]; then echo "OK   ${t} ${got}"; else echo "BEDA ${t} backup=${rows} pulih=${got}" >&2; fail=1; fi
done 3< "${SRC}/MANIFEST.tsv"
ch --query "SELECT brand, entity, max(day) FROM efn_esb.extract_checkpoint FINAL GROUP BY brand, entity ORDER BY brand, entity FORMAT PrettyCompact" || fail=1
if (( fail == 0 )); then logger -t efn-esb-restore-test "ok ${SRC}"; else logger -p user.err -t efn-esb-restore-test "GAGAL ${SRC}"; fi
exit "$fail"
EOF
sudo chmod 0755 /usr/local/sbin/efn-esb-restore-test
```

Jadwal: backup harian 07:00 WIB (setelah jendela backfill, sebelum jam sibuk); uji restore setelah backup penuh
pertama, lalu **bulanan** dari salinan **remote** (`rclone copy <remote>/<STAMP> /srv/efn-esb/backup/rt-<STAMP>`) agar
jalur off-site ikut teruji.

```bash
printf '[Unit]\nDescription=EFN host B backup ClickHouse ESB\nConditionPathIsMountPoint=/srv/efn-esb\n\n[Service]\nType=oneshot\nExecStart=/usr/local/sbin/efn-esb-backup\n' \
  | sudo tee /etc/systemd/system/efn-esb-backup.service > /dev/null
printf '[Unit]\nDescription=EFN host B backup harian 07:00 WIB\n\n[Timer]\nOnCalendar=*-*-* 07:00:00 Asia/Jakarta\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n' \
  | sudo tee /etc/systemd/system/efn-esb-backup.timer > /dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now efn-esb-backup.timer
sudo systemctl start efn-esb-backup.service && journalctl -u efn-esb-backup -n 20 --no-pager
sudo /usr/local/sbin/efn-esb-restore-test "/srv/efn-esb/backup/staging/$(ls /srv/efn-esb/backup/staging | grep '^20' | tail -1)"
```

Uji dengan data dummy sebelum go-live (B0 no. 8): jalankan B8.1 tanpa password ESB, sisipkan satu baris uji ke
`efn_esb.extract_run` lewat `chq`, backup, restore test, lalu `TRUNCATE` baris uji.

Pemulihan nyata (host B hilang): bangun host baru B1–B8.1, lalu impor seperti skrip restore-test tetapi ke service
`clickhouse` proyek `efn-esb` sebagai user admin (dekripsi dengan kunci EFN atau kunci uji restore), tanpa
membuat ulang tabel (skema dari `clickhouse-init`). Prosedur ini belum diuji.

## B12. Akses per orang dan log audit

- Satu akun Linux + satu SSH key + satu peer WireGuard **per orang** Bima yang disetujui EFN. Tidak ada akun bersama.
  Register (disimpan EFN): nama, akun, sidik jari key (`ssh-keygen -lf`), IP peer WG, peran, disetujui oleh, tanggal
  mulai/berakhir. Tinjau bulanan; offboarding di hari yang sama (hapus key, peer WG, akun; rotasi passphrase LUKS bila
  orangnya pemegang).

```bash
sudo -i
U=bima-<nama>
id "$U" >/dev/null 2>&1 || adduser --gecos '' "$U"          # password untuk sudo saja (SSH tetap key-only)
usermod -aG sudo,efn-esb-ops "$U"
install -d -m 0700 -o "$U" -g "$U" "/home/$U/.ssh"
nano "/home/$U/.ssh/authorized_keys" && chown "$U:$U" "/home/$U/.ssh/authorized_keys" && chmod 600 "/home/$U/.ssh/authorized_keys"
# Tambahkan $U ke AllowUsers di /etc/ssh/sshd_config.d/00-efn-hardening.conf, lalu: sshd -t && systemctl reload ssh
exit
```

auditd + sudo log (sudo mencatat setiap perintah per orang di journal; **jangan** aktifkan `log_input` sudo karena
merekam ketikan termasuk password):

```bash
sudo apt-get install -y auditd
sudo tee /etc/audit/rules.d/90-efn-esb.rules > /dev/null <<'EOF'
## Host B (runbook vps-bima.md §B12)
-w /etc/sudoers -p wa -k efn-sudoers
-w /etc/sudoers.d/ -p wa -k efn-sudoers
-w /etc/ssh/sshd_config.d/ -p wa -k efn-sshd
-w /etc/wireguard/ -p wa -k efn-wg
-w /etc/docker/ -p wa -k efn-docker-conf
-w /etc/efn/ -p wa -k efn-conf
-w /usr/bin/docker -p x -k efn-docker-cli
-w /usr/sbin/cryptsetup -p x -k efn-luks
-a always,exit -F arch=b64 -S execve -F euid=0 -F auid>=1000 -F auid!=unset -k efn-root-cmd
EOF
# Path di LUKS dimuat SETELAH unlock (B2), karena watch gagal dipasang bila path belum ada
sudo tee /etc/efn/audit-esb-paths.rules > /dev/null <<'EOF'
-w /srv/efn-esb/repo/secrets/ -p wa -k efn-esb-secrets
-w /srv/efn-esb/repo/infra/compose/.env -p wa -k efn-esb-env
-w /srv/efn-esb/backup/ -p wa -k efn-esb-backup
EOF
sudo augenrules --load && sudo auditctl -R /etc/efn/audit-esb-paths.rules
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=persistent\nSystemMaxUse=2G\n' | sudo tee /etc/systemd/journald.conf.d/90-efn.conf > /dev/null
sudo systemctl restart systemd-journald
# Laporan
sudo ausearch -k efn-root-cmd -i --start today | tail -20
sudo aureport -l --start this-week            # login
journalctl _COMM=sudo --since today
```

Log audit tersimpan di host B (disk root, bukan LUKS: hanya metadata perintah, bukan data). Pengiriman ke Loki EFN
(pola [host-journal-loki.md](host-journal-loki.md)) = opsi bila jalur WG ke EFN tersedia; sampai itu, ekspor mingguan
`ausearch --raw` terenkripsi `age` ke EFN.

## B13. Rotasi dan insiden

- Password ESB / `clickhouse_password_esb`: esb-extract.md §8 (dengan `$DC` host B).
- Passphrase LUKS: `cryptsetup luksAddKey` (baru) → uji buka → `cryptsetup luksRemoveKey` (lama); saat pemegang keluar.
- Kebocoran dicurigai: putus egress dengan esb-extract.md §9 "Darurat" (`docker network disconnect
  efn-esb_egress-esb <container>`; **jangan** `systemctl stop efn-esb-nft`, itu menghapus allowlist), keluarkan brand, minta
  pemilik akun mengganti password, ikuti [security-incident.md](security-incident.md) dan, bila data pribadi terdampak,
  [breach-response-72h.md](breach-response-72h.md).

## B14. Penghapusan data saat pekerjaan selesai

Penghapusan pada disk virtual/SSD dengan `shred` tidak andal (thin provisioning, copy-on-write, snapshot penyedia).
Metode utama = **crypto-erase**: hancurkan semua keyslot LUKS sehingga isi volume tidak bisa didekripsi lagi.

```bash
cd /srv/efn-esb/repo/infra/compose
# 1. Hentikan dan putus jalur ke ESB
sudo $DC stop esb-extract; for c in $(sudo docker ps -aq --filter name=efn-esb-backfill-); do sudo docker rm -f "$c"; done
# 2. (Bila disetujui klien) backup akhir terenkripsi ke storage EFN: sudo /usr/local/sbin/efn-esb-backup
# 3. Hapus stack dan volume
sudo $DC down -v --remove-orphans
sudo systemctl stop docker docker.socket
sudo systemctl disable --now efn-esb-backup.timer efn-esb-heartbeat.timer efn-esb-egress-refresh.timer
# 4. Crypto-erase
sudo umount /srv/efn-esb
sudo cryptsetup close efn-esb
sudo cryptsetup erase <DEVICE_ATAU_FILE>          # hapus semua keyslot (tidak bisa dibatalkan)
sudo cryptsetup luksDump <DEVICE_ATAU_FILE> | sed -n '/^Keyslots:/,/^Tokens:/p'   # tidak boleh ada baris "N: luks2"
# Block volume: blkdiscard <DEVICE> lalu hapus volume di panel penyedia. File: shred -u <FILE> (tambahan saja).
# Header backup LUKS (B3) dan restore-test.agekey ikut dimusnahkan.
```

Lalu, tanpa terkecuali:
1. Hapus semua **snapshot/backup penyedia** VPS B di panel (bisa memuat salinan disk); hapus VM bila tidak dipakai lagi.
2. Pemilik akun ESB tiap brand **mencabut user API** yang dipakai atau mengganti passwordnya (Q-ESB1); konfirmasi
   tertulis.
3. EFN merotasi kredensial storage rclone dan token ghcr (bila dipakai); hapus peer WG dan akun operator B.
4. Backup di storage EFN: ikuti keputusan retensi EFN/DPO (dihapus atau dipertahankan sebagai milik EFN, terenkripsi
   kunci EFN).
5. Periksa tidak ada salinan di luar LUKS: laptop operator, `/tmp`, `/root`, home (`sudo find / -xdev \( -name '*esb*' -o -name '*.native*' \) 2>/dev/null`).
6. **Berita acara penghapusan** (tanggal, metode, saksi EFN, bukti `luksDump`/tangkapan panel) diserahkan ke DPO.

## B15. Belum terverifikasi / sisa risiko

- Seluruh runbook belum dijalankan di VPS nyata; skrip di dalamnya diperiksa manual (tanpa shellcheck/docker).
- Rootless (A5b) dengan stack EFN: IP sumber, `ulimits`, profil `ops` belum diuji.
- Perilaku `containerd-snapshotter` default pada versi Docker yang terpasang: verifikasi `driver=overlay2` di B4.
- Jalur DNS embedded Docker (dari namespace container vs host) menentukan perlu tidaknya set `dns_v4`; aturan memasang
  keduanya. Allowlist IP ESB lemah bila ESB di belakang CDN.
- Ekspor logis `FORMAT Native` + `SHOW CREATE TABLE`: kompatibel untuk versi ClickHouse yang sama (24.8); lintas versi
  belum diuji. Prosedur pemulihan nyata (bukan uji) belum diuji.
- Backup menghentikan loop incremental selama ekspor (menit, tergantung volume); backfill yang sedang di dalam jendela
  malam tidak dihentikan → jadwalkan backup di luar jendela (07:00 WIB).
- LUKS tidak melindungi dari penyedia hypervisor saat VM berjalan; port 443 host B terbuka ke tujuan mana pun kecuali
  opsi ketat B7.2 dipakai.
- Target memori dev K25-06 hanya ditegakkan lewat plafon cgroup, bukan profil compose ramping (usulan di laporan).
