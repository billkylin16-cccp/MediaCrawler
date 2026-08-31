from datetime import datetime, timezone
from pathlib import Path

from openpyxl import load_workbook
import pytest

from store.douyin_opinion_report import DouyinOpinionReport


def _ts(hour: int) -> int:
    return int(datetime(2026, 8, 24, hour, tzinfo=timezone.utc).timestamp())


def test_windows_powershell_launcher_is_ascii_only():
    launcher = Path(__file__).parents[1] / "run_douyin_opinion.ps1"
    assert launcher.read_bytes().isascii()


@pytest.mark.asyncio
async def test_report_filters_and_writes_requested_layout(tmp_path):
    output = tmp_path / "8.24抖音舆论检测.xlsx"
    report = DouyinOpinionReport(
        keywords=["武陟", "西陶"],
        target_date="2026-08-24",
        output_path=output,
        match="all",
    )
    assert report.add_video({
        "aweme_id": "video-1",
        "desc": "武陟西陶当天视频",
        "create_time": _ts(3),
        "author": {"nickname": "视频发布人"},
    })
    assert not report.add_video({
        "aweme_id": "video-2",
        "desc": "只有武陟",
        "create_time": _ts(3),
        "author": {"nickname": "不应收录"},
    })
    assert report.add_video({
        "aweme_id": "video-3",
        "desc": "=武陟西陶公式内容",
        "create_time": _ts(6),
        "author": {"nickname": "@公式发布人"},
    })
    await report.add_comments("video-1", [
        {
            "cid": "comment-1",
            "text": "今天的相关评论",
            "create_time": _ts(4),
            "user": {"nickname": "评论发布人"},
        },
        {
            "cid": "comment-2",
            "text": "非当天评论",
            "create_time": int(datetime(2026, 8, 25, 3, tzinfo=timezone.utc).timestamp()),
            "user": {"nickname": "不应收录"},
        },
        {
            "cid": "comment-3",
            "text": "=HYPERLINK(\"https://invalid.example\")",
            "create_time": _ts(5),
            "user": {"nickname": "+公式账号"},
        },
    ])
    report.flush()

    workbook = load_workbook(output, data_only=True)
    worksheet = workbook["舆论监测"]
    assert worksheet["A1"].value == "8.24抖音舆论检测"
    assert [worksheet.cell(2, index).value for index in range(1, 5)] == [
        "序号", "账号名称（发布人名称）", "发布内容", "关键信息"
    ]
    assert worksheet["B3"].value == "视频发布人"
    assert worksheet["B4"].value == "评论发布人"
    assert worksheet["B5"].value == "'+公式账号"
    assert worksheet["C5"].data_type != "f"
    assert worksheet["B6"].value == "'@公式发布人"
    assert worksheet["C6"].value == "'=武陟西陶公式内容"
    assert worksheet["C6"].data_type != "f"
    assert worksheet.max_row == 6
    workbook.close()


def test_report_any_match_and_invalid_output(tmp_path):
    report = DouyinOpinionReport(
        keywords=["WUZHI", "西陶"],
        target_date="2026-08-24",
        output_path=tmp_path / "report.xlsx",
        match="any",
    )
    assert report.add_video({
        "aweme_id": "video-any",
        "desc": "wuzhi 当天视频",
        "create_time": _ts(3),
        "author": {"nickname": "发布人"},
    })

    with pytest.raises(ValueError, match=".xlsx"):
        DouyinOpinionReport(
            keywords=["武陟"],
            target_date="2026-08-24",
            output_path=tmp_path / "report.csv",
        )

    existing = tmp_path / "existing.xlsx"
    existing.touch()
    with pytest.raises(FileExistsError, match="不会覆盖"):
        DouyinOpinionReport(
            keywords=["武陟"],
            target_date="2026-08-24",
            output_path=existing,
        )
