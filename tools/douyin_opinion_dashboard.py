# -*- coding: utf-8 -*-
"""Generate a dependency-free local HTML dashboard from opinion history."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.douyin_opinion_history import OpinionHistory


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def write_dashboard(history_path: str | Path, output_path: str | Path) -> Path:
    history = OpinionHistory(history_path)
    payload = history.dashboard_payload()
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    daily = payload["daily"]
    max_daily = max((int(row["videos"] or 0) + int(row["comments"] or 0) for row in daily), default=1)
    bars = "".join(
        f"<div class='bar-row'><span>{_escape(row['report_date'])}</span>"
        f"<div class='bar' style='width:{max(4, int((int(row['videos'] or 0) + int(row['comments'] or 0)) / max_daily * 100))}%'></div>"
        f"<b>{int(row['videos'] or 0)}视频 / {int(row['comments'] or 0)}评论</b></div>"
        for row in daily
    ) or "<p class='muted'>暂无历史数据</p>"
    risk = payload["risk"]
    cards = "".join(
        f"<div class='card {css}'><strong>{label}风险</strong><span>{int(risk.get(label, 0))}</span></div>"
        for label, css in (("高", "high"), ("中", "medium"), ("低", "low"))
    )
    recent_rows = "".join(
        "<tr>"
        f"<td>{_escape(row['report_date'])}</td><td>{_escape(row['event_id'])}</td>"
        f"<td><span class='badge risk-{_escape(row['risk_level'])}'>{_escape(row['risk_level'])}</span></td>"
        f"<td>{_escape(row['account'])}</td><td>{_escape(row.get('location'))}</td>"
        f"<td>{_escape(row['summary'] or row['content'])}</td>"
        f"<td><a href='{_escape(row['url'])}' target='_blank'>查看</a></td></tr>"
        for row in payload["recent"]
    ) or "<tr><td colspan='7' class='muted'>暂无数据</td></tr>"
    run_rows = "".join(
        "<tr>"
        f"<td>{_escape(row['target_date'])}</td><td>{_escape(row['state'])}</td>"
        f"<td>{_escape(row['attempt'])}</td><td>{_escape(row['started_at'])}</td>"
        f"<td>{_escape(row['error'])}</td></tr>"
        for row in payload["runs"]
    ) or "<tr><td colspan='5' class='muted'>暂无任务记录</td></tr>"
    document = f"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>抖音舆情本地数据看板</title><style>
body{{margin:0;background:#f4f7fb;color:#172033;font-family:'Microsoft YaHei UI',sans-serif}}
main{{max-width:1280px;margin:auto;padding:28px}}h1{{margin:0}}.muted{{color:#718096}}
.cards{{display:flex;gap:16px;margin:22px 0}}.card{{background:white;border-radius:12px;padding:18px;flex:1;box-shadow:0 2px 10px #d9e2ef}}
.card span{{font-size:32px;display:block;margin-top:8px}}.high{{border-left:6px solid #d64545}}.medium{{border-left:6px solid #e49b22}}.low{{border-left:6px solid #2e9b63}}
section{{background:white;border-radius:12px;padding:20px;margin:18px 0;box-shadow:0 2px 10px #d9e2ef}}
.bar-row{{display:grid;grid-template-columns:110px minmax(80px,1fr) 180px;gap:12px;align-items:center;margin:10px 0}}.bar{{height:16px;background:#2d6fb7;border-radius:8px}}
table{{border-collapse:collapse;width:100%}}th,td{{padding:10px;border-bottom:1px solid #e6edf5;text-align:left;vertical-align:top}}th{{background:#eef4fa}}
.badge{{color:white;border-radius:10px;padding:2px 8px}}.risk-高{{background:#d64545}}.risk-中{{background:#e49b22}}.risk-低{{background:#2e9b63}}a{{color:#1d63a9}}
</style></head><body><main><h1>抖音舆情本地数据看板</h1><p class='muted'>更新时间：{datetime.now():%Y-%m-%d %H:%M:%S} · 数据仅保存在本机</p>
<div class='cards'>{cards}</div><section><h2>近30日监测趋势</h2>{bars}</section>
<section><h2>最近舆情</h2><table><thead><tr><th>日期</th><th>事件</th><th>风险</th><th>账号</th><th>关键定位</th><th>摘要</th><th>原文</th></tr></thead><tbody>{recent_rows}</tbody></table></section>
<section><h2>任务运行记录</h2><table><thead><tr><th>日期</th><th>状态</th><th>尝试</th><th>开始时间</th><th>错误</th></tr></thead><tbody>{run_rows}</tbody></table></section>
</main></body></html>"""
    output.write_text(document, encoding="utf-8")
    return output
