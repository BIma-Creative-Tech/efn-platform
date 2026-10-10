import { canTransitionPart, deriveOrderStatus } from './online.model';

describe('Transisi bagian monoton (§10.6.2 + L-14)', () => {
  it('naik peringkat diizinkan; mundur ditolak', () => {
    expect(canTransitionPart('placed', 'paying')).toBe(true);
    expect(canTransitionPart('ready', 'confirmed')).toBe(false);
    expect(canTransitionPart('handed_over', 'ready')).toBe(false);
    expect(canTransitionPart('cancelled', 'review')).toBe(false);
    expect(canTransitionPart('paying', 'paying')).toBe(false);
  });
  it('L-14: confirmed|ready → review selalu; review → confirmed|cancelled', () => {
    expect(canTransitionPart('confirmed', 'review')).toBe(true);
    expect(canTransitionPart('ready', 'review')).toBe(true);
    expect(canTransitionPart('handed_over', 'review')).toBe(false);
    expect(canTransitionPart('review', 'confirmed')).toBe(true);
    expect(canTransitionPart('review', 'cancelled')).toBe(true);
  });
  it('status order diturunkan dari bagian setelah paid', () => {
    expect(deriveOrderStatus('paid', ['review'])).toBe('fulfilling');
    expect(deriveOrderStatus('fulfilling', ['handed_over', 'cancelled'])).toBe('completed');
    expect(deriveOrderStatus('fulfilling', ['cancelled'])).toBe('cancelled');
    expect(deriveOrderStatus('awaiting_payment', ['placed'])).toBe('awaiting_payment');
  });
});
