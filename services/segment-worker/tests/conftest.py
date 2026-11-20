"""Helper data sintetis (tanpa data nyata/PII): subject_key = hex 64 deterministik."""

import hashlib


def key(i: int | str) -> str:
    return hashlib.sha256(f"subject-{i}".encode()).hexdigest()
