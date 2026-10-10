/**
 * Circuit breaker per kanal (docs/04 §10.4.2): `threshold` kegagalan TRANSPORT beruntun → terbuka `openMs` → setelah itu
 * half-open: satu percobaan diizinkan; sukses menutup, gagal membuka lagi. Penolakan bisnis (UserError) dan sukses
 * mereset hitungan. Per proses (tiap replika BFF menilai sendiri).
 */
export class CircuitBreaker {
  private failures = 0;
  private openedAt: number | null = null;
  private trialInFlight = false;

  constructor(
    private readonly threshold = 5,
    private readonly openMs = 30_000,
    private readonly now: () => number = () => Date.now(),
    private readonly onChange: (open: boolean) => void = () => undefined,
  ) {}

  /** true bila panggilan boleh dilakukan sekarang. */
  allow(): boolean {
    if (this.openedAt === null) return true;
    if (this.now() - this.openedAt < this.openMs) return false;
    if (this.trialInFlight) return false;
    this.trialInFlight = true; // half-open: satu percobaan
    return true;
  }

  isOpen(): boolean {
    return this.openedAt !== null && this.now() - this.openedAt < this.openMs;
  }

  success(): void {
    const wasOpen = this.openedAt !== null;
    this.failures = 0;
    this.openedAt = null;
    this.trialInFlight = false;
    if (wasOpen) this.onChange(false);
  }

  failure(): void {
    this.trialInFlight = false;
    this.failures += 1;
    if (this.openedAt !== null || this.failures >= this.threshold) {
      this.openedAt = this.now();
      this.onChange(true);
    }
  }

  /** Hasil non-transport (mis. UserError) — layanan hidup; tidak membuka circuit. */
  neutral(): void {
    this.success();
  }
}
