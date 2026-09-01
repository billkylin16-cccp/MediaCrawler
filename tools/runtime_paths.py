# -*- coding: utf-8 -*-
"""Resolve bundled resources and writable runtime data directories."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DATA_ENV = "MEDIACRAWLER_DATA_DIR"


def resource_root() -> Path:
    """Return the source tree or PyInstaller bundle resource directory."""

    bundle_root = getattr(sys, "_MEIPASS", "")
    if bundle_root:
        return Path(bundle_root).resolve()
    return Path(__file__).resolve().parents[1]


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)


def runtime_data_root() -> Path:
    """Return a writable root while preserving source-tree CLI behavior."""

    configured = os.getenv(APP_DATA_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.cwd().resolve()


def browser_data_dir() -> Path:
    path = runtime_data_root() / "browser_data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def temp_image_dir() -> Path:
    path = runtime_data_root() / "temp_image"
    path.mkdir(parents=True, exist_ok=True)
    return path
