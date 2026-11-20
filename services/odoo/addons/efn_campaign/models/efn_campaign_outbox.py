"""Outbox kampanye Odoo -> BFF (docs/04 §28.7, L-156, fit-gap G45 S-09).

Odoo tidak publish ke RabbitMQ: baris ``queued`` diklaim BFF lewat ``efn.campaign/efn_campaign_claim`` (atomik,
``FOR UPDATE SKIP LOCKED``), BFF menerbitkan ``promo.campaign.requested`` lalu ``efn_campaign_ack`` -> ``done``.
Klaim tanpa ack 10 menit -> kembali ``queued`` (cron); setelah 5 percobaan -> ``failed`` + aktivitas Marketing.

``payload`` = JSON tanpa data member (kecuali ``partner_id`` integer pada mode ``member_bound``, L-164). Payload
memuat kode voucher (bersifat *bearer*), jadi field ini hanya terbaca Settings/Administrator; BFF membacanya lewat RPC
klaim (sudo setelah cek pemanggil). Baris tidak pernah diubah lewat UI (ACL baca saja).
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models

from . import campaign_api as ca

_logger = logging.getLogger(__name__)

OUTBOX_STATES = [("queued", "Antre"), ("claimed", "Diklaim BFF"), ("done", "Selesai"), ("failed", "Gagal")]
OUTBOX_ERRORS = [
    ("max_attempts", "Melebihi 5 percobaan"),
    ("expired", "Periode kampanye lewat"),
    ("cancelled", "Kampanye dibatalkan"),
]
RECLAIM_MINUTES = 10
MAX_ATTEMPTS = 5


class EfnCampaignOutbox(models.Model):
    _name = "efn.campaign.outbox"
    _description = "EFN outbox kampanye (diklaim BFF)"
    _order = "id desc"
    _check_company_auto = True
    _rec_name = "idempotency_key"

    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True)
    campaign_id = fields.Many2one(
        "efn.campaign", required=True, readonly=True, index=True, ondelete="restrict", check_company=True
    )
    kind = fields.Selection([("segment", "Segmen"), ("markdown", "Markdown")], required=True, readonly=True)
    ref_model = fields.Char(string="Model sumber", required=True, readonly=True)
    ref_id = fields.Integer(string="ID sumber", required=True, readonly=True)
    approval_rev = fields.Integer(required=True, readonly=True)
    idempotency_key = fields.Char(required=True, readonly=True, index=True)
    payload = fields.Json(readonly=True, groups="base.group_system")
    state = fields.Selection(OUTBOX_STATES, default="queued", required=True, readonly=True, index=True)
    claimed_at = fields.Datetime(readonly=True)
    attempts = fields.Integer(readonly=True, default=0)
    done_at = fields.Datetime(readonly=True)
    last_error = fields.Selection(OUTBOX_ERRORS, readonly=True)

    _efn_idempotency_key_uniq = models.Constraint(
        "UNIQUE (idempotency_key)", "Kunci idempotensi outbox kampanye harus unik."
    )
    _efn_campaign_rev_uniq = models.Constraint(
        "UNIQUE (campaign_id, approval_rev)", "Satu baris outbox per revisi approval kampanye."
    )

    @api.model
    def _efn_enqueue(self, campaign, voucher):
        """Antrekan revisi approval kampanye (idempoten per ``idempotency_key``)."""
        camp = campaign.sudo()
        key = camp._efn_idempotency_key()
        existing = self.sudo().search([("idempotency_key", "=", key)], limit=1)
        if existing:
            return existing
        return self.sudo().create(
            {
                "company_id": camp.company_id.id,
                "campaign_id": camp.id,
                "kind": camp.kind,
                "ref_model": camp.source_ref_model or "efn.campaign",
                "ref_id": camp.source_ref_id or camp.id,
                "approval_rev": camp.approval_rev,
                "idempotency_key": key,
                "payload": camp._efn_outbox_payload(voucher),
            }
        )

    def _efn_claim_item(self):
        self.ensure_one()
        row = self.sudo()
        return {
            "id": row.id,
            "idempotency_key": row.idempotency_key,
            "kind": row.kind,
            "ref_model": row.ref_model,
            "ref_id": row.ref_id,
            "approval_rev": row.approval_rev,
            "attempts": row.attempts,
            "payload": row.payload,
        }

    # ---------------------------------------------------------------- cron
    @api.model
    def _cron_efn_reclaim(self):
        """Klaim > 10 menit tanpa ack -> ``queued``; sudah 5 percobaan -> ``failed`` + aktivitas Marketing.

        PT dengan sakelar mati dilewati (baris tetap ``claimed`` sampai sakelar dinyalakan lagi)."""
        Outbox = self.sudo()
        Outbox.flush_model()
        limit = fields.Datetime.now() - timedelta(minutes=RECLAIM_MINUTES)
        self.env.cr.execute(
            "SELECT id FROM efn_campaign_outbox WHERE state = 'claimed' AND claimed_at < %s "
            "ORDER BY id FOR UPDATE SKIP LOCKED",
            (limit,),
        )
        rows = Outbox.browse([r[0] for r in self.env.cr.fetchall()])
        requeued = failed = 0
        enabled = {}
        for row in rows:
            company = row.company_id
            if company.id not in enabled:
                enabled[company.id] = ca.is_enabled(self.env, company)
            if not enabled[company.id]:
                continue
            if row.attempts >= MAX_ATTEMPTS:
                row.write({"state": "failed", "last_error": "max_attempts"})
                reason = _("tidak di-ack BFF setelah %s percobaan", MAX_ATTEMPTS)
                row.campaign_id._efn_notify_delivery_failed(row, reason)
                failed += 1
            else:
                row.write({"state": "queued"})
                requeued += 1
        if requeued or failed:
            _logger.warning("EFN campaign outbox: %d klaim kembali antre, %d gagal final", requeued, failed)
        return {"requeued": requeued, "failed": failed}
