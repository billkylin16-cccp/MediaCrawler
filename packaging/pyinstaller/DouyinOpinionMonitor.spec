# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


project_root = Path(SPEC).resolve().parents[2]
browser_root = Path(os.environ["MEDIACRAWLER_BUILD_BROWSERS_PATH"]).resolve()

if not browser_root.is_dir():
    raise SystemExit(f"Bundled browser directory does not exist: {browser_root}")

datas = [
    (str(project_root / "libs"), "libs"),
    (str(project_root / "LICENSE"), "."),
    (str(project_root / "douyin_watch_accounts.txt"), "."),
    (str(browser_root), "ms-playwright"),
]
binaries = []
hiddenimports = []

for package_name in ("playwright", "rapidocr", "onnxruntime", "cv2", "openpyxl"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

a = Analysis(
    [str(project_root / "douyin_opinion_app.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6",
        "PyQt5",
        "jupyter",
        "notebook",
        "IPython",
        "pandas",
        "matplotlib",
        "wordcloud",
        "jieba",
        "sqlalchemy",
        "motor",
        "pymongo",
        "redis",
        "aiomysql",
        "asyncmy",
        "asyncpg",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DouyinOpinionMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="runtime",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DouyinOpinionMonitor",
)
