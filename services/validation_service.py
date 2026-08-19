"""Data validation service for S-REPORT."""

from __future__ import annotations

import os
import re
from typing import Any, Optional

import pandas as pd

from config import INPUT_COLUMNS, MISSING_DEPT_LABEL, REPORT_DEPARTMENT_ORDER
from services.department_service import get_report_department
from services.leetcode_service import (
    clean_leetcode_link,
    extract_username,
    is_fetchable_leetcode_profile,
)

# Alternate column names seen in contest-lookup and department Excel exports
COLUMN_ALIASES = {
    "Reg No": "Register No",
    "Reg.No": "Register No",
    "RegNo": "Register No",
    "Register No.": "Register No",
    "Registration No": "Register No",
    "Register Number": "Register No",
    "Student Name": "Name",
    "Name of Student": "Name",
    "LeetCode Link": "Leetcode Link",
    "Leetcode link": "Leetcode Link",
    "leetcode link": "Leetcode Link",
    "Leetcode URL": "Leetcode Link",
    "LeetCode URL": "Leetcode Link",
    "Dept": "DEPT",
    "Department Code": "DEPT",
}


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text == "" or text.lower() in ("nan", "none", "null", "na", "-", "<na>")


def normalize_input_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map alternate Excel column names to standard S-REPORT input columns."""
    result = df.copy()
    result.columns = [str(c).strip() for c in result.columns]

    for alt, standard in COLUMN_ALIASES.items():
        if alt not in result.columns:
            continue
        if standard not in result.columns:
            result[standard] = result[alt]
        else:
            blank = result[standard].apply(_is_blank)
            result.loc[blank, standard] = result.loc[blank, alt]

    # Contest lookup files use "Department" instead of "DEPT"
    if "Department" in result.columns:
        if "DEPT" not in result.columns:
            result["DEPT"] = result["Department"]
        else:
            blank_dept = result["DEPT"].apply(_is_blank)
            result.loc[blank_dept, "DEPT"] = result.loc[blank_dept, "Department"]

    return result


def get_missing_input_columns(df: pd.DataFrame) -> list[str]:
    """Return required input columns absent after alias normalization."""
    normalized = normalize_input_columns(df)
    return [col for col in INPUT_COLUMNS if col not in normalized.columns]


def assert_valid_input_columns(df: pd.DataFrame) -> None:
    """Raise a clear error when required upload columns are missing."""
    missing = get_missing_input_columns(df)
    if not missing:
        return
    normalized = normalize_input_columns(df)
    found = ", ".join(str(c) for c in normalized.columns)
    raise ValueError(
        "Missing required Excel columns: "
        + ", ".join(missing)
        + f". Columns found in file: {found}"
    )


def parse_solved_out_of_4(value: Any) -> Optional[int]:
    """Parse values like '3 / 4' or '3' into 0-4 problem count."""
    if _is_blank(value):
        return None
    text = str(value).strip()
    match = re.search(r"(\d+)\s*/\s*4", text)
    if match:
        return max(0, min(4, int(match.group(1))))
    try:
        return max(0, min(4, int(round(float(text)))))
    except (TypeError, ValueError):
        return None


def extract_contest_date_from_filename(filepath: str) -> Optional[str]:
    """Extract YYYY-MM-DD from upload filename (e.g. leetcode-contest-lookup-2026-08-02)."""
    match = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(filepath or ""))
    return match.group(1) if match else None


def _find_contest_column(columns: list[str], *needles: str) -> Optional[str]:
    for col in columns:
        lower = col.lower()
        if all(needle in lower for needle in needles):
            return col
    return None


def apply_excel_contest_data(df: pd.DataFrame, contest_date: Optional[str] = None) -> pd.DataFrame:
    """Import weekly contest stats already present in uploaded Excel."""
    result = df.copy()
    columns = list(result.columns)

    for col in ("contest_attended", "contest_rating", "contest_ranking", "level"):
        if col not in result.columns:
            result[col] = None

    solved_col = _find_contest_column(columns, "solved", "4") or _find_contest_column(columns, "solved", "out")
    rating_col = "Contest Rating" if "Contest Rating" in columns else None
    rank_col = next(
        (c for c in columns if c.lower() in ("contest rank", "contest ranking")),
        None,
    )
    level_col = "Level" if "Level" in columns else None

    for idx, row in result.iterrows():
        has_contest = False

        if solved_col and not _is_blank(row.get(solved_col)):
            problems = parse_solved_out_of_4(row.get(solved_col))
            if problems is not None:
                result.at[idx, "excel_problems_solved"] = problems
                has_contest = True

        if rating_col and not _is_blank(row.get(rating_col)):
            try:
                result.at[idx, "excel_contest_rating"] = float(row[rating_col])
                has_contest = True
            except (TypeError, ValueError):
                pass

        if rank_col and not _is_blank(row.get(rank_col)):
            try:
                result.at[idx, "excel_contest_ranking"] = int(float(row[rank_col]))
                has_contest = True
            except (TypeError, ValueError):
                pass

        if level_col and not _is_blank(row.get(level_col)):
            level_val = str(row[level_col]).strip()
            if level_val in ("Guardian", "Knight", "Unrated"):
                result.at[idx, "level"] = level_val

        if has_contest:
            result.at[idx, "excel_contest_attended"] = 1
            if contest_date:
                result.at[idx, "excel_contest_date"] = contest_date
            if _is_blank(result.at[idx, "contest_attended"]) or int(result.at[idx, "contest_attended"] or 0) == 0:
                result.at[idx, "contest_attended"] = 1
            rating = result.at[idx, "excel_contest_rating"] if "excel_contest_rating" in result.columns else None
            if rating is not None and not (isinstance(rating, float) and pd.isna(rating)):
                if _is_blank(result.at[idx, "contest_rating"]):
                    result.at[idx, "contest_rating"] = rating
            ranking = result.at[idx, "excel_contest_ranking"] if "excel_contest_ranking" in result.columns else None
            if ranking is not None and not (isinstance(ranking, float) and pd.isna(ranking)):
                if _is_blank(result.at[idx, "contest_ranking"]):
                    result.at[idx, "contest_ranking"] = ranking

    return result


def normalize_department(dept: Any) -> str:
    """Return department name or MISSING_DEPT_LABEL for blank values."""
    if _is_blank(dept):
        return MISSING_DEPT_LABEL
    return str(dept).strip().upper()


def validate_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    """Validate uploaded Excel data and return summary."""
    df = normalize_input_columns(df)
    total_records = len(df)
    issues: list[dict[str, Any]] = []

    missing_sno = 0
    missing_register = 0
    missing_name = 0
    missing_dept = 0
    missing_link = 0
    invalid_links = 0
    duplicate_register = 0
    duplicate_username = 0

    register_seen: dict[str, int] = {}
    username_seen: dict[str, int] = {}

    for idx, row in df.iterrows():
        row_num = idx + 2  # Excel row (1-based header + data)

        if _is_blank(row.get("S.No")):
            missing_sno += 1
            issues.append({"row": row_num, "type": "Missing S.No", "register_no": row.get("Register No")})

        reg_no = row.get("Register No")
        if _is_blank(reg_no):
            missing_register += 1
            issues.append({"row": row_num, "type": "Missing Register No", "name": row.get("Name")})
        else:
            reg_str = str(reg_no).strip()
            register_seen[reg_str] = register_seen.get(reg_str, 0) + 1

        if _is_blank(row.get("Name")):
            missing_name += 1
            issues.append({"row": row_num, "type": "Missing Name", "register_no": reg_no})

        if _is_blank(row.get("DEPT")):
            missing_dept += 1
            issues.append({"row": row_num, "type": "Missing Department", "register_no": reg_no, "name": row.get("Name")})

        raw_link = row.get("Leetcode Link")
        if _is_blank(raw_link):
            missing_link += 1
            issues.append({"row": row_num, "type": "Missing LeetCode Link", "register_no": reg_no, "name": row.get("Name")})
        elif not is_fetchable_leetcode_profile(raw_link):
            invalid_links += 1
            issues.append({
                "row": row_num,
                "type": "Invalid LeetCode Link",
                "register_no": reg_no,
                "name": row.get("Name"),
                "link": raw_link,
            })
        else:
            username = extract_username(raw_link)
            if username:
                username_seen[username.lower()] = username_seen.get(username.lower(), 0) + 1

    duplicate_register = sum(1 for count in register_seen.values() if count > 1)
    duplicate_username = sum(1 for count in username_seen.values() if count > 1)

    for reg, count in register_seen.items():
        if count > 1:
            issues.append({"type": "Duplicate Register No", "register_no": reg, "count": count})

    for user, count in username_seen.items():
        if count > 1:
            issues.append({"type": "Duplicate LeetCode Username", "username": user, "count": count})

    valid_records = total_records - missing_register - missing_name

    return {
        "total_records": total_records,
        "valid_records": valid_records,
        "missing_sno": missing_sno,
        "missing_register": missing_register,
        "missing_name": missing_name,
        "missing_department": missing_dept,
        "missing_leetcode_link": missing_link,
        "invalid_links": invalid_links,
        "duplicate_register_no": duplicate_register,
        "duplicate_username": duplicate_username,
        "issues": issues,
    }


def prepare_students_dataframe(
    df: pd.DataFrame,
    contest_date: Optional[str] = None,
) -> pd.DataFrame:
    """Clean and enrich student dataframe."""
    result = normalize_input_columns(df)

    for col in INPUT_COLUMNS:
        if col not in result.columns:
            result[col] = None

    if "S.No" in result.columns:
        result["S.No"] = pd.to_numeric(result["S.No"], errors="coerce").astype("Int64")

    if "Register No" in result.columns:
        result["Register No"] = pd.to_numeric(result["Register No"], errors="coerce").astype("Int64")

    def _resolve_username(raw_link: Any) -> Optional[str]:
        if _is_blank(raw_link):
            return None
        username = extract_username(raw_link)
        if username:
            return username
        cleaned = clean_leetcode_link(raw_link)
        return extract_username(cleaned)

    result["Leetcode Link Clean"] = result["Leetcode Link"].apply(clean_leetcode_link)
    result["LeetCode Username"] = result["Leetcode Link"].apply(_resolve_username)
    result["Department"] = result["DEPT"].apply(normalize_department)
    result["Report Department"] = result["Department"].apply(get_report_department)
    result["Has Valid Link"] = result["Leetcode Link"].apply(is_fetchable_leetcode_profile)
    result = apply_excel_contest_data(result, contest_date=contest_date)

    return result


def get_leetcode_profile_link_status(row: pd.Series) -> str:
    """Return display label for a student's LeetCode profile link status."""
    if _is_blank(row.get("Leetcode Link")):
        return "Missing LeetCode Link"
    if not row.get("Has Valid Link"):
        return "Invalid LeetCode Link"
    fetch_status = row.get("fetch_status")
    if fetch_status and str(fetch_status).strip():
        return str(fetch_status).strip()
    return "Not Fetched"


LEETCODE_PROFILE_LINK_STATUS_OPTIONS = [
    "Success",
    "Not Fetched",
    "Missing LeetCode Link",
    "Invalid LeetCode Link",
    "Profile Not Found",
    "Profile Fetch Failed",
    "Fetch Failed",
]


def _link_issue_for_row(row: pd.Series) -> Optional[str]:
    """Return link issue label if the student's LeetCode link is not working."""
    issue = get_leetcode_profile_link_status(row)
    if issue == "Success":
        return None
    if issue == "Invalid LeetCode Link":
        return "Invalid Link"
    if issue == "Missing LeetCode Link":
        return "Missing Link"
    return issue


def _build_link_not_working_by_dept(students_df: pd.DataFrame) -> list[dict[str, Any]]:
    """Group students with broken LeetCode links by report department."""
    buckets: dict[str, list[dict[str, Any]]] = {
        dept: [] for dept in REPORT_DEPARTMENT_ORDER
    }
    buckets[MISSING_DEPT_LABEL] = []

    for _, row in students_df.iterrows():
        issue = _link_issue_for_row(row)
        if not issue:
            continue
        report_dept = row.get("Report Department") or get_report_department(row.get("Department"))
        if report_dept not in buckets:
            buckets[report_dept] = []
        buckets[report_dept].append({
            "S.No": row.get("S.No"),
            "Register No": row.get("Register No"),
            "Name": row.get("Name"),
            "Department": row.get("Department") or row.get("DEPT"),
            "Issue": issue,
            "Leetcode Link": row.get("Leetcode Link"),
        })

    ordered: list[dict[str, Any]] = []
    for dept in REPORT_DEPARTMENT_ORDER + [MISSING_DEPT_LABEL]:
        items = buckets.get(dept) or []
        if items:
            ordered.append({
                "department": dept,
                "count": len(items),
                "students": items,
            })
    return ordered


def categorize_data_issues(students_df: pd.DataFrame) -> dict[str, Any]:
    """Categorize students with LeetCode link/data issues."""
    issues: dict[str, Any] = {
        "missing_link": [],
        "invalid_link": [],
        "profile_not_found": [],
        "profile_fetch_failed": [],
        "link_not_working_by_dept": [],
    }

    for _, row in students_df.iterrows():
        entry = {
            "S.No": row.get("S.No"),
            "Register No": row.get("Register No"),
            "Name": row.get("Name"),
            "DEPT": row.get("DEPT"),
            "Department": row.get("Department"),
            "Leetcode Link": row.get("Leetcode Link"),
        }

        if _is_blank(row.get("Leetcode Link")):
            issues["missing_link"].append(entry)
        elif not row.get("Has Valid Link"):
            issues["invalid_link"].append(entry)
        elif row.get("fetch_status") == "Profile Not Found":
            issues["profile_not_found"].append(entry)
        elif row.get("fetch_status") == "Profile Fetch Failed":
            issues["profile_fetch_failed"].append(entry)

    issues["link_not_working_by_dept"] = _build_link_not_working_by_dept(students_df)
    return issues

