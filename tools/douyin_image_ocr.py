# -*- coding: utf-8 -*-
"""Small asynchronous wrapper around RapidOCR for Douyin carousel images."""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Optional

import cv2
import numpy as np


@dataclass(frozen=True)
class OcrPageResult:
    page: int
    text: str
    confidence: Optional[float]
    source: str = "image"


def extract_ocr_lines(result: Any) -> list[tuple[str, Optional[float]]]:
    """Normalize RapidOCR v3/v4-style outputs into text/confidence pairs."""
    if result is None:
        return []

    if hasattr(result, "txts"):
        raw_texts = getattr(result, "txts", None)
        raw_scores = getattr(result, "scores", None)
        texts = list(raw_texts) if raw_texts is not None else []
        scores = list(raw_scores) if raw_scores is not None else []
        return [
            (str(text), float(scores[index]) if index < len(scores) else None)
            for index, text in enumerate(texts)
            if str(text).strip()
        ]

    payload = result
    if isinstance(payload, tuple) and payload:
        payload = payload[0]
    if not isinstance(payload, (list, tuple)):
        return []

    lines: list[tuple[str, Optional[float]]] = []
    for item in payload:
        if isinstance(item, str):
            if item.strip():
                lines.append((item, None))
            continue
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        text = item[1] if isinstance(item[1], str) else ""
        if not text.strip():
            continue
        confidence: Optional[float] = None
        if len(item) > 2:
            try:
                confidence = float(item[2])
            except (TypeError, ValueError):
                confidence = None
        lines.append((text, confidence))
    return lines


class DouyinImageOcr:
    def __init__(
        self,
        max_images: int = 35,
        minimum_confidence: float = 0.30,
        engine: Any = None,
    ) -> None:
        if max_images < 1:
            raise ValueError("max_images must be at least 1")
        self.max_images = max_images
        self.minimum_confidence = minimum_confidence
        if engine is None:
            try:
                from rapidocr import RapidOCR
            except ImportError as exc:
                raise RuntimeError(
                    "Image OCR dependencies are missing. Run `uv sync` and try again."
                ) from exc
            engine = RapidOCR()
        self.engine = engine

    def _recognize_image(
        self,
        image: np.ndarray,
        page: int,
        source: str = "image",
    ) -> Optional[OcrPageResult]:
        if image is None:
            return None
        height, width = image.shape[:2]
        maximum_side = max(height, width)
        if maximum_side > 1280:
            scale = 1280 / maximum_side
            image = cv2.resize(
                image,
                (max(1, int(width * scale)), max(1, int(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
        raw_result = self.engine(image)
        accepted: list[tuple[str, Optional[float]]] = []
        for text, confidence in extract_ocr_lines(raw_result):
            if confidence is None or confidence >= self.minimum_confidence:
                cleaned = " ".join(text.split())
                if cleaned:
                    accepted.append((cleaned, confidence))
        if not accepted:
            return None
        scores = [score for _, score in accepted if score is not None]
        confidence = sum(scores) / len(scores) if scores else None
        return OcrPageResult(
            page=page,
            text=" ".join(text for text, _ in accepted),
            confidence=confidence,
            source=source,
        )

    def _recognize_bytes(self, content: bytes, page: int) -> Optional[OcrPageResult]:
        image_data = np.frombuffer(content, dtype=np.uint8)
        image = cv2.imdecode(image_data, cv2.IMREAD_COLOR)
        return self._recognize_image(image, page)

    def _recognize_video_bytes(
        self,
        content: bytes,
        max_frames: int,
    ) -> list[OcrPageResult]:
        temporary_path = ""
        capture = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
                handle.write(content)
                temporary_path = handle.name
            capture = cv2.VideoCapture(temporary_path)
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            if frame_count <= 0:
                return []
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            duration_seconds = frame_count / fps if fps > 0 else float(max_frames)
            sample_count = min(
                max_frames,
                frame_count,
                max(3, int(duration_seconds) + 1),
            )
            frame_indexes = sorted(
                set(
                    int(index)
                    for index in np.linspace(
                        int((frame_count - 1) * 0.05),
                        int((frame_count - 1) * 0.95),
                        sample_count,
                    )
                )
            )
            results: list[OcrPageResult] = []
            seen_text: set[str] = set()
            for frame_number, frame_index in enumerate(frame_indexes, start=1):
                capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ok, frame = capture.read()
                if not ok:
                    continue
                result = self._recognize_image(
                    frame,
                    page=frame_number,
                    source="video",
                )
                if result and result.text not in seen_text:
                    seen_text.add(result.text)
                    results.append(result)
            return results
        finally:
            if capture is not None:
                capture.release()
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    async def recognize_urls(
        self,
        urls: Iterable[str],
        fetcher: Callable[[str], Awaitable[Optional[bytes]]],
    ) -> list[OcrPageResult]:
        results: list[OcrPageResult] = []
        for page, url in enumerate(list(urls)[: self.max_images], start=1):
            if not url:
                continue
            content = await fetcher(url)
            if not content:
                continue
            result = await asyncio.to_thread(self._recognize_bytes, content, page)
            if result:
                results.append(result)
        return results

    async def recognize_video_url(
        self,
        url: str,
        fetcher: Callable[[str], Awaitable[Optional[bytes]]],
        max_frames: int = 6,
    ) -> list[OcrPageResult]:
        if max_frames < 1 or not url:
            return []
        content = await fetcher(url)
        if not content:
            return []
        return await asyncio.to_thread(
            self._recognize_video_bytes,
            content,
            max_frames,
        )
