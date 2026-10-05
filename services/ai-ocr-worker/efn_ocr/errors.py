"""Exception domain worker. Dipisah agar modul logika murni tidak bergantung pada library I/O."""


class PoisonMessageError(ValueError):
    """Pesan tidak valid secara struktur/skema: langsung ke DLQ, tidak di-retry."""


class ImageNotFoundError(LookupError):
    """Objek foto tidak ada di MinIO (atau key di luar prefix yang diizinkan)."""


class UnreadableImageError(ValueError):
    """Foto tidak bisa didekode, terlalu besar, atau bukan gambar."""


class VlmUnavailableError(RuntimeError):
    """Ollama tidak bisa dihubungi, timeout, atau respons tidak bisa dipakai."""
