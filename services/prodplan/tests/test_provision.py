"""Provisioning JIT ke Odoo (docs/04 §30.17.11): flag mati = tanpa koneksi, non-blocking, payload tertutup, dedup."""

import json
import re

from efn_prodplan import metrics
from efn_prodplan.config import OdooProvisionSettings
from efn_prodplan.provision import (
    BODY_MAX_BYTES,
    PAYLOAD_KEYS,
    QUEUE_MAX,
    OdooProvisioner,
    build_provisioner,
    provision_payload,
)


def payload(**over):
    args = {
        "esb_account": "pb",
        "username_norm": "planner.uji",
        "full_name": "Planner Uji",
        "company_code": "PB",
        "role_id": 7,
        "pp_roles": ["pp_warehouse", "pp_planner"],
        "branch_ids": [13, 4, 4],
    }
    args.update(over)
    return provision_payload(**args)


def test_payload_exactly_seven_keys_without_secrets():
    body = payload()
    assert list(body) == ["payload"]
    inner = body["payload"]
    assert tuple(inner) == PAYLOAD_KEYS and len(inner) == 7
    assert inner["pp_roles"] == ["pp_planner", "pp_warehouse"] and inner["branch_ids"] == [4, 13]
    text = json.dumps(body)
    assert "password" not in text and "token" not in text.lower()
    assert len(payload(full_name="x" * 300)["payload"]["full_name"]) == 128
    assert len(payload(branch_ids=range(1, 500))["payload"]["branch_ids"]) == 200


def test_payload_satisfies_odoo_validate_payload_rules():
    """Cermin `efn_esb_identity.validate_payload` (services/odoo/addons/efn_esb_identity/models/efn_esb_identity.py):
    tujuh kunci persis, akun ∈ brand ESB, username regex Odoo, nama <= 128 tanpa karakter kontrol, role_id int
    0..2^32-1 atau None, peran ∈ PP_ROLES tanpa duplikat, cabang int 1..2^32-1 maks 200, body <= 16 KiB."""
    odoo_username = re.compile(r"^[a-z0-9._@-]{1,64}$")
    for body in (
        payload(),
        payload(role_id=None, pp_roles=[], full_name=""),
        payload(full_name="Bu\tdi\nSantoso\x00\x7f", branch_ids=range(1, 500), pp_roles=["pp_planner"] * 3),
    ):
        inner = body["payload"]
        assert set(inner) == set(PAYLOAD_KEYS)
        assert inner["esb_account"] in {"bacha", "cup", "imd", "pb", "wtz"}
        assert odoo_username.match(inner["username"])
        assert len(inner["full_name"]) <= 128
        assert not any(ord(c) < 32 or ord(c) == 127 for c in inner["full_name"])
        assert inner["role_id"] is None or 0 <= inner["role_id"] <= 2**32 - 1
        assert len(set(inner["pp_roles"])) == len(inner["pp_roles"])
        assert set(inner["pp_roles"]) <= {"pp_planner", "pp_warehouse"}
        assert len(inner["branch_ids"]) <= 200 and all(1 <= b <= 2**32 - 1 for b in inner["branch_ids"])
        assert len(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()) <= BODY_MAX_BYTES
    assert payload(full_name="Bu\tdi\nSantoso\x00\x7f")["payload"]["full_name"] == "Bu di Santoso"


def test_disabled_flag_means_no_connection_and_no_queue():
    prov = build_provisioner(OdooProvisionSettings(enabled=False))
    assert not prov.enabled
    prov.enqueue(payload(), 1)
    assert prov.run_once() is False  # tidak ada antrean


def test_send_ok_then_identical_payload_skipped_for_24h():
    sent = []
    now = [0.0]
    prov = OdooProvisioner(lambda body: sent.append(body) or 200, enabled=True, clock=lambda: now[0])
    before = metrics.ODOO_PROVISION.value("skipped")
    prov.enqueue(payload(), 1)
    assert prov.run_once() and len(sent) == 1
    prov.enqueue(payload(), 1)
    assert metrics.ODOO_PROVISION.value("skipped") == before + 1 and prov.run_once() is False
    now[0] = 86_401
    prov.enqueue(payload(), 1)
    assert prov.run_once() and len(sent) == 2


def test_odoo_down_counts_failed_and_never_raises():
    def boom(body):
        raise TimeoutError("odoo timeout")

    prov = OdooProvisioner(boom, enabled=True)
    before = metrics.ODOO_PROVISION.value("failed")
    prov.enqueue(payload(), 1)
    assert prov.run_once() is True
    assert metrics.ODOO_PROVISION.value("failed") == before + 1
    prov503 = OdooProvisioner(lambda body: 503, enabled=True)
    prov503.enqueue(payload(), 1)
    assert prov503.run_once() and metrics.ODOO_PROVISION.value("failed") == before + 2


def test_full_queue_drops():
    prov = OdooProvisioner(lambda body: 200, enabled=True)
    before = metrics.ODOO_PROVISION.value("dropped")
    for i in range(QUEUE_MAX + 3):
        prov.enqueue(payload(username_norm=f"user{i}"), i)
    assert metrics.ODOO_PROVISION.value("dropped") == before + 3
