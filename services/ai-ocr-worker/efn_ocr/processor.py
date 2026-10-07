"""Orkestrasi satu laporan: klaim -> unduh -> anti-fraud -> OCR -> (VLM) -> keputusan -> tulis + publish.

Dependensi I/O diinjeksi lewat Protocol agar alur bisa diuji dengan fake (tests/test_processor.py).
Worker tidak menulis ke ClickHouse maupun Odoo; reward lewat event `loyalty.earn.requested` (ADR-020).
"""

import functools
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from PIL import Image

from efn_ocr.config import Settings
from efn_ocr.errors import ImageNotFoundError, UnreadableImageError, VlmUnavailableError
from efn_ocr.fraud import outside_geofence
from efn_ocr.imaging import compute_phash, encode_for_vlm, load_image
from efn_ocr.messages import OcrRequest, OutgoingMessage, result_messages, short_hash
from efn_ocr.ocr_engines import OcrEngine, OcrResult
from efn_ocr.pricing import extract_price_candidates
from efn_ocr.repository import ReportRow
from efn_ocr.validation import Decision, Extraction, FraudSignals, Reason, Status, Thresholds, decide, screen_fraud
from efn_ocr.vlm import VlmResult

log = logging.getLogger(__name__)
MAX_TEXT_CHARS = 2000


class Repository(Protocol):
    def claim(self, report_id: str, allow_reclaim: bool, stale_seconds: int) -> ReportRow | None: ...

    def current_status(self, report_id: str) -> str | None: ...

    def find_duplicate(self, report_id: str, phash: str, max_distance: int, lookback_days: int) -> str | None: ...

    def count_valid_today(self, user_hash: str, report_id: str) -> int: ...

    def finalize(
        self,
        report_id: str,
        status: str,
        reason: str,
        extracted: dict[str, Any],
        confidence: float | None,
        phash: str | None,
        before_commit: Callable[[], None],
    ) -> bool: ...

    def release(self, report_id: str) -> None: ...


class ImageStore(Protocol):
    def fetch(self, bucket: str, key: str) -> bytes: ...


class VlmClient(Protocol):
    name: str

    def extract(self, image_bytes: bytes) -> VlmResult: ...


class Publisher(Protocol):
    def publish(self, messages: Sequence[OutgoingMessage]) -> None: ...


@dataclass(frozen=True)
class ProcessResult:
    outcome: str  # processed | skipped
    status: str | None = None
    reason: str | None = None
    vlm: str = "not_used"  # not_used | ok | unavailable | disabled
    duration_seconds: float = 0.0


class ReportProcessor:
    def __init__(
        self,
        settings: Settings,
        repo: Repository,
        images: ImageStore,
        ocr: OcrEngine,
        vlm: VlmClient | None,
        publisher: Publisher,
        image_loader: Callable[[bytes], Image.Image] = load_image,
        phasher: Callable[[Image.Image], str] = compute_phash,
        vlm_encoder: Callable[[Image.Image], bytes] | None = None,
    ) -> None:
        self._s = settings
        self._repo = repo
        self._images = images
        self._ocr = ocr
        self._vlm = vlm
        self._publisher = publisher
        self._load = image_loader
        self._phash = phasher
        # G12: gambar diperkecil sebelum VLM (token gambar ~ kuadrat sisi). Default dari Settings.
        self._vlm_encode = vlm_encoder or (
            lambda image: encode_for_vlm(
                image, settings.vlm_max_side, settings.vlm_jpeg_quality, settings.vlm_trim_border
            )
        )
        self._thresholds = Thresholds(
            name_similarity=settings.name_similarity_threshold,
            price_tolerance_pct=settings.price_tolerance_pct,
            daily_cap=settings.daily_valid_cap,
        )

    def process(self, request: OcrRequest, allow_reclaim: bool) -> ProcessResult:
        started = time.monotonic()
        row = self._repo.claim(request.report_id, allow_reclaim, self._s.stale_processing_seconds)
        if row is None:
            status = self._repo.current_status(request.report_id)
            log.info("report=%s tidak diklaim (status=%s); dianggap sudah/sedang diproses", request.report_id, status)
            return ProcessResult(outcome="skipped", status=status)
        if request.user_hash != row.user_hash:
            # DB = source of truth; pesan hanya pemicu. Selisih menandakan bug/manipulasi publisher.
            log.warning("report=%s user_hash di pesan berbeda dengan DB; memakai nilai DB", row.report_id)
        if request.image_bucket and request.image_bucket != self._s.minio_bucket:
            log.warning("report=%s image_bucket di pesan diabaikan; memakai bucket konfigurasi", row.report_id)

        try:
            image_bytes = self._images.fetch(self._s.minio_bucket, row.image_object)
        except ImageNotFoundError:
            decision = Decision(Status.REJECTED, Reason.IMAGE_MISSING)
            return self._finish(row, decision, {}, None, None, "not_used", started)
        except UnreadableImageError:
            decision = Decision(Status.REJECTED, Reason.IMAGE_UNREADABLE)
            return self._finish(row, decision, {}, None, None, "not_used", started)
        try:
            image = self._load(image_bytes)
        except UnreadableImageError:
            decision = Decision(Status.REJECTED, Reason.IMAGE_UNREADABLE)
            return self._finish(row, decision, {}, None, None, "not_used", started)

        phash = self._phash(image)
        signals = FraudSignals(
            duplicate_of=self._repo.find_duplicate(
                row.report_id,
                phash,
                self._s.phash_max_distance,
                self._s.phash_lookback_days,
            ),
            valid_today=self._repo.count_valid_today(row.user_hash, row.report_id),
            outside_geofence=outside_geofence(row.geo_lat, row.geo_lng, self._s.geofence),
        )
        fraud = screen_fraud(signals, self._thresholds)
        if fraud is not None:
            extracted = {"phash": phash, "duplicate_of": signals.duplicate_of, "valid_today": signals.valid_today}
            return self._finish(row, fraud, extracted, None, phash, "not_used", started)

        classic = self._ocr.extract(image)
        extraction, vlm_state, unavailable_reason = self._second_stage(classic, image)
        decision = decide(
            row.product_name,
            row.reported_price,
            extraction,
            signals,
            self._thresholds,
            unavailable_reason,
        )
        extracted = self._extracted_json(classic, extraction, decision, phash)
        confidence = extraction.ocr_confidence if extraction is not None else classic.confidence
        return self._finish(row, decision, extracted, confidence, phash, vlm_state, started)

    def release(self, report_id: str) -> None:
        self._repo.release(report_id)

    def fail(self, report_id: str) -> None:
        """Setelah retry habis: tandai `flagged/processing_failed` (review manual), tanpa notifikasi."""
        self._repo.finalize(
            report_id,
            Status.FLAGGED.value,
            Reason.PROCESSING_FAILED.value,
            {"error": "processing_failed"},
            None,
            None,
            _noop,
        )

    def _second_stage(self, classic: OcrResult, image: Image.Image) -> tuple[Extraction | None, str, Reason]:
        if classic.text and classic.confidence >= self._s.ocr_confidence_threshold:
            extraction = Extraction(
                engine=classic.engine,
                name_text=classic.text,
                price_candidates=tuple(c.value for c in extract_price_candidates(classic.text)),
                ocr_confidence=classic.confidence,
            )
            return extraction, "not_used", Reason.VLM_UNAVAILABLE
        if self._vlm is None:
            return None, "disabled", Reason.VLM_DISABLED
        try:
            vlm_image = self._vlm_encode(image)
        except (OSError, ValueError) as exc:
            log.warning("gambar gagal disiapkan untuk VLM (%s); laporan akan di-flag", type(exc).__name__)
            return None, "unavailable", Reason.VLM_UNAVAILABLE
        try:
            result = self._vlm.extract(vlm_image)
        except VlmUnavailableError as exc:
            log.warning("VLM tidak tersedia (%s); laporan akan di-flag untuk review manual", exc)
            return None, "unavailable", Reason.VLM_UNAVAILABLE
        extraction = Extraction(
            engine=self._vlm.name,
            name_text=result.product_name,
            price_candidates=(result.price,) if result.price is not None else (),
            ocr_confidence=None,
        )
        return extraction, "ok", Reason.VLM_UNAVAILABLE

    @staticmethod
    def _extracted_json(
        classic: OcrResult,
        extraction: Extraction | None,
        decision: Decision,
        phash: str,
    ) -> dict[str, Any]:
        return {
            "engine": extraction.engine if extraction else None,
            "classic_engine": classic.engine,
            "classic_confidence": classic.confidence,
            "classic_text": classic.text[:MAX_TEXT_CHARS],
            "product_name": extraction.name_text[:MAX_TEXT_CHARS] if extraction else None,
            "price_candidates": list(extraction.price_candidates) if extraction else [],
            "price": decision.matched_price,
            "name_score": round(decision.name_score, 1),
            "phash": phash,
        }

    def _finish(
        self,
        row: ReportRow,
        decision: Decision,
        extracted: dict[str, Any],
        confidence: float | None,
        phash: str | None,
        vlm_state: str,
        started: float,
    ) -> ProcessResult:
        points = self._s.points_per_valid_report if decision.status is Status.VALID else 0
        messages = result_messages(
            row.report_id,
            row.user_hash,
            decision.status.value,
            decision.reason.value,
            points,
            product_name=row.product_name,
        )
        updated = self._repo.finalize(
            row.report_id,
            decision.status.value,
            decision.reason.value,
            extracted,
            None if confidence is None else round(confidence, 3),
            phash,
            functools.partial(self._publisher.publish, messages),
        )
        duration = time.monotonic() - started
        if not updated:
            log.info("report=%s sudah difinalisasi proses lain; hasil ini dibuang", row.report_id)
            return ProcessResult(outcome="skipped", vlm=vlm_state, duration_seconds=duration)
        log.info(
            "report=%s user=%s status=%s reason=%s vlm=%s ms=%d",
            row.report_id,
            short_hash(row.user_hash),
            decision.status.value,
            decision.reason.value,
            vlm_state,
            int(duration * 1000),
        )
        return ProcessResult(
            outcome="processed",
            status=decision.status.value,
            reason=decision.reason.value,
            vlm=vlm_state,
            duration_seconds=duration,
        )


def _noop() -> None:
    return None
