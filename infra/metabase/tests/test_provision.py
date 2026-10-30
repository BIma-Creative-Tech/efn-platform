import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import provision as p  # noqa: E402


def _dash(**overrides):
    data = {
        "key": "uji",
        "name": "Uji",
        "parameters": [
            {"slug": "date_from", "name": "Dari", "type": "date/single"},
            {"slug": "outlet", "name": "Outlet", "type": "string/="},
        ],
        "cards": [
            {
                "key": "a",
                "name": "A",
                "display": "table",
                "sql": "SELECT day FROM efn_dwh.v_pos_outlet_daily WHERE 1 = 1 [[AND day >= {{date_from}}]]",
                "row": 0,
                "col": 0,
                "size_x": 12,
                "size_y": 4,
            }
        ],
    }
    data.update(overrides)
    return data


def test_repo_dashboards_are_valid() -> None:
    dashboards = p.load_dashboards(ROOT / "dashboards")
    assert [d.name for d in dashboards] == [
        "Penjualan Harian Outlet",
        "Stok & Kedaluwarsa",
        "Price Hunter",
        "Waste & Yield CK",  # G32 (K25-08)
    ]
    for dash in dashboards:
        slugs = {x.slug for x in dash.parameters}
        assert {"date_from", "date_to"} <= slugs
        assert any(c.display == "scalar" for c in dash.cards)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM efn_dwh.fact_sales_line",
        "SELECT 1 FROM efn_dwh.v_pos_outlet_daily JOIN efn_dwh.dim_outlet USING (store_id)",
        "SELECT * FROM v_pos_outlet_daily",
        "SELECT * FROM system.users",
        "SELECT * FROM url('http://x', CSV, 'a String')",
        "SELECT 1 FROM efn_dwh.v_pos_outlet_daily; DROP TABLE efn_dwh.fact_sales_line",
        "INSERT INTO efn_dwh.v_x SELECT 1",
        "SELECT 1",
        "SELECT * FROM file('/etc/passwd') JOIN efn_dwh.v_pos_outlet_daily ON 1",
    ],
)
def test_check_sql_rejects_outside_semantic_layer(sql: str) -> None:
    with pytest.raises(p.ProvisionError):
        p.check_sql(sql)


def test_check_sql_accepts_views_and_subqueries() -> None:
    p.check_sql(
        "SELECT a FROM efn_dwh.v_competitor_prices WHERE report_id NOT IN (SELECT report_id FROM efn_dwh.v_price_index)"
    )
    # Batas sengaja: nama CTE dianggap relasi -> ditolak (kartu memakai subquery, bukan WITH).
    with pytest.raises(p.ProvisionError):
        p.check_sql("WITH x AS (SELECT 1 AS a FROM efn_dwh.v_stock_current) SELECT a FROM x")


def test_unknown_template_variable_rejected() -> None:
    data = _dash()
    data["cards"][0]["sql"] = "SELECT day FROM efn_dwh.v_pos_outlet_daily WHERE 1 = 1 [[AND pt = {{company}}]]"
    with pytest.raises(p.ProvisionError, match="tanpa parameter"):
        p.parse_dashboard(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [("size_x", 30), ("col", 20), ("display", "map"), ("key", "Bad Key")],
)
def test_card_validation(field: str, value: object) -> None:
    data = _dash()
    data["cards"][0][field] = value
    with pytest.raises(p.ProvisionError):
        p.parse_dashboard(data)


def test_overlapping_cards_rejected() -> None:
    data = _dash()
    second = dict(data["cards"][0], key="b", col=6)
    data["cards"].append(second)
    with pytest.raises(p.ProvisionError, match="bertumpuk"):
        p.parse_dashboard(data)


def test_bad_parameter_type_rejected() -> None:
    with pytest.raises(p.ProvisionError):
        p.parse_dashboard(_dash(parameters=[{"slug": "x", "name": "X", "type": "location/city"}]))


def test_card_payload_template_tags_and_tag() -> None:
    dash = p.parse_dashboard(_dash())
    payload = p.card_payload(dash, dash.cards[0], database_id=2, collection_id=5)
    tags = payload["dataset_query"]["native"]["template-tags"]
    assert list(tags) == ["date_from"]  # outlet tidak dipakai kartu -> tidak dibuat
    assert tags["date_from"]["type"] == "date"
    assert tags["date_from"]["id"] == p.tag_id("uji", "date_from")  # stabil antar-run
    assert payload["description"].endswith("[efn:uji/a]")
    assert p.find_tag(payload["description"]) == "uji/a"
    assert payload["dataset_query"]["database"] == 2


def test_dashcards_map_only_used_parameters() -> None:
    dash = p.parse_dashboard(_dash())
    cards = p.dashcards(dash, {"a": 41})
    assert cards[0]["id"] == -1 and cards[0]["card_id"] == 41
    assert cards[0]["parameter_mappings"] == [
        {"parameter_id": "date_from", "card_id": 41, "target": ["variable", ["template-tag", "date_from"]]}
    ]
    assert [x["slug"] for x in p.dashboard_parameters(dash)] == ["date_from", "outlet"]


def test_clickhouse_details_force_tls_verification() -> None:
    details = p.clickhouse_details("rahasia", "clickhouse", 8443, "/run/secrets/efn_ca_crt")
    assert details["ssl"] is True
    assert details["additional-options"] == "sslmode=strict&sslrootcert=/run/secrets/efn_ca_crt"
    assert details["user"] == "efn_metabase" and details["dbname"] == "efn_dwh"
    # driver bawaan v0.58 (plugin 1.53): default enable-multiple-db=True akan menyinkron semua database
    assert details["enable-multiple-db"] is False
    assert "scan-all-databases" not in details


def test_client_requires_https() -> None:
    with pytest.raises(p.ProvisionError):
        p.MetabaseClient("http://bi.efn.local", None, None, None, None)


def test_secret_file_must_not_be_empty(tmp_path: Path) -> None:
    secret = tmp_path / "s.txt"
    secret.write_text("  \n", encoding="utf-8")
    with pytest.raises(p.ProvisionError):
        p.read_secret_file(str(secret))
    secret.write_text("abc\n", encoding="utf-8")
    assert p.read_secret_file(str(secret)) == "abc"


# ---------------------------------------------------------------- G18/W-19: akun tidak aktif 90 hari

NOW = p.datetime(2026, 10, 10, 12, 0, tzinfo=p.UTC)


def _user(uid: int, last_login: str | None, joined: str = "2026-01-01T00:00:00Z", **extra):
    return {"id": uid, "is_active": True, "last_login": last_login, "date_joined": joined, **extra}


def test_parse_ts_metabase_formats() -> None:
    assert p.parse_ts("2026-10-10T10:03:55.6038Z") == p.datetime(2026, 10, 10, 10, 3, 55, 603800, tzinfo=p.UTC)
    assert p.parse_ts("2026-10-10T10:03:55") == p.datetime(2026, 10, 10, 10, 3, 55, tzinfo=p.UTC)
    for bad in (None, "", "bukan-tanggal", 123):
        assert p.parse_ts(bad) is None


def test_inactive_users_selection() -> None:
    users = [
        _user(1, "2026-01-01T00:00:00Z", is_superuser=True),  # admin provisioning (keep) walau lama
        _user(2, "2026-07-11T11:59:59Z"),  # 91 hari -> nonaktif
        _user(3, "2026-07-12T12:00:00Z"),  # tepat 90 hari -> tetap
        _user(4, None, joined="2026-05-01T00:00:00Z"),  # undangan tak pernah dipakai -> nonaktif
        _user(5, None, joined="2026-10-01T00:00:00Z"),  # undangan baru -> tetap
        _user(6, "2025-01-01T00:00:00Z", is_active=False),  # sudah nonaktif
        _user(7, "2025-01-01T00:00:00Z", type="api-key"),  # bukan akun orang
        _user(8, "rusak", joined="rusak"),  # timestamp tak terbaca -> dilewati (fail safe)
        _user(9, "2025-01-01T00:00:00Z", is_superuser=True),  # admin lain yang lama tidak aktif -> nonaktif
    ]
    assert [u["id"] for u in p.inactive_users(users, NOW, 90, {1})] == [2, 4, 9]
    assert p.inactive_users(users, NOW, 0, {1}) == []


class _FakeClient:
    def __init__(self, users):
        self.users = users
        self.calls: list[tuple[str, str]] = []

    def request(self, method, path, body=None, expect=(200,)):
        self.calls.append((method, path))
        if path == "/api/user/current":
            return {"id": 1}
        if path == "/api/user":
            return {"data": self.users, "total": len(self.users)}
        return None


def test_deactivate_inactive_users_calls_delete_only_for_targets(capsys) -> None:
    client = _FakeClient(
        [
            _user(1, "2020-01-01T00:00:00Z"),
            _user(2, "2026-01-01T00:00:00Z"),
            _user(3, None, joined="2026-10-01T00:00:00Z"),
        ]
    )
    assert p.deactivate_inactive_users(client, 90, NOW) == 1
    assert ("DELETE", "/api/user/2") in client.calls
    assert ("DELETE", "/api/user/1") not in client.calls
    out = capsys.readouterr().out
    assert "@" not in out  # tidak mencetak email


def test_deactivate_disabled_makes_no_calls() -> None:
    client = _FakeClient([_user(2, "2020-01-01T00:00:00Z")])
    assert p.deactivate_inactive_users(client, 0, NOW) == 0
    assert client.calls == []


def test_deactivate_days_cli_range() -> None:
    with pytest.raises(SystemExit):
        p.main(
            [
                "--url",
                "https://bi.efn.local",
                "--admin-email",
                "a@b",
                "--admin-password-file",
                "x",
                "--ch-password-file",
                "y",
                "--deactivate-inactive-days",
                "-1",
            ]
        )
