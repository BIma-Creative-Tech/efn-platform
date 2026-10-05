# Prosedur: Enkripsi disk host (LUKS2)

Kontrak: `docs/04_SERVICE_CONTRACTS.md` §7.2. Melindungi data at rest yang **tidak** dienkripsi di tingkat aplikasi: volume Docker (PostgreSQL data, MinIO, RabbitMQ, Redis AOF, Loki, Prometheus), metadata ClickHouse, file secret `/opt/efn/secrets`, log Docker. Ancaman: disk/server dicuri, disk dibuang tanpa penghapusan, snapshot VM bocor.

## Kebijakan
- Semua host EFN (staging, prod, DR): partisi data `/var/lib/docker` dan `/opt/efn` di atas LUKS2 (`aes-xts-plain64`, kunci 512 bit, PBKDF `argon2id`).
- Root OS juga LUKS bila hosting mendukung console/remote unlock; minimal partisi data.
- Unlock saat boot: **Tang/Clevis** (network-bound, server Tang di jaringan kantor/VPN) atau TPM2 (`systemd-cryptenroll --tpm2-pcrs=7`) + passphrase pemulihan disimpan offline (brankas, dua orang). Jangan simpan passphrase di disk yang sama.
- VPS cloud tanpa akses console: gunakan disk terenkripsi penyedia (customer-managed key) **dan** LUKS untuk volume data dengan unlock Clevis/Tang; catat sebagai kontrol kompensasi di ADR-022 bila hanya enkripsi penyedia.

## Setup (disk data baru `/dev/sdb`, sebelum Docker dipasang)
```bash
cryptsetup luksFormat --type luks2 --cipher aes-xts-plain64 --key-size 512 --pbkdf argon2id /dev/sdb
cryptsetup open /dev/sdb efn-data
mkfs.ext4 -L efn-data /dev/mapper/efn-data
mkdir -p /srv/efn-data && mount /dev/mapper/efn-data /srv/efn-data
# Docker data-root + /opt/efn di volume terenkripsi
mkdir -p /srv/efn-data/docker /srv/efn-data/opt-efn
echo '{ "data-root": "/srv/efn-data/docker" }' > /etc/docker/daemon.json
ln -s /srv/efn-data/opt-efn /opt/efn
# Unlock otomatis (pilih satu):
clevis luks bind -d /dev/sdb tang '{"url":"https://tang.efn.internal"}'      # Tang (HTTPS)
systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7 /dev/sdb                  # TPM2
# /etc/crypttab: efn-data UUID=<uuid> none luks,_netdev  (Tang) | tpm2-device=auto (TPM2)
# /etc/fstab   : /dev/mapper/efn-data /srv/efn-data ext4 defaults,nofail 0 2
# Docker harus start SETELAH mount: systemctl edit docker -> [Unit] RequiresMountsFor=/srv/efn-data
```
Header LUKS dibackup: `cryptsetup luksHeaderBackup /dev/sdb --header-backup-file efn-data.luksheader` → simpan offline terenkripsi (age), **bukan** di host.

## Verifikasi (audit kuartalan)
```bash
lsblk -o NAME,TYPE,FSTYPE,MOUNTPOINT | grep -E 'crypt|efn'
cryptsetup luksDump /dev/sdb | grep -E 'Version|Cipher|PBKDF'
docker info --format '{{.DockerRootDir}}'      # harus di /srv/efn-data/docker
```
Catat hasil di register kontrol keamanan (DoD keamanan docs/03 §5).

## Migrasi host yang sudah berjalan
Jendela maintenance: base backup (`systemctl start efn-backup.service`) + salin repo backup off-site → stop stack → siapkan disk LUKS baru → `rsync -aHAX /var/lib/docker/ /srv/efn-data/docker/` dan `/opt/efn` → ubah `daemon.json` → start → `scripts/restore-test.sh` → `shred`/`blkdiscard` disk lama.

## Dekomisioning disk
`cryptsetup erase /dev/sdX` (hapus semua keyslot) lalu `blkdiscard`; catat nomor seri disk di register.
