"""Guard SQL untuk Text-to-SQL (R-S05, R-T06). Berbasis parser sqlglot dialect ClickHouse, bukan regex.

Aturan:
1. Tepat satu statement, akar harus query (`SELECT` / `WITH ... SELECT` / `UNION|INTERSECT|EXCEPT`).
2. Tidak ada node DDL/DML/perintah (INSERT, ALTER, DROP, SYSTEM, KILL, SET, INTO OUTFILE, ...) di mana pun.
3. Setiap tabel harus ada di whitelist semantic layer, atau nama CTE yang TERLIHAT di scope referensi itu
   (SEC-04): CTE hanya berlaku di query yang mendefinisikannya dan subquery turunannya, tidak di query luar,
   tidak di CTE sebelumnya / badannya sendiri, dan pada UNION hanya di cabang kiri (konservatif). CTE skalar
   (`WITH 1 AS x`) bukan nama tabel. Nama CTE yang bentrok dengan view whitelist atau tabel dasar terlindung
   (`protected_tables`) ditolak. Database lain (`system`, `information_schema`, ...) ditolak; table function
   (`url()`, `file()`, `remote()`, ...) ditolak.
3b. `IN` hanya boleh berisi subquery atau daftar konstanta; `x IN tabel` / `IN (kolom)` ditolak (SEC-04 vektor 2).
4. Fungsi berbahaya/introspeksi ditolak (lihat BLOCKED_FUNCTIONS).
5. `SETTINGS`/`FORMAT` dari pengguna ditolak; guard membungkus query dengan LIMIT <= max dan
   `SETTINGS max_execution_time`.
6. SQL yang dieksekusi adalah hasil regenerasi sqlglot TANPA komentar, bukan teks asli dari LLM.

Lapisan kedua (wajib): user ClickHouse read-only dengan profil `text2sql_readonly`
(infra/clickhouse/access/text2sql-access.sql).
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

DIALECT = "clickhouse"
MAX_SQL_CHARS = 10_000

BLOCKED_FUNCTIONS = frozenset(
    {
        # table function / akses eksternal
        "file",
        "url",
        "input",
        "executable",
        "format",
        "view",
        "merge",
        "loop",
        "values",
        "null",
        "generaterandom",
        "fuzzjson",
        "jdbc",
        "odbc",
        "mysql",
        "postgresql",
        "mongodb",
        "redis",
        "sqlite",
        "hive",
        "dictionary",
        # efek samping / DoS
        "sleep",
        "sleepeachrow",
        "throwif",
        # introspeksi server
        "version",
        "currentuser",
        "current_user",
        "session_user",
        "current_version",
        "current_database",
        "current_schema",
        "current_catalog",
        "currentdatabase",
        "currentprofiles",
        "currentroles",
        "enabledprofiles",
        "enabledroles",
        "defaultroles",
        "hostname",
        "fqdn",
        "uptime",
        "getmacro",
        "getsetting",
        "buildid",
        "serveruuid",
        "tcpport",
        "displayname",
        "hascolumnintable",
        "filesystemavailable",
        "filesystemcapacity",
        "filesystemunreserved",
        "demangle",
    },
)
BLOCKED_FUNCTION_PREFIXES = (
    "s3",
    "hdfs",
    "azure",
    "gcs",
    "oss",
    "cosn",
    "remote",
    "cluster",
    "numbers",
    "zeros",
    "dict",
    "joinget",
    "addressto",
    "iceberg",
    "deltalake",
    "hudi",
    "urlcluster",
    "filecluster",
)

_FORBIDDEN_NODE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Create",
    "Drop",
    "Alter",
    "AlterTable",
    "TruncateTable",
    "Command",
    "Merge",
    "Into",
    "Set",
    "Use",
    "Grant",
    "Revoke",
    "Pragma",
    "Transaction",
    "Commit",
    "Rollback",
    "LoadData",
    "Copy",
    "Kill",
    "Describe",
    "Show",
    "Attach",
    "Detach",
    "Cache",
    "Uncache",
    "Refresh",
)
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name) for name in _FORBIDDEN_NODE_NAMES if isinstance(getattr(exp, name, None), type)
)
ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name) for name in ("Select", "Union", "Intersect", "Except") if hasattr(exp, name)
)
FORBIDDEN_CLAUSES = ("settings", "format", "into")


class SqlGuardError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class GuardConfig:
    allowed_tables: frozenset[str]  # "database.table", huruf kecil
    # Nama tabel dasar (tanpa database, huruf kecil) yang tidak boleh dipakai sebagai nama CTE (anti-shadowing).
    protected_tables: frozenset[str] = frozenset()
    default_database: str = "efn_dwh"
    max_limit: int = 1000
    max_execution_time: int = 15


@dataclass(frozen=True)
class GuardResult:
    sql: str
    tables: tuple[str, ...]
    limit: int


def _function_names(node: exp.Func) -> set[str]:
    """Nama kanonik sqlglot + nama asli (untuk fungsi yang tidak dikenal sqlglot / agregat anonim)."""
    names = {node.sql_name().lower()}
    raw = node.args.get("this")
    if isinstance(raw, str):
        names.add(raw.lower())
    if isinstance(node, exp.Anonymous):
        names.add(str(node.name).lower())
    return names


def _blocked_function(node: exp.Func) -> str | None:
    for name in _function_names(node):
        if name in BLOCKED_FUNCTIONS or name.startswith(BLOCKED_FUNCTION_PREFIXES):
            return name
    return None


def _parse_single(sql: str) -> exp.Expression:
    if not sql or not sql.strip():
        raise SqlGuardError("empty", "SQL kosong")
    if len(sql) > MAX_SQL_CHARS:
        raise SqlGuardError("too_long", "SQL terlalu panjang")
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except (SqlglotError, ValueError, RecursionError) as exc:
        raise SqlGuardError("parse_error", "SQL tidak bisa di-parse") from exc
    if len(statements) != 1:
        raise SqlGuardError("multiple_statements", "hanya satu statement yang diizinkan")
    return statements[0]


def _check_sources(root: exp.Expression) -> None:
    """Sumber FROM/JOIN hanya boleh tabel bernama atau subquery (bukan table function/VALUES/ARRAY JOIN)."""
    for node in root.find_all(exp.From, exp.Join):
        source = node.this
        if isinstance(source, exp.Table):
            if not isinstance(source.this, exp.Identifier):
                raise SqlGuardError("table_function", "table function tidak diizinkan")
        elif not isinstance(source, exp.Subquery):
            raise SqlGuardError("source_not_allowed", "sumber data tidak diizinkan")


def _check_in_predicates(root: exp.Expression) -> None:
    """`IN` hanya subquery atau daftar konstanta (SEC-04 vektor 2).

    ClickHouse menerima `x IN nama_tabel` (sqlglot mem-parse sisi kanan sebagai kolom, bukan Table) sehingga
    tabel itu tidak pernah masuk pemeriksaan whitelist. Daftar yang memuat kolom juga ditolak (konservatif).
    """
    for node in root.find_all(exp.In):
        if node.args.get("field") is not None or node.args.get("unnest") is not None:
            raise SqlGuardError("in_not_allowed", "IN hanya boleh berisi subquery atau daftar nilai")
        query = node.args.get("query")
        expressions = node.args.get("expressions") or []
        if query is not None:
            if not isinstance(query, exp.Subquery | exp.Query):
                raise SqlGuardError("in_not_allowed", "IN hanya boleh berisi subquery atau daftar nilai")
            continue
        if not expressions:
            raise SqlGuardError("in_not_allowed", "IN hanya boleh berisi subquery atau daftar nilai")
        for item in expressions:
            if isinstance(item, exp.Subquery | exp.Query):
                continue
            if any(isinstance(n, exp.Column | exp.Table) for n in item.walk()):
                raise SqlGuardError("in_not_allowed", "IN hanya boleh berisi subquery atau daftar nilai")


class _ScopeChecker:
    """Validasi tabel dengan resolusi CTE per scope (SEC-04 vektor 1)."""

    def __init__(self, config: GuardConfig) -> None:
        self._config = config
        self._reserved = {q.split(".", 1)[1] for q in config.allowed_tables if "." in q} | set(
            config.protected_tables,
        )
        self.used: list[str] = []

    def walk(self, node: exp.Expression, visible: frozenset[str]) -> None:
        if isinstance(node, exp.Table):
            self._check_table(node, visible)
            return
        with_ = node.args.get("with") if isinstance(node, exp.Query) else None
        if isinstance(with_, exp.With):
            self._walk_query_with_ctes(node, with_, visible)
            return
        self._walk_children(node, visible, skip=())

    def _walk_children(self, node: exp.Expression, visible: frozenset[str], skip: tuple[str, ...]) -> None:
        for key, value in node.args.items():
            if key in skip:
                continue
            for child in value if isinstance(value, list) else [value]:
                if isinstance(child, exp.Expression):
                    self.walk(child, visible)

    def _walk_query_with_ctes(self, query: exp.Expression, with_: exp.With, visible: frozenset[str]) -> None:
        if with_.args.get("recursive"):
            raise SqlGuardError("cte_not_allowed", "WITH RECURSIVE tidak diizinkan")
        names = visible
        for cte in with_.expressions:
            if not isinstance(cte, exp.CTE):
                raise SqlGuardError("cte_not_allowed", "bentuk WITH tidak dikenali")
            # Badan CTE hanya melihat CTE sebelumnya, BUKAN dirinya sendiri (non-recursive): referensi ke nama
            # sendiri = tabel dasar, sehingga diperiksa whitelist.
            self.walk(cte.this, names)
            if cte.args.get("scalar"):
                # `WITH <ekspresi> AS x` (ClickHouse): alias skalar, bukan sumber tabel -> tidak menutupi tabel.
                continue
            alias = str(cte.alias or "").lower()
            if not alias:
                raise SqlGuardError("cte_not_allowed", "CTE tanpa nama")
            if alias in self._reserved:
                raise SqlGuardError("cte_shadowing", f"nama CTE {alias} bentrok dengan tabel/view")
            names = names | {alias}
        if isinstance(query, exp.Union | exp.Intersect | exp.Except):
            # Konservatif: CTE di depan set operation hanya dianggap terlihat di cabang kiri.
            self.walk(query.this, names)
            self._walk_children(query, visible, skip=("with", "this"))
            return
        self._walk_children(query, names, skip=("with",))

    def _check_table(self, table: exp.Table, visible: frozenset[str]) -> None:
        if not isinstance(table.this, exp.Identifier):
            raise SqlGuardError("table_function", "table function tidak diizinkan")
        if table.catalog:
            raise SqlGuardError("table_not_allowed", "referensi catalog tidak diizinkan")
        name = table.name.lower()
        database = table.db.lower()
        if not database and name in visible:
            return
        qualified = f"{database or self._config.default_database}.{name}"
        if qualified not in self._config.allowed_tables:
            raise SqlGuardError("table_not_allowed", f"tabel {qualified} tidak ada di semantic layer")
        self.used.append(qualified)
        # Argumen lain Table (alias, sample, hint) tidak memuat sumber data.


def _check_tables(root: exp.Expression, config: GuardConfig) -> tuple[str, ...]:
    checker = _ScopeChecker(config)
    try:
        checker.walk(root, frozenset())
    except RecursionError as exc:
        raise SqlGuardError("too_complex", "query terlalu bersarang") from exc
    if not checker.used:
        # Query tanpa tabel whitelist (mis. SELECT 1, atau CTE konstanta) tidak berguna dan rawan probing fungsi.
        raise SqlGuardError("no_table", "query harus membaca view semantic layer")
    return tuple(dict.fromkeys(checker.used))


def guard_sql(sql: str, config: GuardConfig, limit: int | None = None) -> GuardResult:
    """Validasi SQL dari LLM dan kembalikan SQL aman untuk dieksekusi. Melempar SqlGuardError bila ditolak."""
    root = _parse_single(sql)
    if not isinstance(root, ALLOWED_ROOTS):
        raise SqlGuardError("not_select", "hanya SELECT/WITH yang diizinkan")

    for node in root.find_all(exp.Expression):
        if isinstance(node, FORBIDDEN_NODES):
            raise SqlGuardError("forbidden_statement", f"{type(node).__name__} tidak diizinkan")
        if isinstance(node, ALLOWED_ROOTS) and any(node.args.get(clause) for clause in FORBIDDEN_CLAUSES):
            raise SqlGuardError("forbidden_clause", "SETTINGS/FORMAT tidak boleh ditulis di query")
        if isinstance(node, exp.Func):
            blocked = _blocked_function(node)
            if blocked:
                raise SqlGuardError("function_not_allowed", f"fungsi {blocked} tidak diizinkan")

    _check_sources(root)
    _check_in_predicates(root)
    tables = _check_tables(root, config)

    effective = config.max_limit if limit is None else max(1, min(int(limit), config.max_limit))
    try:
        inner = root.sql(dialect=DIALECT, comments=False)
    except SqlglotError as exc:
        raise SqlGuardError("generate_error", "SQL tidak bisa dinormalisasi") from exc
    # Identifier & angka berasal dari AST tervalidasi dan konfigurasi int, bukan input mentah.
    safe_sql = (
        f"SELECT * FROM ({inner}) AS _guarded LIMIT {effective} "  # noqa: S608
        f"SETTINGS max_execution_time = {int(config.max_execution_time)}"
    )
    return GuardResult(sql=safe_sql, tables=tables, limit=effective)
