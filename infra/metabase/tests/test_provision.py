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
    assert [d.name for d in dashboards] == ["Penjualan Harian Outlet", "Stok & Kedaluwarsa", "Price Hunter"]
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
