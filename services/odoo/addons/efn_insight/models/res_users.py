"""Persona Insight + hook cakupan supermarket (G47 + G52, docs/04 §28.13.2-§28.13.3, §28.15.2-§28.15.4, ADR-041
§3.4/§3.6 Proposed).

G52 (L-239): kelima grup persona dimiliki modul ini (xmlid name tetap, dipindah dari `efn_insight_sm`), dipakai F&B
(exec, store, marketing) dan SMD (kelimanya). `PERSONA_GROUPS` = satu-satunya peta persona -> xmlid; `efn_insight_sm`
memakainya lewat `_efn_insight_personas()`.

`efn_insight` terpasang juga di DB/instalasi F&B tanpa modul supermarket, jadi di sini cakupan SM selalu kosong.
`efn_insight_sm` (modul jembatan, auto_install) menimpa `_efn_insight_sm_scope`. Metode privat (awalan `_`): tidak bisa
dipanggil lewat RPC (SEC-58). Kill switch `INSIGHT_SM_ENABLED` dicek di controller (bukan di sini) agar modul penimpa
tidak bisa melewatinya.
"""

from odoo import fields, models

# docs/04 §28.15.2. Urutan kunci = urutan PERSONAS (abjad).
PERSONA_GROUPS = {
    "category": "efn_insight.group_insight_category",
    "dc": "efn_insight.group_insight_dc",
    "exec": "efn_insight.group_insight_exec",
    "marketing": "efn_insight.group_insight_marketing",
    "store": "efn_insight.group_insight_store",
}


def empty_sm_scope():
    """Bentuk nilai kembali hook. `no_outlet` = user ber-persona store tanpa outlet yang sah (halaman info)."""
    return {"personas": [], "codes": [], "cids": [], "wids": [], "cats": [], "no_outlet": False}


class ResUsers(models.Model):
    _inherit = "res.users"

    # docs/04 §28.15.3 (L-240): cabang ESB yang dikelola kepala toko F&B. Hanya admin user (groups + ACL res.users CE);
    # TIDAK masuk SELF_WRITEABLE_FIELDS. Teraudit lewat efn_audit CONTROL_SPECS["res.users"].
    efn_insight_esb_branch_ids = fields.Many2many(
        "efn.insight.esb.branch",
        "efn_res_users_insight_esb_branch_rel",
        "user_id",
        "branch_id",
        string="Cabang ESB dikelola (Insight)",
        domain="[('company_id', 'in', company_ids)]",
        groups="base.group_erp_manager",
        help="Hanya untuk persona Insight: Kepala toko di brand F&B (data ESB). Kepala toko tanpa persona lain hanya "
        "melihat cabang di sini (maks 50).",
    )

    def _efn_insight_personas(self):
        """Persona yang dipegang user (himpunan kunci PERSONA_GROUPS)."""
        self.ensure_one()
        return {persona for persona, xmlid in PERSONA_GROUPS.items() if self.has_group(xmlid)}

    def _efn_insight_sm_scope(self, personas=None):
        """Cakupan supermarket user (satu record): personas, codes, cids, wids, cats (list int/str terurut).

        `personas` = G' (docs/04 §28.15.4 butir 1) dari controller; None = hitung dari grup user.
        """
        self.ensure_one()
        return empty_sm_scope()
