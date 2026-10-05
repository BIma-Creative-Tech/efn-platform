"""OCR tahap 1 (CPU).

Default: Tesseract (`tesseract-ocr` + data `ind`/`eng` dari apt Debian). Alasan: image tetap ramping (~40 MB
tambahan), tidak mengunduh model saat runtime (container tanpa egress internet), stabil di Python 3.12.
PaddleOCR (`OCR_ENGINE=paddle`) didukung secara opsional bila paket `paddleocr` 2.x + `paddlepaddle` dipasang
di image turunan; bila gagal di-import worker kembali ke Tesseract. Lihat README.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from PIL import Image

from efn_ocr.imaging import prepare_for_ocr

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float  # 0..1
    engine: str


class OcrEngine(Protocol):
    name: str

    def extract(self, image: Image.Image) -> OcrResult: ...


def aggregate_tesseract(texts: list[str], confs: list[object]) -> tuple[str, float]:
    """Gabungkan output `image_to_data`: buang token kosong/conf negatif, rata-rata confidence 0..1."""
    words: list[str] = []
    values: list[float] = []
    for raw_text, raw_conf in zip(texts, confs, strict=True):
        token = str(raw_text).strip()
        try:
            conf = float(str(raw_conf))
        except ValueError:
            continue
        if not token or conf < 0:
            continue
        words.append(token)
        values.append(min(conf, 100.0))
    confidence = sum(values) / len(values) / 100.0 if values else 0.0
    return " ".join(words), round(confidence, 4)


class TesseractEngine:
    name = "tesseract"

    def __init__(self, lang: str) -> None:
        import pytesseract

        self._pt = pytesseract
        self._lang = lang

    def extract(self, image: Image.Image) -> OcrResult:
        try:
            data = self._pt.image_to_data(
                prepare_for_ocr(image),
                lang=self._lang,
                config="--psm 11",
                output_type=self._pt.Output.DICT,
                timeout=30,
            )
        except (self._pt.TesseractNotFoundError, self._pt.TesseractError, RuntimeError, OSError) as exc:
            # Tesseract gagal -> confidence 0 sehingga tahap 2 (VLM) yang memutuskan.
            log.error("tesseract gagal: %s", type(exc).__name__)
            return OcrResult(text="", confidence=0.0, engine=self.name)
        text, confidence = aggregate_tesseract(list(data["text"]), list(data["conf"]))
        return OcrResult(text=text, confidence=confidence, engine=self.name)


class PaddleEngine:
    name = "paddleocr"

    def __init__(self) -> None:
        from paddleocr import PaddleOCR  # opsional; tidak ada di requirements.txt default

        self._ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)

    def extract(self, image: Image.Image) -> OcrResult:
        import numpy as np

        result = self._ocr.ocr(np.asarray(image.convert("RGB")), cls=True)
        lines = result[0] if result and result[0] else []
        texts = [str(line[1][0]) for line in lines]
        confs = [float(line[1][1]) for line in lines]
        confidence = sum(confs) / len(confs) if confs else 0.0
        return OcrResult(text=" ".join(texts), confidence=round(confidence, 4), engine=self.name)


def build_engine(engine: str, tesseract_lang: str) -> OcrEngine:
    if engine == "paddle":
        try:
            return PaddleEngine()
        except ImportError:
            log.warning("paddleocr tidak terpasang; kembali ke tesseract")
    return TesseractEngine(tesseract_lang)
