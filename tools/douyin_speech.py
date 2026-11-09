# -*- coding: utf-8 -*-
"""Optional local Whisper transcription for Douyin video bytes."""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class SpeechResult:
    text: str
    language: str = "zh"
    duration: Optional[float] = None


class DouyinSpeechTranscriber:
    """Load faster-whisper lazily so non-speech runs stay lightweight."""

    def __init__(self, model_name: str = "tiny", model_dir: str | Path | None = None) -> None:
        self.model_name = model_name or "tiny"
        self.model_dir = Path(model_dir).expanduser().resolve() if model_dir else None
        self._model: Any = None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("本地语音识别组件未安装，请重新安装完整版软件") from exc
        if self.model_dir:
            self.model_dir.mkdir(parents=True, exist_ok=True)
        self._model = WhisperModel(
            self.model_name,
            device="cpu",
            compute_type="int8",
            download_root=str(self.model_dir) if self.model_dir else None,
        )
        return self._model

    def _transcribe_bytes(self, content: bytes) -> SpeechResult:
        temporary_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
                handle.write(content)
                temporary_path = handle.name
            model = self._load_model()
            segments, info = model.transcribe(
                temporary_path,
                language="zh",
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            text = " ".join(
                " ".join(str(segment.text or "").split())
                for segment in segments
                if str(segment.text or "").strip()
            )
            return SpeechResult(
                text=text[:20000],
                language=str(getattr(info, "language", "zh") or "zh"),
                duration=getattr(info, "duration", None),
            )
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    async def transcribe_bytes(self, content: bytes) -> SpeechResult:
        if not content:
            return SpeechResult(text="")
        return await asyncio.to_thread(self._transcribe_bytes, content)

