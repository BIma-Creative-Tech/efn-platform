"""Helper test efn_pii: kunci dibangkitkan saat test di folder tmp (tidak pernah di repo)."""

import base64
import os
import shutil
import tempfile
from unittest.mock import patch

from odoo.tools import SQL

from odoo.addons.efn_pii.models import keyring as kr

KEYRING_LOGGER = kr._logger.name
V1 = kr.PREFIX
V2 = kr.PREFIX_V2


def _write_key(path, key):
    with open(path, "w", encoding="ascii") as handle:
        handle.write(base64.b64encode(key).decode("ascii") + "\n")


def make_key_dir(keys=None, bidx_key=None, name_key=None, with_bidx=True, with_name=True, raw_files=None):
    """:returns: (folder, keys, bidx_key|None, name_key|None). Pemanggil wajib menghapus folder."""
    folder = tempfile.mkdtemp(prefix="efn-pii-test-")
    if keys is None:
        keys = {1: os.urandom(32)}
    for version, key in keys.items():
        _write_key(os.path.join(folder, f"odoo_pii_key_v{version}"), key)
    if with_bidx:
        bidx_key = bidx_key or os.urandom(32)
        _write_key(os.path.join(folder, kr.BIDX_FILE_NAME), bidx_key)
    else:
        bidx_key = None
    if with_name:
        name_key = name_key or os.urandom(32)
        _write_key(os.path.join(folder, kr.NAME_FILE_NAME), name_key)
    else:
        name_key = None
    for name, content in (raw_files or {}).items():
        with open(os.path.join(folder, name), "w", encoding="ascii") as handle:
            handle.write(content)
    return folder, keys, bidx_key, name_key


def key_env(folder, active=1, extra_env=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("EFN_ODOO_PII_")}
    env[kr.ENV_KEY_DIR] = folder
    env[kr.ENV_ACTIVE_VERSION] = str(active)
    env.update(extra_env or {})
    return env


class PiiKeyMixin:
    """Memasang key ring uji lewat env (EFN_ODOO_PII_KEY_DIR) + reset cache per test."""

    @classmethod
    def _pii_class_env(cls):
        """Kunci untuk seluruh kelas (dipakai bila setUpClass sudah membuat partner perorangan)."""
        folder, keys, bidx_key, name_key = make_key_dir()
        cls.addClassCleanup(shutil.rmtree, folder, True)
        env_patch = patch.dict(os.environ, key_env(folder), clear=True)
        env_patch.start()
        cls.addClassCleanup(env_patch.stop)
        cls.addClassCleanup(kr.restore_keyring_cache, kr.snapshot_keyring_cache())
        kr.reset_keyring_cache()
        return keys, bidx_key, name_key

    def _pii_env(
        self,
        keys=None,
        bidx_key=None,
        active=1,
        with_bidx=True,
        raw_files=None,
        extra_env=None,
        name_key=None,
        with_name=True,
    ):
        """:returns: (folder, dict versi->kunci, kunci blind index atau None)."""
        folder, keys, bidx_key, name_key = make_key_dir(
            keys=keys,
            bidx_key=bidx_key,
            name_key=name_key,
            with_bidx=with_bidx,
            with_name=with_name,
            raw_files=raw_files,
        )
        self.addCleanup(shutil.rmtree, folder, True)
        self.pii_name_key = name_key
        env_patch = patch.dict(os.environ, key_env(folder, active, extra_env), clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        # pulihkan cache proses setelah test (bukan reset): test modul lain tidak memuat ulang key ring
        self.addCleanup(kr.restore_keyring_cache, kr.snapshot_keyring_cache())
        kr.reset_keyring_cache()
        return folder, keys, bidx_key

    def _pii_locked(self, **kwargs):
        """Mode terkunci: folder kosong (atau sebagian). Log pemuatan (WARNING/CRITICAL) diserap
        di sini agar tidak muncul sebagai baris log di keluaran test."""
        kwargs.setdefault("keys", {})
        kwargs.setdefault("with_bidx", False)
        kwargs.setdefault("with_name", False)
        result = self._pii_env(**kwargs)
        with self.assertLogs(KEYRING_LOGGER, "WARNING") as logs:
            ring = kr.get_keyring(self.env.cr.dbname)
        self.assertFalse(ring.can_encrypt)
        return result, logs

    def _raw_table(self, table, record_id, *columns):
        """Nilai mentah kolom di PostgreSQL (setelah flush), tanpa ORM."""
        self.env.flush_all()
        self.env.cr.execute(
            SQL(
                "SELECT %s FROM %s WHERE id = %s",
                SQL(", ").join(SQL.identifier(c) for c in columns),
                SQL.identifier(table),
                record_id,
            )
        )
        return dict(zip(columns, self.env.cr.fetchone(), strict=True))

    def _raw(self, partner_id, *columns):
        return self._raw_table("res_partner", partner_id, *columns)

    def assertBound(self, value, table, column, record_id, plaintext=None):
        """Ciphertext format 2 yang terikat id record ini (dekripsi dengan AAD id berhasil)."""
        self.assertTrue(isinstance(value, str) and value.startswith(V2), f"{table}.{column} bukan efnpii2")
        ring = kr.get_keyring(self.env.cr.dbname)
        plain = ring.decrypt_text(value, kr.aad_for(table, column, record_id))
        if plaintext is not None:
            self.assertEqual(plain, plaintext)
            self.assertNotIn(plaintext, value)
        return plain
