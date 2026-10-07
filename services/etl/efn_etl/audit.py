"""Salinan audit tamper-evident `efn.audit.log` (+ baris) -> ClickHouse append-only (Gelombang 12, ADR-031, SEC-43).

Logika murni (tanpa I/O): kanonikalisasi, hash berantai, redaksi PII, klasifikasi selisih rekonsiliasi.

Model hash (per company; log tanpa company = rantai 0):
- `line_hash   = sha256(canonical_line)` (nilai ASLI baris, sebelum redaksi salinan);
- `lines_digest = sha256("\\n".join(line_hash urut id))`;
- `canonical_row` = JSON kanonik kolom log + `lines_digest`; `payload_hash = sha256(canonical_row)`;
- `row_hash = sha256(prev_hash || payload_hash)`; rantai pertama memakai `GENESIS`.
  Rantai mengikat `canonical_row` lewat `payload_hash` (setara `sha256(prev_hash || canonical_row)` untuk deteksi
  perubahan) sehingga rantai bisa diverifikasi dari salinan saja tanpa menyimpan nilai pribadi.

Kolom yang TIDAK di-hash karena berubah sah tanpa tamper: `write_date/write_uid` (erasure menulis ulang baris),
`model_id/rule_id/field_id` (FK `ondelete=set null`: uninstall modul / hapus aturan / hapus field), dan kolom related
di baris (`company_id`, `prev_company_id`, `is_personal`).

Redaksi salinan (UU PDP, docs/04 §8.4): ClickHouse copy tidak bisa dihapus oleh ETL (tanpa ALTER DELETE), jadi isi
yang dapat menunjuk orang TIDAK disalin; hanya digest-nya. Lihat `redact_line` / `log_copy_row`.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from efn_etl.watermark import to_utc

GENESIS = "0" * 64
# Disalin dari services/odoo/addons/efn_audit/models/audit_policy.py (harus sinkron; test memeriksa isi).
PERSONAL_MODELS = frozenset({"res.partner", "res.users", "res.partner.bank", "hr.employee"})
PROTECTED_LOG_MODELS = frozenset(
    {"ir.config_parameter", "ir.actions.server", "ir.cron", "ir.module.module", "base.automation", "efn.audit.rule"}
)
MIN_RETENTION_DAYS = 365
ERASURE_NOTE_RE = re.compile(r"^erasure: (\d+) log")
RETENTION_NOTE_RE = re.compile(r"^retensi: (\d+) log")

LOG_HASH_FIELDS = (
    "id",
    "create_date",
    "create_uid",
    "user_id",
    "origin_user_id",
    "is_sudo",
    "operation",
    "model_name",
    "res_id",
    "res_name",
    "company_id",
    "prev_company_id",
    "is_personal",
    "note",
)
LINE_HASH_FIELDS = (
    "id",
    "log_id",
    "create_date",
    "field_name",
    "field_label",
    "old_value",
    "new_value",
    "old_ref",
    "new_ref",
    "is_masked",
)

LOG_COPY_COLUMNS = (
    "chain_company",
    "chain_seq",
    "log_id",
    "create_date",
    "user_id",
    "origin_user_id",
    "is_sudo",
    "operation",
    "model_name",
    "res_id",
    "res_name",
    "company_id",
    "prev_company_id",
    "is_personal",
    "note",
    "line_count",
    "lines_digest",
    "payload_hash",
    "prev_hash",
    "row_hash",
    "ingest_kind",
)
LINE_COPY_COLUMNS = (
    "line_id",
    "log_id",
    "create_date",
    "field_name",
    "field_label",
    "old_value",
    "new_value",
    "old_ref",
    "new_ref",
    "is_masked",
    "redacted",
    "line_hash",
)


def sha256_hex(data: str | bytes) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def _norm(value: Any) -> Any:
    """Nilai kanonik: datetime -> ISO UTC mikrodetik; bool tetap bool; teks kosong/False Odoo -> None."""
    if isinstance(value, datetime):
        return to_utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if value is False or value == "":
        return None
    if isinstance(value, bool) or value is None or isinstance(value, int | str):
        return value
    return str(value)


def _flag(value: Any) -> bool:
    return bool(value)


def canonical(row: Mapping[str, Any], fields: Sequence[str], flags: Iterable[str] = ()) -> str:
    """JSON kanonik (urutan kunci tetap, tanpa spasi, UTF-8 apa adanya). Kolom flag dinormalkan ke bool."""
    flag_set = set(flags)
    data = {name: (_flag(row.get(name)) if name in flag_set else _norm(row.get(name))) for name in fields}
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def line_hash(line: Mapping[str, Any]) -> str:
    return sha256_hex(canonical(line, LINE_HASH_FIELDS, flags=("is_masked",)))


def lines_digest(hashes: Sequence[str]) -> str:
    return sha256_hex("\n".join(hashes))


def canonical_row(log: Mapping[str, Any], digest: str) -> str:
    base = json.loads(canonical(log, LOG_HASH_FIELDS, flags=("is_sudo", "is_personal")))
    base["lines_digest"] = digest
    return json.dumps(base, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_hash(log: Mapping[str, Any], lines: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """(lines_digest, payload_hash) dari baris SUMBER (nilai asli)."""
    ordered = sorted(lines, key=lambda ln: int(ln["id"]))
    digest = lines_digest([line_hash(ln) for ln in ordered])
    return digest, sha256_hex(canonical_row(log, digest))


def chain_hash(prev_hash: str, payload: str) -> str:
    return sha256_hex(prev_hash + payload)


def chain_key(log: Mapping[str, Any]) -> int:
    return int(log.get("company_id") or 0)


def _ref_model(ref: Any) -> str:
    return str(ref).split(",", 1)[0] if ref else ""


def is_line_sensitive(line: Mapping[str, Any], log_personal: bool) -> bool:
    """Baris yang isinya tidak boleh disalin: log orang/ber-mask, atau ref ke model orang (nilai teks M2O berisi
    display_name orang; erasure Odoo mengosongkannya, salinan append-only tidak bisa)."""
    return (
        log_personal
        or bool(line.get("is_masked"))
        or _ref_model(line.get("old_ref")) in PERSONAL_MODELS
        or _ref_model(line.get("new_ref")) in PERSONAL_MODELS
    )


def is_log_personal(log: Mapping[str, Any]) -> bool:
    return bool(log.get("is_personal")) or str(log.get("model_name") or "") in PERSONAL_MODELS


def _opt_int(value: Any) -> int | None:
    return int(value) if value not in (None, False) else None


def line_copy_row(line: Mapping[str, Any], log_personal: bool) -> list[Any]:
    sensitive = is_line_sensitive(line, log_personal)

    def keep(name: str) -> str | None:
        value = _norm(line.get(name))
        return None if sensitive or value is None else str(value)

    return [
        int(line["id"]),
        int(line["log_id"]),
        to_utc(line["create_date"]),
        str(line.get("field_name") or ""),
        str(line.get("field_label") or ""),
        keep("old_value"),
        keep("new_value"),
        keep("old_ref"),
        keep("new_ref"),
        1 if line.get("is_masked") else 0,
        1 if sensitive else 0,
        line_hash(line),
    ]


@dataclass
class ChainHead:
    seq: int = 0
    row_hash: str = GENESIS


@dataclass
class CopyBatch:
    log_rows: list[list[Any]] = field(default_factory=list)
    line_rows: list[list[Any]] = field(default_factory=list)
    line_log_ids: set[int] = field(default_factory=set)


def build_copy(
    logs: Sequence[Mapping[str, Any]],
    lines_by_log: Mapping[int, Sequence[Mapping[str, Any]]],
    heads: dict[int, ChainHead],
    ingest_kind: str,
) -> CopyBatch:
    """Bangun baris salinan untuk `logs` (urut id). `heads` dimutasi (kepala rantai per company maju)."""
    batch = CopyBatch()
    for log in sorted(logs, key=lambda r: int(r["id"])):
        log_id = int(log["id"])
        lines = list(lines_by_log.get(log_id, ()))
        digest, payload = payload_hash(log, lines)
        key = chain_key(log)
        head = heads.setdefault(key, ChainHead())
        row_hash = chain_hash(head.row_hash, payload)
        personal = is_log_personal(log)
        batch.log_rows.append(
            [
                key,
                head.seq + 1,
                log_id,
                to_utc(log["create_date"]),
                int(log["user_id"]),
                _opt_int(log.get("origin_user_id")),
                1 if log.get("is_sudo") else 0,
                str(log["operation"]),
                str(log["model_name"]),
                None if personal else _opt_int(log.get("res_id")),
                None if personal else (_norm(log.get("res_name")) or None),
                _opt_int(log.get("company_id")),
                _opt_int(log.get("prev_company_id")),
                1 if log.get("is_personal") else 0,
                str(log.get("note") or ""),
                len(lines),
                digest,
                payload,
                head.row_hash,
                row_hash,
                ingest_kind,
            ]
        )
        for line in lines:
            batch.line_rows.append(line_copy_row(line, personal))
        if lines:
            batch.line_log_ids.add(log_id)
        heads[key] = ChainHead(seq=head.seq + 1, row_hash=row_hash)
    return batch


# ------------------------------------------------------------------ verifikasi rantai (dari salinan saja)


@dataclass(frozen=True)
class ChainRow:
    chain_company: int
    chain_seq: int
    log_id: int
    payload_hash: str
    prev_hash: str
    row_hash: str


def verify_chain(rows: Sequence[ChainRow], start: ChainHead | None = None) -> tuple[list[str], ChainHead]:
    """Verifikasi satu rantai (urut seq). `start` = kepala dari halaman sebelumnya (None = awal rantai; baris
    pertama yang tersisa dipercaya sebagai jangkar bila seq > 1, mis. setelah TTL). Mengembalikan (masalah, kepala)."""
    problems: list[str] = []
    head = start
    for row in rows:
        if head is not None:
            if row.chain_seq != head.seq + 1:
                problems.append(f"seq_gap company={row.chain_company} after={head.seq} got={row.chain_seq}")
            if row.prev_hash != head.row_hash:
                problems.append(f"prev_mismatch company={row.chain_company} seq={row.chain_seq}")
        elif row.chain_seq == 1 and row.prev_hash != GENESIS:
            problems.append(f"bad_genesis company={row.chain_company}")
        if chain_hash(row.prev_hash, row.payload_hash) != row.row_hash:
            problems.append(f"row_hash_mismatch company={row.chain_company} seq={row.chain_seq} log={row.log_id}")
        head = ChainHead(seq=row.chain_seq, row_hash=row.row_hash)
    return problems, head or start or ChainHead()


# ------------------------------------------------------------------ rekonsiliasi PG vs salinan


@dataclass(frozen=True)
class CopiedLog:
    log_id: int
    create_date: datetime
    model_name: str
    operation: str
    payload_hash: str


@dataclass(frozen=True)
class PurgeLog:
    model_name: str
    note: str
    create_date: datetime


@dataclass
class ReconResult:
    """Hitungan per kelas. `unexplained`, `unseen`, `modified`, `chain_problems` = alert."""

    checked: int = 0
    retention: int = 0
    erasure: int = 0
    unexplained: int = 0
    unseen: int = 0
    modified: int = 0
    erasure_redacted: int = 0
    chain_problems: int = 0
    sample_ids: list[int] = field(default_factory=list)

    def note(self, log_id: int) -> None:
        if len(self.sample_ids) < 50:
            self.sample_ids.append(log_id)

    @property
    def alert(self) -> bool:
        return bool(self.unexplained or self.unseen or self.modified or self.chain_problems)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "retention": self.retention,
            "erasure": self.erasure,
            "unexplained": self.unexplained,
            "unseen": self.unseen,
            "modified": self.modified,
            "erasure_redacted": self.erasure_redacted,
            "chain_problems": self.chain_problems,
            "sample_ids": list(self.sample_ids),
        }


@dataclass
class PurgeBudget:
    """Anggaran penghapusan sah dari log `purge` (log purge tidak pernah dihapus GC, SEC-43)."""

    retention: int = 0
    erasure_by_model: dict[str, int] = field(default_factory=dict)
    erasure_after: dict[str, datetime] = field(default_factory=dict)  # erasure TERAKHIR per model

    @classmethod
    def from_purges(cls, purges: Iterable[PurgeLog]) -> "PurgeBudget":
        budget = cls()
        for p in purges:
            m = RETENTION_NOTE_RE.match(p.note or "")
            if m:
                budget.retention += int(m.group(1))
                continue
            m = ERASURE_NOTE_RE.match(p.note or "")
            if m:
                budget.erasure_by_model[p.model_name] = budget.erasure_by_model.get(p.model_name, 0) + int(m.group(1))
                ts = to_utc(p.create_date)
                if p.model_name not in budget.erasure_after or ts > budget.erasure_after[p.model_name]:
                    budget.erasure_after[p.model_name] = ts
        return budget


def classify_missing(
    missing: Iterable[CopiedLog], budget: PurgeBudget, now: datetime, result: ReconResult
) -> ReconResult:
    """Log yang ADA di salinan tetapi HILANG di PostgreSQL.

    - log `purge` / model kontrol: tidak pernah dihapus sah -> `unexplained`;
    - umur >= MIN_RETENTION_DAYS: kandidat GC retensi (dibatasi total angka di catatan `retensi: N log`);
    - model orang + ada log purge `erasure:` model itu SETELAH log dibuat: erasure UU PDP (dibatasi jumlah per model);
    - sisanya `unexplained` (penghapusan di luar GC/erasure: SQL langsung, server action, dsb.).
    Anggaran adalah total kumulatif sejak awal; pemanggil mengirim SEMUA log hilang dalam satu rekonsiliasi.
    """
    retention_left = budget.retention
    erasure_left = dict(budget.erasure_by_model)
    cutoff = to_utc(now) - timedelta(days=MIN_RETENTION_DAYS)
    for log in sorted(missing, key=lambda r: r.log_id):
        created = to_utc(log.create_date)
        protected = log.operation == "purge" or log.model_name in PROTECTED_LOG_MODELS
        if not protected and created <= cutoff and retention_left > 0:
            retention_left -= 1
            result.retention += 1
            continue
        erased_at = budget.erasure_after.get(log.model_name)
        if (
            not protected
            and log.model_name in PERSONAL_MODELS
            and erased_at is not None
            and erased_at >= created
            and erasure_left.get(log.model_name, 0) > 0
        ):
            erasure_left[log.model_name] -= 1
            result.erasure += 1
            continue
        result.unexplained += 1
        result.note(log.log_id)
    return result


def lines_cleared_by_erasure(lines: Sequence[Mapping[str, Any]]) -> bool:
    """Erasure (`_efn_audit_purge_records`) mengosongkan old/new value+ref baris yang menunjuk subjek: SEMUA baris
    yang berubah harus kosong total."""
    return all(not any(_norm(ln.get(k)) for k in ("old_value", "new_value", "old_ref", "new_ref")) for ln in lines)


def compare_content(
    log: Mapping[str, Any],
    lines: Sequence[Mapping[str, Any]],
    copied: CopiedLog,
    copied_line_hashes: Mapping[int, str],
    budget: PurgeBudget,
    result: ReconResult,
) -> None:
    """Bandingkan isi sumber sekarang dengan hash saat disalin. Perubahan hanya pada baris yang kini kosong total
    (pola erasure) setelah ada log purge erasure = `erasure_redacted` (sah); lainnya `modified`."""
    _digest, payload = payload_hash(log, lines)
    if payload == copied.payload_hash:
        return
    changed = [ln for ln in lines if copied_line_hashes.get(int(ln["id"])) != line_hash(ln)]
    current_ids = {int(ln["id"]) for ln in lines}
    lines_removed = any(lid not in current_ids for lid in copied_line_hashes)
    lines_added = any(int(ln["id"]) not in copied_line_hashes for ln in lines)
    # Header log sama bila payload dengan digest baris versi salinan cocok.
    copied_digest = lines_digest([copied_line_hashes[k] for k in sorted(copied_line_hashes)])
    header_same = sha256_hex(canonical_row(log, copied_digest)) == copied.payload_hash
    if (
        header_same
        and changed
        and not lines_removed
        and not lines_added
        and budget.erasure_after
        and lines_cleared_by_erasure(changed)
    ):
        result.erasure_redacted += 1
        return
    result.modified += 1
    result.note(int(log["id"]))
