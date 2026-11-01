{
    "name": "EFN Tanya Data (Text-to-SQL)",
    "summary": "G38 (docs/04 §23, K25-01, Q-48): staf internal bertanya data operasional dalam bahasa Indonesia; "
    "service text2sql membuat SQL (9router/Ollama), guard sqlglot, ClickHouse efn_t2s dengan row policy per PT.",
    "version": "19.0.1.0.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031, CLAUDE.md).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["base", "mail", "efn_core"],
    "data": [
        "security/efn_data_ask_security.xml",
        "security/ir.model.access.csv",
        "views/efn_data_ask_views.xml",
    ],
    "installable": True,
    "application": False,
}
