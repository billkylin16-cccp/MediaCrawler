# -*- coding: utf-8 -*-

import pytest
import typer

import config
from cmd_arg import parse_cmd


@pytest.mark.asyncio
async def test_douyin_opinion_cli_sets_report_config():
    original = {
        "PLATFORM": config.PLATFORM,
        "CRAWLER_TYPE": config.CRAWLER_TYPE,
        "KEYWORDS": config.KEYWORDS,
        "ENABLE_DOUYIN_OPINION_REPORT": config.ENABLE_DOUYIN_OPINION_REPORT,
        "DOUYIN_OPINION_REPORT_DATE": config.DOUYIN_OPINION_REPORT_DATE,
        "DOUYIN_OPINION_REPORT_OUTPUT": config.DOUYIN_OPINION_REPORT_OUTPUT,
        "DOUYIN_OPINION_MATCH": config.DOUYIN_OPINION_MATCH,
    }
    try:
        result = await parse_cmd([
            "--platform", "dy",
            "--type", "search",
            "--keywords", "武陟,西陶",
            "--douyin_opinion_report", "yes",
            "--opinion_date", "2026-08-24",
            "--opinion_output", "8.24抖音舆论检测.xlsx",
            "--opinion_match", "any",
        ])

        assert result.douyin_opinion_report is True
        assert result.opinion_match == "any"
        assert config.KEYWORDS == "武陟,西陶"
        assert config.DOUYIN_OPINION_REPORT_DATE == "2026-08-24"
        assert config.DOUYIN_OPINION_REPORT_OUTPUT == "8.24抖音舆论检测.xlsx"
    finally:
        for name, value in original.items():
            setattr(config, name, value)


@pytest.mark.asyncio
async def test_douyin_opinion_cli_rejects_other_platforms():
    with pytest.raises(typer.BadParameter, match="--platform dy"):
        await parse_cmd([
            "--platform", "xhs",
            "--douyin_opinion_report", "yes",
        ])
