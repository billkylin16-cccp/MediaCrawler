from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import config
import douyin_opinion_app
from media_platform.douyin.core import DouYinCrawler
from store.douyin_opinion_report import DouyinOpinionReport
from tools.douyin_image_ocr import OcrPageResult
from tools.douyin_opinion_analysis import OpinionAnalyzer
from tools.douyin_opinion_history import OpinionHistory


def _ts(hour: int = 3) -> int:
    return int(datetime(2026, 8, 24, hour, tzinfo=timezone.utc).timestamp())


@pytest.mark.asyncio
async def test_douyin_home_navigation_retries_timeout(monkeypatch):
    class FakePage:
        def __init__(self):
            self.calls = 0
            self.url = "about:blank"

        async def goto(self, url, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise PlaywrightTimeoutError("slow")
            self.url = url

        async def evaluate(self, _script):
            return "loading"

    monkeypatch.setattr(config, "DOUYIN_NAVIGATION_RETRIES", 2)
    monkeypatch.setattr(config, "DOUYIN_NAVIGATION_TIMEOUT_MS", 10_000)
    async def no_sleep(_delay):
        return None
    monkeypatch.setattr("media_platform.douyin.core.asyncio.sleep", no_sleep)
    crawler = DouYinCrawler()
    crawler.context_page = FakePage()
    await crawler.open_index_with_retry()
    assert crawler.context_page.calls == 2


@pytest.mark.asyncio
async def test_history_deduplicates_across_reports_and_updates_dashboard(tmp_path: Path):
    history_path = tmp_path / "history.db"
    dashboard_path = tmp_path / "dashboard.html"
    first = DouyinOpinionReport(
        keywords=["西陶"],
        target_date="2026-08-24",
        output_path=tmp_path / "first.xlsx",
        history_path=history_path,
        dashboard_path=dashboard_path,
        deduplicate=True,
    )
    assert first.add_video(
        {
            "aweme_id": "same-video",
            "desc": "西陶当天内容",
            "create_time": _ts(),
            "author": {"nickname": "发布人"},
        }
    )
    first.flush()
    assert dashboard_path.is_file()
    assert "西陶当天内容" in dashboard_path.read_text(encoding="utf-8")

    second = DouyinOpinionReport(
        keywords=["西陶"],
        target_date="2026-08-24",
        output_path=tmp_path / "second.xlsx",
        history_path=history_path,
        deduplicate=True,
    )
    assert not second.add_video(
        {
            "aweme_id": "same-video",
            "desc": "西陶当天内容",
            "create_time": _ts(),
            "author": {"nickname": "发布人"},
        }
    )
    assert second.duplicate_count == 1
    assert "video:same-video" in OpinionHistory(history_path).seen_keys()


@pytest.mark.asyncio
async def test_audio_transcript_matches_keyword_and_creates_high_risk_analysis(tmp_path: Path):
    output = tmp_path / "speech.xlsx"
    report = DouyinOpinionReport(
        keywords=["西陶"],
        target_date="2026-08-24",
        output_path=output,
    )
    assert report.add_video(
        {
            "aweme_id": "speech-video",
            "desc": "",
            "create_time": _ts(),
            "author": {"nickname": "语音发布人"},
        },
        ocr_pages=[
            OcrPageResult(
                page=1,
                text="西陶发生死亡爆炸事件，群众举报",
                confidence=None,
                source="audio",
            )
        ],
    )
    report.flush()
    workbook = load_workbook(output, data_only=True)
    sheet = workbook["舆论监测"]
    assert "【视频语音识别】" in sheet["C3"].value
    assert "命中来源：视频语音识别" in sheet["D3"].value
    assert "风险：高" in sheet["D3"].value
    assert workbook["事件分析"]["B2"].value == "高"
    workbook.close()


def test_local_event_analysis_merges_similar_content():
    analyzer = OpinionAnalyzer(["西陶"])
    results = analyzer.analyze(
        [
            {"aweme_id": "1", "account": "甲", "content": "西陶道路施工导致交通拥堵", "watch_account": ""},
            {"aweme_id": "2", "account": "乙", "content": "西陶道路施工现场交通拥堵", "watch_account": ""},
        ],
        {},
    )
    assert results["1"].event_id == results["2"].event_id


def test_daily_schedule_uses_windows_task_scheduler(monkeypatch):
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(douyin_opinion_app.subprocess, "run", fake_run)
    monkeypatch.setattr(douyin_opinion_app, "_job_command", lambda: '"app.exe" --scheduled-run')
    douyin_opinion_app.configure_daily_schedule(True, "08:30")
    assert calls[0][0] == "schtasks.exe"
    assert "/Create" in calls[0]
    assert "08:30" in calls[0]
    assert '"app.exe" --scheduled-run' in calls[0]


def test_missing_dates_only_returns_days_without_success(tmp_path: Path):
    history = OpinionHistory(tmp_path / "history.db")
    history.start_run("one", "2026-08-23", "one.xlsx")
    history.finish_run("one", "completed")
    assert [item.isoformat() for item in history.missing_dates(
        datetime(2026, 8, 24).date(), 2
    )] == ["2026-08-24"]


def test_worker_retries_failed_run_and_records_success(monkeypatch, tmp_path: Path):
    calls = []

    async def fake_run(_job):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise RuntimeError("temporary network error")
        return "completed"

    monkeypatch.setattr(douyin_opinion_app, "_run_crawler_job", fake_run)
    monkeypatch.setattr(douyin_opinion_app.time, "sleep", lambda _seconds: None)
    job_path = tmp_path / "retry-job.json"
    status_path = tmp_path / "retry-job.status.json"
    job = {
        "run_id": "retry-job",
        "keywords": ["西陶"],
        "watch_accounts": [],
        "opinion_date": "2026-08-24",
        "output_path": str(tmp_path / "report.xlsx"),
        "dashboard_path": str(tmp_path / "dashboard.html"),
        "history_path": str(tmp_path / "history.db"),
        "log_path": str(tmp_path / "retry-job.log"),
        "status_path": str(status_path),
        "stop_path": str(tmp_path / "retry-job.stop"),
        "run_retries": 1,
    }
    douyin_opinion_app.save_json(job_path, job)
    assert douyin_opinion_app.run_worker(job_path) == 0
    status = douyin_opinion_app.json.loads(status_path.read_text(encoding="utf-8"))
    assert status["state"] == "completed"
    assert status["attempt"] == 2
    assert calls == [1, 2]
