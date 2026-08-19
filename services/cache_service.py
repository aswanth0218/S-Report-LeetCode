"""JSON file cache for LeetCode profile data (not a database)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Optional

from config import LEETCODE_CACHE_FOLDER, LEETCODE_CACHE_TTL_HOURS, PROFILE_CACHE_VERSION, USE_LEETCODE_CACHE

REQUIRED_PROFILE_FIELDS = (
    "total_solved",
    "solved_easy",
    "solved_medium",
    "solved_hard",
    "profile_ranking",
    "contest_attended",
    "contest_rating",
    "contest_ranking",
    "contest_history",
    "easy_topics",
    "medium_topics",
    "hard_topics",
    "solved_languages",
    "latest_badge",
    "badge_details",
)


def _cache_path(username: str) -> str:
    safe_name = username.lower().replace("/", "_")
    return os.path.join(LEETCODE_CACHE_FOLDER, f"{safe_name}.json")


def _is_expired(fetched_at: str) -> bool:
    if LEETCODE_CACHE_TTL_HOURS <= 0:
        return False
    try:
        ts = datetime.fromisoformat(fetched_at)
        return datetime.now() - ts > timedelta(hours=LEETCODE_CACHE_TTL_HOURS)
    except (ValueError, TypeError):
        return True


def is_profile_cache_complete(profile: dict[str, Any]) -> bool:
    """Return True if cached profile has all fields needed for reports."""
    if not profile:
        return False
    if profile.get("cache_version") != PROFILE_CACHE_VERSION:
        return False
    for field in REQUIRED_PROFILE_FIELDS:
        if field not in profile:
            return False
    attended = profile.get("contest_attended") or 0
    try:
        if int(attended) > 0 and not profile.get("contest_history"):
            return False
    except (TypeError, ValueError):
        pass
    return True


def get_cached_profile(username: str) -> Optional[dict[str, Any]]:
    """Return cached LeetCode data if available, complete, and not expired."""
    if not USE_LEETCODE_CACHE or not username:
        return None
    path = _cache_path(username)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if _is_expired(data.get("cached_at", "")):
            return None
        profile = data.get("profile")
        if not is_profile_cache_complete(profile):
            return None
        return profile
    except (json.JSONDecodeError, OSError):
        return None


def save_cached_profile(username: str, profile: dict[str, Any]) -> None:
    """Save LeetCode profile data to JSON cache."""
    if not USE_LEETCODE_CACHE or not username:
        return
    os.makedirs(LEETCODE_CACHE_FOLDER, exist_ok=True)
    path = _cache_path(username)
    payload_profile = dict(profile)
    payload_profile["cache_version"] = PROFILE_CACHE_VERSION
    payload = {
        "username": username,
        "cached_at": datetime.now().isoformat(),
        "cache_version": PROFILE_CACHE_VERSION,
        "profile": payload_profile,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, default=str)


def clear_incomplete_profile_cache() -> int:
    """Remove profile cache files missing required fields or wrong version."""
    if not os.path.isdir(LEETCODE_CACHE_FOLDER):
        return 0
    removed = 0
    for filename in os.listdir(LEETCODE_CACHE_FOLDER):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(LEETCODE_CACHE_FOLDER, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            profile = data.get("profile") or {}
            if not is_profile_cache_complete(profile):
                os.remove(path)
                removed += 1
        except (json.JSONDecodeError, OSError):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed


def clear_all_profile_cache() -> int:
    """Remove all cached LeetCode profile files. Returns count removed."""
    if not os.path.isdir(LEETCODE_CACHE_FOLDER):
        return 0
    removed = 0
    for filename in os.listdir(LEETCODE_CACHE_FOLDER):
        if filename.endswith(".json"):
            try:
                os.remove(os.path.join(LEETCODE_CACHE_FOLDER, filename))
                removed += 1
            except OSError:
                pass
    return removed


def get_cache_stats(usernames: list[str]) -> dict[str, int]:
    """Return how many usernames are cached vs need fetching."""
    cached = sum(1 for u in usernames if get_cached_profile(u) is not None)
    return {"cached": cached, "to_fetch": len(usernames) - cached, "total": len(usernames)}


def clear_expired_cache() -> int:
    """Remove expired cache files. Returns count removed."""
    if not os.path.isdir(LEETCODE_CACHE_FOLDER):
        return 0
    removed = 0
    for filename in os.listdir(LEETCODE_CACHE_FOLDER):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(LEETCODE_CACHE_FOLDER, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if _is_expired(data.get("cached_at", "")):
                os.remove(path)
                removed += 1
        except (json.JSONDecodeError, OSError):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed
