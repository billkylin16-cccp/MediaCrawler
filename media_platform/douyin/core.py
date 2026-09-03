# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/douyin/core.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import asyncio
import importlib
import json
import random
from asyncio import Task
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

import config
from base.base_crawler import AbstractCrawler
from store.douyin_opinion_report import DouyinOpinionReport
from tools import utils
from tools.cdp_browser import CDPBrowserManager
from tools.douyin_image_ocr import DouyinImageOcr
from tools.douyin_media import extract_note_image_list, extract_video_download_url
from tools.runtime_paths import browser_data_dir
from tools.douyin_watchlist import (
    choose_exact_user,
    iter_user_candidates,
    public_account_label,
)
from var import crawler_type_var, source_keyword_var

from .client import DouYinClient
from .exception import DataFetchError
from .field import PublishTimeType, SearchChannelType
from .help import parse_video_info_from_url, parse_creator_info_from_url
from .login import DouYinLogin


class DouYinCrawler(AbstractCrawler):
    context_page: Page
    dy_client: DouYinClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self) -> None:
        self.index_url = "https://www.douyin.com"
        self.cookie_urls = [
            "https://douyin.com",
            self.index_url,
            "https://creator.douyin.com",
            "https://douhot.douyin.com",
            "https://live.douyin.com",
        ]
        self.cdp_manager = None
        self.ip_proxy_pool = None  # Proxy IP pool for automatic proxy refresh
        self.opinion_report: Optional[DouyinOpinionReport] = None
        self.image_ocr: Optional[DouyinImageOcr] = None
        self._processed_opinion_awemes: set[str] = set()
        self._watch_account_cache_path = browser_data_dir() / "douyin_watch_accounts.json"

    @staticmethod
    def _standard_store():
        """Load the full multi-backend store only outside opinion-report mode."""

        return importlib.import_module("store.douyin")

    async def start(self) -> None:
        if config.ENABLE_DOUYIN_OPINION_REPORT:
            if config.CRAWLER_TYPE != "search":
                raise ValueError("抖音舆情监测模式仅支持关键词搜索（--type search）")
            self.opinion_report = DouyinOpinionReport(
                keywords=config.KEYWORDS.split(","),
                target_date=config.DOUYIN_OPINION_REPORT_DATE,
                output_path=config.DOUYIN_OPINION_REPORT_OUTPUT or None,
                match=config.DOUYIN_OPINION_MATCH,
            )
            if config.DOUYIN_OPINION_ENABLE_OCR:
                self.image_ocr = DouyinImageOcr(
                    max_images=config.DOUYIN_OPINION_OCR_MAX_IMAGES
                )
        playwright_proxy_format, httpx_proxy_format = None, None
        if config.ENABLE_IP_PROXY:
            proxy_pool = importlib.import_module("proxy.proxy_ip_pool")
            self.ip_proxy_pool = await proxy_pool.create_ip_pool(
                config.IP_PROXY_POOL_COUNT,
                enable_validate_ip=True,
            )
            ip_proxy_info = await self.ip_proxy_pool.get_proxy()
            playwright_proxy_format, httpx_proxy_format = utils.format_proxy_info(ip_proxy_info)

        async with async_playwright() as playwright:
            # Select startup mode based on configuration
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[DouYinCrawler] 使用CDP模式启动浏览器")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    playwright_proxy_format,
                    None,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[DouYinCrawler] 使用标准模式启动浏览器")
                # Launch a browser context.
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium,
                    playwright_proxy_format,
                    user_agent=None,
                    headless=config.HEADLESS,
                )
                # stealth.min.js is a js script to prevent the website from detecting the crawler.
                await self.browser_context.add_init_script(path="libs/stealth.min.js")

            self.context_page = await self.browser_context.new_page()
            await self.context_page.goto(self.index_url)

            self.dy_client = await self.create_douyin_client(httpx_proxy_format)
            if not await self.dy_client.pong(browser_context=self.browser_context):
                login_obj = DouYinLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",  # you phone number
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await self.dy_client.update_cookies(
                    browser_context=self.browser_context,
                    urls=self.cookie_urls,
                )
            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "search":
                # Search for notes and retrieve their comment information.
                if self.opinion_report and config.DOUYIN_OPINION_SUPPLEMENTAL_VIDEOS:
                    await self.scan_supplemental_awemes()
                if self.opinion_report and config.DOUYIN_OPINION_WATCH_ACCOUNTS:
                    await self.scan_watch_accounts()
                if config.DOUYIN_OPINION_SCOPE != "watch_only":
                    await self.search()
            elif config.CRAWLER_TYPE == "detail":
                # Get the information and comments of the specified post
                await self.get_specified_awemes()
            elif config.CRAWLER_TYPE == "creator":
                # Get the information and comments of the specified creator
                await self.get_creators_and_videos()

            if self.opinion_report:
                output_path = self.opinion_report.flush()
                utils.logger.info(
                    f"[DouYinCrawler.start] Opinion report saved: {output_path} "
                    f"(videos={self.opinion_report.video_count}, comments={self.opinion_report.comment_count})"
                )

            utils.logger.info("[DouYinCrawler.start] Douyin Crawler finished ...")

    async def search(self) -> None:
        utils.logger.info("[DouYinCrawler.search] Begin search douyin keywords")
        dy_limit_count = 10  # douyin limit page fixed value
        if config.CRAWLER_MAX_NOTES_COUNT < dy_limit_count:
            config.CRAWLER_MAX_NOTES_COUNT = dy_limit_count
        start_page = config.START_PAGE  # start page number
        for keyword in config.KEYWORDS.split(","):
            source_keyword_var.set(keyword)
            utils.logger.info(f"[DouYinCrawler.search] Current keyword: {keyword}")
            aweme_list: List[str] = []
            page = 0
            dy_search_id = ""
            while (page - start_page + 1) * dy_limit_count <= config.CRAWLER_MAX_NOTES_COUNT:
                if page < start_page:
                    utils.logger.info(f"[DouYinCrawler.search] Skip {page}")
                    page += 1
                    continue
                try:
                    utils.logger.info(f"[DouYinCrawler.search] search douyin keyword: {keyword}, page: {page}")
                    posts_res = await self.dy_client.search_info_by_keyword(
                        keyword=keyword,
                        offset=page * dy_limit_count - dy_limit_count,
                        publish_time=PublishTimeType(config.PUBLISH_TIME_TYPE),
                        search_id=dy_search_id,
                    )
                    if posts_res.get("data") is None or posts_res.get("data") == []:
                        utils.logger.info(f"[DouYinCrawler.search] search douyin keyword: {keyword}, page: {page} is empty,{posts_res.get('data')}`")
                        break
                except DataFetchError:
                    utils.logger.error(f"[DouYinCrawler.search] search douyin keyword: {keyword} failed")
                    break

                page += 1
                if "data" not in posts_res:
                    utils.logger.error(f"[DouYinCrawler.search] search douyin keyword: {keyword} failed，账号也许被风控了。")
                    break
                dy_search_id = posts_res.get("extra", {}).get("logid", "")
                page_aweme_list = []
                for post_item in posts_res.get("data"):
                    if not isinstance(post_item, dict):
                        continue
                    aweme_info: Optional[Dict] = post_item.get("aweme_info")
                    if not aweme_info:
                        mix_items = (post_item.get("aweme_mix_info") or {}).get("mix_items") or []
                        aweme_info = mix_items[0] if mix_items else None
                    if not isinstance(aweme_info, dict):
                        continue
                    aweme_id = aweme_info.get("aweme_id", "")
                    aweme_list.append(aweme_id)
                    if self.opinion_report:
                        if await self.process_opinion_aweme(
                            aweme_info,
                            discovery_source="关键词搜索",
                        ):
                            page_aweme_list.append(aweme_id)
                    else:
                        page_aweme_list.append(aweme_id)
                        await self._standard_store().update_douyin_aweme(aweme_item=aweme_info)
                        await self.get_aweme_media(aweme_item=aweme_info)
                
                # Batch get note comments for the current page
                await self.batch_get_note_comments(page_aweme_list)

                # Sleep after each page navigation
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                utils.logger.info(f"[DouYinCrawler.search] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after page {page-1}")
            utils.logger.info(f"[DouYinCrawler.search] keyword:{keyword}, aweme_list:{aweme_list}")

    def _load_watch_account_cache(self) -> Dict[str, Dict[str, str]]:
        if not self._watch_account_cache_path.exists():
            return {}
        try:
            data = json.loads(self._watch_account_cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            utils.logger.warning(
                f"[DouYinCrawler] Unable to read watch-account cache: {exc}"
            )
            return {}
        return data if isinstance(data, dict) else {}

    def _save_watch_account_cache(self, cache: Dict[str, Dict[str, str]]) -> None:
        try:
            self._watch_account_cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self._watch_account_cache_path.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(self._watch_account_cache_path)
        except OSError as exc:
            utils.logger.warning(
                f"[DouYinCrawler] Unable to save watch-account cache: {exc}"
            )

    async def resolve_watch_account(
        self,
        requested_account: str,
        cache: Dict[str, Dict[str, str]],
    ) -> Optional[Tuple[str, str]]:
        cached = cache.get(requested_account) or {}
        cached_sec_uid = str(cached.get("sec_uid") or "").strip()
        if cached_sec_uid:
            return cached_sec_uid, str(cached.get("label") or requested_account)

        if requested_account.startswith("MS4wLjAB") or "douyin.com/user/" in requested_account:
            try:
                parsed = parse_creator_info_from_url(requested_account)
                return parsed.sec_user_id, requested_account
            except ValueError:
                pass

        search_response = await self.dy_client.search_info_by_keyword(
            keyword=requested_account,
            offset=0,
            search_channel=SearchChannelType.USER,
        )
        candidate = choose_exact_user(search_response, requested_account)
        responses = [search_response]
        if candidate is None:
            general_response = await self.dy_client.search_info_by_keyword(
                keyword=requested_account,
                offset=0,
            )
            responses.append(general_response)
            candidate = choose_exact_user(general_response, requested_account)
        if candidate is None:
            public_candidates: list[str] = []
            seen_labels: set[str] = set()
            for response in responses:
                for user in iter_user_candidates(response):
                    summary = "/".join(
                        str(user.get(field) or "").strip()
                        for field in ("unique_id", "short_id", "nickname")
                    ).strip("/")
                    if summary and summary not in seen_labels:
                        seen_labels.add(summary)
                        public_candidates.append(summary)
                    if len(public_candidates) >= 5:
                        break
                if len(public_candidates) >= 5:
                    break
            utils.logger.warning(
                f"[DouYinCrawler] No exact user-search match for watch account: "
                f"{requested_account}; candidates={public_candidates}"
            )
            return None

        sec_uid = str(candidate.get("sec_uid") or candidate.get("sec_user_id") or "").strip()
        if not sec_uid:
            return None
        label = public_account_label(candidate, requested_account)
        cache[requested_account] = {"sec_uid": sec_uid, "label": label}
        self._save_watch_account_cache(cache)
        return sec_uid, label

    async def process_opinion_aweme(
        self,
        aweme_info: Dict,
        watch_account: str = "",
        discovery_source: str = "",
    ) -> bool:
        if not self.opinion_report:
            return False
        aweme_id = str(aweme_info.get("aweme_id") or "").strip()
        if not aweme_id or aweme_id in self._processed_opinion_awemes:
            return False
        self._processed_opinion_awemes.add(aweme_id)
        if not self.opinion_report.is_target_date(aweme_info):
            return False

        detail = aweme_info
        image_urls = extract_note_image_list(detail)
        is_image_post = bool(image_urls) or str(detail.get("aweme_type")) == "68"
        if self.image_ocr and is_image_post and not image_urls:
            fetched_detail = await self.fetch_opinion_aweme_detail(aweme_id)
            if fetched_detail:
                detail = fetched_detail
                image_urls = extract_note_image_list(detail)

        ocr_pages = []
        if self.image_ocr and image_urls:
            try:
                ocr_pages = await self.image_ocr.recognize_urls(
                    image_urls,
                    self.dy_client.get_aweme_media,
                )
                utils.logger.info(
                    f"[DouYinCrawler] OCR completed for {aweme_id}: "
                    f"images={len(image_urls)}, text_pages={len(ocr_pages)}"
                )
            except Exception as exc:
                utils.logger.warning(
                    f"[DouYinCrawler] OCR failed for {aweme_id}; continuing with description: {exc}"
                )
        elif (
            self.image_ocr
            and discovery_source in {"指定链接补漏", "重点账号复查"}
        ):
            video_url = extract_video_download_url(detail)
            if video_url:
                try:
                    async def fetch_video_media(url: str) -> Optional[bytes]:
                        return await self.fetch_opinion_video_media(url, aweme_id)

                    ocr_pages = await self.image_ocr.recognize_video_url(
                        video_url,
                        fetch_video_media,
                        max_frames=config.DOUYIN_OPINION_VIDEO_OCR_MAX_FRAMES,
                    )
                    utils.logger.info(
                        f"[DouYinCrawler] Video-frame OCR completed for {aweme_id}: "
                        f"text_frames={len(ocr_pages)}"
                    )
                except Exception as exc:
                    utils.logger.warning(
                        f"[DouYinCrawler] Video-frame OCR failed for {aweme_id}; "
                        f"continuing with description: {exc}"
                    )

        return self.opinion_report.add_video(
            detail,
            ocr_pages=ocr_pages,
            watch_account=watch_account,
            discovery_source=discovery_source,
        )

    async def fetch_opinion_aweme_detail(self, aweme_id: str) -> Dict:
        """Load details through the API, then fall back to the real browser page."""
        try:
            detail = await self.dy_client.get_video_by_id(aweme_id)
            if detail:
                return detail
        except DataFetchError as exc:
            utils.logger.warning(
                f"[DouYinCrawler] Detail API failed for {aweme_id}; "
                f"trying browser-page fallback: {exc}"
            )

        page: Optional[Page] = None
        try:
            page = await self.browser_context.new_page()
            detail_path = "/aweme/v1/web/aweme/detail/"
            async with page.expect_response(
                lambda response: (
                    detail_path in response.url
                    and f"aweme_id={aweme_id}" in response.url
                ),
                timeout=45_000,
            ) as response_info:
                await page.goto(
                    f"https://www.douyin.com/video/{aweme_id}",
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
            response = await response_info.value
            payload = await response.json()
            detail = payload.get("aweme_detail") if isinstance(payload, dict) else None
            if isinstance(detail, dict) and detail:
                utils.logger.info(
                    f"[DouYinCrawler] Browser-page fallback loaded detail: {aweme_id}"
                )
                return detail
            utils.logger.warning(
                f"[DouYinCrawler] Browser-page detail is empty: {aweme_id}"
            )
        except (PlaywrightTimeoutError, ValueError, TypeError) as exc:
            utils.logger.warning(
                f"[DouYinCrawler] Browser-page fallback failed for {aweme_id}: {exc}"
            )
        except Exception as exc:
            utils.logger.error(
                f"[DouYinCrawler] Unexpected browser-page fallback error for {aweme_id}: {exc}"
            )
        finally:
            if page:
                await page.close()
        return {}

    async def fetch_opinion_video_media(
        self,
        url: str,
        aweme_id: str,
    ) -> Optional[bytes]:
        """Download video bytes, using the logged-in browser when the CDN blocks HTTPX."""
        try:
            response = await self.browser_context.request.get(
                url,
                headers={"Referer": f"https://www.douyin.com/video/{aweme_id}"},
                timeout=45_000,
            )
            if response.ok:
                content = await response.body()
                utils.logger.info(
                    f"[DouYinCrawler] Browser session downloaded video: "
                    f"{aweme_id}, bytes={len(content)}"
                )
                return content
            utils.logger.warning(
                f"[DouYinCrawler] Browser video download failed for {aweme_id}: "
                f"HTTP {response.status}"
            )
        except Exception as exc:
            utils.logger.warning(
                f"[DouYinCrawler] Browser video download failed for {aweme_id}: {exc}"
            )
        return await self.dy_client.get_aweme_media(url)

    async def fetch_watch_account_posts(
        self,
        sec_uid: str,
        max_cursor: str = "",
    ) -> Dict:
        """Load a watch account page, falling back to browser data on the first page."""
        try:
            response = await self.dy_client.get_user_aweme_posts(sec_uid, max_cursor)
            if (response or {}).get("aweme_list") or max_cursor:
                return response
        except DataFetchError as exc:
            utils.logger.warning(
                f"[DouYinCrawler] Watch-account API failed for {sec_uid}; "
                f"trying browser-page fallback: {exc}"
            )

        if max_cursor:
            return {}
        page: Optional[Page] = None
        try:
            page = await self.browser_context.new_page()
            post_path = "/aweme/v1/web/aweme/post/"
            async with page.expect_response(
                lambda candidate: (
                    post_path in candidate.url
                    and sec_uid in candidate.url
                ),
                timeout=45_000,
            ) as response_info:
                await page.goto(
                    f"https://www.douyin.com/user/{sec_uid}",
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
            payload = await (await response_info.value).json()
            if isinstance(payload, dict) and payload.get("aweme_list"):
                post_ids = [
                    str(item.get("aweme_id") or "")
                    for item in payload["aweme_list"][:10]
                    if isinstance(item, dict)
                ]
                utils.logger.info(
                    f"[DouYinCrawler] Browser-page fallback loaded watch account: "
                    f"{sec_uid}, posts={len(payload['aweme_list'])}, ids={post_ids}"
                )
                return payload
            utils.logger.warning(
                f"[DouYinCrawler] Browser watch-account page is empty: {sec_uid}"
            )
        except (PlaywrightTimeoutError, ValueError, TypeError) as exc:
            utils.logger.warning(
                f"[DouYinCrawler] Browser watch-account fallback failed for {sec_uid}: {exc}"
            )
        except Exception as exc:
            utils.logger.error(
                f"[DouYinCrawler] Unexpected watch-account fallback error for {sec_uid}: {exc}"
            )
        finally:
            if page:
                await page.close()
        return {}

    async def scan_supplemental_awemes(self) -> None:
        """Fetch user-supplied works directly when keyword search did not return them."""
        if not self.opinion_report:
            return
        entries = config.DOUYIN_OPINION_SUPPLEMENTAL_VIDEOS
        utils.logger.info(
            f"[DouYinCrawler] Begin supplemental-work scan: entries={len(entries)}"
        )
        selected_ids: list[str] = []
        seen_ids: set[str] = set()
        for entry in entries:
            try:
                video_info = parse_video_info_from_url(entry)
                if video_info.url_type == "short":
                    resolved_url = await self.dy_client.resolve_short_url(entry)
                    if not resolved_url:
                        raise ValueError("短链接解析失败")
                    video_info = parse_video_info_from_url(resolved_url)
                aweme_id = str(video_info.aweme_id or "").strip()
                if not aweme_id or aweme_id in seen_ids:
                    continue
                seen_ids.add(aweme_id)
                utils.logger.info(
                    f"[DouYinCrawler] Directly fetching supplemental work: {aweme_id}"
                )
                detail = await self.fetch_opinion_aweme_detail(aweme_id)
                if not detail:
                    utils.logger.warning(
                        f"[DouYinCrawler] Supplemental work detail is empty: {aweme_id}"
                    )
                    continue
                if await self.process_opinion_aweme(
                    detail,
                    discovery_source="指定链接补漏",
                ):
                    selected_ids.append(aweme_id)
                    utils.logger.info(
                        f"[DouYinCrawler] Supplemental work selected for report: {aweme_id}"
                    )
                else:
                    utils.logger.info(
                        f"[DouYinCrawler] Supplemental work did not match the date/keywords: {aweme_id}"
                    )
            except (DataFetchError, ValueError) as exc:
                utils.logger.warning(
                    f"[DouYinCrawler] Supplemental work failed ({entry}): {exc}"
                )
            except Exception as exc:
                utils.logger.error(
                    f"[DouYinCrawler] Unexpected supplemental-work error ({entry}): {exc}"
                )
            finally:
                await asyncio.sleep(min(float(config.CRAWLER_MAX_SLEEP_SEC), 2.0))
        await self.batch_get_note_comments(selected_ids)

    async def scan_watch_accounts(self) -> None:
        if not self.opinion_report:
            return
        cache = self._load_watch_account_cache()
        utils.logger.info(
            f"[DouYinCrawler] Begin prioritized account scan: "
            f"accounts={len(config.DOUYIN_OPINION_WATCH_ACCOUNTS)}"
        )
        for requested_account in config.DOUYIN_OPINION_WATCH_ACCOUNTS:
            try:
                resolved = await self.resolve_watch_account(requested_account, cache)
                if not resolved:
                    continue
                sec_uid, account_label = resolved
                utils.logger.info(
                    f"[DouYinCrawler] Watch account resolved: "
                    f"{requested_account} -> {account_label}"
                )

                max_cursor = ""
                scanned = 0
                stop_on_older_post = False
                while scanned < config.DOUYIN_OPINION_WATCH_MAX_POSTS:
                    response = await self.fetch_watch_account_posts(sec_uid, max_cursor)
                    aweme_list = response.get("aweme_list") or []
                    if not aweme_list:
                        break
                    selected_ids: list[str] = []
                    for aweme_info in aweme_list:
                        if scanned >= config.DOUYIN_OPINION_WATCH_MAX_POSTS:
                            break
                        scanned += 1
                        utils.logger.info(
                            f"[DouYinCrawler] Watch-account work: "
                            f"account={account_label}, aweme_id={aweme_info.get('aweme_id')}"
                        )
                        publish_date = self.opinion_report.publish_date(aweme_info)
                        if publish_date and publish_date < self.opinion_report.target_date:
                            stop_on_older_post = True
                            break
                        if publish_date != self.opinion_report.target_date:
                            continue
                        if await self.process_opinion_aweme(
                            aweme_info,
                            watch_account=account_label,
                            discovery_source="重点账号复查",
                        ):
                            selected_ids.append(str(aweme_info.get("aweme_id")))

                    await self.batch_get_note_comments(selected_ids)
                    if stop_on_older_post or not response.get("has_more"):
                        break
                    max_cursor = str(response.get("max_cursor") or "")
                    await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
            except DataFetchError as exc:
                utils.logger.error(
                    f"[DouYinCrawler] Watch-account scan failed for {requested_account}: {exc}"
                )
            except Exception as exc:
                utils.logger.error(
                    f"[DouYinCrawler] Unexpected watch-account error for {requested_account}: {exc}"
                )
            finally:
                await asyncio.sleep(min(float(config.CRAWLER_MAX_SLEEP_SEC), 2.0))

    async def get_specified_awemes(self):
        """Get the information and comments of the specified post from URLs or IDs"""
        utils.logger.info("[DouYinCrawler.get_specified_awemes] Parsing video URLs...")
        aweme_id_list = []
        for video_url in config.DY_SPECIFIED_ID_LIST:
            try:
                video_info = parse_video_info_from_url(video_url)

                # Handling short links
                if video_info.url_type == "short":
                    utils.logger.info(f"[DouYinCrawler.get_specified_awemes] Resolving short link: {video_url}")
                    resolved_url = await self.dy_client.resolve_short_url(video_url)
                    if resolved_url:
                        # Extract video ID from parsed URL
                        video_info = parse_video_info_from_url(resolved_url)
                        utils.logger.info(f"[DouYinCrawler.get_specified_awemes] Short link resolved to aweme ID: {video_info.aweme_id}")
                    else:
                        utils.logger.error(f"[DouYinCrawler.get_specified_awemes] Failed to resolve short link: {video_url}")
                        continue

                aweme_id_list.append(video_info.aweme_id)
                utils.logger.info(f"[DouYinCrawler.get_specified_awemes] Parsed aweme ID: {video_info.aweme_id} from {video_url}")
            except ValueError as e:
                utils.logger.error(f"[DouYinCrawler.get_specified_awemes] Failed to parse video URL: {e}")
                continue

        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [self.get_aweme_detail(aweme_id=aweme_id, semaphore=semaphore) for aweme_id in aweme_id_list]
        aweme_details = await asyncio.gather(*task_list)
        for aweme_detail in aweme_details:
            if aweme_detail is not None:
                await self._standard_store().update_douyin_aweme(aweme_item=aweme_detail)
                await self.get_aweme_media(aweme_item=aweme_detail)
        await self.batch_get_note_comments(aweme_id_list)

    async def get_aweme_detail(self, aweme_id: str, semaphore: asyncio.Semaphore) -> Any:
        """Get note detail"""
        async with semaphore:
            try:
                result = await self.dy_client.get_video_by_id(aweme_id)
                # Sleep after fetching aweme detail
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                utils.logger.info(f"[DouYinCrawler.get_aweme_detail] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after fetching aweme {aweme_id}")
                return result
            except DataFetchError as ex:
                utils.logger.error(f"[DouYinCrawler.get_aweme_detail] Get aweme detail error: {ex}")
                return None
            except KeyError as ex:
                utils.logger.error(f"[DouYinCrawler.get_aweme_detail] have not fund note detail aweme_id:{aweme_id}, err: {ex}")
                return None

    async def batch_get_note_comments(self, aweme_list: List[str]) -> None:
        """
        Batch get note comments
        """
        if not config.ENABLE_GET_COMMENTS:
            utils.logger.info(f"[DouYinCrawler.batch_get_note_comments] Crawling comment mode is not enabled")
            return

        task_list: List[Task] = []
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        for aweme_id in aweme_list:
            task = asyncio.create_task(self.get_comments(aweme_id, semaphore), name=aweme_id)
            task_list.append(task)
        if len(task_list) > 0:
            await asyncio.wait(task_list)

    async def get_comments(self, aweme_id: str, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            try:
                # Pass the list of keywords to the get_aweme_all_comments method
                # Use fixed crawling interval
                crawl_interval = config.CRAWLER_MAX_SLEEP_SEC
                callback = (
                    self.opinion_report.add_comments
                    if self.opinion_report
                    else self._standard_store().batch_update_dy_aweme_comments
                )
                await self.dy_client.get_aweme_all_comments(
                    aweme_id=aweme_id,
                    crawl_interval=crawl_interval,
                    is_fetch_sub_comments=config.ENABLE_GET_SUB_COMMENTS,
                    callback=callback,
                    max_count=config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES,
                )
                # Sleep after fetching comments
                await asyncio.sleep(crawl_interval)
                utils.logger.info(f"[DouYinCrawler.get_comments] Sleeping for {crawl_interval} seconds after fetching comments for aweme {aweme_id}")
                utils.logger.info(f"[DouYinCrawler.get_comments] aweme_id: {aweme_id} comments have all been obtained and filtered ...")
            except DataFetchError as e:
                utils.logger.error(f"[DouYinCrawler.get_comments] aweme_id: {aweme_id} get comments failed, error: {e}")

    async def get_creators_and_videos(self) -> None:
        """
        Get the information and videos of the specified creator from URLs or IDs
        """
        utils.logger.info("[DouYinCrawler.get_creators_and_videos] Begin get douyin creators")
        utils.logger.info("[DouYinCrawler.get_creators_and_videos] Parsing creator URLs...")

        for creator_url in config.DY_CREATOR_ID_LIST:
            try:
                creator_info_parsed = parse_creator_info_from_url(creator_url)
                user_id = creator_info_parsed.sec_user_id
                utils.logger.info(f"[DouYinCrawler.get_creators_and_videos] Parsed sec_user_id: {user_id} from {creator_url}")
            except ValueError as e:
                utils.logger.error(f"[DouYinCrawler.get_creators_and_videos] Failed to parse creator URL: {e}")
                continue

            creator_info: Dict = await self.dy_client.get_user_info(user_id)
            if creator_info:
                await self._standard_store().save_creator(user_id, creator=creator_info)

            # Get all video information of the creator
            all_video_list = await self.dy_client.get_all_user_aweme_posts(sec_user_id=user_id, callback=self.fetch_creator_video_detail)

            video_ids = [video_item.get("aweme_id") for video_item in all_video_list]
            await self.batch_get_note_comments(video_ids)

    async def fetch_creator_video_detail(self, video_list: List[Dict]):
        """
        Concurrently obtain the specified post list and save the data
        """
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [self.get_aweme_detail(post_item.get("aweme_id"), semaphore) for post_item in video_list]

        note_details = await asyncio.gather(*task_list)
        for aweme_item in note_details:
            if aweme_item is not None:
                await self._standard_store().update_douyin_aweme(aweme_item=aweme_item)
                await self.get_aweme_media(aweme_item=aweme_item)

    async def create_douyin_client(self, httpx_proxy: Optional[str]) -> DouYinClient:
        """Create douyin client"""
        cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
            self.browser_context,
            urls=self.cookie_urls,
        )  # type: ignore
        douyin_client = DouYinClient(
            proxy=httpx_proxy,
            headers={
                "User-Agent": await self.context_page.evaluate("() => navigator.userAgent"),
                "Cookie": cookie_str,
                "Host": "www.douyin.com",
                "Origin": "https://www.douyin.com/",
                "Referer": "https://www.douyin.com/",
                "Content-Type": "application/json;charset=UTF-8",
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
            proxy_ip_pool=self.ip_proxy_pool,  # Pass proxy pool for automatic refresh
        )
        return douyin_client

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """Launch browser and create browser context"""
        if config.SAVE_LOGIN_STATE:
            user_data_dir = str(browser_data_dir() / (config.USER_DATA_DIR % config.PLATFORM))  # type: ignore
            browser_context = await chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                accept_downloads=True,
                headless=headless,
                proxy=playwright_proxy,  # type: ignore
                viewport={
                    "width": 1920,
                    "height": 1080
                },
                user_agent=user_agent,
            )  # type: ignore
            return browser_context
        else:
            browser = await chromium.launch(headless=headless, proxy=playwright_proxy)  # type: ignore
            browser_context = await browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=user_agent)
            return browser_context

    async def launch_browser_with_cdp(
        self,
        playwright: Playwright,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """
        使用CDP模式启动浏览器
        """
        try:
            self.cdp_manager = CDPBrowserManager()
            browser_context = await self.cdp_manager.launch_and_connect(
                playwright=playwright,
                playwright_proxy=playwright_proxy,
                user_agent=user_agent,
                headless=headless,
            )

            # Add anti-detection script
            await self.cdp_manager.add_stealth_script()

            # Show browser information
            browser_info = await self.cdp_manager.get_browser_info()
            utils.logger.info(f"[DouYinCrawler] CDP浏览器信息: {browser_info}")

            return browser_context

        except Exception as e:
            utils.logger.error(f"[DouYinCrawler] CDP模式启动失败，回退到标准模式: {e}")
            # Fall back to standard mode
            chromium = playwright.chromium
            return await self.launch_browser(chromium, playwright_proxy, user_agent, headless)

    async def close(self) -> None:
        """Close browser context"""
        # If you use CDP mode, special processing is required
        if self.cdp_manager:
            await self.cdp_manager.cleanup()
            self.cdp_manager = None
        else:
            await self.browser_context.close()
        utils.logger.info("[DouYinCrawler.close] Browser context closed ...")

    async def get_aweme_media(self, aweme_item: Dict):
        """
        获取抖音媒体，自动判断媒体类型是短视频还是帖子图片并下载

        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            utils.logger.info(f"[DouYinCrawler.get_aweme_media] Crawling image mode is not enabled")
            return
        # List of note urls. If it is a short video type, an empty list will be returned.
        note_download_url: List[str] = extract_note_image_list(aweme_item)
        # The video URL will always exist, but when it is a short video type, the file is actually an audio file.
        video_download_url: str = extract_video_download_url(aweme_item)
        # TODO: Douyin does not adopt the audio and video separation strategy, so the audio can be separated from the original video and will not be extracted for the time being.
        if note_download_url:
            await self.get_aweme_images(aweme_item)
        else:
            await self.get_aweme_video(aweme_item)

    async def get_aweme_images(self, aweme_item: Dict):
        """
        get aweme images. please use get_aweme_media

        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            return
        aweme_id = aweme_item.get("aweme_id")
        # List of note urls. If it is a short video type, an empty list will be returned.
        note_download_url: List[str] = extract_note_image_list(aweme_item)

        if not note_download_url:
            return
        picNum = 0
        for url in note_download_url:
            if not url:
                continue
            content = await self.dy_client.get_aweme_media(url)
            await asyncio.sleep(random.random())
            if content is None:
                continue
            extension_file_name = f"{picNum:>03d}.jpeg"
            picNum += 1
            await self._standard_store().update_dy_aweme_image(aweme_id, content, extension_file_name)

    async def get_aweme_video(self, aweme_item: Dict):
        """
        get aweme videos. please use get_aweme_media

        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            return
        aweme_id = aweme_item.get("aweme_id")

        # The video URL will always exist, but when it is a short video type, the file is actually an audio file.
        video_download_url: str = extract_video_download_url(aweme_item)

        if not video_download_url:
            return
        content = await self.dy_client.get_aweme_media(video_download_url)
        await asyncio.sleep(random.random())
        if content is None:
            return
        extension_file_name = f"video.mp4"
        await self._standard_store().update_dy_aweme_video(aweme_id, content, extension_file_name)
