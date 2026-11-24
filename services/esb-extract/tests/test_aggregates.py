"""Tabel agregat Insight (efn_esb/aggregates.py): rentang bulan, urutan refresh per bulan (cek kunci ->
TRUNCATE build -> INSERT -> pagar penyusutan -> cek kunci -> REPLACE PARTITION), kunci heartbeat (balapan, proses mati,
kunci hilang), kegagalan tidak bocor ke ekstraksi, CLI. ClickHouse palsu (tanpa service)."""

import argparse
import logging
import re
import threading
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

import efn_esb.__main__ as cli
from efn_esb import aggregates
from efn_esb.aggregates import (
    AGGREGATES,
    INSERT_SETTINGS,
    LOCK_TABLE,
    REFRESH_COLUMNS,
    REFRESH_TABLE,
    TRIGGER_ENTITIES,
    RefreshLock,
    drop_partition_sql,
    error_label,
    holder_sql,
    insert_month_sql,
    is_shrink,
    month_ranges,
    partitions_sql,
    refresh_all,
    replace_partition_sql,
)
from efn_esb.config import BrandConfig, EsbSettings
from efn_esb.extract import SliceResult
from efn_esb.planning import SliceTask, wib_today
from efn_esb.records import RunRecord
from efn_esb.runner import RefreshTrigger, refresh_aggregates

T0 = datetime(2026, 11, 22, 1, 0, 0, tzinfo=UTC)
SALES_BRANCH = AGGREGATES[0]
A = SALES_BRANCH.name
B = SALES_BRANCH.build
RUN = uuid.UUID("00000000-0000-4000-8000-000000000001")
OTHER = uuid.UUID("00000000-0000-4000-8000-000000000002")


@pytest.fixture(autouse=True)
def no_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aggregates, "LOCK_SETTLE_SECONDS", 0.0)


class FakeCh:
    """ClickHouse palsu. `events` = urutan operasi ringkas ("holder", "beat", "release", "TRUNCATE <t>",
    "INSERT <t> <yyyymm>", "REPLACE <t> <yyyymm>", "DROP <t> <yyyymm>"). Kunci: baris (run_id, urutan, released);
    pemegang = run belum dilepas & tidak basi dengan baris pertama paling awal (= holder_sql)."""

    def __init__(
        self,
        ranges: dict[tuple[str, ...], tuple[date, date, int]] | None = None,
        agg_counts: dict[str, int] | None = None,
        build_counts: dict[str, int] | None = None,
        partitions: Sequence[str] = (),
        fail_on: str = "",
        fail_record: bool = False,
        fail_lock: bool = False,
        on_event: Callable[["FakeCh", str], None] | None = None,
    ) -> None:
        self.ranges = ranges or {}
        self.agg_counts = agg_counts or {}
        self.build_counts = build_counts or {}
        self.partitions = list(partitions)
        self.fail_on = fail_on
        self.fail_record = fail_record
        self.fail_lock = fail_lock
        self.on_event = on_event
        self.events: list[str] = []
        self.commands: list[tuple[str, dict[str, Any] | None, dict[str, Any] | None]] = []
        self.records: list[list[Any]] = []
        self.lock_rows: list[tuple[uuid.UUID, int, int]] = []
        self.stale: set[uuid.UUID] = set()
        self._seq = 0
        self._month = ""

    @property
    def mart_db(self) -> str:
        return "efn_dwh"

    @property
    def raw_db(self) -> str:
        return "efn_esb"

    def holder(self) -> uuid.UUID | None:
        runs: dict[uuid.UUID, list[int]] = {}
        for run_id, seq, released in self.lock_rows:
            first_rel = runs.setdefault(run_id, [seq, 0])
            first_rel[1] = max(first_rel[1], released)
        live = sorted((s, str(r), r) for r, (s, rel) in runs.items() if not rel and r not in self.stale)
        return live[0][2] if live else None

    def add_lock_row(self, run_id: uuid.UUID, released: int = 0) -> None:
        self._seq += 1
        self.lock_rows.append((run_id, self._seq, released))

    def _check(self, sql: str) -> None:
        if self.fail_on and self.fail_on in sql:
            # Pesan galat ClickHouse bisa memuat potongan query/nilai -> tidak boleh dicatat.
            raise RuntimeError("Code: 241. DB::Exception: Memory limit exceeded 62812345678 rahasia")

    def _event(self, event: str) -> None:
        self.events.append(event)
        if self.on_event is not None:
            self.on_event(self, event)

    def command(
        self, sql: str, parameters: dict[str, Any] | None = None, settings: dict[str, Any] | None = None
    ) -> None:
        self._check(sql)
        self.commands.append((sql, parameters, settings))
        match = re.search(r"efn_dwh\.(\w+)", sql)
        assert match, sql
        table = match.group(1)
        word = sql.split()[0]
        if word == "INSERT":
            assert parameters is not None
            self._month = parameters["start"].strftime("%Y%m")
            self._event(f"INSERT {table} {self._month}")
        elif word == "TRUNCATE":
            self._event(f"TRUNCATE {table}")
        elif word == "ALTER":
            part = re.search(r"(REPLACE|DROP) PARTITION ID '(\d{6})'", sql)
            assert part, sql
            self._event(f"{part.group(1)} {table} {part.group(2)}")
        else:
            raise AssertionError(f"perintah tak terduga: {sql}")

    def query_rows(self, sql: str, parameters: dict[str, Any] | None = None) -> list[Sequence[Any]]:
        self._check(sql)
        if LOCK_TABLE in sql:
            self.events.append("holder")
            holder = self.holder()
            return [(holder,)] if holder else []
        if "_partition_id" in sql:
            assert sql in {partitions_sql(agg, "efn_dwh") for agg in AGGREGATES}, sql
            return [(p,) for p in self.partitions]
        assert parameters is not None or sql.startswith("SELECT count() FROM")
        if "extract_checkpoint" in sql:
            assert parameters is not None
            return [self.ranges.get(tuple(parameters["entities"]), (date(1970, 1, 1), date(1970, 1, 1), 0))]
        match = re.match(r"SELECT count\(\) FROM efn_dwh\.(\w+)", sql)
        assert match, sql
        if match.group(1).endswith("_build"):
            return [(self.build_counts.get(self._month, 1234),)]
        if "WHERE" in sql:
            assert parameters is not None
            return [(self.agg_counts.get(parameters["start"].strftime("%Y%m"), 0),)]
        return [(9999,)]

    def insert_control(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        if table == LOCK_TABLE:
            if self.fail_lock:
                raise RuntimeError("Code: 497. ACCESS_DENIED")
            assert tuple(columns) in (("run_id",), ("run_id", "released"))
            for row in rows:
                self.add_lock_row(row[0], row[1] if len(row) > 1 else 0)
            self.events.append("release" if len(columns) > 1 else "beat")
            return
        if self.fail_record:
            raise RuntimeError("Code: 497. ACCESS_DENIED")
        assert table == REFRESH_TABLE and tuple(columns) == REFRESH_COLUMNS
        assert all(len(row) == len(columns) for row in rows)
        self.records.extend(rows)

    def table_events(self, *tables: str) -> list[str]:
        return [e for e in self.events if len(e.split()) > 1 and e.split()[1] in tables]


def clock() -> datetime:
    return T0


def lock_for(ch: FakeCh, run_id: uuid.UUID = RUN) -> RefreshLock:
    return RefreshLock(ch, run_id=run_id, sleep=lambda _s: None, settle=0)


def month(table: str, partition: str) -> list[str]:
    """Urutan wajib satu bulan yang diganti."""
    return [
        "holder",
        f"TRUNCATE {table}_build",
        f"INSERT {table}_build {partition}",
        "holder",
        f"REPLACE {table} {partition}",
        "beat",
    ]


# ------------------------------------------------------------------------------------------------ murni
def test_month_ranges_full_calendar_months() -> None:
    assert month_ranges(date(2025, 11, 17), date(2026, 1, 3)) == [
        (date(2025, 11, 1), date(2025, 11, 30)),
        (date(2025, 12, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 31)),
    ]
    assert month_ranges(date(2026, 2, 10), date(2026, 2, 10)) == [(date(2026, 2, 1), date(2026, 2, 28))]
    # 369 hari data PB (17 Nov 2025 .. 20 Nov 2026) = 13 bulan kalender -> 13 REPLACE PARTITION per agregat.
    assert len(month_ranges(date(2025, 11, 17), date(2026, 11, 20))) == 13


def test_month_ranges_rejects_inverted_range() -> None:
    with pytest.raises(ValueError):
        month_ranges(date(2026, 2, 1), date(2026, 1, 31))


def test_sql_shapes() -> None:
    sql = insert_month_sql(SALES_BRANCH, "efn_dwh")
    assert sql.startswith(f"INSERT INTO efn_dwh.{B} (sales_date, brand, company_code,")
    assert "FROM efn_dwh.v_esb_sales_branch_daily\n" in sql
    assert sql.endswith("WHERE sales_date >= {start:Date} AND sales_date <= {end:Date}")
    assert "*" not in sql  # kolom eksplisit, tidak pernah SELECT * tanpa filter
    assert insert_month_sql(AGGREGATES[2], "efn_dwh").endswith("WHERE day >= {start:Date} AND day <= {end:Date}")
    assert replace_partition_sql(SALES_BRANCH, "efn_dwh", "202611") == (
        f"ALTER TABLE efn_dwh.{A} REPLACE PARTITION ID '202611' FROM efn_dwh.{B}"
    )
    drop = drop_partition_sql(SALES_BRANCH, "efn_dwh", "202401")
    assert drop == f"ALTER TABLE efn_dwh.{A} DROP PARTITION ID '202401'"
    # Partisi lewat kolom virtual (cukup SELECT pada agg); esb_writer tanpa hak system.* (ACCESS_DENIED di 24.8.14).
    parts = partitions_sql(SALES_BRANCH, "efn_dwh")
    assert parts == f"SELECT DISTINCT _partition_id FROM efn_dwh.{A} ORDER BY _partition_id"
    # Kunci: jam server, belum dilepas, urut (mulai, run_id), ambang basi lewat parameter.
    lock = holder_sql("efn_esb")
    assert "FROM efn_esb.aggregate_refresh_lock GROUP BY run_id" in lock
    assert "now64(3) - toIntervalSecond({stale:UInt32})" in lock and "ORDER BY s, run_id LIMIT 1" in lock
    # SQL refresh tidak pernah DDL pembuat/penghapus tabel (SEC-AG-01).
    for agg in AGGREGATES:
        for text in (insert_month_sql(agg, "efn_dwh"), replace_partition_sql(agg, "efn_dwh", "202601")):
            assert not re.search(r"\b(CREATE|DROP TABLE|EXCHANGE|RENAME)\b", text)


@pytest.mark.parametrize("bad", ["2026-11", "20261", "202611'; DROP TABLE x --", "abcdef", "2026111"])
def test_partition_id_validated(bad: str) -> None:
    with pytest.raises(ValueError):
        replace_partition_sql(SALES_BRANCH, "efn_dwh", bad)
    with pytest.raises(ValueError):
        drop_partition_sql(SALES_BRANCH, "efn_dwh", bad)


def test_is_shrink() -> None:
    assert is_shrink(0, 10) and is_shrink(4, 10)
    assert not is_shrink(5, 10) and not is_shrink(12, 10)
    assert not is_shrink(0, 0) and not is_shrink(10, 0)  # agg kosong (refresh pertama) bukan penyusutan


def test_error_label_drops_message() -> None:
    exc = RuntimeError("Code: 159. Timeout ... query: SELECT ... 62812345678")
    assert error_label(exc) == "RuntimeError code=159"
    assert error_label(ValueError("nomor 62812345678")) == "ValueError"


def test_trigger_entities() -> None:
    assert TRIGGER_ENTITIES == {"sales", "production", "delivery", "transfer", "fin_sold", "master"}
    assert "branch" not in TRIGGER_ENTITIES and "payment" not in TRIGGER_ENTITIES


# ------------------------------------------------------------------------------------------------ refresh per bulan
def test_refresh_order_per_month_replace_partition() -> None:
    ch = FakeCh(ranges={("sales",): (date(2025, 11, 17), date(2026, 1, 3), 900)})
    results = refresh_all(ch, [SALES_BRANCH], clock=clock, lock=lock_for(ch))
    assert [r.status for r in results] == ["ok"]
    assert ch.events == [
        "beat",
        "holder",
        *month(A, "202511"),
        *month(A, "202512"),
        *month(A, "202601"),
        "holder",
        f"TRUNCATE {B}",  # build kosong di luar refresh
        "release",
    ]
    inserts = [(p, s) for sql, p, s in ch.commands if sql.startswith("INSERT")]
    assert [p for p, _ in inserts] == [
        {"start": date(2025, 11, 1), "end": date(2025, 11, 30)},
        {"start": date(2025, 12, 1), "end": date(2025, 12, 31)},
        {"start": date(2026, 1, 1), "end": date(2026, 1, 31)},
    ]
    assert all(s == INSERT_SETTINGS for _, s in inserts)
    assert ch.records == [[A, "ok", T0, T0, 9999, 3, date(2025, 11, 17), date(2026, 1, 3), ""]]


def test_refresh_all_three_aggregates_with_their_entities() -> None:
    ch = FakeCh(
        ranges={
            ("sales",): (date(2026, 10, 2), date(2026, 11, 20), 50),
            ("production", "delivery", "transfer", "fin_sold"): (date(2026, 11, 1), date(2026, 11, 20), 9),
        }
    )
    results = refresh_all(ch, clock=clock, lock=lock_for(ch))
    assert [(r.name, r.status, r.months) for r in results] == [
        ("agg_esb_sales_branch_daily", "ok", 2),
        ("agg_esb_sales_daily", "ok", 2),
        ("agg_esb_flow_daily", "ok", 1),
    ]
    assert ch.events.count("beat") == 1 + 5  # acquire + satu per bulan
    assert ch.events[-1] == "release" and len(ch.records) == 3


def test_refresh_without_data_touches_nothing_but_build() -> None:
    ch = FakeCh()
    results = refresh_all(ch, [SALES_BRANCH], clock=clock, lock=lock_for(ch))
    assert results[0].status == "ok" and results[0].months == 0
    assert ch.table_events(A, B) == [f"TRUNCATE {B}"]
    assert ch.records[0][6] == date(1970, 1, 1)  # first_day kosong


# ------------------------------------------------------------------------------------------------ SEC-AG-03 penyusutan
def test_shrinking_month_is_skipped_unless_allowed() -> None:
    ranges = {("sales",): (date(2026, 9, 1), date(2026, 11, 20), 80)}
    counts = {"agg_counts": {"202609": 100, "202610": 100, "202611": 100}, "build_counts": {"202609": 0, "202610": 40}}
    ch = FakeCh(ranges=ranges, **counts)
    result = refresh_all(ch, [SALES_BRANCH], clock=clock, lock=lock_for(ch))[0]
    # 0 baris dan turun 60% dilewati; November (1234 >= 100) diganti. Agregat berstatus error.
    assert [e for e in ch.table_events(A) if e.startswith("REPLACE")] == [f"REPLACE {A} 202611"]
    assert (result.status, result.error, result.months) == ("error", "shrink 202609,202610", 1)
    allowed = FakeCh(ranges=ranges, **counts)
    result = refresh_all(allowed, [SALES_BRANCH], clock=clock, allow_shrink=True, lock=lock_for(allowed))[0]
    assert result.status == "ok" and result.months == 3


def test_partition_missing_from_source_kept_unless_allowed() -> None:
    ranges = {("sales",): (date(2026, 11, 1), date(2026, 11, 20), 20)}
    ch = FakeCh(ranges=ranges, partitions=["202401", "202611"])
    result = refresh_all(ch, [SALES_BRANCH], clock=clock, lock=lock_for(ch))[0]
    assert not [e for e in ch.events if e.startswith("DROP")]
    assert (result.status, result.error) == ("error", "shrink 202401")
    allowed = FakeCh(ranges=ranges, partitions=["202401", "202611"])
    result = refresh_all(allowed, [SALES_BRANCH], clock=clock, allow_shrink=True, lock=lock_for(allowed))[0]
    assert result.status == "ok"
    drop_at = allowed.events.index(f"DROP {A} 202401")
    assert allowed.events[drop_at - 1] == "holder"  # kunci dicek sebelum DROP PARTITION


def test_empty_source_never_empties_existing_aggregate() -> None:
    # Checkpoint tidak terbaca (mis. CH_RAW_DATABASE salah / restore DR parsial) -> tidak ada bulan; agg yang berisi
    # TIDAK dikosongkan (sebelumnya: build kosong di-EXCHANGE).
    ch = FakeCh(partitions=["202610", "202611"])
    result = refresh_all(ch, [SALES_BRANCH], clock=clock, lock=lock_for(ch))[0]
    assert result.status == "error" and result.error == "shrink 202610,202611"
    assert ch.table_events(A) == []


# ------------------------------------------------------------------------------------------------ SEC-AG-02 kunci
def test_busy_when_other_run_holds_lock() -> None:
    ch = FakeCh(ranges={("sales",): (date(2026, 11, 1), date(2026, 11, 20), 20)})
    ch.add_lock_row(OTHER)  # refresh lain mulai lebih dulu dan masih ber-heartbeat
    results = refresh_all(ch, AGGREGATES, clock=clock, lock=lock_for(ch))
    assert [r.status for r in results] == ["busy", "busy", "busy"]
    assert ch.table_events(*(a.name for a in AGGREGATES), *(a.build for a in AGGREGATES)) == []
    assert ch.commands == []  # tidak menyentuh tabel apa pun
    assert ch.events[-1] == "release" and ch.holder() == OTHER
    assert all(r[1] == "busy" and r[8] for r in ch.records)


def test_stale_holder_can_be_taken_over() -> None:
    ch = FakeCh(ranges={("sales",): (date(2026, 11, 1), date(2026, 11, 20), 20)})
    ch.add_lock_row(OTHER)
    ch.stale.add(OTHER)  # heartbeat terakhir > 15 menit: proses mati
    assert refresh_all(ch, [SALES_BRANCH], clock=clock, lock=lock_for(ch))[0].status == "ok"
    ch2 = FakeCh()
    ch2.add_lock_row(OTHER)
    ch2.add_lock_row(OTHER, released=1)  # dilepas normal
    assert refresh_all(ch2, [SALES_BRANCH], clock=clock, lock=lock_for(ch2))[0].status == "ok"


def test_two_processes_racing_get_exactly_one_holder() -> None:
    ch = FakeCh()
    second = lock_for(ch, OTHER)

    # Baris proses kedua tertulis SAAT proses pertama menunggu (settle) -> keduanya tetap sepakat pemegang = pertama.
    def interleave(_s: float) -> None:
        ch.add_lock_row(OTHER)

    first = RefreshLock(ch, run_id=RUN, sleep=interleave, settle=0)
    assert first.acquire() is True
    assert second.acquire() is False  # pemegang tunggal
    first.ensure()  # tidak melempar
    with pytest.raises(aggregates.LockLost):
        second.ensure()
    # Pemegang melepas -> proses kedua boleh mengambil alih.
    first.release()
    second.ensure()


def test_lock_lost_mid_refresh_stops_without_further_writes() -> None:
    def take_over(ch: FakeCh, event: str) -> None:
        if event == f"REPLACE {A} 202610":
            ch.stale.add(RUN)  # heartbeat proses ini dianggap basi, proses lain mengambil alih
            ch.add_lock_row(OTHER)

    ch = FakeCh(ranges={("sales",): (date(2026, 10, 1), date(2026, 11, 20), 50)}, on_event=take_over)
    results = refresh_all(ch, AGGREGATES[:2], clock=clock, lock=lock_for(ch))
    assert [(r.status, r.error) for r in results] == [("error", "lock_lost"), ("busy", aggregates.BUSY_ERROR)]
    after = ch.events[ch.events.index(f"REPLACE {A} 202610") + 1 :]
    assert not [e for e in after if e.split()[0] in ("TRUNCATE", "INSERT", "REPLACE", "DROP")]
    assert ch.table_events(AGGREGATES[1].name, AGGREGATES[1].build) == []


def test_heartbeat_after_every_month() -> None:
    ch = FakeCh(ranges={("sales",): (date(2025, 11, 17), date(2026, 11, 20), 300)})
    refresh_all(ch, [SALES_BRANCH], clock=clock, lock=lock_for(ch))
    replaces = [i for i, e in enumerate(ch.events) if e.startswith("REPLACE")]
    assert len(replaces) == 13 and all(ch.events[i + 1] == "beat" for i in replaces)


# ------------------------------------------------------------------------------------------------ kegagalan
def test_insert_failure_never_replaces_partition(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    ch = FakeCh(ranges={("sales",): (date(2026, 10, 1), date(2026, 11, 20), 50)}, fail_on=f"INSERT INTO efn_dwh.{B}")
    results = refresh_all(ch, AGGREGATES[:2], clock=clock, lock=lock_for(ch))
    assert [r.status for r in results] == ["error", "ok"]
    assert not [e for e in ch.table_events(A) if e.startswith("REPLACE")]  # Insight tetap membaca isi lama
    assert ch.records[0][1] == "error" and ch.records[0][8] == "RuntimeError code=241"
    assert "62812345678" not in caplog.text and "rahasia" not in caplog.text
    assert "62812345678" not in repr(ch.records)
    assert ch.events[-1] == "release"


def test_replace_failure_reports_error_and_continues() -> None:
    ch = FakeCh(ranges={("sales",): (date(2026, 11, 1), date(2026, 11, 20), 20)}, fail_on=f"TABLE efn_dwh.{A} REPLACE")
    results = refresh_all(ch, AGGREGATES[:2], clock=clock, lock=lock_for(ch))
    assert [r.status for r in results] == ["error", "ok"]


def test_record_failure_does_not_fail_refresh() -> None:
    ch = FakeCh(fail_record=True)
    assert [r.status for r in refresh_all(ch, [SALES_BRANCH], clock=clock, lock=lock_for(ch))] == ["ok"]


# ------------------------------------------------------------------------------------------------ runner / CLI
def test_refresh_aggregates_never_raises() -> None:
    assert refresh_aggregates(FakeCh()) is True
    assert refresh_aggregates(FakeCh(fail_on="extract_checkpoint")) is False
    assert refresh_aggregates(FakeCh(fail_lock=True)) is False  # kunci tidak bisa ditulis
    assert refresh_aggregates(FakeCh(partitions=["202611"])) is False  # penyusutan
    assert refresh_aggregates(FakeCh(partitions=["202611"]), allow_shrink=True) is True


def _slice(entity: str) -> SliceResult:
    return SliceResult(task=SliceTask(date(2026, 11, 21), entity, "*"), version=T0)


def test_trigger_refreshes_only_after_data_slices_and_retries_on_failure() -> None:
    trigger = RefreshTrigger()
    trigger.note(_slice("branch"))  # ditarik setiap siklus -> bukan pemicu
    trigger.note(_slice("payment"))  # tidak dipakai agregat
    assert trigger.maybe_refresh(FakeCh()) is None
    trigger.note(_slice("sales"))
    failing = FakeCh(fail_on="extract_checkpoint")
    assert trigger.maybe_refresh(failing) is False and trigger.dirty  # dicoba lagi siklus berikutnya
    ch = FakeCh()
    assert trigger.maybe_refresh(ch) is True and not trigger.dirty
    assert trigger.maybe_refresh(ch) is None


def test_cli_refresh_runs_with_esb_disabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Salin data dari DR: env tanpa ESB aktif tetap bisa membangun agregat (hanya ClickHouse).
    monkeypatch.setenv("ESB_ENABLED", "false")
    ch = FakeCh(ranges={("sales",): (date(2026, 11, 1), date(2026, 11, 2), 2)})
    monkeypatch.setattr(cli, "_store", lambda _env: ch)
    assert cli.main(["refresh-aggregates"]) == 0
    assert "refresh-aggregates status=ok" in capsys.readouterr().out
    assert len(ch.records) == len(AGGREGATES)


def test_cli_allow_shrink_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_store", lambda _env: FakeCh(partitions=["202611"]))
    assert cli.main(["refresh-aggregates"]) == 1
    monkeypatch.setattr(cli, "_store", lambda _env: FakeCh(partitions=["202611"]))
    assert cli.main(["refresh-aggregates", "--allow-shrink"]) == 0


def test_cli_refresh_exit_nonzero_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_store", lambda _env: FakeCh(fail_on="extract_checkpoint"))
    assert cli.main(["refresh-aggregates"]) == 1

    def broken(_env: Any) -> Any:
        raise RuntimeError("CH_PASSWORD_FILE di-set tetapi file tidak ditemukan")

    monkeypatch.setattr(cli, "_store", broken)
    assert cli.main(["refresh-aggregates"]) == 1


def test_cli_backfill_exit_code_unaffected_by_refresh_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    cfg = BrandConfig("pb", "user-sintetis", "pw-sintetis", "PB", frozenset({4}), frozenset(), {})
    settings = EsbSettings(
        enabled=True, brands=(cfg,), production=False, allow_ignore_window=True, heartbeat_file=str(tmp_path / "hb")
    )
    ch = FakeCh(fail_on="extract_checkpoint")
    monkeypatch.setattr(cli, "_store", lambda _env: ch)

    def fake_backfill(*_args: Any, on_written: Any = None, **_kwargs: Any) -> RunRecord:
        on_written(_slice("sales"))
        return RunRecord("pb", "backfill", T0, uuid.uuid4(), slices=1)

    monkeypatch.setattr("efn_esb.runner.run_backfill", fake_backfill)
    end = wib_today(datetime.now(UTC)) - timedelta(days=2)
    args = argparse.Namespace(brand="pb", start=end, end=end, entity=None, force=False, ignore_window=True)
    assert cli._cmd_backfill(settings, args, threading.Event()) == 0
    assert all(r[1] == "error" for r in ch.records)  # refresh dicoba, gagal, backfill tetap sukses
