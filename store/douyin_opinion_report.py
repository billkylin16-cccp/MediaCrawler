# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# Local fork modification: compact, date-bounded Douyin review workbook.
# This module only processes data returned by the user's normal authenticated
# browser session. It does not perform network requests or bypass access limits.

"""Build the four-column Douyin opinion-monitor workbook.

The collector supplies raw public result objects to :class:`DouyinOpinionReport`.
This storage-layer module only filters them locally and writes the requested
review sheet; it does not make any network calls itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from tools.douyin_image_ocr import OcrPageResult
from tools.douyin_opinion_analysis import AnalysisResult, OpinionAnalyzer
from tools.douyin_opinion_dashboard import write_dashboard
from tools.douyin_opinion_history import OpinionHistory


CHINA_TZ = timezone(timedelta(hours=8))
HEADERS = ["序号", "账号名称（发布人名称）", "发布内容", "关键信息"]


@dataclass(frozen=True)
class VideoRecord:
    aweme_id: str
    account: str
    content: str
    created_at: datetime
    keywords: tuple[str, ...]
    description_keywords: tuple[str, ...]
    locations: tuple[str, ...]
    metadata_locations: tuple[str, ...]
    location_evidence: tuple[str, ...]
    ocr_pages: tuple[OcrPageResult, ...]
    watch_account: str
    discovery_source: str

    @property
    def url(self) -> str:
        return f"https://www.douyin.com/video/{self.aweme_id}"


@dataclass(frozen=True)
class CommentRecord:
    comment_id: str
    aweme_id: str
    account: str
    content: str
    created_at: datetime
    keywords: tuple[str, ...]


def _china_today() -> date:
    return datetime.now(CHINA_TZ).date()


def _parse_target_date(value: str | date | None) -> date:
    if value is None or value == "":
        return _china_today()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(CHINA_TZ).date()
    if isinstance(value, date):
        return value
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("舆情日期必须为 YYYY-MM-DD，例如 2026-08-24")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("舆情日期必须为 YYYY-MM-DD，例如 2026-08-24") from exc


def _parse_window_datetime(value: str | datetime, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=CHINA_TZ) if value.tzinfo is None else value.astimezone(CHINA_TZ)
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=CHINA_TZ)
        except ValueError:
            continue
    raise ValueError(f"{field_name}必须为 YYYY-MM-DD HH:MM，例如 2026-08-24 08:30")


def _flatten_location_values(value: Any) -> list[str]:
    if isinstance(value, str):
        cleaned = _clean(value)
        return [cleaned] if cleaned else []
    if isinstance(value, dict):
        result: list[str] = []
        location_keys = {
            "name", "title", "text", "poi_name", "poi_address", "address",
            "address_info", "province", "city", "district", "town", "village",
            "location", "location_name", "ip_attribution", "ip_location",
        }
        for key, nested in value.items():
            if str(key).casefold() in location_keys:
                result.extend(_flatten_location_values(nested))
            elif isinstance(nested, (dict, list, tuple)):
                result.extend(_flatten_location_values(nested))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for nested in value:
            result.extend(_flatten_location_values(nested))
        return result
    return []


def extract_location_evidence(aweme: Dict[str, Any]) -> tuple[str, ...]:
    """Return public POI/address/IP-location strings from heterogeneous Douyin payloads."""

    container_labels = {
        "poi_info": "POI",
        "poi_struct": "POI",
        "address_info": "地址",
        "location": "定位",
        "anchor_info": "地点锚点",
    }
    direct_labels = {
        "poi_name": "POI",
        "poi_address": "POI地址",
        "address": "地址",
        "province": "省",
        "city": "市",
        "district": "区县",
        "ip_attribution": "IP属地",
        "ip_location": "IP属地",
    }
    evidence: list[str] = []
    for key, label in container_labels.items():
        for value in _flatten_location_values(aweme.get(key)):
            evidence.append(f"{label}：{value}")
    for key, label in direct_labels.items():
        for value in _flatten_location_values(aweme.get(key)):
            evidence.append(f"{label}：{value}")
    return tuple(dict.fromkeys(evidence))


def _to_china_datetime(value: Any) -> Optional[datetime]:
    """Convert Douyin's seconds/milliseconds timestamp to China time."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ)
        return value.astimezone(CHINA_TZ)
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        timestamp = float(value)
        if timestamp > 20_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(CHINA_TZ)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=CHINA_TZ)
    return parsed.astimezone(CHINA_TZ)


def _clean(value: Any) -> str:
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", str(value or ""))
    return " ".join(text.split())


def _excel_safe_text(value: Any) -> str:
    """Keep user-controlled text from being interpreted as an Excel formula."""
    text = _clean(value)
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


class DouyinOpinionReport:
    """Accumulate selected videos/comments and export one review workbook."""

    def __init__(
        self,
        keywords: Iterable[str],
        target_date: str | date | None = None,
        locations: Iterable[str] = (),
        start_datetime: str | datetime | None = None,
        end_datetime: str | datetime | None = None,
        output_path: str | Path | None = None,
        match: str = "all",
        history_path: str | Path | None = None,
        deduplicate: bool = False,
        dashboard_path: str | Path | None = None,
    ) -> None:
        self.keywords = tuple(item.strip() for item in keywords if item and item.strip())
        self.locations = tuple(item.strip() for item in locations if item and item.strip())
        if not self.keywords and not self.locations:
            raise ValueError("舆情监测至少需要一个关键词或关键定位")
        if match not in {"all", "any"}:
            raise ValueError("关键词匹配规则只能是 all 或 any")
        if start_datetime is None and end_datetime is None:
            self.target_date = _parse_target_date(target_date)
            self.start_datetime = datetime.combine(
                self.target_date, datetime.min.time(), tzinfo=CHINA_TZ
            )
            self.end_datetime = datetime.combine(
                self.target_date, datetime.max.time(), tzinfo=CHINA_TZ
            )
        elif start_datetime is not None and end_datetime is not None:
            self.start_datetime = _parse_window_datetime(start_datetime, "监测开始时间")
            self.end_datetime = _parse_window_datetime(end_datetime, "监测结束时间")
            if self.end_datetime < self.start_datetime:
                raise ValueError("监测结束时间不能早于开始时间")
            self.target_date = self.start_datetime.date()
        else:
            raise ValueError("监测开始时间和结束时间必须同时填写")
        self.match = match
        default_name = f"{self.target_date.month}.{self.target_date.day:02d}抖音舆论检测.xlsx"
        self.output_path = Path(output_path) if output_path else Path(default_name)
        if self.output_path.suffix.lower() != ".xlsx":
            raise ValueError("舆情报表输出文件必须使用 .xlsx 扩展名")
        if self.output_path.exists():
            raise FileExistsError(
                f"舆情报表已存在，不会覆盖：{self.output_path}；请更换 --opinion_output"
            )
        self._videos: Dict[str, VideoRecord] = {}
        self._comments: Dict[str, CommentRecord] = {}
        self._analysis: Dict[str, AnalysisResult] = {}
        self._history = OpinionHistory(history_path) if history_path else None
        self._seen_keys = self._history.seen_keys() if self._history and deduplicate else set()
        self._deduplicate = bool(deduplicate)
        self.duplicate_count = 0
        self.dashboard_path = Path(dashboard_path) if dashboard_path else None

    def _matched_keywords(self, text: str) -> tuple[str, ...]:
        folded_text = text.casefold()
        return tuple(keyword for keyword in self.keywords if keyword.casefold() in folded_text)

    def _matches(self, hit: tuple[str, ...]) -> bool:
        return len(hit) == len(self.keywords) if self.match == "all" else bool(hit)

    def _matched_locations(self, text: str) -> tuple[str, ...]:
        folded_text = text.casefold()
        return tuple(location for location in self.locations if location.casefold() in folded_text)

    def is_target_time(self, aweme: Dict[str, Any]) -> bool:
        created_at = self.publish_datetime(aweme)
        return bool(created_at and self.start_datetime <= created_at <= self.end_datetime)

    def is_target_date(self, aweme: Dict[str, Any]) -> bool:
        return self.is_target_time(aweme)

    @staticmethod
    def publish_datetime(aweme: Dict[str, Any]) -> Optional[datetime]:
        return _to_china_datetime(aweme.get("create_time"))

    @staticmethod
    def publish_date(aweme: Dict[str, Any]) -> Optional[date]:
        created_at = _to_china_datetime(aweme.get("create_time"))
        return created_at.date() if created_at else None

    def add_video(
        self,
        aweme: Dict[str, Any],
        ocr_pages: Iterable[OcrPageResult] = (),
        watch_account: str = "",
        discovery_source: str = "",
    ) -> bool:
        """Keep a video only when its text and publish date satisfy the report rule.

        The return value tells the caller whether comments for this video should
        be fetched. Duplicate search hits therefore do not trigger repeat work.
        """
        aweme_id = _clean(aweme.get("aweme_id"))
        if not aweme_id or aweme_id in self._videos:
            return False
        created_at = _to_china_datetime(aweme.get("create_time"))
        content = _clean(aweme.get("desc"))
        normalized_ocr_pages = tuple(
            OcrPageResult(
                page=page.page,
                text=_clean(page.text),
                confidence=page.confidence,
                source=page.source,
            )
            for page in ocr_pages
            if _clean(page.text)
        )
        description_hits = self._matched_keywords(content)
        combined_text = " ".join([content, *(page.text for page in normalized_ocr_pages)])
        hits = self._matched_keywords(combined_text)
        location_evidence = extract_location_evidence(aweme)
        metadata_location_hits = self._matched_locations(" ".join(location_evidence))
        content_location_hits = self._matched_locations(combined_text)
        location_hits = tuple(dict.fromkeys((*metadata_location_hits, *content_location_hits)))
        keyword_match = bool(self.keywords) and self._matches(hits)
        location_match = bool(location_hits)
        if (
            not created_at
            or not (self.start_datetime <= created_at <= self.end_datetime)
            or not (keyword_match or location_match)
        ):
            return False
        if self._deduplicate and f"video:{aweme_id}" in self._seen_keys:
            self.duplicate_count += 1
            return False
        author = aweme.get("author") or {}
        account = _excel_safe_text(author.get("nickname")) or "未知账号"
        self._videos[aweme_id] = VideoRecord(
            aweme_id=aweme_id,
            account=account,
            content=_excel_safe_text(content) or "（视频未提供文字描述）",
            created_at=created_at,
            keywords=hits,
            description_keywords=description_hits,
            locations=location_hits,
            metadata_locations=metadata_location_hits,
            location_evidence=location_evidence,
            ocr_pages=normalized_ocr_pages,
            watch_account=_excel_safe_text(watch_account),
            discovery_source=_excel_safe_text(discovery_source),
        )
        return True

    async def add_comments(self, aweme_id: str, comments: List[Dict[str, Any]]) -> None:
        """Keep comments published on the monitored date for a selected video."""
        if aweme_id not in self._videos:
            return
        for comment in comments:
            comment_id = _clean(comment.get("cid"))
            comment_key = f"{aweme_id}:{comment_id}"
            if not comment_id or comment_key in self._comments:
                continue
            if self._deduplicate and f"comment:{comment_key}" in self._seen_keys:
                self.duplicate_count += 1
                continue
            created_at = _to_china_datetime(comment.get("create_time"))
            if not created_at or not (self.start_datetime <= created_at <= self.end_datetime):
                continue
            user = comment.get("user") or {}
            content = _clean(comment.get("text"))
            self._comments[comment_key] = CommentRecord(
                comment_id=comment_id,
                aweme_id=aweme_id,
                account=_excel_safe_text(user.get("nickname")) or "未知账号",
                content=_excel_safe_text(content) or "（评论未提供文字内容）",
                created_at=created_at,
                keywords=self._matched_keywords(content),
            )

    @staticmethod
    def _time_text(value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _recognized_page_label(page: OcrPageResult) -> str:
        if page.source == "audio":
            return "语音"
        suffix = "帧" if page.source == "video" else "张"
        return f"第{page.page}{suffix}"

    def _rows(self) -> list[list[Any]]:
        rows: list[list[Any]] = []
        videos = sorted(self._videos.values(), key=lambda item: (item.created_at, item.aweme_id))
        comments_by_video: Dict[str, list[CommentRecord]] = {}
        for comment in self._comments.values():
            comments_by_video.setdefault(comment.aweme_id, []).append(comment)

        for video in videos:
            analysis = self._analysis.get(video.aweme_id)
            matched = "、".join(video.keywords) or "未命中（由关键定位收录）"
            source_parts: list[str] = []
            if video.description_keywords:
                source_parts.append("作品描述")
            ocr_hit_pages = [
                page.page
                for page in video.ocr_pages
                if page.source == "image" and self._matched_keywords(page.text)
            ]
            video_hit_frames = [
                page.page
                for page in video.ocr_pages
                if page.source == "video" and self._matched_keywords(page.text)
            ]
            if ocr_hit_pages:
                source_parts.append(
                    "图片OCR第" + "、".join(str(page) for page in ocr_hit_pages) + "张"
                )
            if video_hit_frames:
                source_parts.append(
                    "视频OCR第" + "、".join(str(page) for page in video_hit_frames) + "帧"
                )
            speech_hits = [
                page.page
                for page in video.ocr_pages
                if page.source == "audio" and self._matched_keywords(page.text)
            ]
            if speech_hits:
                source_parts.append("视频语音识别")
            if video.metadata_locations:
                source_parts.append("作品定位信息")
            elif video.locations:
                source_parts.append("内容地点文字")
            if not source_parts:
                source_parts.append("搜索命中")

            content_parts = [video.content]
            if video.ocr_pages:
                ocr_text = "｜".join(
                    (
                        f"{self._recognized_page_label(page)}："
                        f"{page.text[:1000]}"
                    )
                    for page in video.ocr_pages
                )
                sources = {page.source for page in video.ocr_pages}
                if len(sources) > 1:
                    label = "多模态识别"
                elif "audio" in sources:
                    label = "视频语音识别"
                elif "video" in sources:
                    label = "视频抽帧OCR"
                else:
                    label = "图片OCR"
                content_parts.append(f"【{label}】{ocr_text[:12000]}")
            content = "\n".join(content_parts)

            info_parts = [
                "视频",
                f"发布时间：{self._time_text(video.created_at)}",
                f"关键词匹配：{matched}",
                f"命中来源：{'、'.join(source_parts)}",
            ]
            if video.locations:
                info_parts.append(f"关键定位：{'、'.join(video.locations)}")
            if video.location_evidence:
                info_parts.append(f"作品定位：{'；'.join(video.location_evidence)}")
            if video.discovery_source:
                info_parts.append(f"发现方式：{video.discovery_source}")
            if video.watch_account:
                info_parts.append(f"重点账号：{video.watch_account}")
            if analysis:
                info_parts.extend(
                    [
                        f"事件：{analysis.event_id}",
                        f"风险：{analysis.risk_level}（{analysis.risk_score}分）",
                        f"研判：{'；'.join(analysis.risk_reasons)}",
                    ]
                )
            info_parts.append(f"链接：{video.url}")
            rows.append([
                None,
                video.account,
                content,
                "｜".join(info_parts),
            ])
            for comment in sorted(comments_by_video.get(video.aweme_id, []), key=lambda item: (item.created_at, item.comment_id)):
                matched = "、".join(comment.keywords) or "关联视频"
                rows.append([
                    None,
                    comment.account,
                    f"【评论】{comment.content}",
                    f"评论｜发布时间：{self._time_text(comment.created_at)}｜关联视频发布人：{video.account}｜匹配：{matched}｜链接：{video.url}",
                ])
        for index, row in enumerate(rows, start=1):
            row[0] = index
        return rows

    def _prepare_analysis(self) -> None:
        comments_by_video: Dict[str, list[dict[str, Any]]] = {}
        for comment in self._comments.values():
            comments_by_video.setdefault(comment.aweme_id, []).append(
                {"content": comment.content, "account": comment.account}
            )
        videos = []
        for video in self._videos.values():
            recognized_text = " ".join(page.text for page in video.ocr_pages)
            videos.append(
                {
                    "aweme_id": video.aweme_id,
                    "account": video.account,
                    "content": " ".join([video.content, recognized_text]).strip(),
                    "watch_account": video.watch_account,
                }
            )
        self._analysis = OpinionAnalyzer((*self.keywords, *self.locations)).analyze(
            videos, comments_by_video
        )

    def _write_analysis_sheets(self, workbook: Workbook) -> None:
        event_sheet = workbook.create_sheet("事件分析")
        event_headers = ["事件编号", "风险等级", "风险分值", "账号", "摘要", "风险依据", "视频链接"]
        for column, header in enumerate(event_headers, start=1):
            cell = event_sheet.cell(1, column, header)
            cell.font = Font(name="Microsoft YaHei", bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
        for row_number, video in enumerate(
            sorted(self._videos.values(), key=lambda item: (item.created_at, item.aweme_id)),
            start=2,
        ):
            analysis = self._analysis[video.aweme_id]
            values = [
                analysis.event_id,
                analysis.risk_level,
                analysis.risk_score,
                video.account,
                analysis.summary,
                "；".join(analysis.risk_reasons),
                video.url,
            ]
            for column, value in enumerate(values, start=1):
                cell = event_sheet.cell(row_number, column, value)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if column == 2:
                    color = {"高": "C00000", "中": "F4B183", "低": "70AD47"}[analysis.risk_level]
                    cell.fill = PatternFill("solid", fgColor=color)
                    cell.font = Font(name="Microsoft YaHei", color="FFFFFF", bold=True)
        for column, width in enumerate((12, 12, 12, 24, 60, 50, 52), start=1):
            event_sheet.column_dimensions[chr(64 + column)].width = width
        event_sheet.freeze_panes = "A2"

        summary_sheet = workbook.create_sheet("统计摘要")
        risk_counts = {"高": 0, "中": 0, "低": 0}
        for result in self._analysis.values():
            risk_counts[result.risk_level] += 1
        summary_rows = [
            ("监测开始", self.start_datetime.strftime("%Y-%m-%d %H:%M")),
            ("监测结束", self.end_datetime.strftime("%Y-%m-%d %H:%M")),
            ("关键词", "、".join(self.keywords)),
            ("关键定位", "、".join(self.locations)),
            ("新增视频", len(self._videos)),
            ("新增评论", len(self._comments)),
            ("跨日期重复项", self.duplicate_count),
            ("高风险事件", risk_counts["高"]),
            ("中风险事件", risk_counts["中"]),
            ("低风险事件", risk_counts["低"]),
        ]
        for row, (label, value) in enumerate(summary_rows, start=1):
            summary_sheet.cell(row, 1, label).font = Font(name="Microsoft YaHei", bold=True)
            summary_sheet.cell(row, 2, value)
        summary_sheet.column_dimensions["A"].width = 22
        summary_sheet.column_dimensions["B"].width = 60

    def _history_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        comments_by_video: Dict[str, list[CommentRecord]] = {}
        for comment in self._comments.values():
            comments_by_video.setdefault(comment.aweme_id, []).append(comment)
        for video in self._videos.values():
            analysis = self._analysis[video.aweme_id]
            source = "、".join(sorted({page.source for page in video.ocr_pages}))
            items.append(
                {
                    "item_key": f"video:{video.aweme_id}",
                    "kind": "video",
                    "item_id": video.aweme_id,
                    "aweme_id": video.aweme_id,
                    "account": video.account,
                    "content": video.content,
                    "created_at": video.created_at.isoformat(),
                    "report_date": self.target_date.isoformat(),
                    "keywords": list(video.keywords),
                    "source": source or video.discovery_source,
                    "risk_level": analysis.risk_level,
                    "risk_score": analysis.risk_score,
                    "event_id": analysis.event_id,
                    "summary": analysis.summary,
                    "url": video.url,
                    "payload": {
                        "risk_reasons": list(analysis.risk_reasons),
                        "locations": list(video.locations),
                        "location_evidence": list(video.location_evidence),
                        "window_start": self.start_datetime.isoformat(),
                        "window_end": self.end_datetime.isoformat(),
                    },
                }
            )
            for comment in comments_by_video.get(video.aweme_id, []):
                items.append(
                    {
                        "item_key": f"comment:{video.aweme_id}:{comment.comment_id}",
                        "kind": "comment",
                        "item_id": comment.comment_id,
                        "aweme_id": video.aweme_id,
                        "account": comment.account,
                        "content": comment.content,
                        "created_at": comment.created_at.isoformat(),
                        "report_date": self.target_date.isoformat(),
                        "keywords": list(comment.keywords),
                        "source": "评论",
                        "risk_level": analysis.risk_level,
                        "risk_score": analysis.risk_score,
                        "event_id": analysis.event_id,
                        "summary": analysis.summary,
                        "url": video.url,
                    }
                )
        return items

    def flush(self) -> Path:
        """Write the requested single-sheet Excel report, including an empty report."""
        self._prepare_analysis()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "舆论监测"
        worksheet.sheet_view.showGridLines = False

        if self.start_datetime.date() == self.end_datetime.date():
            title = f"{self.target_date.month}.{self.target_date.day:02d}抖音舆论检测"
        else:
            title = (
                f"{self.start_datetime.month}.{self.start_datetime.day:02d}-"
                f"{self.end_datetime.month}.{self.end_datetime.day:02d}抖音舆论检测"
            )
        worksheet.merge_cells("A1:D1")
        worksheet["A1"] = title
        worksheet["A1"].font = Font(name="Microsoft YaHei", bold=True, size=14, color="FFFFFF")
        worksheet["A1"].fill = PatternFill("solid", fgColor="17365D")
        worksheet["A1"].alignment = Alignment(horizontal="center", vertical="center")
        worksheet.row_dimensions[1].height = 28

        header_fill = PatternFill("solid", fgColor="1F4E78")
        header_font = Font(name="Microsoft YaHei", bold=True, color="FFFFFF")
        thin_border = Border(
            left=Side(style="thin", color="D9E2F3"),
            right=Side(style="thin", color="D9E2F3"),
            top=Side(style="thin", color="D9E2F3"),
            bottom=Side(style="thin", color="D9E2F3"),
        )
        for column, header in enumerate(HEADERS, start=1):
            cell = worksheet.cell(row=2, column=column, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = thin_border
        worksheet.row_dimensions[2].height = 26

        for row_number, values in enumerate(self._rows(), start=3):
            for column, value in enumerate(values, start=1):
                cell = worksheet.cell(row=row_number, column=column, value=value)
                cell.font = Font(name="Microsoft YaHei", size=10)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                cell.border = thin_border
            estimated_lines = max(
                4,
                (len(str(values[2] or "")) // 42) + 1,
                (len(str(values[3] or "")) // 48) + 1,
            )
            worksheet.row_dimensions[row_number].height = min(300, estimated_lines * 15)

        for column, width in {"A": 9, "B": 24, "C": 68, "D": 72}.items():
            worksheet.column_dimensions[column].width = width
        worksheet.freeze_panes = "A3"
        worksheet.auto_filter.ref = f"A2:D{max(2, worksheet.max_row)}"
        self._write_analysis_sheets(workbook)
        try:
            workbook.save(self.output_path)
        finally:
            workbook.close()
        if self._history:
            self._history.save_items(self._history_items())
            if self.dashboard_path:
                write_dashboard(self._history.path, self.dashboard_path)
        return self.output_path

    @property
    def video_count(self) -> int:
        return len(self._videos)

    @property
    def comment_count(self) -> int:
        return len(self._comments)
