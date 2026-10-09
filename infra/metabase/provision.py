#!/usr/bin/env python3
"""Provisioning dashboard Metabase sebagai kode (Gelombang 13, Fase 1 — docs/01 §7 "3 dashboard Metabase").

Idempoten: aman dijalankan berulang (deploy/CI). Membuat atau memperbarui:
  1. setup awal Metabase (bila belum di-setup) dengan admin dari file secret — menutup jendela "siapa cepat dia dapat"
     pada instance yang belum di-setup;
  2. koneksi database ClickHouse `EFN DWH (ClickHouse)`: HTTPS 8443, user `efn_metabase` (read-only, role
     metabase_reader), verifikasi sertifikat CA internal (`sslmode=strict`, `sslrootcert=/run/secrets/efn_ca_crt`);
  3. collection `EFN Fase 1`;
  4. dashboard dari `dashboards/*.json` — kartu SQL native yang HANYA membaca view `efn_dwh.v_*` (semantic layer),
     filter tanggal/PT/outlet sebagai variabel template.

Transport: HTTPS ke Metabase lewat Traefik (`https://bi.<domain>`, entrypoint wg-https, allowlist subnet staf),
sertifikat server diverifikasi (CA sistem atau `--ca-file`); mTLS opsional
(`--client-cert/--client-key`) bila dijalankan langsung ke sidecar ghostunnel. Tidak ada mode tanpa verifikasi.

Secret hanya dari file (`--admin-password-file`, `--ch-password-file`); tidak dicetak, tidak di argv.
Hanya stdlib Python 3.12 (tanpa dependency).

Contoh (dev, Git Bash; Traefik wg-https dipublish ke 127.0.0.1:9443, sertifikat edge dev self-signed):
  python infra/metabase/provision.py --url https://bi.efn.local:9443 --connect-host 127.0.0.1 \
      --ca-file secrets/dev_edge_crt.txt --admin-email admin@efn.local \
      --admin-password-file secrets/metabase_admin_password.txt \
      --ch-password-file secrets/clickhouse_password_metabase.txt
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import socket
import ssl
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DB_NAME = "EFN DWH (ClickHouse)"
COLLECTION_NAME = "EFN Fase 1"
TAG_RE = re.compile(r"\[efn:([a-z0-9_.\-/]+)\]")
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,60}$")
TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
# Relasi yang dibaca kartu: setelah FROM/JOIN. Hanya view semantic layer yang boleh (bukan tabel mentah).
RELATION_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*|\()", re.IGNORECASE)
ALLOWED_RELATION_RE = re.compile(r"^efn_dwh\.v_[a-z0-9_]+$")
FORBIDDEN_SQL_RE = re.compile(
    r"\b(INSERT|ALTER|DROP|CREATE|TRUNCATE|OPTIMIZE|SYSTEM|GRANT|REVOKE|ATTACH|DETACH|KILL|SET)\b|"
    r"\b(file|url|remote|remoteSecure|s3|hdfs|mysql|postgresql|jdbc|odbc|cluster|input|executable)\s*\(",
    re.IGNORECASE,
)
PARAM_TYPES = {"date/single": "date", "string/=": "text", "number/=": "number"}
DISPLAYS = {"scalar", "table", "line", "bar", "row", "pie", "area", "combo", "progress", "gauge"}
GRID_WIDTH = 24


class ProvisionError(RuntimeError):
    pass


# ============================================================== definisi (logika murni, diuji pytest)


@dataclass(frozen=True)
class ParamDef:
    slug: str
    name: str
    type: str
    default: Any = None


@dataclass(frozen=True)
class CardDef:
    key: str
    name: str
    display: str
    sql: str
    row: int
    col: int
    size_x: int
    size_y: int
    description: str = ""
    visualization_settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DashboardDef:
    key: str
    name: str
    description: str
    parameters: tuple[ParamDef, ...]
    cards: tuple[CardDef, ...]


def check_sql(sql: str) -> None:
    """Kartu hanya SELECT/WITH ke view efn_dwh.v_* (semantic layer), tanpa DDL/DML/fungsi tabel."""
    stripped = sql.strip()
    if not re.match(r"^(SELECT|WITH)\b", stripped, re.IGNORECASE):
        raise ProvisionError("kartu harus diawali SELECT/WITH")
    if ";" in stripped.rstrip(";"):
        raise ProvisionError("kartu hanya satu statement")
    if FORBIDDEN_SQL_RE.search(stripped):
        raise ProvisionError("kartu memuat kata kunci/fungsi terlarang")
    relations = [r for r in RELATION_RE.findall(stripped) if r != "("]
    if not relations:
        raise ProvisionError("kartu tidak membaca view apa pun")
    for relation in relations:
        if not ALLOWED_RELATION_RE.fullmatch(relation):
            raise ProvisionError(f"kartu membaca relasi di luar semantic layer: {relation}")


def parse_dashboard(data: dict[str, Any]) -> DashboardDef:
    key = str(data.get("key", ""))
    if not KEY_RE.fullmatch(key):
        raise ProvisionError("key dashboard tidak valid")
    params = []
    for p in data.get("parameters", []):
        slug, ptype = str(p["slug"]), str(p["type"])
        if not SLUG_RE.fullmatch(slug):
            raise ProvisionError(f"slug parameter tidak valid: {slug}")
        if ptype not in PARAM_TYPES:
            raise ProvisionError(f"tipe parameter tidak didukung: {ptype}")
        params.append(ParamDef(slug, str(p["name"]), ptype, p.get("default")))
    slugs = {p.slug for p in params}
    if len(slugs) != len(params):
        raise ProvisionError("slug parameter duplikat")
    cards = []
    for c in data["cards"]:
        card = CardDef(
            key=str(c["key"]),
            name=str(c["name"]),
            display=str(c["display"]),
            sql=str(c["sql"]),
            row=int(c["row"]),
            col=int(c["col"]),
            size_x=int(c["size_x"]),
            size_y=int(c["size_y"]),
            description=str(c.get("description", "")),
            visualization_settings=dict(c.get("visualization_settings", {})),
        )
        if not KEY_RE.fullmatch(card.key):
            raise ProvisionError(f"key kartu tidak valid: {card.key}")
        if card.display not in DISPLAYS:
            raise ProvisionError(f"display tidak didukung: {card.display}")
        if card.col < 0 or card.size_x < 1 or card.col + card.size_x > GRID_WIDTH or card.row < 0 or card.size_y < 1:
            raise ProvisionError(f"posisi kartu di luar grid: {card.key}")
        check_sql(card.sql)
        unknown = set(TEMPLATE_VAR_RE.findall(card.sql)) - slugs
        if unknown:
            raise ProvisionError(f"kartu {card.key} memakai variabel tanpa parameter: {sorted(unknown)}")
        cards.append(card)
    if len({c.key for c in cards}) != len(cards):
        raise ProvisionError("key kartu duplikat")
    _check_overlap(cards)
    return DashboardDef(key, str(data["name"]), str(data.get("description", "")), tuple(params), tuple(cards))


def _check_overlap(cards: list[CardDef]) -> None:
    cells: dict[tuple[int, int], str] = {}
    for card in cards:
        for r in range(card.row, card.row + card.size_y):
            for c in range(card.col, card.col + card.size_x):
                if (r, c) in cells:
                    raise ProvisionError(f"kartu {card.key} bertumpuk dengan {cells[(r, c)]}")
                cells[(r, c)] = card.key


def tag(dash_key: str, card_key: str | None = None) -> str:
    return f"[efn:{dash_key}]" if card_key is None else f"[efn:{dash_key}/{card_key}]"


def find_tag(description: str | None) -> str | None:
    match = TAG_RE.search(description or "")
    return match.group(1) if match else None


def tag_id(dash_key: str, slug: str) -> str:
    """ID template-tag stabil (uuid5) agar mapping filter tidak berubah antar-run."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"efn-metabase/{dash_key}/{slug}"))


def template_tags(dash: DashboardDef, card: CardDef) -> dict[str, Any]:
    used = set(TEMPLATE_VAR_RE.findall(card.sql))
    tags: dict[str, Any] = {}
    for p in dash.parameters:
        if p.slug not in used:
            continue
        tags[p.slug] = {
            "id": tag_id(dash.key, p.slug),
            "name": p.slug,
            "display-name": p.name,
            "type": PARAM_TYPES[p.type],
            "required": False,
        }
    return tags


def card_payload(dash: DashboardDef, card: CardDef, database_id: int, collection_id: int) -> dict[str, Any]:
    description = f"{card.description} {tag(dash.key, card.key)}".strip()
    return {
        "name": card.name,
        "description": description,
        "display": card.display,
        "collection_id": collection_id,
        "visualization_settings": card.visualization_settings,
        "dataset_query": {
            "type": "native",
            "database": database_id,
            "native": {"query": card.sql.strip(), "template-tags": template_tags(dash, card)},
        },
    }


def dashboard_parameters(dash: DashboardDef) -> list[dict[str, Any]]:
    out = []
    for p in dash.parameters:
        param: dict[str, Any] = {"id": p.slug, "name": p.name, "slug": p.slug, "type": p.type}
        if p.default is not None:
            param["default"] = p.default
        out.append(param)
    return out


def dashcards(dash: DashboardDef, card_ids: dict[str, int]) -> list[dict[str, Any]]:
    """Seluruh dashcard (id negatif = baru; PUT /api/dashboard/:id mengganti isi dashboard sesuai definisi)."""
    out = []
    for index, card in enumerate(dash.cards, start=1):
        card_id = card_ids[card.key]
        used = set(TEMPLATE_VAR_RE.findall(card.sql))
        mappings = [
            {"parameter_id": p.slug, "card_id": card_id, "target": ["variable", ["template-tag", p.slug]]}
            for p in dash.parameters
            if p.slug in used
        ]
        out.append(
            {
                "id": -index,
                "card_id": card_id,
                "row": card.row,
                "col": card.col,
                "size_x": card.size_x,
                "size_y": card.size_y,
                "parameter_mappings": mappings,
                "visualization_settings": {},
                "series": [],
            }
        )
    return out


def clickhouse_details(
    password: str, host: str, port: int, ca_path: str, database: str = "efn_dwh", user: str = "efn_metabase"
) -> dict[str, Any]:
    """Detail koneksi driver ClickHouse BAWAAN Metabase v0.58 (plugin 1.53.x): TLS wajib + verifikasi CA internal.

    `ssl` -> JDBC `ssl=true`; `additional-options` ditempel ke URL JDBC (`sslmode=strict` = verifikasi rantai CA +
    hostname, `sslrootcert` = CA internal di container Metabase). Driver 1.53 mengganti `scan-all-databases` dengan
    `enable-multiple-db` yang default-nya TRUE (sinkron semua database) -> dipaksa False agar hanya `dbname`.
    """
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "dbname": database,
        "enable-multiple-db": False,
        "ssl": True,
        "additional-options": f"sslmode=strict&sslrootcert={ca_path}",
        "advanced-options": False,
    }


def load_dashboards(directory: Path) -> list[DashboardDef]:
    dashboards = [parse_dashboard(json.loads(p.read_text(encoding="utf-8"))) for p in sorted(directory.glob("*.json"))]
    if len({d.key for d in dashboards}) != len(dashboards):
        raise ProvisionError("key dashboard duplikat")
    return dashboards


def read_secret_file(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise ProvisionError(f"file secret kosong: {Path(path).name}")
    return value


# ============================================================== klien HTTP (I/O)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS ke `connect_host` (mis. 127.0.0.1) tetapi SNI + verifikasi hostname = host URL (seperti curl --resolve)."""

    def __init__(self, host: str, port: int, connect_host: str, context: ssl.SSLContext, timeout: float) -> None:
        super().__init__(host, port, context=context, timeout=timeout)
        self._connect_host = connect_host
        self._ctx = context

    def connect(self) -> None:
        raw = socket.create_connection((self._connect_host, self.port), self.timeout)
        self.sock = self._ctx.wrap_socket(raw, server_hostname=self.host)


class MetabaseClient:
    def __init__(
        self,
        url: str,
        ca_file: str | None,
        client_cert: str | None,
        client_key: str | None,
        connect_host: str | None,
        timeout: float = 120.0,
    ) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ProvisionError("--url wajib https:// (tidak ada mode plaintext)")
        self.host = parts.hostname
        self.port = parts.port or 443
        self.prefix = parts.path.rstrip("/")
        context = ssl.create_default_context(cafile=ca_file)  # verify_mode=CERT_REQUIRED, check_hostname=True
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if client_cert:
            context.load_cert_chain(client_cert, client_key)
        self.context = context
        self.connect_host = connect_host or self.host
        self.timeout = timeout
        self.session: str | None = None

    def request(self, method: str, path: str, body: Any = None, expect: tuple[int, ...] = (200,)) -> Any:
        conn = _PinnedHTTPSConnection(self.host, self.port, self.connect_host, self.context, self.timeout)
        headers = {"Accept": "application/json"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.session:
            headers["X-Metabase-Session"] = self.session
        try:
            conn.request(method, f"{self.prefix}{path}", body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
        finally:
            conn.close()
        if response.status not in expect:
            # Isi respons bisa memuat detail koneksi; potong dan jangan pernah mencetak payload permintaan.
            snippet = raw.decode("utf-8", "replace")[:300]
            raise ProvisionError(f"{method} {path} -> HTTP {response.status}: {snippet}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode("utf-8", "replace")


# ============================================================== langkah provisioning


def login_or_setup(client: MetabaseClient, email: str, password: str) -> None:
    props = client.request("GET", "/api/session/properties")
    if not props.get("has-user-setup"):
        token = props.get("setup-token")
        if not token:
            raise ProvisionError("Metabase belum di-setup tetapi setup-token tidak tersedia")
        result = client.request(
            "POST",
            "/api/setup",
            {
                "token": token,
                "user": {
                    "email": email,
                    "password": password,
                    "first_name": "EFN",
                    "last_name": "Admin",
                    "site_name": "EFN BI",
                },
                "prefs": {"site_name": "EFN BI", "site_locale": "id", "allow_tracking": False},
            },
        )
        client.session = result["id"]
        print("setup awal Metabase selesai (admin dari file secret)")
        return
    result = client.request("POST", "/api/session", {"username": email, "password": password})
    client.session = result["id"]


def apply_settings(client: MetabaseClient) -> None:
    for key, value in (("anon-tracking-enabled", False), ("report-timezone", "Asia/Jakarta"), ("site-locale", "id")):
        client.request("PUT", f"/api/setting/{key}", {"value": value}, expect=(200, 204))


def ensure_database(client: MetabaseClient, details: dict[str, Any]) -> int:
    databases = client.request("GET", "/api/database")
    items = databases.get("data", databases) if isinstance(databases, dict) else databases
    for db in items:
        if db.get("name") == DB_NAME:
            # Selalu diperbarui: rotasi password `clickhouse_password_metabase` cukup jalankan ulang skrip.
            client.request("PUT", f"/api/database/{db['id']}", {"engine": "clickhouse", "details": details})
            print(f"database '{DB_NAME}' diperbarui (id={db['id']})")
            return int(db["id"])
    created = client.request(
        "POST",
        "/api/database",
        {"engine": "clickhouse", "name": DB_NAME, "details": details, "is_full_sync": True, "auto_run_queries": True},
    )
    print(f"database '{DB_NAME}' dibuat (id={created['id']})")
    return int(created["id"])


def ensure_collection(client: MetabaseClient) -> int:
    for col in client.request("GET", "/api/collection"):
        if col.get("name") == COLLECTION_NAME and not col.get("archived") and col.get("personal_owner_id") is None:
            return int(col["id"])
    created = client.request(
        "POST", "/api/collection", {"name": COLLECTION_NAME, "description": "Dashboard Fase 1 (dashboard sebagai kode)"}
    )
    print(f"collection '{COLLECTION_NAME}' dibuat (id={created['id']})")
    return int(created["id"])


def collection_items(client: MetabaseClient, collection_id: int, model: str) -> list[dict[str, Any]]:
    result = client.request("GET", f"/api/collection/{collection_id}/items?models={model}")
    return list(result.get("data", []) if isinstance(result, dict) else result)


def provision_dashboard(client: MetabaseClient, dash: DashboardDef, database_id: int, collection_id: int) -> int:
    existing_cards = {find_tag(c.get("description")): c for c in collection_items(client, collection_id, "card")}
    card_ids: dict[str, int] = {}
    for card in dash.cards:
        payload = card_payload(dash, card, database_id, collection_id)
        current = existing_cards.get(f"{dash.key}/{card.key}")
        if current:
            client.request("PUT", f"/api/card/{current['id']}", payload)
            card_ids[card.key] = int(current["id"])
        else:
            created = client.request("POST", "/api/card", payload)
            card_ids[card.key] = int(created["id"])
    # Kartu ber-tag dashboard ini yang sudah tidak ada di definisi -> diarsipkan (bukan dihapus).
    wanted = {f"{dash.key}/{c.key}" for c in dash.cards}
    for card_tag, item in existing_cards.items():
        if card_tag and card_tag.startswith(f"{dash.key}/") and card_tag not in wanted:
            client.request("PUT", f"/api/card/{item['id']}", {"archived": True})

    dashboards = {find_tag(d.get("description")): d for d in collection_items(client, collection_id, "dashboard")}
    description = f"{dash.description} {tag(dash.key)}".strip()
    current = dashboards.get(dash.key)
    if current:
        dashboard_id = int(current["id"])
    else:
        created = client.request(
            "POST", "/api/dashboard", {"name": dash.name, "description": description, "collection_id": collection_id}
        )
        dashboard_id = int(created["id"])
    client.request(
        "PUT",
        f"/api/dashboard/{dashboard_id}",
        {
            "name": dash.name,
            "description": description,
            "collection_id": collection_id,
            "parameters": dashboard_parameters(dash),
            "dashcards": dashcards(dash, card_ids),
        },
    )
    print(f"dashboard '{dash.name}' siap (id={dashboard_id}, kartu={len(card_ids)})")
    return dashboard_id


def verify_cards(client: MetabaseClient, collection_id: int) -> int:
    """Jalankan setiap kartu EFN sekali (tanpa filter). Gagal = exit != 0."""
    failures = 0
    for item in collection_items(client, collection_id, "card"):
        if not find_tag(item.get("description")):
            continue
        result = client.request("POST", f"/api/card/{item['id']}/query", {}, expect=(200, 202))
        status = result.get("status")
        rows = len(result.get("data", {}).get("rows", [])) if status == "completed" else 0
        print(f"  [{status}] {item['name']} -> {rows} baris")
        if status != "completed":
            failures += 1
            print(f"    error: {str(result.get('error', ''))[:300]}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="https://bi.<domain> (Traefik, WireGuard)")
    parser.add_argument("--ca-file", help="CA untuk sertifikat server Metabase/Traefik (default: CA sistem)")
    parser.add_argument("--client-cert", help="mTLS opsional (langsung ke sidecar ghostunnel)")
    parser.add_argument("--client-key")
    parser.add_argument("--connect-host", help="alamat TCP tujuan bila DNS belum ada (SNI tetap host URL)")
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-password-file", required=True)
    parser.add_argument("--ch-password-file", required=True, help="secret clickhouse_password_metabase")
    parser.add_argument("--ch-host", default="clickhouse")
    parser.add_argument("--ch-port", type=int, default=8443)
    parser.add_argument("--ch-ca-path", default="/run/secrets/efn_ca_crt", help="path CA DI DALAM container Metabase")
    parser.add_argument("--dashboards", default=str(Path(__file__).resolve().parent / "dashboards"))
    parser.add_argument("--no-verify-cards", action="store_true", help="lewati eksekusi uji setiap kartu")
    args = parser.parse_args(argv)

    try:
        dashboards = load_dashboards(Path(args.dashboards))
        client = MetabaseClient(args.url, args.ca_file, args.client_cert, args.client_key, args.connect_host)
        login_or_setup(client, args.admin_email, read_secret_file(args.admin_password_file))
        apply_settings(client)
        ch_password = read_secret_file(args.ch_password_file)
        details = clickhouse_details(ch_password, args.ch_host, args.ch_port, args.ch_ca_path)
        database_id = ensure_database(client, details)
        collection_id = ensure_collection(client)
        for dash in dashboards:
            provision_dashboard(client, dash, database_id, collection_id)
        if not args.no_verify_cards:
            print("verifikasi kartu:")
            failures = verify_cards(client, collection_id)
            if failures:
                print(f"{failures} kartu gagal", file=sys.stderr)
                return 1
    except (ProvisionError, OSError, ssl.SSLError, KeyError) as exc:
        print(f"provision gagal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
