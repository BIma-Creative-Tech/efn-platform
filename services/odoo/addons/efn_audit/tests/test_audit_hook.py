"""Hook create/write/unlink, format nilai, masking, denylist, filter_domain, aturan (docs/08 §5.1).

Nomor HP/email di bawah fiktif khusus test.
"""

from unittest.mock import patch

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.efn_audit.models.base import Base as EfnAuditBase
from odoo.addons.efn_audit.models.base import mask_email

from .common import EfnAuditCase

PHONE_OLD = "0812-3456-7890"
PHONE_NEW = "0813-1111-2222"


@tagged("post_install", "-at_install", "efn")
class TestEfnAuditHook(EfnAuditCase):
    def setUp(self):
        super().setUp()
        self.Category = self.env["res.partner.category"]
        self.rule = self._set_rule("res.partner.category", tracked=("name", "color", "parent_id", "partner_ids"))

    # ------------------------------------------------------------- operasi
    def test_create_write_unlink_logged(self):
        cat = self.Category.create({"name": "Audit A", "color": 3})
        log = self._logs(cat, "create")
        self.assertEqual(len(log), 1)
        self.assertEqual(log.user_id, self.env.user)
        self.assertEqual(log.rule_id, self.rule)
        self.assertEqual(log.res_name, "Audit A")
        self.assertEqual(set(log.line_ids.mapped("field_name")), {"name", "color"}, "hanya field di vals")
        self.assertFalse(self._line(log, "name").old_value)
        self.assertEqual(self._line(log, "name").new_value, "Audit A")

        cat.write({"name": "Audit B", "color": 3})
        log = self._logs(cat, "write")
        self.assertEqual(len(log), 1)
        self.assertEqual(log.line_ids.mapped("field_name"), ["name"], "field tidak berubah dibuang")
        self.assertEqual((self._line(log, "name").old_value, self._line(log, "name").new_value), ("Audit A", "Audit B"))

        cat_id = cat.id
        cat.unlink()
        log = self.Log.search(
            [("model_name", "=", "res.partner.category"), ("res_id", "=", cat_id), ("operation", "=", "unlink")]
        )
        self.assertEqual(len(log), 1)
        self.assertFalse(log.line_ids, "tanpa snapshot bila capture_unlink_values=False")

    def test_flags_respected(self):
        self.rule.write({"log_create": False, "log_write": False, "log_unlink": False})
        cat = self.Category.create({"name": "Tanpa Log"})
        cat.write({"name": "Tetap Tanpa Log"})
        cat_id = cat.id
        cat.unlink()
        self.assertFalse(self.Log.search([("model_name", "=", "res.partner.category"), ("res_id", "=", cat_id)]))

    def test_unchanged_value_no_log(self):
        cat = self.Category.create({"name": "Sama"})
        cat.write({"name": "Sama", "color": cat.color})
        self.assertFalse(self._logs(cat, "write"))

    def test_untracked_field_no_read(self):
        cat = self.Category.create({"name": "Tak Dilacak"})
        with patch.object(EfnAuditBase, "_efn_audit_read", autospec=True, side_effect=AssertionError("read")) as mocked:
            cat.write({"active": False})
        mocked.assert_not_called()
        self.assertFalse(self._logs(cat, "write"))

    def test_unlink_snapshot(self):
        self.rule.capture_unlink_values = True
        cat = self.Category.create({"name": "Snapshot", "color": 5})
        cat_id = cat.id
        cat.unlink()
        log = self.Log.search(
            [("model_name", "=", "res.partner.category"), ("res_id", "=", cat_id), ("operation", "=", "unlink")]
        )
        self.assertEqual(self._line(log, "name").old_value, "Snapshot")
        self.assertEqual(self._line(log, "color").old_value, "5")
        self.assertFalse(self._line(log, "name").new_value)

    def test_relational_format_and_refs(self):
        parent = self.Category.create({"name": "Induk"})
        partners = self.env["res.partner"].create([{"name": "Relasi 1"}, {"name": "Relasi 2"}])
        cat = self.Category.create({"name": "Anak"})
        cat.write({"parent_id": parent.id, "partner_ids": [Command.set(partners.ids)]})
        log = self._logs(cat, "write")
        parent_line = self._line(log, "parent_id")
        self.assertEqual(parent_line.new_value, "Induk")
        self.assertEqual(parent_line.new_ref, f"res.partner.category,{parent.id}")
        m2m = self._line(log, "partner_ids")
        self.assertEqual(set(m2m.new_value.split(", ")), {"Relasi 1", "Relasi 2"})
        self.assertEqual(m2m.new_ref, "res.partner," + ",".join(str(i) for i in sorted(partners.ids)))

    def test_sudo_keeps_real_user(self):
        user = self.env["res.users"].create(
            {
                "name": "User Audit",
                "login": "user.audit.efn",
                "group_ids": [Command.set([self.env.ref("base.group_user").id])],
            }
        )
        cat = self.Category.create({"name": "Sudo"})
        cat.with_user(user).sudo().write({"name": "Sudo 2"})
        log = self._logs(cat, "write")
        self.assertEqual(log.user_id, user)
        self.assertTrue(log.is_sudo)

    def test_new_rule_active_without_restart(self):
        country = self.env["res.country"].search([], limit=1)
        old_name = country.name
        country.write({"name": "Negara Uji 1"})
        self.assertFalse(self._logs(country))
        self._set_rule("res.country", tracked=("name",))
        country.write({"name": "Negara Uji 2"})
        log = self._logs(country, "write")
        self.assertEqual(len(log), 1)
        self.assertEqual(self._line(log, "name").new_value, "Negara Uji 2")
        self.assertNotEqual(old_name, "Negara Uji 2")
        # Arsip aturan -> berhenti mencatat, juga tanpa restart.
        self.Rule.search([("model_name", "=", "res.country")]).active = False
        country.write({"name": "Negara Uji 3"})
        self.assertEqual(len(self._logs(country, "write")), 1)

    def test_rule_changes_always_audited(self):
        self.rule.write({"retention_days": 90})
        log = self.Log.search(
            [("model_name", "=", "efn.audit.rule"), ("res_id", "=", self.rule.id), ("operation", "=", "write")], limit=1
        )
        self.assertTrue(log)
        self.assertEqual(self._line(log, "retention_days").new_value, "90")

    def test_company_of_record_stored(self):
        company_b = self.env["res.company"].create({"name": "PT Audit B"})
        self._set_rule("res.partner", tracked=("name",))
        partner = self.env["res.partner"].create({"name": "Partner B", "company_id": company_b.id})
        partner.write({"name": "Partner B2"})
        self.assertEqual(self._logs(partner, "write").company_id, company_b)

    # ------------------------------------------------------ filter_domain
    def test_filter_domain_config_parameter(self):
        self._set_rule(
            "ir.config_parameter",
            tracked=("key", "value"),
            filter_domain="['|', ('key', '=like', 'efn.%'), ('key', 'in', ['auth_totp.policy'])]",
        )
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("efn.audit.test_key", "1")
        ICP.set_param("efn.audit.test_key", "2")
        ICP.set_param("efnx.audit.not_logged", "1")
        efn = ICP.search([("key", "=", "efn.audit.test_key")])
        other = ICP.search([("key", "=", "efnx.audit.not_logged")])
        self.assertEqual(len(self._logs(efn, "create")), 1)
        write = self._logs(efn, "write")
        self.assertEqual((self._line(write, "value").old_value, self._line(write, "value").new_value), ("1", "2"))
        self.assertFalse(self._logs(other))

    def test_invalid_rule_rejected(self):
        with self.assertRaises(ValidationError):
            self._set_rule("res.partner.category", filter_domain="[('tidak_ada', '=', 1)]")
        with self.assertRaises(ValidationError):
            self.Rule.create({"name": "Log", "model_id": self.env["ir.model"]._get_id("efn.audit.log")})
        with self.assertRaises(ValidationError):
            self.Rule.create({"name": "Wizard", "model_id": self.env["ir.model"]._get_id("base.module.update")})
        with self.assertRaises(ValidationError):
            self.rule.write({"tracked_field_ids": [Command.set(self._fields("res.partner", "name"))]})

    # ------------------------------------------------------ masking & deny
    def test_phone_email_masked(self):
        self._set_rule("res.partner", tracked=("phone", "email", "name"), masked=("phone", "email"))
        partner = self.env["res.partner"].create(
            {"name": "Member Mask", "phone": PHONE_OLD, "email": "budi.mask@example.com"}
        )
        partner.write({"phone": PHONE_NEW, "email": "budi.baru@example.com"})
        log = self._logs(partner, "write")
        phone = self._line(log, "phone")
        self.assertTrue(phone.is_masked)
        self.assertEqual((phone.old_value, phone.new_value), ("62812****890", "62813****222"))
        self.assertEqual(self._line(log, "email").new_value, "b***@example.com")
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT count(*) FROM efn_audit_log_line"
            " WHERE old_value ~ %s OR new_value ~ %s OR old_value ILIKE %s OR new_value ILIKE %s",
            ["34567|11112222|3456", "34567|11112222|3456", "%budi.baru%", "%budi.baru%"],
        )
        self.assertEqual(self.env.cr.fetchone()[0], 0, "nilai lengkap HP/email tidak boleh ada di DB")
        self.assertEqual(mask_email("x"), "****")

    def test_denylist_never_logged(self):
        user = self.env["res.users"].create({"name": "Deny", "login": "deny.audit.efn"})
        self._set_rule("res.users", tracked=("login", "signature"))
        user.write({"login": "deny2.audit.efn", "signature": "<p>rahasia</p>"})
        log = self._logs(user, "write")
        self.assertEqual(log.line_ids.mapped("field_name"), ["login"])
        # Binary dipilih eksplisit -> tetap tidak dicatat.
        self._set_rule("res.partner", tracked=("name", "image_1920"))
        partner = self.env["res.partner"].create({"name": "Img"})
        partner.write({"name": "Img 2", "image_1920": False})
        names = self._logs(partner, "write").line_ids.mapped("field_name")
        self.assertEqual(names, ["name"])
        # Semua field (tracked kosong): password/signature/binary/teknis tidak ikut.
        self._set_rule("res.users")
        tracked = self.Rule._efn_get_rule("res.users").tracked
        self.assertIn("login", tracked)
        self.assertFalse({f for f in tracked if "password" in f or "signature" in f})
        self.assertFalse(tracked & {"write_date", "write_uid", "create_date", "image_1920"})
