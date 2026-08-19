"""LeetCode data fetching and classification service."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

from config import (
    CACHE_FOLDER,
    CONTEST_RANKING_BUCKETS,
    CONTEST_RATING_BUCKETS,
    FETCH_MAX_WORKERS,
    FETCH_REQUEST_TIMEOUT,
    FETCH_RETRY_COUNT,
    USE_LEETCODE_CACHE,
)
from services.cache_service import get_cached_profile, save_cached_profile

LEETCODE_GRAPHQL_URL = "https://leetcode.com/graphql"
LEETCODE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_thread_local = threading.local()

MARKDOWN_LINK_PATTERN = re.compile(
    r"\[?(https?://leetcode\.com/u/[^\]\s\)]+/?)\]?\(?(https?://leetcode\.com/u/[^\]\s\)]+/?)\)?",
    re.IGNORECASE,
)
PLAIN_LINK_PATTERN = re.compile(
    r"https?://leetcode\.com/u/([A-Za-z0-9_\-]+)/?",
    re.IGNORECASE,
)
BARE_DOMAIN_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?leetcode\.com/u/([A-Za-z0-9_\-]+)/?",
    re.IGNORECASE,
)
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")

INVALID_LINK_PLACEHOLDERS = frozenset({
    "long absent",
    "absent",
    "na",
    "n/a",
    "nil",
    "none",
    "-",
    "--",
    "not available",
    "no link",
    "missing",
    "no profile",
    "not provided",
    "absentee",
})

PROFILE_QUERY = """
query userPublicProfile($username: String!) {
  matchedUser(username: $username) {
    username
    profile {
      ranking
      userAvatar
      reputation
    }
    submitStatsGlobal {
      acSubmissionNum {
        difficulty
        count
      }
    }
    contestBadge {
      name
      shortName
      displayName
    }
    badges {
      id
      name
      shortName
      displayName
      creationDate
    }
  }
  userContestRanking(username: $username) {
    attendedContestsCount
    rating
    globalRanking
    totalParticipants
    topPercentage
  }
}
"""

CONTEST_HISTORY_QUERY = """
query userContestRankingHistory($username: String!) {
  userContestRankingHistory(username: $username) {
    attended
    rating
    ranking
    problemsSolved
    totalProblems
    contest {
      title
      startTime
    }
  }
}
"""

PAST_CONTESTS_QUERY = """
query pastContests($pageNo: Int!) {
  pastContests(pageNo: $pageNo) {
    title
    startTime
  }
}
"""

GLOBAL_CONTEST_LISTING_QUERY = """
query globalContestListing($pageNo: Int!) {
  globalContestListing(pageNo: $pageNo) {
    contests {
      title
      startTime
    }
  }
}
"""

SKILL_STATS_QUERY = """
query skillStats($username: String!) {
  matchedUser(username: $username) {
    tagProblemCounts {
      fundamental {
        tagName
        tagSlug
        problemsSolved
      }
      intermediate {
        tagName
        tagSlug
        problemsSolved
      }
      advanced {
        tagName
        tagSlug
        problemsSolved
      }
    }
    languageProblemCount {
      languageName
      problemsSolved
    }
  }
}
"""


def _warmup_session(session: requests.Session) -> None:
    """Load LeetCode cookies needed for some API endpoints."""
    try:
        session.get(
            "https://leetcode.com/contest/",
            headers={"User-Agent": LEETCODE_USER_AGENT},
            timeout=FETCH_REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        pass


def _graphql_contest_request(session: requests.Session, query: str, page_no: int) -> list[dict[str, Any]]:
    """Run a GraphQL contest listing query."""
    payload = {"query": query, "variables": {"pageNo": page_no}}
    headers = {
        "Content-Type": "application/json",
        "User-Agent": LEETCODE_USER_AGENT,
        "Referer": "https://leetcode.com/contest/",
        "Origin": "https://leetcode.com",
    }
    response = session.post(
        LEETCODE_GRAPHQL_URL, json=payload, headers=headers, timeout=FETCH_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        return []
    raw_data = data.get("data") or {}
    if "pastContests" in raw_data:
        raw = raw_data.get("pastContests") or []
    elif "globalContestListing" in raw_data:
        raw = (raw_data.get("globalContestListing") or {}).get("contests") or []
    else:
        raw = []
    contests = []
    for c in raw:
        start_time = c.get("startTime")
        contests.append({
            "title": c.get("title") or "Weekly Contest",
            "start_time": start_time,
            "contest_date": _start_time_to_date(start_time),
        })
    return [c for c in contests if c.get("contest_date")]


def _fetch_past_contests_rest(session: requests.Session) -> list[dict[str, Any]]:
    """Fetch contests from LeetCode REST API."""
    contests: list[dict[str, Any]] = []
    try:
        response = session.get(
            "https://leetcode.com/contest/api/list/",
            headers={"User-Agent": LEETCODE_USER_AGENT, "Referer": "https://leetcode.com/contest/"},
            timeout=FETCH_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        for key in ("past_contests", "pastContests", "contests"):
            items = data.get(key)
            if not isinstance(items, list):
                continue
            for c in items:
                start_time = c.get("start_time") or c.get("startTime")
                contests.append({
                    "title": c.get("title") or "Weekly Contest",
                    "start_time": start_time,
                    "contest_date": _start_time_to_date(start_time),
                })
    except Exception:
        pass
    return [c for c in contests if c.get("contest_date")]


def _get_thread_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        sess = requests.Session()
        adapter = HTTPAdapter(pool_connections=25, pool_maxsize=25, max_retries=1)
        sess.mount("https://", adapter)
        sess.mount("http://", adapter)
        _thread_local.session = sess
        _warmup_session(_thread_local.session)
    return _thread_local.session


def _is_invalid_link_placeholder(text: str) -> bool:
    """Treat absent/NA-style Excel values as missing links."""
    lowered = text.strip().lower()
    if not lowered:
        return True
    if lowered in INVALID_LINK_PLACEHOLDERS:
        return True
    return lowered.startswith("long absent")


def _normalize_profile_url(username: str) -> str:
    return f"https://leetcode.com/u/{username}/"


def extract_username(link_or_username: Any) -> Optional[str]:
    """Extract LeetCode username from link or plain username string (handles spaces, markdown, URLs)."""
    if link_or_username is None:
        return None
    text = str(link_or_username).strip()
    if not text or text.lower() in ("nan", "none", "null"):
        return None
    if _is_invalid_link_placeholder(text):
        return None

    # Handle markdown [link](url) or <url>
    md_match = re.search(r"\[([^\]]+)\]\(([^)]+)\)", text)
    if md_match:
        text = md_match.group(2).strip()

    # Remove non-breaking spaces and clean whitespace
    text = text.replace("\u00a0", " ").replace("\xa0", " ").strip("<>\"' ")

    # If it's a URL (contains leetcode.com)
    if "leetcode.com" in text.lower():
        u_match = re.search(r"leetcode\.com/u/([^/?#]+)", text, re.IGNORECASE)
        if u_match:
            raw_user = u_match.group(1)
            cleaned_user = raw_user.replace("%20", "").replace(" ", "").strip(".,;:)'\"`")
            if cleaned_user and USERNAME_PATTERN.match(cleaned_user):
                return cleaned_user
        legacy_match = re.search(r"leetcode\.com/([A-Za-z0-9_\-]+)/?", text, re.IGNORECASE)
        if legacy_match:
            candidate = legacy_match.group(1).strip()
            if candidate.lower() not in ("u", "contest", "problems", "problemset", "explore"):
                return candidate

    cleaned_plain = text.replace(" ", "").strip(".,;:)'\"`")
    if USERNAME_PATTERN.match(cleaned_plain):
        return cleaned_plain

    return None


def clean_leetcode_link(raw_link: Any) -> str:
    """Clean LeetCode link from various formats including markdown and spaces."""
    user = extract_username(raw_link)
    if user:
        return _normalize_profile_url(user)
    if raw_link is None:
        return ""
    text = str(raw_link).strip()
    if not text or text.lower() in ("nan", "none", "null") or _is_invalid_link_placeholder(text):
        return ""
    return text


def is_valid_leetcode_url(link: Any) -> bool:
    """Check if link is a valid LeetCode profile URL."""
    return extract_username(link) is not None


def is_fetchable_leetcode_profile(link_or_username: Any) -> bool:
    """True when a username can be resolved for API fetch (URL or plain username)."""
    return extract_username(link_or_username) is not None


def _graphql_request(username: str, session: requests.Session) -> dict[str, Any]:
    payload = {
        "query": PROFILE_QUERY,
        "variables": {"username": username},
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": LEETCODE_USER_AGENT,
        "Referer": f"https://leetcode.com/u/{username}/",
    }
    response = session.post(
        LEETCODE_GRAPHQL_URL,
        json=payload,
        headers=headers,
        timeout=FETCH_REQUEST_TIMEOUT,
    )
    if response.status_code == 429:
        raise requests.HTTPError("Rate limited", response=response)
    response.raise_for_status()
    return response.json()


def fetch_profile(username: str, session: Optional[requests.Session] = None) -> dict[str, Any]:
    """Fetch LeetCode profile data for a username with retry on rate limit."""
    sess = session or _get_thread_session()
    last_error = "Profile Fetch Failed"

    for attempt in range(FETCH_RETRY_COUNT + 1):
        try:
            data = _graphql_request(username, sess)
            if data.get("errors"):
                return {
                    "success": False,
                    "error": "Profile Not Found",
                    "username": username,
                }
            matched = data.get("data", {}).get("matchedUser")
            if not matched:
                return {
                    "success": False,
                    "error": "Profile Not Found",
                    "username": username,
                }
            return {
                "success": True,
                "username": username,
                "raw": data.get("data", {}),
            }
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                last_error = "Profile Fetch Failed"
                continue
            last_error = "Profile Fetch Failed"
            break
        except requests.Timeout:
            last_error = "Profile Fetch Failed"
            if attempt < FETCH_RETRY_COUNT:
                time.sleep(0.5)
                continue
            break
        except requests.RequestException:
            last_error = "Profile Fetch Failed"
            break
        except Exception:
            last_error = "Profile Fetch Failed"
            break

    return {"success": False, "error": last_error, "username": username}


def fetch_problem_statistics(profile_data: dict[str, Any]) -> dict[str, Any]:
    """Extract solved problems by difficulty and profile rank from profile data."""
    if not profile_data.get("success"):
        return {
            "total_solved": None,
            "solved_easy": None,
            "solved_medium": None,
            "solved_hard": None,
            "profile_ranking": None,
            "error": profile_data.get("error"),
        }

    raw = profile_data.get("raw", {})
    matched = raw.get("matchedUser") or {}
    stats = matched.get("submitStatsGlobal", {}).get("acSubmissionNum", [])
    if not stats:
        stats = matched.get("submitStats", {}).get("acSubmissionNum", [])
    profile = matched.get("profile") or {}

    easy = medium = hard = 0
    total = 0
    for item in stats:
        diff = (item.get("difficulty") or "").strip().lower()
        count = int(item.get("count") or 0)
        if diff == "all":
            total = count
        elif diff == "easy":
            easy = count
        elif diff == "medium":
            medium = count
        elif diff == "hard":
            hard = count

    if total == 0 and stats:
        total = easy + medium + hard

    ranking = profile.get("ranking")
    return {
        "total_solved": total,
        "solved_easy": easy,
        "solved_medium": medium,
        "solved_hard": hard,
        "profile_ranking": int(ranking) if ranking is not None else None,
    }


def _format_topic_breakdown(tags: Optional[list[dict[str, Any]]]) -> str:
    """Format topic list as 'Array (5), DP (3), ...'."""
    if not tags:
        return "-"
    items: list[str] = []
    for tag in sorted(tags, key=lambda t: int(t.get("problemsSolved") or 0), reverse=True):
        count = int(tag.get("problemsSolved") or 0)
        if count <= 0:
            continue
        name = (tag.get("tagName") or tag.get("tagSlug") or "Unknown").strip()
        items.append(f"{name} ({count})")
    return ", ".join(items) if items else "-"


def _format_language_breakdown(languages: Optional[list[dict[str, Any]]]) -> str:
    """Format language list as 'Python (10), C++ (5), ...'."""
    if not languages:
        return "-"
    items: list[str] = []
    for lang in sorted(languages, key=lambda t: int(t.get("problemsSolved") or 0), reverse=True):
        count = int(lang.get("problemsSolved") or 0)
        if count <= 0:
            continue
        name = (lang.get("languageName") or lang.get("langSlug") or "Unknown").strip()
        items.append(f"{name} ({count})")
    return ", ".join(items) if items else "-"


def fetch_skill_topic_stats(username: str, session: Optional[requests.Session] = None) -> dict[str, Any]:
    """
    Fetch solved problem topics and programming languages from LeetCode.
    fundamental ≈ Easy topics, intermediate ≈ Medium, advanced ≈ Hard.
    """
    sess = session or _get_thread_session()
    empty = {
        "easy_topics": [],
        "medium_topics": [],
        "hard_topics": [],
        "solved_languages": [],
    }
    try:
        payload = {"query": SKILL_STATS_QUERY, "variables": {"username": username}}
        headers = {
            "Content-Type": "application/json",
            "User-Agent": LEETCODE_USER_AGENT,
            "Referer": f"https://leetcode.com/u/{username}/",
        }
        response = sess.post(
            LEETCODE_GRAPHQL_URL, json=payload, headers=headers, timeout=FETCH_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        user = (data.get("data", {}).get("matchedUser") or {})
        counts = user.get("tagProblemCounts") or {}
        return {
            "easy_topics": counts.get("fundamental") or [],
            "medium_topics": counts.get("intermediate") or [],
            "hard_topics": counts.get("advanced") or [],
            "solved_languages": user.get("languageProblemCount") or [],
        }
    except Exception:
        return empty


def build_topic_report_fields(topic_stats: dict[str, Any]) -> dict[str, Any]:
    """Build topic and language strings for reports from raw LeetCode data."""
    easy = topic_stats.get("easy_topics") or []
    medium = topic_stats.get("medium_topics") or []
    hard = topic_stats.get("hard_topics") or []
    languages = topic_stats.get("solved_languages") or []
    return {
        "easy_topics": easy,
        "medium_topics": medium,
        "hard_topics": hard,
        "solved_languages": languages,
        "easy_topics_text": _format_topic_breakdown(easy),
        "medium_topics_text": _format_topic_breakdown(medium),
        "hard_topics_text": _format_topic_breakdown(hard),
        "solved_languages_text": _format_language_breakdown(languages),
        **build_language_solved_counts(languages),
    }


SOLVED_PROBLEMS_LANGUAGE_COLUMNS = (
    "Java",
    "Python",
    "Python3",
    "C",
    "C++",
    "MySQL",
)


def _normalize_language_name(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "")


def build_language_solved_counts(languages: Optional[list[dict[str, Any]]]) -> dict[str, int]:
    """Map LeetCode languageProblemCount rows to fixed report language columns."""
    counts = {label: 0 for label in SOLVED_PROBLEMS_LANGUAGE_COLUMNS}
    for lang in languages or []:
        solved = int(lang.get("problemsSolved") or 0)
        if solved <= 0:
            continue
        name = _normalize_language_name(lang.get("languageName") or lang.get("langSlug"))
        if name == "python3":
            counts["Python3"] += solved
        elif name == "python":
            counts["Python"] += solved
        elif name in ("c++", "cpp"):
            counts["C++"] += solved
        elif name == "c":
            counts["C"] += solved
        elif name == "java":
            counts["Java"] += solved
        elif name == "mysql":
            counts["MySQL"] += solved
    return counts


def fetch_contest_statistics(profile_data: dict[str, Any]) -> dict[str, Any]:
    """Extract contest statistics from profile data."""
    if not profile_data.get("success"):
        return {
            "contest_attended": 0,
            "contest_rating": None,
            "contest_ranking": None,
            "error": profile_data.get("error"),
        }

    raw = profile_data.get("raw", {})
    contest = raw.get("userContestRanking")

    if not contest:
        return {
            "contest_attended": 0,
            "contest_rating": None,
            "contest_ranking": None,
            "error": "Contest Data Missing",
        }

    attended = contest.get("attendedContestsCount") or 0
    rating = contest.get("rating")
    ranking = contest.get("globalRanking")

    return {
        "contest_attended": int(attended),
        "contest_rating": float(rating) if rating is not None else None,
        "contest_ranking": int(ranking) if ranking is not None else None,
        "error": None if attended > 0 else "Contest Data Missing",
    }


def classify_contest_ranking(ranking: Optional[int]) -> Optional[str]:
    """Classify weekly contest ranking into bucket (boundary values included)."""
    if ranking is None:
        return None
    r = int(round(float(ranking)))
    if r <= 5000:
        return "Below 5000"
    if r <= 10000:
        return "5001 - 10000"
    if r <= 15000:
        return "10001 - 15000"
    return "Above 15000"


def classify_contest_rating(rating: Optional[float]) -> Optional[str]:
    """Classify contest rating into bucket (1500 and 5000 boundaries included)."""
    if rating is None:
        return None
    r = float(rating)
    if r <= 1500:
        return "Below 1500"
    if r <= 2000:
        return "1501 - 2000"
    return "Above 2000"


def classify_profile_ranking(ranking: Optional[int]) -> str:
    """Classify profile overall ranking (global rank) into bucket."""
    from config import PROFILE_RANKING_BUCKETS

    default_bucket = PROFILE_RANKING_BUCKETS[-1]["label"]

    if ranking is None:
        return default_bucket
    if isinstance(ranking, float) and pd.isna(ranking):
        return default_bucket
    try:
        r = int(round(float(ranking)))
    except (TypeError, ValueError):
        return default_bucket
    if r <= 0 or r >= 5_000_000:
        return default_bucket

    for bucket in PROFILE_RANKING_BUCKETS:
        min_v = bucket.get("min")
        max_v = bucket.get("max")
        if min_v is not None and r < min_v:
            continue
        if max_v is not None and r > max_v:
            continue
        return bucket["label"]

    return default_bucket


def _badge_label(badge: Optional[dict[str, Any]]) -> Optional[str]:
    if not badge:
        return None
    for key in ("displayName", "name", "shortName"):
        value = badge.get(key)
        if value and str(value).strip():
            return str(value).strip()
    return None


def _format_badge_date(creation: Any) -> Optional[str]:
    """Format LeetCode badge creationDate as YYYY-MM-DD."""
    if creation is None:
        return None
    try:
        ts = int(creation)
        if ts > 1_000_000_000_000:
            ts //= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None


def extract_badge_details(profile_data: dict[str, Any]) -> str:
    """Return all LeetCode badges with earned dates, newest first."""
    if not profile_data.get("success"):
        return "-"

    raw = profile_data.get("raw", {})
    matched = raw.get("matchedUser") or {}
    badges = matched.get("badges") or []

    entries: list[tuple[int, str]] = []
    seen_names: set[str] = set()

    for badge in badges:
        name = _badge_label(badge)
        if not name:
            continue
        creation = badge.get("creationDate")
        try:
            ts = int(creation) if creation is not None else 0
        except (TypeError, ValueError):
            ts = 0
        date_str = _format_badge_date(creation)
        label = f"{name} ({date_str})" if date_str else name
        entries.append((ts, label))
        seen_names.add(name.lower())

    contest_name = _badge_label(matched.get("contestBadge"))
    if contest_name and contest_name.lower() not in seen_names:
        entries.append((0, f"{contest_name} (Contest)"))

    if not entries:
        return "-"

    entries.sort(key=lambda item: item[0], reverse=True)
    return "; ".join(label for _, label in entries)


def extract_latest_leetcode_badge(profile_data: dict[str, Any]) -> str:
    """Return the most recently earned LeetCode badge/award name."""
    if not profile_data.get("success"):
        return "-"

    raw = profile_data.get("raw", {})
    matched = raw.get("matchedUser") or {}
    badges = matched.get("badges") or []

    latest_name: Optional[str] = None
    latest_ts = -1
    for badge in badges:
        name = _badge_label(badge)
        if not name:
            continue
        creation = badge.get("creationDate")
        try:
            ts = int(creation) if creation is not None else 0
        except (TypeError, ValueError):
            ts = 0
        if ts >= latest_ts:
            latest_ts = ts
            latest_name = name

    if latest_name:
        return latest_name

    contest_name = _badge_label(matched.get("contestBadge"))
    if contest_name:
        return contest_name

    return "-"


def classify_level(profile_data: dict[str, Any], contest_stats: dict[str, Any]) -> str:
    """Classify LeetCode level as Guardian, Knight, or Unrated."""
    if not profile_data.get("success"):
        return "Unrated"

    raw = profile_data.get("raw", {})
    matched = raw.get("matchedUser") or {}
    badge = matched.get("contestBadge")

    if badge:
        display = (badge.get("displayName") or badge.get("name") or badge.get("shortName") or "").lower()
        if "guardian" in display:
            return "Guardian"
        if "knight" in display:
            return "Knight"

    rating = contest_stats.get("contest_rating")
    ranking = contest_stats.get("contest_ranking")
    attended = contest_stats.get("contest_attended", 0)

    if attended == 0 or (rating is None and ranking is None):
        return "Unrated"

    return "Unrated"


def fetch_contest_history(username: str, session: Optional[requests.Session] = None) -> list[dict[str, Any]]:
    """Fetch user's contest history from LeetCode with retry support."""
    sess = session or _get_thread_session()
    for attempt in range(FETCH_RETRY_COUNT + 1):
        try:
            payload = {
                "query": CONTEST_HISTORY_QUERY,
                "variables": {"username": username},
            }
            headers = {
                "Content-Type": "application/json",
                "User-Agent": LEETCODE_USER_AGENT,
                "Referer": f"https://leetcode.com/u/{username}/",
            }
            response = sess.post(
                LEETCODE_GRAPHQL_URL,
                json=payload,
                headers=headers,
                timeout=FETCH_REQUEST_TIMEOUT,
            )
            if response.status_code == 429:
                time.sleep(1.0 * (attempt + 1))
                continue
            response.raise_for_status()
            data = response.json()
            history = data.get("data", {}).get("userContestRankingHistory") or []
            result = []
            for entry in history:
                contest = entry.get("contest") or {}
                start_time = contest.get("startTime")
                contest_date = _start_time_to_date(start_time) if start_time else None
                result.append({
                    "title": contest.get("title"),
                    "start_time": start_time,
                    "contest_date": contest_date,
                    "attended": bool(entry.get("attended")),
                    "rating": float(entry["rating"]) if entry.get("rating") is not None else None,
                    "ranking": int(entry["ranking"]) if entry.get("ranking") is not None else None,
                    "problems_solved": int(entry.get("problemsSolved") or 0),
                    "total_problems": int(entry.get("totalProblems") or 0),
                })
            return result
        except Exception:
            if attempt < FETCH_RETRY_COUNT:
                time.sleep(0.5)
                continue
            return []
    return []


def _start_time_to_date(start_time: Any) -> Optional[str]:
    """Convert LeetCode startTime (unix) to YYYY-MM-DD."""
    try:
        ts = int(start_time)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None


def clear_past_contests_cache() -> None:
    """Remove cached past contest list so API is queried again."""
    cache_path = os.path.join(CACHE_FOLDER, "past_contests.json")
    try:
        if os.path.exists(cache_path):
            os.remove(cache_path)
    except OSError:
        pass


def generate_recent_weekly_contest_dates(weeks: int = 52) -> list[dict[str, Any]]:
    """Generate recent Sunday dates as fallback when LeetCode contest API is unavailable."""
    today = datetime.now(timezone.utc).date()
    days_since_sunday = (today.weekday() + 1) % 7
    most_recent_sunday = today - timedelta(days=days_since_sunday)
    contests: list[dict[str, Any]] = []
    for week in range(weeks):
        contest_day = most_recent_sunday - timedelta(weeks=week)
        date_str = contest_day.strftime("%Y-%m-%d")
        contests.append({
            "title": f"Weekly Contest ({date_str})",
            "start_time": None,
            "contest_date": date_str,
            "source": "generated",
        })
    return contests


_MEM_PAST_CONTESTS_CACHE: dict[int, list[dict[str, Any]]] = {}


def fetch_past_contests(page_no: int = 1, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Fetch list of past weekly contests from LeetCode (multiple API fallbacks with in-memory + disk cache)."""
    if not force_refresh and page_no in _MEM_PAST_CONTESTS_CACHE:
        return _MEM_PAST_CONTESTS_CACHE[page_no]

    cache_path = os.path.join(CACHE_FOLDER, "past_contests.json")

    if not force_refresh and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            page_cache = cached.get("pages", {}).get(str(page_no))
            if page_cache:
                _MEM_PAST_CONTESTS_CACHE[page_no] = page_cache
                return page_cache
        except (json.JSONDecodeError, OSError):
            pass

    session = _get_thread_session()
    contests: list[dict[str, Any]] = []

    for query in (PAST_CONTESTS_QUERY, GLOBAL_CONTEST_LISTING_QUERY):
        if contests:
            break
        try:
            contests = _graphql_contest_request(session, query, page_no)
        except Exception:
            contests = []

    if not contests and page_no == 1:
        contests = _fetch_past_contests_rest(session)

    if not contests:
        # Generate recent weekly fallback so we don't repeat failed network requests
        contests = generate_recent_weekly_contest_dates(weeks=15)

    _MEM_PAST_CONTESTS_CACHE[page_no] = contests

    try:
        cached_pages: dict = {}
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                cached_pages = json.load(f).get("pages", {})
        cached_pages[str(page_no)] = contests
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"pages": cached_pages, "fetched_at": datetime.now().isoformat()}, f)
    except OSError:
        pass

    return contests


def collect_contest_dates_from_students(students_df: Optional[pd.DataFrame]) -> list[dict[str, str]]:
    """Build contest date list from uploaded students' contest history."""
    if students_df is None or students_df.empty:
        return []

    from services.student_data_service import parse_contest_history

    seen: dict[str, str] = {}
    for _, row in students_df.iterrows():
        for entry in parse_contest_history(row.get("contest_history")):
            date = entry.get("contest_date")
            title = entry.get("title") or "Weekly Contest"
            if date and date not in seen:
                seen[date] = title

    return [
        {"date": d, "title": seen[d], "label": f"{d} — {seen[d]}"}
        for d in sorted(seen.keys(), reverse=True)
    ]


def get_available_contest_dates(
    students_df: Optional[pd.DataFrame] = None,
    force_refresh: bool = False,
    skip_api: bool = False,
) -> list[dict[str, str]]:
    """Return weekly contest dates from LeetCode API, student history, or generated fallback."""
    merged: dict[str, str] = {}
    from_api = False

    if force_refresh:
        clear_past_contests_cache()

    if not skip_api:
        for page_no in range(1, 4):
            for c in fetch_past_contests(page_no, force_refresh=force_refresh and page_no == 1):
                date = c.get("contest_date")
                title = c.get("title") or "Weekly Contest"
                if date:
                    merged.setdefault(date, title)
                    if c.get("source") != "generated":
                        from_api = True
    else:
        # Load from disk cache without network requests
        cache_path = os.path.join(CACHE_FOLDER, "past_contests.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                for page_items in cached.get("pages", {}).values():
                    for c in page_items:
                        date = c.get("contest_date")
                        title = c.get("title") or "Weekly Contest"
                        if date:
                            merged.setdefault(date, title)
                            if c.get("source") != "generated":
                                from_api = True
            except (json.JSONDecodeError, OSError):
                pass

    for item in collect_contest_dates_from_students(students_df):
        merged.setdefault(item["date"], item["title"])
        from_api = True

    if not merged:
        for c in generate_recent_weekly_contest_dates():
            date = c.get("contest_date")
            title = c.get("title") or "Weekly Contest"
            if date:
                merged.setdefault(date, title)

    return [
        {
            "date": d,
            "title": merged[d],
            "label": f"{d} — {merged[d]}",
            "from_api": from_api,
        }
        for d in sorted(merged.keys(), reverse=True)
    ]


def find_contest_for_date(contest_date: str) -> Optional[dict[str, Any]]:
    """Find contest matching a selected date."""
    if not contest_date:
        return None
    for page_no in range(1, 4):
        for c in fetch_past_contests(page_no):
            if c.get("contest_date") == contest_date:
                return c
    for c in generate_recent_weekly_contest_dates():
        if c.get("contest_date") == contest_date:
            return c
    return {"contest_date": contest_date, "title": f"Weekly Contest ({contest_date})"}


CONTEST_NUMBER_PATTERN = re.compile(r"Weekly Contest\s+(\d+)", re.IGNORECASE)


def extract_contest_number(contest_title: Optional[str]) -> Optional[str]:
    """Extract weekly contest number from titles like 'Weekly Contest 511'."""
    if not contest_title:
        return None
    match = CONTEST_NUMBER_PATTERN.search(str(contest_title))
    return match.group(1) if match else None


def format_s_report_title(contest_date: Optional[str], contest_title: Optional[str] = None) -> str:
    """Build S-Report Excel heading: Weekly Contest {number} Report — YYYY-MM-DD."""
    if not contest_date:
        return "Weekly Contest Report"
    number = extract_contest_number(contest_title)
    if number:
        return f"Weekly Contest {number} Report — {contest_date}"
    return f"Weekly Contest Report — {contest_date}"


def _contest_title_from_student_history(students_df: pd.DataFrame, contest_date: str) -> Optional[str]:
    """Find a LeetCode contest title with number from student contest history."""
    from services.student_data_service import parse_contest_history

    target = _normalize_contest_date(contest_date)
    if not target:
        return None
    for _, row in students_df.iterrows():
        for entry in parse_contest_history(row.get("contest_history")):
            if _normalize_contest_date(entry.get("contest_date")) != target:
                continue
            title = entry.get("title")
            if title and extract_contest_number(str(title)):
                return str(title).strip()
    return None


def _normalize_contest_date(value: Any) -> Optional[str]:
    """Normalize contest dates to YYYY-MM-DD for reliable matching."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "nat", ""):
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    try:
        parsed = pd.to_datetime(text, errors="coerce")
        if parsed is not pd.NaT and not pd.isna(parsed):
            return parsed.strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        pass
    return text[:10] if len(text) >= 10 else text


def is_entry_attended(entry: Any) -> bool:
    """Determine if a contest history entry represents an attended contest."""
    if not entry or not isinstance(entry, dict):
        return False
    att = entry.get("attended")
    if att is True or str(att).strip().lower() in ("true", "1", "attended", "yes"):
        return True
    try:
        if int(entry.get("problems_solved") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    try:
        rank = entry.get("ranking")
        if rank is not None and not (isinstance(rank, float) and pd.isna(rank)):
            if int(rank) > 0:
                return True
    except (TypeError, ValueError):
        pass
    return False


def get_contest_entry_for_date(
    history: list[dict[str, Any]],
    contest_date: str,
    contest_title: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Find contest history entry matching date or contest title/number, preferring attended entries."""
    if not history or not contest_date:
        return None
    target = _normalize_contest_date(contest_date)
    if not target:
        return None

    target_number = extract_contest_number(contest_title) if contest_title else None
    target_is_biweekly = "biweekly" in str(contest_title or "").lower()

    # Pass 1: Exact date match with attendance
    for entry in history:
        entry_date = _normalize_contest_date(entry.get("contest_date"))
        if entry_date == target and is_entry_attended(entry):
            entry_title = str(entry.get("title") or "").lower()
            is_entry_biweekly = "biweekly" in entry_title
            if is_entry_biweekly == target_is_biweekly or not contest_title:
                return entry

    # Pass 2: Contest number match with attendance (handles timezone/date skew)
    if target_number:
        for entry in history:
            entry_number = extract_contest_number(entry.get("title"))
            if entry_number and entry_number == target_number and is_entry_attended(entry):
                return entry

    # Pass 3: Date within 1 day with attendance (same contest type only)
    try:
        target_dt = datetime.strptime(target, "%Y-%m-%d")
        for entry in history:
            entry_date = _normalize_contest_date(entry.get("contest_date"))
            if entry_date and is_entry_attended(entry):
                try:
                    entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")
                    if abs((entry_dt - target_dt).days) <= 1:
                        entry_title = str(entry.get("title") or "").lower()
                        is_entry_biweekly = "biweekly" in entry_title
                        if is_entry_biweekly == target_is_biweekly:
                            return entry
                except ValueError:
                    pass
    except ValueError:
        pass

    # Pass 4: Fallback to any exact date entry
    for entry in history:
        entry_date = _normalize_contest_date(entry.get("contest_date"))
        if entry_date == target:
            return entry

    return None


def _student_has_excel_contest_data(row: Any) -> bool:
    def _present(value: Any) -> bool:
        if value is None:
            return False
        try:
            if pd.isna(value):
                return False
        except (TypeError, ValueError):
            pass
        return True

    attended_raw = row.get("excel_contest_attended")
    try:
        attended = int(attended_raw or 0)
    except (TypeError, ValueError):
        attended = 0

    return (
        attended > 0
        or _present(row.get("excel_problems_solved"))
        or _present(row.get("excel_contest_rating"))
        or _present(row.get("excel_contest_ranking"))
    )


def _excel_contest_date(row: Any) -> Optional[str]:
    return _normalize_contest_date(row.get("excel_contest_date"))


def _any_undated_excel_contest_data(students_df: pd.DataFrame) -> bool:
    if students_df is None or students_df.empty:
        return False
    for _, row in students_df.iterrows():
        if _student_has_excel_contest_data(row) and not _excel_contest_date(row):
            return True
    return False


def _excel_contest_applies_to_date(row: Any, contest_date: Optional[str]) -> bool:
    """True when uploaded Excel contest stats belong to the selected contest week."""
    if not _student_has_excel_contest_data(row):
        return False
    excel_date = _excel_contest_date(row)
    target = _normalize_contest_date(contest_date)

    from services.student_data_service import parse_contest_history
    history = parse_contest_history(row.get("contest_history"))
    if history:
        # Real LeetCode API history present: do not use undated excel contest columns
        if excel_date and target:
            return excel_date == target
        return False

    if excel_date and target:
        return excel_date == target
    return True


def _student_attended_contest_on_date(row: Any, contest_date: str) -> bool:
    from services.student_data_service import parse_contest_history

    history = parse_contest_history(row.get("contest_history"))
    entry = get_contest_entry_for_date(history, contest_date)
    if entry and is_entry_attended(entry):
        return True
    if not _student_has_excel_contest_data(row):
        return False
    excel_date = _excel_contest_date(row)
    target = _normalize_contest_date(contest_date)
    if excel_date and target:
        return excel_date == target
    return False


def count_contest_attendees_for_date(students_df: pd.DataFrame, contest_date: str) -> int:
    """Count students with weekly contest data for a specific date."""
    if students_df is None or students_df.empty or not contest_date:
        return 0
    return sum(
        1 for _, row in students_df.iterrows()
        if _student_attended_contest_on_date(row, contest_date)
    )


def get_latest_attended_contest_date(students_df: Optional[pd.DataFrame]) -> Optional[str]:
    """Most recent contest date with at least one attended student."""
    if students_df is None or students_df.empty:
        return None

    from services.student_data_service import parse_contest_history

    dates: set[str] = set()
    for _, row in students_df.iterrows():
        for entry in parse_contest_history(row.get("contest_history")):
            if is_entry_attended(entry):
                normalized = _normalize_contest_date(entry.get("contest_date"))
                if normalized:
                    dates.add(normalized)
        excel_date = _excel_contest_date(row)
        if excel_date and _student_has_excel_contest_data(row):
            dates.add(excel_date)

    return max(dates) if dates else None


def parse_contest_date_param(selected: str = "", custom: str = "") -> Optional[str]:
    """
    Parse contest date from UI controls.
    Empty dropdown means 'Latest Weekly Contest' (returns None for auto-resolve).
    Custom date is only used when the dropdown is left on Latest.
    """
    selected = (selected or "").strip()
    custom = (custom or "").strip()
    if selected:
        return selected
    if custom:
        return custom
    return None


def resolve_report_contest_date(
    students_df: pd.DataFrame,
    contest_date: Optional[str] = None,
) -> Optional[str]:
    """
    Choose the weekly contest date for S-Report.
    When unset, prefer the latest contest that students actually attended.
    """
    explicit = _normalize_contest_date(contest_date)
    if explicit:
        return explicit

    if "excel_contest_date" in students_df.columns:
        excel_dates = sorted({
            _excel_contest_date(row)
            for _, row in students_df.iterrows()
            if _excel_contest_date(row)
        })
        if len(excel_dates) == 1:
            return excel_dates[0]

    student_latest = get_latest_attended_contest_date(students_df)
    if student_latest:
        return student_latest

    if _any_undated_excel_contest_data(students_df):
        available = get_available_contest_dates(students_df)
        if available:
            return available[0]["date"]

    available = get_available_contest_dates(students_df)
    api_latest = available[0]["date"] if available else None

    if api_latest and count_contest_attendees_for_date(students_df, api_latest) > 0:
        return api_latest

    return api_latest


def get_default_contest_date(students_df: Optional[pd.DataFrame] = None) -> Optional[str]:
    """Return the best default weekly contest date for reports."""
    if students_df is None or students_df.empty:
        dates = get_available_contest_dates(students_df)
        return dates[0]["date"] if dates else None
    return resolve_report_contest_date(students_df, contest_date=None)


def _weekly_report_fields(
    row: Any,
    used_date: Optional[str],
    contest_title: Optional[str] = None,
) -> dict[str, Any]:
    """Build weekly contest report columns for one student row."""
    from services.student_data_service import parse_contest_history

    history = parse_contest_history(row.get("contest_history"))
    entry = get_contest_entry_for_date(history, used_date, contest_title) if used_date else None

    if entry:
        if is_entry_attended(entry):
            return {
                "report_contest_attended": 1,
                "report_problems_solved": int(entry.get("problems_solved") or 0),
                "report_contest_rating": entry.get("rating"),
                "report_contest_ranking": entry.get("ranking"),
            }
        else:
            return {
                "report_contest_attended": 0,
                "report_problems_solved": 0,
                "report_contest_rating": None,
                "report_contest_ranking": None,
            }

    if _excel_contest_applies_to_date(row, used_date):
        attended = int(row.get("excel_contest_attended") or 0) > 0 or (
            row.get("excel_problems_solved") is not None
            or row.get("excel_contest_ranking") is not None
        )
        if attended:
            return {
                "report_contest_attended": 1,
                "report_problems_solved": int(row.get("excel_problems_solved") or 0),
                "report_contest_rating": row.get("excel_contest_rating"),
                "report_contest_ranking": row.get("excel_contest_ranking"),
            }

    return {
        "report_contest_attended": 0,
        "report_problems_solved": 0,
        "report_contest_rating": None,
        "report_contest_ranking": None,
    }


def prepare_report_dataframe(
    students_df: pd.DataFrame,
    contest_date: Optional[str] = None,
) -> tuple[pd.DataFrame, Optional[str], str]:
    """
    Build report metrics using weekly contest data (problems 0-4 in that contest).
    Does NOT modify lifetime student fields.
    Returns: (report_df, contest_date_used, contest_title)
    """
    from services.student_data_service import ensure_student_columns

    result = ensure_student_columns(students_df)

    for col, default in (
        ("report_contest_attended", 0),
        ("report_problems_solved", 0),
        ("report_contest_rating", None),
        ("report_contest_ranking", None),
    ):
        if col not in result.columns:
            result[col] = default

    used_date = _normalize_contest_date(contest_date)
    if not used_date and "excel_contest_date" in result.columns:
        excel_dates = sorted({
            date
            for date in result["excel_contest_date"].map(_excel_contest_date).dropna().unique()
            if date
        })
        if len(excel_dates) == 1:
            used_date = excel_dates[0]

    used_date = used_date or resolve_report_contest_date(result, contest_date=None)
    contest_info = find_contest_for_date(used_date) if used_date else None
    contest_title = (contest_info or {}).get("title", "") if used_date else ""
    if used_date and not extract_contest_number(contest_title):
        history_title = _contest_title_from_student_history(result, used_date)
        if history_title:
            contest_title = history_title

    if used_date:
        metrics = result.apply(
            lambda row: pd.Series(_weekly_report_fields(row, used_date, contest_title)),
            axis=1,
        )
        result[
            [
                "report_contest_attended",
                "report_problems_solved",
                "report_contest_rating",
                "report_contest_ranking",
            ]
        ] = metrics

    result["report_contest_date"] = used_date or ""
    result["report_contest_title"] = contest_title
    return result, used_date, contest_title


def apply_contest_date_to_students(students_df: pd.DataFrame, contest_date: str) -> pd.DataFrame:
    """Legacy wrapper — use prepare_report_dataframe instead."""
    df, _, _ = prepare_report_dataframe(students_df, contest_date)
    return df


def _normalize_student_result(result: dict[str, Any]) -> dict[str, Any]:
    """Ensure all fields needed for reports are present with sensible defaults."""
    if result.get("fetch_status") != "Success":
        return result

    for field in ("solved_easy", "solved_medium", "solved_hard"):
        if result.get(field) is None:
            result[field] = 0
    if result.get("contest_attended") is None:
        result["contest_attended"] = 0
    if result.get("contest_history") is None:
        result["contest_history"] = []
    if result.get("level") is None:
        result["level"] = "Unrated"
    if not result.get("latest_badge"):
        result["latest_badge"] = "-"
    if not result.get("badge_details"):
        result["badge_details"] = "-"
    for field in ("easy_topics", "medium_topics", "hard_topics", "solved_languages"):
        if result.get(field) is None:
            result[field] = []
    for field in ("easy_topics_text", "medium_topics_text", "hard_topics_text", "solved_languages_text"):
        if not result.get(field):
            result[field] = "-"
    return result


def _build_student_result(username: str, profile: dict[str, Any], session: requests.Session) -> dict[str, Any]:
    """Build student result dict from profile response."""
    problems = fetch_problem_statistics(profile)
    contests = fetch_contest_statistics(profile)
    level = classify_level(profile, contests)

    total_solved = problems.get("total_solved")
    if total_solved is None and profile.get("success"):
        total_solved = 0

    result = {
        "username": username,
        "fetch_status": "Success" if profile.get("success") else profile.get("error", "Fetch Failed"),
        "total_solved": total_solved,
        "solved_easy": problems.get("solved_easy"),
        "solved_medium": problems.get("solved_medium"),
        "solved_hard": problems.get("solved_hard"),
        "profile_ranking": problems.get("profile_ranking"),
        "contest_attended": contests.get("contest_attended", 0),
        "contest_rating": contests.get("contest_rating"),
        "contest_ranking": contests.get("contest_ranking"),
        "contest_ranking_bucket": classify_contest_ranking(contests.get("contest_ranking")),
        "contest_rating_bucket": classify_contest_rating(contests.get("contest_rating")),
        "level": level,
        "latest_badge": extract_latest_leetcode_badge(profile),
        "badge_details": extract_badge_details(profile),
    }

    if not profile.get("success"):
        result["fetch_status"] = profile.get("error", "Profile Fetch Failed")
    elif contests.get("error") == "Contest Data Missing" and contests.get("contest_attended", 0) == 0:
        result["contest_data_status"] = "Contest Data Missing"

    history = fetch_contest_history(username, session)
    result["contest_history"] = history

    topic_stats = fetch_skill_topic_stats(username, session)
    result.update(build_topic_report_fields(topic_stats))

    return _normalize_student_result(result)


def fetch_student_data(username: str) -> dict[str, Any]:
    """Fetch complete LeetCode data for one student (with cache support)."""
    if USE_LEETCODE_CACHE:
        cached = get_cached_profile(username)
        if cached is not None:
            cached = dict(cached)
            cached["from_cache"] = True
            return cached

    session = _get_thread_session()
    profile = fetch_profile(username, session)
    result = _build_student_result(username, profile, session)

    if USE_LEETCODE_CACHE and result.get("fetch_status") == "Success":
        save_cached_profile(username, result)

    return result


ProgressCallback = Callable[[int, int, str, str], None]


def batch_fetch_students(
    usernames: list[str],
    progress_callback: Optional[ProgressCallback] = None,
    max_workers: int = FETCH_MAX_WORKERS,
) -> dict[str, dict[str, Any]]:
    """
    Fetch LeetCode data for multiple usernames in parallel.
    Uses JSON cache for already-fetched profiles.
    """
    results: dict[str, dict[str, Any]] = {}
    unique_usernames = list(dict.fromkeys(u for u in usernames if u))
    total = len(unique_usernames)
    done = 0

    to_fetch: list[str] = []
    for username in unique_usernames:
        if USE_LEETCODE_CACHE:
            cached = get_cached_profile(username)
            if cached is not None:
                results[username] = dict(cached, from_cache=True)
                done += 1
                if progress_callback:
                    progress_callback(done, total, username, "cached")
                continue
        to_fetch.append(username)

    if not to_fetch:
        return results

    workers = min(max_workers, len(to_fetch))

    def _fetch_one(username: str) -> tuple[str, dict[str, Any]]:
        return username, fetch_student_data(username)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch_one, u): u for u in to_fetch}
        for future in as_completed(futures):
            username, data = future.result()
            results[username] = data
            done += 1
            if progress_callback:
                source = "cache" if data.get("from_cache") else "fetched"
                progress_callback(done, total, username, source)

    return results
