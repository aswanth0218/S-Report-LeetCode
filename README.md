# S-REPORT

**LeetCode Student Performance Report System**

Excel-based web application for generating department-wise LeetCode performance reports. **No database** — all data is loaded from uploaded Excel files and processed in memory using Pandas.

## Features

- Upload Excel with student details (S.No, Register No, Name, DEPT, Leetcode Link)
- Automatic LeetCode link cleaning (including markdown format)
- LeetCode profile & contest data fetching via GraphQL API
- Department-wise performance report with configurable problem-solved thresholds
- Missing department students tracked separately (never counted as a department)
- Validation summary with duplicate/missing data detection
- Web dashboard with Chart.js visualizations
- Generate formatted **S-Report.xlsx** with merged grouped headers (OpenPyXL)
- Filter by department, name, register number

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, Flask, Pandas |
| Excel I/O | OpenPyXL |
| HTTP | Requests |
| Frontend | HTML5, Bootstrap 5, Chart.js |

## Project Structure

```
S-Report/
├── app.py                      # Flask application
├── config.py                   # Configurable thresholds & settings
├── requirements.txt
├── services/
│   ├── leetcode_service.py     # LeetCode API & classification
│   ├── report_service.py       # Report calculations
│   ├── excel_service.py        # Excel read/write & formatting
│   └── validation_service.py   # Data validation
├── templates/                  # HTML templates
├── static/css/style.css
├── static/js/dashboard.js
├── uploads/                    # Uploaded Excel files
├── exports/                    # Generated S-Report.xlsx
└── cache/                      # Session JSON cache (not a database)
```

## Setup Instructions

### Prerequisites

- Python 3.10 or higher
- pip

### Installation

```bash
# Navigate to project directory
cd S-Report

# Create virtual environment (recommended)
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Run Instructions

```bash
# Start the Flask development server
python app.py
```

Open your browser and navigate to: **http://localhost:5000**

## Usage Workflow

1. **Upload Excel** — Go to *Upload Excel* page and upload your student file
2. **Review Dashboard** — View summary statistics and charts
3. **Student Details** — Browse individual student LeetCode data with filters
4. **Department Report** — View department-wise breakdown table
5. **Missing Data** — Check validation issues and missing department students
6. **Generate Report** — Click *Generate S-Report* then *Download Report*

## Input Excel Format

| S.No | Register No | Name | DEPT | Leetcode Link |
|------|-------------|------|------|---------------|
| 1 | 732124205001 | AAKIL SHIHAB S | IT | https://leetcode.com/u/Aakil-shihab14/ |

Download a sample input file from the Upload page.

## Performance

Upload speed has been optimized:

| Feature | Benefit |
|---------|---------|
| **Parallel fetching** | Up to 10 LeetCode profiles fetched simultaneously |
| **JSON cache** | Re-uploads load cached profiles instantly (24h TTL) |
| **Background processing** | Upload returns immediately; progress page shows live status |
| **Quick upload** | Skip LeetCode fetch for instant upload, fetch later from Dashboard |

Tune settings in `config.py`:

```python
FETCH_MAX_WORKERS = 10       # Increase to 15 for faster (may hit rate limits)
USE_LEETCODE_CACHE = True    # Reuse cached profiles
LEETCODE_CACHE_TTL_HOURS = 24
```

## Configuration

Edit `config.py` to customize:

- **Problem solved thresholds** (`PROBLEM_SOLVED_CONFIG`)
- **Contest ranking buckets** (`CONTEST_RANKING_BUCKETS`)
- **Contest rating buckets** (`CONTEST_RATING_BUCKETS`)
- **Include missing dept in Total row** (`INCLUDE_MISSING_DEPT_IN_TOTAL`)

## Important Rules

- **No database** — SQLite, MySQL, MongoDB, ORM are NOT used
- **Blank departments** are shown under "Missing Department", never as a department row
- **Total Strength** counts all students in a department regardless of LeetCode data availability
- **Total attended** counts only students with at least one contest participation
- Students without contest ranking/rating are excluded from those category columns
- Original uploaded Excel is never modified

## Output

Generated file: `exports/S-Report.xlsx`

- Sheet **S-Report**: Department-wise report with grouped headers and Total row
- Sheet **Missing Department Students**: Students with blank department

## License

MIT
