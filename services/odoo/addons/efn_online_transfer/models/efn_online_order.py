"""G26 §17.15: jalur bukti transfer (RPC #12) + pelunasan internal oleh Verifikator."""

import base64
import binascii
import hashlib
import logging
import re

from odoo import api, fields, models

from odoo.addons.efn_online_order.models.efn_online_channel import parse_iso_utc
from odoo.addons.efn_online_order.models.efn_online_common import (
    IDEMPOTENCY_KEY_RE,
    PICKUP_CODE_RE,
    UUID_RE,
    canonical_sha256,
    check_str,
    online_error,
    require,
)

_logger = logging.getLogger(__name__)

PROOF_KEYS = frozenset(
    {
        "idempotency_key",
        "proof_id",
        "transfer_ref",
        "image_b64",
        "sha256",
        "duplicate_suspect",
        "uploaded_at",
    }
)
MAX_PROOF_BYTES = 1_572_864  # 1,5 MiB JPEG setelah re-encode BFF (§17.5)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
JPEG_MAGIC = b"\xff\xd8\xff"
LATE_REVIEW = {
    "cancelled": "transfer_paid_after_cancel",
    "placed": "transfer_paid_after_expiry",
}


class EfnOnlineOrder(models.Model):
    _inherit = "efn.online.order"

    transfer_proof_ids = fields.One2many("efn.online.transfer.proof", "order_id", readonly=True)

    def _efn_payment_journal(self):
        self.ensure_one()
        if self.payment_mode == "bank_transfer":
            return self.channel_id.sudo().efn_transfer_journal_id
        return super()._efn_payment_journal()

    # ================================================================ BFF: RPC #12
    @api.model
    def efn_online_transfer_submit(self, channel_code, platform_order_ref, proof):
        """BFF mengirim foto bukti transfer (sudah di-re-encode JPEG, metadata dibuang) -> antrean Verifikator."""
        channel = self._efn_channel_env(channel_code)
        require(isinstance(proof, dict) and set(proof) == PROOF_KEYS)
        key = check_str(proof["idempotency_key"], IDEMPOTENCY_KEY_RE)
        proof_id = check_str(proof["proof_id"], UUID_RE, 36).lower()
        transfer_ref = check_str(proof["transfer_ref"], PICKUP_CODE_RE, 8)
        sha = check_str(proof["sha256"], SHA256_RE, 64)
        require(isinstance(proof["duplicate_suspect"], bool))
        uploaded_at = parse_iso_utc(proof["uploaded_at"])
        image_b64 = proof["image_b64"]
        require(isinstance(image_b64, str) and 0 < len(image_b64) <= (MAX_PROOF_BYTES * 4) // 3 + 4)
        try:
            raw = base64.b64decode(image_b64, validate=True)
        except (binascii.Error, ValueError):
            raise online_error("invalid_input") from None
        require(0 < len(raw) <= MAX_PROOF_BYTES and raw.startswith(JPEG_MAGIC))
        require(hashlib.sha256(raw).hexdigest() == sha)
        payload_sha = canonical_sha256(
            {
                "ref": platform_order_ref,
                "proof_id": proof_id,
                "transfer_ref": transfer_ref,
                "sha256": sha,
                "duplicate_suspect": proof["duplicate_suspect"],
                "uploaded_at": proof["uploaded_at"],
            }
        )
        Proof = self.env["efn.online.transfer.proof"].with_env(channel.env)
        order = self.with_env(channel.env)._efn_find(channel, platform_order_ref)
        if not order:
            raise online_error("not_found")
        existing = Proof.search(["|", ("idempotency_key", "=", key), ("proof_id", "=", proof_id)], limit=1)
        if existing:
            if existing.order_id != order or existing.payload_sha256 != payload_sha or existing.idempotency_key != key:
                raise online_error("idempotency_conflict")
            return {"state": existing.state}
        now = fields.Datetime.now()
        if (
            order.payment_mode != "bank_transfer"
            or order.state != "placed"
            or order.paid_idempotency_key
            or not order.expires_at
            or order.expires_at <= now
        ):
            raise online_error("invalid_state")
        pending = order.transfer_proof_ids.filtered(lambda p: p.state == "awaiting_verification")
        if pending:
            # Satu bukti aktif per pesanan: BFF hanya mengirim ulang setelah `transfer_rejected` (§17.4 no. 1).
            raise online_error("invalid_state")
        account = order.channel_id._efn_online_transfer_account() or {}
        record = Proof.create(
            {
                "company_id": order.company_id.id,
                "order_id": order.id,
                "proof_id": proof_id,
                "idempotency_key": key,
                "payload_sha256": payload_sha,
                "sha256": sha,
                "duplicate_suspect": proof["duplicate_suspect"],
                "uploaded_at": uploaded_at,
                "sale_order_name": order.name,
                "outlet_name": order.outlet_id.public_name or order.outlet_id.code,
                "amount_due": order.amount_due,
                "transfer_ref": transfer_ref,
                "expires_at": order.expires_at,
                "account_label": " ".join(v for v in (account.get("bank_name"), account.get("account_number")) if v),
            }
        )
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": f"bukti-transfer-{proof_id}.jpg",
                    "raw": raw,
                    "mimetype": "image/jpeg",
                    "res_model": record._name,
                    "res_id": record.id,
                    "company_id": order.company_id.id,
                    "public": False,
                }
            )
        )
        record.write({"attachment_id": attachment.id})
        order.transfer_proof_ids.filtered(lambda p: p.state == "rejected").write({"state": "superseded"})
        _logger.info(
            "EFN transfer: bukti %s untuk order %s diterima (dup=%s)",
            record.id,
            order.id,
            record.duplicate_suspect,
        )
        return {"state": record.state}

    # ============================================================ jalur internal
    def _efn_transfer_mark_paid(self, proof):
        """Persetujuan Verifikator (wizard). Order terkunci oleh pemanggil. Alur = ``mark_paid`` (stok -> konfirmasi
        -> faktur -> pembayaran di jurnal transfer kanal). Order sudah batal/kedaluwarsa -> pembayaran dicatat +
        ``review`` (Finance mengembalikan dana, pola L-56)."""
        self.ensure_one()
        order = self.with_env(self.env(su=True)).with_company(self.company_id)
        if order.payment_mode != "bank_transfer":
            raise online_error("invalid_state")
        key = f"transfer:{proof.proof_id}"
        if order.paid_idempotency_key:
            raise online_error("invalid_state")
        now = fields.Datetime.now()
        if order.state == "placed" and order.expires_at and order.expires_at > now:
            return order._efn_apply_payment(proof.proof_id, key, proof.bank_amount, now)
        reason = LATE_REVIEW.get(order.state)
        if not reason:
            raise online_error("invalid_state")
        order.write(
            {
                "payment_ref": proof.proof_id,
                "paid_idempotency_key": key,
                "paid_amount": proof.bank_amount,
                "paid_at": now,
            }
        )
        order._efn_set_review(reason)
        return order._efn_paid_result()

    def _efn_after_cancelled(self):
        result = super()._efn_after_cancelled()
        self.sudo().transfer_proof_ids.filtered(lambda p: p.state == "awaiting_verification").write(
            {"state": "expired"}
        )
        return result
