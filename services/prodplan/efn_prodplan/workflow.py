"""Aturan alur rencana, peran, SoD, dan gerbang posting (docs/04 §30.7–§30.8). Logika murni.

Galat API = `ApiError(status, code, message, details)`; pesan bahasa Indonesia singkat, tanpa detail internal.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from efn_prodplan.actor import Actor

PLANNER = "pp_planner"
WAREHOUSE = "pp_warehouse"
ANY_ROLE = frozenset({PLANNER, WAREHOUSE})
WIB = timezone(timedelta(hours=7))
PLAN_STATUSES = ("draft", "submitted", "approved", "posted", "cancelled")
# Transisi status rencana yang sah (aksi -> (status asal, status tujuan)).
TRANSITIONS: Mapping[str, tuple[frozenset[str], str]] = {
    "submit": (frozenset({"draft"}), "submitted"),
    "approve": (frozenset({"submitted"}), "approved"),
    "return_to_draft": (frozenset({"submitted"}), "draft"),
    "post_production": (frozenset({"approved"}), "posted"),
    "cancel": (frozenset({"draft", "submitted", "approved", "posted"}), "cancelled"),
}
LIMITATIONS = (
    "L-253: stok WIP tidak di-netting; baris produksi WIP = total kebutuhan",
    "L-254: transfer berjalan (pendingQty) tidak dikurangi dari stok",
    "L-255: urutan sumber transfer = stok terbesar, bukan FEFO (ESB tanpa lot kedaluwarsa)",
    "L-256: goods delivery dari goods-transfer-request tidak dicek sebelum posting produksi",
    "L-257: satu dokumen produksi ESB per baris produksi",
)

MESSAGES: Mapping[str, str] = {
    "invalid_request": "Permintaan tidak valid",
    "actor_invalid": "Identitas pengguna tidak valid",
    "forbidden": "Tidak berwenang",
    "not_found": "Data tidak ditemukan",
    "conflict_version": "Data sudah diubah pengguna lain; muat ulang",
    "invalid_state": "Aksi tidak sesuai status rencana",
    "rate_limited": "Terlalu banyak permintaan; coba lagi sebentar",
    "esb_unavailable": "ESB tidak dapat dihubungi",
    "disabled": "Fitur perencanaan produksi tidak aktif",
    "internal": "Terjadi kesalahan internal",
    "invalid_branch": "Cabang atau lokasi produksi tidak diizinkan",
    "bom_not_assembly": "Produk tidak memiliki BOM Assembly",
    "bom_cycle": "BOM membentuk siklus",
    "bom_too_deep": "BOM terlalu dalam (maksimum 10 level)",
    "job_running": "Masih ada proses yang berjalan untuk rencana ini",
    "stale_calculation": "Perhitungan sudah usang; hitung ulang",
    "stock_too_old": "Data stok terlalu lama; hitung ulang",
    "sod_violation": "Penyetuju tidak boleh pembuat, pengaju, atau pengubah isi rencana",
    "transfers_undecided": "Masih ada usulan transfer yang belum diputuskan",
    "write_disabled": "Posting ke ESB tidak aktif",
    "digest_mismatch": "Rencana berubah sejak pratinjau; tinjau ulang",
    "transfers_not_authorized": "Transfer belum di-authorize di ESB",
    "shortage_open": "Masih ada kekurangan bahan",
    "too_many_docs": "Jumlah dokumen ESB melebihi batas",
    "date_out_of_window": "Tanggal produksi di luar jendela posting",
    "method_not_allowed": "Method tidak diizinkan",
    "payload_too_large": "Body terlalu besar",
    "empty_plan": "Rencana belum memiliki baris produksi",
    "bad_input": "Input pencarian tidak valid",
    "invalid_quantity": "Jumlah pada dokumen ESB tidak valid (harus lebih dari nol)",
    "reconcile_incomplete": "Pemeriksaan dokumen di ESB belum tuntas; coba lagi nanti",
    # §30.17.5 / §30.17.13 (pesan login tetap; tidak membedakan "user tidak ada" vs "password salah")
    "invalid_credentials": "Username atau password ESB salah.",
    "login_locked": "Terlalu banyak percobaan masuk. Coba lagi dalam 15 menit.",
    "no_pp_access": "Akun ESB ini tidak memiliki izin modul Produksi.",
    "esb_company_not_allowed": "Akun ESB ini tidak memiliki izin modul Produksi.",
    "session_capacity": "ESB sedang tidak dapat dihubungi. Coba beberapa saat lagi.",
    "session_expired": "Sesi ESB berakhir (maksimal 1 jam). Silakan masuk lagi.",
    "branch_forbidden": "Akun ESB Anda tidak memegang cabang dokumen ini",
    "esb_access_missing": "Akun ESB Anda tidak memiliki izin dokumen ini di ESB",
    "resume_requires_original_user": "Hanya pengguna yang memulai posting yang boleh melanjutkan dokumen ini",
    "nothing_to_resume": "Tidak ada posting yang menunggu dilanjutkan",
}

# Semua kode galat job (`job.error_code`) yang bisa ditulis worker/store — satu tempat (frontend memberi label).
JOB_ERROR_CODES = frozenset(
    {
        "esb_unavailable",
        "esb_auth_failed",
        "call_budget_exceeded",
        "bom_cycle",  # result.path = jalur productCode
        "bom_too_deep",  # result.path
        "bom_missing",
        "bom_not_assembly",
        "parse_error",
        "conflict_version",
        "invalid_state",
        "not_found",
        "forbidden",
        "interrupted",  # store.recover_running saat restart
        "needs_login",  # §30.17.9: tanpa token sesi hidup milik pemicu / token ditolak di tengah job
        "internal",
    }
)

# Aksi audit yang MENGUBAH ISI rencana (SEC-227): pelakunya tidak boleh menyetujui rencana/transfer itu.
CONTENT_ACTIONS = (
    "plan_created",
    "demand_saved",
    "suggestion_accept",
    "suggestion_reject",
    "job_calculate",
    "job_pr_import",
    "plan_submitted",
    "plan_returned",
)

# posting_state (ringkasan outbox untuk UI, QA-B).
_ACTIVE_OUTBOX = frozenset({"pending", "sending", "sent", "unknown", "delete_pending", "deleting"})
_ATTENTION_ERRORS = frozenset({"doc_not_found", "marker_mismatch", "reconcile_incomplete"})
NEEDS_LOGIN = "needs_login"
# Baris outbox yang belum terminal (§30.17.9 resume / cancel).
NONTERMINAL_OUTBOX = frozenset(_ACTIVE_OUTBOX | {NEEDS_LOGIN})


class ApiError(Exception):
    def __init__(self, status: int, code: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.details = dict(details) if details else None

    def body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": MESSAGES.get(self.code, MESSAGES["internal"])}
        if self.details:
            err["details"] = self.details
        return {"error": err}


def require_role(actor: Actor, allowed: Iterable[str]) -> None:
    if not actor.roles & frozenset(allowed):
        raise ApiError(403, "forbidden")


def require_brand(actor: Actor, brand: str, active_brands: Iterable[str]) -> None:
    """§30.17.7: brand ∈ PRODPLAN_BRANDS dan = brand sesi ESB pemanggil."""
    if brand not in set(active_brands) or brand != actor.brand:
        raise ApiError(403, "forbidden")


def require_branches(actor: Actor, branch_ids: Iterable[Any]) -> None:
    """§30.17.6: pemanggil wajib memegang semua cabang (`/branch/list/user` saat login) -> 403 `branch_forbidden`."""
    if not {int(b) for b in branch_ids} <= actor.branch_ids:
        raise ApiError(403, "branch_forbidden")


def require_doc_access(actor: Actor, insert_kinds: Iterable[str] = (), delete_kinds: Iterable[str] = ()) -> None:
    """§30.17.6: `doc_insert`/`doc_delete` sesi wajib mencakup doc_kind -> 403 `esb_access_missing` (+ doc_kinds)."""
    missing = (set(insert_kinds) - actor.doc_insert) | (set(delete_kinds) - actor.doc_delete)
    if missing:
        raise ApiError(403, "esb_access_missing", {"doc_kinds": sorted(missing)})


def run_as(row: Mapping[str, Any]) -> int:
    """Aktor yang tokennya dipakai untuk baris outbox: `coalesce(run_as_sub, created_by_sub)`."""
    return int(row.get("run_as_sub") or row.get("created_by_sub") or 0)


def _waiting_login(row: Mapping[str, Any]) -> bool:
    return row.get("status") == NEEDS_LOGIN or row.get("last_error_code") == NEEDS_LOGIN


def _maybe_created(row: Mapping[str, Any]) -> bool:
    """Baris yang MUNGKIN sudah tercipta di ESB oleh aktor sebelumnya (§30.17.9 resume langkah 2)."""
    status = row.get("status")
    return status in ("unknown", "sent") or (
        status == NEEDS_LOGIN and int(row.get("attempts") or 0) > 0 and not row.get("esb_doc_number")
    )


def resume_status(row: Mapping[str, Any]) -> str:
    """Status baru saat dilanjutkan: `needs_login` -> pending (belum pernah dikirim) / unknown (pernah dikirim, tanpa
    nomor: rekonsiliasi marker dulu, TIDAK langsung POST) / delete_pending (nomor ada). Status lain tetap."""
    status = str(row.get("status"))
    if status != NEEDS_LOGIN:
        return status
    if row.get("esb_doc_number"):
        return "delete_pending"
    return "unknown" if int(row.get("attempts") or 0) > 0 else "pending"


def plan_resume(
    rows: Iterable[Mapping[str, Any]], actor: Actor, row_branches: Mapping[Any, Iterable[int]]
) -> list[tuple[Any, str]]:
    """`POST /v1/plans/{id}/resume-posting` langkah 1-3 (murni). Kembalikan [(outbox_id, status_baru)] semua baris
    non-terminal (langkah 4: semuanya `run_as_sub` = pemanggil). `row_branches[id]` = cabang asal/tujuan baris."""
    live = [r for r in rows if r.get("status") in NONTERMINAL_OUTBOX]
    if not any(_waiting_login(r) for r in live):
        raise ApiError(409, "nothing_to_resume")
    if any(_maybe_created(r) and run_as(r) != actor.sub for r in live):
        raise ApiError(403, "resume_requires_original_user")
    branches = {b for r in live for b in row_branches.get(r["id"], ())}
    require_branches(actor, branches)
    targets = [(r, resume_status(r)) for r in live]
    insert_kinds = {str(r["doc_kind"]) for r, s in targets if s == "pending"}
    delete_kinds = {str(r["doc_kind"]) for r, s in targets if s in ("delete_pending", "deleting")}
    require_doc_access(actor, insert_kinds, delete_kinds)
    return [(r["id"], s) for r, s in targets]


def require_status(status: str, allowed: Iterable[str]) -> None:
    if status not in set(allowed):
        raise ApiError(409, "invalid_state")


def check_version(current: int, given: Any) -> None:
    if not isinstance(given, int) or isinstance(given, bool) or given != current:
        raise ApiError(409, "conflict_version", {"row_version": current})


def transition(action: str, status: str) -> str:
    sources, target = TRANSITIONS[action]
    require_status(status, sources)
    return target


def check_sod(
    actor_sub: int,
    created_by_sub: int | None,
    submitted_by_sub: int | None,
    contributors: Iterable[int | None] = (),
) -> None:
    """Penyetuju rencana/transfer ≠ pembuat ≠ pengaju ≠ siapa pun yang pernah mengubah isi rencana (SEC-227:
    `contributors` = aktor audit `CONTENT_ACTIONS`). CHECK DB di tabel plan hanya menjaga dua kolom pertama."""
    if actor_sub in {created_by_sub, submitted_by_sub} or actor_sub in set(contributors):
        raise ApiError(403, "sod_violation")


def posting_state(rows: Iterable[Mapping[str, Any]]) -> str:
    """Ringkasan outbox rencana: none | in_progress | needs_attention | complete (`failed/cancelled` diabaikan)."""
    active = [r for r in rows if not (r.get("status") == "failed" and r.get("last_error_code") == "cancelled")]
    if not active:
        return "none"
    if any(_waiting_login(r) for r in active):
        return "needs_attention"  # §30.17.9: "Posting berhenti karena sesi ESB berakhir" + Lanjutkan posting
    if any(r.get("status") in ("failed", "delete_failed") for r in active) or any(
        r.get("status") in ("sent", "unknown") and r.get("last_error_code") in _ATTENTION_ERRORS for r in active
    ):
        return "needs_attention"
    if any(r.get("status") in _ACTIVE_OUTBOX for r in active):
        return "in_progress"
    return "complete"


def wib_today(now: datetime) -> date:
    return now.astimezone(WIB).date()


def check_date_window(production_date: date, now: datetime, window: tuple[int, int]) -> None:
    today = wib_today(now)
    if not today + timedelta(days=window[0]) <= production_date <= today + timedelta(days=window[1]):
        raise ApiError(409, "date_out_of_window")


def check_stock_age(fetched_at: datetime | None, now: datetime, max_minutes: int) -> None:
    if fetched_at is None or now - fetched_at > timedelta(minutes=max_minutes):
        raise ApiError(409, "stock_too_old")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def preview_digest(docs: Any) -> str:
    """§30.8: hex SHA-256 dari JSON kanonik `docs` pratinjau."""
    return hashlib.sha256(canonical_json(docs).encode("utf-8")).hexdigest()


def plan_no(company_code: str, production_date: date, seq: int) -> str:
    return f"PP-{company_code}-{production_date.strftime('%Y%m%d')}-{seq:03d}"


def now_utc() -> datetime:
    return datetime.now(UTC)
