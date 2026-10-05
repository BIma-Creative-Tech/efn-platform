"""Memuat semantic layer (YAML) -> whitelist guard + konteks prompt LLM."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from efn_text2sql.guard import GuardConfig


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type: str
    description: str = ""


@dataclass(frozen=True)
class TableSpec:
    name: str
    description: str
    columns: tuple[ColumnSpec, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SemanticLayer:
    database: str
    tables: tuple[TableSpec, ...]
    max_limit: int = 1000
    max_execution_time_seconds: int = 15
    examples: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    protected_tables: tuple[str, ...] = field(default_factory=tuple)

    def guard_config(self) -> GuardConfig:
        allowed = frozenset(f"{self.database}.{t.name}".lower() for t in self.tables)
        return GuardConfig(
            allowed_tables=allowed,
            protected_tables=frozenset(name.lower() for name in self.protected_tables),
            default_database=self.database.lower(),
            max_limit=self.max_limit,
            max_execution_time=self.max_execution_time_seconds,
        )


def parse_semantic_layer(data: dict[str, Any]) -> SemanticLayer:
    if not isinstance(data, dict) or "tables" not in data:
        raise ValueError("semantic layer harus berisi kunci 'tables'")
    tables = []
    for raw in data["tables"]:
        name = str(raw["name"])
        if "." in name:
            raise ValueError("nama tabel ditulis tanpa database; database diatur di kunci 'database'")
        columns = tuple(
            ColumnSpec(name=str(c["name"]), type=str(c.get("type", "")), description=str(c.get("description", "")))
            for c in raw.get("columns", [])
        )
        tables.append(TableSpec(name=name, description=str(raw.get("description", "")), columns=columns))
    examples = tuple((str(e["question"]), str(e["sql"])) for e in data.get("examples", []))
    return SemanticLayer(
        database=str(data.get("database", "efn_dwh")),
        tables=tuple(tables),
        max_limit=int(data.get("max_limit", 1000)),
        max_execution_time_seconds=int(data.get("max_execution_time_seconds", 15)),
        examples=examples,
        protected_tables=tuple(str(name) for name in data.get("protected_tables", [])),
    )


def load_semantic_layer(path: str | Path) -> SemanticLayer:
    with Path(path).open(encoding="utf-8") as handle:
        return parse_semantic_layer(yaml.safe_load(handle))
