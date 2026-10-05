'use client';

/**
 * Form Price Hunter. Alur async: kirim -> 202 queued -> status di /pantau-harga/[id];
 * TIDAK menunggu OCR (OCR async via RabbitMQ, docs/04 §3.3/§4).
 *
 * Lokasi (UU PDP, docs/04 §8.4): hanya ditawarkan bila consent `location` aktif (dikelola di
 * /akun/privasi). Walau aktif, tetap opt-in per laporan (default MATI) — minimalisasi data.
 * Koordinat dibulatkan 4 desimal (~11 m) sebelum dikirim; BFF membulatkan ulang & membuang bila consent tidak aktif.
 */
import { useEffect, useState, type ChangeEvent, type FormEvent } from 'react';
import Link from 'next/link';
import { compressImage, ImageCompressError } from '@/lib/image-compress';

type GeoState =
  | { kind: 'off' }
  | { kind: 'pending' }
  | { kind: 'ok'; lat: number; lng: number }
  | { kind: 'error'; message: string };

type SubmitState =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'queued'; reportId: string }
  | { kind: 'error'; message: string };

const round4 = (n: number) => Math.round(n * 1e4) / 1e4;

/** "12.500" / "Rp 12.500" / "12500" -> 12500. Tidak menerima pecahan. */
function parseRupiah(input: string): number | null {
  const digits = input.replace(/[^\d]/g, '');
  if (!digits) return null;
  const n = Number(digits);
  return Number.isSafeInteger(n) && n > 0 && n <= 100_000_000 ? n : null;
}

export function PriceReportForm({ locationConsent }: { locationConsent: boolean }) {
  const [photo, setPhoto] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [compressing, setCompressing] = useState(false);
  const [productName, setProductName] = useState('');
  const [price, setPrice] = useState('');
  const [competitor, setCompetitor] = useState('');
  const [shareLocation, setShareLocation] = useState(false);
  const [geo, setGeo] = useState<GeoState>({ kind: 'off' });
  const [submit, setSubmit] = useState<SubmitState>({ kind: 'idle' });
  const [fieldError, setFieldError] = useState<string | null>(null);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  async function onPhotoChange(e: ChangeEvent<HTMLInputElement>) {
    setFieldError(null);
    const file = e.target.files?.[0];
    setPhoto(null);
    setPreviewUrl(null);
    if (!file) return;
    setCompressing(true);
    try {
      const compressed = await compressImage(file);
      setPhoto(compressed);
      setPreviewUrl(URL.createObjectURL(compressed));
    } catch (err) {
      setFieldError(err instanceof ImageCompressError ? err.message : 'Foto tidak dapat diproses.');
      e.target.value = '';
    } finally {
      setCompressing(false);
    }
  }

  function onLocationToggle(e: ChangeEvent<HTMLInputElement>) {
    const checked = e.target.checked;
    setShareLocation(checked);
    if (!checked) {
      setGeo({ kind: 'off' });
      return;
    }
    if (!('geolocation' in navigator)) {
      setGeo({ kind: 'error', message: 'Perangkat tidak mendukung lokasi. Laporan tetap bisa dikirim tanpa lokasi.' });
      return;
    }
    setGeo({ kind: 'pending' });
    navigator.geolocation.getCurrentPosition(
      (pos) => setGeo({ kind: 'ok', lat: round4(pos.coords.latitude), lng: round4(pos.coords.longitude) }),
      () =>
        setGeo({
          kind: 'error',
          message: 'Lokasi tidak didapat (izin ditolak atau tidak tersedia). Laporan tetap bisa dikirim tanpa lokasi.',
        }),
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 60_000 },
    );
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setFieldError(null);
    if (!photo) return setFieldError('Pilih atau ambil foto label harga terlebih dahulu.');
    const name = productName.trim();
    if (name.length < 2 || name.length > 200) return setFieldError('Nama produk wajib diisi (2–200 karakter).');
    const priceValue = parseRupiah(price);
    if (priceValue === null) return setFieldError('Harga wajib diisi dalam Rupiah, mis. 12500.');
    const comp = competitor.trim();
    if (comp.length > 120) return setFieldError('Nama toko terlalu panjang (maks 120 karakter).');

    const form = new FormData();
    form.append('photo', photo, photo.name);
    form.append('product_name', name);
    form.append('price', String(priceValue));
    if (comp) form.append('competitor_name', comp);
    if (locationConsent && shareLocation && geo.kind === 'ok') {
      form.append('lat', String(geo.lat));
      form.append('lng', String(geo.lng));
    }

    setSubmit({ kind: 'sending' });
    try {
      const res = await fetch('/api/market-scout/reports', { method: 'POST', body: form });
      if (res.status === 202) {
        const data = (await res.json()) as { report_id: string };
        setSubmit({ kind: 'queued', reportId: data.report_id });
        return;
      }
      const data = (await res.json().catch(() => null)) as { error?: { message?: string } } | null;
      let message: string;
      switch (res.status) {
        case 401:
          message = 'Sesi Anda berakhir. Silakan masuk kembali.';
          break;
        case 413:
          message = 'Ukuran foto terlalu besar (maks 8 MB).';
          break;
        case 415:
          message = 'Format foto tidak didukung. Gunakan JPEG, PNG, atau WebP.';
          break;
        case 429:
          message = 'Batas laporan harian tercapai. Coba lagi besok.';
          break;
        default:
          message = data?.error?.message ?? 'Laporan belum dapat dikirim. Silakan coba lagi.';
      }
      setSubmit({ kind: 'error', message });
    } catch {
      setSubmit({ kind: 'error', message: 'Koneksi bermasalah. Periksa internet Anda lalu coba lagi.' });
    }
  }

  function resetForm() {
    setPhoto(null);
    setPreviewUrl(null);
    setProductName('');
    setPrice('');
    setCompetitor('');
    setShareLocation(false);
    setGeo({ kind: 'off' });
    setSubmit({ kind: 'idle' });
  }

  if (submit.kind === 'queued') {
    return (
      <div className="card stack" role="status" aria-live="polite">
        <p className="alert alert-success" style={{ margin: 0 }}>
          Laporan diterima dan <strong>sedang divalidasi</strong>. Hasilnya dikirim lewat notifikasi; Anda tidak perlu
          menunggu di halaman ini.
        </p>
        <p style={{ margin: 0 }}>
          <Link href={`/pantau-harga/${encodeURIComponent(submit.reportId)}`}>Lihat status laporan</Link>
        </p>
        <p style={{ margin: 0 }}>
          <button type="button" className="btn btn-secondary btn-small" onClick={resetForm}>
            Kirim laporan lain
          </button>
        </p>
      </div>
    );
  }

  const sending = submit.kind === 'sending';

  return (
    <form className="card stack" onSubmit={onSubmit} noValidate aria-busy={sending}>
      <div aria-live="polite">
        {fieldError && (
          <p className="alert alert-error" role="alert">
            {fieldError}
          </p>
        )}
        {submit.kind === 'error' && (
          <p className="alert alert-error" role="alert">
            {submit.message}
          </p>
        )}
      </div>

      <div className="field">
        <label htmlFor="photo">Foto label harga</label>
        <input
          id="photo"
          name="photo"
          type="file"
          accept="image/jpeg,image/png,image/webp"
          capture="environment"
          onChange={onPhotoChange}
          aria-describedby="photo-hint"
          required
        />
        <span id="photo-hint" className="hint">
          Pastikan nama produk dan harga terbaca jelas. JPEG, PNG, atau WebP.
        </span>
        {compressing && <span className="hint">Memproses foto…</span>}
        {previewUrl && (
          // eslint-disable-next-line @next/next/no-img-element -- pratinjau blob lokal, bukan aset
          <img src={previewUrl} alt="Pratinjau foto label harga" className="photo-preview" />
        )}
      </div>

      <div className="field">
        <label htmlFor="product_name">Nama produk</label>
        <input
          id="product_name"
          name="product_name"
          type="text"
          maxLength={200}
          value={productName}
          onChange={(e) => setProductName(e.target.value)}
          placeholder="mis. Minyak Goreng 2 L merek X"
          required
        />
      </div>

      <div className="field">
        <label htmlFor="price">Harga (Rupiah)</label>
        <input
          id="price"
          name="price"
          type="text"
          inputMode="numeric"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          placeholder="mis. 32500"
          required
        />
      </div>

      <div className="field">
        <label htmlFor="competitor_name">
          Nama toko <span className="muted">(opsional)</span>
        </label>
        <input
          id="competitor_name"
          name="competitor_name"
          type="text"
          maxLength={120}
          value={competitor}
          onChange={(e) => setCompetitor(e.target.value)}
          placeholder="mis. Toko ABC Jl. Merdeka"
        />
      </div>

      {!locationConsent ? (
        <p className="hint" style={{ margin: 0 }}>
          Laporan dikirim <strong>tanpa lokasi</strong>. Bila ingin menyertakan lokasi perkiraan untuk membantu
          verifikasi toko, aktifkan persetujuan lokasi di <Link href="/akun/privasi">Pengaturan Privasi</Link>.
        </p>
      ) : (
      <fieldset className="stack" style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius)' }}>
        <legend>
          Lokasi <span className="muted">(opsional)</span>
        </legend>
        <div className="checkbox">
          <input id="share_location" type="checkbox" checked={shareLocation} onChange={onLocationToggle} />
          <label htmlFor="share_location">
            Sertakan lokasi perkiraan (dibulatkan ±11 meter) <strong>pada laporan ini</strong>, untuk memverifikasi
            toko yang dilaporkan.
          </label>
        </div>
        <p className="hint" style={{ margin: 0 }}>
          Lokasi tidak dilacak terus-menerus dan tidak wajib. Anda tetap bisa mengirim laporan tanpa lokasi.
        </p>
        {geo.kind === 'pending' && <p className="hint">Mengambil lokasi…</p>}
        {geo.kind === 'ok' && <p className="hint">Lokasi siap dikirim bersama laporan.</p>}
        {geo.kind === 'error' && <p className="hint">{geo.message}</p>}
        <p className="hint" style={{ margin: 0 }}>
          Persetujuan lokasi dapat ditarik di <Link href="/akun/privasi">Pengaturan Privasi</Link>.
        </p>
      </fieldset>
      )}

      <button type="submit" className="btn" disabled={sending || compressing || geo.kind === 'pending'}>
        {sending ? 'Mengirim…' : 'Kirim laporan'}
      </button>
    </form>
  );
}
