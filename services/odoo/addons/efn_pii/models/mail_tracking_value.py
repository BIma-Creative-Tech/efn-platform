"""SEC-136: tracking many2one ke partner terlindungi tidak menyimpan nama (display_name) plaintext.

`mail.tracking.value._create_tracking_values` (19.0, `@api.model`) mengisi `old_value_char`/`new_value_char`
dengan `display_name` record many2one, mis. `sale.order.partner_id`, `res.partner.parent_id`. Untuk partner
terlindungi label diganti `Kontak terlindungi #<id>` (bukan data pribadi); id tetap di kolom integer.
"""

from odoo import api, models

from .fields_pii import PROTECTED_FIELD
from .pii_model import REDACTED_LABEL


def _is_protected_partner(value):
    return (
        isinstance(value, models.BaseModel)
        and value._name == "res.partner"
        and len(value) == 1
        and bool(value.sudo()[PROTECTED_FIELD])
    )


class MailTrackingValue(models.Model):
    _inherit = "mail.tracking.value"

    @api.model
    def _create_tracking_values(self, initial_value, new_value, col_name, col_info, record):
        values = super()._create_tracking_values(initial_value, new_value, col_name, col_info, record)
        if not isinstance(values, dict) or (col_info or {}).get("relation") != "res.partner":
            return values
        for key, partner in (("old_value_char", initial_value), ("new_value_char", new_value)):
            if key in values and _is_protected_partner(partner):
                values[key] = f"{REDACTED_LABEL}{partner.id}"
        return values
