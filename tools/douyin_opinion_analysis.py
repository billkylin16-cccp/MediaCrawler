# -*- coding: utf-8 -*-
"""Deterministic, fully local risk scoring and lightweight event clustering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


HIGH_RISK_TERMS = (
    "死亡", "伤亡", "爆炸", "火灾", "刑事", "暴力", "群体事件", "集体上访",
    "聚集", "强拆", "腐败", "贪污", "举报", "拖欠工资", "欠薪", "维权",
)
MEDIUM_RISK_TERMS = (
    "投诉", "质疑", "不满", "事故", "污染", "扰民", "纠纷", "冲突", "造假",
    "不作为", "推诿", "违规", "违法", "黑幕", "曝光", "求助", "辟谣",
)


@dataclass(frozen=True)
class AnalysisResult:
    event_id: str
    risk_level: str
    risk_score: int
    risk_reasons: tuple[str, ...]
    summary: str


def _compact(text: Any, limit: int = 180) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _tokens(text: str, ignored: Iterable[str] = ()) -> set[str]:
    normalized = text.casefold()
    for word in ignored:
        normalized = normalized.replace(str(word).casefold(), " ")
    result = set(re.findall(r"[a-z0-9]{2,}", normalized))
    for block in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        result.update(block[index:index + 2] for index in range(len(block) - 1))
    return result


def _similar(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


class OpinionAnalyzer:
    def __init__(self, keywords: Iterable[str]) -> None:
        self.keywords = tuple(str(item).strip() for item in keywords if str(item).strip())

    def analyze(
        self,
        videos: Iterable[dict[str, Any]],
        comments_by_video: dict[str, list[dict[str, Any]]],
    ) -> dict[str, AnalysisResult]:
        video_list = list(videos)
        clusters: list[dict[str, Any]] = []
        assignments: dict[str, str] = {}
        for video in video_list:
            aweme_id = str(video["aweme_id"])
            comment_text = " ".join(
                str(item.get("content") or "")
                for item in comments_by_video.get(aweme_id, [])
            )
            tokens = _tokens(f"{video.get('content', '')} {comment_text}", self.keywords)
            best_index = -1
            best_score = 0.0
            for index, cluster in enumerate(clusters):
                score = _similar(tokens, cluster["tokens"])
                if score > best_score:
                    best_index, best_score = index, score
            if best_index >= 0 and best_score >= 0.28:
                clusters[best_index]["tokens"].update(tokens)
                event_id = clusters[best_index]["event_id"]
            else:
                event_id = f"E{len(clusters) + 1:03d}"
                clusters.append({"event_id": event_id, "tokens": set(tokens)})
            assignments[aweme_id] = event_id

        results: dict[str, AnalysisResult] = {}
        for video in video_list:
            aweme_id = str(video["aweme_id"])
            comments = comments_by_video.get(aweme_id, [])
            text = " ".join(
                [
                    str(video.get("content") or ""),
                    *(str(item.get("content") or "") for item in comments),
                ]
            )
            high_hits = tuple(term for term in HIGH_RISK_TERMS if term in text)
            medium_hits = tuple(term for term in MEDIUM_RISK_TERMS if term in text)
            score = min(100, len(high_hits) * 28 + len(medium_hits) * 12)
            reasons: list[str] = []
            if high_hits:
                reasons.append("高风险词：" + "、".join(high_hits[:5]))
            if medium_hits:
                reasons.append("关注词：" + "、".join(medium_hits[:5]))
            if len(comments) >= 50:
                score = min(100, score + 20)
                reasons.append(f"评论集中（{len(comments)}条）")
            elif len(comments) >= 10:
                score = min(100, score + 10)
                reasons.append(f"评论较多（{len(comments)}条）")
            if video.get("watch_account"):
                score = min(100, score + 5)
                reasons.append("重点账号发布")
            if score >= 60:
                level = "高"
            elif score >= 25:
                level = "中"
            else:
                level = "低"
            if not reasons:
                reasons.append("暂未发现明显风险表达")
            summary_text = _compact(video.get("content") or "（无文字描述）", 120)
            summary = f"{video.get('account') or '未知账号'}发布：{summary_text}"
            if comments:
                summary += f"；关联评论{len(comments)}条"
            results[aweme_id] = AnalysisResult(
                event_id=assignments[aweme_id],
                risk_level=level,
                risk_score=score,
                risk_reasons=tuple(reasons),
                summary=summary,
            )
        return results

