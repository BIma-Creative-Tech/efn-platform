"""Mesin status outbox dengan ESB + store palsu (docs/04 §30.9): tidak ada POST ganda tanpa rekonsiliasi."""

from datetime import UTC, date, datetime, timedelta

from efn_prodplan.esb import EsbNotSent, EsbRejected, EsbSentUnknown
from efn_prodplan.outbox import OutboxWorker, backoff_seconds, pick_next

T0 = datetime(2026, 11, 24, 3, 0, tzinfo=UTC)
SM_NUM = "SM202611240001 - 1"


def row(seq, status="pending", kind="simple_transfer", **kw):
    base = {
        "id": f"o{seq}",
        "company_code": "PB",
        "esb_brand": "pb",
        "esb_env": "staging",
        "plan_id": "p1",
        "seq": seq,
        "phase": "production" if kind == "simple_manufacturing" else "transfers",
        "doc_kind": kind,
        "marker": f"pp:{seq:012x}",
        "request_body": {"seq": seq},
        "status": status,
        "attempts": 0,
        "esb_doc_number": None,
        "next_attempt_at": T0,
        "sent_at": None,
        "production_date": date(2026, 11, 25),
        "branch_id": 4,
        "production_location_id": 7,
    }
    base.update(kw)
    return base


class FakeStore:
    def __init__(self, rows, clock):
        self.rows = {r["id"]: dict(r) for r in rows}
        self.audits = []
        self.clock = clock

    def recover_sending(self):
        n = 0
        for r in self.rows.values():
            if r["status"] == "sending":
                r.update(status="unknown", sent_at=r["sent_at"] or self.clock(), next_attempt_at=self.clock())
                n += 1
        return n

    def candidates(self, esb_env, now):
        return [dict(r) for r in self.rows.values() if r["esb_env"] == esb_env]

    def claim(self, outbox_id, expected_status, new_status, lease_until):
        r = self.rows[outbox_id]
        if r["status"] != expected_status:
            return False
        if new_status == "sending":
            r["attempts"] += 1
            r["sent_at"] = self.clock()
        if new_status is not None:
            r["status"] = new_status
        r["next_attempt_at"] = lease_until
        return True

    def update(self, outbox_id, fields):
        self.rows[outbox_id].update(fields)

    def known_numbers(self, esb_env, doc_kind):
        return {r["esb_doc_number"] for r in self.rows.values() if r["doc_kind"] == doc_kind and r["esb_doc_number"]}

    def audit(self, row, action, detail):
        self.audits.append((row["id"], action))

    def status_counts(self, esb_env):
        counts = {}
        for r in self.rows.values():
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return counts, 0.0


class FakeEsb:
    def __init__(self):
        self.posts = []
        self.deletes = []
        self.gets = []
        self.post_outcomes = []
        self.delete_outcomes = []
        self.docs = {}
        self.listing = []

    def post_doc(self, kind, body):
        self.posts.append((kind, body))
        outcome = self.post_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def delete_doc(self, kind, num):
        self.deletes.append((kind, num))
        outcome = self.delete_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == "deleted":
            self.docs.pop(num, None)
        return outcome

    def get_doc(self, kind, num):
        return self.docs.get(num)

    def get(self, path, params=None):
        self.gets.append((path, dict(params or {})))
        return {"page": 1, "limit": 50, "count": len(self.listing), "data": self.listing}


class Env:
    def __init__(self, rows, write=True):
        self.now = T0
        self.store = FakeStore(rows, lambda: self.now)
        self.esb = FakeEsb()
        self.worker = OutboxWorker(
            self.store,
            {"pb": self.esb},
            esb_env="staging",
            write_enabled=write,
            reconcile_grace_seconds=300,
            clock=lambda: self.now,
        )

    def advance(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)

    def status(self, oid):
        return self.store.rows[oid]["status"]


# ---------------------------------------------------------------- pemilihan (murni)
def test_pick_next_respects_seq_and_write_flag():
    rows = [row(1), row(2)]
    assert pick_next(rows, True, T0)[0]["seq"] == 1
    assert pick_next(rows, False, T0) is None
    rows = [row(1, "sent", esb_doc_number="ST1"), row(2)]
    picked, action = pick_next(rows, True, T0)
    assert (picked["seq"], action) == (1, "confirm")
    rows = [row(1, "confirmed"), row(2)]
    assert pick_next(rows, True, T0)[0]["seq"] == 2
    rows = [row(1, "failed"), row(2)]
    assert pick_next(rows, True, T0) is None  # baris berikut menunggu baris sebelumnya confirmed
    rows = [row(1, "unknown")]
    assert pick_next(rows, False, T0)[1] == "reconcile"  # rekonsiliasi GET tetap jalan saat tulis mati
    assert pick_next([row(1, next_attempt_at=T0 + timedelta(seconds=1))], True, T0) is None


def test_pick_next_delete_descending_seq():
    rows = [row(1, "delete_pending", esb_doc_number="ST1"), row(2, "delete_pending", esb_doc_number="ST2")]
    assert pick_next(rows, True, T0)[0]["seq"] == 2
    rows = [row(1, "delete_pending", esb_doc_number="ST1"), row(2, "deleted")]
    assert pick_next(rows, True, T0)[0]["seq"] == 1
    rows = [row(1, "delete_pending", esb_doc_number="ST1"), row(2, "delete_failed")]
    assert pick_next(rows, True, T0) is None
    assert pick_next([row(1, "delete_pending")], False, T0) is None


def test_backoff():
    assert [backoff_seconds(n) for n in (1, 2, 3, 6, 10)] == [30, 60, 120, 600, 600]


# ---------------------------------------------------------------- alur normal
def test_send_then_confirm():
    env = Env([row(1)])
    env.esb.post_outcomes = ["ST202611240001"]
    assert env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "sent" and r["esb_doc_number"] == "ST202611240001" and r["attempts"] == 1
    assert env.esb.posts == [("simple_transfer", {"seq": 1})]  # body persis dari outbox
    env.esb.docs["ST202611240001"] = {"additionalInfo": r["marker"], "statusID": 1}
    assert env.worker.run_once()
    assert env.status("o1") == "confirmed" and env.store.rows["o1"]["esb_status_id"] == 1
    assert ("o1", "outbox_confirmed") in env.store.audits
    assert not env.worker.run_once()
    assert len(env.esb.posts) == 1


def test_confirm_marker_mismatch_stays_sent_without_resend():
    # SEC-226 (putaran 2): baris ber-nomor tidak turun ke failed; ditandai untuk perhatian manusia.
    env = Env([row(1, "sent", esb_doc_number="ST9")])
    env.esb.docs["ST9"] = {"additionalInfo": "pp:ffffffffffff"}
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "sent" and r["last_error_code"] == "marker_mismatch" and r["esb_doc_number"] == "ST9"
    assert ("o1", "outbox_marker_mismatch") in env.store.audits
    assert env.esb.posts == []


# ---------------------------------------------------------------- timeout setelah kirim
def test_timeout_after_send_goes_unknown_and_never_reposts():
    env = Env([row(1)])
    env.esb.post_outcomes = [EsbSentUnknown("timeout")]
    env.worker.run_once()
    assert env.status("o1") == "unknown"
    assert not env.worker.run_once()  # belum jatuh tempo
    env.advance(31)
    env.worker.run_once()  # rekonsiliasi: tidak ditemukan, masih dalam grace
    assert env.status("o1") == "unknown"
    assert env.esb.gets and env.esb.gets[-1][1]["additionalInfo"] == "pp:000000000001"
    assert env.esb.gets[-1][1]["dateFrom"] == "2026-11-24" and env.esb.gets[-1][1]["dateTo"] == "2026-11-26"
    env.advance(400)
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "failed" and r["last_error_code"] == "not_found_after_timeout"
    env.advance(3600)
    assert not env.worker.run_once()
    assert len(env.esb.posts) == 1  # TIDAK ada POST kedua otomatis


def test_unknown_reconcile_finds_marker():
    env = Env([row(1)])
    env.esb.post_outcomes = [EsbSentUnknown("reset")]
    env.worker.run_once()
    env.esb.listing = [{"simpleTransferNum": "ST77", "additionalInfo": "pp:000000000001"}]
    env.advance(31)
    env.worker.run_once()
    assert env.status("o1") == "sent" and env.store.rows["o1"]["esb_doc_number"] == "ST77"
    env.esb.docs["ST77"] = {"additionalInfo": "pp:000000000001", "statusID": 1}
    env.worker.run_once()
    assert env.status("o1") == "confirmed"
    assert len(env.esb.posts) == 1


def test_reconcile_production_by_detail_and_skips_known_numbers():
    other = row(9, "confirmed", kind="simple_manufacturing", plan_id="p0", esb_doc_number="SM202611240000 - 1")
    env = Env([row(1, "unknown", kind="simple_manufacturing", sent_at=T0), other])
    env.esb.listing = [{"simpleManufacturingNum": "SM202611240000 - 1"}, {"simpleManufacturingNum": SM_NUM}]
    env.esb.docs[SM_NUM] = {"simpleManufacturingDetails": [{"additionalInfo": "pp:000000000001"}], "statusID": 3}
    env.esb.docs["SM202611240000 - 1"] = {"simpleManufacturingDetails": [{"additionalInfo": "pp:000000000001"}]}
    env.worker.run_once()
    assert env.store.rows["o1"]["esb_doc_number"] == SM_NUM
    path, params = env.esb.gets[-1]
    assert path == "/production/simple-manufacturing" and params["branchID"] == 4 and "additionalInfo" not in params


def test_crash_while_sending_recovers_as_unknown():
    env = Env([row(1, "sending", attempts=1, sent_at=T0)])
    env.worker.start()
    assert env.status("o1") == "unknown"
    env.worker.run_once()
    assert env.esb.posts == []


# ---------------------------------------------------------------- gagal sebelum kirim / ditolak
def test_not_sent_backs_off_then_retries():
    env = Env([row(1)])
    env.esb.post_outcomes = [EsbNotSent("dns"), "ST1"]
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "pending" and r["next_attempt_at"] == T0 + timedelta(seconds=30)
    assert not env.worker.run_once()
    env.advance(31)
    env.worker.run_once()
    assert env.status("o1") == "sent" and len(env.esb.posts) == 2


def test_rejected_fails_and_is_audited():
    env = Env([row(1)])
    env.esb.post_outcomes = [EsbRejected(400, "VAL")]
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "failed" and r["last_http_status"] == 400 and r["last_error_code"] == "esb_rejected"
    assert ("o1", "outbox_failed") in env.store.audits


def test_write_disabled_does_not_send():
    env = Env([row(1)], write=False)
    assert not env.worker.run_once()
    assert env.esb.posts == []


def test_second_row_waits_for_first_confirmed():
    env = Env([row(1), row(2)])
    env.esb.post_outcomes = ["ST1", "ST2"]
    env.worker.run_once()
    assert env.status("o1") == "sent" and env.status("o2") == "pending"
    env.esb.docs["ST1"] = {"additionalInfo": "pp:000000000001"}
    env.worker.run_once()  # confirm o1
    env.worker.run_once()  # baru kirim o2
    assert env.status("o2") == "sent"
    assert [p[1]["seq"] for p in env.esb.posts] == [1, 2]


# ---------------------------------------------------------------- pembatalan
def test_delete_flow_descending():
    env = Env(
        [
            row(1, "delete_pending", esb_doc_number="ST1"),
            row(2, "delete_pending", kind="simple_manufacturing", esb_doc_number=SM_NUM),
        ]
    )
    env.esb.docs = {"ST1": {"additionalInfo": "pp:000000000001"}, SM_NUM: {"additionalInfo": "pp:000000000002"}}
    env.esb.delete_outcomes = ["deleted", "deleted"]
    env.worker.run_once()
    env.worker.run_once()
    assert env.esb.deletes == [("simple_manufacturing", SM_NUM), ("simple_transfer", "ST1")]
    assert env.status("o1") == "deleted" and env.status("o2") == "deleted"


def test_delete_rejected_when_doc_still_exists():
    env = Env([row(1, "delete_pending", esb_doc_number="ST1")])
    env.esb.docs = {"ST1": {"statusID": 3}}
    env.esb.delete_outcomes = [EsbRejected(400)]
    env.worker.run_once()
    assert env.status("o1") == "delete_failed"
    assert ("o1", "outbox_delete_failed") in env.store.audits
    env.advance(3600)
    assert not env.worker.run_once()  # delete_failed tidak diulang otomatis


def test_delete_unknown_then_check_finds_gone():
    env = Env([row(1, "delete_pending", esb_doc_number="ST1")])
    env.esb.docs = {"ST1": {"additionalInfo": "pp:000000000001"}}
    env.esb.delete_outcomes = [EsbSentUnknown("timeout")]
    env.worker.run_once()
    assert env.status("o1") == "deleting"
    env.esb.docs = {}
    env.advance(31)
    env.worker.run_once()
    assert env.status("o1") == "deleted"


def test_delete_refuses_document_with_foreign_marker():
    """ESB memakai ulang nomor dokumen yang dihapus: dokumen bernomor sama tetapi marker lain TIDAK boleh dihapus."""
    env = Env([row(1, "delete_pending", esb_doc_number="ST1")])
    env.esb.docs = {"ST1": {"additionalInfo": "dibuat manual di ESB"}}
    env.esb.delete_outcomes = ["deleted"]
    env.worker.run_once()
    assert env.esb.deletes == []
    assert env.status("o1") == "delete_failed"
    assert env.store.rows["o1"]["last_error_code"] == "marker_mismatch"


def test_delete_when_document_already_gone_marks_deleted_without_delete_call():
    env = Env([row(1, "delete_pending", esb_doc_number="ST1")])
    env.esb.docs = {}
    env.worker.run_once()
    assert env.esb.deletes == []
    assert env.status("o1") == "deleted"
