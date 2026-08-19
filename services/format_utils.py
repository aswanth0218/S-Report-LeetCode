"""Display formatting utilities."""

from __future__ import annotations

from typing import Any

import pandas as pd


def round_value(value: Any, decimals: int = 0) -> Any:
    """Round numeric values; return None for missing."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        num = float(value)
        if decimals == 0:
            return int(round(num))
        return round(num, decimals)
    except (TypeError, ValueError):
        return value


def format_s_no(value: Any) -> Any:
    """Format serial number as integer."""
    return round_value(value, 0)


def format_total_solved(value: Any) -> Any:
    return round_value(value, 0)


def format_contest_attended(value: Any) -> Any:
    return round_value(value, 0)


def format_contest_rating(value: Any) -> Any:
    return round_value(value, 0)


def format_contest_ranking(value: Any) -> Any:
    return round_value(value, 0)


def format_profile_ranking(value: Any) -> Any:
    return round_value(value, 0)


def format_badge_details(value: Any) -> str:
    """Format full badge list for display or export."""
    if value is None:
        return "-"
    try:
        if pd.isna(value):
            return "-"
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "-", "", "na", "<na>"):
        return "-"
    return text


def parse_badge_display_list(
    badge_details: Any = None,
    latest_badge: Any = None,
    level: Any = None,
) -> list[str]:
    """Split stored badge details into individual badge labels for UI display."""
    details = format_badge_details(badge_details)
    if details != "-":
        parts = []
        for chunk in details.replace("\n", ";").split(";"):
            label = chunk.strip()
            if label:
                parts.append(label)
        if parts:
            return parts

    latest = format_latest_badge(latest_badge, level)
    if latest != "-":
        return [latest]
    return []


def badge_chip_class(label: str) -> str:
    """Bootstrap badge class for a single badge label."""
    text = (label or "").lower()
    if "guardian" in text:
        return "bg-warning text-dark"
    if "knight" in text:
        return "bg-info text-dark"
    return "bg-light text-dark border"


def badge_sort_rank(latest_badge: Any = None, level: Any = None, badge_details: Any = None) -> int:
    """Rank badges for sorting: Guardian highest, then Knight, then other awards."""
    label = format_latest_badge(latest_badge, level).lower()
    if "guardian" in label:
        return 1000
    if "knight" in label:
        return 900

    details = format_badge_details(badge_details)
    if details != "-":
        primary = details.split(";")[0].strip().lower()
        if "guardian" in primary:
            return 1000
        if "knight" in primary:
            return 900
        return 500

    if label and label != "-":
        return 400
    return 0


def format_latest_badge(value: Any, level: Any = None) -> str:
    """Format latest LeetCode award/badge for display or export."""
    if value is not None:
        try:
            if not pd.isna(value):
                text = str(value).strip()
                if text and text.lower() not in ("nan", "none", "-", "", "na", "<na>"):
                    return text
        except (TypeError, ValueError):
            pass
    if level in ("Guardian", "Knight"):
        return str(level)
    return "-"


def format_display(value: Any, field: str) -> str:
    """Format a field for HTML display."""
    formatters = {
        "total_solved": format_total_solved,
        "solved_easy": format_total_solved,
        "solved_medium": format_total_solved,
        "solved_hard": format_total_solved,
        "contest_attended": format_contest_attended,
        "profile_ranking": format_profile_ranking,
    }
    if field in formatters:
        result = formatters[field](value)
        return "-" if result is None else str(result)
    if value is None:
        return "-"
    try:
        if pd.isna(value):
            return "-"
    except (TypeError, ValueError):
        pass
    return str(value)


def format_student_for_display(student: dict[str, Any]) -> dict[str, Any]:
    """Apply rounded display values to a student record."""
    result = dict(student)
    s_no = format_s_no(student.get("S.No"))
    result["S.No"] = s_no if s_no is not None else ""
    reg_no = format_s_no(student.get("Register No"))
    result["Register No"] = reg_no if reg_no is not None else ""
    result["badge_display"] = format_badge_details(student.get("badge_details"))
    if result["badge_display"] == "-":
        result["badge_display"] = format_latest_badge(
            student.get("latest_badge"),
            student.get("level"),
        )
    result["badge_details_display"] = result["badge_display"]
    result["badge_list"] = parse_badge_display_list(
        student.get("badge_details"),
        student.get("latest_badge"),
        student.get("level"),
    )
    result["badge_items"] = [
        {"name": name, "chip_class": badge_chip_class(name)}
        for name in result["badge_list"]
    ]
    for field in (
        "total_solved", "solved_easy", "solved_medium", "solved_hard",
        "contest_attended", "profile_ranking",
    ):
        result[f"{field}_display"] = format_display(student.get(field), field)
    return result
