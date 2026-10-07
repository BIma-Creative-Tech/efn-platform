"""Dekode gambar dan perceptual hash. BFF sudah re-encode JPEG <= 2048 px dan strip EXIF (docs/04 §3.3)."""

import io

import imagehash
from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError

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


# Qwen2.5-VL memotong gambar menjadi patch 28x28 px efektif (patch 14 px + merge 2x2) -> 1 token per patch.
VLM_PATCH_PX = 28
VLM_BORDER_TOLERANCE = 12  # selisih nilai piksel (0..255) yang masih dianggap "latar polos" saat trim tepi
VLM_BORDER_PAD = 16


def estimate_vlm_image_tokens(width: int, height: int) -> int:
    """Perkiraan token gambar Qwen2.5-VL (dipakai untuk validasi num_ctx dan dokumentasi)."""
    return max(1, -(-width // VLM_PATCH_PX)) * max(1, -(-height // VLM_PATCH_PX))


def trim_uniform_border(image: Image.Image) -> Image.Image:
    """Buang tepi berwarna polos (latar foto produk/label sintetis). Foto nyata tanpa tepi polos tidak berubah.

    Warna latar = piksel kiri-atas; area yang berbeda > toleransi dipertahankan + padding. Tidak memotong bila hasil
    terlalu kecil (< 1/4 sisi) agar label kecil tidak hilang karena noise.
    """
    background = Image.new(image.mode, image.size, image.getpixel((0, 0)))
    diff = ImageChops.difference(image, background).convert("L").point(lambda v: 255 if v > VLM_BORDER_TOLERANCE else 0)
    box = diff.getbbox()
    if box is None:
        return image
    left, top, right, bottom = box
    left, top = max(0, left - VLM_BORDER_PAD), max(0, top - VLM_BORDER_PAD)
    right, bottom = min(image.width, right + VLM_BORDER_PAD), min(image.height, bottom + VLM_BORDER_PAD)
    if (right - left) < image.width // 4 or (bottom - top) < image.height // 4:
        return image
    return image.crop((left, top, right, bottom))


def encode_for_vlm(image: Image.Image, max_side: int, quality: int, trim: bool = False) -> bytes:
    """Gambar untuk VLM tahap 2: (opsional) trim tepi polos, perkecil ke sisi terpanjang `max_side` (tidak pernah
    memperbesar), JPEG `quality`. Token gambar turun kira-kira kuadratik terhadap skala (G12, lihat README)."""
    prepared = image.convert("RGB")
    if trim:
        prepared = trim_uniform_border(prepared)
    if max(prepared.size) > max_side:
        prepared = prepared.copy()
        prepared.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    prepared.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()
