# -*- coding: utf-8 -*-
"""Lightweight helpers for extracting media URLs from public Douyin payloads."""

from __future__ import annotations

from typing import Any


def extract_note_image_list(aweme_detail: dict[str, Any]) -> list[str]:
    image_urls: list[str] = []
    for image in aweme_detail.get("images") or []:
        url_list = image.get("url_list") or []
        if url_list:
            image_urls.append(url_list[-1])
    return image_urls


def extract_video_download_url(aweme_detail: dict[str, Any]) -> str:
    video = aweme_detail.get("video") or {}
    candidates = (
        (video.get("play_addr_h264") or {}).get("url_list")
        or (video.get("play_addr_256") or {}).get("url_list")
        or (video.get("play_addr") or {}).get("url_list")
        or []
    )
    return candidates[-1] if candidates else ""
