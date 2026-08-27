"""
hris/parser.py
--------------
Handles CSV ingestion and row-level validation.

Responsibilities
----------------
* Decode the uploaded file (UTF-8 with or without BOM).
* Parse fields using stdlib `csv` so quoted values are handled correctly.
* Normalise every value (strip whitespace; lowercase email fields).
* Detect required-field violations and duplicate employee_id / email.
* Return two lists: valid rows ready for hierarchy analysis, and invalid rows
  with human-readable error messages and 1-based source row numbers.

No Django imports — this module is intentionally framework-free so the logic
can be exercised in plain unit tests without spinning up the request/response
cycle.
"""

import csv
import io
from dataclasses import dataclass, field
from typing import List, Tuple


REQUIRED_HEADERS = {"employee_id", "employee_name", "email", "manager_id", "manager_email", "department"}


@dataclass
class EmployeeRow:
    """A single, normalised HRIS row."""
    source_row: int          # 1-based row number in the original file (header = row 1)
    employee_id: str
    employee_name: str
    email: str
    manager_id: str          # may be empty string
    manager_email: str       # may be empty string (already lowercased)
    department: str


@dataclass
class InvalidRow:
    """A row that failed validation, kept for reporting."""
    source_row: int
    raw: dict                # the original key→value dict before validation
    errors: List[str] = field(default_factory=list)


@dataclass
class ParseResult:
    total_source_rows: int
    valid_rows: List[EmployeeRow]
    invalid_rows: List[InvalidRow]


def parse_csv(file_obj) -> ParseResult:
    """
    Parse an uploaded CSV file object (Django InMemoryUploadedFile or any
    file-like object) and return a ParseResult.

    Steps
    -----
    1. Read raw bytes and decode as UTF-8, stripping the BOM if present.
    2. Parse with csv.DictReader.
    3. Normalise and validate each row.
    4. Second pass: flag all rows whose employee_id or email is duplicated.
    """
    raw_bytes = file_obj.read()

    # Strip UTF-8 BOM (EF BB BF) if present
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raw_bytes = raw_bytes[3:]

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not valid UTF-8: {exc}") from exc

    reader = csv.DictReader(io.StringIO(text))

    # Validate headers
    if reader.fieldnames is None:
        raise ValueError("Uploaded file appears to be empty.")

    actual_headers = {h.strip().lower() for h in reader.fieldnames}
    missing = REQUIRED_HEADERS - actual_headers
    if missing:
        raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")

    # --- First pass: normalise and collect per-row errors ---
    raw_rows: List[dict] = []
    normalised: List[dict] = []  # parallel list

    for row in reader:
        # Normalise: strip whitespace from all values, lowercase emails
        norm = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
        norm["email"] = norm.get("email", "").lower()
        norm["manager_email"] = norm.get("manager_email", "").lower()
        # employee_id stays case-sensitive but is still whitespace-trimmed
        # (the key was lowercased above so we reconstruct the value only)
        raw_rows.append(norm)
        normalised.append(norm)

    total = len(normalised)

    # --- Build duplicate sets for employee_id and email ---
    id_counts: dict[str, List[int]] = {}   # employee_id → list of 0-based indices
    email_counts: dict[str, List[int]] = {}

    for idx, norm in enumerate(normalised):
        eid = norm.get("employee_id", "")
        email = norm.get("email", "")
        if eid:
            id_counts.setdefault(eid, []).append(idx)
        if email:
            email_counts.setdefault(email, []).append(idx)

    duplicate_id_indices: set[int] = set()
    for eid, indices in id_counts.items():
        if len(indices) > 1:
            for i in indices:
                duplicate_id_indices.add(i)

    duplicate_email_indices: set[int] = set()
    for email, indices in email_counts.items():
        if len(indices) > 1:
            for i in indices:
                duplicate_email_indices.add(i)

    # --- Second pass: build valid/invalid lists ---
    valid_rows: List[EmployeeRow] = []
    invalid_rows: List[InvalidRow] = []

    for idx, norm in enumerate(normalised):
        source_row = idx + 2  # +1 for 0-index, +1 for header row
        errors: List[str] = []

        eid = norm.get("employee_id", "")
        email = norm.get("email", "")

        # Required fields
        if not eid:
            errors.append("employee_id is required")
        if not email:
            errors.append("email is required")

        # Duplicate checks
        if eid and idx in duplicate_id_indices:
            errors.append(f"Duplicate employee_id '{eid}'")
        if email and idx in duplicate_email_indices:
            errors.append(f"Duplicate email '{email}'")

        if errors:
            invalid_rows.append(InvalidRow(source_row=source_row, raw=norm, errors=errors))
        else:
            valid_rows.append(EmployeeRow(
                source_row=source_row,
                employee_id=eid,
                employee_name=norm.get("employee_name", ""),
                email=email,
                manager_id=norm.get("manager_id", ""),
                manager_email=norm.get("manager_email", ""),
                department=norm.get("department", ""),
            ))

    return ParseResult(
        total_source_rows=total,
        valid_rows=valid_rows,
        invalid_rows=invalid_rows,
    )
