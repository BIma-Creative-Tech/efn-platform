"""Hook install/uninstall (port dari core/custom_home_console/hooks.py, ADR-049).

Beranda EFN menjadi landing default dengan mengisi ``res.users.action_id`` hanya untuk user INTERNAL yang belum punya
landing sendiri (pilihan admin per user tidak ditimpa). Saat uninstall hanya pointer ke action kita yang dikosongkan.
"""

ACTION_XMLID = "efn_web_home.action_home"


def _efn_post_init_set_default_home(env):
    action = env.ref(ACTION_XMLID, raise_if_not_found=False)
    if not action:
        return
    users = (
        env["res.users"]
        .sudo()
        .with_context(active_test=False)
        .search([("action_id", "=", False), ("share", "=", False)])
    )
    users.write({"action_id": action.id})


def _efn_uninstall_clear_default_home(env):
    action = env.ref(ACTION_XMLID, raise_if_not_found=False)
    if not action:
        return
    env["res.users"].sudo().with_context(active_test=False).search([("action_id", "=", action.id)]).write(
        {"action_id": False}
    )
