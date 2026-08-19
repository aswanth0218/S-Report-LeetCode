"""Department grouping and report department utilities."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from config import MISSING_DEPT_LABEL, REPORT_DEPARTMENT_ORDER

# Maps input department variants to grouped report department
DEPT_GROUPING_MAP = {
    # CSE variants
    "CSE": "CSE",
    "CSE A": "CSE",
    "CSE B": "CSE",
    "CSE C": "CSE",
    "CSE-A": "CSE",
    "CSE-B": "CSE",
    "CSE-C": "CSE",
    "CSE_A": "CSE",
    "CSE_B": "CSE",
    "CSEA": "CSE",
    "CSEB": "CSE",
    "CSE (A)": "CSE",
    "CSE (B)": "CSE",
    "CSE(A)": "CSE",
    "CSE(B)": "CSE",
    "CSE - A": "CSE",
    "CSE - B": "CSE",
    "COMPUTER SCIENCE": "CSE",
    "COMPUTER SCIENCE AND ENGINEERING": "CSE",
    "COMPUTER SCIENCE & ENGINEERING": "CSE",
    "COMP SCIENCE": "CSE",
    "B.E CSE": "CSE",
    "BE CSE": "CSE",
    "B.E. CSE": "CSE",
    "B.TECH CSE": "CSE",
    "BTECH CSE": "CSE",

    # IT variants
    "IT": "IT",
    "IT A": "IT",
    "IT B": "IT",
    "IT-A": "IT",
    "IT-B": "IT",
    "IT_A": "IT",
    "IT_B": "IT",
    "ITA": "IT",
    "ITB": "IT",
    "IT (A)": "IT",
    "IT (B)": "IT",
    "IT(A)": "IT",
    "IT(B)": "IT",
    "IT - A": "IT",
    "IT - B": "IT",
    "IT/A": "IT",
    "IT/B": "IT",
    "IT 1": "IT",
    "IT 2": "IT",
    "IT-1": "IT",
    "IT-2": "IT",
    "IT1": "IT",
    "IT2": "IT",
    "INFORMATION TECHNOLOGY": "IT",
    "INFORMATION TECHNOLOGY A": "IT",
    "INFORMATION TECHNOLOGY B": "IT",
    "INFORMATION TECH": "IT",
    "INFOTECH": "IT",
    "INFO TECH": "IT",
    "INFO TECH A": "IT",
    "INFO TECH B": "IT",
    "INFO-TECH": "IT",
    "B.TECH IT": "IT",
    "BTECH IT": "IT",
    "B.TECH - IT": "IT",
    "B.TECH(IT)": "IT",
    "BTECH(IT)": "IT",
    "B.TECH (IT)": "IT",
    "B.E IT": "IT",
    "BE IT": "IT",
    "B.E. IT": "IT",

    # AI&DS variants
    "AIDS": "AI&DS",
    "AI&DS": "AI&DS",
    "AI & DS": "AI&DS",
    "AI-DS": "AI&DS",
    "AI DS": "AI&DS",
    "AI AND DS": "AI&DS",
    "AI_DS": "AI&DS",
    "AI / DS": "AI&DS",
    "AI/DS": "AI&DS",
    "AI&DS A": "AI&DS",
    "AI&DS B": "AI&DS",
    "AI&DS-A": "AI&DS",
    "AI&DS-B": "AI&DS",
    "AIDS A": "AI&DS",
    "AIDS B": "AI&DS",
    "AIDS-A": "AI&DS",
    "AIDS-B": "AI&DS",
    "AI & DS A": "AI&DS",
    "AI & DS B": "AI&DS",
    "ARTIFICIAL INTELLIGENCE": "AI&DS",
    "ARTIFICIAL INTELLIGENCE AND DATA SCIENCE": "AI&DS",
    "ARTIFICIAL INTELLIGENCE & DATA SCIENCE": "AI&DS",
    "B.TECH AI&DS": "AI&DS",
    "BTECH AI&DS": "AI&DS",
    "B.TECH AIDS": "AI&DS",
    "BTECH AIDS": "AI&DS",
    "B.TECH AI & DS": "AI&DS",

    # EEE variants
    "EEE": "EEE",
    "EEE A": "EEE",
    "EEE B": "EEE",
    "EEE-A": "EEE",
    "EEE-B": "EEE",
    "EEE_A": "EEE",
    "EEE_B": "EEE",
    "EEEA": "EEE",
    "EEEB": "EEE",
    "EEE (A)": "EEE",
    "EEE (B)": "EEE",
    "EEE(A)": "EEE",
    "EEE(B)": "EEE",
    "EEE - A": "EEE",
    "EEE - B": "EEE",
    "EE": "EEE",
    "E & E": "EEE",
    "E AND E": "EEE",
    "ELECTRICAL AND ELECTRONICS": "EEE",
    "ELECTRICAL & ELECTRONICS": "EEE",
    "ELECTRICAL AND ELECTRONICS ENGINEERING": "EEE",
    "ELECTRICAL & ELECTRONICS ENGINEERING": "EEE",
    "B.E EEE": "EEE",
    "BE EEE": "EEE",
    "B.E. EEE": "EEE",

    # ECE variants
    "ECE": "ECE",
    "ECE A": "ECE",
    "ECE B": "ECE",
    "ECE-A": "ECE",
    "ECE-B": "ECE",
    "ECE_A": "ECE",
    "ECE_B": "ECE",
    "ECEA": "ECE",
    "ECEB": "ECE",
    "ECE (A)": "ECE",
    "ECE (B)": "ECE",
    "ECE(A)": "ECE",
    "ECE(B)": "ECE",
    "ECE - A": "ECE",
    "ECE - B": "ECE",
    "ELECTRONICS AND COMMUNICATION": "ECE",
    "ELECTRONICS & COMMUNICATION": "ECE",
    "ELECTRONICS AND COMMUNICATION ENGINEERING": "ECE",
    "ELECTRONICS & COMMUNICATION ENGINEERING": "ECE",
    "B.E ECE": "ECE",
    "BE ECE": "ECE",
    "B.E. ECE": "ECE",
}


SEVEN_DEPARTMENTS = ["CSE A", "CSE B", "EEE", "ECE", "IT A", "IT B", "AI&DS"]


def normalize_dept_key(dept: Any) -> str:
    if dept is None:
        return ""
    try:
        if pd.isna(dept):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(dept).strip().upper()
    if not text or text in ("NAN", "NONE", "NULL", "NA", "-", "<NA>", "MISSING DEPARTMENT", "MISSING DEPT"):
        return ""
    return re.sub(r"\s+", " ", text)


def student_department_matches(dept_val: Any, target_dept: str) -> bool:
    """Check if a student's raw or mapped department matches the target filter."""
    if not target_dept or target_dept.strip().lower() in ("all", "all departments", ""):
        return True

    val_key = normalize_dept_key(dept_val)
    target_key = normalize_dept_key(target_dept)

    if not val_key:
        return target_key in ("MISSING DEPARTMENT", "MISSING DEPT", "NA", "N/A")

    if target_key in ("AIDS", "AI&DS", "AI & DS", "AI-DS", "AI DS", "AI AND DS"):
        return (
            get_report_department(val_key) == "AI&DS"
            or val_key in ("AIDS", "AI&DS", "AI & DS", "AI-DS", "AI DS", "AI AND DS", "AIDS A", "AIDS B", "AI&DS A", "AI&DS B")
            or "AI" in val_key
        )

    if target_key in ("CSE A", "CSE-A", "CSE_A", "CSEA", "CSE (A)", "CSE(A)", "CSE - A"):
        return val_key in ("CSE A", "CSE-A", "CSE_A", "CSEA", "CSE (A)", "CSE(A)", "CSE - A")
    if target_key in ("CSE B", "CSE-B", "CSE_B", "CSEB", "CSE (B)", "CSE(B)", "CSE - B"):
        return val_key in ("CSE B", "CSE-B", "CSE_B", "CSEB", "CSE (B)", "CSE(B)", "CSE - B")
    if target_key in ("IT A", "IT-A", "IT_A", "ITA", "IT (A)", "IT(A)", "IT - A"):
        return val_key in ("IT A", "IT-A", "IT_A", "ITA", "IT (A)", "IT(A)", "IT - A", "INFORMATION TECHNOLOGY A", "INFO TECH A")
    if target_key in ("IT B", "IT-B", "IT_B", "ITB", "IT (B)", "IT(B)", "IT - B"):
        return val_key in ("IT B", "IT-B", "IT_B", "ITB", "IT (B)", "IT(B)", "IT - B", "INFORMATION TECHNOLOGY B", "INFO TECH B")
    if target_key == "ECE":
        return get_report_department(val_key) == "ECE" or val_key.startswith("ECE")
    if target_key == "EEE":
        return get_report_department(val_key) == "EEE" or val_key.startswith("EEE") or val_key in ("EE", "E & E", "E AND E")
    if target_key == "CSE":
        return get_report_department(val_key) == "CSE" or val_key.startswith("CSE")
    if target_key == "IT":
        return (
            get_report_department(val_key) == "IT"
            or val_key.startswith("IT")
            or "INFORMATION TECH" in val_key
            or "INFOTECH" in val_key
            or "INFO TECH" in val_key
        )

    return val_key == target_key or get_report_department(val_key) == get_report_department(target_key)


def get_report_department_label(dept: str) -> str:
    """Return user-facing department label for reports and UI."""
    from config import REPORT_DEPARTMENT_LABELS

    return REPORT_DEPARTMENT_LABELS.get(dept, dept)


def get_report_department(dept: Any) -> str:
    """
    Map input departments to grouped report departments:
    CSE A/B -> CSE, IT A/B -> IT, AI & DS / AIDS -> AI&DS, ECE, EEE.
    Blank department returns Missing Department.
    """
    if dept == MISSING_DEPT_LABEL:
        return MISSING_DEPT_LABEL

    key = normalize_dept_key(dept)
    if not key:
        return MISSING_DEPT_LABEL

    if key in DEPT_GROUPING_MAP:
        return DEPT_GROUPING_MAP[key]

    # Pattern match: section departments -> grouped report departments
    if key.startswith("CSE") or "COMPUTER SCIENCE" in key or re.search(r"\bCSE\b|\bCS\b", key):
        return "CSE"
    if (
        key.startswith("IT")
        or "INFORMATION TECH" in key
        or "INFOTECH" in key
        or "INFO TECH" in key
        or re.search(r"\bIT\b", key)
    ):
        return "IT"
    if key.startswith("EEE") or "ELECTRICAL" in key or key in ("EE", "E & E", "E AND E") or re.search(r"\bEEE\b|\bEE\b", key):
        return "EEE"
    if key.startswith("ECE") or "ELECTRONICS" in key or re.search(r"\bECE\b|\bEC\b", key):
        return "ECE"
    if (
        key.startswith("AIDS")
        or "ARTIFICIAL INTELLIGENCE" in key
        or "DATA SCIENCE" in key
        or ("AI" in key and "DS" in key)
        or re.search(r"\bAIDS\b|\bAI&DS\b", key)
    ):
        return "AI&DS"
    if key in REPORT_DEPARTMENT_ORDER:
        return key

    return key


def get_all_filter_departments(students_departments: list[str]) -> dict[str, list[str]]:
    """Return grouped filter options: report dept -> list of sections."""
    sections: dict[str, list[str]] = {d: [] for d in REPORT_DEPARTMENT_ORDER}
    for dept in students_departments:
        if not dept or dept == MISSING_DEPT_LABEL:
            continue
        report_dept = get_report_department(dept)
        if report_dept not in sections:
            sections[report_dept] = []
        if dept not in sections[report_dept]:
            sections[report_dept].append(dept)

    for report_dept in sections:
        sections[report_dept].sort()

    return sections


def ordered_report_departments(students_df_report_depts: list[str]) -> list[str]:
    """Return report departments in fixed order: CSE, AI&DS, IT, ECE, EEE."""
    present = set(students_df_report_depts)
    ordered = [d for d in REPORT_DEPARTMENT_ORDER if d in present]
    extras = sorted(d for d in present if d not in REPORT_DEPARTMENT_ORDER and d != MISSING_DEPT_LABEL)
    return ordered + extras
