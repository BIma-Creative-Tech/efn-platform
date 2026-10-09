import 'reflect-metadata';
import { OnlinePrivacyService } from '../orders/online-privacy.service';
import { DsarService } from './dsar.service';

/**
 * Regresi QA G17 (smoke nyata): `@Optional() online: OnlinePrivacyService | null` tanpa token eksplisit diserialisasi
 * TypeScript sebagai `Object` di `design:paramtypes` → Nest menyuntik undefined → `DELETE /v1/me` tidak pernah
 * menjalankan L-4 (`409 open_online_orders`) dan menjadwalkan hapus akun walau pesanan online masih terbuka.
 * Test unit lain membuat DsarService dengan `new` sehingga tidak menangkapnya.
 */
describe('DsarService DI (L-4)', () => {
  it('OnlinePrivacyService disuntik lewat token eksplisit, bukan metadata tipe union', () => {
    const declared = (Reflect.getMetadata('self:paramtypes', DsarService) ?? []) as { index: number; param: unknown }[];
    const online = declared.find((d) => d.param === OnlinePrivacyService);
    expect(online).toBeDefined();
    const designTypes = Reflect.getMetadata('design:paramtypes', DsarService) as unknown[];
    expect(online!.index).toBe(designTypes.length - 1);
  });
});
