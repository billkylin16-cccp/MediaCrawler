# -*- coding: utf-8 -*-
"""Resolve public Douyin account identifiers from user-search responses."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Iterator, Optional


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalized(value: Any) -> str:
    return _clean(value).lstrip("@").casefold()


def iter_user_candidates(payload: Any) -> Iterator[Dict[str, Any]]:
    """Yield de-duplicated user dictionaries found in a search payload."""
    seen_objects: set[int] = set()
    seen_sec_uids: set[str] = set()
    stack = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            object_id = id(current)
            if object_id in seen_objects:
                continue
            seen_objects.add(object_id)

            nested_user = current.get("user_info")
            if isinstance(nested_user, dict):
                stack.append(nested_user)

            sec_uid = _clean(current.get("sec_uid") or current.get("sec_user_id"))
            if sec_uid and sec_uid not in seen_sec_uids:
                seen_sec_uids.add(sec_uid)
                yield current

            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)


def choose_exact_user(payload: Any, requested_account: str) -> Optional[Dict[str, Any]]:
    """Choose only an exact public ID/nickname match to avoid monitoring a wrong user."""
    requested = _normalized(requested_account)
    if not requested:
        return None

    best: Optional[Dict[str, Any]] = None
    best_score = 0
    for candidate in iter_user_candidates(payload):
        exact_fields: Iterable[tuple[str, int]] = (
            (_normalized(candidate.get("unique_id")), 100),
            (_normalized(candidate.get("short_id")), 95),
            (_normalized(candidate.get("uid")), 90),
            (_normalized(candidate.get("nickname")), 80),
        )
        score = max((weight for value, weight in exact_fields if value == requested), default=0)
        if score > best_score:
            best = candidate
            best_score = score

    return best


def public_account_label(user: Dict[str, Any], fallback: str) -> str:
    """Return a compact public account label for logs/report metadata."""
    nickname = _clean(user.get("nickname"))
    unique_id = _clean(user.get("unique_id") or user.get("short_id"))
    if nickname and unique_id:
        return f"{nickname}（{unique_id}）"
    return nickname or unique_id or _clean(fallback)
