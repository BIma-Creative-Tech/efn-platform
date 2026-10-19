"""E-47d (G22, docs/04 §14.7 L-74; docs/08 §13.6): grup teknis QR + parameter grup sensitif = parameter kontrol."""

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.efn_role.models.role_policy import SENSITIVE_PARAM, TECHNICAL_GROUP_XMLIDS

from .common import EfnRoleCase


@tagged("post_install", "-at_install", "efn")
class TestEfnRoleE47d(EfnRoleCase):
    def setUp(self):
        super().setUp()
        self.ICP = self.env["ir.config_parameter"].sudo()
        # Aturan admin parameter (efn_setup_audit, filter `efn.%`) dimatikan: yang diuji spesifikasi BAWAAN.
        self.env["efn.audit.rule"].sudo().search([("model_name", "=", "ir.config_parameter")]).write({"active": False})

    def _param_logs(self, key):
        param = self.ICP.search([("key", "=", key)])
        return (
            self.env["efn.audit.log"]
            .sudo()
            .search([("model_name", "=", "ir.config_parameter"), ("res_id", "=", param.id)])
        )

    def test_qr_group_is_technical(self):
        self.assertIn("efn_pos_bff_order.group_qr_bff", TECHNICAL_GROUP_XMLIDS)
        group = self.env.ref("efn_pos_bff_order.group_qr_bff", raise_if_not_found=False)
        if group is None:
            self.skipTest("efn_pos_bff_order belum terpasang")
        tech = self._make_user("efn_role_qr_tech", ["efn_pos_bff_order.group_qr_bff"], self.pb)
        role = self._role("Peran Uji QR", self.g_role1, company=self.pb)
        with self.assertRaises(ValidationError):
            self._assign(role, tech)
        with self.assertRaises(ValidationError):
            self._role("Peran berisi grup QR", group, company=self.pb)

    def test_sensitive_param_is_control(self):
        # Parameter bisa sudah ada (efn_setup_role) -> set pertama = create atau write; set kedua selalu write.
        self.ICP.set_param(SENSITIVE_PARAM, "efn_audit.group_auditor")
        self.ICP.set_param(SENSITIVE_PARAM, "efn_audit.group_auditor,base.group_erp_manager")
        logs = self._param_logs(SENSITIVE_PARAM)
        self.assertIn("write", set(logs.mapped("operation")))
        # Parameter efn_role.* lain tidak otomatis menjadi kontrol.
        self.ICP.set_param("efn_role.efn_test_other_flag", "1")
        self.assertFalse(self._param_logs("efn_role.efn_test_other_flag"))
