import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { Icon } from '@/components/ui';
import { firstParam, type SearchParams } from '@/lib/pp-page';
import { ppRequest } from '@/lib/prodplan-client';
import { envLabel } from '@/lib/prodplan-format';
import { LOGIN_ERROR_MESSAGES, parseLoginError } from '@/lib/prodplan-session';
import { parseMeta } from '@/lib/prodplan-types';
import { getConfig } from '@/lib/runtime';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Masuk - Production Order', robots: { index: false } };

/**
 * `/produksi/masuk` (docs/04 §30.17.2): satu-satunya halaman modul Produksi yang terbuka tanpa sesi. Form HTML biasa (tanpa JS
 * wajib) yang mengirim username + password ESB ke `POST /api/prodplan/login`. Username TIDAK diisi ulang dari query; pesan galat
 * dipilih dari tabel tetap lewat `?e=` (nilai lain diabaikan) dan tidak membedakan "user tidak ada" dari "password salah".
 * Banner lingkungan ESB dari `GET /v1/meta` tanpa sesi (hanya `esb_env`).
 */
export default async function ProdLoginPage({ searchParams }: { searchParams: SearchParams }) {
  if (!getConfig().ppEnabled) redirect('/pp-belum-aktif');
  const err = parseLoginError(firstParam(await searchParams, 'e'));
  const metaRes = await ppRequest('', 'GET', '/v1/meta');
  const env = metaRes.ok ? (parseMeta(metaRes.data)?.esb_env ?? 'unknown') : null;
  const prod = env === 'production';
  const known = prod || env === 'staging';

  return (
    <main className="info-page pp-login" id="konten">
      <h1>Masuk ke modul Produksi</h1>
      {env === null ? (
        <div className="banner banner-unknown pp-banner" role="status">
          <Icon name="warn" size={16} /> <strong>Lingkungan ESB tidak diketahui.</strong> Layanan perencanaan produksi tidak dapat dihubungi.
        </div>
      ) : (
        <div className={`banner ${prod ? 'banner-bad' : known ? 'banner-warn' : 'banner-unknown'} pp-banner`} role="status">
          <Icon name={prod ? 'bad' : 'warn'} size={16} /> <strong>{envLabel(env)}</strong>
          {prod ? ': Anda akan bekerja di data ESB produksi.' : known ? ': Anda akan bekerja di lingkungan uji (staging) ESB.' : '.'}
        </div>
      )}
      <p>
        Masuk dengan akun ESB Anda sendiri. Aplikasi ini meneruskan username dan password ke ESB satu kali dan tidak menyimpan password. Sesi berlaku paling lama 1 jam.
      </p>
      {err ? (
        <p role="alert" id="pp-login-error" className="pp-msg pp-msg-error">
          <strong>Tidak bisa masuk:</strong> {LOGIN_ERROR_MESSAGES[err]}
        </p>
      ) : null}
      <form method="post" action="/api/prodplan/login" className="pp-form pp-login-form">
        <div className="pp-field">
          <label htmlFor="pp-login-user">Username ESB</label>
          <input
            id="pp-login-user"
            name="username"
            type="text"
            className="input"
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            maxLength={64}
            required
            autoFocus
            aria-describedby={err ? 'pp-login-error' : undefined}
          />
        </div>
        <div className="pp-field">
          <label htmlFor="pp-login-pass">Password ESB</label>
          <input
            id="pp-login-pass"
            name="password"
            type="password"
            className="input"
            autoComplete="current-password"
            maxLength={128}
            required
            aria-describedby={err ? 'pp-login-error' : undefined}
          />
        </div>
        <div className="pp-form-actions">
          <button type="submit" className="btn btn-primary">
            Masuk
          </button>
        </div>
      </form>
    </main>
  );
}
