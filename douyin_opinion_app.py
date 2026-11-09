# -*- coding: utf-8 -*-
"""Windows desktop entry point for the Douyin opinion-monitoring workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional


APP_NAME = "抖音舆情监测"
APP_ID = "DouyinOpinionMonitor"
APP_VERSION = "0.3.1-beta.1"
LICENSE_ACCEPTED_VERSION = "1.1"
SCHEDULE_TASK_NAME = "DouyinOpinionMonitor每日监测"

MODE_LABELS = {
    "全站关键词 + 重点账号": "all",
    "仅重点账号": "watch_only",
}
MATCH_LABELS = {
    "全部关键词同时出现": "all",
    "任一关键词出现": "any",
}


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    bundle_dir = getattr(sys, "_MEIPASS", "")
    if bundle_dir:
        return Path(bundle_dir).resolve()
    return Path(__file__).resolve().parent


def local_data_root() -> Path:
    configured = os.getenv("MEDIACRAWLER_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return (base / APP_ID).resolve()


def default_report_dir() -> Path:
    documents = Path.home() / "Documents"
    if not documents.exists():
        documents = Path.home()
    return documents / APP_NAME


def user_paths() -> dict[str, Path]:
    root = local_data_root()
    return {
        "root": root,
        "config": root / "config",
        "jobs": root / "jobs",
        "logs": root / "logs",
        "watchlist": root / "config" / "douyin_watch_accounts.txt",
        "settings": root / "config" / "settings.json",
        "history": root / "history" / "opinion_history.db",
        "dashboard": root / "dashboard" / "抖音舆情数据看板.html",
    }


def prepare_runtime_environment() -> None:
    paths = user_paths()
    for key in ("root", "config", "jobs", "logs"):
        paths[key].mkdir(parents=True, exist_ok=True)
    os.environ["MEDIACRAWLER_DATA_DIR"] = str(paths["root"])
    os.environ["MEDIACRAWLER_ENABLE_CDP_MODE"] = "false"
    os.environ["MEDIACRAWLER_CDP_CONNECT_EXISTING"] = "false"

    if is_frozen():
        node_dir = bundle_root() / "playwright" / "driver"
        browser_dir = bundle_root() / "ms-playwright"
        os.environ["PATH"] = str(node_dir) + os.pathsep + os.environ.get("PATH", "")
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_dir)
        os.environ["PLAYWRIGHT_SKIP_BROWSER_GC"] = "1"


def ensure_user_files() -> dict[str, Path]:
    prepare_runtime_environment()
    paths = user_paths()
    if not paths["watchlist"].exists():
        bundled_watchlist = bundle_root() / "douyin_watch_accounts.txt"
        if bundled_watchlist.exists():
            shutil.copy2(bundled_watchlist, paths["watchlist"])
        else:
            paths["watchlist"].write_text(
                "# One public Douyin creator profile URL per line.\n",
                encoding="utf-8",
            )
    default_report_dir().mkdir(parents=True, exist_ok=True)
    return paths


def read_watch_accounts(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def normalize_keywords(value: str) -> list[str]:
    normalized = value
    for separator in ("，", "、", "；", ";"):
        normalized = normalized.replace(separator, ",")
    return [item.strip() for item in re.split(r"[,\s]+", normalized) if item.strip()]


def normalize_video_entries(value: str) -> list[str]:
    """Accept Douyin work URLs/IDs separated by whitespace or common punctuation."""
    normalized = value.replace("\\&", "&")
    for separator in ("，", "、", "；", ";"):
        normalized = normalized.replace(separator, ",")
    entries = [item.strip() for item in re.split(r"[,\s]+", normalized) if item.strip()]
    return list(dict.fromkeys(entries))


def safe_file_component(value: str, max_length: int = 36) -> str:
    safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", value, flags=re.UNICODE).strip("_")
    return (safe or "关键词")[:max_length].rstrip("_")


def next_report_path(
    directory: Path,
    opinion_date: str,
    keywords: list[str],
    end_datetime: str | None = None,
) -> Path:
    parsed = datetime.strptime(opinion_date[:10], "%Y-%m-%d")
    end_parsed = (
        datetime.strptime(end_datetime[:10], "%Y-%m-%d")
        if end_datetime
        else parsed
    )
    if parsed.date() == end_parsed.date():
        base = f"{parsed.month}.{parsed.day:02d}抖音舆论检测"
    else:
        base = (
            f"{parsed.month}.{parsed.day:02d}-"
            f"{end_parsed.month}.{end_parsed.day:02d}抖音舆论检测"
        )
    candidate = directory / f"{base}.xlsx"
    if not candidate.exists():
        return candidate
    keyword_part = safe_file_component("_".join(keywords))
    candidate = directory / f"{base}-{keyword_part}.xlsx"
    if not candidate.exists():
        return candidate
    return directory / f"{base}-{keyword_part}-{datetime.now():%H%M%S}.xlsx"


def load_settings(path: Path) -> dict[str, Any]:
    today = datetime.now().strftime("%Y-%m-%d")
    defaults: dict[str, Any] = {
        "keywords": "西陶",
        "locations": "西陶",
        "opinion_date": today,
        "start_datetime": f"{today} 00:00",
        "end_datetime": f"{today} 23:59",
        "mode": "all",
        "match": "all",
        "supplemental_videos": "",
        "output_dir": str(default_report_dir()),
        "enable_ocr": True,
        "get_comments": True,
        "include_replies": False,
        "max_videos": 100,
        "max_comments": 200,
        "ocr_max_images": 35,
        "video_ocr_max_frames": 6,
        "watch_max_posts": 36,
        "enable_speech": False,
        "speech_model": "tiny",
        "speech_max_videos": 12,
        "deduplicate": True,
        "run_retries": 2,
        "schedule_enabled": False,
        "schedule_time": "08:30",
        "backfill_days": 3,
        "license_accepted": "",
    }
    if not path.exists():
        return defaults
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if isinstance(loaded, dict):
        defaults.update(loaded)
        legacy_date = str(loaded.get("opinion_date") or today)
        if "start_datetime" not in loaded:
            defaults["start_datetime"] = f"{legacy_date} 00:00"
        if "end_datetime" not in loaded:
            defaults["end_datetime"] = f"{legacy_date} 23:59"
    return defaults


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def runtime_self_check() -> list[str]:
    prepare_runtime_environment()
    errors: list[str] = []
    required_resources = [bundle_root() / "libs" / "douyin.js", bundle_root() / "LICENSE"]
    for resource in required_resources:
        if not resource.exists():
            errors.append(f"缺少资源：{resource}")
    if not shutil.which("node"):
        errors.append("未找到内置或系统 Node.js")
    if is_frozen() and not (bundle_root() / "ms-playwright").exists():
        errors.append("未找到内置 Chromium")
    try:
        probe = user_paths()["root"] / ".write-test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
    except OSError as exc:
        errors.append(f"用户数据目录不可写：{exc}")
    try:
        from rapidocr import RapidOCR
        import onnxruntime  # noqa: F401

        RapidOCR()
    except Exception as exc:
        errors.append(f"OCR 运行时不可用：{exc}")
    try:
        import sqlite3  # noqa: F401
        from faster_whisper import WhisperModel  # noqa: F401
    except Exception as exc:
        errors.append(f"本地历史或语音识别运行时不可用：{exc}")
    try:
        from media_platform.douyin import DouYinCrawler

        DouYinCrawler()
    except Exception as exc:
        errors.append(f"监测模块无法加载：{exc}")
    try:
        asyncio.run(_browser_self_check())
    except Exception as exc:
        errors.append(f"内置浏览器无法启动：{exc}")
    return errors


async def _browser_self_check() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chromium", headless=True)
        try:
            page = await browser.new_page()
            await page.goto("about:blank")
        finally:
            await browser.close()


async def _close_crawler(crawler: Any) -> None:
    cdp_manager = getattr(crawler, "cdp_manager", None)
    if cdp_manager:
        try:
            await cdp_manager.cleanup(force=True)
        except Exception:
            pass
        return
    browser_context = getattr(crawler, "browser_context", None)
    if browser_context:
        try:
            await browser_context.close()
        except Exception:
            pass


async def _wait_for_stop(stop_path: Path) -> None:
    while not stop_path.exists():
        await asyncio.sleep(0.5)


async def _run_crawler_job(job: dict[str, Any]) -> str:
    import config
    from media_platform.douyin import DouYinCrawler
    from var import crawler_type_var

    config.PLATFORM = "dy"
    config.LOGIN_TYPE = "qrcode"
    config.CRAWLER_TYPE = "search"
    discovery_terms = list(dict.fromkeys([*job["keywords"], *job.get("locations", [])]))
    config.KEYWORDS = ",".join(discovery_terms)
    config.HEADLESS = False
    config.CDP_HEADLESS = False
    config.ENABLE_CDP_MODE = False
    config.CDP_CONNECT_EXISTING = False
    config.SAVE_LOGIN_STATE = True
    config.SAVE_DATA_OPTION = "jsonl"
    config.ENABLE_GET_COMMENTS = bool(job["get_comments"])
    config.ENABLE_GET_SUB_COMMENTS = bool(job["include_replies"])
    config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = int(job["max_comments"])
    config.CRAWLER_MAX_NOTES_COUNT = int(job["max_videos"])
    config.MAX_CONCURRENCY_NUM = 1
    config.ENABLE_DOUYIN_OPINION_REPORT = True
    config.DOUYIN_OPINION_REPORT_DATE = job["opinion_date"]
    config.DOUYIN_OPINION_START_DATETIME = job["start_datetime"]
    config.DOUYIN_OPINION_END_DATETIME = job["end_datetime"]
    config.DOUYIN_OPINION_REPORT_OUTPUT = job["output_path"]
    config.DOUYIN_OPINION_MATCH = job["match"]
    config.DOUYIN_OPINION_CONTENT_KEYWORDS = list(job["keywords"])
    config.DOUYIN_OPINION_LOCATIONS = list(job.get("locations", []))
    config.DOUYIN_OPINION_WATCH_ACCOUNTS = list(job["watch_accounts"])
    config.DOUYIN_OPINION_SUPPLEMENTAL_VIDEOS = list(job.get("supplemental_videos", []))
    config.DOUYIN_OPINION_ENABLE_OCR = bool(job["enable_ocr"])
    config.DOUYIN_OPINION_OCR_MAX_IMAGES = int(job["ocr_max_images"])
    config.DOUYIN_OPINION_VIDEO_OCR_MAX_FRAMES = int(job.get("video_ocr_max_frames", 6))
    config.DOUYIN_OPINION_WATCH_MAX_POSTS = int(job["watch_max_posts"])
    config.DOUYIN_OPINION_SCOPE = job["mode"]
    config.DOUYIN_OPINION_HISTORY_DB = str(job.get("history_path") or "")
    config.DOUYIN_OPINION_DEDUPLICATE = bool(job.get("deduplicate", True))
    config.DOUYIN_OPINION_DASHBOARD_OUTPUT = str(job.get("dashboard_path") or "")
    config.DOUYIN_OPINION_ENABLE_SPEECH = bool(job.get("enable_speech", False))
    config.DOUYIN_OPINION_SPEECH_MODEL = str(job.get("speech_model") or "tiny")
    config.DOUYIN_OPINION_SPEECH_MAX_VIDEOS = int(job.get("speech_max_videos", 12))
    config.DOUYIN_OPINION_SPEECH_MODEL_DIR = str(
        local_data_root() / "models" / "faster-whisper"
    )
    config.ENABLE_IP_PROXY = False
    config.ENABLE_GET_MEIDAS = False

    crawler_type_var.set("search")
    crawler = DouYinCrawler()
    crawl_task = asyncio.create_task(crawler.start(), name="douyin-opinion-crawl")
    stop_task = asyncio.create_task(
        _wait_for_stop(Path(job["stop_path"])),
        name="douyin-opinion-stop-watcher",
    )
    try:
        done, _ = await asyncio.wait(
            {crawl_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done and not crawl_task.done():
            print("[Desktop] 收到停止请求，正在关闭浏览器并保存清理状态……", flush=True)
            crawl_task.cancel()
            await asyncio.gather(crawl_task, return_exceptions=True)
            return "cancelled"
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        await crawl_task
        return "completed"
    finally:
        await _close_crawler(crawler)


def run_worker(job_path: Path) -> int:
    from tools.douyin_opinion_history import OpinionHistory

    job = json.loads(job_path.read_text(encoding="utf-8"))
    legacy_date = str(job.get("opinion_date") or datetime.now().strftime("%Y-%m-%d"))
    job.setdefault("locations", [])
    job.setdefault("start_datetime", f"{legacy_date} 00:00")
    job.setdefault("end_datetime", f"{legacy_date} 23:59")
    log_path = Path(job["log_path"])
    status_path = Path(job["status_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("a", encoding="utf-8", buffering=1, errors="replace")
    original_stdout, original_stderr = sys.stdout, sys.stderr
    sys.stdout = log_handle
    sys.stderr = log_handle
    prepare_runtime_environment()
    status: dict[str, Any] = {
        "state": "running",
        "output_path": job["output_path"],
        "dashboard_path": job.get("dashboard_path", ""),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "error": "",
        "attempt": 1,
    }
    save_json(status_path, status)
    history = OpinionHistory(job["history_path"])
    run_id = str(job.get("run_id") or job_path.stem)
    max_attempts = max(1, min(6, int(job.get("run_retries", 2)) + 1))
    try:
        print(f"[{APP_NAME}] 版本 {APP_VERSION}", flush=True)
        print(f"[{APP_NAME}] 关键词：{'、'.join(job['keywords']) or '（未设置）'}", flush=True)
        print(f"[{APP_NAME}] 关键定位：{'、'.join(job.get('locations', [])) or '（未设置）'}", flush=True)
        print(
            f"[{APP_NAME}] 监测时间：{job['start_datetime']} 至 {job['end_datetime']}",
            flush=True,
        )
        print(f"[{APP_NAME}] 重点账号：{len(job['watch_accounts'])} 个", flush=True)
        import traceback

        last_error = ""
        for attempt in range(1, max_attempts + 1):
            status["attempt"] = attempt
            status["state"] = "running"
            status["error"] = ""
            save_json(status_path, status)
            history.start_run(run_id, job["opinion_date"], job["output_path"], attempt)
            print(f"[{APP_NAME}] 开始第 {attempt}/{max_attempts} 次尝试", flush=True)
            try:
                result = asyncio.run(_run_crawler_job(job))
                status["state"] = result
                status["finished_at"] = datetime.now().isoformat(timespec="seconds")
                save_json(status_path, status)
                history.finish_run(run_id, result)
                return 0 if result == "completed" else 130
            except Exception as exc:
                traceback.print_exc()
                last_error = str(exc)
                if attempt >= max_attempts or Path(job["stop_path"]).exists():
                    break
                delay = min(60, 10 * attempt)
                print(
                    f"[{APP_NAME}] 本次运行失败，{delay} 秒后自动重试：{exc}",
                    flush=True,
                )
                time.sleep(delay)
        status["state"] = "failed"
        status["error"] = last_error
        status["finished_at"] = datetime.now().isoformat(timespec="seconds")
        save_json(status_path, status)
        history.finish_run(run_id, "failed", last_error)
        return 1
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_handle.close()


def _job_command() -> str:
    if is_frozen():
        return f'"{Path(sys.executable).resolve()}" --scheduled-run'
    return f'"{Path(sys.executable).resolve()}" "{Path(__file__).resolve()}" --scheduled-run'


def configure_daily_schedule(enabled: bool, schedule_time: str) -> None:
    """Create or remove the current user's interactive daily Windows task."""
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule_time):
        raise ValueError("定时时间必须为 HH:MM，例如 08:30。")
    if enabled:
        command = [
            "schtasks.exe",
            "/Create",
            "/TN",
            SCHEDULE_TASK_NAME,
            "/TR",
            _job_command(),
            "/SC",
            "DAILY",
            "/ST",
            schedule_time,
            "/RL",
            "LIMITED",
            "/IT",
            "/F",
        ]
    else:
        command = ["schtasks.exe", "/Delete", "/TN", SCHEDULE_TASK_NAME, "/F"]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0 and enabled:
        message = (result.stderr or result.stdout or "任务计划操作失败").strip()
        raise RuntimeError(message)


def create_job(
    settings: dict[str, Any],
    watch_accounts: list[str],
    paths: dict[str, Path],
    opinion_date: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    configured_start = datetime.strptime(str(settings["start_datetime"]), "%Y-%m-%d %H:%M")
    configured_end = datetime.strptime(str(settings["end_datetime"]), "%Y-%m-%d %H:%M")
    if opinion_date:
        target_day = date.fromisoformat(opinion_date)
        start_datetime = datetime.combine(target_day, configured_start.time())
        duration = configured_end - configured_start
        if duration < timedelta(0):
            duration = timedelta(hours=23, minutes=59)
        end_datetime = start_datetime + duration
    else:
        start_datetime = configured_start
        end_datetime = configured_end
    target_date = start_datetime.date().isoformat()
    keywords = normalize_keywords(str(settings["keywords"]))
    locations = normalize_keywords(str(settings.get("locations") or ""))
    output_path = next_report_path(
        Path(settings["output_dir"]),
        start_datetime.strftime("%Y-%m-%d %H:%M"),
        list(dict.fromkeys([*keywords, *locations])),
        end_datetime.strftime("%Y-%m-%d %H:%M"),
    )
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    job_path = paths["jobs"] / f"{job_id}.json"
    job = {
        **settings,
        "run_id": job_id,
        "opinion_date": target_date,
        "keywords": keywords,
        "locations": locations,
        "start_datetime": start_datetime.strftime("%Y-%m-%d %H:%M"),
        "end_datetime": end_datetime.strftime("%Y-%m-%d %H:%M"),
        "watch_accounts": watch_accounts,
        "supplemental_videos": normalize_video_entries(
            str(settings.get("supplemental_videos") or "")
        ),
        "output_path": str(output_path),
        "dashboard_path": str(paths["dashboard"]),
        "history_path": str(paths["history"]),
        "log_path": str(paths["logs"] / f"{job_id}.log"),
        "status_path": str(paths["jobs"] / f"{job_id}.status.json"),
        "stop_path": str(paths["jobs"] / f"{job_id}.stop"),
    }
    save_json(job_path, job)
    return job_path, job


def run_scheduled() -> int:
    """Run today plus any missed dates in the configured local backfill window."""
    from tools.douyin_opinion_dashboard import write_dashboard
    from tools.douyin_opinion_history import OpinionHistory

    paths = ensure_user_files()
    settings = load_settings(paths["settings"])
    if not settings.get("schedule_enabled"):
        return 0
    watch_accounts = read_watch_accounts(paths["watchlist"])
    history = OpinionHistory(paths["history"])
    missing = history.missing_dates(
        date.today(),
        int(settings.get("backfill_days", 3)),
    )
    exit_code = 0
    for missing_date in missing:
        job_path, _ = create_job(
            settings,
            watch_accounts,
            paths,
            opinion_date=missing_date.isoformat(),
        )
        result = run_worker(job_path)
        if result not in {0, 130}:
            exit_code = result
    write_dashboard(paths["history"], paths["dashboard"])
    return exit_code


class OpinionMonitorApp:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.paths = ensure_user_files()
        self.settings = load_settings(self.paths["settings"])
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("1080x900")
        self.root.minsize(940, 740)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.keywords_var = tk.StringVar(value=str(self.settings["keywords"]))
        self.locations_var = tk.StringVar(value=str(self.settings.get("locations", "西陶")))
        self.start_datetime_var = tk.StringVar(value=str(self.settings["start_datetime"]))
        self.end_datetime_var = tk.StringVar(value=str(self.settings["end_datetime"]))
        self.mode_var = tk.StringVar(value=self._label_for(MODE_LABELS, self.settings["mode"]))
        self.match_var = tk.StringVar(value=self._label_for(MATCH_LABELS, self.settings["match"]))
        self.supplemental_var = tk.StringVar(value=str(self.settings["supplemental_videos"]))
        self.output_var = tk.StringVar(value=str(self.settings["output_dir"]))
        self.ocr_var = tk.BooleanVar(value=bool(self.settings["enable_ocr"]))
        self.speech_var = tk.BooleanVar(value=bool(self.settings.get("enable_speech", False)))
        self.comments_var = tk.BooleanVar(value=bool(self.settings["get_comments"]))
        self.replies_var = tk.BooleanVar(value=bool(self.settings["include_replies"]))
        self.schedule_var = tk.BooleanVar(value=bool(self.settings.get("schedule_enabled", False)))
        self.schedule_time_var = tk.StringVar(value=str(self.settings.get("schedule_time", "08:30")))
        self.status_var = tk.StringVar(value="准备就绪")
        self.watch_count_var = tk.StringVar()

        self.process: Optional[subprocess.Popen[Any]] = None
        self.log_handle: Optional[Any] = None
        self.log_offset = 0
        self.current_job: Optional[dict[str, Any]] = None
        self.last_output: Optional[Path] = None
        self.last_dashboard: Optional[Path] = self.paths["dashboard"]
        self.closing_after_stop = False
        self._build_ui()
        self.refresh_watch_count()
        self.root.after(100, self._show_license_if_needed)

    @staticmethod
    def _label_for(mapping: dict[str, str], value: str) -> str:
        return next((label for label, key in mapping.items() if key == value), next(iter(mapping)))

    def _build_ui(self) -> None:
        tk, ttk = self.tk, self.ttk
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Subtitle.TLabel", foreground="#4B5563")
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))

        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="自动补采 · 历史去重 · 图片/视频/语音识别 · 风险研判 · Excel与本地看板",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 14))

        form = ttk.LabelFrame(outer, text="监测设置", padding=12)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="关键词").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(form, textvariable=self.keywords_var).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(form, text="可手动编辑，多个用空格或逗号分隔").grid(row=0, column=2, sticky="w", padx=(10, 0))

        ttk.Label(form, text="关键定位").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(form, textvariable=self.locations_var).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(form, text="例：西陶、西陶镇；POI/地址/IP属地命中即收录").grid(row=1, column=2, sticky="w", padx=(10, 0))

        ttk.Label(form, text="监测开始").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(form, textvariable=self.start_datetime_var, width=22).grid(row=2, column=1, sticky="w", pady=5)
        ttk.Label(form, text="YYYY-MM-DD HH:MM").grid(row=2, column=2, sticky="w", padx=(10, 0))

        ttk.Label(form, text="监测结束").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(form, textvariable=self.end_datetime_var, width=22).grid(row=3, column=1, sticky="w", pady=5)
        ttk.Button(form, text="今天全天", command=self.set_today_window).grid(row=3, column=2, sticky="w", padx=(10, 0))

        ttk.Label(form, text="监测范围").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Combobox(form, textvariable=self.mode_var, values=list(MODE_LABELS), state="readonly").grid(row=4, column=1, sticky="ew", pady=5)
        ttk.Button(form, text="编辑重点账号", command=self.open_watchlist).grid(row=4, column=2, sticky="w", padx=(10, 0))

        ttk.Label(form, text="关键词规则").grid(row=5, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Combobox(form, textvariable=self.match_var, values=list(MATCH_LABELS), state="readonly").grid(row=5, column=1, sticky="ew", pady=5)
        ttk.Label(form, textvariable=self.watch_count_var).grid(row=5, column=2, sticky="w", padx=(10, 0))

        ttk.Label(form, text="指定作品补漏").grid(row=6, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(form, textvariable=self.supplemental_var).grid(row=6, column=1, sticky="ew", pady=5)
        ttk.Label(form, text="粘贴作品链接或ID，多个用空格分隔").grid(row=6, column=2, sticky="w", padx=(10, 0))

        ttk.Label(form, text="报表目录").grid(row=7, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(form, textvariable=self.output_var).grid(row=7, column=1, sticky="ew", pady=5)
        ttk.Button(form, text="选择目录", command=self.choose_output_dir).grid(row=7, column=2, sticky="w", padx=(10, 0))

        options = ttk.Frame(form)
        options.grid(row=8, column=1, columnspan=2, sticky="w", pady=(7, 2))
        ttk.Checkbutton(options, text="画面文字识别（OCR）", variable=self.ocr_var).grid(row=0, column=0, sticky="w", padx=(0, 18))
        ttk.Checkbutton(options, text="视频语音转文字（首次下载本地模型）", variable=self.speech_var).grid(row=0, column=1, sticky="w", padx=(0, 18))
        ttk.Checkbutton(options, text="抓取评论", variable=self.comments_var).grid(row=1, column=0, sticky="w", padx=(0, 18), pady=(5, 0))
        ttk.Checkbutton(options, text="包含二级回复", variable=self.replies_var).grid(row=1, column=1, sticky="w", pady=(5, 0))

        schedule = ttk.Frame(form)
        schedule.grid(row=9, column=1, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Checkbutton(schedule, text="每日自动运行", variable=self.schedule_var).pack(side="left")
        ttk.Label(schedule, text="时间").pack(side="left", padx=(14, 5))
        ttk.Entry(schedule, textvariable=self.schedule_time_var, width=8).pack(side="left")
        ttk.Label(schedule, text="失败自动重试并补采最近3天").pack(side="left", padx=(12, 8))
        ttk.Button(schedule, text="保存定时任务", command=self.save_schedule).pack(side="left")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=12)
        self.start_button = ttk.Button(actions, text="开始监测并生成日报", style="Accent.TButton", command=self.start_monitoring)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="停止", command=self.stop_monitoring, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.open_report_button = ttk.Button(actions, text="打开最新报表", command=self.open_last_report, state="disabled")
        self.open_report_button.pack(side="left", padx=(0, 8))
        self.open_dashboard_button = ttk.Button(actions, text="打开数据看板", command=self.open_dashboard)
        self.open_dashboard_button.pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="打开报表目录", command=self.open_output_dir).pack(side="left")
        ttk.Button(actions, text="环境检查", command=self.show_self_check).pack(side="right")

        status_frame = ttk.Frame(outer)
        status_frame.pack(fill="x")
        self.progress = ttk.Progressbar(status_frame, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True)
        ttk.Label(status_frame, textvariable=self.status_var, width=22, anchor="e").pack(side="right", padx=(10, 0))

        log_frame = ttk.LabelFrame(outer, text="运行日志", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(10, 8))
        self.log_text = tk.Text(log_frame, wrap="word", height=14, font=("Consolas", 9), state="disabled")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Label(
            outer,
            text="仅限非商业学习和研究；请遵守平台规则并控制访问频率。登录信息只保存在本机。",
            style="Subtitle.TLabel",
        ).pack(anchor="w")

    def _show_license_if_needed(self) -> None:
        from tkinter import messagebox

        if self.settings.get("license_accepted") == LICENSE_ACCEPTED_VERSION:
            return
        accepted = messagebox.askokcancel(
            "使用许可",
            "本软件基于 NON-COMMERCIAL LEARNING LICENSE 1.1，"
            "仅限非商业学习和研究，不得用于大规模抓取或干扰平台运行。\n\n"
            "点击“确定”表示你同意许可证和上述限制。",
            parent=self.root,
        )
        if not accepted:
            self.root.destroy()
            return
        self.settings["license_accepted"] = LICENSE_ACCEPTED_VERSION
        save_json(self.paths["settings"], self.settings)

    def refresh_watch_count(self) -> None:
        self.watch_count_var.set(f"重点账号：{len(read_watch_accounts(self.paths['watchlist']))} 个")

    def choose_output_dir(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askdirectory(initialdir=self.output_var.get(), parent=self.root)
        if chosen:
            self.output_var.set(chosen)

    def set_today_window(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        self.start_datetime_var.set(f"{today} 00:00")
        self.end_datetime_var.set(f"{today} 23:59")

    def open_watchlist(self) -> None:
        subprocess.Popen(["notepad.exe", str(self.paths["watchlist"])])
        self.root.after(1000, self.refresh_watch_count)

    def open_output_dir(self) -> None:
        directory = Path(self.output_var.get()).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(str(directory))

    def open_last_report(self) -> None:
        if self.last_output and self.last_output.exists():
            os.startfile(str(self.last_output))

    def open_dashboard(self) -> None:
        from tkinter import messagebox

        dashboard = self.last_dashboard or self.paths["dashboard"]
        if dashboard.exists():
            os.startfile(str(dashboard))
        else:
            messagebox.showinfo("数据看板", "完成一次监测后会自动生成本地数据看板。", parent=self.root)

    def save_schedule(self) -> None:
        from tkinter import messagebox

        try:
            settings, _ = self._collect_settings()
            configure_daily_schedule(
                bool(settings["schedule_enabled"]),
                str(settings["schedule_time"]),
            )
            save_json(self.paths["settings"], settings)
            self.settings = settings
        except Exception as exc:
            messagebox.showerror("定时任务", f"保存失败：\n{exc}", parent=self.root)
            return
        state = "已启用" if settings["schedule_enabled"] else "已关闭"
        messagebox.showinfo(
            "定时任务",
            f"每日监测{state}。电脑需保持开机并登录 Windows。",
            parent=self.root,
        )

    def show_self_check(self) -> None:
        from tkinter import messagebox

        errors = runtime_self_check()
        if errors:
            messagebox.showerror("环境检查", "发现以下问题：\n\n" + "\n".join(errors), parent=self.root)
        else:
            messagebox.showinfo("环境检查", "环境检查通过，可以开始监测。", parent=self.root)

    def _collect_settings(self) -> tuple[dict[str, Any], list[str]]:
        keywords = normalize_keywords(self.keywords_var.get())
        locations = normalize_keywords(self.locations_var.get())
        if not keywords and not locations:
            raise ValueError("请至少输入一个关键词或关键定位。")
        start_datetime_text = self.start_datetime_var.get().strip()
        end_datetime_text = self.end_datetime_var.get().strip()
        try:
            start_datetime = datetime.strptime(start_datetime_text, "%Y-%m-%d %H:%M")
            end_datetime = datetime.strptime(end_datetime_text, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError("监测时间必须为 YYYY-MM-DD HH:MM。") from exc
        if end_datetime < start_datetime:
            raise ValueError("监测结束时间不能早于开始时间。")
        mode = MODE_LABELS[self.mode_var.get()]
        match = MATCH_LABELS[self.match_var.get()]
        supplemental_videos = normalize_video_entries(self.supplemental_var.get())
        watch_accounts = read_watch_accounts(self.paths["watchlist"])
        if mode == "watch_only" and not watch_accounts:
            raise ValueError("“仅重点账号”模式至少需要一个重点账号。")
        output_dir = Path(self.output_var.get()).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        settings = {
            "keywords": ",".join(keywords),
            "locations": ",".join(locations),
            "opinion_date": start_datetime.date().isoformat(),
            "start_datetime": start_datetime_text,
            "end_datetime": end_datetime_text,
            "mode": mode,
            "match": match,
            "supplemental_videos": "\n".join(supplemental_videos),
            "output_dir": str(output_dir),
            "enable_ocr": bool(self.ocr_var.get()),
            "enable_speech": bool(self.speech_var.get()),
            "get_comments": bool(self.comments_var.get()),
            "include_replies": bool(self.replies_var.get()),
            "max_videos": int(self.settings.get("max_videos", 100)),
            "max_comments": int(self.settings.get("max_comments", 200)),
            "ocr_max_images": int(self.settings.get("ocr_max_images", 35)),
            "video_ocr_max_frames": int(self.settings.get("video_ocr_max_frames", 6)),
            "watch_max_posts": int(self.settings.get("watch_max_posts", 36)),
            "speech_model": str(self.settings.get("speech_model", "tiny")),
            "speech_max_videos": int(self.settings.get("speech_max_videos", 12)),
            "deduplicate": bool(self.settings.get("deduplicate", True)),
            "run_retries": int(self.settings.get("run_retries", 2)),
            "schedule_enabled": bool(self.schedule_var.get()),
            "schedule_time": self.schedule_time_var.get().strip(),
            "backfill_days": int(self.settings.get("backfill_days", 3)),
            "license_accepted": LICENSE_ACCEPTED_VERSION,
        }
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", settings["schedule_time"]):
            raise ValueError("定时时间必须为 HH:MM，例如 08:30。")
        return settings, watch_accounts

    def start_monitoring(self) -> None:
        from tkinter import messagebox

        if self.process and self.process.poll() is None:
            return
        try:
            settings, watch_accounts = self._collect_settings()
        except ValueError as exc:
            messagebox.showerror("设置有误", str(exc), parent=self.root)
            return
        save_json(self.paths["settings"], settings)
        self.settings = settings
        job_path, job = create_job(settings, watch_accounts, self.paths)
        log_path = Path(job["log_path"])
        stop_path = Path(job["stop_path"])
        if stop_path.exists():
            stop_path.unlink()

        command = [sys.executable]
        if not is_frozen():
            command.append(str(Path(__file__).resolve()))
        command.extend(["--worker", str(job_path)])
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.log_handle = log_path.open("ab")
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(bundle_root()),
                env=os.environ.copy(),
                stdout=self.log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
        except Exception as exc:
            self.log_handle.close()
            self.log_handle = None
            messagebox.showerror("启动失败", f"无法启动监测任务：\n{exc}", parent=self.root)
            return
        self.current_job = job
        self.log_offset = 0
        self.last_output = None
        self._clear_log()
        self.status_var.set("正在监测，请在浏览器中登录")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.open_report_button.configure(state="disabled")
        self.progress.start(12)
        self.root.after(400, self.poll_process)

    def stop_monitoring(self) -> None:
        if not self.current_job or not self.process or self.process.poll() is not None:
            return
        Path(self.current_job["stop_path"]).touch()
        self.status_var.set("正在安全停止……")
        self.stop_button.configure(state="disabled")

    def poll_process(self) -> None:
        if not self.process or not self.current_job:
            return
        self._append_new_log(Path(self.current_job["log_path"]))
        return_code = self.process.poll()
        if return_code is None:
            self.root.after(500, self.poll_process)
            return

        self._append_new_log(Path(self.current_job["log_path"]))
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None
        self.progress.stop()
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        status_path = Path(self.current_job["status_path"])
        status: dict[str, Any] = {}
        if status_path.exists():
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        state = status.get("state", "failed")
        output_path = Path(self.current_job["output_path"])
        if state == "completed" and output_path.exists():
            self.last_output = output_path
            dashboard_path = Path(status.get("dashboard_path") or self.current_job.get("dashboard_path", ""))
            if dashboard_path.exists():
                self.last_dashboard = dashboard_path
            self.status_var.set("监测完成")
            self.open_report_button.configure(state="normal")
            from tkinter import messagebox

            messagebox.showinfo(
                "监测完成",
                f"日报已生成：\n{output_path}\n\n本地数据看板也已更新。",
                parent=self.root,
            )
        elif state == "cancelled" or return_code == 130:
            self.status_var.set("已停止")
        else:
            self.status_var.set("运行失败，请查看日志")
            from tkinter import messagebox

            messagebox.showerror(
                "运行失败",
                status.get("error") or "监测未完成，请查看运行日志。",
                parent=self.root,
            )
        self.process = None
        self.current_job = None
        if self.closing_after_stop:
            self.root.destroy()

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _append_new_log(self, path: Path) -> None:
        if not path.exists():
            return
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(self.log_offset)
            text = handle.read()
            self.log_offset = handle.tell()
        if not text:
            return
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def on_close(self) -> None:
        from tkinter import messagebox

        if self.process and self.process.poll() is None:
            if not messagebox.askyesno("正在运行", "监测仍在运行，是否安全停止后退出？", parent=self.root):
                return
            self.closing_after_stop = True
            self.stop_monitoring()
            return
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--scheduled-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--self-test-report", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        return run_worker(args.worker)
    if args.scheduled_run:
        return run_scheduled()
    if args.self_test:
        errors = runtime_self_check()
        if args.self_test_report:
            save_json(
                args.self_test_report,
                {"ok": not errors, "errors": errors, "version": APP_VERSION},
            )
        if errors:
            if sys.stdout:
                for error in errors:
                    print(error)
            return 1
        if sys.stdout:
            print("Environment check passed.")
        return 0
    OpinionMonitorApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
