# ADR-004: Topologi Produksi 4 Node VPS/Bare-Metal + Docker Compose (Swarm/k3s saat Scale)

- **Status:** Proposed
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** IT Lead + Finance (OpEx infra) ; Legal/DPO untuk lokasi data (UU PDP)
- **Referensi review:** D-03, §6.3, R-T07, R-T08, R-T09, R-L01

> **Ringkasan manajemen.** Produksi fase 1 berjalan di 4 server (VPS atau bare-metal) di data center Indonesia, dikelola dengan Docker Compose. Kubernetes managed (EKS/GKE) tidak dipakai karena bertentangan dengan arahan "hindari AWS/GCP", mahal, dan butuh keahlian khusus. Target uptime fase 1 adalah 99,5%, bukan 99,9%, sampai database punya failover otomatis.

## Konteks
- Arsitektur v2: **3 server VPS**.
- TSD: **4 node**.
- Cetak Biru: **Kubernetes EKS/GKE + Cloudflare** — padahal dokumen lain menyatakan menghindari AWS/GCP.
- Tidak satu dokumen pun mendefinisikan RPO/RTO, backup, atau observability (R-T08, R-T09).

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu (asumsi, validasi dengan vendor) |
|---|---|---|---|
| A. **4 node + Compose** (edge-app, erp-db, data-ai, replica-backup) | Sesuai TSD; pemisahan beban POS vs analitik vs backup; replica & backup di node terpisah | Failover manual di fase 1; scaling vertikal | ~Rp10–19 jt/bulan untuk 4 node produksi (tanpa GPU, lihat `docs/07_TCO_TEMPLATE.md`); setup 2–3 minggu |
| B. 3 node | Lebih murah | Replica/backup menumpang di node aplikasi → satu kegagalan menghapus primary dan backup sekaligus | ~Rp8–16 jt/bulan |
| C. K8s managed (EKS/GKE) | Auto-healing, scaling | Melanggar arahan non-AWS/GCP; lokasi data; biaya control plane + egress; butuh SRE K8s | 2–4× biaya opsi A; +6–10 minggu; +1 FTE SRE |
| D. k3s self-managed sejak awal | Siap scale | Kompleksitas tinggi untuk tim kecil di fase 0 | +4–6 minggu |

## Keputusan
Opsi A: 4 node sesuai `docs/01 §6.3`, jaringan privat WireGuard, hanya `edge-app-01` port 80/443 publik. Orkestrasi Docker Compose di fase 1; naik ke Swarm atau k3s saat >1 node aplikasi (fase 6). Target **99,5%** sampai PostgreSQL HA (Patroni/repmgr) aktif. **RPO 15 menit** (WAL archiving → MinIO + salinan offsite; implementasi memakai **WAL-G**, bukan pgBackRest, karena repo S3 pgBackRest butuh HTTPS ke MinIO internal yang HTTP — lihat `docs/runbooks/backup-restore.md`; catatan 2026-10-05: sejak ADR-022 MinIO wajib HTTPS sehingga alasan itu gugur, tetapi WAL-G tetap dipertahankan karena enkripsi libsodium client-side, zstd, dan PITR sudah berjalan — tidak ada alasan berpindah), **RTO 4 jam**, restore test bulanan.

## Konsekuensi
- **Positif:** biaya dapat diprediksi, data di Indonesia, seluruh stack sudah di-scaffold di `infra/compose`.
- **Negatif:** failover DB manual (RTO jam, bukan menit) di fase 1; GPU opsional menambah biaya signifikan (lihat ADR-008).
- **Dampak dokumen:** Cetak Biru (EKS/GKE) tidak berlaku; KPI uptime BRD 99,9% → 99,5% fase 1.

## Risiko terkait
R-T07 (uptime), R-T08 (backup/DR), R-T09 (observability), R-L01 (lokasi data PDP), R-S03.

## Pertanyaan terbuka untuk klien
- **Q-50** lokasi hosting & provider yang disetujui; **Q-51** RPO/RTO per sistem; **Q-52** jam operasional / maintenance window; **Q-53** siapa on-call; **Q-55** koneksi internet outlet.

## Kapan ditinjau ulang
Bila CPU/RAM node aplikasi >70% sustained 2 minggu, jumlah outlet >30, atau bisnis mensyaratkan 99,9% (memerlukan HA DB terlebih dahulu).
