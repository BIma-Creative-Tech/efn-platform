/**
 * Banner tetap lingkungan ESB yang dituju + status tulis (docs/04 §30.12): "ESB STAGING" / "ESB PRODUCTION" dari `/v1/meta`.
 * Teks + ikon + warna (tidak hanya warna). Server component.
 */
import type { ReactNode } from 'react';
import { envLabel } from '@/lib/prodplan-format';
import type { PpMeta } from '@/lib/prodplan-types';
import { Icon } from '@/components/ui';

export function EnvBanner({ meta, errorText }: { meta: PpMeta | null; errorText?: string }): ReactNode {
  if (!meta) {
    return (
      <div className="banner banner-unknown pp-banner" role="status">
        <Icon name="warn" size={16} /> <strong>Lingkungan ESB tidak diketahui.</strong> {errorText ?? 'Layanan perencanaan produksi tidak dapat dihubungi.'} Jangan memposting sebelum status ini jelas.
      </div>
    );
  }
  const prod = meta.esb_env === 'production';
  const known = prod || meta.esb_env === 'staging';
  return (
    <div className={`banner ${prod ? 'banner-bad' : known ? 'banner-warn' : 'banner-unknown'} pp-banner`} role="status">
      <Icon name={prod ? 'bad' : 'warn'} size={16} /> <strong>{envLabel(meta.esb_env)}</strong>
      {prod
        ? ': dokumen yang diposting dibuat di ESB produksi dan langsung memengaruhi stok nyata.'
        : known
          ? ': dokumen yang diposting dibuat di lingkungan uji (staging) ESB.'
          : '.'}{' '}
      {meta.write_enabled ? (
        <span>
          Penulisan ke ESB: <strong>AKTIF</strong>.
        </span>
      ) : (
        <span>
          Penulisan ke ESB: <strong>MATI</strong>. Rencana bisa dibuat, dihitung, dan disetujui, tetapi belum bisa diposting.
        </span>
      )}
    </div>
  );
}
