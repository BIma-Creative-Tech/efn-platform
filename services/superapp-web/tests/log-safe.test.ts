import { describe, expect, it } from 'vitest';
import { logSafe, safeRequestId } from '@/lib/log-safe';

describe('log-safe (CodeQL js/log-injection)', () => {
  it('menghapus CR/LF dan karakter kontrol sehingga tidak bisa membuat baris log palsu', () => {
    expect(logSafe('/v1/x\r\n[bff] palsu')).toBe('/v1/x  [bff] palsu');
    expect(logSafe('a\u0000b\u001bc')).toBe('a?b?c');
    expect(logSafe('x'.repeat(300))).toHaveLength(257);
  });

  it('request id dari klien dipakai hanya bila bentuknya wajar', () => {
    expect(safeRequestId('req-123_abc:1.2')).toBe('req-123_abc:1.2');
    expect(safeRequestId('bad\nid')).toMatch(/^[0-9a-f-]{36}$/);
    expect(safeRequestId('x'.repeat(129))).toMatch(/^[0-9a-f-]{36}$/);
    expect(safeRequestId(null)).toMatch(/^[0-9a-f-]{36}$/);
  });
});
