"""Dekode gambar dan perceptual hash. BFF sudah re-encode JPEG <= 2048 px dan strip EXIF (docs/04 §3.3)."""

import io

import imagehash
from PIL import Image, ImageOps, UnidentifiedImageError

from efn_ocr.errors import UnreadableImageError

# Perlindungan decompression bomb (R-S06); 40 MP jauh di atas 2048x2048 hasil re-encode BFF.
Image.MAX_IMAGE_PIXELS = 40_000_000


def load_image(data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            return img.convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise UnreadableImageError(type(exc).__name__) from exc


def compute_phash(image: Image.Image) -> str:
    """pHash 64-bit -> 16 karakter hex, cocok dengan kolom `image_phash char(16)`."""
    return str(imagehash.phash(image))


def prepare_for_ocr(image: Image.Image) -> Image.Image:
    """Grayscale + autocontrast; perbesar foto kecil agar Tesseract membaca angka harga lebih stabil."""
    gray = ImageOps.grayscale(image)
    longest = max(gray.size)
    if 0 < longest < 1200:
        factor = 1200 / longest
        gray = gray.resize((round(gray.width * factor), round(gray.height * factor)), Image.Resampling.LANCZOS)
    return ImageOps.autocontrast(gray)
