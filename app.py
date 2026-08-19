"""
S-REPORT - LeetCode Student Performance Report System
Excel-based, no database. All data processed in memory with Pandas.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime
from io import BytesIO
from urllib.parse import unquote_plus

import pandas as pd
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from config import (
    ALLOWED_EXTENSIONS,
    AUTH_PASSWORD,
    AUTH_USERNAME,
    CACHE_FOLDER,
    EXPORT_FOLDER,
    GLOBAL_RANKING_FILTER_OPTIONS,
    LOGIN_BANNER_FILE,
    LOGIN_BANNER_SOURCES,
    MAX_RECENT_UPLOADS,
    MISSING_DEPT_LABEL,
    RECENT_UPLOADS_FILE,
    REPORT_DEPARTMENT_LABELS,
    REPORT_DEPARTMENT_ORDER,
    STATIC_IMAGES_FOLDER,
    UPLOAD_FOLDER,
)
from services.cache_service import (
    clear_all_profile_cache,
    clear_expired_cache,
    clear_incomplete_profile_cache,
    get_cache_stats,
)
from services.excel_service import (
    create_sample_input_excel_bytes,
    ensure_folders,
    generate_full_leetcode_excel,
    generate_missing_data_issues_excel_bytes,
    generate_overall_department_excel,
    generate_s_report_excel_bytes,
    generate_solved_problems_excel,
    generate_student_contest_report_excel,
    generate_weekly_contest_details_excel,
    read_input_excel,
)
from services.format_utils import format_student_for_display
from services.leetcode_service import (
    batch_fetch_students,
    clear_past_contests_cache,
    find_contest_for_date,
    get_available_contest_dates,
    parse_contest_date_param,
)
from services.report_service import (
    calculate_dashboard_stats,
    calculate_department_report,
    calculate_grouped_department_report,
    calculate_overall_ranking_summary,
    calculate_department_wise_global_ranking_comparison,
    filter_department_report,
    get_department_report_display_columns,
    compute_student_filter_counts,
    filter_students,
    get_student_sort_columns,
    get_student_contest_report_records,
    get_weekly_contest_details,
    get_weekly_contest_filter_options,
    normalize_department_filter,
    prepare_student_details_dataframe,
    sort_students,
)
from services.student_data_service import ensure_student_columns
from services.validation_service import (
    _is_blank,
    assert_valid_input_columns,
    categorize_data_issues,
    extract_contest_date_from_filename,
    prepare_students_dataframe,
    validate_dataframe,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "s-report-dev-secret-key-change-in-production")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB

ensure_folders(UPLOAD_FOLDER, EXPORT_FOLDER, CACHE_FOLDER, STATIC_IMAGES_FOLDER)


@app.template_filter("dept_label")
def dept_label_filter(dept: str) -> str:
    """User-facing department name (e.g. AI&DS -> AI & DS)."""
    return REPORT_DEPARTMENT_LABELS.get(str(dept), dept)


@app.errorhandler(413)
def request_entity_too_large(_error):
    flash("File too large. Maximum upload size is 16 MB.", "danger")
    return redirect(url_for("upload"))


_process_lock = threading.Lock()

PUBLIC_ENDPOINTS = frozenset({"login", "logout", "login_bg", "static"})


def _resolve_login_banner_path() -> str | None:
    """Return path to Nandha login background image."""
    dest = os.path.join(STATIC_IMAGES_FOLDER, LOGIN_BANNER_FILE)
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return dest
    os.makedirs(STATIC_IMAGES_FOLDER, exist_ok=True)
    for src in LOGIN_BANNER_SOURCES:
        if src == dest:
            continue
        if os.path.isfile(src) and os.path.getsize(src) > 0:
            try:
                shutil.copy2(src, dest)
                return dest
            except OSError:
                return src
    return None


def _ensure_login_banner() -> None:
    _resolve_login_banner_path()


_ensure_login_banner()


@app.before_request
def enforce_login():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if session.get("authenticated"):
        return None
    if request.endpoint is None:
        return None
    if request.path.startswith("/static/"):
        return None
    next_url = request.path
    if request.query_string:
        next_url += "?" + request.query_string.decode("utf-8", errors="ignore")
    return redirect(url_for("login", next=next_url))


def allowed_file(filename: str) -> bool:
    if not filename:
        return False
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    return ext in ALLOWED_EXTENSIONS


def _safe_upload_filename(original_filename: str) -> str:
    """Return a safe filename, preserving extension when secure_filename strips the name."""
    cleaned = secure_filename(original_filename)
    if cleaned:
        return cleaned
    ext = os.path.splitext(original_filename)[1].lower()
    if ext in (".xlsx", ".xls", ".xlsm"):
        return f"upload{ext}"
    return "upload.xlsx"


def _session_cache_path(session_id: str) -> str:
    return os.path.join(CACHE_FOLDER, f"{session_id}.json")


def _progress_path(session_id: str) -> str:
    return os.path.join(CACHE_FOLDER, f"{session_id}_progress.json")


def _get_session_data() -> dict:
    """Load session data from JSON cache file (not a database)."""
    session_id = session.get("session_id")
    if not session_id:
        return {}
    cache_path = _session_cache_path(session_id)
    if not os.path.exists(cache_path):
        return {}
    with open(cache_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_session_data(data: dict, session_id: str | None = None) -> str:
    """Save session data to JSON cache file."""
    if not session_id:
        session_id = session.get("session_id")
        if not session_id:
            session_id = str(uuid.uuid4())
            session["session_id"] = session_id
    cache_path = _session_cache_path(session_id)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, default=str)
    return session_id


def _update_progress(session_id: str, **kwargs) -> None:
    path = _progress_path(session_id)
    existing: dict = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = {}
    existing.update(kwargs)
    existing["updated_at"] = datetime.now().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, default=str)


def _get_progress(session_id: str) -> dict:
    path = _progress_path(session_id)
    if not os.path.exists(path):
        return {"status": "idle"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"status": "idle"}


def _get_recent_uploads() -> list[dict[str, Any]]:
    """Return metadata for the last 3 uploads."""
    if not os.path.exists(RECENT_UPLOADS_FILE):
        return []
    try:
        with open(RECENT_UPLOADS_FILE, "r", encoding="utf-8") as f:
            uploads = json.load(f)
            return uploads[:MAX_RECENT_UPLOADS] if isinstance(uploads, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _record_recent_upload(
    session_id: str,
    filename: str,
    total_records: int,
    contest_date: str | None = None,
) -> None:
    """Record an upload to the recent uploads JSON list (capped at MAX_RECENT_UPLOADS = 3)."""
    current = _get_recent_uploads()
    filtered = [u for u in current if u.get("session_id") != session_id]
    entry = {
        "session_id": session_id,
        "filename": filename,
        "total_records": total_records,
        "contest_date": contest_date or "-",
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    filtered.insert(0, entry)
    trimmed = filtered[:MAX_RECENT_UPLOADS]
    try:
        ensure_folders(CACHE_FOLDER)
        with open(RECENT_UPLOADS_FILE, "w", encoding="utf-8") as f:
            json.dump(trimmed, f, default=str)
    except OSError:
        pass


def _students_to_records(df: pd.DataFrame) -> list[dict]:
    return df.to_dict(orient="records")


def _records_to_students(records: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(records)


def _get_students_df() -> pd.DataFrame | None:
    data = _get_session_data()
    records = data.get("students")
    if not records:
        return None
    return ensure_student_columns(_records_to_students(records))


def _get_dept_report_df() -> pd.DataFrame | None:
    data = _get_session_data()
    records = data.get("dept_report")
    if not records:
        return None
    return pd.DataFrame(records)


def _get_missing_dept_df() -> pd.DataFrame | None:
    data = _get_session_data()
    records = data.get("missing_dept")
    if not records:
        return None
    return pd.DataFrame(records)


def _get_grouped_dept_report_df() -> pd.DataFrame | None:
    data = _get_session_data()
    records = data.get("grouped_dept_report")
    if not records:
        return None
    return pd.DataFrame(records)


def _get_contest_dates() -> list[dict]:
    data = _get_session_data()
    students_df = _get_students_df()
    cached = data.get("contest_dates")
    if cached:
        return cached
    dates = get_available_contest_dates(students_df)
    if dates:
        data["contest_dates"] = dates
        _save_session_data(data)
    return dates


EXCEL_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _send_excel_buffer(buffer: BytesIO, download_name: str):
    """Send an in-memory Excel workbook to the browser."""
    payload = buffer.getvalue()
    if not payload:
        raise ValueError("Generated Excel file is empty.")
    out = BytesIO(payload)
    out.seek(0)
    response = send_file(
        out,
        as_attachment=True,
        download_name=download_name,
        mimetype=EXCEL_MIMETYPE,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _persist_bytes_async(buffer: BytesIO, output_path: str) -> None:
    """Write export file in the background so the download response returns faster."""
    payload = buffer.getvalue()

    def _write() -> None:
        ensure_folders(os.path.dirname(output_path) or EXPORT_FOLDER)
        with open(output_path, "wb") as handle:
            handle.write(payload)

    threading.Thread(target=_write, daemon=True).start()


def _defer_session_save(data: dict) -> None:
    """Save session cache outside the request thread without touching Flask session."""
    session_id = session.get("session_id")
    if not session_id:
        return
    threading.Thread(target=_save_session_data, args=(data, session_id), daemon=True).start()


def _send_excel_download(output_path: str, download_name: str):
    """Send an Excel file from disk using an in-memory buffer (avoids file locks)."""
    with open(output_path, "rb") as handle:
        buffer = BytesIO(handle.read())
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=download_name,
        mimetype=EXCEL_MIMETYPE,
    )


def _s_report_download_name(contest_date: str | None) -> str:
    if contest_date:
        return f"S-Report_{contest_date}.xlsx"
    return "S-Report.xlsx"


def _s_report_export_path(contest_date: str | None) -> str:
    if contest_date:
        safe_date = contest_date.replace("-", "")
        return os.path.join(EXPORT_FOLDER, f"S-Report_{safe_date}.xlsx")
    return os.path.join(EXPORT_FOLDER, "S-Report.xlsx")


def _send_s_report_download(
    grouped_dept_df: pd.DataFrame,
    missing_dept_df: pd.DataFrame,
    used_date: str | None,
    contest_title: str,
):
    download_name = _s_report_download_name(used_date)
    buffer = generate_s_report_excel_bytes(
        grouped_dept_df,
        missing_dept_df,
        contest_date=used_date,
        contest_title=contest_title,
    )
    output_path = _s_report_export_path(used_date)
    _persist_bytes_async(buffer, output_path)
    return _send_excel_buffer(buffer, download_name), output_path


def _students_need_leetcode_refetch(students_df: pd.DataFrame) -> bool:
    """True when cached session rows lack extended fields needed for full/topic reports."""
    if students_df is None or students_df.empty:
        return False
    if "fetch_status" not in students_df.columns:
        return False

    success = students_df[students_df["fetch_status"] == "Success"]
    if success.empty:
        return False

    if "solved_easy" not in success.columns:
        return True

    has_solved = success["total_solved"].notna()
    if (has_solved & success["solved_easy"].isna()).any():
        return True

    if "easy_topics" not in success.columns or "solved_languages" not in success.columns:
        return True
    missing_both = success["easy_topics"].isna() & success["solved_languages"].isna()
    return bool(missing_both.any())


def _get_departments() -> list[str]:
    df = _get_students_df()
    if df is None or df.empty:
        return []
    return sorted(d for d in df["Department"].unique() if d and d != MISSING_DEPT_LABEL)


def _normalize_username(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _build_lc_lookup(lc_results: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Case-insensitive lookup map for LeetCode fetch results."""
    lookup: dict[str, dict[str, Any]] = {}
    for key, data in lc_results.items():
        if not key:
            continue
        lookup[key] = data
        lookup[key.lower()] = data
    return lookup


def _apply_leetcode_results(students_df: pd.DataFrame, lc_results: dict) -> pd.DataFrame:
    """Merge LeetCode fetch results into students dataframe efficiently."""
    lc_map = _build_lc_lookup(lc_results)

    students_df = students_df.copy()
    for col in [
        "fetch_status", "total_solved", "solved_easy", "solved_medium", "solved_hard",
        "profile_ranking", "contest_attended", "contest_rating",
        "contest_ranking", "contest_ranking_bucket", "contest_rating_bucket",
        "level", "contest_data_status", "contest_history", "latest_badge", "badge_details",
        "easy_topics", "medium_topics", "hard_topics",
        "easy_topics_text", "medium_topics_text", "hard_topics_text",
        "solved_languages", "solved_languages_text",
    ]:
        if col not in students_df.columns:
            students_df[col] = None

    for idx, row in students_df.iterrows():
        username = _normalize_username(row.get("LeetCode Username"))
        lc = lc_map.get(username) if username else None
        if lc is None and username:
            lc = lc_map.get(username.lower())
        if lc:
            students_df.at[idx, "fetch_status"] = lc.get("fetch_status")
            students_df.at[idx, "total_solved"] = lc.get("total_solved")
            students_df.at[idx, "lifetime_total_solved"] = lc.get("total_solved")
            students_df.at[idx, "solved_easy"] = lc.get("solved_easy")
            students_df.at[idx, "solved_medium"] = lc.get("solved_medium")
            students_df.at[idx, "solved_hard"] = lc.get("solved_hard")
            students_df.at[idx, "profile_ranking"] = lc.get("profile_ranking")

            excel_attended = int(row.get("excel_contest_attended") or 0)
            api_attended = int(lc.get("contest_attended") or 0)
            if api_attended > 0 or excel_attended == 0:
                students_df.at[idx, "contest_attended"] = api_attended
                students_df.at[idx, "contest_rating"] = lc.get("contest_rating")
                students_df.at[idx, "contest_ranking"] = lc.get("contest_ranking")
                students_df.at[idx, "contest_ranking_bucket"] = lc.get("contest_ranking_bucket")
                students_df.at[idx, "contest_rating_bucket"] = lc.get("contest_rating_bucket")

            api_level = lc.get("level", "Unrated")
            current_level = row.get("level")
            if api_level != "Unrated" or not current_level or str(current_level).strip() in ("", "Unrated", "nan"):
                students_df.at[idx, "level"] = api_level

            if lc.get("contest_data_status"):
                students_df.at[idx, "contest_data_status"] = lc["contest_data_status"]
            if lc.get("contest_history") is not None:
                students_df.at[idx, "contest_history"] = lc.get("contest_history")
            students_df.at[idx, "easy_topics"] = lc.get("easy_topics") or []
            students_df.at[idx, "medium_topics"] = lc.get("medium_topics") or []
            students_df.at[idx, "hard_topics"] = lc.get("hard_topics") or []
            students_df.at[idx, "easy_topics_text"] = lc.get("easy_topics_text") or "-"
            students_df.at[idx, "medium_topics_text"] = lc.get("medium_topics_text") or "-"
            students_df.at[idx, "hard_topics_text"] = lc.get("hard_topics_text") or "-"
            students_df.at[idx, "solved_languages"] = lc.get("solved_languages") or []
            students_df.at[idx, "solved_languages_text"] = lc.get("solved_languages_text") or "-"
            students_df.at[idx, "latest_badge"] = lc.get("latest_badge") or "-"
            students_df.at[idx, "badge_details"] = lc.get("badge_details") or "-"
        else:
            if _is_blank(row.get("Leetcode Link")):
                students_df.at[idx, "fetch_status"] = "Missing LeetCode Link"
            elif not row.get("Has Valid Link"):
                students_df.at[idx, "fetch_status"] = "Invalid LeetCode Link"
            else:
                students_df.at[idx, "fetch_status"] = "Not Fetched"
            if students_df.at[idx, "total_solved"] is None:
                students_df.at[idx, "total_solved"] = None
            if int(row.get("excel_contest_attended") or 0) == 0:
                if students_df.at[idx, "contest_attended"] is None:
                    students_df.at[idx, "contest_attended"] = 0
                if students_df.at[idx, "contest_rating"] is None:
                    students_df.at[idx, "contest_rating"] = None
                if students_df.at[idx, "contest_ranking"] is None:
                    students_df.at[idx, "contest_ranking"] = None
            current_level = students_df.at[idx, "level"]
            if current_level is None or (isinstance(current_level, float) and pd.isna(current_level)):
                students_df.at[idx, "level"] = "Unrated"
            if students_df.at[idx, "latest_badge"] is None or (
                isinstance(students_df.at[idx, "latest_badge"], float) and pd.isna(students_df.at[idx, "latest_badge"])
            ):
                students_df.at[idx, "latest_badge"] = "-"
            if students_df.at[idx, "badge_details"] is None or (
                isinstance(students_df.at[idx, "badge_details"], float) and pd.isna(students_df.at[idx, "badge_details"])
            ):
                students_df.at[idx, "badge_details"] = "-"

    return students_df


def _process_uploaded_file(
    filepath: str,
    session_id: str,
    skip_fetch: bool = False,
) -> dict:
    """Full processing pipeline: read, validate, fetch, report."""
    _update_progress(
        session_id,
        status="processing",
        stage="Reading Excel file...",
        percent=5,
        done=0,
        total=0,
    )

    raw_df = read_input_excel(filepath)
    assert_valid_input_columns(raw_df)
    contest_date_from_file = extract_contest_date_from_filename(filepath)
    students_df = prepare_students_dataframe(raw_df, contest_date=contest_date_from_file)
    validation = validate_dataframe(raw_df)

    usernames: list[str] = []
    seen_usernames: set[str] = set()
    for _, row in students_df.iterrows():
        if not row.get("Has Valid Link"):
            continue
        username = _normalize_username(row.get("LeetCode Username"))
        if not username:
            continue
        key = username.lower()
        if key in seen_usernames:
            continue
        seen_usernames.add(key)
        usernames.append(username)
    cache_stats = get_cache_stats(list(usernames))

    _update_progress(
        session_id,
        stage="Validating data...",
        percent=10,
        total_profiles=len(usernames),
        cached_profiles=cache_stats["cached"],
        to_fetch=cache_stats["to_fetch"],
    )

    lc_results: dict = {}
    if not skip_fetch and usernames:
        clear_incomplete_profile_cache()
        clear_past_contests_cache()

        def progress_callback(done, total, username, source):
            pct = 10 + int((done / total) * 75) if total else 85
            _update_progress(
                session_id,
                status="processing",
                stage=f"Fetching LeetCode data ({done}/{total})",
                done=done,
                total=total,
                current_username=username,
                current_source=source,
                percent=pct,
            )

        lc_results = batch_fetch_students(list(usernames), progress_callback=progress_callback)

    _update_progress(session_id, stage="Calculating report...", percent=90)

    students_df = _apply_leetcode_results(students_df, lc_results)
    students_df = ensure_student_columns(students_df)
    dept_report_df, missing_dept_df, quality, _, _ = calculate_department_report(students_df, grouped=False)
    grouped_dept_df, _, grouped_quality, used_date, contest_title = calculate_grouped_department_report(
        students_df, contest_date=contest_date_from_file
    )
    contest_dates_list = get_available_contest_dates(
        students_df,
        force_refresh=not skip_fetch,
        skip_api=skip_fetch,
    )
    if contest_date_from_file:
        known_dates = {item["date"] for item in contest_dates_list}
        if contest_date_from_file not in known_dates:
            contest_dates_list.insert(0, {
                "date": contest_date_from_file,
                "title": f"Weekly Contest ({contest_date_from_file})",
                "label": f"{contest_date_from_file} — Weekly Contest (from upload)",
                "from_api": False,
            })
    dashboard = calculate_dashboard_stats(students_df, grouped_dept_df)
    data_issues = categorize_data_issues(students_df)

    session_data = {
        "upload_file": filepath,
        "upload_time": datetime.now().isoformat(),
        "students": _students_to_records(students_df),
        "dept_report": dept_report_df.to_dict(orient="records"),
        "grouped_dept_report": grouped_dept_df.to_dict(orient="records"),
        "missing_dept": missing_dept_df.to_dict(orient="records"),
        "validation": validation,
        "quality": quality,
        "grouped_quality": grouped_quality,
        "dashboard": dashboard,
        "data_issues": data_issues,
        "contest_dates": contest_dates_list,
        "report_generated": False,
        "report_path": None,
        "full_report_path": None,
        "selected_contest_date": used_date or contest_date_from_file,
        "selected_contest_title": contest_title,
        "fetch_skipped": skip_fetch,
    }
    _save_session_data(session_data, session_id)

    # Track in recent uploads list (max 3)
    _record_recent_upload(
        session_id=session_id,
        filename=os.path.basename(filepath),
        total_records=len(students_df),
        contest_date=used_date or contest_date_from_file,
    )

    _update_progress(
        session_id,
        status="complete",
        stage="Done!",
        percent=100,
        done=len(usernames),
        total=len(usernames),
    )

    return session_data


def _run_processing_background(filepath: str, session_id: str, skip_fetch: bool = False) -> None:
    """Run processing in background thread."""
    try:
        clear_expired_cache()
        _process_uploaded_file(filepath, session_id, skip_fetch=skip_fetch)
    except Exception as exc:
        _update_progress(
            session_id,
            status="error",
            stage="Processing failed",
            message=str(exc),
            percent=0,
        )


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("authenticated"):
        return redirect(url_for("dashboard"))

    next_url = request.args.get("next") or request.form.get("next") or url_for("dashboard")
    banner_url = url_for("login_bg")

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if username == AUTH_USERNAME and password == AUTH_PASSWORD:
            session["authenticated"] = True
            session["auth_user"] = username
            flash("Login successful. Welcome to NCT Training and Placement Cell.", "success")
            if not next_url.startswith("/"):
                next_url = url_for("dashboard")
            return redirect(next_url)
        flash("Invalid user name or password.", "danger")

    return render_template("login.html", next_url=next_url, banner_url=banner_url)


@app.route("/login-bg")
def login_bg():
    """Serve Nandha banner for login page background."""
    banner_path = _resolve_login_banner_path()
    if not banner_path:
        return "", 404
    return send_file(banner_path, mimetype="image/png")


@app.route("/logout")
def logout():
    session.pop("authenticated", None)
    session.pop("auth_user", None)
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/")
def dashboard():
    data = _get_session_data()
    students_df = _get_students_df()
    has_data = students_df is not None and not students_df.empty
    dashboard_stats = calculate_dashboard_stats(students_df) if has_data else data.get("dashboard", {})
    return render_template(
        "dashboard.html",
        stats=dashboard_stats,
        has_data=has_data,
        departments=_get_departments(),
        fetch_skipped=data.get("fetch_skipped", False),
        needs_leetcode_refetch=_students_need_leetcode_refetch(students_df) if has_data else False,
    )


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        if "file" not in request.files:
            flash("No file selected.", "danger")
            return redirect(url_for("upload"))

        file = request.files["file"]
        if file.filename == "":
            flash("No file selected.", "danger")
            return redirect(url_for("upload"))

        if not allowed_file(file.filename):
            flash("Invalid file type. Please upload .xlsx or .xls file.", "danger")
            return redirect(url_for("upload"))

        filename = _safe_upload_filename(file.filename)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_name = f"{timestamp}_{filename}"
        ensure_folders(app.config["UPLOAD_FOLDER"])
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], saved_name)
        try:
            file.save(filepath)
        except OSError as exc:
            flash(f"Could not save uploaded file: {exc}", "danger")
            return redirect(url_for("upload"))

        if os.path.getsize(filepath) <= 0:
            flash("Uploaded file is empty. Please choose a valid Excel file.", "danger")
            try:
                os.remove(filepath)
            except OSError:
                pass
            return redirect(url_for("upload"))

        skip_fetch = request.form.get("skip_fetch") == "on"
        session_id = session.get("session_id") or str(uuid.uuid4())
        session["session_id"] = session_id

        _update_progress(
            session_id,
            status="starting",
            stage="Upload complete, starting processing...",
            percent=0,
            done=0,
            total=0,
        )

        thread = threading.Thread(
            target=_run_processing_background,
            args=(filepath, session_id, skip_fetch),
            daemon=True,
        )
        thread.start()

        return redirect(url_for("processing"))

    return render_template("upload.html", recent_uploads=_get_recent_uploads())


@app.route("/load-upload/<target_session_id>")
def load_upload(target_session_id: str):
    """Switch active session to a previously uploaded dataset from the recent list."""
    cache_path = _session_cache_path(target_session_id)
    if not os.path.exists(cache_path):
        flash("Upload session data not found or expired.", "warning")
        return redirect(url_for("upload"))
    session["session_id"] = target_session_id
    flash("Switched to selected dataset.", "success")
    return redirect(url_for("dashboard"))


@app.route("/processing")
def processing():
    session_id = session.get("session_id")
    if not session_id:
        return redirect(url_for("upload"))
    progress = _get_progress(session_id)
    return render_template("processing.html", progress=progress)


@app.route("/api/process-status")
def api_process_status():
    session_id = session.get("session_id")
    if not session_id:
        return jsonify({"status": "idle"})
    return jsonify(_get_progress(session_id))


@app.route("/students")
def students():
    students_df = _get_students_df()
    if students_df is None:
        flash("Please upload an Excel file first.", "warning")
        return redirect(url_for("upload"))

    session_data = _get_session_data()
    contest_date = (
        request.args.get("contest_date", "").strip()
        or session_data.get("selected_contest_date")
        or None
    )

    raw_dept = unquote_plus(request.args.get("department", "") or "").strip()
    if raw_dept == "AI":
        qs = request.query_string.decode("utf-8", errors="ignore")
        if "AI%26DS" in qs or "AI&DS" in qs:
            raw_dept = "AI&DS"
    dept_filter = normalize_department_filter(raw_dept)
    contest_filter = request.args.get("contest", "").strip()
    search_query = request.args.get("search", "").strip()
    sort_by = request.args.get("sort_by", "").strip()
    sort_dir = request.args.get("sort_dir", "asc").strip().lower()
    if sort_dir not in ("asc", "desc"):
        sort_dir = "asc"

    students_df, used_date, contest_title = prepare_student_details_dataframe(
        students_df, contest_date=contest_date
    )

    student_filters = {
        "department": dept_filter,
        "contest": contest_filter,
        "search": search_query,
    }

    filter_counts = compute_student_filter_counts(students_df, student_filters)
    filtered = filter_students(students_df, student_filters)
    filtered = sort_students(filtered, sort_by or None, sort_dir)

    display_cols = [
        "S.No", "Register No", "Name", "Department", "LeetCode Username",
        "total_solved", "solved_easy", "solved_medium", "solved_hard",
        "contest_attended",
        "profile_ranking", "badge_details", "latest_badge", "level",
    ]
    available = [c for c in display_cols if c in filtered.columns]
    records = [format_student_for_display(r) for r in filtered[available].to_dict(orient="records")]

    return render_template(
        "students.html",
        students=records,
        report_departments=REPORT_DEPARTMENT_ORDER,
        sort_columns=get_student_sort_columns(),
        sort_by=sort_by,
        sort_dir=sort_dir,
        filters=student_filters,
        filter_counts=filter_counts,
        result_count=len(records),
        selected_contest_date=used_date or "",
        selected_contest_title=contest_title or "",
    )


@app.route("/department-report")
def department_report():
    students_df = _get_students_df()
    if students_df is None:
        flash("Please upload an Excel file first.", "warning")
        return redirect(url_for("upload"))

    dept_filter = request.args.get("department", "")
    global_rank_filter = (request.args.get("global_rank") or request.args.get("global_ranking") or "").strip()
    contest_date = parse_contest_date_param(
        request.args.get("contest_date", ""),
        request.args.get("contest_date_custom", ""),
    )

    grouped_dept_df, _, _, used_date, contest_title = calculate_grouped_department_report(
        students_df,
        contest_date=contest_date,
        global_rank_filter=global_rank_filter,
    )

    filtered = filter_department_report(grouped_dept_df, dept_filter)
    contest_dates = _get_contest_dates()

    return render_template(
        "department_report.html",
        report=filtered.to_dict(orient="records"),
        report_columns=get_department_report_display_columns(),
        departments=REPORT_DEPARTMENT_ORDER,
        global_ranking_options=GLOBAL_RANKING_FILTER_OPTIONS,
        selected_dept=dept_filter,
        selected_global_rank=global_rank_filter,
        contest_dates=contest_dates,
        selected_contest_date=used_date or "",
        selected_contest_title=contest_title,
    )


@app.route("/export-department-report")
def export_department_report():
    students_df = _get_students_df()
    if students_df is None:
        flash("Please upload an Excel file first.", "warning")
        return redirect(url_for("upload"))

    dept_filter = request.args.get("department", "")
    global_rank_filter = (request.args.get("global_rank") or request.args.get("global_ranking") or "").strip()
    contest_date = parse_contest_date_param(
        request.args.get("contest_date", ""),
        request.args.get("contest_date_custom", ""),
    )
    grouped_dept_df, missing_dept_df, _, used_date, contest_title = calculate_grouped_department_report(
        students_df,
        contest_date=contest_date,
        global_rank_filter=global_rank_filter,
    )
    if dept_filter and dept_filter.lower() not in ("all", "all departments", ""):
        grouped_dept_df = filter_department_report(grouped_dept_df, dept_filter)

    try:
        response, _ = _send_s_report_download(
            grouped_dept_df,
            missing_dept_df,
            used_date,
            contest_title,
        )
        return response
    except Exception as exc:
        flash(f"Error exporting S-Report: {exc}", "danger")
        return redirect(url_for("department_report"))


def _weekly_contest_filters_from_request() -> dict[str, str]:
    """Read weekly contest table filter query params."""
    return {
        "department": normalize_department_filter(request.args.get("department", "")),
        "s_no": (request.args.get("s_no", "") or "").strip(),
        "register_no": (request.args.get("register_no", "") or "").strip(),
        "name": (request.args.get("name", "") or "").strip(),
        "search": (request.args.get("search", "") or request.args.get("student_search", "") or "").strip(),
        "link_status": (request.args.get("link_status", "") or "").strip(),
        "contest": (request.args.get("contest", "") or "").strip().lower(),
        "problems_solved": (request.args.get("problems_solved", "") or "").strip(),
        "contest_rating": (request.args.get("contest_rating", "") or "").strip(),
        "contest_rank": (request.args.get("contest_rank", "") or "").strip(),
        "global_rank": (request.args.get("global_rank", "") or request.args.get("global_ranking", "") or request.args.get("overall_global_rank") or "").strip(),
    }


@app.route("/weekly-contest-details")
def weekly_contest_details():
    students_df = _get_students_df()
    if students_df is None:
        flash("Please upload an Excel file first.", "warning")
        return redirect(url_for("upload"))

    contest_date = parse_contest_date_param(
        request.args.get("contest_date", ""),
        request.args.get("contest_date_custom", ""),
    )
    table_filters = _weekly_contest_filters_from_request()
    sort_by = (request.args.get("sort_by", "") or "").strip()
    sort_dir = (request.args.get("sort_dir", "asc") or "asc").strip().lower()

    records, used_date, contest_title, filter_counts = get_weekly_contest_details(
        students_df,
        contest_date=contest_date,
        contest_filter=table_filters["contest"],
        department_filter=table_filters["department"],
        s_no_filter=table_filters["s_no"],
        register_filter=table_filters["register_no"],
        name_filter=table_filters["name"],
        search_filter=table_filters["search"],
        link_status_filter=table_filters["link_status"],
        problems_filter=table_filters["problems_solved"],
        rating_filter=table_filters["contest_rating"],
        rank_filter=table_filters["contest_rank"],
        global_rank_filter=table_filters["global_rank"],
        sort_by=sort_by or None,
        sort_dir=sort_dir,
    )
    contest_dates = _get_contest_dates()

    ranking_summary = calculate_overall_ranking_summary(
        students_df,
        department_filter=table_filters["department"],
    )
    dept_comparison = calculate_department_wise_global_ranking_comparison(students_df)

    return render_template(
        "weekly_contest_details.html",
        students=records,
        departments=["CSE", "EEE", "ECE", "IT", "AI&DS"],
        contest_dates=contest_dates,
        selected_contest_date=used_date or "",
        selected_contest_title=contest_title or "",
        filters=table_filters,
        filter_counts=filter_counts,
        filter_options=get_weekly_contest_filter_options(),
        ranking_summary=ranking_summary,
        dept_comparison=dept_comparison,
        result_count=len(records),
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@app.route("/export-weekly-contest-details")
def export_weekly_contest_details():
    students_df = _get_students_df()
    if students_df is None:
        flash("Please upload an Excel file first.", "warning")
        return redirect(url_for("upload"))

    contest_date = parse_contest_date_param(
        request.args.get("contest_date", ""),
        request.args.get("contest_date_custom", ""),
    )
    table_filters = _weekly_contest_filters_from_request()
    sort_by = (request.args.get("sort_by", "") or "").strip()
    sort_dir = (request.args.get("sort_dir", "asc") or "asc").strip().lower()

    try:
        buffer = generate_weekly_contest_details_excel(
            students_df,
            contest_date=contest_date,
            contest_filter=table_filters["contest"],
            department_filter=table_filters["department"],
            s_no_filter=table_filters["s_no"],
            register_filter=table_filters["register_no"],
            name_filter=table_filters["name"],
            search_filter=table_filters["search"],
            link_status_filter=table_filters["link_status"],
            problems_filter=table_filters["problems_solved"],
            rating_filter=table_filters["contest_rating"],
            rank_filter=table_filters["contest_rank"],
            global_rank_filter=table_filters["global_rank"],
            sort_by=sort_by or None,
            sort_dir=sort_dir,
            output_path=BytesIO(),
        )
        _, used_date, _, _ = get_weekly_contest_details(
            students_df,
            contest_date=contest_date,
            contest_filter=table_filters["contest"],
            department_filter=table_filters["department"],
            s_no_filter=table_filters["s_no"],
            register_filter=table_filters["register_no"],
            name_filter=table_filters["name"],
            search_filter=table_filters["search"],
            link_status_filter=table_filters["link_status"],
            problems_filter=table_filters["problems_solved"],
            rating_filter=table_filters["contest_rating"],
            rank_filter=table_filters["contest_rank"],
            global_rank_filter=table_filters["global_rank"],
            sort_by=sort_by or None,
            sort_dir=sort_dir,
        )
        download_name = (
            f"Weekly-Contest-Details_{used_date}.xlsx" if used_date else "Weekly-Contest-Details.xlsx"
        )
        return _send_excel_buffer(buffer, download_name)
    except Exception as exc:
        flash(f"Error exporting weekly contest details: {exc}", "danger")
        return redirect(url_for("weekly_contest_details"))


@app.route("/missing-data")
def missing_data():
    data = _get_session_data()
    if not data.get("students"):
        flash("Please upload an Excel file first.", "warning")
        return redirect(url_for("upload"))

    students_df = _get_students_df()
    data_issues = categorize_data_issues(students_df) if students_df is not None else data.get("data_issues", {})

    return render_template(
        "missing_data.html",
        missing_dept=data.get("missing_dept", []),
        link_not_working_by_dept=data_issues.get("link_not_working_by_dept", []),
        validation=data.get("validation", {}),
    )


@app.route("/download/missing-data")
def download_missing_data():
    """Download comprehensive Missing & Invalid Data Excel report."""
    students_df = _get_students_df()
    if students_df is None or students_df.empty:
        flash("Please upload an Excel file first.", "warning")
        return redirect(url_for("upload"))

    try:
        buffer = generate_missing_data_issues_excel_bytes(students_df)
        download_name = "Missing-and-Invalid-Data-Report.xlsx"
        return _send_excel_buffer(buffer, download_name)
    except Exception as exc:
        flash(f"Error generating Missing Data Report: {exc}", "danger")
        return redirect(url_for("missing_data"))


@app.route("/report", methods=["GET", "POST"])
def report():
    data = _get_session_data()
    students_df = _get_students_df()
    if students_df is None:
        flash("Please upload an Excel file first.", "warning")
        return redirect(url_for("upload"))

    quality = data.get("grouped_quality") or data.get("quality", {})
    validation = data.get("validation", {})
    report_generated = data.get("report_generated", False)
    report_path = data.get("report_path")
    contest_dates = _get_contest_dates()
    needs_refetch = _students_need_leetcode_refetch(students_df)
    selected_contest_date = data.get("selected_contest_date", "")
    selected_contest_title = data.get("selected_contest_title", "")
    full_report_path = data.get("full_report_path")
    overall_dept_report_path = data.get("overall_dept_report_path")
    solved_problems_report_path = data.get("solved_problems_report_path")

    if request.method == "POST":
        action = request.form.get("action", "generate")

        if action == "generate_solved_problems":
            if _students_need_leetcode_refetch(students_df):
                flash(
                    "Topic breakdown requires a fresh LeetCode fetch. "
                    "Use Re-fetch LeetCode Data on the Dashboard, then generate again.",
                    "warning",
                )
                return redirect(url_for("report"))
            try:
                buffer = BytesIO()
                generate_solved_problems_excel(students_df, output_path=buffer)
                output_path = os.path.join(EXPORT_FOLDER, "Solved-Problems-Report.xlsx")
                data["solved_problems_report_path"] = output_path
                _persist_bytes_async(buffer, output_path)
                _defer_session_save(data)
                return _send_excel_buffer(buffer, "Solved-Problems-Report.xlsx")
            except Exception as exc:
                flash(f"Error generating solved problems report: {exc}", "danger")
                return redirect(url_for("report"))

        if action == "generate_full":
            if _students_need_leetcode_refetch(students_df):
                flash(
                    "Easy/Medium/Hard and contest fields are missing. "
                    "Use Re-fetch LeetCode Data on the Dashboard, then generate again.",
                    "warning",
                )
                return redirect(url_for("report"))
            try:
                buffer = BytesIO()
                generate_full_leetcode_excel(students_df, output_path=buffer)
                output_path = os.path.join(EXPORT_FOLDER, "LeetCode-Full-Report.xlsx")
                data["full_report_path"] = output_path
                _persist_bytes_async(buffer, output_path)
                _defer_session_save(data)
                return _send_excel_buffer(buffer, "LeetCode-Full-Report.xlsx")
            except Exception as exc:
                flash(f"Error generating full report: {exc}", "danger")
                return redirect(url_for("report"))

        if action == "generate_overall_dept":
            try:
                buffer = BytesIO()
                generate_overall_department_excel(students_df, output_path=buffer)
                output_path = os.path.join(EXPORT_FOLDER, "Overall-Department-Report.xlsx")
                data["overall_dept_report_path"] = output_path
                _persist_bytes_async(buffer, output_path)
                _defer_session_save(data)
                return _send_excel_buffer(buffer, "Overall-Department-Report.xlsx")
            except Exception as exc:
                flash(f"Error generating overall department report: {exc}", "danger")
                return redirect(url_for("report"))

        if action == "generate_weekly_contest":
            contest_date = parse_contest_date_param(
                request.form.get("contest_date", ""),
                request.form.get("contest_date_custom", ""),
            )
            department = request.form.get("department", "").strip()
            try:
                buffer = generate_weekly_contest_details_excel(
                    students_df,
                    contest_date=contest_date,
                    department_filter=department,
                    output_path=BytesIO(),
                )
                _, used_date, _, _ = get_weekly_contest_details(
                    students_df,
                    contest_date=contest_date,
                    department_filter=department,
                )
                download_name = (
                    f"Weekly-Contest-Details_{used_date}.xlsx" if used_date else "Weekly-Contest-Details.xlsx"
                )
                output_path = os.path.join(EXPORT_FOLDER, download_name)
                data["weekly_contest_details_path"] = output_path
                _persist_bytes_async(buffer, output_path)
                _defer_session_save(data)
                return _send_excel_buffer(buffer, download_name)
            except Exception as exc:
                flash(f"Error generating weekly contest details report: {exc}", "danger")
                return redirect(url_for("report"))

        if action == "generate_student_contest":
            if _students_need_leetcode_refetch(students_df):
                flash(
                    "Easy/Medium/Hard and profile fields are missing. "
                    "Use Re-fetch LeetCode Data on the Dashboard, then generate again.",
                    "warning",
                )
                return redirect(url_for("report"))
            contest_date = parse_contest_date_param(
                request.form.get("contest_date", ""),
                request.form.get("contest_date_custom", ""),
            )
            department = request.form.get("department", "").strip()
            try:
                buffer = BytesIO()
                generate_student_contest_report_excel(
                    students_df,
                    contest_date=contest_date,
                    output_path=buffer,
                    department=department,
                )
                _, used_date, _ = get_student_contest_report_records(
                    students_df,
                    contest_date=contest_date,
                    department=department,
                )
                download_name = (
                    f"Student-Contest-Report_{used_date}.xlsx"
                    if used_date
                    else "Student-Contest-Report.xlsx"
                )
                output_path = os.path.join(EXPORT_FOLDER, download_name)
                data["student_contest_report_path"] = output_path
                _persist_bytes_async(buffer, output_path)
                _defer_session_save(data)
                return _send_excel_buffer(buffer, download_name)
            except Exception as exc:
                flash(f"Error generating student contest report: {exc}", "danger")
                return redirect(url_for("report"))

        contest_date = parse_contest_date_param(
            request.form.get("contest_date", ""),
            request.form.get("contest_date_custom", ""),
        )
        grouped_dept_df, missing_dept_df, quality, used_date, contest_title = calculate_grouped_department_report(
            students_df, contest_date=contest_date
        )

        if quality and not quality.get("passed", True):
            flash("Quality check failed. Review issues before generating.", "warning")

        if not grouped_dept_df.empty and "Total" in grouped_dept_df["Dept"].values:
            total_attended = int(
                grouped_dept_df[grouped_dept_df["Dept"] == "Total"]["Total attended"].iloc[0] or 0
            )
            if total_attended == 0:
                flash(
                    f"No weekly contest attendance found for {used_date or 'the selected date'}. "
                    "Re-fetch LeetCode data or choose another contest date.",
                    "warning",
                )

        try:
            response, output_path = _send_s_report_download(
                grouped_dept_df,
                missing_dept_df,
                used_date,
                contest_title,
            )
            data["report_generated"] = True
            data["report_path"] = output_path
            data["grouped_quality"] = quality
            data["selected_contest_date"] = used_date
            data["selected_contest_title"] = contest_title
            _defer_session_save(data)
            return response
        except Exception as exc:
            flash(f"Error generating report: {exc}", "danger")
            return redirect(url_for("report"))

    return render_template(
        "report.html",
        quality=quality,
        validation=validation,
        report_generated=report_generated,
        report_path=report_path,
        contest_dates=contest_dates,
        selected_contest_date=selected_contest_date,
        selected_contest_title=selected_contest_title,
        full_report_path=full_report_path,
        overall_dept_report_path=overall_dept_report_path,
        solved_problems_report_path=solved_problems_report_path,
        report_departments=REPORT_DEPARTMENT_ORDER,
        departments=["CSE", "EEE", "ECE", "IT", "AI&DS"],
        needs_leetcode_refetch=needs_refetch,
    )


@app.route("/refetch-leetcode", methods=["POST"])
def refetch_leetcode():
    """Re-fetch LeetCode data for current session (clears stale cache first)."""
    data = _get_session_data()
    filepath = data.get("upload_file")
    session_id = session.get("session_id")

    if not filepath or not session_id or not os.path.exists(filepath):
        flash("No uploaded file found. Please upload again.", "warning")
        return redirect(url_for("upload"))

    clear_all_profile_cache()
    clear_past_contests_cache()
    data["contest_dates"] = []
    _save_session_data(data)

    _update_progress(session_id, status="starting", stage="Re-fetching LeetCode data...", percent=0)
    thread = threading.Thread(
        target=_run_processing_background,
        args=(filepath, session_id, False),
        daemon=True,
    )
    thread.start()
    return redirect(url_for("processing"))


@app.route("/download-report")
def download_report():
    data = _get_session_data()
    students_df = _get_students_df()
    if students_df is None:
        flash("Please upload an Excel file first.", "warning")
        return redirect(url_for("upload"))

    contest_date = data.get("selected_contest_date") or None
    cached_path = data.get("report_path")
    if cached_path and os.path.isfile(cached_path):
        return _send_excel_download(cached_path, _s_report_download_name(contest_date))

    grouped_dept_df, missing_dept_df, _, used_date, contest_title = calculate_grouped_department_report(
        students_df, contest_date=contest_date
    )
    try:
        response, output_path = _send_s_report_download(
            grouped_dept_df,
            missing_dept_df,
            used_date,
            contest_title,
        )
        data["report_path"] = output_path
        data["report_generated"] = True
        data["selected_contest_date"] = used_date
        data["selected_contest_title"] = contest_title
        _save_session_data(data)
        return response
    except Exception as exc:
        flash(f"Error generating report: {exc}", "danger")
        return redirect(url_for("report"))


@app.route("/api/dashboard-stats")
def api_dashboard_stats():
    students_df = _get_students_df()
    if students_df is not None and not students_df.empty:
        return jsonify(calculate_dashboard_stats(students_df))
    data = _get_session_data()
    return jsonify(data.get("dashboard", {}))


@app.route("/api/department-report")
def api_department_report():
    students_df = _get_students_df()
    if students_df is None:
        return jsonify([])

    session_data = _get_session_data()
    contest_date = session_data.get("selected_contest_date") or None
    grouped_dept_df, _, _, _, _ = calculate_grouped_department_report(
        students_df, contest_date=contest_date
    )
    dept_filter = request.args.get("department", "")
    filtered = filter_department_report(grouped_dept_df, dept_filter)
    return jsonify(filtered.to_dict(orient="records"))


@app.route("/sample-input")
def sample_input():
    try:
        buffer = create_sample_input_excel_bytes()
    except Exception as exc:
        flash(f"Could not create sample input file: {exc}", "danger")
        return redirect(url_for("upload"))
    return send_file(
        buffer,
        as_attachment=True,
        download_name="sample_input.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("true", "1", "t")
    app.run(debug=debug, host="0.0.0.0", port=port)
