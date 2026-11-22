"""Persona & cakupan supermarket untuk token eit2/eit3 (G47 + G52, docs/04 §28.13.2-§28.13.3, §28.15.2-§28.15.4,
fit-gap G47 §3, §7.2).

Menimpa hook privat `res.users._efn_insight_sm_scope()` milik `efn_insight` (bukan metode publik, SEC-58). G52: grup
persona dan peta `PERSONA_GROUPS` milik `efn_insight` (dipakai lewat `_efn_insight_personas()`); controller mengirim
G' (aturan store lintas domain L-245) sebagai `personas`.
"""

from odoo import fields, models

from odoo.addons.efn_insight.lib.insight_token import ORG_PERSONAS, SM_CODES

# Persona PT yang diizinkan row policy di SEMUA view yang mengizinkan store (insight-sm-policy.sh SM_POLICY_VIEWS).
# SEC-G51-01: hanya exec; category tidak ada di v_sm_payment_daily (G51), jadi category + store tidak menerbitkan
# store.
# Invarian diuji services/etl/tests/test_insight_sm_access.py.
STORE_COVERING_PERSONAS = frozenset({"exec"})


class ResUsers(models.Model):
    _inherit = "res.users"

    # docs/04 §28.13.2 (Q-INS-S6): CE 19 tidak menyimpan "kategori milik buyer". v1 = filter default saja (L-189),
    # ikut ditandatangani (cats). Diubah admin user (ACL res.users = base.group_erp_manager); teraudit efn_audit
    # (CONTROL_SPECS res.users). Jangan tambahkan ke SELF_WRITEABLE_FIELDS.
    efn_insight_categ_ids = fields.Many2many(
        "product.category",
        "efn_res_users_insight_categ_rel",
        "user_id",
        "categ_id",
        string="Kategori dikelola (Insight)",
        help="Hanya untuk persona Insight SM: Category manager/buyer. Default filter kategori di Insight (maks 50).",
    )

    def _efn_insight_sm_scope(self, personas=None):
        """docs/04 §28.13.3 butir 2 dengan G' (§28.15.4 butir 1). Dipanggil controller hanya bila INSIGHT_SM_ENABLED =
        "true". `personas` None = persona dari grup user (PERSONA_GROUPS efn_insight)."""
        res = super()._efn_insight_sm_scope(personas=personas)
        held = set(self._efn_insight_personas() if personas is None else personas)
        if not held:
            return res
        # SMC: company di tabel relasi user (bukan switcher), aktif, kode SM. Pola _insight_scope §26.2.
        self.env["res.users"].flush_model(["company_ids"])
        self.env.cr.execute("SELECT cid FROM res_company_users_rel WHERE user_id = %s", (self.id,))
        ids = sorted({row[0] for row in self.env.cr.fetchall()})
        smc = self.env["res.company"].sudo().with_context(active_test=False).browse(ids).exists()
        smc = smc.filtered(lambda c: c.active and c.efn_brand_code in SM_CODES)
        if not smc:
            return res  # tanpa PT SM -> tidak ada persona
        wh_all = set(self.env["stock.warehouse"].sudo().search([("company_id", "in", smc.ids)]).ids)
        roles, wids = set(), set()
        org = held & ORG_PERSONAS
        if org:
            roles |= org
            wids |= {0} | wh_all
        no_outlet = False
        # SEC-G47-01: row policy mengecek persona dan wids terpisah, sedangkan wids = gabungan semua persona. store +
        # persona PT yang TIDAK mencakup semua view store (marketing, dc) akan membuka data store semua outlet; store
        # tidak diterbitkan untuk kombinasi itu. exec mencakup semua view store -> tanpa akses tambahan (SEC-G51-01).
        # Perbaikan model (klaim swids terpisah) menunggu revisi kontrak §28.13.
        if "store" in held and org and not org & STORE_COVERING_PERSONAS:
            held = held - {"store"}
        if "store" in held:
            own = set(self.sudo().efn_outlet_warehouse_ids.ids) & wh_all
            if own:
                roles.add("store")
                wids |= own
            else:
                no_outlet = True
        if not roles:
            return dict(res, no_outlet=no_outlet)
        cats = sorted(self.sudo().efn_insight_categ_ids.exists().ids) if "category" in roles else []
        return {
            "personas": sorted(roles),
            "codes": sorted({c.efn_brand_code for c in smc}),
            "cids": sorted(smc.ids),
            "wids": sorted(wids),
            "cats": cats,
            "no_outlet": no_outlet,
        }
