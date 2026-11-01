"""Entry point: `python -m efn_text2sql` (docs/04 §23)."""

from __future__ import annotations

import logging

from efn_text2sql.clickhouse import ClickHouseClient
from efn_text2sql.config import Settings
from efn_text2sql.llm import build_llm
from efn_text2sql.semantic import load_semantic_layer
from efn_text2sql.server import build_server
from efn_text2sql.service import AskService


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    layer = load_semantic_layer(settings.semantic_layer_path)
    executor = ClickHouseClient(
        settings.ch_url,
        settings.ch_user,
        settings.ch_password,
        settings.ch_ca_file or None,
        database=layer.database,
    )
    llm = build_llm(settings)
    service = AskService(layer, llm, executor, settings.scope_key, settings.enabled)
    httpd = build_server(
        service,
        settings.api_key,
        settings.bind,
        settings.port,
        settings.tls_cert_file,
        settings.tls_key_file,
    )
    logging.getLogger("efn_text2sql").info(
        "text2sql mulai: enabled=%s llm=%s https=%s port=%d",
        settings.enabled,
        llm.name,
        bool(settings.tls_cert_file),
        settings.port,
    )
    httpd.serve_forever()


if __name__ == "__main__":
    main()
