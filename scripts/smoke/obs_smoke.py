"""Smoke observability (gelombang 13c): query Loki & Alertmanager NYATA lewat mTLS (dev saja).

Dijalankan run-e2e-smoke.sh di container `python:3.12` (network efn_internal) dengan sertifikat klien Grafana
(`tls_grafana_*`, klien sah Loki dan Alertmanager) dan CA internal di-mount read-only dari `secrets/`. Standar library.

  python obs_smoke.py loki '<logql>' <menit>     -> SMOKE_RESULT {"lines": n, "streams": [label...], "sample": "..."}
  python obs_smoke.py alert <alertname> <label=nilai>   -> SMOKE_RESULT {"alerts": [{state, labels}]}
"""

import json
import ssl
import sys
import time
import urllib.parse
import urllib.request

CTX = ssl.create_default_context(cafile="/s/efn_ca_crt.txt")
CTX.load_cert_chain("/s/tls_grafana_crt.txt", "/s/tls_grafana_key.txt")


def get(url: str) -> dict:
    with urllib.request.urlopen(url, context=CTX, timeout=20) as r:  # noqa: S310 - URL internal tetap
        return json.load(r)


def loki(query: str, minutes: int) -> dict:
    now = time.time_ns()
    qs = urllib.parse.urlencode({"query": query, "start": now - minutes * 60 * 10**9, "end": now, "limit": 200})
    d = get("https://loki:3100/loki/api/v1/query_range?" + qs)
    lines, streams, sample = 0, [], ""
    for s in d["data"]["result"]:
        labels = {
            k: v for k, v in s["stream"].items() if k in ("stream", "loyalty_event", "kind", "severity", "recon_status")
        }
        streams.append(labels)
        lines += len(s["values"])
        if s["values"] and not sample:
            sample = s["values"][0][1][-200:]
    return {"lines": lines, "streams": streams, "sample": sample}


def alerts(name: str, matchers: list[str]) -> dict:
    d = get("https://alertmanager:9093/alertmanager/api/v2/alerts")
    out = []
    for a in d:
        lab = a.get("labels", {})
        if lab.get("alertname") != name:
            continue
        if all(lab.get(k) == v for k, v in (m.split("=", 1) for m in matchers)):
            out.append({"state": a["status"]["state"], "labels": lab})
    return {"alerts": out}


def main() -> None:
    cmd = sys.argv[1]
    try:
        res = loki(sys.argv[2], int(sys.argv[3])) if cmd == "loki" else alerts(sys.argv[2], sys.argv[3:])
    except Exception as exc:  # noqa: BLE001 - laporan smoke
        res = {"error": type(exc).__name__, "detail": str(exc)[:160]}
    print("SMOKE_RESULT " + json.dumps(res), flush=True)


if __name__ == "__main__":
    main()
