from pathlib import Path

from media_platform.douyin.help import parse_creator_info_from_url
from tools.douyin_image_ocr import extract_ocr_lines
from tools.douyin_watchlist import choose_exact_user, public_account_label


class CurrentOcrResult:
    txts = ("西陶镇", "轮播图片")
    scores = (0.98, 0.87)


def test_extract_ocr_lines_supports_current_and_legacy_shapes():
    assert extract_ocr_lines(CurrentOcrResult()) == [
        ("西陶镇", 0.98),
        ("轮播图片", 0.87),
    ]
    legacy = [
        [[[0, 0], [1, 0], [1, 1], [0, 1]], "重点信息", 0.93],
    ]
    assert extract_ocr_lines((legacy, {"all": 0.1})) == [("重点信息", 0.93)]


def test_choose_exact_user_avoids_similar_account():
    response = {
        "data": [
            {"user_info": {"sec_uid": "wrong", "unique_id": "xuhaoran8888", "nickname": "相似账号"}},
            {"user_info": {"sec_uid": "right", "unique_id": "xuhaoran888", "nickname": "重点账号"}},
        ]
    }
    chosen = choose_exact_user(response, "@XuHaoRan888")
    assert chosen is not None
    assert chosen["sec_uid"] == "right"
    assert public_account_label(chosen, "fallback") == "重点账号（xuhaoran888）"


def test_default_watchlist_uses_seven_unique_creator_urls():
    watchlist_path = Path(__file__).resolve().parents[1] / "douyin_watch_accounts.txt"
    entries = [
        line.strip()
        for line in watchlist_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert len(entries) == 7
    assert all(entry.startswith("https://www.douyin.com/user/") for entry in entries)

    sec_user_ids = {
        parse_creator_info_from_url(entry).sec_user_id
        for entry in entries
    }
    assert len(sec_user_ids) == 7
    assert all(sec_user_id.startswith("MS4wLjAB") for sec_user_id in sec_user_ids)
