"""Outbox publikasi kebijakan MGM Odoo -> BFF (docs/04 §20.1; G31).

Mesin kirim, tanda tangan, retry/backoff, 409/422 final, dan "tanpa kunci = tertahan" diwarisi dari
``efn.loyalty.policy.outbox`` (``_inherit`` prototipe, tabel sendiri). Yang berbeda hanya path BFF, cron, dan kebijakan
sumber (``efn.mgm.policy``). ``idempotency_key = mgm_policy:<company_code>:<version>``.
"""

from odoo import _, api, fields, models

MGM_POLICY_PATH = "/v1/pos/loyalty/mgm-policies/{company_code}/{version}"


class EfnMgmPolicyOutbox(models.Model):
    _name = "efn.mgm.policy.outbox"
    _inherit = "efn.loyalty.policy.outbox"
    _description = "EFN MGM Policy Publication Outbox"

    policy_id = fields.Many2one(
        "efn.mgm.policy", required=True, readonly=True, ondelete="restrict", index=True, check_company=True
    )

    _efn_path_template = MGM_POLICY_PATH
    _efn_cron_xmlid = "efn_mgm_policy.ir_cron_efn_mgm_policy_outbox"

    _idempotency_key_unique = models.Constraint(
        "UNIQUE (idempotency_key)", "Kunci idempotensi outbox kebijakan MGM harus unik."
    )

    @api.model
    def _efn_enqueue_policy(self, policy):
        key = f"mgm_policy:{policy.company_code}:{policy.version}"
        existing = self.sudo().search([("idempotency_key", "=", key)], limit=1)
        if existing:
            return existing
        return self.sudo().create({"company_id": policy.company_id.id, "policy_id": policy.id, "idempotency_key": key})

    @api.model
    def _cron_efn_mgm_policy_outbox_run(self):
        self._efn_process_due(limit=50, commit=True)

    def action_efn_retry(self):
        # Eksplisit agar permukaan RPC modul ini terlihat di pemindaian statis (SEC-58); pemeriksaan grup/akses/company
        # dan syarat state failed ada di implementasi induk.
        return super().action_efn_retry()

    def _efn_activity_summary(self):
        return _("Publikasi kebijakan MGM")
