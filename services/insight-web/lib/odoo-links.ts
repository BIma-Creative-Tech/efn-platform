/**
 * Tautan "Buka di Odoo" (docs/04 §28.13.11, L-197): Insight hanya menyusun URL dari origin `EFN_INSIGHT_ODOO_URL` + model +
 * id yang disediakan view. Tidak ada data lain di URL. SATU-SATUNYA tempat pola URL Odoo ditulis, supaya mudah diganti.
 *
 * Pola Odoo 19 (diverifikasi fit-gap docs/fit-gap/G47_INSIGHT_SM.md §7): `{origin}/odoo/action-<xmlid>/<id>` untuk
 * rekaman dan `{origin}/odoo/action-<xmlid>` untuk daftar. Pasangan model -> action ada di ACTIONS. Record rule Odoo tetap
 * berlaku; sesi Odoo habis -> login -> kembali ke tautan.
 *
 * Belum terverifikasi: render form `orderpoint` (`stock.action_orderpoint_replenish`); belum dipakai di kartu mana pun.
 */
export const ACTIONS = {
  'efn.replenishment.proposal': 'efn_replenishment.efn_replenishment_proposal_action',
  'efn.expiry.alert': 'efn_expiry_monitor.efn_expiry_alert_action',
  'efn.lot.markdown': 'efn_expiry_monitor.efn_lot_markdown_action',
  'efn.campaign': 'efn_campaign.efn_campaign_action',
  'purchase.order': 'purchase.purchase_rfq',
  'stock.picking': 'stock.action_picking_tree_all',
  'stock.lot': 'stock.action_production_lot_form',
  'product.template': 'stock.product_template_action_product',
  'product.product': 'stock.stock_product_normal_action',
  'stock.warehouse.orderpoint': 'stock.action_orderpoint_replenish',
  'stock.scrap': 'stock.action_stock_scrap',
} as const;
export type OdooLinkModel = keyof typeof ACTIONS;
export const ODOO_LINK_MODELS = Object.keys(ACTIONS) as OdooLinkModel[];

const isModel = (m: string): m is OdooLinkModel => Object.prototype.hasOwnProperty.call(ACTIONS, m);

const RECORD_PATTERN = (model: OdooLinkModel, id: number): string => `/odoo/action-${ACTIONS[model]}/${id}`;
const LIST_PATTERN = (model: OdooLinkModel): string => `/odoo/action-${ACTIONS[model]}`;

function validOrigin(origin: string | null | undefined): origin is string {
  if (!origin) return false;
  try {
    const u = new URL(origin);
    return (u.protocol === 'https:' || u.protocol === 'http:') && u.origin === origin;
  } catch {
    return false;
  }
}

/** URL rekaman, atau null bila origin tidak ada/tak sah, model di luar daftar, atau id bukan bilangan bulat positif. */
export function odooRecordUrl(origin: string | null | undefined, model: string, id: number | null | undefined): string | null {
  if (!validOrigin(origin) || !isModel(model)) return null;
  if (typeof id !== 'number' || !Number.isSafeInteger(id) || id <= 0) return null;
  return `${origin}${RECORD_PATTERN(model, id)}`;
}

/** URL daftar model (tanpa id; mis. `stock.scrap`). null bila origin/model tidak sah. */
export function odooListUrl(origin: string | null | undefined, model: string): string | null {
  if (!validOrigin(origin) || !isModel(model)) return null;
  return `${origin}${LIST_PATTERN(model)}`;
}
