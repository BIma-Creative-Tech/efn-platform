# Data-only (ADR-031, ADR-045): tidak ada model dan tidak ada hook.
# Tahap B (19.0.2.0.0) menghapus post_init_hook tahap A: efn_is_dc DC kini diisi di data/stock_config_data.xml karena
# efn_assortment sudah menjadi depends. Satu-satunya Python = migrations/19.0.2.0.0/post-migrate.py (upgrade dari 1.x).
