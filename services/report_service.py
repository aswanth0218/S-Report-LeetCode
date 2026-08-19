"""Report calculation service for S-REPORT."""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from config import (
    CONTEST_RANKING_BUCKETS,
    CONTEST_RATING_BUCKETS,
    GLOBAL_RANKING_FILTER_OPTIONS,
    GLOBAL_RANKING_SUMMARY_BUCKETS,
    LEVELS,
    MISSING_DEPT_LABEL,
    OVERALL_CONTEST_RANKING_BUCKETS,
    PROBLEM_SOLVED_CONFIG,
    PROFILE_RANKING_BUCKETS,
    REPORT_DEPARTMENT_ORDER,
    REPORT_DEPARTMENT_LABELS,
    contest_ranking_col,
    global_ranking_col,
    profile_ranking_col,
)
from services.department_service import get_report_department, student_department_matches
from services.leetcode_service import (
    classify_contest_ranking,
    classify_contest_rating,
    classify_profile_ranking,
    prepare_report_dataframe,
)
from services.student_data_service import ensure_student_columns
from services.validation_service import (
    LEETCODE_PROFILE_LINK_STATUS_OPTIONS,
    get_leetcode_profile_link_status,
)


def classify_problem_solved(total_solved: Any, config: dict | None = None) -> Optional[str]:
    """Classify student into problem solved bucket (0-4 for weekly contest)."""
    cfg = config or PROBLEM_SOLVED_CONFIG
    if total_solved is None or (isinstance(total_solved, float) and pd.isna(total_solved)):
        return "0"

    try:
        count = int(round(float(total_solved)))
    except (TypeError, ValueError):
        return "0"

    # Weekly contest: clamp to 0-4
    count = max(0, min(4, count))

    for bucket in ["4", "3", "2", "1", "0"]:
        rule = cfg[bucket]
        op = rule["op"]
        val = rule["value"]
        if op == ">=" and count >= val:
            return bucket
        if op == "==" and count == val:
            return bucket
    return "0"


def classify_student_global_ranking(rank: Optional[int]) -> str:
    """Classify student's overall global ranking into Department Summary category."""
    if rank is None or rank <= 0:
        return "N/A"
    if rank <= 20000:
        return "Below 20000"
    elif rank <= 100000:
        return "20000 < 100000"
    else:
        return "Above 100000"


def _init_dept_row(dept: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "Dept": dept,
        "Total Strength": 0,
        "Total attended": 0,
    }
    for key in PROBLEM_SOLVED_CONFIG:
        row[key] = 0
    for bucket in CONTEST_RANKING_BUCKETS:
        row[contest_ranking_col(bucket["label"])] = 0
    for bucket in CONTEST_RATING_BUCKETS:
        row[bucket["label"]] = 0
    for level in LEVELS:
        row[level] = 0
    for bucket in GLOBAL_RANKING_SUMMARY_BUCKETS:
        row[global_ranking_col(bucket["label"])] = 0
    # Backward compatibility alias
    for bucket in PROFILE_RANKING_BUCKETS:
        row[profile_ranking_col(bucket["label"])] = 0
    return row


def _get_student_dept(student: pd.Series, grouped: bool) -> str:
    if grouped:
        dept = student.get("Report Department")
        if (
            dept is None
            or (isinstance(dept, float) and pd.isna(dept))
            or str(dept).strip().upper() in ("NAN", "NONE", "<NA>", "MISSING DEPARTMENT", "MISSING DEPT", "")
            or dept == MISSING_DEPT_LABEL
        ):
            raw_dept = student.get("Department")
            resolved = get_report_department(raw_dept)
            return resolved
        return str(dept)
    dept = student.get("Department", MISSING_DEPT_LABEL)
    if (
        dept is None
        or (isinstance(dept, float) and pd.isna(dept))
        or str(dept).strip().upper() in ("NAN", "NONE", "<NA>", "MISSING DEPARTMENT", "MISSING DEPT", "")
        or dept == MISSING_DEPT_LABEL
    ):
        return MISSING_DEPT_LABEL
    return str(dept)


def _report_metric(student: pd.Series, report_key: str, *fallback_keys: str) -> Any:
    """Weekly contest value with report, lifetime, and excel fallbacks."""
    keys = (report_key,) + fallback_keys
    for key in keys:
        val = student.get(key)
        if val is None or pd.isna(val):
            continue
        return val
    return None


def _resolve_attendee_buckets(student: pd.Series) -> dict[str, str]:
    """
    Classify one contest attendee into report buckets.
    Always returns exactly one bucket per category so totals match Total attended.
    """
    problem_bucket = classify_problem_solved(student.get("report_problems_solved")) or "0"

    rating = _report_metric(
        student,
        "report_contest_rating",
        "excel_contest_rating",
        "contest_rating",
    )
    if rating is None:
        rating = 1500.0
    rating_bucket = classify_contest_rating(float(rating)) or "Below 1500"

    ranking = _report_metric(
        student,
        "report_contest_ranking",
        "excel_contest_ranking",
        "contest_ranking",
    )
    if ranking is None:
        ranking = 999999
    ranking_bucket = classify_contest_ranking(int(round(float(ranking)))) or "Above 15000"

    level = student.get("level") or "Unrated"
    if level not in LEVELS:
        level = "Unrated"

    return {
        "problem": problem_bucket,
        "rating": rating_bucket,
        "ranking": ranking_bucket,
        "level": level,
    }


def _aggregate_student_into_row(row: dict[str, Any], student: pd.Series) -> None:
    """Aggregate using weekly contest report metrics."""
    row["Total Strength"] += 1

    # Student's overall/best global ranking classification
    raw_global_rank = get_best_global_ranking(student)
    global_cat = classify_student_global_ranking(raw_global_rank)
    row[global_ranking_col(global_cat)] += 1

    # Backward compatibility alias
    profile_bucket = classify_profile_ranking(student.get("profile_ranking"))
    if profile_ranking_col(profile_bucket) in row:
        row[profile_ranking_col(profile_bucket)] += 1

    attended = int(student.get("report_contest_attended") or 0)
    if attended <= 0:
        return

    row["Total attended"] += 1
    buckets = _resolve_attendee_buckets(student)

    row[buckets["problem"]] += 1
    row[buckets["rating"]] += 1
    row[contest_ranking_col(buckets["ranking"])] += 1
    row[buckets["level"]] += 1


def calculate_department_report(
    students_df: pd.DataFrame,
    grouped: bool = False,
    contest_date: Optional[str] = None,
    fixed_order: Optional[list[str]] = None,
    global_rank_filter: Optional[str] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], Optional[str], str]:
    """
    Calculate department-wise report using weekly contest metrics.
    Returns: (dept_report_df, missing_dept_df, quality_check, contest_date_used, contest_title)
    """
    working_df, used_date, contest_title = prepare_report_dataframe(students_df, contest_date)

    if global_rank_filter and str(global_rank_filter).strip().lower() not in ("all", ""):
        working_df = working_df[
            working_df.apply(
                lambda s: matches_global_ranking_filter(get_best_global_ranking(s), global_rank_filter),
                axis=1,
            )
        ]

    if grouped and fixed_order:
        valid_depts = list(fixed_order)
    elif grouped:
        present = sorted(
            d for d in working_df["Report Department"].dropna().unique()
            if d and d != MISSING_DEPT_LABEL
        )
        valid_depts = [d for d in REPORT_DEPARTMENT_ORDER if d in present]
        valid_depts += sorted(d for d in present if d not in REPORT_DEPARTMENT_ORDER)
    else:
        valid_depts = sorted(
            d for d in working_df["Department"].unique()
            if d and d != MISSING_DEPT_LABEL
        )

    dept_rows: dict[str, dict[str, Any]] = {d: _init_dept_row(d) for d in valid_depts}
    missing_dept_records: list[dict[str, Any]] = []

    for _, student in working_df.iterrows():
        dept = _get_student_dept(student, grouped)

        if dept == MISSING_DEPT_LABEL:
            missing_dept_records.append(_student_to_missing_record(student))
            continue

        if dept not in dept_rows:
            dept_rows[dept] = _init_dept_row(dept)

        _aggregate_student_into_row(dept_rows[dept], student)

    if grouped and fixed_order:
        dept_list = [dept_rows[d] for d in fixed_order if d in dept_rows]
        extras = sorted(
            d for d, row in dept_rows.items()
            if d not in fixed_order and int(row.get("Total Strength") or 0) > 0
        )
        dept_list.extend(dept_rows[d] for d in extras)
    else:
        dept_list = [dept_rows[d] for d in valid_depts if d in dept_rows]

    if dept_list:
        total_row = _init_dept_row("Total")
        numeric_keys = [k for k in total_row if k != "Dept"]
        for dept_row in dept_list:
            for key in numeric_keys:
                total_row[key] += dept_row[key]
        dept_list.append(total_row)

    dept_report_df = pd.DataFrame(dept_list)
    missing_dept_df = pd.DataFrame(missing_dept_records)
    quality = _quality_check(working_df, dept_report_df, missing_dept_df, grouped=grouped)

    return dept_report_df, missing_dept_df, quality, used_date, contest_title


def calculate_grouped_department_report(
    students_df: pd.DataFrame,
    contest_date: Optional[str] = None,
    global_rank_filter: Optional[str] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], Optional[str], str]:
    """Calculate report for CSE, AI&DS, IT, ECE, EEE (grouped sections)."""
    return calculate_department_report(
        students_df,
        grouped=True,
        contest_date=contest_date,
        fixed_order=REPORT_DEPARTMENT_ORDER,
        global_rank_filter=global_rank_filter,
    )


def _student_to_missing_record(student: pd.Series) -> dict[str, Any]:
    return {
        "S.No": student.get("S.No"),
        "Register No": student.get("Register No"),
        "Name": student.get("Name"),
        "DEPT": student.get("DEPT"),
        "LeetCode Link": student.get("Leetcode Link Clean") or student.get("Leetcode Link"),
        "Problem Solved": student.get("report_problems_solved", student.get("total_solved")),
        "Contest Rating": student.get("report_contest_rating", student.get("contest_rating")),
        "Contest Ranking": student.get("report_contest_ranking", student.get("contest_ranking")),
    }


def calculate_dashboard_stats(students_df: pd.DataFrame, dept_report_df: pd.DataFrame | None = None) -> dict[str, Any]:
    """Calculate dashboard summary statistics."""
    _ = dept_report_df  # kept for backward compatibility with callers
    students_df = ensure_student_columns(students_df)
    total_students = len(students_df)
    missing_dept_count = int((students_df["Report Department"] == MISSING_DEPT_LABEL).sum())

    dept_counts: dict[str, int] = {}
    dept_solved_total: dict[str, int] = {}
    dept_solved_avg: dict[str, float] = {}

    valid = students_df[students_df["Report Department"] != MISSING_DEPT_LABEL]

    for dept in REPORT_DEPARTMENT_ORDER:
        dept_counts[dept] = int((students_df["Report Department"] == dept).sum())
        dept_students = valid[valid["Report Department"] == dept]
        solved_sum = int(dept_students["total_solved"].fillna(0).sum())
        dept_solved_total[dept] = solved_sum
        dept_solved_avg[dept] = (
            round(float(dept_students["total_solved"].fillna(0).mean()), 1)
            if len(dept_students) else 0.0
        )

    # Rank departments by total problems solved (highest first)
    ranked_by_total = sorted(
        REPORT_DEPARTMENT_ORDER,
        key=lambda d: dept_solved_total.get(d, 0),
        reverse=True,
    )
    solved_total_labels = ranked_by_total
    solved_total_values = [dept_solved_total.get(d, 0) for d in ranked_by_total]

    # Rank departments by average solved per student (growth / performance)
    ranked_by_avg = sorted(
        REPORT_DEPARTMENT_ORDER,
        key=lambda d: dept_solved_avg.get(d, 0),
        reverse=True,
    )
    solved_growth_labels = ranked_by_avg
    solved_growth_values = [dept_solved_avg.get(d, 0) for d in ranked_by_avg]

    top_solved_dept = ranked_by_total[0] if ranked_by_total else ""
    top_growth_dept = ranked_by_avg[0] if ranked_by_avg else ""

    return {
        "total_students": total_students,
        "it_count": dept_counts.get("IT", 0),
        "cse_count": dept_counts.get("CSE", 0),
        "ece_count": dept_counts.get("ECE", 0),
        "eee_count": dept_counts.get("EEE", 0),
        "ai_ds_count": dept_counts.get("AI&DS", 0),
        "missing_department_count": missing_dept_count,
        "dept_counts": dept_counts,
        "dept_solved_total": dept_solved_total,
        "dept_solved_avg": dept_solved_avg,
        "solved_total_labels": solved_total_labels,
        "solved_total_values": solved_total_values,
        "solved_growth_labels": solved_growth_labels,
        "solved_growth_values": solved_growth_values,
        "top_solved_dept": top_solved_dept,
        "top_growth_dept": top_growth_dept,
    }


def _normalize_department_filter(value: str) -> str:
    """Normalize department filter from query string (handles AI&DS encoding quirks)."""
    text = (value or "").strip()
    if text in ("AI_DS", "AIDS") or text.upper() == "AIDS":
        return "AI&DS"
    return text


def normalize_department_filter(value: str) -> str:
    """Public wrapper for department filter query values."""
    return _normalize_department_filter(value)


def _department_series_matches(students_df: pd.DataFrame, report_dept: str) -> pd.Series:
    """Match students to a grouped report department using Report Department."""
    if "Report Department" in students_df.columns:
        mapped = students_df["Report Department"].astype(str).str.strip()
    elif "Department" in students_df.columns:
        mapped = students_df["Department"].apply(get_report_department).astype(str).str.strip()
    elif "DEPT" in students_df.columns:
        mapped = students_df["DEPT"].apply(get_report_department).astype(str).str.strip()
    else:
        return pd.Series(False, index=students_df.index)

    target = report_dept.strip()
    return mapped == target


def filter_students(
    students_df: pd.DataFrame,
    filters: Optional[dict[str, str]] = None,
) -> pd.DataFrame:
    """Filter students by report department or section."""
    result = ensure_student_columns(students_df)
    filters = filters or {}

    department = _normalize_department_filter(filters.get("department") or "")
    if department and department.lower() not in ("all", "all departments"):
        if department == MISSING_DEPT_LABEL:
            result = result[result["Report Department"] == MISSING_DEPT_LABEL]
        else:
            result = result[
                result.apply(
                    lambda row: (
                        student_department_matches(row.get("Department"), department)
                        or student_department_matches(row.get("Report Department"), department)
                        or student_department_matches(row.get("DEPT"), department)
                    ),
                    axis=1,
                )
            ]

    level = (filters.get("level") or "").strip()
    if level and level.lower() not in ("all", "") and "level" in result.columns:
        result = result[result["level"].astype(str).str.lower() == level.lower()]

    contest = (filters.get("contest") or "").strip().lower()
    attended_col = "contest_attended"
    if attended_col not in result.columns and "report_contest_attended" in result.columns:
        attended_col = "report_contest_attended"

    if contest in ("attended", "yes", "1") and attended_col in result.columns:
        attended = pd.to_numeric(result[attended_col], errors="coerce").fillna(0)
        result = result[attended.astype(int) > 0]
    elif contest in ("not_attended", "no", "0") and attended_col in result.columns:
        attended = pd.to_numeric(result[attended_col], errors="coerce").fillna(0)
        result = result[attended.astype(int) == 0]

    search = (filters.get("search") or filters.get("student_search") or "").strip()
    if search:
        term = search.lower()
        reg_match = (
            result["Register No"].astype(str).str.lower().str.contains(term, na=False, regex=False)
            if "Register No" in result.columns
            else pd.Series(False, index=result.index)
        )
        name_match = (
            result["Name"].astype(str).str.lower().str.contains(term, na=False, regex=False)
            if "Name" in result.columns
            else pd.Series(False, index=result.index)
        )
        result = result[reg_match | name_match]

    return result


def compute_student_filter_counts(
    students_df: pd.DataFrame,
    filters: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Counts for filter dropdown labels (excluding the filter being counted)."""
    filters = filters or {}
    base_filters = {
        "department": filters.get("department", ""),
        "contest": filters.get("contest", ""),
        "search": filters.get("search", ""),
    }

    for_dept_counts = filter_students(
        students_df,
        {**base_filters, "department": ""},
    )
    for_contest_counts = filter_students(
        students_df,
        {**base_filters, "contest": ""},
    )

    dept_counts: dict[str, int] = {"all": len(for_dept_counts)}
    for dept in REPORT_DEPARTMENT_ORDER:
        dept_counts[dept] = len(filter_students(for_dept_counts, {"department": dept}))
    dept_counts[MISSING_DEPT_LABEL] = len(
        filter_students(for_dept_counts, {"department": MISSING_DEPT_LABEL})
    )

    if "contest_attended" in for_contest_counts.columns:
        attended = pd.to_numeric(for_contest_counts["contest_attended"], errors="coerce").fillna(0)
        attended_n = int((attended.astype(int) > 0).sum())
    elif "report_contest_attended" in for_contest_counts.columns:
        attended = pd.to_numeric(for_contest_counts["report_contest_attended"], errors="coerce").fillna(0)
        attended_n = int((attended.astype(int) > 0).sum())
    else:
        attended_n = 0
    total_contest = len(for_contest_counts)

    return {
        "departments": dept_counts,
        "contest": {
            "all": total_contest,
            "attended": attended_n,
            "not_attended": total_contest - attended_n,
        },
    }


def prepare_student_details_dataframe(
    students_df: pd.DataFrame,
    contest_date: Optional[str] = None,
) -> tuple[pd.DataFrame, Optional[str], str]:
    """Attach weekly report_* fields; keep lifetime contest_attended for Student Details."""
    working_df, used_date, contest_title = prepare_report_dataframe(students_df, contest_date)
    if "contest_attended" in working_df.columns:
        working_df["contest_attended"] = (
            pd.to_numeric(working_df["contest_attended"], errors="coerce").fillna(0).astype(int)
        )
    else:
        working_df["contest_attended"] = 0
    return working_df, used_date, contest_title


def get_student_sort_columns() -> list[dict[str, str]]:
    """Sortable columns: text = A→Z, numeric = Low→High."""
    return [
        {"param": "s_no", "column": "S.No", "label": "S.No", "kind": "numeric"},
        {"param": "register_no", "column": "Register No", "label": "Register No", "kind": "numeric"},
        {"param": "name", "column": "Name", "label": "Name", "kind": "text"},
        {"param": "department", "column": "Department", "label": "Department", "kind": "text"},
        {"param": "username", "column": "LeetCode Username", "label": "Username", "kind": "text"},
        {"param": "total_solved", "column": "total_solved", "label": "Total", "kind": "numeric"},
        {"param": "solved_easy", "column": "solved_easy", "label": "Easy", "kind": "numeric"},
        {"param": "solved_medium", "column": "solved_medium", "label": "Medium", "kind": "numeric"},
        {"param": "solved_hard", "column": "solved_hard", "label": "Hard", "kind": "numeric"},
        {"param": "contest_attended", "column": "contest_attended", "label": "Contests", "kind": "numeric"},
        {"param": "profile_ranking", "column": "profile_ranking", "label": "Profile Rank", "kind": "numeric"},
        {"param": "badge", "column": "badge_details", "label": "Badge", "kind": "badge"},
        {"param": "level", "column": "level", "label": "Level", "kind": "text"},
    ]


def sort_students(
    students_df: pd.DataFrame,
    sort_by: Optional[str] = None,
    sort_dir: str = "asc",
) -> pd.DataFrame:
    """Sort students: text A→Z / numeric Low→High (asc), reverse for desc."""
    if not sort_by:
        return students_df

    col_info = next((c for c in get_student_sort_columns() if c["param"] == sort_by), None)
    if not col_info and sort_by in ("latest_badge", "badge_details"):
        col_info = next((c for c in get_student_sort_columns() if c["param"] == "badge"), None)
    if not col_info:
        return students_df

    column = col_info["column"]
    if column not in students_df.columns and col_info.get("kind") != "badge":
        return students_df

    ascending = str(sort_dir).lower() != "desc"
    result = students_df.copy()
    kind = col_info.get("kind", "text")

    if kind == "badge":
        from services.format_utils import badge_sort_rank

        result["_sort_key"] = result.apply(
            lambda row: badge_sort_rank(
                row.get("latest_badge"),
                row.get("level"),
                row.get("badge_details"),
            ),
            axis=1,
        )
        result = result.sort_values("_sort_key", ascending=ascending, na_position="last")
        result = result.drop(columns=["_sort_key"])
    elif kind == "numeric":
        result["_sort_key"] = pd.to_numeric(result[column], errors="coerce")
        result = result.sort_values("_sort_key", ascending=ascending, na_position="last")
        result = result.drop(columns=["_sort_key"])
    else:
        result = result.sort_values(
            column,
            ascending=ascending,
            na_position="last",
            key=lambda series: series.astype(str).str.lower(),
        )

    return result.reset_index(drop=True)


def filter_department_report(
    dept_report_df: pd.DataFrame,
    department: Optional[str] = None,
) -> pd.DataFrame:
    """Filter department report by department (excludes grand Total when filtered)."""
    if dept_report_df is None or dept_report_df.empty:
        return dept_report_df

    if not department or department.lower() in ("all", "all departments", ""):
        return dept_report_df

    if department == MISSING_DEPT_LABEL:
        return pd.DataFrame()

    dept_key = department.strip().upper()
    match_values = {dept_key}
    if dept_key == "AI&DS":
        match_values.add("AIDS")

    filtered = dept_report_df[
        dept_report_df["Dept"].astype(str).str.upper().isin(match_values)
    ].copy()
    return filtered


def get_department_report_display_columns() -> dict[str, list[tuple[str, str]]]:
    """Return (header label, dataframe column key) pairs for the department report table."""
    return {
        "problems": [(key, key) for key in PROBLEM_SOLVED_CONFIG],
        "contest_ranking": [
            (bucket["label"], contest_ranking_col(bucket["label"]))
            for bucket in CONTEST_RANKING_BUCKETS
        ],
        "contest_rating": [
            (bucket["label"], bucket["label"]) for bucket in CONTEST_RATING_BUCKETS
        ],
        "levels": [(level, level) for level in LEVELS],
        "global_ranking": [
            (bucket["label"], global_ranking_col(bucket["label"]))
            for bucket in GLOBAL_RANKING_SUMMARY_BUCKETS
        ],
        "overall_contest_ranking": [
            (bucket["label"], global_ranking_col(bucket["label"]))
            for bucket in GLOBAL_RANKING_SUMMARY_BUCKETS
        ],
    }


def _quality_check(
    students_df: pd.DataFrame,
    dept_report_df: pd.DataFrame,
    missing_dept_df: pd.DataFrame,
    grouped: bool = False,
) -> dict[str, Any]:
    """Run quality checks before report generation."""
    checks: list[dict[str, Any]] = []
    passed = True

    dept_col = "Report Department" if grouped else "Department"
    valid_students = students_df[students_df[dept_col] != MISSING_DEPT_LABEL]
    expected_strength = len(valid_students)

    if not dept_report_df.empty and "Total" in dept_report_df["Dept"].values:
        total_row = dept_report_df[dept_report_df["Dept"] == "Total"].iloc[0]
        actual_strength = int(total_row["Total Strength"])
        strength_ok = actual_strength == expected_strength
        checks.append({
            "name": "Department total strength",
            "expected": expected_strength,
            "actual": actual_strength,
            "passed": strength_ok,
        })
        if not strength_ok:
            passed = False

        # Problem buckets must sum to total attended (only attendees are bucketed)
        attended = int(total_row["Total attended"])
        bucket_sum = sum(int(total_row.get(k, 0) or 0) for k in PROBLEM_SOLVED_CONFIG)
        bucket_ok = bucket_sum == attended
        checks.append({
            "name": "Problem bucket sum equals total attended",
            "expected": attended,
            "actual": bucket_sum,
            "passed": bucket_ok,
        })
        if not bucket_ok:
            passed = False

        bucket_4 = int(total_row.get("4", 0) or 0)
        attended_ok = bucket_4 <= attended
        checks.append({
            "name": "Solved-4 count <= Total attended",
            "expected": f"<= {attended}",
            "actual": bucket_4,
            "passed": attended_ok,
        })
        if not attended_ok:
            passed = False

        ranking_sum = sum(
            int(total_row.get(contest_ranking_col(b["label"]), 0) or 0)
            for b in CONTEST_RANKING_BUCKETS
        )
        ranking_ok = ranking_sum == attended
        checks.append({
            "name": "Ranking bucket sum equals total attended",
            "expected": attended,
            "actual": ranking_sum,
            "passed": ranking_ok,
        })
        if not ranking_ok:
            passed = False

        rating_sum = sum(
            int(total_row.get(b["label"], 0) or 0) for b in CONTEST_RATING_BUCKETS
        )
        rating_ok = rating_sum == attended
        checks.append({
            "name": "Rating bucket sum equals total attended",
            "expected": attended,
            "actual": rating_sum,
            "passed": rating_ok,
        })
        if not rating_ok:
            passed = False

        level_sum = sum(int(total_row.get(level, 0) or 0) for level in LEVELS)
        level_ok = level_sum == attended
        checks.append({
            "name": "Level sum equals total attended",
            "expected": attended,
            "actual": level_sum,
            "passed": level_ok,
        })
        if not level_ok:
            passed = False

        profile_sum = sum(
            int(total_row.get(profile_ranking_col(b["label"]), 0) or 0)
            for b in PROFILE_RANKING_BUCKETS
        )
        strength = int(total_row["Total Strength"])
        profile_ok = profile_sum == strength
        checks.append({
            "name": "Profile ranking bucket sum equals total strength",
            "expected": strength,
            "actual": profile_sum,
            "passed": profile_ok,
        })
        if not profile_ok:
            passed = False

    blank_in_dept = MISSING_DEPT_LABEL in [
        d for d in dept_report_df["Dept"].tolist() if d != "Total"
    ] if not dept_report_df.empty else False
    checks.append({
        "name": "No blank department in report",
        "expected": True,
        "actual": not blank_in_dept,
        "passed": not blank_in_dept,
    })
    if blank_in_dept:
        passed = False

    missing_count = len(missing_dept_df)
    actual_missing = len(students_df[students_df[dept_col] == MISSING_DEPT_LABEL])
    checks.append({
        "name": "Missing department count",
        "expected": actual_missing,
        "actual": missing_count,
        "passed": missing_count == actual_missing,
    })

    return {"passed": passed, "checks": checks}


def _weekly_contains_filter(series: pd.Series, term: str) -> pd.Series:
    """Case-insensitive substring match for weekly contest text filters."""
    text = (term or "").strip().lower()
    if not text:
        return pd.Series(True, index=series.index)
    return series.astype(str).str.lower().str.contains(text, na=False, regex=False)


def get_best_global_ranking(row: Any) -> Optional[int]:
    """Return student's overall numerical Contest Global Ranking from LeetCode profile contest statistics card."""
    for key in (
        "contest_ranking",
        "raw_global_ranking",
        "global_ranking",
        "globalRanking",
        "Global Ranking",
        "Overall Global Ranking",
        "excel_contest_ranking",
        "excel_global_ranking",
    ):
        val = row.get(key) if hasattr(row, "get") else getattr(row, key, None)
        if val is not None and not (isinstance(val, float) and pd.isna(val)):
            try:
                num = int(round(float(str(val).replace(",", "").strip())))
                if num > 0:
                    return num
            except (ValueError, TypeError):
                pass
    return None


def matches_global_ranking_filter(rank: Optional[int], filter_label: str) -> bool:
    """Check if numeric rank satisfies the given global ranking filter label."""
    norm = (filter_label or "").strip().lower().replace(",", "").replace("–", "-")
    if not norm or norm in ("all", "all global rankings", "all overall global rankings"):
        return True
    if "n/a" in norm or "not available" in norm or norm == "na":
        return rank is None or rank <= 0
    if rank is None or rank <= 0:
        return False
    if ("20000" in norm or "20k" in norm) and ("100000" in norm or "100k" in norm):
        return 20000 <= rank <= 100000
    if "below" in norm or ("<" in norm and "20000" in norm):
        return rank <= 20000
    if "above" in norm or ">" in norm or "100000" in norm or "100k" in norm:
        return rank > 100000
    return False


def calculate_overall_ranking_summary(
    students_df: pd.DataFrame,
    department_filter: str = "",
) -> dict[str, int]:
    """Calculate overall global ranking summary counts for selected department (or all)."""
    df = filter_students(students_df, {"department": department_filter}) if department_filter else students_df

    below_20k = 0
    from_20k_to_100k = 0
    above_100k = 0
    not_available = 0

    for _, row in df.iterrows():
        rank = get_best_global_ranking(row)
        if rank is None or rank <= 0:
            not_available += 1
        elif rank <= 20000:
            below_20k += 1
        elif rank <= 100000:
            from_20k_to_100k += 1
        else:
            above_100k += 1

    return {
        "below_20k": below_20k,
        "from_20k_to_100k": from_20k_to_100k,
        "above_100k": above_100k,
        "not_available": not_available,
        "total": len(df),
    }


def calculate_department_wise_global_ranking_comparison(
    students_df: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Calculate department comparison across 5 main departments for global ranking buckets."""
    depts = ["CSE", "EEE", "ECE", "IT", "AI&DS"]
    rows = []

    total_below_20k = 0
    total_20k_100k = 0
    total_above_100k = 0
    total_na = 0
    grand_total = 0

    for dept in depts:
        summary = calculate_overall_ranking_summary(students_df, department_filter=dept)
        rows.append({
            "department": dept,
            "below_20k": summary["below_20k"],
            "from_20k_to_100k": summary["from_20k_to_100k"],
            "above_100k": summary["above_100k"],
            "not_available": summary["not_available"],
            "total": summary["total"],
        })
        total_below_20k += summary["below_20k"]
        total_20k_100k += summary["from_20k_to_100k"]
        total_above_100k += summary["above_100k"]
        total_na += summary["not_available"]
        grand_total += summary["total"]

    # Grand total row
    rows.append({
        "department": "Total",
        "below_20k": total_below_20k,
        "from_20k_to_100k": total_20k_100k,
        "above_100k": total_above_100k,
        "not_available": total_na,
        "total": grand_total,
    })
    return rows


def _weekly_contest_filter_kwargs(scoped: dict[str, str]) -> dict[str, str]:
    """Map active weekly contest filter dict to filter function kwargs."""
    return {
        "contest_filter": scoped["contest"],
        "department_filter": scoped["department"],
        "s_no_filter": scoped["s_no"],
        "register_filter": scoped["register_no"],
        "name_filter": scoped["name"],
        "search_filter": scoped.get("search", ""),
        "link_status_filter": scoped["link_status"],
        "problems_filter": scoped["problems_solved"],
        "rating_filter": scoped["contest_rating"],
        "rank_filter": scoped["contest_rank"],
        "global_rank_filter": scoped.get("global_rank", ""),
    }


def filter_weekly_contest_details(
    students_df: pd.DataFrame,
    contest_filter: str = "",
    department_filter: str = "",
    s_no_filter: str = "",
    register_filter: str = "",
    name_filter: str = "",
    search_filter: str = "",
    link_status_filter: str = "",
    problems_filter: str = "",
    rating_filter: str = "",
    rank_filter: str = "",
    global_rank_filter: str = "",
) -> pd.DataFrame:
    """Filter students for weekly contest details view."""
    result = ensure_student_columns(students_df)

    s_no = (s_no_filter or "").strip()
    if s_no and "S.No" in result.columns:
        result = result[_weekly_contains_filter(result["S.No"], s_no)]

    register = (register_filter or "").strip()
    if register and "Register No" in result.columns:
        result = result[_weekly_contains_filter(result["Register No"], register)]

    name = (name_filter or "").strip()
    if name and "Name" in result.columns:
        result = result[_weekly_contains_filter(result["Name"], name)]

    search = (search_filter or "").strip()
    if search:
        term = search.lower()
        reg_match = (
            result["Register No"].astype(str).str.lower().str.contains(term, na=False, regex=False)
            if "Register No" in result.columns
            else pd.Series(False, index=result.index)
        )
        name_match = (
            result["Name"].astype(str).str.lower().str.contains(term, na=False, regex=False)
            if "Name" in result.columns
            else pd.Series(False, index=result.index)
        )
        result = result[reg_match | name_match]

    department = _normalize_department_filter(department_filter or "")
    if department and department.lower() not in ("all", "all departments"):
        result = filter_students(result, {"department": department})

    link_status = (link_status_filter or "").strip()
    if link_status:
        result = result[
            result.apply(lambda row: get_leetcode_profile_link_status(row) == link_status, axis=1)
        ]

    contest = (contest_filter or "").strip().lower()
    attended_col = "report_contest_attended"
    if attended_col in result.columns:
        attended = pd.to_numeric(result[attended_col], errors="coerce").fillna(0).astype(int)
        if contest in ("attended", "yes", "1"):
            result = result[attended > 0]
        elif contest in ("not_attended", "no", "0"):
            result = result[attended == 0]

    problems = (problems_filter or "").strip()
    if problems and "report_problems_solved" in result.columns:
        solved = (
            pd.to_numeric(result["report_problems_solved"], errors="coerce")
            .fillna(0)
            .astype(int)
            .clip(0, 4)
        )
        result = result[solved == int(problems)]

    rating = (rating_filter or "").strip()
    if rating and "report_contest_rating" in result.columns:
        result = result[
            result["report_contest_rating"].apply(
                lambda val: (
                    classify_contest_rating(float(val)) == rating
                    if val is not None and not (isinstance(val, float) and pd.isna(val))
                    else False
                )
            )
        ]

    rank = (rank_filter or "").strip()
    if rank:
        if rank in [b["label"] for b in CONTEST_RANKING_BUCKETS]:
            if "report_contest_ranking" in result.columns:
                result = result[
                    result["report_contest_ranking"].apply(
                        lambda val: (
                            classify_contest_ranking(int(round(float(val)))) == rank
                            if val is not None and not (isinstance(val, float) and pd.isna(val))
                            else False
                        )
                    )
                ]
        elif not global_rank_filter:
            result = result[
                result.apply(
                    lambda row: matches_global_ranking_filter(get_best_global_ranking(row), rank),
                    axis=1,
                )
            ]

    global_rank = (global_rank_filter or "").strip()
    if global_rank:
        result = result[
            result.apply(
                lambda row: matches_global_ranking_filter(get_best_global_ranking(row), global_rank),
                axis=1,
            )
        ]

    return result.reset_index(drop=True)


def sort_weekly_contest_details(
    students_df: pd.DataFrame,
    sort_by: Optional[str] = None,
    sort_dir: str = "asc",
) -> pd.DataFrame:
    """Sort weekly contest rows; global ranking uses best available numerical rank."""
    if not sort_by or sort_by not in ("global_ranking", "overall_contest_ranking", "overall_global_ranking", "global_rank", "overall_global_rank"):
        return students_df

    ascending = str(sort_dir).lower() != "desc"
    result = students_df.copy()
    result["_sort_key"] = result.apply(get_best_global_ranking, axis=1)
    result = result.sort_values("_sort_key", ascending=ascending, na_position="last")
    return result.drop(columns=["_sort_key"]).reset_index(drop=True)


def _weekly_contest_active_filters(filters: dict[str, str]) -> dict[str, str]:
    """Normalize weekly contest filter values from query params."""
    return {
        "department": (filters.get("department") or "").strip(),
        "s_no": (filters.get("s_no") or "").strip(),
        "register_no": (filters.get("register_no") or "").strip(),
        "name": (filters.get("name") or "").strip(),
        "search": (filters.get("search") or filters.get("student_search") or "").strip(),
        "link_status": (filters.get("link_status") or "").strip(),
        "contest": (filters.get("contest") or "").strip().lower(),
        "problems_solved": (filters.get("problems_solved") or "").strip(),
        "contest_rating": (filters.get("contest_rating") or "").strip(),
        "contest_rank": (filters.get("contest_rank") or "").strip(),
        "global_rank": (filters.get("global_rank") or filters.get("global_ranking") or filters.get("overall_global_rank") or filters.get("overall_global_ranking") or "").strip(),
    }


def weekly_contest_filter_counts(students_df: pd.DataFrame, filters: dict[str, str]) -> dict[str, Any]:
    """Counts for weekly contest details column filter dropdowns."""
    active = _weekly_contest_active_filters(filters)

    def subset(exclude: str) -> pd.DataFrame:
        scoped = {**active, exclude: ""}
        return filter_weekly_contest_details(students_df, **_weekly_contest_filter_kwargs(scoped))

    dept_base = subset("department")
    dept_counts: dict[str, int] = {"all": len(dept_base)}
    for dept in ["CSE A", "CSE B", "EEE", "ECE", "IT A", "IT B", "AI&DS"]:
        dept_counts[dept] = len(filter_students(dept_base, {"department": dept}))
    for dept in REPORT_DEPARTMENT_ORDER:
        if dept not in dept_counts:
            dept_counts[dept] = len(filter_students(dept_base, {"department": dept}))
    dept_counts[MISSING_DEPT_LABEL] = len(
        filter_students(dept_base, {"department": MISSING_DEPT_LABEL})
    )

    link_base = subset("link_status")
    link_counts: dict[str, int] = {"all": len(link_base)}
    for label in LEETCODE_PROFILE_LINK_STATUS_OPTIONS:
        link_counts[label] = int(
            link_base.apply(lambda row: get_leetcode_profile_link_status(row) == label, axis=1).sum()
        )

    contest_base = subset("contest")
    if "report_contest_attended" not in contest_base.columns:
        contest_counts = {"all": len(contest_base), "attended": 0, "not_attended": len(contest_base)}
    else:
        attended = pd.to_numeric(contest_base["report_contest_attended"], errors="coerce").fillna(0).astype(int)
        attended_n = int((attended > 0).sum())
        total = len(contest_base)
        contest_counts = {
            "all": total,
            "attended": attended_n,
            "not_attended": total - attended_n,
        }

    problems_base = subset("problems_solved")
    if "report_problems_solved" in problems_base.columns:
        solved = (
            pd.to_numeric(problems_base["report_problems_solved"], errors="coerce")
            .fillna(0)
            .astype(int)
            .clip(0, 4)
        )
        problems_counts: dict[str, int] = {"all": len(problems_base)}
        for value in PROBLEM_SOLVED_CONFIG:
            problems_counts[value] = int((solved == int(value)).sum())
    else:
        problems_counts = {"all": len(problems_base), **{key: 0 for key in PROBLEM_SOLVED_CONFIG}}

    rating_base = subset("contest_rating")
    rating_counts: dict[str, int] = {"all": len(rating_base)}
    for bucket in CONTEST_RATING_BUCKETS:
        label = bucket["label"]
        if "report_contest_rating" not in rating_base.columns:
            rating_counts[label] = 0
            continue
        rating_counts[label] = int(
            rating_base["report_contest_rating"].apply(
                lambda val: (
                    classify_contest_rating(float(val)) == label
                    if val is not None and not (isinstance(val, float) and pd.isna(val))
                    else False
                )
            ).sum()
        )

    rank_base = subset("contest_rank")
    rank_counts: dict[str, int] = {"all": len(rank_base)}
    for bucket in CONTEST_RANKING_BUCKETS:
        label = bucket["label"]
        if "report_contest_ranking" not in rank_base.columns:
            rank_counts[label] = 0
            continue
        rank_counts[label] = int(
            rank_base["report_contest_ranking"].apply(
                lambda val: (
                    classify_contest_ranking(int(round(float(val)))) == label
                    if val is not None and not (isinstance(val, float) and pd.isna(val))
                    else False
                )
            ).sum()
        )

    global_rank_base = subset("global_rank")
    global_rank_counts: dict[str, int] = {"all": len(global_rank_base)}
    for label in GLOBAL_RANKING_FILTER_OPTIONS:
        global_rank_counts[label] = int(
            global_rank_base.apply(
                lambda row: matches_global_ranking_filter(get_best_global_ranking(row), label),
                axis=1,
            ).sum()
        )

    return {
        "department": dept_counts,
        "link_status": link_counts,
        "contest": contest_counts,
        "problems_solved": problems_counts,
        "contest_rating": rating_counts,
        "contest_rank": rank_counts,
        "global_rank": global_rank_counts,
        "overall_global_rank": global_rank_counts,
        "overall_global_ranking": global_rank_counts,
        "overall_contest_rank": global_rank_counts,
    }


def build_weekly_contest_detail_records(row: pd.Series) -> dict[str, Any]:
    """Format one student row for weekly contest details table/export."""
    from services.format_utils import (
        format_contest_rating,
        format_contest_ranking,
        format_profile_ranking,
        format_total_solved,
        format_s_no,
    )

    attended = int(row.get("report_contest_attended") or 0) > 0
    problems = int(row.get("report_problems_solved") or 0)
    reg_no = format_s_no(row.get("Register No"))
    rating = format_contest_rating(row.get("report_contest_rating"))
    ranking = format_contest_ranking(row.get("report_contest_ranking"))
    raw_global_rank = get_best_global_ranking(row)
    formatted_global_rank = f"{raw_global_rank:,}" if raw_global_rank is not None else "N/A"
    level = str(row.get("level") or "Unrated").strip()
    raw_dept = str(row.get("Department") or row.get("DEPT") or row.get("Report Department") or "").strip()
    profile_rank_fmt = format_profile_ranking(row.get("profile_ranking"))
    total_solved = format_total_solved(row.get("total_solved"))
    solved_easy = format_total_solved(row.get("solved_easy"))
    solved_medium = format_total_solved(row.get("solved_medium"))
    solved_hard = format_total_solved(row.get("solved_hard"))

    return {
        "S.No": format_s_no(row.get("S.No")) or "",
        "Register No": reg_no if reg_no is not None else "",
        "Student Name": row.get("Name") or "",
        "Name": row.get("Name") or "",
        "Department": raw_dept,
        "Level": level,
        "level": level,
        "Leetcode profile Link Status": get_leetcode_profile_link_status(row),
        "Contest": "Attended" if attended else "Not Attended",
        "Problems Solved (out of 4)": str(problems) if attended else "-",
        "Solved": f"{problems}/4" if attended else "-",
        "Contest Rating": rating if attended and rating is not None else "-",
        "Contest Rank": ranking if attended and ranking is not None else "-",
        "Overall Global Ranking": formatted_global_rank,
        "Global Ranking": formatted_global_rank,
        "raw_global_ranking": raw_global_rank,
        "raw_overall_global_ranking": raw_global_rank,
        "Over all contest global": raw_global_rank if raw_global_rank is not None else "-",
        "Overall Contest Global Ranking": raw_global_rank if raw_global_rank is not None else "-",
        "Contest Global Ranking Details": raw_global_rank if raw_global_rank is not None else "-",
        "Global Ranking Details": raw_global_rank if raw_global_rank is not None else "-",
        "Total Problems Solved": total_solved,
        "Totall problem solved": total_solved,
        "Total Problem Solved": total_solved,
        "Overall Problems Solved": total_solved,
        "Easy": solved_easy,
        "Medium": solved_medium,
        "Hard": solved_hard,
        "Profile Rank": profile_rank_fmt if profile_rank_fmt is not None else "N/A",
        "profile_rank": profile_rank_fmt if profile_rank_fmt is not None else "N/A",
    }


def get_weekly_contest_details(
    students_df: pd.DataFrame,
    contest_date: Optional[str] = None,
    contest_filter: str = "",
    department_filter: str = "",
    s_no_filter: str = "",
    register_filter: str = "",
    name_filter: str = "",
    search_filter: str = "",
    link_status_filter: str = "",
    problems_filter: str = "",
    rating_filter: str = "",
    rank_filter: str = "",
    global_rank_filter: str = "",
    sort_by: Optional[str] = None,
    sort_dir: str = "asc",
) -> tuple[list[dict[str, Any]], Optional[str], str, dict[str, Any]]:
    """Return weekly contest student rows, date used, title, and filter counts."""
    working_df, used_date, contest_title = prepare_report_dataframe(students_df, contest_date)
    filters = {
        "department": department_filter,
        "s_no": s_no_filter,
        "register_no": register_filter,
        "name": name_filter,
        "search": search_filter,
        "link_status": link_status_filter,
        "contest": contest_filter,
        "problems_solved": problems_filter,
        "contest_rating": rating_filter,
        "contest_rank": rank_filter,
        "global_rank": global_rank_filter,
    }
    counts = weekly_contest_filter_counts(working_df, filters)
    filtered = filter_weekly_contest_details(
        working_df,
        contest_filter=contest_filter,
        department_filter=department_filter,
        s_no_filter=s_no_filter,
        register_filter=register_filter,
        name_filter=name_filter,
        search_filter=search_filter,
        link_status_filter=link_status_filter,
        problems_filter=problems_filter,
        rating_filter=rating_filter,
        rank_filter=rank_filter,
        global_rank_filter=global_rank_filter,
    )
    filtered = sort_weekly_contest_details(filtered, sort_by=sort_by, sort_dir=sort_dir)

    records = [build_weekly_contest_detail_records(row) for _, row in filtered.iterrows()]
    return records, used_date, contest_title, counts


def get_weekly_contest_filter_options() -> dict[str, list[str]]:
    """Dropdown values for weekly contest details column filters."""
    return {
        "departments": ["CSE", "EEE", "ECE", "IT", "AI&DS"],
        "link_status": list(LEETCODE_PROFILE_LINK_STATUS_OPTIONS),
        "problems_solved": list(PROBLEM_SOLVED_CONFIG.keys()),
        "contest_rating": [bucket["label"] for bucket in CONTEST_RATING_BUCKETS],
        "contest_rank": [bucket["label"] for bucket in CONTEST_RANKING_BUCKETS],
        "global_rank": list(GLOBAL_RANKING_FILTER_OPTIONS),
        "overall_contest_rank": list(GLOBAL_RANKING_FILTER_OPTIONS),
    }


def build_student_contest_report_record(row: pd.Series) -> dict[str, Any]:
    """Format one student row for the Generate Reports student contest export."""
    from services.format_utils import (
        format_contest_rating,
        format_contest_ranking,
        format_profile_ranking,
        format_s_no,
        format_total_solved,
    )

    attended = int(row.get("report_contest_attended") or 0) > 0
    problems = int(row.get("report_problems_solved") or 0)
    reg_no = format_s_no(row.get("Register No"))
    rating = format_contest_rating(row.get("report_contest_rating"))
    ranking = format_contest_ranking(row.get("report_contest_ranking"))
    raw_global_rank = get_best_global_ranking(row)
    formatted_global_rank = f"{raw_global_rank:,}" if raw_global_rank is not None else "N/A"
    global_rank = format_profile_ranking(row.get("profile_ranking"))
    report_dept = row.get("Report Department") or get_report_department(row.get("Department"))
    dept_display = REPORT_DEPARTMENT_LABELS.get(str(report_dept), report_dept or "")
    profile_url = row.get("Leetcode Link Clean") or row.get("Leetcode Link") or ""

    return {
        "S.No": format_s_no(row.get("S.No")) or "",
        "Register Number": reg_no if reg_no is not None else "",
        "Name": row.get("Name") or "",
        "Department": dept_display,
        "Profile URL": profile_url,
        "Contest (Attended / Not Attended)": "Attended" if attended else "Not Attended",
        "Problems Solved (out of 4)": str(problems) if attended else "-",
        "Problems Solved": f"{problems}/4" if attended else "-",
        "Contest Rating": rating if attended and rating is not None else "-",
        "Contest Rank (Weekly Contest Rank)": ranking if attended and ranking is not None else "-",
        "Contest Rank": ranking if attended and ranking is not None else "-",
        "Overall Global Ranking (Overall / Best)": formatted_global_rank,
        "Overall Global Ranking": formatted_global_rank,
        "Overall Global Profile Ranking": formatted_global_rank,
        "over all Global Profile Ranking": formatted_global_rank,
        "Global Profile Ranking": global_rank if global_rank is not None else "N/A",
        "Global Ranking": formatted_global_rank,
        "Overall Problems Solved": format_total_solved(row.get("total_solved")),
        "Easy": format_total_solved(row.get("solved_easy")),
        "Medium": format_total_solved(row.get("solved_medium")),
        "Hard": format_total_solved(row.get("solved_hard")),
        "Overall Contest Global Ranking": formatted_global_rank,
    }


def get_student_contest_report_records(
    students_df: pd.DataFrame,
    contest_date: Optional[str] = None,
    department: Optional[str] = None,
) -> tuple[list[dict[str, Any]], Optional[str], str]:
    """Return student contest report rows with contest date used and title."""
    working_df, used_date, contest_title = prepare_report_dataframe(students_df, contest_date)
    if department and department.strip().lower() not in ("all", "all departments", ""):
        dept_mask = filter_by_department(working_df, department.strip())
        working_df = working_df[dept_mask].copy()
    records = [build_student_contest_report_record(row) for _, row in working_df.iterrows()]
    return records, used_date, contest_title
