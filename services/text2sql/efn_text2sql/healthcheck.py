"""Healthcheck container: GET https://127.0.0.1:<port>/healthz (sertifikat tidak diverifikasi di loopback)."""

import os
import ssl
import sys
import urllib.request


def main() -> int:
    port = int(os.environ.get("TEXT2SQL_PORT", "8443"))
    tls = bool(os.environ.get("TLS_CERT_FILE"))
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # loopback di namespace container sendiri
    url = f"{'https' if tls else 'http'}://127.0.0.1:{port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=4, context=ctx if tls else None) as resp:  # noqa: S310
            return 0 if resp.status == 200 else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
