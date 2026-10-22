# Smoke G23 (dev saja), dijalankan lewat `odoo shell`: staf (manajer POS PB dev) mengubah status reservasi -> outbox
# event -> cron kirim ke BFF (HMAC). Cetak state/seq/outbox singkat. Bukan modul/test unit.
# ruff: noqa: F821  (variabel `env` disediakan odoo shell)
import os

ref = os.environ.get("BK_REF") or ""
B = env["efn.table.booking"].sudo()
rec = B.search([("platform_booking_ref", "=", ref)], limit=1) if ref else B.search([], order="id desc", limit=1)
assert rec, "reservasi tidak ditemukan"
mgr = env["res.users"].sudo().search([("login", "=", "efn_dev_pos_mgr_pb")], limit=1)
PB = rec.company_id
if not mgr:
    from odoo import Command

    mgr = (
        env["res.users"]
        .sudo()
        .with_context(no_reset_password=True)
        .create(
            {
                "name": "Manajer POS PB (dev)",
                "login": "efn_dev_pos_mgr_pb",
                "company_id": PB.id,
                "company_ids": [Command.set(PB.ids)],
                "group_ids": [
                    Command.set([env.ref("base.group_user").id, env.ref("point_of_sale.group_pos_manager").id])
                ],
            }
        )
    )
op = os.environ.get("BK_OP", "confirm")
if op != "noop":
    getattr(rec.with_user(mgr), "action_efn_" + op)()
    env.cr.commit()
Out = env["efn.booking.event.outbox"].sudo()
Out._cron_efn_booking_outbox_run()
env.cr.commit()
ev = Out.search([("booking_id", "=", rec.id)], order="id desc", limit=3)
outbox = [(e.event, e.state, e.attempt_count, e.last_error) for e in ev]
print(f"EFN_BK state={rec.state} seq={rec.seq} table={rec.table_label} outbox={outbox}")
