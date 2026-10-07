#!/usr/bin/env python3
"""Render odoo.conf dari template + Docker secrets (SEC-15, docs/04 §7).

Kenapa Python, bukan `sed`/`envsubst`:
- Nilai rahasia TIDAK pernah disisipkan ke teks/regex. Template dibaca dengan
  `configparser.RawConfigParser` (tanpa interpolasi `%`), nilai di-set sebagai objek,
  lalu ditulis ulang oleh parser yang sama dengan yang dipakai Odoo 19
  (`odoo/tools/config.py` memakai `RawConfigParser`). Karakter `| & / \\ % ; # $` aman.
- Satu-satunya karakter yang tidak bisa direpresentasikan oleh format INI adalah baris
  baru/karakter kontrol dan spasi di awal/akhir (di-strip parser saat dibaca) -> DITOLAK
  dengan pesan jelas, bukan diam-diam dirusak. Nilai literal `False`/`false`/`None`
  juga ditolak karena Odoo 19 memperlakukannya khusus (opsi di-skip / menjadi None).

Kebijakan (gagal start, tidak ada fallback diam-diam):
- `ODOO_ADMIN_PASSWORD_FILE` wajib ada, terbaca, panjang >= 16. Disimpan sebagai hash
  pbkdf2_sha512 (skema yang sama dengan `crypt_context` Odoo 19), bukan plaintext.
- Password DB hanya dari `PASSWORD_FILE`; env `PASSWORD`/`PGPASSWORD` ditolak (secret di env
  terlihat di /proc/<pid>/environ dan `docker inspect`). Password DB ditulis ke odoo.conf
  (mode 0600), TIDAK sebagai argumen CLI (entrypoint resmi memakai `--db_password`,
  terlihat di `ps`).
- `db_sslmode = verify-full` + CA di `PGSSLROOTCERT` (default `EFN_CA_FILE`) wajib terbaca.
- `http_interface` default 127.0.0.1 (pola sidecar TLS ghostunnel); antarmuka non-loopback
  berarti HTTP plaintext di jaringan container -> hanya boleh dengan
  `EFN_ALLOW_PLAINTEXT=true` di APP_ENV non-produksi.
- User DB superuser `postgres` ditolak (least privilege, docs/04 §7.3).
"""

import argparse
import configparser
import ipaddress
import os
import re
import sys
import tempfile
import time

TRUE_VALUES = {"1", "true", "yes", "y", "on"}
NON_PRODUCTION_ENVS = {"development", "dev", "local", "test", "ci"}
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")
SPECIAL_LITERALS = {"False", "false", "None"}
MIN_ADMIN_PASSWORD_LEN = 16

_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-.]{0,251}[A-Za-z0-9])?$")
_PG_USER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def log(msg):
    print(f"[efn-entrypoint] {msg}", file=sys.stderr, flush=True)


def fail(msg):
    log(f"FATAL: {msg}")
    sys.exit(1)


def env(name, default=None):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def plaintext_allowed():
    """`EFN_ALLOW_PLAINTEXT=true` hanya sah bila APP_ENV eksplisit non-produksi."""
    flag = (env("EFN_ALLOW_PLAINTEXT", "") or "").lower() in TRUE_VALUES
    app_env = (env("APP_ENV", "") or "").lower()
    if flag and app_env not in NON_PRODUCTION_ENVS:
        fail(
            "EFN_ALLOW_PLAINTEXT=true ditolak: APP_ENV harus salah satu "
            f"{sorted(NON_PRODUCTION_ENVS)} (nilai sekarang: {app_env or '<kosong>'})"
        )
    if flag:
        log(f"PERINGATAN: mode plaintext diizinkan (APP_ENV={app_env}); JANGAN dipakai di produksi")
    return flag


def check_ini_value(name, value):
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        fail(f"{name} mengandung baris baru/karakter kontrol (tidak dapat ditulis aman ke odoo.conf)")
    if value != value.strip():
        fail(f"{name} diawali/diakhiri spasi (akan terpotong oleh parser odoo.conf)")
    if value in SPECIAL_LITERALS:
        fail(f"{name} bernilai literal {value!r} yang diperlakukan khusus oleh Odoo")
    return value


def read_secret_file(file_env):
    path = env(file_env)
    if not path:
        fail(f"env {file_env} wajib diisi (secret dari /run/secrets/*)")
    try:
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        fail(f"secret yang ditunjuk {file_env} tidak dapat dibaca ({type(exc).__name__})")
    # Hanya buang newline penutup (gaya `echo > file`); isi lain dipertahankan apa adanya.
    value = raw.rstrip("\r\n")
    if not value:
        fail(f"secret yang ditunjuk {file_env} kosong")
    return check_ini_value(file_env, value)


def check_ca_file(path, purpose):
    try:
        with open(path, encoding="ascii", errors="replace") as handle:
            content = handle.read()
    except OSError as exc:
        fail(f"CA untuk {purpose} tidak terbaca di {path} ({type(exc).__name__})")
    if "BEGIN CERTIFICATE" not in content:
        fail(f"CA untuk {purpose} di {path} bukan sertifikat PEM")


def hash_admin_password(password):
    # SEC-30: tidak ada fallback plaintext. passlib adalah dependensi wajib Odoo 19 (image
    # resmi: paket python3-passlib); bila hilang, render GAGAL agar admin_passwd tidak pernah
    # ditulis tanpa hash.
    try:
        from passlib.context import CryptContext
    except ImportError:
        fail("passlib tidak tersedia; admin_passwd tidak boleh ditulis tanpa hash (SEC-30)")
    return CryptContext(schemes=["pbkdf2_sha512"], pbkdf2_sha512__rounds=600_000).hash(password)


def int_env(name, default, minimum, maximum):
    raw = env(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        fail(f"{name} harus bilangan bulat")
    if not minimum <= value <= maximum:
        fail(f"{name} harus di antara {minimum}..{maximum}")
    return value


def resolve_db(allow_plaintext):
    for forbidden, file_env in (
        ("PASSWORD", "PASSWORD_FILE"),
        ("PGPASSWORD", "PASSWORD_FILE"),
        ("ODOO_ADMIN_PASSWORD", "ODOO_ADMIN_PASSWORD_FILE"),
    ):
        if os.environ.get(forbidden):
            fail(f"env {forbidden} dilarang; gunakan {file_env} (Docker secret)")

    host = env("HOST", "postgres")
    if not _HOST_RE.match(host):
        fail("HOST bukan hostname yang valid")
    port = int_env("PORT", 5432, 1, 65535)
    user = env("DB_USER") or env("USER") or "efn_odoo"
    if not _PG_USER_RE.match(user):
        fail("user DB (DB_USER/USER) tidak valid")
    if user == "postgres":
        fail("user DB superuser 'postgres' dilarang untuk aplikasi (docs/04 §7.3); gunakan efn_odoo")
    password = read_secret_file("PASSWORD_FILE")

    sslmode = "verify-full"
    override = env("ODOO_DB_SSLMODE")
    if override and override != sslmode:
        if not allow_plaintext:
            fail("ODOO_DB_SSLMODE selain verify-full hanya boleh dengan EFN_ALLOW_PLAINTEXT=true (non-produksi)")
        if override not in SSL_MODES:
            fail(f"ODOO_DB_SSLMODE harus salah satu {SSL_MODES}")
        sslmode = override
    # Odoo 19: env PGSSLMODE mengalahkan nilai di odoo.conf (ChainMap env > file).
    pg_sslmode_env = env("PGSSLMODE")
    if pg_sslmode_env and pg_sslmode_env != sslmode:
        fail(f"env PGSSLMODE={pg_sslmode_env} bertentangan dengan db_sslmode={sslmode}")

    if sslmode in ("verify-ca", "verify-full"):
        root_cert = env("PGSSLROOTCERT")
        if not root_cert:
            fail("PGSSLROOTCERT wajib diisi untuk db_sslmode verify-full")
        check_ca_file(root_cert, "PostgreSQL (PGSSLROOTCERT)")
    return {"host": host, "port": port, "user": user, "password": password, "sslmode": sslmode}


def resolve_http_interface(allow_plaintext):
    value = env("ODOO_HTTP_INTERFACE", "127.0.0.1")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        fail("ODOO_HTTP_INTERFACE harus alamat IP (mis. 127.0.0.1)")
    if not address.is_loopback and not allow_plaintext:
        fail(
            "ODOO_HTTP_INTERFACE non-loopback berarti HTTP plaintext di jaringan container; "
            "pakai sidecar TLS (127.0.0.1) atau EFN_ALLOW_PLAINTEXT=true di non-produksi"
        )
    return value


def wait_for_db(db, timeout):
    if timeout <= 0:
        return
    try:
        import psycopg2
    except ImportError:
        log("PERINGATAN: psycopg2 tidak tersedia; lewati tunggu-DB")
        return
    deadline = time.monotonic() + timeout
    last_error = "?"
    while time.monotonic() < deadline:
        try:
            conn = psycopg2.connect(
                host=db["host"],
                port=db["port"],
                user=db["user"],
                password=db["password"],
                dbname=env("ODOO_DB_WAIT_DBNAME", "postgres"),
                sslmode=db["sslmode"],
                connect_timeout=5,
            )
            conn.close()
            log(f"PostgreSQL {db['host']}:{db['port']} terjangkau (sslmode={db['sslmode']})")
            return
        except psycopg2.OperationalError as exc:
            # Pesan libpq tidak memuat password; dipotong agar log ringkas.
            last_error = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else type(exc).__name__
            time.sleep(2)
    fail(f"PostgreSQL tidak terjangkau dalam {timeout}s: {last_error}")


def render(template_path, output_path):
    allow_plaintext = plaintext_allowed()
    db = resolve_db(allow_plaintext)
    http_interface = resolve_http_interface(allow_plaintext)
    admin_password = read_secret_file("ODOO_ADMIN_PASSWORD_FILE")
    if len(admin_password) < MIN_ADMIN_PASSWORD_LEN:
        fail(f"master password Odoo minimal {MIN_ADMIN_PASSWORD_LEN} karakter")

    parser = configparser.RawConfigParser(interpolation=None)
    try:
        with open(template_path, encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as exc:
        fail(f"template {template_path} tidak valid ({type(exc).__name__})")
    if not parser.has_section("options"):
        fail("template tidak punya section [options]")
    for key in ("admin_passwd", "db_password", "db_user", "db_host"):
        if parser.has_option("options", key):
            fail(f"template tidak boleh memuat '{key}' (diisi entrypoint dari env/secret)")

    values = {
        "admin_passwd": hash_admin_password(admin_password),
        "db_host": db["host"],
        "db_port": str(db["port"]),
        "db_user": db["user"],
        "db_password": db["password"],
        "db_sslmode": db["sslmode"],
        "http_interface": http_interface,
        "proxy_mode": "True",
        "workers": str(int_env("ODOO_WORKERS", 2, 0, 64)),
        "max_cron_threads": str(int_env("ODOO_MAX_CRON_THREADS", 1, 0, 16)),
    }
    for key, value in values.items():
        parser.set("options", key, value)

    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".odoo.conf.", dir=out_dir)  # mode 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("; DIBUAT OTOMATIS oleh render_odoo_conf.py saat start. Jangan diedit.\n")
            parser.write(handle)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, output_path)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        fail(f"gagal menulis {output_path} ({type(exc).__name__})")

    log(
        f"odoo.conf dirender: db={db['user']}@{db['host']}:{db['port']} sslmode={db['sslmode']} "
        f"http_interface={http_interface} proxy_mode=True"
    )
    wait_for_db(db, int_env("ODOO_DB_WAIT_TIMEOUT", 30, 0, 600))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--template", default="/etc/odoo/odoo.conf.tpl")
    parser.add_argument("--output", default=os.environ.get("ODOO_RC", "/etc/odoo/odoo.conf"))
    args = parser.parse_args()
    render(args.template, args.output)


if __name__ == "__main__":
    main()
