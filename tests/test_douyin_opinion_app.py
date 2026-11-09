from pathlib import Path

from douyin_opinion_app import (
    create_job,
    load_settings,
    next_report_path,
    normalize_keywords,
    normalize_video_entries,
    read_watch_accounts,
    safe_file_component,
)
from tools.runtime_paths import browser_data_dir, resource_path, runtime_data_root


def test_normalize_keywords_accepts_chinese_and_ascii_separators():
    assert normalize_keywords("西陶，武陟; 招聘、民生") == [
        "西陶",
        "武陟",
        "招聘",
        "民生",
    ]


def test_normalize_video_entries_accepts_modal_links_and_deduplicates():
    target = "https://www.douyin.com/jingxuan/search/test?modal_id=7681224368026714971"
    assert normalize_video_entries(f"{target}；7681224368026714971 {target}") == [
        target,
        "7681224368026714971",
    ]


def test_next_report_path_never_overwrites_existing_file(tmp_path: Path):
    first = next_report_path(tmp_path, "2026-09-01", ["西陶"])
    assert first.name == "9.01抖音舆论检测.xlsx"
    first.touch()

    second = next_report_path(tmp_path, "2026-09-01", ["西陶"])
    assert second.name == "9.01抖音舆论检测-西陶.xlsx"


def test_watchlist_ignores_comments_and_blank_lines(tmp_path: Path):
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("# account\n\nhttps://www.douyin.com/user/one\nMS4wLjABtwo\n", encoding="utf-8")
    assert read_watch_accounts(watchlist) == [
        "https://www.douyin.com/user/one",
        "MS4wLjABtwo",
    ]


def test_runtime_paths_honor_writable_data_environment(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MEDIACRAWLER_DATA_DIR", str(tmp_path))
    assert runtime_data_root() == tmp_path.resolve()
    assert browser_data_dir() == tmp_path.resolve() / "browser_data"
    assert resource_path("libs", "douyin.js").is_file()


def test_safe_file_component_removes_windows_path_characters():
    assert safe_file_component('西陶:*?"<>|') == "西陶"


def test_legacy_settings_migrate_to_editable_location_and_full_day(tmp_path: Path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        '{"keywords":"民生","opinion_date":"2026-08-31"}', encoding="utf-8"
    )
    settings = load_settings(settings_path)
    assert settings["locations"] == "西陶"
    assert settings["start_datetime"] == "2026-08-31 00:00"
    assert settings["end_datetime"] == "2026-08-31 23:59"


def test_create_job_keeps_custom_time_and_location(tmp_path: Path):
    paths = {
        "jobs": tmp_path / "jobs",
        "logs": tmp_path / "logs",
        "history": tmp_path / "history.db",
        "dashboard": tmp_path / "dashboard.html",
    }
    paths["jobs"].mkdir()
    paths["logs"].mkdir()
    settings = {
        "keywords": "民生",
        "locations": "西陶,西陶镇",
        "opinion_date": "2026-08-31",
        "start_datetime": "2026-08-31 09:15",
        "end_datetime": "2026-08-31 17:45",
        "output_dir": str(tmp_path),
    }
    _job_path, job = create_job(settings, [], paths)
    assert job["keywords"] == ["民生"]
    assert job["locations"] == ["西陶", "西陶镇"]
    assert job["start_datetime"] == "2026-08-31 09:15"
    assert job["end_datetime"] == "2026-08-31 17:45"
