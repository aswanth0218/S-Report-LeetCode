"""Application configuration for S-REPORT."""

import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
EXPORT_FOLDER = os.path.join(BASE_DIR, "exports")
CACHE_FOLDER = os.path.join(BASE_DIR, "cache")
LEETCODE_CACHE_FOLDER = os.path.join(CACHE_FOLDER, "leetcode")
STATIC_IMAGES_FOLDER = os.path.join(BASE_DIR, "static", "images")
LOGIN_BANNER_FILE = "nandha-banner.png"
LOGIN_BANNER_SOURCES = [
    os.path.join(STATIC_IMAGES_FOLDER, LOGIN_BANNER_FILE),
    os.path.join(
        os.path.expanduser("~"),
        ".cursor",
        "projects",
        "empty-window",
        "assets",
        "c__Users_aswan_AppData_Roaming_Cursor_User_workspaceStorage_empty-window_images_nandha-banner-a01bcde8-33ae-46a3-9d89-c0edf960ecbe.png",
    ),
    os.path.join(
        os.path.expanduser("~"),
        ".cursor",
        "projects",
        "empty-window",
        "assets",
        "c__Users_aswan_AppData_Roaming_Cursor_User_workspaceStorage_empty-window_images_WhatsApp_Image_2026-08-12_at_10.39.45_AM-06def3ca-a6db-45c6-be10-b7f7c3cce526.png",
    ),
]

ALLOWED_EXTENSIONS = {"xlsx", "xls"}

# Problem solved classification thresholds (configurable)
PROBLEM_SOLVED_CONFIG = {
    "4": {"op": ">=", "value": 4},
    "3": {"op": "==", "value": 3},
    "2": {"op": "==", "value": 2},
    "1": {"op": "==", "value": 1},
    "0": {"op": "==", "value": 0},
}

# Weekly Contest ranking buckets (boundaries: <=5000, 5001-10000, 10001-15000, >15000)
CONTEST_RANKING_BUCKETS = [
    {"label": "Below 5000", "min": None, "max": 5000},
    {"label": "5001 - 10000", "min": 5001, "max": 10000},
    {"label": "10001 - 15000", "min": 10001, "max": 15000},
    {"label": "Above 15000", "min": 15001, "max": None},
]

# Contest rating buckets (boundaries: <=1500, 1501-2000, >2000)
CONTEST_RATING_BUCKETS = [
    {"label": "Below 1500", "min": None, "max": 1500},
    {"label": "1501 - 2000", "min": 1501, "max": 2000},
    {"label": "Above 2000", "min": 2001, "max": None},
]

# Overall Contest / Profile ranking buckets
GLOBAL_RANK_GROUP_LABEL = "Global Rank"
OVERALL_CONTEST_RANKING_BUCKETS = [
    {"label": "Below 20000", "min": 1, "max": 20000},
    {"label": "20000 < 100000", "min": 20001, "max": 100000},
    {"label": "Above 100000", "min": 100001, "max": None},
]
PROFILE_RANKING_BUCKETS = OVERALL_CONTEST_RANKING_BUCKETS
GLOBAL_RANKING_FILTER_OPTIONS = [
    "Below 20000",
    "20000 < 100000",
    "100000 Above",
    "N/A / Not Available",
]

GLOBAL_RANKING_SUMMARY_BUCKETS = [
    {"label": "Below 20000", "min": 1, "max": 20000},
    {"label": "20000 < 100000", "min": 20001, "max": 100000},
    {"label": "Above 100000", "min": 100001, "max": None},
    {"label": "N/A", "min": None, "max": None},
]


def contest_ranking_col(label: str) -> str:
    """Internal dept-report column key for a contest ranking bucket."""
    return f"contest_rank::{label}"


def profile_ranking_col(label: str) -> str:
    """Internal dept-report column key for a profile ranking bucket."""
    return f"profile_rank::{label}"


def global_ranking_col(label: str) -> str:
    """Internal dept-report column key for a global ranking bucket."""
    return f"global_rank::{label}"


LEVELS = ["Guardian", "Knight", "Unrated"]

INPUT_COLUMNS = ["S.No", "Register No", "Name", "DEPT", "Leetcode Link"]

MISSING_DEPT_LABEL = "Missing Department"

# Grouped report departments:
# CSE A/B -> CSE, IT A/B -> IT, AI & DS (standalone), ECE, EEE
REPORT_DEPARTMENT_ORDER = ["CSE", "AI&DS", "IT", "ECE", "EEE"]

# Display labels for report UI (internal keys stay short for Excel/filters)
REPORT_DEPARTMENT_LABELS = {
    "CSE": "CSE",
    "AI&DS": "AI & DS",
    "IT": "IT",
    "ECE": "ECE",
    "EEE": "EEE",
}

# Include blank-dept students in Total row (default: False per spec)
INCLUDE_MISSING_DEPT_IN_TOTAL = False

# LeetCode fetch performance settings
FETCH_MAX_WORKERS = 15          # Parallel API requests for fast fetching
FETCH_REQUEST_TIMEOUT = 12      # Seconds per API call
FETCH_RETRY_COUNT = 2           # Retries on rate-limit / timeout
USE_LEETCODE_CACHE = True       # Reuse cached profile data on re-upload
LEETCODE_CACHE_TTL_HOURS = 24   # Cache expiry (0 = never expire)
PROFILE_CACHE_VERSION = 6       # Bump to invalidate old incomplete caches

# Recent Uploads Tracking
RECENT_UPLOADS_FILE = os.path.join(CACHE_FOLDER, "recent_uploads.json")
MAX_RECENT_UPLOADS = 3

# Login credentials (session-based auth, no database)
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "NCT1234")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "NCT@TP02")
