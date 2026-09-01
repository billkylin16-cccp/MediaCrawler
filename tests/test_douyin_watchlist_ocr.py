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
