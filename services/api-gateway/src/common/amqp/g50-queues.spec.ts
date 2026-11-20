import { BEHAVIOR_EVENT_QUEUE } from '../../behavior/behavior-message';
import { PROMO_CAMPAIGN_REQUESTED_QUEUE } from '../../campaign/campaign-message';
import { NOTIFY_EMAIL_QUEUE } from '../../notification/email/email.constants';
import { WA_QUEUE, WA_RESULT_QUEUE } from '../../notification/wa-unofficial/wa-unofficial.contract';
import { NOTIFY_PRICE_ALERT_QUEUE, NOTIFY_RESTOCK_QUEUE } from '../../wishlist/alert-message';
import { G50_QUEUES, g50QueuesFor } from './queues';

const OFF = {
  behaviorTrackingEnabled: false,
  promoCampaignEnabled: false,
  restockAlertEnabled: false,
  priceAlertEnabled: false,
  emailEnabled: false,
  waUnofficialEnabled: false,
};

describe('G50 queue passive check (docs/04 §28.7)', () => {
  it('nama queue sama dengan konstanta modul', () => {
    expect(G50_QUEUES.behavior).toEqual([BEHAVIOR_EVENT_QUEUE]);
    expect(G50_QUEUES.campaign).toEqual([PROMO_CAMPAIGN_REQUESTED_QUEUE]);
    expect(G50_QUEUES.restockAlert).toEqual([NOTIFY_RESTOCK_QUEUE]);
    expect(G50_QUEUES.priceAlert).toEqual([NOTIFY_PRICE_ALERT_QUEUE]);
    expect(G50_QUEUES.email).toEqual([NOTIFY_EMAIL_QUEUE]);
    expect(G50_QUEUES.waUnofficial).toEqual([WA_QUEUE, WA_RESULT_QUEUE]);
  });

  it('semua flag mati -> tidak ada queue baru yang diperiksa', () => {
    expect(g50QueuesFor(OFF)).toEqual([]);
  });

  it('hanya queue fitur yang aktif yang diperiksa', () => {
    expect(g50QueuesFor({ ...OFF, emailEnabled: true, waUnofficialEnabled: true })).toEqual([
      NOTIFY_EMAIL_QUEUE,
      WA_QUEUE,
      WA_RESULT_QUEUE,
    ]);
    expect(g50QueuesFor({ ...OFF, behaviorTrackingEnabled: true })).toEqual([BEHAVIOR_EVENT_QUEUE]);
  });
});
