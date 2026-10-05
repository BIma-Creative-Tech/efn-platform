"""Healthcheck container: `python -m efn_ocr.healthcheck` -> GET /metrics di loopback (exit 0 = sehat).

Bila `METRICS_TLS_CERT_FILE` di-set, endpoint HTTPS: rantai sertifikat diverifikasi terhadap CA internal
(`METRICS_TLS_CA_FILE`, default `/run/secrets/efn_ca_crt`). Pemeriksaan hostname dimatikan karena koneksi ke
127.0.0.1 (sertifikat diterbitkan untuk `ocr-worker`); ini hanya probe loopback di dalam container yang sama.
Bila `METRICS_TLS_CLIENT_CA_FILE` di-set (mTLS, docs/04 §8.5), probe membawa sertifikat worker sendiri
(`METRICS_TLS_CERT_FILE`/`METRICS_TLS_KEY_FILE`; EKU server+client dari gen-pki.sh) sebagai sertifikat klien.
"""

import os
import ssl
import sys
import urllib.request

from efn_ocr.tls import DEFAULT_CA_FILE


def main() -> int:
    port = int(os.getenv("METRICS_PORT") or "9100")
    cert = os.getenv("METRICS_TLS_CERT_FILE")
    if cert:
        context = ssl.create_default_context(cafile=os.getenv("METRICS_TLS_CA_FILE") or DEFAULT_CA_FILE)
        context.check_hostname = False
        if os.getenv("METRICS_TLS_CLIENT_CA_FILE"):
            context.load_cert_chain(cert, os.getenv("METRICS_TLS_KEY_FILE") or None)
        url = f"https://127.0.0.1:{port}/metrics"
    else:
        context = None
        url = f"http://127.0.0.1:{port}/metrics"
    try:
        with urllib.request.urlopen(url, timeout=3, context=context) as resp:  # noqa: S310 - URL konstan loopback
            return 0 if resp.status == 200 else 1
    except Exception:  # healthcheck: error apa pun = tidak sehat
        return 1


if __name__ == "__main__":
    sys.exit(main())
