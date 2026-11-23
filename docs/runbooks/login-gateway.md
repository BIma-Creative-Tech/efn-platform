# Runbook: halaman masuk staf `login-gateway` (G62)

- **Keputusan:** ADR-050 (pendekatan diputuskan pemilik proyek 23 Nov 2026; rincian teknis Proposed sampai review security G62).
- **Kontrak:** `docs/04_SERVICE_CONTRACTS.md` §29. **Kode:** `services/login-gateway` (Next.js standalone, target `runtime`).
- **Risiko yang dimitigasi:** R-S01 (tidak ada secret/password di service ini), R-S15 (permukaan serangan: wg-https saja,
  network sendiri tanpa DB/broker/Odoo).

## 1. Ringkas

| Hal | Nilai |
|---|---|
| Host | `login.<TRAEFIK_DOMAIN>` di entrypoint `wg-https` saja (dev: `https://login.efn.local:9443`) |
| Middleware Traefik | `internal-only` (subnet outlet + staf, sama dengan `erp.`), `secure-headers-insight`, `ratelimit-login` |
| Container | `login-gateway` (Next.js, `127.0.0.1:3005`, plaintext hanya di loopback) + `login-gateway-tls` (ghostunnel `:3443`) |
| mTLS | `--allow-cn=traefik`, `--allow-cn=blackbox-exporter`; serversTransport `efn-login-gateway` (serverName `login-gateway`) |
| Network | `login-ingress` (`<EFN_SUBNET_BASE>.22.0/24`, internal): traefik, blackbox-exporter, login-gateway |
| Profil compose | `core` (pintu masuk ERP tidak bergantung pada stack data) |
| Secret | tidak ada di app; sidecar: `efn_ca_crt`, `tls_login_gateway_crt`, `tls_login_gateway_key` |
| Env | `LOGIN_GATEWAY_ERP_URL`, `LOGIN_GATEWAY_INSIGHT_URL` (allowlist redirect, https absolut) |
| Alert | `EfnLoginGatewayDown` (warning, 15 menit, job Prometheus `probe-login`) |

Gateway hanya menampilkan pilihan tujuan lalu redirect ke URL allowlist. Password tetap diketik di formulir Odoo
(`erp.<domain>/web/login`). Bila gateway mati, staf/kasir tetap bisa membuka `erp.<domain>` langsung.

## 2. Boot pertama (dev / stack lokal)

```bash
# 1) Sertifikat: service PKI baru -> CA dev dibuat ulang otomatis + semua leaf diterbitkan ulang.
#    Jalankan TANPA `APP_ENV=production` di shell (APP_ENV=production di infra/compose/.env tidak dibaca skrip ini).
bash scripts/gen-secrets.sh
ls secrets/tls_login_gateway_crt.txt secrets/tls_login_gateway_key.txt

# 2) CA berganti -> semua container memegang bundle lama: recreate seluruh stack sekali.
cd infra/compose
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile core --profile data build login-gateway
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile core --profile data up -d --force-recreate
```

Hosts laptop: `127.0.0.1 login.efn.local` (sertifikat edge dev `*.efn.local` sudah mencakupnya).

Staging/produksi: intermediate **wajib diterbitkan ulang** sebelum deploy pertama yang memuat G62
([internal-pki.md](internal-pki.md#service-baru)); tanpa itu `deploy.sh` berhenti di langkah PKI untuk semua service.
Wrapper host `efn-deploy` juga wajib dipasang ulang (manifest rilis kini 12 image, `IMAGE_LOGIN_GATEWAY`;
[key-custody.md](key-custody.md) §4). DNS split: `login.<domain>` → IP WireGuard server (sama dengan `erp.`); sertifikat
wg-https dari `acme-dns` kini memuat SAN `login.` (`ACME_DOMAINS`), diterbitkan ulang otomatis pada siklus berikutnya.

Gladi lokal prod di port wg-https selain 443 (mis. `efn-prod` 9443): isi `LOGIN_GATEWAY_ERP_URL` /
`LOGIN_GATEWAY_INSIGHT_URL` dengan port di `infra/compose/.env` (contoh di `.env.example`), lalu `up -d login-gateway`.

## 3. Gotcha: recreate app = recreate sidecar (502)

`login-gateway-tls` memakai `network_mode: service:login-gateway`. Bila `login-gateway` dibuat ulang (image baru, env
berubah) namespace lama hilang dan sidecar lama tidak lagi mendengar di IP container baru → Traefik **502**.
Compose menangani ini lewat `depends_on: { login-gateway: { condition: service_started, restart: true } }` selama
perintahnya lewat compose:

```bash
docker compose ... up -d login-gateway          # sidecar ikut di-restart
```

Bila masih 502 (mis. container diubah di luar compose): `docker compose ... up -d --force-recreate login-gateway-tls`.

## 4. Verifikasi

```bash
# Lewat edge (dev, wg-https 127.0.0.1:9443). 200 + JSON {"status":"ok"}; 403 = IP di luar allowlist (multi-env-local §4).
curl -sS --cacert secrets/dev_edge_crt.txt --resolve login.efn.local:9443:127.0.0.1 https://login.efn.local:9443/healthz

# Header keamanan dari Traefik + CSP dari aplikasi
curl -sSI --cacert secrets/dev_edge_crt.txt --resolve login.efn.local:9443:127.0.0.1 https://login.efn.local:9443/ \
  | grep -Ei 'strict-transport|referrer-policy|content-security-policy|x-frame'

# Redirect hanya ke allowlist: Location = LOGIN_GATEWAY_ERP_URL persis
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' --cacert secrets/dev_edge_crt.txt \
  --resolve login.efn.local:9443:127.0.0.1 https://login.efn.local:9443/go/erp

# Container + sidecar sehat; sidecar menolak klien tanpa sertifikat CN sah
docker compose ... ps login-gateway login-gateway-tls
```

Prometheus: target `probe-login` → `probe_success == 1` (`ops.<domain>`, Status > Targets).

## 5. Troubleshooting

| Gejala | Penyebab umum | Tindakan |
|---|---|---|
| 502 dari Traefik | sidecar tertinggal namespace lama (§3) atau app belum sehat | `up -d login-gateway`; bila tetap, `--force-recreate login-gateway-tls`; `logs login-gateway` |
| 403 | IP klien di luar `internal-only` | cek peer WireGuard (outlet/staf) atau `EFN_INTERNAL_EXTRA_CIDR` dev |
| `EfnLoginGatewayDown` | app crash, sertifikat leaf kedaluwarsa/CA berganti tanpa recreate | `logs login-gateway-tls` (`certificate`/`unknown authority`) → `gen-secrets.sh`/deploy lalu recreate pasangan |
| Tombol tujuan tidak aktif | `LOGIN_GATEWAY_*_URL` kosong / bukan https absolut | log startup menyebut nama variabel (nilai tidak dicetak); perbaiki env, `up -d login-gateway` |
| Sertifikat wg-https tidak memuat `login.` | `acme-dns` belum siklus ulang | runbook [acme-dns-token.md](acme-dns-token.md) |

## 6. Rollback

Gateway tidak menyimpan data dan tidak ada di jalur transaksi.

1. Cepat (tanpa rilis): `docker compose ... stop login-gateway-tls login-gateway`. Staf memakai `erp.<domain>/web/login`
   langsung; alert `EfnLoginGatewayDown` akan menyala setelah 15 menit — silence di Alertmanager dengan alasan rollback.
2. Versi sebelumnya: rilis bertanda tangan sebelumnya (production) atau `IMAGE_LOGIN_GATEWAY=<digest lama>` di `.env`
   lokal lalu `up -d login-gateway` (sidecar ikut).
3. Mencabut fitur sepenuhnya = revert commit G62 (compose, Traefik, PKI, rantai rilis) + pasang ulang wrapper `efn-deploy`
   versi 11 image di host.
