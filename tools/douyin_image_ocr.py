# -*- coding: utf-8 -*-
"""Small asynchronous wrapper around RapidOCR for Douyin carousel images."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Optional

import cv2
import numpy as np


@dataclass(frozen=True)
class OcrPageResult:
    page: int
    text: str
    confidence: Optional[float]


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

    def _recognize_bytes(self, content: bytes, page: int) -> Optional[OcrPageResult]:
        image_data = np.frombuffer(content, dtype=np.uint8)
        image = cv2.imdecode(image_data, cv2.IMREAD_COLOR)
        if image is None:
            return None
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
        )

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
