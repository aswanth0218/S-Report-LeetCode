"""Prepare student dataframes with consistent columns for filtering and reporting."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from config import MISSING_DEPT_LABEL
from services.department_service import get_report_department


def _is_blank_value(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return (
        not text
        or text.upper() in ("NAN", "NONE", "NULL", "NA", "-", "<NA>", "MISSING DEPARTMENT", "MISSING DEPT")
        or text == MISSING_DEPT_LABEL
    )


def _normalize_department_cell(value: Any) -> str:
    if _is_blank_value(value):
        return MISSING_DEPT_LABEL
    text = str(value).strip()
    if text.upper() in (MISSING_DEPT_LABEL.upper(), "MISSING DEPARTMENT", "MISSING DEPT"):
        return MISSING_DEPT_LABEL
    return text.upper()


def _resolve_department_series(df: pd.DataFrame) -> pd.Series:
    """Prefer Department; fall back to DEPT when Department is blank."""
    if "Department" in df.columns:
        resolved = df["Department"].apply(_normalize_department_cell)
    else:
        resolved = pd.Series(MISSING_DEPT_LABEL, index=df.index, dtype=object)

    if "DEPT" in df.columns:
        fallback = df["DEPT"].apply(_normalize_department_cell)
        blank = resolved == MISSING_DEPT_LABEL
        resolved = resolved.where(~blank, fallback)

    return resolved


def parse_contest_history(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _clean_register_no(val: Any) -> Any:
    if val is None:
        return val
    try:
        if pd.isna(val):
            return val
    except (TypeError, ValueError):
        pass
    if isinstance(val, float) and val.is_integer():
        return int(val)
    text = str(val).strip()
    try:
        f = float(text)
        if f.is_integer():
            return int(f)
    except (TypeError, ValueError):
        pass
    return text


def ensure_student_columns(students_df: pd.DataFrame) -> pd.DataFrame:
    """Ensure Report Department and common LeetCode fields exist."""
    df = students_df.copy()

    if "S.No" in df.columns:
        df["S.No"] = pd.to_numeric(df["S.No"], errors="coerce").astype("Int64")

    if "Register No" in df.columns:
        df["Register No"] = df["Register No"].apply(_clean_register_no)

    if "Department" not in df.columns and "DEPT" in df.columns:
        df["Department"] = df["DEPT"].apply(_normalize_department_cell)

    df["Department"] = _resolve_department_series(df)
    df["Report Department"] = df["Department"].apply(get_report_department)

    if "lifetime_total_solved" not in df.columns and "total_solved" in df.columns:
        df["lifetime_total_solved"] = df["total_solved"]

    numeric_defaults = {
        "total_solved": 0,
        "solved_easy": 0,
        "solved_medium": 0,
        "solved_hard": 0,
        "contest_attended": 0,
        "contest_rating": pd.NA,
        "contest_ranking": pd.NA,
        "profile_ranking": pd.NA,
    }
    text_defaults = {
        "level": "Unrated",
        "latest_badge": "-",
        "badge_details": "-",
        "fetch_status": "",
        "LeetCode Username": "",
    }

    for col, default in numeric_defaults.items():
        if col not in df.columns:
            df[col] = default
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col, default in text_defaults.items():
        if col not in df.columns:
            df[col] = default

    return df
