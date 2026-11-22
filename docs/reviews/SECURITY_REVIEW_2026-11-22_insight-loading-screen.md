# Security review pra-merge — `feat/insight-loading-screen` (703e6e0 di atas main b51fd59)

Tanggal: 22 Nov 2026. Reviewer: efn-security-reviewer (read-only). Cakupan: `services/insight-web` — `components/LoadingOverlay.tsx`,
`lib/loading-store.ts`, `app/loading.tsx`, `app/globals.css` (blok `.ld-*`), `components/Shell.tsx`, `components/FilterBar.tsx`,
`components/sm/SmFilterForm.tsx`, `components/RetryButton.tsx`, `lib/client-nav.ts`; dicocokkan dengan `lib/csp.ts`.
Catatan metode: diff dibaca dari working tree branch (bersih = commit 703e6e0); `git diff` tidak dijalankan karena reviewer tidak punya shell.

## Ringkasan eksekutif

Perubahan murni UI klien: tidak menambah endpoint, tidak menyentuh sesi/cookie/auth, tidak ada inline `<style>`/`<script>` baru, dan
tidak ada data sesi/PII/secret yang dikirim ke klien atau log. Tidak ada temuan Critical/High/Medium. Dua temuan Low soal ketersediaan
di sisi klien (halaman bisa terkunci lebih lama dari yang diniatkan) dan dua Info aksesibilitas. **Verdict: boleh merge**; SEC-LS-01
disarankan diperbaiki dalam PR ini atau berikutnya karena murah.

## Tabel temuan

| ID | Severity | File:baris | Ringkas | Risiko |
|---|---|---|---|---|
| SEC-LS-01 | Low (PLAUSIBLE) | `lib/loading-store.ts:51-61` | Sumber `useBusy` tidak punya batas aman; transisi menggantung = halaman terkunci tanpa batas waktu | Ketersediaan klien |
| SEC-LS-02 | Low (laten) | `components/LoadingOverlay.tsx:139-151`, `lib/loading-store.ts:72,85` | Kunci `beginNav` 90 dtk tanpa perubahan URL: cek `defaultPrevented` di capture tidak melihat `preventDefault` React; redirect ke URL yang sama | Ketersediaan klien |
| SEC-LS-03 | Info | `components/Shell.tsx:70-74` + `LoadingOverlay.tsx:164` | Tombol Keluar tidak bisa dipakai selama memuat (maks ±55-90 dtk) | Sesi di perangkat bersama |
| SEC-LS-04 | Info | `components/LoadingOverlay.tsx:57-59,166,177-185` | Fokus sengaja terperangkap di dialog; tidak dikembalikan ke elemen asal setelah selesai | A11y |

## Detail

### SEC-LS-01 — `useBusy` tanpa batas aman (Low, PLAUSIBLE)
`beginNav()` punya `SAFETY_MS` (90 dtk), tetapi `useBusy(pending)` menaikkan `pendingCount` selama `useTransition` pending tanpa batas
waktu. Skenario: filter diubah -> `start(() => router.push(...))`; permintaan RSC menggantung (koneksi Traefik/proxy terputus tanpa respons,
laptop pindah jaringan). Router Next tidak memasang timeout fetch, sehingga `pending` bisa tetap `true` -> `.shell` `inert` + overlay
penuh layar tanpa akhir. Jalan keluar hanya reload/tutup tab (status hanya di memori, jadi reload selalu memulihkan). Tidak ada vektor
penyerang (butuh aksi pengguna sendiri; tautan dari origin lain memicu muat penuh, bukan overlay). Dampak: self-DoS/UX, bukan kebocoran.
Rekomendasi: terapkan batas yang sama untuk sumber transisi — mis. di `isBusy` abaikan `pendingCount` bila sudah > `SAFETY_MS` sejak
naik pertama, atau tampilkan tombol "Batalkan/Muat ulang halaman" di `LoadingScreen` setelah ±60 dtk (`window.location.reload()`).

### SEC-LS-02 — kunci 90 dtk tanpa perubahan URL (Low, laten)
Listener `click` dipasang di `document` fase capture, yang berjalan **sebelum** listener root React. Jadi `ev.defaultPrevented` di
`isInternalNavClick` hanya menangkap `preventDefault` dari listener capture lain, bukan dari `onClick` React pada `<a>`. Saat ini tidak ada
`<a>` dengan `onClick` + `preventDefault` di insight-web (diverifikasi grep), jadi laten. Kasus lain: tautan internal yang server-nya
me-`redirect()` kembali ke URL saat ini (URL tidak berubah) -> overlay bertahan sampai `SAFETY_MS`. Rekomendasi: tunda keputusan ke
setelah dispatch (`setTimeout(() => { if (!ev.defaultPrevented) beginNav(); }, 0)`) atau pakai listener bubble di `document`; dan/atau
tombol batal dari SEC-LS-01 menutup keduanya. Tambah unit test `isInternalNavClick` untuk kasus `preventDefault` bila diubah.

### SEC-LS-03 — Keluar tidak terjangkau saat memuat (Info)
Selama busy, form `POST /logout` ada di dalam `.shell` yang `inert`. Durasi terbatas (query maks ±55 dtk; `beginNav` maks 90 dtk) kecuali
SEC-LS-01. Pengguna di perangkat bersama tetap bisa menutup tab; logout sendiri tidak terdampak (dikecualikan di `isInternalNavClick`,
POST form). Diterima; bila SEC-LS-01 diberi tombol batal, cukup. Opsional: tombol "Keluar" kecil di kartu memuat (form POST yang sama).

### SEC-LS-04 — fokus (Info)
Pola dialog modal (`role="dialog"`, `aria-modal`, `inert` pada `.shell` dan skip-link, fokus ke "Tip lain") sesuai; perangkap fokus memang
diniatkan dan dibatasi waktu. Setelah selesai fokus tidak dikembalikan ke elemen pemicu (`blur()` di baris 166); `FocusMain` yang sudah ada
kemungkinan memindahkan fokus ke `main` saat rute berubah — tidak diverifikasi di browser. Bukan isu keamanan.

## Yang sudah benar
- CSP: tidak ada `<style>`, `<script>`, `dangerouslySetInnerHTML` baru. Gaya dinamis hanya lewat atribut `style` (diizinkan
  `style-src-attr 'unsafe-inline'`), nilainya dari angka internal (`pct`, `boost`, indeks), bukan input pengguna -> tidak ada CSS injection.
  CSS baru di `globals.css` (file statis, `'self'`). SVG inline tanpa skrip/`href`.
- `app/loading.tsx` server component tanpa data; `LoadingScreen` tidak menerima/merender data sesi. Tidak ada `console.*`/log baru,
  tidak ada `localStorage`/cookie baru.
- `isInternalNavClick` hanya memutuskan tampil/tidaknya overlay — tidak pernah melakukan navigasi, jadi tidak ada open redirect. Parsing
  URL di `try/catch` fail-closed, cek `origin` sama, `/api/*` dan `/logout` dikecualikan, `download`/`target` lain/modifier/klik non-kiri
  dikecualikan, hash-only tidak mengunci.
- Timer modul dibersihkan (`clearTimeout`), cleanup efek melepas `inert`; counter `useBusy` seimbang (aman di StrictMode).
- Overlay bukan kontrol keamanan dan tidak diperlakukan begitu; otorisasi tetap di server.
