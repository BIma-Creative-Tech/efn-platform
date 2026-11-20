'use client';

import type { ReactNode } from 'react';
import { useEngage } from './EngageProvider';

type Feature = 'tracking' | 'wishlist' | 'restock' | 'priceAlert' | 'campaignInbox';

/** Render `children` hanya bila flag fitur menyala (dan, opsional, member login). Server Component boleh menjadi `children`. */
export function FeatureGate({ feature, requireLogin = false, children }: { feature: Feature | Feature[]; requireLogin?: boolean; children: ReactNode }) {
  const { features, ready, loggedIn } = useEngage();
  const list = Array.isArray(feature) ? feature : [feature];
  if (!ready || !list.some((f) => features[f]) || (requireLogin && !loggedIn)) return null;
  return <>{children}</>;
}
