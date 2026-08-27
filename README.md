# HRIS Import Preview

A Django web application that accepts an HRIS CSV export, validates it,
analyses the reporting hierarchy, and presents a clear import preview —
**before any data is written to a database**.

Built as the **Diversio Engineer I Exercise** submission.

---

## Quick Start (TL;DR)

```bash
git clone <repo-url> && cd diverso
python -m venv venv && venv\Scripts\activate   # Windows
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
# open http://127.0.0.1:8000/
```

---

## Setup & Run Instructions

### Prerequisites

| Requirement | Minimum version |
|-------------|----------------|
| Python      | 3.10           |
| pip         | any recent     |

### Step 1 — Get the code

```bash
# From a ZIP archive:
unzip diverso.zip -d diverso
cd diverso

# Or clone the repository:
git clone <repo-url>
cd diverso
```

### Step 2 — Create and activate a virtual environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python -m venv venv
source venv/bin/activate
```

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

Only Django is required. No other third-party packages.

### Step 4 — Apply migrations

Django needs to set up its internal tables (sessions, admin). No application
models are defined, so this only creates Django's own tables:

```bash
python manage.py migrate
```

### Step 5 — Run the development server

```bash
python manage.py runserver
```

Open **http://127.0.0.1:8000/** in your browser.

The app has two pages:

| URL         | Purpose                             |
|-------------|-------------------------------------|
| `/`         | File upload form                    |
| `/preview/` | Import preview results (POST only)  |

---

## Test Instructions

Run all tests with:

```bash
python manage.py test hris
```

Run with verbose output to see each test name:

```bash
python manage.py test hris --verbosity=2
```

**Expected output:**

```
Found 38 test(s).
......................................
----------------------------------------------------------------------
Ran 38 tests in 0.XXXs

OK
```

### Test structure

Tests live in [`hris/tests.py`](hris/tests.py) and are organised into three classes:

| Class           | What it covers                                                                                                             | Tests |
|-----------------|----------------------------------------------------------------------------------------------------------------------------|-------|
| `ParserTests`   | Valid rows, source row numbers (header=row 1, BOM-safe), normalisation, required fields, duplicate ID/email, quoted values  | 17    |
| `AnalyserTests` | Manager resolution (by id / email / both / conflict / self-manage / not-found), roots, cycle exact membership, reporter-into-cycle excluded, empty input | 16    |
| `ViewTests`     | GET upload form, missing file, wrong extension, valid round-trip, malformed header                                          | 5     |

All tests call the library functions directly — **no browser driver, no running server** required.

---

## Project Layout

```
diverso/
├── manage.py
├── requirements.txt          ← Django only
├── README.md
├── sample_hris.csv           ← the supplied sample file
│
├── diverso_project/          ← Django project package
│   ├── settings.py           ← hris app registered, templates dir set
│   ├── urls.py               ← includes hris.urls at root
│   └── wsgi.py
│
├── hris/                     ← Django application
│   ├── parser.py             ← CSV ingestion + row-level validation (pure Python)
│   ├── analyser.py           ← Hierarchy graph + cycle detection (pure Python)
│   ├── views.py              ← Thin Django views; calls parser → analyser → template
│   ├── urls.py               ← Two routes: GET / and POST /preview/
│   └── tests.py              ← 29 automated tests
│
└── templates/
    ├── upload.html           ← Upload page (drag-and-drop, error banner)
    └── preview.html          ← Preview results (6 collapsible sections)
```

---

## How It Works

### 1. Parsing — `hris/parser.py`

`parse_csv(file_obj) → ParseResult`

1. Read raw bytes; strip UTF-8 BOM (`\xef\xbb\xbf`) if present.
2. Decode as UTF-8 — raises `ValueError` with a clear message on failure.
3. Parse with `csv.DictReader` — handles quoted values (e.g. `"Alvarez, Renée"`).
4. Validate headers — raise `ValueError` listing any missing required columns.
5. **First pass** — normalise every row:
   - Strip surrounding whitespace from all values.
   - Lowercase `email` and `manager_email`.
   - Preserve `employee_id` case.
6. **Duplicate detection** — scan all rows and mark every row sharing a
   duplicated `employee_id` or `email` as invalid (both are flagged, not just
   the second occurrence).
7. **Second pass** — separate valid rows from invalid rows; attach source row
   numbers (1-based; header = row 1).
8. Return `ParseResult(total_source_rows, valid_rows, invalid_rows)`.

### 2. Analysis — `hris/analyser.py`

`analyse(valid_rows) → AnalysisResult`

1. Build two lookup dicts: `employee_id → row` and `email → row`.
2. Resolve each employee's manager using `_resolve_manager()`:
   - Both blank → **root**.
   - `manager_id` only → dict lookup by ID.
   - `manager_email` only → dict lookup by lowercased email.
   - Both supplied → both must resolve to the same employee; otherwise error.
   - Self-management → error.
   - Not found → error.
3. Employees with a manager error remain **accepted** but have no reporting
   relationship and are not roots.
4. **Cycle detection** — `_find_cycle_members()` runs an iterative three-colour
   DFS (WHITE → GRAY → BLACK) on the resolved-edge graph:
   - Assign every node WHITE (unvisited).
   - DFS from each WHITE node; colour it GRAY (on stack) on entry.
   - If we reach a GRAY node, everything on the stack between that node and the
     current position is in a cycle — mark all as cycle members.
   - Colour nodes BLACK (done) on exit.
   - **Complexity**: O(N + E) time, O(N) space; E ≤ N since each employee has
     at most one manager edge.
5. **Cycle participants** — separately walk each non-cycle employee's ancestor
   chain (memoised) to find those who report *into* a cycle. These appear in
   `reporting_cycle_participants` but not in `cycle_members`.

### 3. Views — `hris/views.py`

Deliberately thin:

| Step | Action |
|------|--------|
| Validate | Check file present, `.csv` extension, ≤ 10 MB |
| Parse | Call `parse_csv(uploaded_file)` |
| Analyse | Call `analyse(parse_result.valid_rows)` |
| Render | Pass context dict to `preview.html` |
| Errors | Any exception → re-render upload form with a readable message |

---

## What the Preview Shows

| Section | Description |
|---------|-------------|
| Summary cards | Total rows · Accepted · Invalid rows · Manager errors · Roots · Cycle members |
| Row-level validation errors | Source row number, employee ID, and per-row error messages |
| Root employees | Employees with no manager and no manager error |
| Manager resolution errors | Per-employee messages (not found / conflict / self-manage) |
| Managers & direct reports | All managers with direct-report count and names |
| Reporting cycle members | Employees *inside* a cycle (reporters-into-cycles excluded per spec) |

All sections are collapsible. Sections with no items default to collapsed.

---

## Sample File Analysis

The supplied `sample_hris.csv` (25 rows, the exact file from the assignment) produces:

| Stat | Value |
|------|-------|
| Total source rows | 25 |
| Accepted employees | 25 |
| Invalid rows | 0 |
| Manager errors | 2 |
| Root employees | 1 (DIV-1001 Avery Morgan) |
| Cycle members | 3 (DIV-1701, DIV-1702, DIV-1703) |

**Manager errors** in the sample:

| Employee | Error |
|----------|-------|
| DIV-1600 Casey Bell | Manager ID `DIV-9999` not found |
| DIV-1601 Riley Cooper | `manager_id` DIV-1100 (Priya Shah) conflicts with `manager_email` demo.mateo.rivera@diversio.com (Mateo Rivera) |

**Reporting cycle** in the sample:

| Employee | Chain |
|----------|-------|
| DIV-1701 Morgan Ellis | → DIV-1702 |
| DIV-1702 Alex Romero | → DIV-1703 |
| DIV-1703 Taylor Brooks | → DIV-1701 |

All three are inside the cycle. No employee merely reports *into* the cycle without being a member.

---

## Assumptions & Known Limitations

1. **Encoding** — Only UTF-8 (with or without BOM) is accepted. Latin-1 or
   other legacy encodings are not supported.

2. **In-memory processing** — The entire file is read into memory. At 100 k
   employees (~25 MB of CSV), this is well within typical server limits. The
   10 MB upload cap in `views.py` can be raised for larger files.

3. **No database persistence** — Analysis exists only for the duration of the
   HTTP request. With more time I would store results by upload UUID so large
   previews could be paginated.

4. **Cycle participant definition** — "Employees that participate in a reporting
   cycle" is interpreted as: employees *inside* a cycle **or** employees whose
   reporting chain eventually leads into a cycle. The UI distinguishes these two
   groups clearly.

5. **Column order** — `csv.DictReader` accepts any column order.

6. **employee_id case sensitivity** — IDs are case-sensitive per the spec.
   `EMP-1` and `emp-1` are treated as different employees.

7. **No authentication, no production deployment** — as specified.

---

## Time Spent

| Phase | Approximate time |
|-------|-----------------|
| Implementation (coding + testing) | ~90 minutes |
| Narrated video walkthrough | ~10 minutes |
| **Total** | ~100 minutes |

---

## AI Tools Used

**Google Antigravity (Gemini and Claude Sonnet)** was used throughout:

- Scaffolding the Django project structure and module split.
- Writing the initial versions of `parser.py`, `analyser.py`, `views.py`, and tests.
- Generating the HTML/CSS for both templates.

### What I changed from AI output

1. **Cycle participant logic** — The initial AI output counted *all* downstream
   employees as cycle members. I corrected this: `cycle_members` contains only
   nodes *inside* a cycle; `reporting_cycle_participants` adds employees who
   report *into* a cycle. These are distinct concepts in the spec.

2. **Manager error + accepted** — Early output excluded employees with manager
   errors from the "accepted" count. The spec says they remain accepted. I
   verified and corrected this.

3. **DFS iterative vs recursive** — The AI initially proposed a recursive DFS.
   I switched to an iterative implementation to avoid Python's default recursion
   limit for large files.

I reviewed, understood, and validated every piece of code before submission.
I am able to explain all non-trivial logic in my own words.
