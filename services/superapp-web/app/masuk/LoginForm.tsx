'use client';

/**
 * Login OTP dua langkah. Validasi di sini hanya untuk UX; BFF adalah validator utama.
 * Token tidak pernah menyentuh JavaScript: Route Handler /api/auth/otp/verify yang men-set cookie.
 *
 * Captcha (docs/04 §8.1): setiap permintaan OTP (termasuk kirim ulang) wajib membawa payload ALTCHA
 * baru — challenge sekali pakai, jadi widget di-remount (`altchaKey`) setelah setiap permintaan.
 */
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { AltchaWidget } from '@/components/AltchaWidget';
import { AGE_CHECKBOX_LABEL, AGE_REQUIRED_MESSAGE, MIN_AGE } from '@/lib/auth-age';
import { normalizeIndonesianPhone } from '@/lib/phone';
import { safeNextPath } from '@/lib/safe-next';

type Step = 'phone' | 'otp';

const RESEND_COOLDOWN_S = 60;

export function LoginForm({ nextPath }: { nextPath: string }) {
  const router = useRouter();
  const [step, setStep] = useState<Step>('phone');
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [challengeId, setChallengeId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const [altchaPayload, setAltchaPayload] = useState<string | null>(null);
  const [altchaKey, setAltchaKey] = useState(0);
  const otpRef = useRef<HTMLInputElement>(null);
  // G18 (Q-P3): konfirmasi usia, TIDAK tercentang secara default, diminta di setiap login (login = daftar).
  const [ageConfirmed, setAgeConfirmed] = useState(false);
  const ageRef = useRef<HTMLInputElement>(null);
  const onAltchaPayload = useCallback((p: string | null) => setAltchaPayload(p), []);

  /** Challenge sudah terpakai (atau ditolak) -> minta challenge baru. */
  const resetCaptcha = () => {
    setAltchaPayload(null);
    setAltchaKey((k) => k + 1);
  };

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  useEffect(() => {
    if (step === 'otp') otpRef.current?.focus();
  }, [step]);

  async function requestOtp(e?: FormEvent) {
    e?.preventDefault();
    setError(null);
    setInfo(null);
    const normalized = normalizeIndonesianPhone(phone);
    if (!normalized) {
      setError('Nomor HP tidak valid. Contoh: 0812 3456 7890.');
      return;
    }
    if (!altchaPayload) {
      setError('Selesaikan verifikasi keamanan ("Saya bukan robot") terlebih dahulu.');
      return;
    }
    setBusy(true);
    try {
      const res = await fetch('/api/auth/otp/request', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ phone: normalized, altcha: altchaPayload }),
      });
      // Payload sekali pakai: apa pun hasilnya, siapkan challenge baru untuk permintaan berikutnya.
      resetCaptcha();
      if (res.status === 202) {
        const data = (await res.json()) as { challenge_id: string };
        setChallengeId(data.challenge_id);
        setCode('');
        setStep('otp');
        setCooldown(RESEND_COOLDOWN_S);
        setInfo('Jika nomor dapat menerima pesan, kode telah dikirim lewat WhatsApp.');
      } else if (res.status === 429) {
        setError('Terlalu banyak permintaan. Coba lagi beberapa menit lagi.');
      } else if (res.status === 400) {
        const data = (await res.json().catch(() => null)) as { error?: { code?: string } } | null;
        if (data?.error?.code === 'captcha_required') {
          setError('Verifikasi keamanan kedaluwarsa atau tidak valid. Centang "Saya bukan robot" lagi, lalu kirim.');
        } else {
          setError('Nomor HP tidak valid. Contoh: 0812 3456 7890.');
        }
      } else {
        setError('Kode belum dapat dikirim. Silakan coba lagi.');
      }
    } catch {
      setError('Koneksi bermasalah. Periksa internet Anda lalu coba lagi.');
    } finally {
      setBusy(false);
    }
  }

  async function verifyOtp(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    if (!/^\d{6}$/.test(code)) {
      setError('Masukkan 6 digit kode.');
      return;
    }
    if (!ageConfirmed) {
      setError(AGE_REQUIRED_MESSAGE);
      ageRef.current?.focus();
      return;
    }
    setBusy(true);
    try {
      const res = await fetch('/api/auth/otp/verify', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ challenge_id: challengeId, code, age_confirmed: ageConfirmed }),
      });
      if (res.ok) {
        // Lapis kedua SEC-13: validasi ulang di client walau server sudah menyaring.
        router.replace(safeNextPath(nextPath));
        router.refresh();
        return;
      }
      if (res.status === 400) {
        const data = (await res.json().catch(() => ({}))) as {
          reason?: string;
          attempts_left?: number;
          error?: { code?: string };
        };
        // Challenge tidak hangus: cukup centang lalu kirim lagi dengan kode yang sama.
        if (data.error?.code === 'age_confirmation_required') {
          setError(AGE_REQUIRED_MESSAGE);
          ageRef.current?.focus();
        } else if (data.reason === 'expired') setError('Kode sudah kedaluwarsa. Silakan minta kode baru.');
        else if (data.reason === 'locked') setError('Terlalu banyak percobaan. Silakan minta kode baru.');
        else
          setError(
            typeof data.attempts_left === 'number'
              ? `Kode tidak cocok. Sisa percobaan: ${data.attempts_left}.`
              : 'Kode tidak cocok.',
          );
      } else if (res.status === 429) {
        setError('Terlalu banyak percobaan. Coba lagi beberapa menit lagi.');
      } else if (res.status === 409) {
        // docs/04 §8.11: penghapusan akun sudah diproses (masa tenggang lewat) -> tidak bisa login/dibatalkan lagi.
        setError(
          'Akun ini sedang dalam proses penghapusan sehingga tidak dapat digunakan. Pembatalan hanya bisa dilakukan selama masa tenggang.',
        );
      } else {
        setError('Verifikasi belum dapat diproses. Silakan coba lagi.');
      }
    } catch {
      setError('Koneksi bermasalah. Periksa internet Anda lalu coba lagi.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <div aria-live="polite">
        {error && (
          <p className="alert alert-error" role="alert">
            {error}
          </p>
        )}
        {info && <p className="alert alert-success">{info}</p>}
      </div>

      {step === 'phone' ? (
        <form onSubmit={requestOtp} className="stack" noValidate>
          <div className="field">
            <label htmlFor="phone">Nomor HP</label>
            <input
              id="phone"
              name="phone"
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              placeholder="0812 3456 7890"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              required
              aria-describedby="phone-hint"
            />
            <span id="phone-hint" className="hint">
              Format 08xx atau +628xx.
            </span>
          </div>
          <AltchaWidget key={`phone-${altchaKey}`} onPayload={onAltchaPayload} />
          <button type="submit" className="btn" disabled={busy}>
            {busy ? 'Mengirim…' : 'Kirim kode'}
          </button>
        </form>
      ) : (
        <form onSubmit={verifyOtp} className="stack" noValidate>
          <div className="field">
            <label htmlFor="otp">Kode verifikasi (6 digit)</label>
            <input
              ref={otpRef}
              id="otp"
              name="otp"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="\d{6}"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              required
            />
          </div>
          <div className="checkbox">
            <input
              ref={ageRef}
              id="age-confirmed"
              name="age_confirmed"
              type="checkbox"
              checked={ageConfirmed}
              onChange={(e) => setAgeConfirmed(e.target.checked)}
              required
              aria-required="true"
              aria-describedby="age-hint"
            />
            <label htmlFor="age-confirmed">{AGE_CHECKBOX_LABEL}</label>
          </div>
          <span id="age-hint" className="hint" style={{ marginTop: '0.25rem' }}>
            Wajib untuk masuk atau mendaftar. Layanan ini hanya untuk pengguna berusia {MIN_AGE} tahun ke atas; kami tidak
            meminta tanggal lahir.
          </span>
          <button type="submit" className="btn" disabled={busy}>
            {busy ? 'Memeriksa…' : 'Masuk'}
          </button>
        </form>
      )}

      {step === 'otp' && (
        // Sengaja DI LUAR <form> verifikasi: widget ALTCHA memasang listener `submit` pada form induknya
        // dan dapat menahan submit selama belum terverifikasi — tidak boleh menghalangi verifikasi kode.
        <div className="stack">
          {cooldown <= 0 && (
            <div className="stack">
              <p className="hint" style={{ margin: 0 }}>
                Belum menerima kode? Centang verifikasi keamanan, lalu kirim ulang.
              </p>
              <AltchaWidget key={`resend-${altchaKey}`} onPayload={onAltchaPayload} />
            </div>
          )}
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => requestOtp()}
              disabled={busy || cooldown > 0 || !altchaPayload}
            >
              {cooldown > 0 ? `Kirim ulang (${cooldown} dtk)` : 'Kirim ulang kode'}
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => {
                setStep('phone');
                setError(null);
                setInfo(null);
                resetCaptcha();
              }}
              disabled={busy}
            >
              Ganti nomor
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
