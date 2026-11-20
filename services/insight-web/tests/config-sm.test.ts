import { describe, expect, it } from 'vitest';
import { parseConfig } from '@/lib/config';

describe('flag G47 (docs/04 §28.13.12)', () => {
  it('INSIGHT_SM_ENABLED aktif hanya string persis "true" (default false)', () => {
    expect(parseConfig({}).smEnabled).toBe(false);
    for (const v of ['1', 'True', 'TRUE', 'yes', '']) expect(parseConfig({ INSIGHT_SM_ENABLED: v }).smEnabled, v).toBe(false);
    expect(parseConfig({ INSIGHT_SM_ENABLED: 'true' }).smEnabled).toBe(true);
  });
  it('INSIGHT_ACCEPT_EIT1: default true bila tidak diset; bila diset hanya "true" persis yang menerima', () => {
    expect(parseConfig({}).acceptEit1).toBe(true);
    expect(parseConfig({ INSIGHT_ACCEPT_EIT1: '' }).acceptEit1).toBe(true);
    expect(parseConfig({ INSIGHT_ACCEPT_EIT1: 'true' }).acceptEit1).toBe(true);
    for (const v of ['false', 'False', '0', 'ture', 'no']) expect(parseConfig({ INSIGHT_ACCEPT_EIT1: v }).acceptEit1, v).toBe(false);
  });
});
