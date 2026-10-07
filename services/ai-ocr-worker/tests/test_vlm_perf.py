"""G12: persiapan gambar & batas token VLM (logika murni, tanpa Ollama)."""

import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from efn_ocr.config import Settings
from efn_ocr.imaging import encode_for_vlm, estimate_vlm_image_tokens, trim_uniform_border
from efn_ocr.vlm import OllamaVlm


def _tag(width: int = 1200, height: int = 800) -> Image.Image:
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([300, 200, 900, 600], fill="black")
    return img


def test_encode_for_vlm_downscales_longest_side_and_never_upscales() -> None:
    small = Image.open(io.BytesIO(encode_for_vlm(Image.new("RGB", (400, 300), "gray"), 768, 85)))
    assert small.size == (400, 300)
    noisy = Image.effect_noise((1600, 1200), 80).convert("RGB")  # tanpa tepi polos -> tidak di-trim
    out = Image.open(io.BytesIO(encode_for_vlm(noisy, 768, 85)))
    assert out.format == "JPEG"
    assert max(out.size) == 768
    assert out.size == (768, 576)


def test_trim_uniform_border_keeps_content_with_padding() -> None:
    trimmed = trim_uniform_border(_tag())
    assert trimmed.size == (600 + 1 + 32, 400 + 1 + 32)
    assert Image.open(io.BytesIO(encode_for_vlm(_tag(), 2048, 85))).size == (1200, 800)
    assert Image.open(io.BytesIO(encode_for_vlm(_tag(), 2048, 85, trim=True))).size == (633, 433)


def test_trim_uniform_border_ignores_blank_or_tiny_content() -> None:
    blank = Image.new("RGB", (500, 500), "white")
    assert trim_uniform_border(blank).size == (500, 500)
    speck = Image.new("RGB", (800, 800), "white")
    speck.putpixel((10, 10), (0, 0, 0))
    assert trim_uniform_border(speck).size == (800, 800)


def test_estimate_tokens_matches_measured_baseline() -> None:
    # Diukur di dev (G12): 1200x800 -> prompt_eval_count 1333 (gambar ~1247 + teks).
    assert estimate_vlm_image_tokens(1200, 800) == 43 * 29
    assert estimate_vlm_image_tokens(768, 512) == 28 * 19


class _Resp:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"response": '{"product_name": "Gula", "price": "Rp 15.900"}'}


class _Session:
    def __init__(self) -> None:
        self.payload: dict[str, Any] = {}
        self.verify: Any = None
        self.trust_env = True
        self.cert: Any = None

    def post(self, url: str, json: dict[str, Any], timeout: float) -> _Resp:
        self.payload = json
        return _Resp()


def test_ollama_payload_limits_tokens_and_keeps_model_loaded() -> None:
    session = _Session()
    vlm = OllamaVlm("http://ollama:11434", "qwen2.5vl:3b", 30, None, session=session, num_predict=96, num_ctx=2048)  # type: ignore[arg-type]
    result = vlm.extract(b"jpeg")
    assert result.price == 15900
    assert session.payload["options"] == {"temperature": 0, "num_predict": 96, "num_ctx": 2048}
    assert session.payload["keep_alive"] == "30m"
    assert session.payload["format"] == "json"


def _plain_env() -> dict[str, str]:
    return {
        "APP_ENV": "development",
        "EFN_ALLOW_PLAINTEXT": "true",
        "AMQP_URL": "amqp://efn-ocr@rabbitmq:5672",
        "MINIO_USE_SSL": "false",
        "OLLAMA_URL": "http://ollama:11434",
        "PG_SSLMODE": "disable",
    }


def test_settings_vlm_defaults_and_overrides() -> None:
    s = Settings.from_env(_plain_env())
    assert (s.vlm_max_side, s.vlm_jpeg_quality, s.ollama_num_predict, s.ollama_num_ctx) == (1024, 85, 128, 2048)
    assert s.vlm_trim_border is False
    assert Settings.from_env({**_plain_env(), "OCR_VLM_TRIM_BORDER": "true"}).vlm_trim_border is True
    s = Settings.from_env({**_plain_env(), "OCR_VLM_MAX_SIDE": "768", "OLLAMA_NUM_CTX": "1536"})
    assert (s.vlm_max_side, s.ollama_num_ctx) == (768, 1536)


@pytest.mark.parametrize(
    "override",
    [
        {"OCR_VLM_MAX_SIDE": "100"},
        {"OCR_VLM_MAX_SIDE": "4096"},
        {"OCR_VLM_JPEG_QUALITY": "100"},
        {"OLLAMA_NUM_PREDICT": "8"},
        {"OCR_VLM_MAX_SIDE": "2048", "OLLAMA_NUM_CTX": "2048"},  # gambar 2048 px tidak muat konteks 2048
    ],
)
def test_settings_vlm_limits_rejected(override: dict[str, str], tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Settings.from_env({**_plain_env(), **override})
