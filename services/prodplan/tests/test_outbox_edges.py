"""Celah mesin status outbox (docs/04 §30.9) di luar test_outbox.py: penolakan ESB di tengah rangkaian dokumen,
auth gagal, kegagalan baca saat konfirmasi, jalur hapus (pembatalan) yang tidak mulus, brand tidak aktif, kirim ulang.

Memakai FakeStore/FakeEsb/Env dari test_outbox.py (satu waktu simulasi; tidak ada tidur nyata).
"""

from datetime import timedelta  # noqa: I001 - `test_outbox` = modul tetangga (bukan first-party), urutan blok sengaja

from efn_prodplan.esb import EsbAuthError, EsbError, EsbNotSent, EsbRejected, EsbSentUnknown
from efn_prodplan.outbox import pick_next
from test_outbox import T0, Env, row


def _boom(kind, num):
    raise EsbError("down")


# ---------------------------------------------------------------- rangkaian dokumen
def test_rejection_mid_sequence_blocks_rest_never_reposts_and_cancel_deletes_confirmed():
    env = Env([row(1), row(2), row(3)])
    env.esb.post_outcomes = ["ST1", EsbRejected(400, "VAL")]
    env.worker.run_once()  # kirim o1
    env.esb.docs["ST1"] = {"additionalInfo": env.store.rows["o1"]["marker"], "statusID": 1}
    env.worker.run_once()  # konfirmasi o1
    env.worker.run_once()  # kirim o2 -> ditolak
    assert [env.status(o) for o in ("o1", "o2", "o3")] == ["confirmed", "failed", "pending"]
    assert not env.worker.run_once()  # o3 menunggu o2 yang tidak confirmed
    env.advance(3600)
    assert not env.worker.run_once()
    assert len(env.esb.posts) == 2  # tidak ada POST ulang, o3 tidak pernah dikirim

    # Pembatalan rencana (service.cancel): pending -> failed `cancelled`, confirmed -> delete_pending.
    env.store.rows["o3"].update(status="failed", last_error_code="cancelled")
    env.store.rows["o1"].update(status="delete_pending", next_attempt_at=env.now)
    env.esb.delete_outcomes = ["deleted"]
    env.worker.run_once()
    assert env.status("o1") == "deleted" and env.esb.deletes == [("simple_transfer", "ST1")]
    assert env.status("o2") == "failed" and env.status("o3") == "failed"
    assert not env.worker.run_once()


def test_resend_after_not_found_reuses_same_body_and_marker():
    env = Env([row(1)])
    env.esb.post_outcomes = [EsbSentUnknown("timeout"), "ST9"]
    env.worker.run_once()
    env.advance(301)
    env.worker.run_once()  # rekonsiliasi: lewat grace -> failed
    r = env.store.rows["o1"]
    assert r["status"] == "failed" and r["last_error_code"] == "not_found_after_timeout"
    marker = r["marker"]
    # service.resend: failed -> pending, last_error_code dihapus, next_attempt_at = sekarang
    r.update(status="pending", last_error_code=None, next_attempt_at=env.now)
    env.worker.run_once()
    assert env.status("o1") == "sent" and env.store.rows["o1"]["attempts"] == 2
    assert env.store.rows["o1"]["marker"] == marker
    assert env.esb.posts[0] == env.esb.posts[1] == ("simple_transfer", {"seq": 1})


# ---------------------------------------------------------------- kirim
def test_auth_failure_before_send_backs_off_exponentially_and_stays_pending():
    env = Env([row(1)])
    env.esb.post_outcomes = [EsbAuthError("login"), EsbAuthError("login")]
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "pending" and r["last_error_code"] == "esb_auth_failed"
    assert r["next_attempt_at"] == T0 + timedelta(seconds=30)
    env.advance(31)
    env.worker.run_once()
    assert env.store.rows["o1"]["next_attempt_at"] == env.now + timedelta(seconds=60)  # percobaan ke-2
    assert env.store.rows["o1"]["status"] == "pending"


def test_generic_client_error_becomes_unknown_and_is_never_reposted():
    env = Env([row(1)])
    env.esb.post_outcomes = [EsbError("allowlist")]
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "unknown" and r["last_error_code"] == "client_error"
    assert not env.worker.run_once()  # lease belum habis
    env.advance(121)
    env.worker.run_once()  # rekonsiliasi (belum lewat grace 300 dtk)
    assert env.status("o1") == "unknown"
    env.advance(300)
    env.worker.run_once()
    assert env.status("o1") == "failed" and env.store.rows["o1"]["last_error_code"] == "not_found_after_timeout"
    assert len(env.esb.posts) == 1


def test_unknown_brand_client_defers_row_without_state_change():
    env = Env([row(1, esb_brand="zzz")])
    assert env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "pending" and r["next_attempt_at"] == T0 + timedelta(seconds=600)
    assert env.esb.posts == []


# ---------------------------------------------------------------- konfirmasi
def test_confirm_never_downgrades_row_with_document_number():
    # Perilaku berubah (SEC-226, putaran 2): dulu sent -> unknown -> failed; kini baris ber-nomor tetap `sent`.
    env = Env([row(1, "sent", esb_doc_number="ST1", sent_at=T0)])
    env.worker.run_once()  # dokumen belum terlihat, masih dalam grace
    r = env.store.rows["o1"]
    assert r["status"] == "sent" and r["next_attempt_at"] == T0 + timedelta(seconds=30)
    env.advance(301)
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "sent" and r["last_error_code"] == "doc_not_found" and r["esb_doc_number"] == "ST1"
    assert r["next_attempt_at"] == env.now + timedelta(seconds=600)
    env.advance(601)
    env.worker.run_once()
    assert env.status("o1") == "sent"
    assert [a for a in env.store.audits if a[1] == "outbox_doc_not_found"] == [("o1", "outbox_doc_not_found")]
    env.esb.docs["ST1"] = {"additionalInfo": r["marker"], "statusID": 1}
    env.advance(601)
    env.worker.run_once()
    assert env.status("o1") == "confirmed"
    assert env.esb.posts == []


def test_confirm_survives_esb_read_error_and_keeps_status():
    env = Env([row(1, "sent", esb_doc_number="ST1", sent_at=T0)])
    env.esb.get_doc = _boom
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "sent" and r["next_attempt_at"] == T0 + timedelta(seconds=30)


# ---------------------------------------------------------------- hapus (pembatalan)
def test_delete_not_sent_goes_back_to_delete_pending_with_backoff():
    env = Env([row(1, "delete_pending", esb_doc_number="ST1", attempts=1)])
    env.esb.docs = {"ST1": {"additionalInfo": "pp:000000000001"}}
    env.esb.delete_outcomes = [EsbNotSent("dns")]
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "delete_pending" and r["last_error_code"] == "not_sent"
    assert r["next_attempt_at"] == T0 + timedelta(seconds=30)


def test_delete_when_document_already_gone_is_deleted_with_404():
    env = Env([row(1, "delete_pending", esb_doc_number="ST1")])
    env.esb.delete_outcomes = ["missing"]
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "deleted" and r["last_http_status"] == 404


def test_delete_rejected_but_document_gone_counts_as_deleted():
    env = Env([row(1, "delete_pending", esb_doc_number="ST1")])
    env.esb.delete_outcomes = [EsbRejected(404)]
    env.esb.docs = {}
    env.worker.run_once()
    assert env.status("o1") == "deleted"


def test_delete_without_document_number_never_calls_esb():
    env = Env([row(1, "delete_pending")])
    env.worker.run_once()
    assert env.status("o1") == "deleted" and env.esb.deletes == []


def test_delete_check_with_write_disabled_only_waits_if_document_exists():
    env = Env([row(1, "deleting", esb_doc_number="ST1")], write=False)
    env.esb.docs = {"ST1": {}}
    env.worker.run_once()
    assert env.status("o1") == "deleting" and env.esb.deletes == []
    assert env.store.rows["o1"]["next_attempt_at"] == T0 + timedelta(seconds=30)
    env.advance(31)
    env.esb.docs = {}
    env.worker.run_once()
    assert env.status("o1") == "deleted"


# ---------------------------------------------------------------- pemilihan
def test_pick_next_takes_oldest_due_plan_first_and_ignores_blocked_plan():
    rows = [
        row(1, plan_id="A", next_attempt_at=T0),
        row(1, plan_id="B", next_attempt_at=T0 - timedelta(seconds=10)),
        row(1, "failed", plan_id="C"),
        row(2, plan_id="C"),  # C: baris 2 menunggu baris 1 yang gagal
    ]
    picked = pick_next(rows, True, T0)
    assert picked is not None and picked[0]["plan_id"] == "B" and picked[1] == "send"


def test_pick_next_delete_blocked_by_inflight_higher_rows():
    for blocker in ("sent", "unknown", "sending", "confirmed", "delete_failed"):
        rows = [row(1, "delete_pending", esb_doc_number="ST1"), row(2, blocker, esb_doc_number="ST2")]
        picked = pick_next(rows, True, T0)
        assert picked is None or picked[0]["seq"] == 2, blocker  # baris 1 tidak boleh dihapus lebih dulu
