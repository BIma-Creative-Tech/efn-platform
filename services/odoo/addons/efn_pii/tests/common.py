"""Helper test efn_pii: kunci dibangkitkan saat test di folder tmp (tidak pernah di repo)."""

import base64
import os
import shutil
import tempfile
from unittest.mock import patch

from odoo.tools import SQL

from odoo.addons.efn_pii.models import keyring as kr

KEYRING_LOGGER = kr._logger.name


def _write_key(path, key):
    with open(path, "w", encoding="ascii") as handle:
        handle.write(base64.b64encode(key).decode("ascii") + "\n")


class PiiKeyMixin:
    """Memasang key ring uji lewat env (EFN_ODOO_PII_KEY_DIR) + reset cache per test."""

    def _pii_env(self, keys=None, bidx_key=None, active=1, with_bidx=True, raw_files=None, extra_env=None):
        """:returns: (folder, dict versi->kunci, kunci blind index atau None)."""
        folder = tempfile.mkdtemp(prefix="efn-pii-test-")
        self.addCleanup(shutil.rmtree, folder, True)
        if keys is None:
            keys = {1: os.urandom(32)}
        for version, key in keys.items():
            _write_key(os.path.join(folder, f"odoo_pii_key_v{version}"), key)
        if with_bidx:
            bidx_key = bidx_key or os.urandom(32)
            _write_key(os.path.join(folder, kr.BIDX_FILE_NAME), bidx_key)
        else:
            bidx_key = None
        for name, content in (raw_files or {}).items():
            with open(os.path.join(folder, name), "w", encoding="ascii") as handle:
                handle.write(content)
        env = {k: v for k, v in os.environ.items() if not k.startswith("EFN_ODOO_PII_")}
        env[kr.ENV_KEY_DIR] = folder
        env[kr.ENV_ACTIVE_VERSION] = str(active)
        env.update(extra_env or {})
        env_patch = patch.dict(os.environ, env, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        # pulihkan cache proses setelah test (bukan reset): test modul lain yang mencari
        # partner by phone tidak memuat ulang key ring dan tidak mencetak log tambahan
        self.addCleanup(kr.restore_keyring_cache, kr.snapshot_keyring_cache())
        kr.reset_keyring_cache()
        return folder, keys, bidx_key

    def _pii_locked(self, **kwargs):
        """Mode terkunci: folder kosong (atau sebagian). Log pemuatan (WARNING/CRITICAL) diserap
        di sini agar tidak muncul sebagai baris log di keluaran test."""
        kwargs.setdefault("keys", {})
        kwargs.setdefault("with_bidx", False)
        result = self._pii_env(**kwargs)
        with self.assertLogs(KEYRING_LOGGER, "WARNING") as logs:
            ring = kr.get_keyring(self.env.cr.dbname)
        self.assertFalse(ring.can_encrypt)
        return result, logs

    def _raw(self, partner_id, *columns):
        """Nilai mentah kolom di PostgreSQL (setelah flush), tanpa ORM."""
        self.env.flush_all()
        self.env.cr.execute(
            SQL(
                "SELECT %s FROM res_partner WHERE id = %s",
                SQL(", ").join(SQL.identifier(c) for c in columns),
                partner_id,
            )
        )
        return dict(zip(columns, self.env.cr.fetchone(), strict=True))
